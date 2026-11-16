"""Incertitude des mesures de coût : bootstrap par grappes de dates, différences appariées, quantiles.

Les échantillons d'un même jour (plusieurs paires à 00:00 UTC) ne sont pas indépendants : on
rééchantillonne donc les DATES, avec toutes leurs mesures. Graines fixes : résultats reproductibles.
"""
from __future__ import annotations

import numpy as np


def cluster_bootstrap(values: np.ndarray, clusters: np.ndarray, *, stat=np.mean, reps: int = 2000, seed: int = 4401,
                      confidence: float = 0.95) -> dict:
    x = np.asarray(values, float)
    g = np.asarray(clusters)
    ok = np.isfinite(x)
    x, g = x[ok], g[ok]
    if len(x) < 5:
        return {"n": int(len(x)), "clusters": int(len(set(g.tolist()))), "estimate": float(stat(x)) if len(x) else None, "ci_low": None, "ci_high": None}
    keys, inv = np.unique(g, return_inverse=True)
    groups = [np.flatnonzero(inv == k) for k in range(len(keys))]
    rng = np.random.default_rng(seed)
    draws = np.empty(reps)
    for r in range(reps):
        pick = rng.integers(len(groups), size=len(groups))
        draws[r] = stat(x[np.concatenate([groups[k] for k in pick])])
    q = (1 - confidence) / 2
    return {"n": int(len(x)), "clusters": int(len(keys)), "estimate": float(stat(x)), "ci_low": float(np.quantile(draws, q)),
            "ci_high": float(np.quantile(draws, 1 - q))}


def describe(values) -> dict:
    x = np.asarray([v for v in values if v is not None and np.isfinite(v)], float)
    if not len(x):
        return {"n": 0}
    return {"n": int(len(x)), "mean": float(x.mean()), "median": float(np.median(x)), "p10": float(np.quantile(x, 0.10)),
            "p90": float(np.quantile(x, 0.90)), "p95": float(np.quantile(x, 0.95)), "p99": float(np.quantile(x, 0.99)),
            "min": float(x.min()), "max": float(x.max())}


def paired_difference(a: np.ndarray, b: np.ndarray, clusters: np.ndarray, **kw) -> dict:
    """Différence moyenne a − b sur les mêmes échantillons (seuls ceux mesurés des deux côtés comptent)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    out = cluster_bootstrap(a[ok] - b[ok], np.asarray(clusters)[ok], **kw)
    out["share_a_better"] = float(np.mean(a[ok] < b[ok])) if ok.any() else None
    return out
