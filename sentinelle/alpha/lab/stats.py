"""Mesures nettes et incertitude : résultat, baisse maximale, intervalles par blocs, essais multiples.

- intervalles : bootstrap circulaire par blocs de 7 jours (celui du projet, ``ai.evaluation``) ;
- essais multiples : quand on essaie K candidats, le meilleur paraît bon par hasard. Les
  probabilités critiques sont corrigées par la méthode du maximum (Romano et Wolf, descendante) :
  on rééchantillonne les MÊMES jours pour tous les candidats (leurs liens sont conservés) et on
  compare chaque statistique à la distribution du maximum sous l'hypothèse « aucun avantage » ;
- référence de même exposition : un candidat investi 40 % du temps est comparé à 40 % de la
  simple détention. Cela sépare le talent de sélection de la simple prise de risque de marché.
"""
from __future__ import annotations

import numpy as np

from ..shadow.analysis import interval

DAYS_PER_YEAR = 365.0
PLAN = {"block_days": 7, "bootstrap_reps": 2000, "bootstrap_seed": 701, "confidence": 0.95}


def max_drawdown(equity: np.ndarray, initial: float) -> float:
    eq = np.concatenate([[initial], np.asarray(equity, float)])
    return float(np.max(1 - eq / np.maximum.accumulate(eq)))


def metrics(returns: np.ndarray, equity: np.ndarray, initial: float) -> dict:
    r = np.asarray(returns, float)
    n = len(r)
    years = n / DAYS_PER_YEAR
    total = float(equity[-1] / initial - 1) if n else 0.0
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    dd = max_drawdown(equity, initial) if n else 0.0
    cagr = float((1 + total) ** (1 / years) - 1) if n and total > -1 else -1.0
    return {"days": n, "net_pnl": float(equity[-1] - initial) if n else 0.0, "total_return": total, "cagr": cagr,
            "annual_volatility": sd * np.sqrt(DAYS_PER_YEAR), "sharpe": (float(r.mean()) / sd * np.sqrt(DAYS_PER_YEAR)) if sd > 0 else 0.0,
            "max_drawdown": dd, "calmar": (cagr / dd) if dd > 0 else None, "worst_day": float(r.min()) if n else 0.0,
            "best_day": float(r.max()) if n else 0.0}


def mean_interval(x: np.ndarray, plan: dict = PLAN) -> dict:
    return interval(np.asarray(x, float), plan)


def t_stat(x: np.ndarray) -> float:
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else 0.0


def familywise(matrix: np.ndarray, *, block: int = 7, reps: int = 5000, seed: int = 1701) -> dict:
    """Probabilités critiques unilatérales (moyenne > 0), brutes et corrigées des essais multiples.

    ``matrix`` : jours × candidats. Méthode du maximum, descendante : la colonne la plus forte est
    comparée au maximum de toutes les colonnes rééchantillonnées, la suivante au maximum des
    colonnes restantes, etc. Une colonne constante (ne rien faire) a une probabilité de 1.
    """
    x = np.asarray(matrix, float)
    days, k = x.shape
    mean, sd = x.mean(axis=0), x.std(axis=0, ddof=1)
    live = sd > 0
    t = np.where(live, mean / np.where(live, sd, 1.0) * np.sqrt(days), 0.0)
    rng = np.random.default_rng(seed)
    boot = np.zeros((reps, k))
    n_blocks = (days + block - 1) // block
    for lo in range(0, reps, 100):
        m = min(100, reps - lo)
        starts = rng.integers(days, size=(m, n_blocks))
        idx = ((starts[:, :, None] + np.arange(block)) % days).reshape(m, -1)[:, :days]
        sample = x[idx]  # (m, jours, k)
        bm, bs = sample.mean(axis=1), sample.std(axis=1, ddof=1)
        ok = bs > 0
        boot[lo:lo + m] = np.where(ok, (bm - mean) / np.where(ok, bs, 1.0) * np.sqrt(days), 0.0)
    raw = np.where(live, (boot >= t).mean(axis=0), 1.0)
    order = np.argsort(-t, kind="mergesort")
    adjusted = np.ones(k)
    running = 0.0
    for pos, j in enumerate(order):
        rest = order[pos:]
        p = float((boot[:, rest].max(axis=1) >= t[j]).mean()) if live[j] else 1.0
        running = max(running, p)  # monotonie : une colonne plus faible n'a jamais une probabilité plus petite
        adjusted[j] = running
    return {"t": t.tolist(), "p_raw": raw.tolist(), "p_adjusted": adjusted.tolist(), "reps": reps, "block_days": block, "seed": seed,
            "tests": int(k)}


