"""Executable paper FX from dated two-sided quotes; reference rates are indicative only."""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from ..execution.base import OrderError

from decimal import Decimal
from .fx_book import FXBook, FXError, FXQuote
from .fx_provider import PublicFXProvider

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

    market: dict | None = None

    @classmethod
    def from_book(cls, base, currency, book, max_age):
        q, = book.quotes.values()
        info = {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(q).items()}
        # Indicative value is the cash recoverable when selling one unit.
        rate = book.convert(1, currency, base, now=q.timestamp, max_age=max_age)["amount"]
        return cls(base, currency, float(rate), q.source, q.timestamp, False, max_age, info)

    def executable(self, now):
        if self.base == self.quote:
            return
        if (not self.market or self.approx or not math.isfinite(now) or not math.isfinite(self.ts)
                or not math.isfinite(self.max_age) or not 0 <= now - self.ts <= self.max_age):
            raise FxUnavailable(f"Taux {self.base}/{self.quote} indicatif, futur ou trop ancien")
        book = FXBook([FXQuote(**self.market)])
        q, = book.quotes.values()
        if q.timestamp != self.ts or q.source != self.source or q.bid_depth is None or q.ask_depth is None:
            raise FxUnavailable("Provenance ou profondeur de change incohérente")
        return book

    def cash_leg(self, quantity, side, now):
        """Cash price of acquiring/selling exactly quantity units of the trade currency."""
        book = self.executable(now)
        if not book:
            return Decimal(1), []
        value = Decimal(str(quantity))
        try:
            if side == "buy":
                rate = book.convert(1, self.base, self.quote, now=now, max_age=self.max_age)["amount"]
                legs = book.convert(value / rate, self.base, self.quote, now=now, max_age=self.max_age, require_depth=True)["legs"]
                return 1 / rate, legs
            out = book.convert(value, self.quote, self.base, now=now, max_age=self.max_age, require_depth=True)
            return out["legs"][0]["rate"], out["legs"]
        except FXError as exc:
            raise FxUnavailable(str(exc)) from exc

    def age(self, now: float) -> float:
        return max(0.0, now - self.ts)

    def to_dict(self, now: float | None = None) -> dict:
        d = asdict(self)
        d["inverse"] = 1.0 / self.rate if self.rate else None  # combien de `quote` pour 1 `base`
        if now is not None:
            d["age_s"] = self.age(now)
            d["stale"] = not 0 <= now - self.ts <= self.max_age
            d["indicative"] = self.approx or self.market is None and self.base != self.quote
        return d


class FxUnavailable(OrderError):
    """Aucun taux de change utilisable : l'ordre est refusé."""

    def __init__(self, message: str):
        super().__init__(message, "no_fx")


Fetcher = Callable[[str, str], FxQuote | None]


def identity(currency: str, now: float) -> FxQuote:
    return FxQuote(currency, currency, 1.0, "même monnaie", now, False, float("inf"))


def market_fetcher(source, max_age: float | Callable[[], float] = 1800.0) -> Fetcher:
    """Read only an explicitly listed direct or inverse market and its exchange timestamp."""
    def fetch(base, quote):
        age = float(max_age() if callable(max_age) else max_age)
        provider = PublicFXProvider(source, source.load_markets(), lambda: source.now_ms() / 1000, max_age=age)
        return FxQuote.from_book(base, quote, provider.snapshot(base, quote), age)

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
        offsets = {"USD": 0.0, "USDC": 0.002, "USDT": -0.003}
        if base != "EUR" or quote not in offsets:
            return None
        now = clock()
        mid = per_base * (1 + offsets[quote] + 0.01 * math.sin(now / 86_400 / 9))
        q = FXQuote(base, quote, mid * 0.9998, mid * 1.0002, now, "carnet simulé (démonstration)", 500, 500)
        return FxQuote.from_book(base, quote, FXBook([q]), 1800.0)

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
            if q.market is not None:
                self.db.execute("INSERT OR IGNORE INTO fx_snapshots(base, quote, ts, source, snapshot) VALUES(?,?,?,?,?)",
                                (q.base, q.quote, q.ts, q.source, dumps(asdict(q))))
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
        if q.quote != currency or q.base != self.base:
            raise FxUnavailable("Devises du taux incompatibles")
        if for_trading:
            q.executable(self.clock())
        return q

    def historical(self, currency, at):
        """Never consult the current source or most recently cached quote during a replay."""
        from ..storage.db import loads
        if self.base == currency:
            return identity(currency, at)
        row = self.db.one("SELECT snapshot FROM fx_snapshots WHERE base=? AND quote=? AND ts<=? "
                          "ORDER BY ts DESC, source LIMIT 1", (self.base, currency, at)) if self.db else None
        if not row:
            raise FxUnavailable(f"Aucun carnet historique {self.base}/{currency} à {at}")
        quote = FxQuote(**loads(row["snapshot"], {}))
        quote.executable(at)
        return quote

    def status(self, currencies) -> dict:
        now = self.clock()
        out = {}
        for cur in currencies:
            if cur == self.base:
                continue
            q = self.latest(cur)
            out[cur] = q.to_dict(now) if q else None
        return {"base": self.base, "rates": out, "error": self.last_error}
