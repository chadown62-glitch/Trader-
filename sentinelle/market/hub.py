"""Centrale de marché : prix en direct, bougies clôturées, état des données.

Fonctionne dans le thread du moteur (`services.engine`). À chaque relevé :
1. récupère le prix de chaque paire suivie ;
2. construit une « micro-bougie » entre deux relevés (plus haut / plus bas
   réellement atteints depuis le relevé précédent, sans faux déclenchement) ;
3. la transmet aux brokers (déclenchement des stops, ordres limites) ;
4. détecte la clôture des bougies utilisées par l'IA et prévient les agents.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import pandas as pd

from ..config import timeframe_ms
from .history import HistoryStore, clean_frame, to_frame
from .markets import MarketInfo
from .sources import MarketSource

log = logging.getLogger("sentinelle.hub")

TickHandler = Callable[[str, float, float, float, float, int, int], None]
CandleHandler = Callable[[str, str, pd.DataFrame], None]

STALE_SECONDS = 120


class MarketHub:
    def __init__(self, source: MarketSource, history: HistoryStore, bus=None):
        self.source = source
        self.history = history
        self.bus = bus
        self.markets: dict[str, MarketInfo] = {}
        self.prices: dict[str, float] = {}
        self.price_ts: dict[str, float] = {}
        self.watch: set[str] = set()
        self.tick_handlers: list[TickHandler] = []
        self.candle_handlers: list[CandleHandler] = []
        self._minute: dict[str, tuple[int, float, float]] = {}
        self._series: dict[tuple[str, str], pd.DataFrame] = {}
        self._tracked: dict[str, set[str]] = {}  # timeframe -> paires
        self._ui_cache: dict[tuple, tuple[float, list]] = {}
        self.status = {"ok": False, "last_ok": None, "errors": 0, "last_error": None, "source": source.name}

    # -- démarrage --------------------------------------------------------
    def load_markets(self) -> None:
        self.markets = self.source.load_markets()
        log.info("%d paires chargées depuis %s", len(self.markets), self.source.name)

    def market(self, symbol: str) -> MarketInfo:
        m = self.markets.get(symbol)
        if m is None:
            raise KeyError(f"Paire inconnue : {symbol}")
        return m

    def set_watch(self, symbols) -> None:
        self.watch = {s for s in symbols if s in self.markets} if self.markets else set(symbols)

    # -- prix -------------------------------------------------------------
    def price(self, symbol: str) -> float | None:
        return self.prices.get(symbol)

    def is_fresh(self, symbol: str | None = None, max_age: float = STALE_SECONDS) -> bool:
        now = time.time()
        if symbol is not None:
            ts = self.price_ts.get(symbol)
            return ts is not None and now - ts <= max_age
        return bool(self.price_ts) and all(now - t <= max_age for t in self.price_ts.values())

    def poll(self) -> None:
        """Un relevé complet (appelé périodiquement par le moteur)."""
        ok_any = False
        for symbol in sorted(self.watch):
            try:
                self._poll_symbol(symbol)
                ok_any = True
            except Exception as exc:  # réseau, exchange en maintenance...
                self._error(f"{symbol} : {exc}")
        if ok_any:
            self.status.update(ok=True, last_ok=time.time())
        self._check_closes()

    def _error(self, msg: str) -> None:
        self.status["errors"] += 1
        self.status["last_error"] = msg[:300]
        if self.status["errors"] % 20 == 1:
            log.warning("Données de marché : %s", msg)
        if self.status["last_ok"] is None or time.time() - self.status["last_ok"] > STALE_SECONDS:
            self.status["ok"] = False

    def _poll_symbol(self, symbol: str) -> None:
        rows = self.source.fetch_ohlcv(symbol, "1m", limit=2)
        if not rows:
            raise RuntimeError("aucune donnée reçue")
        cur = rows[-1]
        minute_ts, H, L, close = int(cur[0]), float(cur[2]), float(cur[3]), float(cur[4])
        if not (close > 0):
            raise RuntimeError("prix invalide")
        prev_price = self.prices.get(symbol)
        st = self._minute.get(symbol)
        hi = lo = close
        if st is not None:
            if st[0] == minute_ts:
                # même minute : seuls les nouveaux extrêmes ont eu lieu après le relevé précédent
                if H > st[1]:
                    hi = max(hi, H)
                if L < st[2]:
                    lo = min(lo, L)
            elif st[0] < minute_ts:
                prev_row = rows[-2] if len(rows) >= 2 and int(rows[-2][0]) == st[0] else None
                if prev_row is not None:
                    if prev_row[2] > st[1]:
                        hi = max(hi, float(prev_row[2]))
                    if prev_row[3] < st[2]:
                        lo = min(lo, float(prev_row[3]))
                # la nouvelle minute a commencé après le relevé précédent
                hi = max(hi, H)
                lo = min(lo, L)
        self._minute[symbol] = (minute_ts, H, L)
        o = prev_price if prev_price else close
        hi = max(hi, o, close)
        lo = min(lo, o, close)
        now_ms = self.source.now_ms()
        start_ms = int(self.price_ts.get(symbol, now_ms / 1000) * 1000)
        self.prices[symbol] = close
        self.price_ts[symbol] = now_ms / 1000
        for h in list(self.tick_handlers):
            try:
                h(symbol, o, hi, lo, close, start_ms, now_ms)
            except Exception:
                log.exception("Erreur lors du traitement d'un prix (%s)", symbol)
        if self.bus:
            self.bus.publish("tick", {"symbol": symbol, "price": close, "ts": now_ms, "high": hi, "low": lo})

    # -- bougies clôturées (IA) ---------------------------------------------
    def track(self, symbol: str, timeframe: str, warmup_days: int) -> pd.DataFrame:
        """Charge l'historique et suit les clôtures de bougies pour une paire."""
        df = self.history.update(symbol, timeframe, warmup_days)
        self._series[(symbol, timeframe)] = df
        self._tracked.setdefault(timeframe, set()).add(symbol)
        return df

    def untrack(self, symbol: str, timeframe: str) -> None:
        self._tracked.get(timeframe, set()).discard(symbol)

    def series(self, symbol: str, timeframe: str) -> pd.DataFrame:
        return self._series.get((symbol, timeframe), to_frame([]))

    def _check_closes(self) -> None:
        now = self.source.now_ms()
        for tf, symbols in list(self._tracked.items()):
            step = timeframe_ms(tf)
            bucket = now - now % step
            last_closed_expected = bucket - step
            if now - bucket < 3000:  # laisse quelques secondes à l'exchange pour finaliser
                continue
            for symbol in sorted(symbols):
                df = self._series.get((symbol, tf))
                last = int(df["ts"].iloc[-1]) if df is not None and len(df) else -1
                if last >= last_closed_expected:
                    continue
                try:
                    since = last + step if last > 0 else None
                    rows = self.source.fetch_ohlcv(symbol, tf, since=since, limit=1000)
                except Exception as exc:
                    self._error(f"bougies {symbol} {tf} : {exc}")
                    continue
                closed = [r for r in rows if r[0] + step <= now and r[0] > last]
                if not closed:
                    continue
                new = to_frame(closed)
                merged = new if df is None or df.empty else pd.concat([df, new], ignore_index=True)
                merged, _ = clean_frame(merged)
                if len(merged) > 20_000:
                    merged = merged.iloc[-20_000:].reset_index(drop=True)
                self._series[(symbol, tf)] = merged
                for h in list(self.candle_handlers):
                    try:
                        h(symbol, tf, merged)
                    except Exception:
                        log.exception("Erreur d'un agent sur la bougie %s %s", symbol, tf)
                if self.bus:
                    r = closed[-1]
                    self.bus.publish(
                        "candle",
                        {"symbol": symbol, "timeframe": tf, "ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]},
                    )

    # -- graphiques du panel --------------------------------------------------
    def ui_candles(self, symbol: str, timeframe: str, limit: int = 500) -> list[list[float]]:
        key = (symbol, timeframe, limit)
        cached = self._ui_cache.get(key)
        ttl = 3.0 if timeframe in ("1m", "5m") else 10.0
        if cached and time.time() - cached[0] < ttl:
            return cached[1]
        rows = self.source.fetch_ohlcv(symbol, timeframe, limit=limit)
        self._ui_cache[key] = (time.time(), rows)
        if len(self._ui_cache) > 200:
            self._ui_cache.clear()
        return rows

    def snapshot(self) -> dict:
        return {
            "status": dict(self.status),
            "prices": dict(self.prices),
            "fresh": {s: self.is_fresh(s) for s in self.watch},
            "watch": sorted(self.watch),
        }
