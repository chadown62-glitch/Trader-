"""Stratégies « génératrices de candidats ».

Chaque stratégie repère des situations d'achat classiques et propose un stop,
un objectif et une durée maximale. L'IA (méta-modèle) apprend ensuite lesquels
de ces candidats valent vraiment la peine d'être pris.
Toutes les stratégies sont « long only » (achat puis revente), sans levier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    description: str
    stop_atr: float
    target_atr: float
    horizon_bars: int
    breakeven_r: float | None
    signal: Callable[[pd.DataFrame], pd.Series]

    @property
    def reward_risk(self) -> float:
        return self.target_atr / self.stop_atr


def _first_of_run(sig: pd.Series) -> pd.Series:
    """Ne garde que la première bougie d'une série de signaux consécutifs."""
    sig = sig.fillna(False).astype(bool)
    return sig & ~sig.shift(1, fill_value=False)


def _trend_breakout(f: pd.DataFrame) -> pd.Series:
    sig = (f["close"] > f["don_high"]) & (f["regime"] != "bear") & (f["close"] > f["ema50"]) & (f["slope_ema20"] > 0)
    return _first_of_run(sig)


def _pullback(f: pd.DataFrame) -> pd.Series:
    sig = (f["regime"] == "bull") & (f["close"] > f["ema50"]) & (f["rsi14"] < 40) & (f["close"] < f["ema20"])
    return _first_of_run(sig)


def _mean_reversion(f: pd.DataFrame) -> pd.Series:
    sig = (f["regime"] != "bear") & (f["regime"] != "unknown") & (f["close"] < f["bb_lower"]) & (f["rsi14"] < 30)
    return _first_of_run(sig)


def _momentum(f: pd.DataFrame) -> pd.Series:
    cross = (f["ema20"] > f["ema50"]) & (f["ema20"].shift(1) <= f["ema50"].shift(1))
    sig = cross & (f["macd_hist"] > 0) & (f["regime"] != "bear")
    return _first_of_run(sig)


STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in (
        Strategy(
            "trend_breakout",
            "Cassure de tendance",
            "Achat quand le prix dépasse le plus haut des 20 dernières bougies, en tendance haussière.",
            stop_atr=2.0, target_atr=4.0, horizon_bars=48, breakeven_r=1.5, signal=_trend_breakout,
        ),
        Strategy(
            "pullback",
            "Repli en tendance",
            "Achat sur une baisse passagère (RSI bas) au sein d'une tendance haussière de fond.",
            stop_atr=1.5, target_atr=2.0, horizon_bars=24, breakeven_r=None, signal=_pullback,
        ),
        Strategy(
            "mean_reversion",
            "Retour à la moyenne",
            "Achat quand le prix sort nettement sous sa bande de Bollinger basse (survente).",
            stop_atr=1.5, target_atr=1.5, horizon_bars=24, breakeven_r=None, signal=_mean_reversion,
        ),
        Strategy(
            "momentum",
            "Élan haussier",
            "Achat quand la moyenne courte croise au-dessus de la moyenne longue avec un MACD positif.",
            stop_atr=2.0, target_atr=3.0, horizon_bars=72, breakeven_r=1.0, signal=_momentum,
        ),
    )
}


def signals(features: pd.DataFrame, enabled: list[str] | None = None) -> dict[str, np.ndarray]:
    """Indices des bougies où chaque stratégie donne un signal."""
    names = enabled if enabled is not None else list(STRATEGIES)
    out = {}
    for name in names:
        s = STRATEGIES[name].signal(features)
        valid = features["atr"].notna() & (features["atr"] > 0)
        out[name] = np.flatnonzero((s & valid).to_numpy())
    return out


def levels(strategy: Strategy, close: float, atr_value: float) -> tuple[float, float]:
    """Stop et objectif à partir du prix de clôture et de la volatilité."""
    return close - strategy.stop_atr * atr_value, close + strategy.target_atr * atr_value