def exposure_matched(candidate_exposure: np.ndarray, benchmark_returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Rendements d'une détention de la référence à la même exposition MOYENNE que le candidat (sans frais :
    avantage laissé à la référence). Renvoie aussi cette exposition."""
    e = float(np.mean(candidate_exposure)) if len(candidate_exposure) else 0.0
    return e * np.asarray(benchmark_returns, float), e


def by_period(days: np.ndarray, returns: np.ndarray, bounds: list[tuple[str, int, int]]) -> dict:
    """Résultat composé sur des périodes disjointes (bornes en jours, incluses)."""
    out = {}
    for name, lo, hi in bounds:
        m = (days >= lo) & (days <= hi)
        r = returns[m]
        out[name] = {"days": int(m.sum()), "return": float(np.prod(1 + r) - 1) if len(r) else None,
                     "mean_daily": float(r.mean()) if len(r) else None}
    return out


def by_year(days: np.ndarray, returns: np.ndarray) -> dict:
    years = (days.astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970)
    return {str(y): float(np.prod(1 + returns[years == y]) - 1) for y in sorted(set(years.tolist()))}


def concentration(by_asset: dict) -> dict:
    """Part du principal actif gagnant dans le total des gains (1 = tout vient d'un seul actif)."""
    gains = {a.split("#")[0]: 0.0 for a in by_asset}
    for a, v in by_asset.items():
        gains[a.split("#")[0]] += v
    pos = sorted(((v, a) for a, v in gains.items() if v > 0), reverse=True)
    total = sum(v for v, _ in pos)
    return {"assets_traded": len(gains), "winners": len(pos), "top_asset": pos[0][1] if pos else None,
            "top_share_of_gains": (pos[0][0] / total) if total > 0 else None,
            "worst_asset": min(gains, key=gains.get) if gains else None, "worst_loss": min(gains.values()) if gains else None}


def alpha_beta(returns: np.ndarray, benchmark: np.ndarray) -> dict:
    """Régression simple du rendement du candidat sur celui de la référence : part expliquée par le marché."""
    x, y = np.asarray(benchmark, float), np.asarray(returns, float)
    vx = x.var()
    if len(x) < 30 or vx <= 0:
        return {"beta": None, "alpha_annual": None, "r2": None}
    beta = float(np.cov(x, y, bias=True)[0, 1] / vx)
    alpha = float(y.mean() - beta * x.mean())
    resid = y - alpha - beta * x
    return {"beta": beta, "alpha_annual": alpha * DAYS_PER_YEAR, "r2": float(1 - resid.var() / y.var()) if y.var() > 0 else None}


def risk_scaled(m: dict, limit: float) -> dict:
    """Taille qui aurait ramené la baisse maximale passée à la limite de risque du projet (ordre de grandeur :
    rendement et baisse supposés proportionnels à la taille ; ne tient pas compte du minimum d'ordre)."""
    dd = m["max_drawdown"]
    scale = 1.0 if dd <= limit else limit / dd
    return {"limit": limit, "scale": scale, "cagr_at_scale": m["cagr"] * scale, "within_limit_at_full_size": dd <= limit}
