"""Statistiques de performance, avec intervalles de confiance honnêtes."""

from __future__ import annotations

import math

import numpy as np


BLOCK_DAYS = 30  # taille des blocs de temps : des trades du même mois ne sont pas supposés indépendants
MIN_BLOCKS = 6  # en dessous, aucune preuve statistique n'est possible


def block_ids(ts_ms, block_days: int = BLOCK_DAYS) -> np.ndarray:
    """Numéro du bloc de temps (30 jours par défaut) de chaque instant, en millisecondes."""
    return (np.asarray(ts_ms, dtype="float64") // (block_days * 86_400_000)).astype("int64")


def clustered_se(r, ts_ms, block_days: int = BLOCK_DAYS) -> tuple[float | None, int]:
    """Erreur type de la moyenne quand les résultats proches dans le temps se ressemblent.

    Plusieurs cryptos montent ou baissent ensemble, et des positions se chevauchent : les trades ne sont pas
    indépendants, et l'erreur type habituelle est trop optimiste. On regroupe donc les trades par blocs de
    temps et l'on mesure la dispersion ENTRE blocs. Le résultat n'est jamais plus optimiste que le calcul
    habituel. Renvoie (erreur type | None, nombre de blocs)."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n < 2:
        return None, int(n > 0)
    ids = block_ids(ts_ms, block_days)
    _, inverse = np.unique(ids, return_inverse=True)
    sums = np.bincount(inverse, weights=r - r.mean())
    g = len(sums)
    if g < 2:
        return None, g
    var = (g / (g - 1)) * float(np.sum(sums ** 2)) / n ** 2
    naive = float(r.std(ddof=1)) / math.sqrt(n)
    return max(math.sqrt(var), naive), g


def critical_value(alpha: float = 0.05, blocks: int | None = None) -> float:
    """Seuil du test unilatéral : loi de Student quand on raisonne sur peu de blocs, loi normale sinon."""
    from scipy import stats

    if blocks is not None and blocks >= 2:
        return float(stats.t.ppf(1 - alpha, blocks - 1))
    return float(stats.norm.ppf(1 - alpha))


def trade_metrics(r, seed: int = 0, n_boot: int = 1000, ts=None) -> dict:
    """Statistiques d'une série de résultats exprimés en R.

    Avec `ts` (instant de chaque trade, en ms), l'incertitude est calculée par blocs de temps : erreur type
    `se_cluster`, nombre de blocs, et intervalle à 95 % par ré-échantillonnage de blocs entiers."""
    keep = [k for k, x in enumerate(r) if x is not None and np.isfinite(x)]
    r = np.asarray([r[k] for k in keep], dtype=float) if len(keep) else np.asarray([], dtype=float)
    if ts is not None:
        ts = np.asarray(ts, dtype="float64")[keep] if len(keep) else np.asarray([], dtype="float64")
    n = len(r)
    if n == 0:
        out = {"n": 0, "win_rate": None, "mean_r": None, "sum_r": 0.0, "profit_factor": None,
               "se": None, "ci_low": None, "ci_high": None, "max_dd_r": 0.0, "avg_win": None, "avg_loss": None}
        if ts is not None:
            out.update(se_cluster=None, blocks=0)
        return out
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
    extra = {}
    if ts is not None:
        se_c, blocks = clustered_se(r, ts)
        extra = {"se_cluster": se_c, "blocks": blocks}
        if blocks >= 2 and n >= 5:  # intervalle par blocs : on tire des mois entiers, pas des trades isolés
            ids = block_ids(ts)
            uniq = np.unique(ids)
            groups = [r[ids == u] for u in uniq]
            sums = np.array([g.sum() for g in groups])
            counts = np.array([len(g) for g in groups])
            rng = np.random.default_rng(seed)
            pick = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
            boots = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
            ci_low, ci_high = float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))
    return {
        **extra,
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


def proven_positive(m: dict, alpha: float = 0.05) -> bool:
    """L'espérance est-elle positive avec une confiance de 1 − alpha (95 % par défaut) ?

    Sur du bruit pur, un critère plus souple validait à tort environ 4 entraînements sur 10
    (mesuré sur le banc d'essai : 8 sur 18) ; avec « moyenne − 1,645 erreur type > 0 », 2 sur 72.

    Quand les dates des trades sont connues (`se_cluster`), le test est plus prudent encore : l'erreur type
    tient compte de la dépendance entre trades proches, le seuil vient de la loi de Student sur le nombre de
    blocs de temps, et il faut au moins MIN_BLOCKS blocs. `alpha` est abaissé quand plusieurs configurations
    ont été essayées sur la même période de contrôle (voir `bonferroni_alpha`).
    """
    if not m or not m.get("n") or m.get("mean_r") is None:
        return False
    if "se_cluster" in m:
        if m["se_cluster"] is None or (m.get("blocks") or 0) < MIN_BLOCKS:
            return False
        return m["mean_r"] - critical_value(alpha, m["blocks"]) * m["se_cluster"] > 0
    if m.get("se") is None:
        return False
    return m["mean_r"] - critical_value(alpha) * m["se"] > 0


def bonferroni_alpha(n_configs: int, alpha: float = 0.05) -> float:
    """Niveau de risque par essai quand `n_configs` configurations différentes ont été jugées sur les mêmes données.

    Avec 20 essais à 5 %, on en « valide » un par hasard en moyenne. On exige donc 5 % ÷ nombre d'essais."""
    return alpha / max(1, int(n_configs))


def paired_comparison(r_a, r_b, ts_ms, seed: int = 0, n_boot: int = 2000, block_days: int = BLOCK_DAYS) -> dict:
    """Compare deux modèles sur EXACTEMENT les mêmes situations.

    `r_a[k]` et `r_b[k]` : résultat (en R) obtenu par chaque modèle sur la situation k, 0 s'il ne la prend pas.
    La différence moyenne par situation et son intervalle à 90 % (soit 95 % d'un seul côté) sont calculés en
    ré-échantillonnant des blocs de temps entiers : les différences proches dans le temps ne sont pas
    supposées indépendantes."""
    a = np.asarray(r_a, dtype=float)
    b = np.asarray(r_b, dtype=float)
    d = b - a
    n = len(d)
    out = {"n": int(n), "n_diff": int(np.sum(np.abs(d) > 1e-12)), "blocks": 0,
           "n_taken_a": int(np.sum(a != 0)), "n_taken_b": int(np.sum(b != 0)),
           "mean_a": float(a.mean()) if n else None, "mean_b": float(b.mean()) if n else None,
           "mean_taken_a": float(a[a != 0].mean()) if np.any(a != 0) else None,
           "mean_taken_b": float(b[b != 0].mean()) if np.any(b != 0) else None,
           "sum_a": float(a.sum()), "sum_b": float(b.sum()),
           "diff": float(d.mean()) if n else None, "ci_low": None, "ci_high": None, "p_better": None}
    if n < 2:
        return out
    ids = block_ids(ts_ms, block_days)
    uniq = np.unique(ids)
    out["blocks"] = int(len(uniq))
    if len(uniq) < 2:
        return out
    sums = np.array([d[ids == u].sum() for u in uniq])
    counts = np.array([np.sum(ids == u) for u in uniq])
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    boots = sums[pick].sum(axis=1) / counts[pick].sum(axis=1)
    out.update(ci_low=float(np.percentile(boots, 5)), ci_high=float(np.percentile(boots, 95)), p_better=float(np.mean(boots > 0)))
    return out


def comparison_verdict(cmp: dict, min_diff: int = 30, min_blocks: int = MIN_BLOCKS) -> tuple[str, str]:
    """Décision tirée d'une comparaison appariée (B contre A). Renvoie (verdict, explication).

    - « meilleur » : B fait mieux que A, et ce n'est très probablement pas dû au hasard (borne basse > 0) ;
    - « moins bon » : A fait mieux que B avec la même exigence (borne haute < 0) ;
    - « non départagés » : la différence peut être due au hasard ;
    - « insuffisant » : trop peu de situations où les deux modèles décident différemment, ou trop peu de blocs de temps."""
    if cmp["n_diff"] < min_diff or cmp["blocks"] < min_blocks or cmp["ci_low"] is None:
        return "insuffisant", (f"Seulement {cmp['n_diff']} situation(s) où les deux modèles décident différemment, sur {cmp['blocks']} "
                               f"bloc(s) de temps (minimum {min_diff} et {min_blocks}) : pas de conclusion possible.")
    span = f"{cmp['diff']:+.3f} R par situation, intervalle {cmp['ci_low']:+.3f} à {cmp['ci_high']:+.3f}".replace(".", ",")
    if cmp["ci_low"] > 0:
        return "meilleur", f"Le nouveau modèle fait mieux sur les mêmes situations ({span})."
    if cmp["ci_high"] < 0:
        return "moins bon", f"Le nouveau modèle fait moins bien sur les mêmes situations ({span})."
    return "non départagés", f"Différence trop incertaine pour conclure ({span})."


def lower_bound(m: dict) -> float:
    """Estimation prudente de l'espérance : moyenne moins une erreur type."""
    if not m or not m.get("n"):
        return -math.inf
    return m["mean_r"] - (m["se"] or 0.0)
