"""Bougies et indicateurs strictement causaux.

Convention : la valeur d'un indicateur à l'indice ``i`` n'utilise que les bougies ``0..i``
(closes à ``ts[i] + tf_ms``). Une décision prise à la clôture de ``i`` s'exécute à
l'ouverture de ``i + 1``. ``tests/test_alpha_core.py`` vérifie l'invariance par préfixe :
calculer sur ``bars[:k]`` ou sur ``bars`` donne les mêmes valeurs jusqu'à ``k - 1``.
"""
from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as _windows

TF_4H_MS = 14_400_000


@dataclass(frozen=True)
class Bars:
    symbol: str
    ts: np.ndarray  # ouverture de chaque bougie, ms UTC
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    spread_bps: np.ndarray | None = None  # écart achat/vente observé (bps) ; None = hypothèse du modèle de coûts
    tf_ms: int = TF_4H_MS
    provenance: str = "synthetic"

    def __len__(self) -> int:
        return len(self.ts)

    def prefix(self, n: int) -> "Bars":
        """Les ``n`` premières bougies : ce qui était connu à la clôture de la bougie ``n - 1``."""
        cut = lambda a: None if a is None else a[:n]  # noqa: E731
        return Bars(self.symbol, self.ts[:n], self.open[:n], self.high[:n], self.low[:n], self.close[:n],
                    self.volume[:n], cut(self.spread_bps), self.tf_ms, self.provenance)

    def close_time(self, i: int) -> int:
        return int(self.ts[i]) + self.tf_ms


@dataclass(frozen=True)
class Universe:
    """Plusieurs actifs sur la même grille temporelle (bougies alignées)."""
    bars: dict[str, Bars]
    truth: dict = field(default_factory=dict)  # vérité des marchés synthétiques : RÉSERVÉE à l'évaluation

    @property
    def symbols(self) -> list[str]:
        return sorted(self.bars)

    @property
    def length(self) -> int:
        return len(next(iter(self.bars.values())))

    @property
    def ts(self) -> np.ndarray:
        return next(iter(self.bars.values())).ts

    def prefix(self, n: int) -> "Universe":
        return Universe({s: b.prefix(n) for s, b in self.bars.items()}, {})


