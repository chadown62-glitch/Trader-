"""Veilleurs : le carnet de stratégies d'un trader, appliqué en continu à chaque marché suivi.

Un veilleur ne décide rien. À la clôture d'une bougie de 5 minutes, il reconnaît une configuration connue et
émet une alerte avec un plan complet, comme un trader l'écrirait avant d'entrer :
- la thèse (pourquoi maintenant) et le contexte (tendance de fond, marché, volume) ;
- le niveau d'invalidation (stop sous une structure, avec une marge de volatilité) ;
- l'objectif (au moins 2 fois le risque) et la durée maximale ;
- le coût du trade rapporté au risque : une alerte dont les frais mangent l'avantage est écartée.

L'entrée se fait à l'ouverture de la bougie suivante ; l'IA principale décide si elle est prise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ROUND_TRIP = 0.0022  # frais 0,1 % et glissement 0,01 % par côté (protocole)
MAX_STOP = 0.04  # risque d'au plus 4 % du prix ; le stop minimal découle du coût (coût ≤ 0,25 R)
REWARD_R = 2.0


@dataclass(frozen=True)
class Setup:
    name: str
    label: str
    max_minutes: int


SETUPS = {
    "pullback": Setup("pullback", "Repli dans une tendance haussière", 720),
    "breakout": Setup("breakout", "Cassure de range avec volume", 360),
    "capitulation": Setup("capitulation", "Rebond après capitulation", 240),
    "squeeze": Setup("squeeze", "Expansion après compression", 360),
}


def _first(sig: pd.Series) -> pd.Series:
    sig = sig.fillna(False).astype(bool)
    return sig & ~sig.shift(1, fill_value=False)


def detect(f: pd.DataFrame, *, daily_trend: bool = False) -> dict[str, tuple[pd.Series, pd.Series]]:
    """Pour chaque configuration : (déclenchement à la clôture, niveau du stop).

    « daily_trend » : comme un trader qui ne joue que dans le sens de la tendance de fond, les configurations de
    tendance (repli, cassure, compression) exigent un prix au-dessus de sa moyenne de 20 jours, elle-même montante."""
    c, o, h, l, atr = f["c"], f["o"], f["h"], f["l"], f["atr"]
    up_1h = (f["trend_1h"] > 0) & (f["slope_1h"] > 0)
    btc_ok = f["btc_ret_1h"] > -0.015
    if daily_trend:
        btc_ok = btc_ok & (f["trend_1d"] > 0) & (f["slope_1d"] > 0)
    bullish = (c > o) & (c > (h + l) / 2)
    out = {}
    # 1. Repli : tendance d'une heure haussière, le prix revient sur sa moyenne 20 puis repart (confirmation).
    touched = l.rolling(3).min() <= f["ema20"] + 0.25 * atr
    sig = up_1h & (f["trend_4h"] > 0) & touched & (c > f["ema20"]) & bullish & (c > h.shift(1)) & (c < f["hi24"]) & btc_ok
    out["pullback"] = (_first(sig), l.rolling(6).min() - 0.25 * atr)
    # 2. Cassure : range serré de 2 heures cassé à la clôture, volume inhabituel, acheteurs au marché dominants.
    sig = (c > f["hi24"]) & (f["range_height"] <= 6) & (f["vol_z"] >= 2) & (f["taker_ratio"] >= 0.55) & (f["trend_1h"] > -0.002) & btc_ok
    out["breakout"] = (_first(sig), (f["hi24"] + f["lo24"]) / 2 - 0.25 * atr)
    # 3. Capitulation : chute de 4 ATR en 30 minutes sur volume extrême, puis bougie de reprise ; marché pas en chute libre.
    flush = (c.shift(6) - l.rolling(6).min()) / atr >= 4
    sig = flush & (f["vol_z"] >= 3) & bullish & (f["btc_ret_1h"] > -0.03)
    out["capitulation"] = (_first(sig), l.rolling(6).min() - 0.25 * atr)
    # 4. Compression : volatilité au plus bas de deux jours, puis clôture au-dessus de la bande haute, tendance non baissière.
    sig = (f["squeeze"].rolling(3).min() <= 1.05) & (c > f["bb_up"]) & (f["trend_1h"] >= 0) & btc_ok
    out["squeeze"] = (_first(sig), f["bb_mid"] - 0.25 * atr)
    return out


def _thesis(setup: Setup, row) -> str:
    trend = "haussière" if row.trend_1h > 0 else "neutre ou baissière"
    market = "BTC stable" if row.btc_ret_1h > -0.005 else "BTC en repli"
    return (f"{setup.label} : tendance d'une heure {trend} ({row.trend_1h:+.2%}), volume {row.vol_z:+.1f} écarts, "
            f"{row.taker_ratio:.0%} d'achats au marché, {market}. Invalidation sous {row.stop:.6g}, objectif {REWARD_R:g} R.")


def alerts(symbol: str, f: pd.DataFrame, *, round_trip: float = ROUND_TRIP, daily_trend: bool = False) -> pd.DataFrame:
    """Alertes d'un marché : une ligne par configuration déclenchée, avec son plan et ses caractéristiques.

    Le stop minimal découle du coût : un trade dont les frais dépassent 0,25 R est écarté."""
    rows, min_stop = [], round_trip / 0.25
    for name, (sig, stop) in detect(f, daily_trend=daily_trend).items():
        setup = SETUPS[name]
        idx = np.flatnonzero(sig.to_numpy() & f[["atr", "trend_1h", "btc_ret_1h", "vol_z"]].notna().all(axis=1).to_numpy())
        if not len(idx):
            continue
        a = f.iloc[idx].copy()
        a["stop"] = stop.iloc[idx].to_numpy()
        a["risk_pct"] = (a["c"] - a["stop"]) / a["c"]
        a = a[(a["risk_pct"] >= min_stop) & (a["risk_pct"] <= MAX_STOP)]
        if a.empty:
            continue
        a["setup"], a["symbol"], a["max_minutes"] = name, symbol, setup.max_minutes
        a["cost_r"] = round_trip / a["risk_pct"]
        a["decided_at"] = a["t"] + 300_000  # clôture de la bougie
        a["thesis"] = [_thesis(setup, r) for r in a.itertuples()]
        rows.append(a)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows).sort_values(["decided_at", "setup"]).reset_index(drop=True)
