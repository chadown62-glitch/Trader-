"""Série d'un actif étendue bougie par bougie, indicateurs mis à jour sans jamais voir le futur.

``Tape.append`` ajoute UNE bougie close et calcule les valeurs d'indicateurs de cette seule
bougie à partir de l'état précédent. Aucune méthode ne reçoit de bougie postérieure : le moteur
en ligne ne peut donc pas « relire » l'avenir, même par erreur.

Les formules sont celles de ``sentinelle.alpha.bars`` (récurrences et fenêtres identiques) ;
``tests/test_alpha_shadow_engine.py`` vérifie que la série en ligne redonne les mêmes valeurs
que le calcul d'un seul bloc utilisé pendant le développement.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as _windows

from ..bars import Bars, Indicators

_FIELDS = ("ema20", "ema50", "ema200", "sma5", "sma200", "atr14", "atr20", "rsi2", "rsi3", "rsi14", "high20", "low10",
           "low20", "ret1", "ret6", "ret24", "vol24", "atr_rank", "volume_ratio", "spread_bps")
_RAW = ("ts", "open", "high", "low", "close", "volume")
_EXITS = ("close_below_ema50", "close_below_low10", "close_above_ema20", "close_above_sma5")
_WORK = ("tr", "up", "dn", "atr_pct", "cum", "w_up2", "w_dn2", "w_up3", "w_dn3", "w_up14", "w_dn14")


class Tape:
    def __init__(self, symbol: str, tf_ms: int, default_spread_bps: float, provenance: str = "shadow", capacity: int = 2048):
        self.symbol, self.tf_ms, self.default_spread_bps, self.provenance = symbol, tf_ms, float(default_spread_bps), provenance
        self.n = 0
        self._cap = capacity
        self._a: dict[str, np.ndarray] = {k: np.full(capacity, np.nan) for k in _FIELDS + _RAW[1:] + _WORK}
        self._a["ts"] = np.zeros(capacity, dtype=np.int64)
        self._x = {k: np.zeros(capacity, dtype=bool) for k in _EXITS}

    def _grow(self) -> None:
        self._cap *= 2
        for store in (self._a, self._x):
            for k, v in store.items():
                new = np.zeros(self._cap, dtype=v.dtype) if v.dtype != float else np.full(self._cap, np.nan)
                new[:self.n] = v[:self.n]
                store[k] = new

    # -- récurrences (mêmes opérations, dans le même ordre, que bars.ema / bars.wilder) --

    def _ema(self, name: str, src: str, n: int) -> None:
        i, out, x = self.n, self._a[name], self._a[src]
        if i + 1 == n:
            out[i] = float(np.mean(x[:n]))
        elif i + 1 > n:
            a = 2.0 / (n + 1)
            out[i] = a * x[i] + (1 - a) * out[i - 1]

    def _wilder(self, name: str, src: str, n: int) -> None:
        i, out, x = self.n, self._a[name], self._a[src]
        if i + 1 == n:
            out[i] = float(np.mean(x[:n]))
        elif i + 1 > n:
            out[i] = (out[i - 1] * (n - 1) + x[i]) / n

    def _rsi(self, name: str, n: int) -> None:
        i = self.n
        self._wilder(f"w_up{n}", "up", n)
        self._wilder(f"w_dn{n}", "dn", n)
        up, dn = self._a[f"w_up{n}"][i], self._a[f"w_dn{n}"][i]
        if np.isnan(up):
            return
        if dn == 0:
            self._a[name][i] = 100.0 if up > 0 else 50.0
        else:
            self._a[name][i] = 100 - 100 / (1 + up / dn)

    def append(self, ts: int, o: float, h: float, lo: float, c: float, v: float) -> int:
        """Ajoute la bougie suivante (ouverture ``ts``) ; renvoie son indice."""
        if self.n and ts != int(self._a["ts"][self.n - 1]) + self.tf_ms:
            raise ValueError("La série doit rester contiguë : une bougie manquante n'est jamais comblée")
        if self.n == self._cap:
            self._grow()
        i, a = self.n, self._a
        a["ts"][i] = ts
        a["open"][i], a["high"][i], a["low"][i], a["close"][i], a["volume"][i] = o, h, lo, c, v
        close, high, low, vol = a["close"], a["high"], a["low"], a["volume"]
        prev = close[i - 1] if i else c
        a["tr"][i] = max(h - lo, abs(h - prev), abs(lo - prev))
        d = c - prev
        a["up"][i], a["dn"][i] = max(d, 0.0), max(-d, 0.0)
        a["cum"][i] = (a["cum"][i - 1] if i else 0.0) + c  # somme cumulée séquentielle, comme np.cumsum
        for name, n in (("ema20", 20), ("ema50", 50), ("ema200", 200)):
            self._ema(name, "close", n)
        for name, n in (("sma5", 5), ("sma200", 200)):
            if i + 1 >= n:
                a[name][i] = (a["cum"][i] - (a["cum"][i - n] if i >= n else 0.0)) / n
        self._wilder("atr14", "tr", 14)
        self._wilder("atr20", "tr", 20)
        for n in (2, 3, 14):
            self._rsi(f"rsi{n}", n)
        if i >= 20:
            a["high20"][i] = high[i - 20:i].max()
            a["low20"][i] = low[i - 20:i].min()
        if i >= 10:
            a["low10"][i] = low[i - 10:i].min()
        for name, k in (("ret1", 1), ("ret6", 6), ("ret24", 24)):
            if i >= k:
                a[name][i] = np.log(close[i:i + 1] / close[i - k:i - k + 1])[0]
        if i + 1 >= 24:
            a["vol24"][i] = _windows(a["ret1"][i - 23:i + 1], 24).std(axis=1, ddof=1)[0]
        a["atr_pct"][i] = a["atr14"][i] / c
        if i + 1 >= 120:
            w = a["atr_pct"][i - 119:i + 1]
            a["atr_rank"][i] = np.nan if np.isnan(w).any() else (w <= w[-1]).mean()
        if i + 1 >= 50:
            med = np.median(vol[i - 49:i + 1])
            with np.errstate(divide="ignore", invalid="ignore"):
                a["volume_ratio"][i] = np.float64(v) / med
        a["spread_bps"][i] = self.default_spread_bps
        with np.errstate(invalid="ignore"):
            self._x["close_below_ema50"][i] = c < a["ema50"][i]
            self._x["close_below_low10"][i] = c < a["low10"][i]
            self._x["close_above_ema20"][i] = c > a["ema20"][i]
            self._x["close_above_sma5"][i] = c > a["sma5"][i]
        self.n += 1
        return i

    # -- vues en lecture seule sur ce qui est déjà connu --

    def _view(self, arr: np.ndarray, lo: int = 0) -> np.ndarray:
        v = arr[lo:self.n]
        v.flags.writeable = False
        return v

    def bars(self, lo: int = 0) -> Bars:
        a = self._a
        return Bars(self.symbol, self._view(a["ts"], lo), self._view(a["open"], lo), self._view(a["high"], lo),
                    self._view(a["low"], lo), self._view(a["close"], lo), self._view(a["volume"], lo), None, self.tf_ms,
                    self.provenance)

    def indicators(self, lo: int = 0) -> Indicators:
        return Indicators(**{k: self._view(self._a[k], lo) for k in _FIELDS})

    def exits(self) -> dict[str, np.ndarray]:
        return {k: self._view(v) for k, v in self._x.items()}

    @property
    def last_open(self) -> int | None:
        return int(self._a["ts"][self.n - 1]) if self.n else None

    @property
    def first_open(self) -> int | None:
        return int(self._a["ts"][0]) if self.n else None

    def index_of(self, open_ms: int) -> int:
        i = (open_ms - int(self._a["ts"][0])) // self.tf_ms
        if not 0 <= i < self.n or int(self._a["ts"][i]) != open_ms:
            raise KeyError(open_ms)
        return int(i)