# ---------------------------------------------------------------------------
# Indicateurs (NaN pendant l'échauffement)
# ---------------------------------------------------------------------------


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(np.asarray(x, float), 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for i in range(n, len(x)):
        v = a * x[i] + (1 - a) * v
        out[i] = v
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for i in range(n, len(x)):
        v = (v * (n - 1) + x[i]) / n
        out[i] = v
    return out


def atr(b: Bars, n: int = 14) -> np.ndarray:
    prev = np.concatenate([[b.close[0]], b.close[:-1]])
    tr = np.maximum(b.high - b.low, np.maximum(np.abs(b.high - prev), np.abs(b.low - prev)))
    return wilder(tr, n)


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(close, prepend=close[0])
    up, dn = wilder(np.maximum(d, 0.0), n), wilder(np.maximum(-d, 0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = up / dn
        out = 100 - 100 / (1 + rs)
    out[(dn == 0) & (up > 0)] = 100.0
    out[(dn == 0) & (up == 0)] = 50.0
    out[np.isnan(up)] = np.nan
    return out


def prior_max(x: np.ndarray, n: int) -> np.ndarray:
    """Plus haut des ``n`` bougies PRÉCÉDENTES (la bougie courante exclue)."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = _windows(np.asarray(x, float), n).max(axis=1)[:-1]
    return out


def prior_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = _windows(np.asarray(x, float), n).min(axis=1)[:-1]
    return out


def log_returns(close: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) > k:
        out[k:] = np.log(close[k:] / close[:-k])
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        w = _windows(np.asarray(x, float), n)
        out[n - 1:] = w.std(axis=1, ddof=1)  # NaN si la fenêtre contient un NaN
    return out


def rolling_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Rang (0..1) de la valeur courante parmi les ``n`` dernières : repère les extrêmes sans futur."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        w = _windows(np.asarray(x, float), n)
        r = (w <= w[:, -1:]).mean(axis=1)
        r[np.isnan(w).any(axis=1)] = np.nan
        out[n - 1:] = r
    return out


def rolling_median(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.median(_windows(np.asarray(x, float), n), axis=1)
    return out


@dataclass(frozen=True)
class Indicators:
    ema20: np.ndarray
    ema50: np.ndarray
    ema200: np.ndarray
    sma5: np.ndarray
    sma200: np.ndarray
    atr14: np.ndarray
    atr20: np.ndarray
    rsi2: np.ndarray
    rsi3: np.ndarray
    rsi14: np.ndarray
    high20: np.ndarray
    low10: np.ndarray
    low20: np.ndarray
    ret1: np.ndarray
    ret6: np.ndarray
    ret24: np.ndarray
    vol24: np.ndarray
    atr_rank: np.ndarray
    volume_ratio: np.ndarray
    spread_bps: np.ndarray


def indicators(b: Bars, default_spread_bps: float = 4.0) -> Indicators:
    c = b.close.astype(float)
    r1 = log_returns(c, 1)
    a14 = atr(b, 14)
    vol_med = rolling_median(b.volume, 50)
    with np.errstate(divide="ignore", invalid="ignore"):
        vratio = b.volume / vol_med
    spread = b.spread_bps.astype(float) if b.spread_bps is not None else np.full(len(c), default_spread_bps)
    return Indicators(
        ema20=ema(c, 20), ema50=ema(c, 50), ema200=ema(c, 200), sma5=sma(c, 5), sma200=sma(c, 200),
        atr14=a14, atr20=atr(b, 20), rsi2=rsi(c, 2), rsi3=rsi(c, 3), rsi14=rsi(c, 14),
        high20=prior_max(b.high, 20), low10=prior_min(b.low, 10), low20=prior_min(b.low, 20),
        ret1=r1, ret6=log_returns(c, 6), ret24=log_returns(c, 24), vol24=rolling_std(r1, 24),
        atr_rank=rolling_rank(a14 / c, 120), volume_ratio=vratio, spread_bps=spread,
    )


FEATURES = ("ret1", "ret6", "ret24", "vol24", "atr_pct", "rsi3", "rsi14", "d_ema20", "d_ema50", "d_ema200",
            "trend", "breakout_dist", "atr_rank", "volume_ratio", "spread_bps")


def feature_row(b: Bars, ind: Indicators, i: int) -> np.ndarray:
    """Caractéristiques connues à la clôture de la bougie ``i`` (aucune donnée postérieure)."""
    c = float(b.close[i])
    a = float(ind.atr14[i])
    return np.array([
        ind.ret1[i], ind.ret6[i], ind.ret24[i], ind.vol24[i], a / c,
        ind.rsi3[i] / 100, ind.rsi14[i] / 100,
        c / ind.ema20[i] - 1, c / ind.ema50[i] - 1, c / ind.ema200[i] - 1,
        1.0 if ind.ema50[i] > ind.ema200[i] else -1.0,
        (c - ind.high20[i]) / a if a > 0 else np.nan,
        ind.atr_rank[i], ind.volume_ratio[i], ind.spread_bps[i],
    ], dtype=float)


# ---------------------------------------------------------------------------
# Archives publiques : lecture seule
# ---------------------------------------------------------------------------


def load_history_csv(path: Path, symbol: str, *, tf_ms: int = TF_4H_MS) -> tuple[Bars, str]:
    """Lit un cache d'historique public (format HistoryStore : ts,open,high,low,close,volume, gzip).

    Lecture seule ; renvoie aussi l'empreinte SHA-256 du fichier pour la traçabilité. Les bougies
    invalides ou dupliquées sont écartées, jamais corrigées.
    """
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows = []
    for k, line in enumerate(gzip.decompress(raw).decode("utf-8").splitlines()):
        if k == 0:
            if not line.startswith("ts,"):
                raise ValueError("En-tête d'historique inattendu")
            continue
        parts = line.split(",")
        try:
            vals = [float(x) for x in parts[:6]]
        except ValueError:
            continue
        rows.append(vals)
    a = np.array(rows, dtype=float)
    _, keep = np.unique(a[:, 0], return_index=True)
    a = a[np.sort(keep)]
    ok = (a[:, 1:5] > 0).all(axis=1) & (a[:, 2] >= np.maximum(a[:, 1], a[:, 4])) & (a[:, 3] <= np.minimum(a[:, 1], a[:, 4]))
    a = a[ok]
    return Bars(symbol, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], None, tf_ms,
                f"public:{Path(path).name}:sha256={digest}"), digest


def align(bars: list[Bars]) -> Universe:
    """Garde les instants communs à tous les actifs (aucune bougie inventée pour combler un trou)."""
    common = set(bars[0].ts.tolist())
    for b in bars[1:]:
        common &= set(b.ts.tolist())
    grid = np.array(sorted(common), dtype=np.int64)
    out = {}
    for b in bars:
        idx = np.searchsorted(b.ts, grid)
        spread = None if b.spread_bps is None else b.spread_bps[idx]
        out[b.symbol] = Bars(b.symbol, grid, b.open[idx], b.high[idx], b.low[idx], b.close[idx], b.volume[idx],
                             spread, b.tf_ms, b.provenance)
    return Universe(out)
