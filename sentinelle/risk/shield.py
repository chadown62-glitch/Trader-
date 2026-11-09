"""Bouclier temps réel : interdit les NOUVELLES positions quand le marché devient dangereux.

Il ne vend rien et ne prédit rien : il évite d'entrer au pire moment. Ses gardes :

1. **Volatilité extrême** : une bougie d'une minute anormalement grande (dépêche,
   krach éclair, liquidations en chaîne) déclenche une pause.
2. **Microstructure** : avant chaque entrée, l'écart achat/vente (spread) et la
   liquidité du carnet d'ordres sont vérifiés. Pendant une annonce, les teneurs
   de marché se retirent : acheter à ce moment coûte très cher.
3. **Cascade de liquidations** sur les marchés à levier (données publiques OKX) :
   pause tant que la vague de ventes forcées n'est pas retombée.
4. **Actualité majeure** : une nouvelle jugée très importante par l'IA locale,
   dans un sens ou dans l'autre, déclenche une pause (l'incertitude coûte cher).

Le calendrier des annonces macro est appliqué par le gestionnaire de risque.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from typing import Callable

import httpx

from ..storage.db import loads

log = logging.getLogger("sentinelle.shield")

OKX = "https://www.okx.com/api/v5/public"


def fr(x: float, digits: int = 1) -> str:
    """Nombre au format français (virgule décimale)."""
    return f"{x:.{digits}f}".replace(".", ",")


class Shield:
    def __init__(self, settings_fn: Callable[[], dict], *, book_fn=None, db=None, bus=None,
                 clock: Callable[[], float] = time.time, transport=None):
        self.settings_fn = settings_fn
        self.book_fn = book_fn  # (symbol) -> {"bids": [[px, qty]...], "asks": [...]}
        self.db = db
        self.bus = bus
        self.clock = clock
        self.transport = transport
        self._lock = threading.Lock()
        self._minutes: dict[str, deque] = {}  # symbol -> deque[(minute, high, low, close)]
        self._cur: dict[str, list] = {}
        self.vol_until: dict[str, tuple[float, str]] = {}
        self._book_cache: dict[str, tuple[float, dict]] = {}
        self._liq_minutes: dict[int, float] = {}
        self._liq_seen: set = set()
        self._ct_val: dict[str, float] = {}
        self._liq_since_min: int | None = None  # début de la période de liquidations observée sans trou
        self._liq_newest: dict[str, int] = {}  # liquidation la plus récente déjà lue, par marché
        self.cascade_until: tuple[float, str] | None = None
        self.last_liq_check = 0.0
        self.metrics: dict = {"liquidations_15m_musd": None, "liquidations_ref_musd": None}

    @property
    def cfg(self) -> dict:
        return self.settings_fn()["shield"]

    # -- 1. volatilité extrême (alimenté par les relevés de prix) ------------------
    def on_tick(self, symbol: str, o: float, h: float, l: float, c: float, start_ms: int, end_ms: int | None = None) -> None:
        minute = int((end_ms or start_ms) // 60_000)
        alert = None
        with self._lock:
            cur = self._cur.get(symbol)
            if cur is None or cur[0] != minute:
                if cur is not None:
                    dq = self._minutes.setdefault(symbol, deque(maxlen=240))
                    dq.append(tuple(cur))
                    alert = self._evaluate_volatility(symbol)
                self._cur[symbol] = [minute, max(o, h), min(o, l), c]
            else:
                cur[1] = max(cur[1], h)
                cur[2] = min(cur[2], l)
                cur[3] = c
        if alert and self.bus:  # publié hors du verrou : un abonné peut relire l'état du bouclier
            self.bus.publish("alert", {"level": "warning", "message": alert})

    def _evaluate_volatility(self, symbol: str) -> str | None:
        """Déclenche une pause si la dernière minute est anormale ; renvoie le message d'alerte à publier."""
        dq = self._minutes.get(symbol)
        if not dq or len(dq) < 30:
            return None  # pas encore assez d'historique pour juger
        ranges = [(hi - lo) / cl * 100 for _, hi, lo, cl in dq if cl > 0]
        typical = statistics.median(ranges[:-1]) if len(ranges) > 1 else 0.0
        last = ranges[-1]
        cfg = self.cfg
        if last >= max(cfg["volatility_multiple"] * typical, cfg["volatility_min_pct"]):
            until = self.clock() + cfg["cooldown_minutes"] * 60
            reason = f"mouvement brutal sur {symbol} ({fr(last, 2)} % en une minute, {last / max(typical, 1e-9):.0f}× la normale)"
            already = symbol in self.vol_until and self.vol_until[symbol][0] > self.clock()
            self.vol_until[symbol] = (until, reason)
            if not already:
                return f"Bouclier : {reason}. Aucune nouvelle position pendant {cfg['cooldown_minutes']} min."
        return None

    # -- 2. microstructure (spread et liquidité) --------------------------------------
    def book(self, symbol: str) -> dict | None:
        if self.book_fn is None:
            return None
        hit = self._book_cache.get(symbol)
        if hit and self.clock() - hit[0] < 5:
            return hit[1]
        try:
            ob = self.book_fn(symbol)
        except Exception as exc:
            log.info("Carnet d'ordres indisponible (%s) : %s", symbol, exc)
            return None
        self._book_cache[symbol] = (self.clock(), ob)
        return ob

    def microstructure(self, symbol: str, notional: float) -> dict | None:
        ob = self.book(symbol)
        if not ob or not ob.get("bids") or not ob.get("asks"):
            return None
        bid, ask = float(ob["bids"][0][0]), float(ob["asks"][0][0])
        mid = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000
        limit = ask * 1.005
        depth = sum(float(p) * float(q) for p, q in ob["asks"] if float(p) <= limit)
        return {"bid": bid, "ask": ask, "spread_bps": spread_bps, "depth_quote": depth,
                "depth_ratio": depth / notional if notional > 0 else None}

    # -- 3. cascades de liquidations (OKX, données publiques) ------------------------
    # OKX publie les liquidations des dernières 24 h, 100 par page, des plus récentes aux plus
    # anciennes. Au premier relevé, les 24 h sont lues (référence « habituelle » disponible tout
    # de suite) ; ensuite, chaque minute, les pages sont lues jusqu'aux données déjà connues :
    # même pendant une cascade (des milliers de liquidations), rien n'est oublié.
    def _read_liquidations(self, client: httpx.Client, uly: str, inst: str, max_pages: int) -> int | None:
        """Lit les pages récentes ; renvoie l'horodatage (ms) le plus ancien couvert sans trou.

        La lecture continue jusqu'à 5 minutes avant la liquidation la plus récente déjà connue :
        une liquidation publiée avec un peu de retard n'est pas oubliée."""
        stop_ts = self._liq_newest.get(uly)
        after = None
        oldest = None
        for _ in range(max_pages):
            params = {"instType": "SWAP", "uly": uly, "state": "filled", "limit": 100}
            if after:
                params["after"] = after
            r = client.get(f"{OKX}/liquidation-orders", params=params)
            details = [d for block in r.json().get("data", []) for d in block.get("details", [])]
            if not details:
                break
            # comparaison avec les relevés PRÉCÉDENTS : deux liquidations identiques dans la même
            # page sont deux événements distincts
            fresh = [(k, d) for k, d in (((uly, d["ts"], d["sz"], d["bkPx"], d.get("side")), d) for d in details)
                     if k not in self._liq_seen]
            for _, d in fresh:
                usd = float(d["sz"]) * self._ct_val[inst] * float(d["bkPx"])
                minute = int(int(d["ts"]) // 60_000)
                self._liq_minutes[minute] = self._liq_minutes.get(minute, 0.0) + usd
            self._liq_seen.update(k for k, _ in fresh)
            newest = max(int(d["ts"]) for d in details)
            self._liq_newest[uly] = max(self._liq_newest.get(uly, 0), newest)
            oldest = min(int(d["ts"]) for d in details)
            if stop_ts is not None and oldest <= stop_ts - 300_000:
                break  # on a rejoint (avec 5 min de marge) les données déjà relevées
            after = str(oldest)
            if stop_ts is None:
                time.sleep(0.1)  # premier relevé (24 h) : on reste très en dessous des limites d'OKX
        return oldest

    def poll_liquidations(self, client: httpx.Client | None = None) -> None:
        if not self.cfg["liquidations"]:
            return
        own = client is None
        client = client or httpx.Client(timeout=10, transport=self.transport, headers={"User-Agent": "Sentinelle"})
        first = self._liq_since_min is None
        try:
            covered = []
            for uly in ("BTC-USDT", "ETH-USDT"):
                inst = f"{uly}-SWAP"
                if inst not in self._ct_val:
                    r = client.get(f"{OKX}/instruments", params={"instType": "SWAP", "instId": inst})
                    self._ct_val[inst] = float(r.json()["data"][0]["ctVal"])
                oldest = self._read_liquidations(client, uly, inst, max_pages=80 if first else 20)
                if oldest is not None:
                    covered.append(int(oldest // 60_000))
            if first:
                # la référence ne porte que sur la période réellement observée
                self._liq_since_min = max(covered) if covered else int(self.clock() // 60)
            self.last_liq_check = self.clock()
            self._evaluate_cascade()
        except Exception as exc:
            log.info("Liquidations indisponibles : %s", exc)
        finally:
            if own:
                client.close()
            cutoff = (self.clock() - 25 * 3600) * 1000
            if len(self._liq_seen) > 20_000:
                self._liq_seen = {k for k in self._liq_seen if int(k[1]) >= cutoff}

    def _evaluate_cascade(self) -> None:
        now_min = int(self.clock() // 60)
        horizon = 24 * 60
        for m in [m for m in self._liq_minutes if m < now_min - horizon]:
            del self._liq_minutes[m]
        last15 = sum(v for m, v in self._liq_minutes.items() if m > now_min - 15)
        start0 = max(now_min - horizon, self._liq_since_min if self._liq_since_min is not None else now_min)
        windows = [sum(v for m, v in self._liq_minutes.items() if start <= m < start + 15)
                   for start in range(start0, now_min - 15, 15)]
        ref = statistics.median(windows) if len(windows) >= 8 else None  # au moins 2 h d'observation
        self.metrics.update(liquidations_15m_musd=last15 / 1e6, liquidations_ref_musd=(ref / 1e6) if ref is not None else None)
        cfg = self.cfg
        threshold = cfg["cascade_min_musd"] * 1e6
        if ref is not None:
            threshold = max(threshold, cfg["cascade_multiple"] * ref)
        if last15 >= threshold:
            reason = f"cascade de liquidations sur les marchés à levier ({last15 / 1e6:.0f} M$ en 15 min)"
            fresh = self.cascade_until is None or self.cascade_until[0] < self.clock()
            self.cascade_until = (self.clock() + 60 * 60, reason)
            if fresh and self.bus:
                self.bus.publish("alert", {"level": "warning", "message": f"Bouclier : {reason}. Aucune nouvelle position pendant 1 h."})

    # -- 4. actualité majeure ------------------------------------------------------------
    def _major_news(self, base: str) -> str | None:
        cfg = self.cfg
        if not cfg["news_pause"] or self.db is None:
            return None
        since = self.clock() - cfg["news_pause_minutes"] * 60
        rows = self.db.query(
            "SELECT title, assets FROM news WHERE importance >= 0.9 AND confidence >= 0.7 AND published_ts >= ? "
            "ORDER BY published_ts DESC LIMIT 20", (since,),
        )
        for r in rows:
            assets = loads(r["assets"], []) or []
            if "*" in assets or base in assets or (base == "*" and assets):
                return f"actualité majeure en cours (« {r['title'][:90]} »)"
        return None

    # -- décision ------------------------------------------------------------------------
    def check_entry(self, symbol: str, notional: float) -> str | None:
        """Raison de bloquer une nouvelle position, ou None si tout va bien."""
        cfg = self.cfg
        if not cfg["enabled"]:
            return None
        now = self.clock()
        base, quote = symbol.split("/")
        for key in (symbol, f"BTC/{quote}"):  # un choc sur le BTC concerne tout le marché
            hit = self.vol_until.get(key)
            if hit and hit[0] > now:
                return hit[1]
        if self.cascade_until and self.cascade_until[0] > now:
            return self.cascade_until[1]
        news = self._major_news(base)
        if news:
            return news
        ms = self.microstructure(symbol, notional)
        if ms:
            if ms["spread_bps"] > cfg["max_spread_bps"]:
                return f"écart achat/vente trop large ({fr(ms['spread_bps'])} points de base, maximum {fr(cfg['max_spread_bps'], 0)})"
            if ms["depth_ratio"] is not None and ms["depth_ratio"] < cfg["depth_multiple"]:
                return f"liquidité insuffisante dans le carnet d'ordres ({fr(ms['depth_ratio'])}× la taille de l'ordre)"
        return None

    def status(self) -> dict:
        now = self.clock()
        with self._lock:  # on_tick modifie ces dictionnaires depuis un autre fil d'exécution
            vol = {s: {"until": u, "reason": r} for s, (u, r) in self.vol_until.items() if u > now}
            tracked = {s: len(self._minutes.get(s, ())) for s in set(self._minutes) | set(self._cur)}
        cu = self.cascade_until
        cascade = {"until": cu[0], "reason": cu[1]} if cu and cu[0] > now else None
        news = None
        try:
            news = self._major_news("*")
        except Exception:
            news = None
        return {"enabled": self.cfg["enabled"], "volatility": vol, "cascade": cascade, "news": news, "metrics": dict(self.metrics),
                "tracked": tracked, "last_liquidation_check": self.last_liq_check}
