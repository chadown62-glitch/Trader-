"""Taux de change entre la monnaie du portefeuille (l'euro) et les monnaies de cotation.

Le portefeuille principal est tenu en euros, alors que les cryptos sont cotées en
USDC, en USDT ou en dollars. Chaque conversion est donc **explicite** : un taux, sa
**source** et son **horodatage**, enregistrés avec l'opération.

Sources, dans l'ordre :
1. la paire EUR/<monnaie> de la source de marché (Binance : cotée 24 h/24) ;
2. à défaut, la paire EUR/USDT de la même source, en supposant 1 stablecoin ≈ 1 dollar
   (le taux est alors marqué « approximatif ») ;
3. à défaut, le taux de référence quotidien de la Banque centrale européenne (dollar),
   lui aussi marqué « approximatif » ;
4. sur le marché simulé (démo, tests) : un taux simulé, déterministe.

Règle de sûreté : sans taux assez récent, aucun ordre n'est passé sur un portefeuille
en euros (refus, jamais de taux inventé). Pour le simple affichage, le dernier taux
connu reste utilisé et signalé comme ancien.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from ..execution.base import OrderError

log = logging.getLogger("sentinelle.fx")

# Monnaies traitées comme « un dollar » quand aucune paire directe n'existe (approximation signalée)
USD_LIKE = ("USD", "USDC", "USDT", "FDUSD", "TUSD", "USDP", "DAI")
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_MAX_AGE = 4 * 86_400  # le taux BCE n'est pas publié le week-end


@dataclass(frozen=True)
class FxQuote:
    base: str  # monnaie du portefeuille (EUR)
    quote: str  # monnaie de cotation (USDC, USDT, USD…)
    rate: float  # valeur de 1 unité de `quote`, exprimée en `base` (ex. 1 USDC = 0,86 EUR)
    source: str
    ts: float  # horodatage de la donnée (secondes)
    approx: bool = False  # vrai si l'on a supposé 1 stablecoin = 1 dollar
    max_age: float = 1800.0  # durée pendant laquelle ce taux peut servir à passer un ordre

    def age(self, now: float) -> float:
        return max(0.0, now - self.ts)

    def to_dict(self, now: float | None = None) -> dict:
        d = asdict(self)
        d["inverse"] = 1.0 / self.rate if self.rate else None  # combien de `quote` pour 1 `base`
        if now is not None:
            d["age_s"] = self.age(now)
            d["stale"] = self.age(now) > self.max_age
        return d


class FxUnavailable(OrderError):
    """Aucun taux de change utilisable : l'ordre est refusé."""

    def __init__(self, message: str):
        super().__init__(message, "no_fx")


Fetcher = Callable[[str, str], FxQuote | None]


def identity(currency: str, now: float) -> FxQuote:
    return FxQuote(currency, currency, 1.0, "même monnaie", now, False, float("inf"))


def market_fetcher(source, max_age: float | Callable[[], float] = 1800.0) -> Fetcher:
    """Taux lu sur la source de marché : paire directe, sinon EUR/USDT pour une monnaie assimilée au dollar.

    `max_age` (secondes, ou fonction qui le renvoie) : durée de validité du taux pour passer un ordre."""

    def last_close(pair: str) -> tuple[float, float] | None:
        rows = source.fetch_ohlcv(pair, "1m", limit=2)
        if not rows:
            return None
        px = float(rows[-1][4])
        if not (px > 0) or not math.isfinite(px):
            return None
        return px, min(source.now_ms() / 1000, rows[-1][0] / 1000 + 60)

    def fetch(base: str, quote: str) -> FxQuote | None:
        name = getattr(source, "name", "marché")
        tries = [(f"{base}/{quote}", False)]
        if quote in USD_LIKE and quote != "USDT":
            tries.append((f"{base}/USDT", True))
        for pair, approx in tries:
            try:
                hit = last_close(pair)
            except Exception:
                hit = None
            if hit:
                px, ts = hit
                label = f"{name} {pair}" + (f" (1 {quote} ≈ 1 USDT)" if approx else "")
                return FxQuote(base, quote, 1.0 / px, label, ts, approx, float(max_age() if callable(max_age) else max_age))
        return None

    return fetch


