"""Vérification croisée des prix : avant chaque entrée de l'IA, le prix est
comparé à celui d'une seconde plateforme. Un écart anormal (donnée erronée,
plateforme en difficulté) bloque le trade.
"""

from __future__ import annotations

import logging
import threading
import time

from .sources import make_ccxt_exchange

log = logging.getLogger("sentinelle.crosscheck")


class CrossChecker:
    def __init__(self, primary: str = "binance", ttl: float = 30.0, exchange=None):
        self.second_id = "kraken" if primary != "kraken" else "binance"
        self._ex = exchange
        self.ttl = ttl
        self._cache: dict[str, tuple[float, float | None]] = {}
        self._markets: set[str] | None = None
        self._lock = threading.Lock()
        self.status = {"source": self.second_id, "last_error": None}

    def _exchange(self):
        if self._ex is None:
            self._ex = make_ccxt_exchange(self.second_id, public_mirror=True)
        return self._ex

    def reference(self, symbol: str) -> float | None:
        with self._lock:
            hit = self._cache.get(symbol)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
            try:
                ex = self._exchange()
                if self._markets is None:
                    self._markets = set(ex.load_markets())
                if symbol not in self._markets:
                    px = None
                else:
                    t = ex.fetch_ticker(symbol)
                    px = float(t.get("last") or t.get("close") or 0) or None
                self.status["last_error"] = None
            except Exception as exc:
                self.status["last_error"] = str(exc)[:200]
                px = None
            self._cache[symbol] = (time.time(), px)
            return px

    def check(self, symbol: str, price: float, max_pct: float) -> str | None:
        """Renvoie un message si l'écart dépasse `max_pct` %, sinon None (y compris si la 2e source manque)."""
        ref = self.reference(symbol)
        if not ref or not price:
            return None
        gap = abs(price / ref - 1) * 100
        if gap > max_pct:
            return f"prix incohérent entre deux plateformes ({gap:.1f} % d'écart avec {self.second_id})"
        return None
