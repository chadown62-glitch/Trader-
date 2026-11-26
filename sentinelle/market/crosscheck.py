"""Contrôle croisé des prix avant les entrées fictives de l'IA."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from numbers import Real

from .hub import STALE_SECONDS
from .sources import make_ccxt_exchange


class QuoteState(str, Enum):
    VERIFIED = "VERIFIED"
    INCONSISTENT = "INCONSISTENT"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"


@dataclass(frozen=True)
class CrossCheck:
    state: QuoteState
    detail: str

    @property
    def veto(self) -> str | None:
        return None if self.state == QuoteState.VERIFIED else f"{self.state.value} : {self.detail}"


def _positive(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


@dataclass(frozen=True)
class Quote:
    price: float | None
    quoted_at: float | None
    received_at: float | None

    def problem(self, now: float, label: str, max_age: float = STALE_SECONDS) -> CrossCheck | None:
        if not all(_positive(v) is not None for v in (self.price, self.quoted_at, self.received_at)):
            return CrossCheck(QuoteState.UNVERIFIED, f"{label} : prix ou horodatage absent ou invalide")
        if self.quoted_at > self.received_at or self.received_at > now:
            return CrossCheck(QuoteState.UNVERIFIED, f"{label} : horodatage futur ou incohérent")
        if now - self.quoted_at > max_age:
            return CrossCheck(QuoteState.STALE, f"{label} : cotation périmée")
        return None


class CrossChecker:
    def __init__(self, primary: str = "binance", ttl: float = 30.0, exchange=None, *, clock=time.time):
        self.second_id = "kraken" if primary != "kraken" else "binance"
        self._ex = exchange
        self.ttl = ttl
        self.clock = clock
        self._cache: dict[str, tuple[Quote, str | None]] = {}
        self._markets: set[str] | None = None
        self._lock = threading.Lock()
        self.status = {"source": self.second_id, "state": None, "last_error": None}

    def _reference(self, symbol: str) -> tuple[Quote, str | None]:
        with self._lock:
            cached = self._cache.get(symbol)
            if cached and 0 <= self.clock() - cached[0].received_at < self.ttl:
                return cached
            price = timestamp = error = None
            try:
                if self._ex is None:
                    self._ex = make_ccxt_exchange(self.second_id, public_mirror=True)
                if self._markets is None:
                    self._markets = set(self._ex.load_markets())
                if symbol not in self._markets:
                    error = f"{symbol} absent de {self.second_id}"
                else:
                    ticker = self._ex.fetch_ticker(symbol)
                    if ticker.get("symbol") not in (None, symbol):
                        error = "Paire de référence différente de la paire demandée"
                    else:
                        last = ticker.get("last")
                        price = _positive(ticker.get("close") if last is None else last)
                        timestamp_ms = _positive(ticker.get("timestamp"))
                        timestamp = timestamp_ms / 1000 if timestamp_ms is not None else None
            except Exception as exc:
                error = f"{self.second_id} indisponible ({type(exc).__name__})"
            result = Quote(price, timestamp, self.clock()), error
            self._cache[symbol] = result
            return result

    def check(self, symbol: str, quote: Quote, max_pct: float, *,
              signal: tuple[float, float] | None = None) -> CrossCheck:
        """Compare les prix de la décision et du marché au même ticker indépendant."""
        result = self._compare(symbol, quote, max_pct, signal)
        self.status = {"source": self.second_id, "symbol": symbol,
                       "state": result.state.value, "last_error": result.veto}
        return result

    def _compare(self, symbol, quote, max_pct, signal) -> CrossCheck:
        started = self.clock()
        if _positive(started) is None or _positive(max_pct) is None:
            return CrossCheck(QuoteState.UNVERIFIED, "Horloge ou tolérance invalide")
        problem = quote.problem(started, "Marché principal")
        if problem is not None:
            return problem
        if signal is not None:
            close, closed_at = signal
            if _positive(close) is None or _positive(closed_at) is None or closed_at > started:
                return CrossCheck(QuoteState.UNVERIFIED, "Prix ou date de clôture du signal invalide")
        reference, error = self._reference(symbol)
        now = self.clock()  # après le réseau, également sur un hit de cache
        if _positive(now) is None or now < started:
            return CrossCheck(QuoteState.UNVERIFIED, "Horloge incohérente pendant le contrôle")
        if error:
            return CrossCheck(QuoteState.UNVERIFIED, error)
        for observation, label in ((quote, "Marché principal"), (reference, self.second_id)):
            problem = observation.problem(now, label)
            if problem is not None:
                return problem
        prices = [quote.price]
        if signal is not None:
            close, closed_at = signal
            if now - closed_at > STALE_SECONDS:
                return CrossCheck(QuoteState.STALE, "Clôture du signal périmée")
            prices.append(close)
        gap = max(abs(px - reference.price) / reference.price * 100 for px in prices)
        if gap > max_pct:
            return CrossCheck(QuoteState.INCONSISTENT, f"Prix incohérent avec {self.second_id} (écart {gap:.2f} %)")
        return CrossCheck(QuoteState.VERIFIED, "Cotations datées et concordantes")