def ecb_fetcher(timeout: float = 10.0) -> Fetcher:
    """Taux de référence quotidien de la BCE (dollar). Approximation pour les stablecoins."""

    def fetch(base: str, quote: str) -> FxQuote | None:
        if base != "EUR" or quote not in USD_LIKE:
            return None
        import re
        from datetime import datetime, timezone

        import httpx

        text = httpx.get(ECB_URL, timeout=timeout, headers={"User-Agent": "Sentinelle"}).text
        day = re.search(r"time='(\d{4}-\d{2}-\d{2})'", text)
        usd = re.search(r"currency='USD' rate='([0-9.]+)'", text)
        if not day or not usd:
            return None
        per_eur = float(usd.group(1))
        if not (per_eur > 0):
            return None
        # publication vers 16 h (heure d'Europe centrale), soit environ 14 h 15 UTC
        ts = datetime.strptime(day.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() + 14.25 * 3600
        label = "BCE (taux de référence du jour)" + ("" if quote == "USD" else f", 1 {quote} ≈ 1 USD")
        return FxQuote(base, quote, 1.0 / per_eur, label, ts, quote != "USD", ECB_MAX_AGE)

    return fetch


def synthetic_fetcher(clock: Callable[[], float], per_base: float = 1.11) -> Fetcher:
    """Taux simulé et déterministe (démo hors-ligne, tests) : oscille lentement de ±1 % autour de 1,11 USDC pour 1 €."""

    def fetch(base: str, quote: str) -> FxQuote | None:
        if quote not in USD_LIKE:
            return None
        now = clock()
        px = per_base * (1 + 0.01 * math.sin(now / 86_400 / 9))
        return FxQuote(base, quote, 1.0 / px, "taux simulé (démonstration)", now, False, 1800.0)

    return fetch


class FxService:
    """Fournit le taux d'une monnaie de cotation vers la monnaie du portefeuille, avec mémoire et repli."""

    def __init__(self, base: str, fetchers: list[Fetcher], clock: Callable[[], float] = time.time, db=None, ttl: float = 30.0):
        self.base = base
        self.fetchers = fetchers
        self.clock = clock
        self.db = db
        self.ttl = ttl
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, FxQuote]] = {}  # monnaie -> (heure du relevé, taux)
        self._retry_at: dict[str, float] = {}  # après un échec : pas de nouvel essai avant cette heure
        self.last_error: str | None = None

    # -- mémoire -------------------------------------------------------------------
    def _remember(self, q: FxQuote) -> None:
        if self.db is None:
            return
        try:
            from ..storage.db import dumps

            self.db.set_meta(f"fx_last_{q.quote}", dumps(asdict(q)))
            last = self.db.one("SELECT ts, rate FROM fx_rates WHERE quote = ? ORDER BY id DESC LIMIT 1", (q.quote,))
            if last is None or q.ts - last["ts"] >= 600 or abs(q.rate / last["rate"] - 1) > 0.001:
                self.db.insert("fx_rates", {"ts": q.ts, "base": q.base, "quote": q.quote, "rate": q.rate, "source": q.source})
        except Exception:
            log.debug("Taux de change non mémorisé", exc_info=True)

    def _recall(self, currency: str) -> FxQuote | None:
        if self.db is None:
            return None
        try:
            from ..storage.db import loads

            d = loads(self.db.get_meta(f"fx_last_{currency}"), None)
            if d and d.get("base") == self.base and d.get("rate", 0) > 0:
                if d.get("max_age") is None:
                    d["max_age"] = 1800.0
                return FxQuote(**d)
        except Exception:
            return None
        return None

    # -- lecture ---------------------------------------------------------------------
    def refresh(self, currency: str) -> FxQuote | None:
        """Interroge les sources dans l'ordre. Renvoie None si aucune ne répond."""
        if currency == self.base:
            return identity(currency, self.clock())
        errors = []
        for fetch in self.fetchers:
            try:
                q = fetch(self.base, currency)
            except Exception as exc:
                errors.append(str(exc)[:120])
                continue
            if q is not None and q.rate > 0 and math.isfinite(q.rate):
                with self._lock:
                    self._cache[currency] = (self.clock(), q)
                self.last_error = None
                self._remember(q)
                return q
        self.last_error = "; ".join(errors) or f"aucune source ne cote {self.base}/{currency}"
        self._retry_at[currency] = self.clock() + 60.0
        return None

    def latest(self, currency: str) -> FxQuote | None:
        """Dernier taux connu (éventuellement ancien), sans exiger qu'il soit frais."""
        if currency == self.base:
            return identity(currency, self.clock())
        now = self.clock()
        with self._lock:
            hit = self._cache.get(currency)
        if (hit is None or now - hit[0] > self.ttl) and now >= self._retry_at.get(currency, 0.0):
            fresh = self.refresh(currency)
            if fresh is not None:
                return fresh
        if hit is not None:
            return hit[1]
        return self._recall(currency)

    def quote(self, currency: str, *, for_trading: bool = True) -> FxQuote:
        """Taux à utiliser. Pour passer un ordre, il doit être assez récent ; sinon l'ordre est refusé."""
        q = self.latest(currency)
        if q is None:
            raise FxUnavailable(f"Taux de change {self.base}/{currency} indisponible : aucun ordre possible sur un portefeuille en {self.base}")
        if for_trading and q.age(self.clock()) > q.max_age:
            minutes = q.age(self.clock()) / 60
            raise FxUnavailable(f"Taux de change {self.base}/{currency} trop ancien ({minutes:.0f} min, source : {q.source}) : ordre refusé par prudence")
        return q

    def status(self, currencies) -> dict:
        now = self.clock()
        out = {}
        for cur in currencies:
            if cur == self.base:
                continue
            q = self.latest(cur)
            out[cur] = q.to_dict(now) if q else None
        return {"base": self.base, "rates": out, "error": self.last_error}
