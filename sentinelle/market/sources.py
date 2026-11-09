"""Sources de données de marché.

- `CcxtSource` : données réelles d'un exchange (par défaut Binance, via son
  miroir officiel de données publiques `data-api.binance.vision`, sans compte).
- `SyntheticSource` : marché simulé, déterministe, pour la démo hors-ligne et
  les tests automatiques.

Format des bougies : [ts_ms, open, high, low, close, volume] (convention CCXT).
La dernière bougie renvoyée peut être en cours de formation.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Callable, Protocol

import numpy as np

from ..config import timeframe_ms
from .markets import MarketInfo, generic_market

BINANCE_PUBLIC_MIRROR = "https://data-api.binance.vision/api/v3"


class MarketSource(Protocol):
    name: str

    def load_markets(self) -> dict[str, MarketInfo]: ...

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int = 500
    ) -> list[list[float]]: ...

    def now_ms(self) -> int: ...


def make_ccxt_exchange(
    exchange_id: str,
    *,
    api_key: str | None = None,
    secret: str | None = None,
    password: str | None = None,
    testnet: bool = False,
    public_mirror: bool = False,
    timeout_ms: int = 15000,
):
    """Crée une instance CCXT synchrone configurée prudemment (spot uniquement)."""
    import ccxt  # import local : dépendance lourde

    cls = getattr(ccxt, exchange_id)
    cfg: dict = {
        "enableRateLimit": True,
        "timeout": timeout_ms,
        # respecte les réglages réseau du système (proxy, certificats)
        "requests_trust_env": True,
        "options": {"defaultType": "spot"},
    }
    if exchange_id == "binance":
        cfg["options"]["fetchMarkets"] = {"types": ["spot"]}
        # évite des appels aux API de produits dérivés (inutiles ici)
        cfg["options"]["fetchCurrencies"] = False
    if api_key:
        cfg["apiKey"] = api_key
        cfg["secret"] = secret or ""
        if password:
            cfg["password"] = password
    ex = cls(cfg)
    if testnet:
        ex.set_sandbox_mode(True)
    elif public_mirror and exchange_id == "binance" and not api_key:
        ex.urls["api"]["public"] = BINANCE_PUBLIC_MIRROR
    return ex


def market_from_ccxt(m: dict, precision_mode: int) -> MarketInfo:
    import ccxt

    prec = m.get("precision") or {}
    limits = m.get("limits") or {}

    def step(value, default):
        if value is None:
            return default
        if precision_mode == ccxt.TICK_SIZE:
            return float(value)
        return float(10 ** (-int(value)))

    info = m.get("info") or {}
    order_types = tuple(info.get("orderTypes") or ())
    return MarketInfo(
        symbol=m["symbol"],
        base=m["base"],
        quote=m["quote"],
        amount_step=step(prec.get("amount"), 1e-8),
        price_step=step(prec.get("price"), 0.01),
        min_amount=float((limits.get("amount") or {}).get("min") or 0.0),
        min_notional=float((limits.get("cost") or {}).get("min") or 0.0),
        taker_fee=float(m.get("taker") or 0.001),
        maker_fee=float(m.get("maker") or 0.001),
        order_types=order_types,
    )


class CcxtSource:
    """Données publiques réelles via CCXT."""

    def __init__(self, exchange_id: str = "binance", public_mirror: bool = True):
        self.exchange_id = exchange_id
        self.name = exchange_id
        self._ex = make_ccxt_exchange(exchange_id, public_mirror=public_mirror)
        self._lock = threading.Lock()  # une instance CCXT n'est pas thread-safe

    def load_markets(self) -> dict[str, MarketInfo]:
        with self._lock:
            raw = self._ex.load_markets()
            mode = self._ex.precisionMode
        out = {}
        for sym, m in raw.items():
            if m.get("spot") and m.get("active", True) is not False and ":" not in sym:
                out[sym] = market_from_ccxt(m, mode)
        return out

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
        with self._lock:
            rows = self._ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0)] for r in rows]

    def now_ms(self) -> int:
        return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Marché simulé
# ---------------------------------------------------------------------------

_START_PRICES = {"BTC": 60000.0, "ETH": 3000.0, "SOL": 150.0, "BNB": 550.0, "XRP": 0.6}


class SyntheticSource:
    """Marché simulé réaliste (tendances, phases de range, volatilité variable).

    Les prix sont générés minute par minute de façon déterministe à partir d'une
    graine : deux instances avec la même graine produisent le même marché.
    """

    name = "synthetic"

    def __init__(
        self,
        symbols: list[str],
        seed: int = 7,
        days: int = 60,
        clock: Callable[[], float] = time.time,
    ):
        self.seed = seed
        self.clock = clock
        self.symbols = list(symbols)
        self._lock = threading.Lock()
        now_min = self._minute_floor(self.now_ms())
        self._start = now_min - days * 86_400_000
        self._data: dict[str, dict[str, np.ndarray]] = {}
        self._rng: dict[str, np.random.Generator] = {}
        self._state: dict[str, dict] = {}
        for s in self.symbols:
            self._init_symbol(s)

    # -- temps ---------------------------------------------------------------
    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    @staticmethod
    def _minute_floor(ms: int) -> int:
        return ms - ms % 60_000

    # -- génération ---------------------------------------------------------
    def _init_symbol(self, symbol: str) -> None:
        h = int(hashlib.sha256(f"{symbol}:{self.seed}".encode()).hexdigest()[:12], 16)
        self._rng[symbol] = np.random.default_rng(h)
        base = symbol.split("/")[0]
        self._state[symbol] = {"price": _START_PRICES.get(base, 100.0), "regime": 0, "vol": 1.0}
        self._data[symbol] = {k: np.empty(0) for k in ("ts", "o", "h", "l", "c", "v")}
        self._data[symbol]["ts"] = np.empty(0, dtype=np.int64)

    def _generate(self, symbol: str, n: int) -> None:
        rng = self._rng[symbol]
        st = self._state[symbol]
        d = self._data[symbol]
        first_ts = self._start if len(d["ts"]) == 0 else int(d["ts"][-1]) + 60_000
        # Régimes : 0 = range, 1 = haussier, 2 = baissier (changent en moyenne toutes les ~3 jours)
        drift = {0: 0.0, 1: 3.5e-5, 2: -3.5e-5}
        rets = np.empty(n)
        regime = st["regime"]
        vol = st["vol"]
        switch = rng.random(n) < 1.0 / (3 * 1440)
        shocks = rng.standard_normal(n)
        vol_noise = rng.standard_normal(n)
        for i in range(n):
            if switch[i]:
                regime = int(rng.integers(0, 3))
            # volatilité en grappes (processus de retour à la moyenne)
            vol = max(0.3, min(3.0, vol + 0.02 * (1.0 - vol) + 0.03 * vol_noise[i]))
            rets[i] = drift[regime] + 0.0007 * vol * shocks[i]
        st["regime"], st["vol"] = regime, vol
        closes = st["price"] * np.exp(np.cumsum(rets))
        opens = np.concatenate([[st["price"]], closes[:-1]])
        spread = np.abs(rng.standard_normal(n)) * 0.00035 * closes
        highs = np.maximum(opens, closes) + spread
        lows = np.minimum(opens, closes) - np.abs(rng.standard_normal(n)) * 0.00035 * closes
        vols = np.exp(rng.standard_normal(n) * 0.5) * (1 + 400 * np.abs(rets)) * 10.0
        st["price"] = float(closes[-1])
        ts = first_ts + 60_000 * np.arange(n, dtype=np.int64)
        for k, arr in (("ts", ts), ("o", opens), ("h", highs), ("l", lows), ("c", closes), ("v", vols)):
            d[k] = np.concatenate([d[k], arr])

    def _ensure(self, symbol: str, until_ms: int) -> None:
        d = self._data[symbol]
        last = int(d["ts"][-1]) if len(d["ts"]) else self._start - 60_000
        missing = (until_ms - last) // 60_000
        if missing > 0:
            self._generate(symbol, int(missing))

    # -- interface -------------------------------------------------------------
    def load_markets(self) -> dict[str, MarketInfo]:
        return {s: generic_market(s) for s in self.symbols}

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
        if symbol not in self._data:
            raise ValueError(f"Paire inconnue du marché simulé : {symbol}")
        now = self.now_ms()
        cur_min = self._minute_floor(now)
        with self._lock:
            self._ensure(symbol, cur_min)  # inclut la minute en cours (révélée partiellement)
            d = self._data[symbol]
            ts, o, h, l, c, v = d["ts"], d["o"], d["h"], d["l"], d["c"], d["v"]
            n = int(np.searchsorted(ts, cur_min, side="right"))  # jusqu'à la minute courante incluse
            ts, o, h, l, c, v = ts[:n], o[:n], h[:n], l[:n], c[:n], v[:n].copy()
            h = h.copy()
            l = l.copy()
            c = c.copy()
            if n and ts[-1] == cur_min:
                # minute en cours : on ne révèle qu'une partie de la bougie
                f = min(1.0, max(0.0, (now - cur_min) / 60_000))
                o_, H, L, C = o[-1], h[-1], l[-1], c[-1]
                cp = o_ + (C - o_) * f
                h[-1] = max(o_, cp) + (H - max(o_, C)) * f
                l[-1] = min(o_, cp) - (min(o_, C) - L) * f
                c[-1] = cp
                v[-1] = v[-1] * f
        tf = timeframe_ms(timeframe)
        if tf == 60_000:
            rows = np.column_stack([ts.astype(float), o, h, l, c, v])
        else:
            bucket = ts - ts % tf
            idx = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
            rows = np.column_stack(
                [
                    bucket[idx].astype(float),
                    o[idx],
                    np.maximum.reduceat(h, idx),
                    np.minimum.reduceat(l, idx),
                    c[np.r_[idx[1:] - 1, len(c) - 1]],
                    np.add.reduceat(v, idx),
                ]
            )
        if since is not None:
            rows = rows[rows[:, 0] >= since]
            rows = rows[:limit]
        else:
            rows = rows[-limit:]
        return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]


def build_source(settings: dict) -> MarketSource:
    m = settings["market"]
    if m["source"] == "synthetic":
        days = max(60, min(int(m.get("history_days", 365)), 400))
        return SyntheticSource(settings["general"]["watchlist"], days=days)
    return CcxtSource(m["source"], public_mirror=bool(m.get("binance_public_mirror", True)))


def is_finite_price(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x) and x > 0
