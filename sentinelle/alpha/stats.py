"""Mesures prédictives et financières, intervalles prudents, verdict de réplication."""
from __future__ import annotations

import math

import numpy as np

from ..ai.evaluation import block_interval

DAY_MS = 86_400_000


def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    y, p = np.asarray(y, int), np.asarray(p, float)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    allv = np.concatenate([pos, neg])[order]
    # rangs moyens en cas d'égalité
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def brier(y: np.ndarray, p: np.ndarray) -> float | None:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2)) if len(y) else None


def logloss(y: np.ndarray, p: np.ndarray) -> float | None:
    if not len(y):
        return None
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float | None:
    if not len(y):
        return None
    y, p = np.asarray(y, float), np.asarray(p, float)
    k = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(y[k == b].mean() - p[k == b].mean()) * (k == b).sum() for b in range(bins) if (k == b).any()) / len(y))


def predictive(y: np.ndarray, p: np.ndarray, base_rate: float) -> dict:
    b0 = brier(y, np.full(len(y), base_rate))
    b1 = brier(y, p)
    return {"n": int(len(y)), "auc": auc(y, p), "brier": b1, "logloss": logloss(y, p), "ece": ece(y, p),
            "brier_constant": b0, "brier_skill": (1 - b1 / b0) if b0 and b1 is not None else None}


def paired_daily_interval(a: dict[int, float], b: dict[int, float], *, confidence: float = 0.95, seed: int = 701) -> dict:
    """Différence de rendements quotidiens appariés (a - b) sur les mêmes jours ; bootstrap par blocs de 7 jours."""
    days = sorted(set(a) & set(b))
    diff = np.array([a[d] - b[d] for d in days])
    ci = block_interval(diff, block=7, seed=seed, reps=2000, confidence=confidence) if len(diff) else None
    return {"days": len(days), "mean_daily_diff": float(diff.mean()) if len(diff) else None,
            "ci_low": None if ci is None else float(ci[0]), "ci_high": None if ci is None else float(ci[1])}


def daily_mean_interval(values_by_day: dict[int, float], *, confidence: float = 0.95, seed: int = 701) -> dict:
    days = sorted(values_by_day)
    x = np.array([values_by_day[d] for d in days])
    ci = block_interval(x, block=7, seed=seed, reps=2000, confidence=confidence) if len(x) else None
    return {"days": len(days), "mean": float(x.mean()) if len(x) else None,
            "ci_low": None if ci is None else float(ci[0]), "ci_high": None if ci is None else float(ci[1])}


def replication_verdict(lows: list[float | None], *, required: float = 2 / 3) -> dict:
    """« Amélioration répliquée » seulement si la borne basse est > 0 sur au moins 2/3 des réplications
    indépendantes (et au moins 2). Sinon : « non démontré ». Une borne absente compte comme un échec."""
    k = sum(1 for x in lows if x is not None and x > 0)
    n = len(lows)
    need = max(2, math.ceil(required * n))
    ok = n >= 2 and k >= need
    return {"replications": n, "positive_lower_bounds": k, "required": need,
            "verdict": "amélioration répliquée hors échantillon" if ok else "non démontré"}


def bonferroni(confidence: float, trials: int) -> float:
    return 1 - (1 - confidence) / max(1, trials)
