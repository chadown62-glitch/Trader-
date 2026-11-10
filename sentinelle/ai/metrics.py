"""Statistiques de performance, avec intervalles de confiance honnêtes."""

from __future__ import annotations

import math

import numpy as np


def trade_metrics(r, seed: int = 0, n_boot: int = 1000) -> dict:
    """Statistiques d'une série de résultats exprimés en R."""
    r = np.asarray([x for x in r if x is not None and np.isfinite(x)], dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0, "win_rate": None, "mean_r": None, "sum_r": 0.0, "profit_factor": None,
                "se": None, "ci_low": None, "ci_high": None, "max_dd_r": 0.0, "avg_win": None, "avg_loss": None}
    wins = r[r > 0]
    losses = r[r <= 0]
    mean = float(r.mean())
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else None
    ci_low = ci_high = None
    if n >= 5:
        rng = np.random.default_rng(seed)
        boots = rng.choice(r, size=(n_boot, n), replace=True).mean(axis=1)
        ci_low, ci_high = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    max_dd = float((peak - cum).max()) if n else 0.0
    gross_loss = float(-losses.sum())
    return {
        "n": n,
        "win_rate": float(len(wins) / n),
        "mean_r": mean,
        "sum_r": float(r.sum()),
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else None,
        "se": se,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "max_dd_r": max_dd,
        "avg_win": float(wins.mean()) if len(wins) else None,
        "avg_loss": float(losses.mean()) if len(losses) else None,
    }


def equity_metrics(equity: list[float]) -> dict:
    eq = np.asarray(equity, dtype=float)
    if len(eq) < 2:
        return {"return_pct": 0.0, "max_dd_pct": 0.0}
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    return {"return_pct": float((eq[-1] / eq[0] - 1) * 100), "max_dd_pct": float(dd.max() * 100)}


def proven_positive(m: dict) -> bool:
    """L'espérance est-elle positive avec 95 % de confiance ? (moyenne − 1,645 erreur type > 0)

    Sur du bruit pur, un critère plus souple validait à tort environ 4 entraînements sur 10
    (mesuré sur le banc d'essai) ; celui-ci limite ces fausses validations à environ 1 sur 20.
    """
    if not m or not m.get("n") or m.get("se") is None:
        return False
    return m["mean_r"] - 1.645 * m["se"] > 0


def lower_bound(m: dict) -> float:
    """Estimation prudente de l'espérance : moyenne moins une erreur type."""
    if not m or not m.get("n"):
        return -math.inf
    return m["mean_r"] - (m["se"] or 0.0)
