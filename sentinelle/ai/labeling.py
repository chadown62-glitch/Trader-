"""Génération et étiquetage des candidats sur l'historique (« méthode des triples barrières »).

Pour chaque signal d'une stratégie à la clôture de la bougie i :
- entrée au prix d'ouverture de la bougie i+1, glissement compris ;
- stop et objectif calculés à la clôture de i (comme en direct) ;
- simulation bougie par bougie avec exactement les règles de sortie du trading
  réel (`exits.py`) jusqu'au stop, à l'objectif ou à la durée maximale ;
- résultat net de frais, exprimé en R (multiple du risque).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import timeframe_ms
from .exits import ExitState, r_multiple, step
from .features import FEATURE_COLUMNS
from .strategies import STRATEGIES, levels, signals

STRATEGY_COLUMNS = [f"s_{name}" for name in STRATEGIES]
MODEL_COLUMNS = FEATURE_COLUMNS + ["cost_r"] + STRATEGY_COLUMNS


def cost_in_r(entry: float, stop: float, fee_rate: float, slip: float) -> float:
    """Coût d'un aller-retour (frais + glissement) exprimé en R."""
    risk = entry - stop
    return float("inf") if risk <= 0 else entry * (2 * fee_rate + 2 * slip) / risk


def simulate_candidate(o, h, l, c, i: int, stop: float, target: float, strategy: str, fee_rate: float, slip: float):
    """Simule un candidat décidé à la clôture de la bougie i.

    Renvoie None si le trade était impossible (ouverture sous le stop),
    (None, nan, None) si le résultat n'est pas encore connu,
    sinon (motif, résultat en R, indice de la bougie de sortie).
    """
    st = STRATEGIES[strategy]
    n = len(o)
    if i + 1 >= n:
        return (None, float("nan"), None)
    entry_fill = o[i + 1] * (1 + slip)
    if entry_fill <= stop:
        return None
    state = ExitState(entry_fill, stop, target, stop, st.horizon_bars, st.breakeven_r, fee_rate, slip)
    for j in range(i + 1, min(n, i + 2 + st.horizon_bars)):
        px, reason = step(state, o[j], h[j], l[j], c[j])
        if px is not None:
            return (reason, r_multiple(entry_fill, px, stop, fee_rate), j)
    return (None, float("nan"), None)


def candidate_row(feat_row, strategy: str, stop: float, fee_rate: float, slip: float) -> dict:
    """Ligne de caractéristiques d'un candidat (pour une prédiction en direct)."""
    row = {k: float(feat_row[k]) if feat_row[k] is not None else np.nan for k in FEATURE_COLUMNS}
    row["cost_r"] = cost_in_r(float(feat_row["close"]), stop, fee_rate, slip)
    for name in STRATEGIES:
        row[f"s_{name}"] = 1.0 if name == strategy else 0.0
    row["strategy"] = strategy
    return row


def label_candidates(
    feat: pd.DataFrame,
    symbol: str,
    timeframe: str,
    enabled: list[str] | None = None,
    fee_rate: float = 0.001,
    slip: float = 0.0005,
) -> pd.DataFrame:
    tf = timeframe_ms(timeframe)
    o = feat["open"].to_numpy()
    h = feat["high"].to_numpy()
    l = feat["low"].to_numpy()
    c = feat["close"].to_numpy()
    a = feat["atr"].to_numpy()
    ts = feat["ts"].to_numpy()
    n = len(feat)
    rows = []
    for name, idxs in signals(feat, enabled).items():
        st = STRATEGIES[name]
        for i in idxs:
            stop, target = levels(st, c[i], a[i])
            row = {
                "symbol": symbol,
                "strategy": name,
                "idx": int(i),
                "ts": int(ts[i]),
                "decision_ts": int(ts[i] + tf),
                "entry_ref": float(c[i]),
                "stop": float(stop),
                "target": float(target),
                "horizon_bars": st.horizon_bars,
                "cost_r": cost_in_r(c[i], stop, fee_rate, slip),
                "outcome": None,
                "outcome_r": np.nan,
                "exit_ts": np.nan,
            }
            if i + 1 < n:
                sim = simulate_candidate(o, h, l, c, i, stop, target, name, fee_rate, slip)
                if sim is None:
                    continue  # ouverture déjà sous le stop : trade impossible
                if sim[0] is not None:
                    row["outcome"], row["outcome_r"] = sim[0], sim[1]
                    row["exit_ts"] = int(ts[sim[2]] + tf)
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["symbol", "strategy", "idx", "ts", "decision_ts", "entry_ref", "stop", "target",
                                     "horizon_bars", "cost_r", "outcome", "outcome_r", "exit_ts", *MODEL_COLUMNS])
    cand = pd.DataFrame(rows)
    feats = feat.iloc[cand["idx"].to_numpy()][FEATURE_COLUMNS].reset_index(drop=True)
    cand = pd.concat([cand.reset_index(drop=True), feats], axis=1)
    for name in STRATEGIES:
        cand[f"s_{name}"] = (cand["strategy"] == name).astype(float)
    return cand.sort_values(["decision_ts", "symbol", "strategy"]).reset_index(drop=True)


def model_matrix(cand: pd.DataFrame) -> pd.DataFrame:
    X = cand.reindex(columns=MODEL_COLUMNS).astype(float)
    return X.replace([np.inf, -np.inf], np.nan)


def non_overlapping(cand: pd.DataFrame, mask: np.ndarray | None = None) -> np.ndarray:
    """Sélection réaliste : une seule position à la fois par crypto.

    `cand` doit être trié par `decision_ts`. Renvoie un masque booléen.
    """
    take = np.zeros(len(cand), dtype=bool)
    busy: dict[str, float] = {}
    sym = cand["symbol"].to_numpy()
    dec = cand["decision_ts"].to_numpy()
    ext = cand["exit_ts"].to_numpy()
    res = cand["outcome"].notna().to_numpy()
    for k in range(len(cand)):
        if mask is not None and not mask[k]:
            continue
        if not res[k]:
            continue
        if dec[k] < busy.get(sym[k], -1):
            continue
        take[k] = True
        busy[sym[k]] = ext[k]
    return take
