"""Comparer deux modèles sur exactement les mêmes situations, et mesurer si ré-apprendre sert à quelque chose.

Règle de base : une comparaison n'a de valeur que sur des situations qu'AUCUN des deux modèles n'a vues pendant
son apprentissage. Chaque modèle porte donc sa fiche d'identité (`meta["train_end"]` : fin de ce qu'il a vu).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .labeling import non_overlapping
from .metrics import comparison_verdict, paired_comparison
from .model import DEFAULT_RECIPE, MetaModel

WEEK_DAYS = 7  # blocs d'une semaine pour une DIFFÉRENCE entre deux modèles (les mouvements communs du marché s'annulent)


def decisions(model: MetaModel, rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ce que ferait un modèle sur ces situations (triées par date). Renvoie (probabilité, espérance en R, pris ?).

    « Pris » respecte la règle réaliste « une seule position à la fois par crypto »."""
    p, er = model.predict(rows)
    take = non_overlapping(rows, er >= model.threshold)
    return p, er, take


def realized(rows: pd.DataFrame, take: np.ndarray) -> np.ndarray:
    """Résultat obtenu sur chaque situation : son résultat en R si elle est prise, 0 sinon."""
    return np.where(take, rows["outcome_r"].to_numpy(dtype=float), 0.0)


def train_end(model: MetaModel) -> int | None:
    """Fin de la période vue par le modèle pendant son apprentissage (ms), si elle est connue."""
    value = (model.meta or {}).get("train_end")
    return int(value) if value is not None else None


def compare_models(rows: pd.DataFrame, champion: MetaModel, challenger: MetaModel, *, challenger_seen_until: int | None = None,
                   block_days: int = WEEK_DAYS, min_diff: int = 30, min_blocks: int = 6) -> dict:
    """Champion contre challenger sur les mêmes situations dénouées.

    Seules les situations postérieures à ce que CHACUN a vu comptent pour la décision (`clean`). La comparaison
    sur toutes les situations est donnée à titre indicatif, avec la part que le champion connaissait déjà."""
    rows = rows[rows["outcome"].notna()].sort_values("decision_ts").reset_index(drop=True)
    out: dict = {"n_rows": int(len(rows)), "champion": champion.version, "challenger": challenger.version,
                 "champion_seen_until": train_end(champion), "challenger_seen_until": challenger_seen_until}
    if len(rows) == 0:
        return {**out, "clean": None, "indicative": None, "verdict": "insuffisant", "explanation": "Aucune situation à comparer."}
    seen_a = train_end(champion)
    cutoff = max(x for x in (seen_a, challenger_seen_until, -1) if x is not None)
    known = seen_a is not None
    clean_rows = rows[rows["decision_ts"] > cutoff].reset_index(drop=True) if known else rows.iloc[0:0]
    out["champion_knew_share"] = float(np.mean(rows["decision_ts"].to_numpy() <= seen_a)) if known else None

    def run(sub: pd.DataFrame) -> dict | None:
        if len(sub) < 2:
            return None
        _, _, take_a = decisions(champion, sub)
        _, _, take_b = decisions(challenger, sub)
        return paired_comparison(realized(sub, take_a), realized(sub, take_b), sub["decision_ts"].to_numpy(), block_days=block_days)

    out["indicative"] = run(rows)
    out["clean"] = run(clean_rows)
    if out["clean"] is None:
        why = ("le champion ne porte pas la date de fin de son apprentissage" if not known
               else "le champion a déjà vu ces situations pendant son apprentissage")
        out["verdict"], out["explanation"] = "insuffisant", (
            f"Pas de comparaison équitable possible sur l'historique : {why}. Le nouveau modèle sera comparé au champion "
            "sur les situations à venir, que ni l'un ni l'autre ne connaît.")
    else:
        out["verdict"], out["explanation"] = comparison_verdict(out["clean"], min_diff, min_blocks)
    return out


def frozen_vs_retrained(cand: pd.DataFrame, timeframe: str, *, recipe: str = DEFAULT_RECIPE, half_life_days: float | None = None,
                        n_steps: int = 6, start_frac: float = 0.5, block_days: int = 30, progress=None) -> dict:
    """Ré-apprendre avec les données nouvelles fait-il mieux que garder un modèle figé ?

    Au milieu de l'historique, un modèle est entraîné puis **figé**. La suite est découpée en `n_steps` périodes.
    Au début de chaque période, un second modèle est **ré-entraîné** avec tout ce qui est connu à cette date.
    Les deux décident ensuite sur la période, qu'aucun n'a vue. On compare leurs résultats situation par situation."""
    from .trainer import train_walk_forward  # import tardif : évite une dépendance circulaire

    res = cand[cand["outcome"].notna()].sort_values("decision_ts").reset_index(drop=True)
    out: dict = {"n_steps": n_steps, "recipe": recipe, "steps": [], "comparison": None, "verdict": "insuffisant",
                 "explanation": "Pas assez de données pour cette mesure."}
    if len(res) < 600:
        return out
    t0, t1 = int(res["decision_ts"].min()), int(res["decision_ts"].max())
    edges = np.linspace(t0 + (t1 - t0) * start_frac, t1 + 1, n_steps + 1).astype("int64")

    def fit(until: int) -> MetaModel | None:
        past = res[res["exit_ts"] < until]
        if len(past) < 300:
            return None
        return train_walk_forward(past, timeframe, recipe=recipe, half_life_days=half_life_days, with_importance=False).final_model

    frozen = fit(int(edges[0]))
    if frozen is None:
        return out
    parts = []
    for k in range(n_steps):
        if progress:
            progress(k / n_steps, f"Modèle figé contre modèle ré-entraîné : période {k + 1}/{n_steps}")
        window = res[(res["decision_ts"] >= edges[k]) & (res["decision_ts"] < edges[k + 1])].reset_index(drop=True)
        fresh = frozen if k == 0 else fit(int(edges[k]))
        if fresh is None or len(window) < 5:
            continue
        p_a, _, take_a = decisions(frozen, window)
        p_b, _, take_b = decisions(fresh, window)
        part = pd.DataFrame({"ts": window["decision_ts"].to_numpy(), "y": (window["outcome_r"].to_numpy() > 0).astype(int),
                             "r_frozen": realized(window, take_a), "r_fresh": realized(window, take_b), "p_frozen": p_a, "p_fresh": p_b})
        parts.append(part)
        out["steps"].append({"start": int(edges[k]), "end": int(edges[k + 1]), "n": int(len(window)),
                             "frozen": float(part["r_frozen"].sum()), "retrained": float(part["r_fresh"].sum()),
                             "taken_frozen": int(take_a.sum()), "taken_retrained": int(take_b.sum())})
    if not parts:
        return out
    allp = pd.concat(parts, ignore_index=True)
    later = allp[allp["ts"] >= edges[1]]  # sur la première période, les deux modèles sont identiques
    cmp = paired_comparison(later["r_frozen"].to_numpy(), later["r_fresh"].to_numpy(), later["ts"].to_numpy(), block_days=block_days)
    out["comparison"] = cmp

    def auc(col: str) -> float | None:
        return float(roc_auc_score(later["y"], later[col])) if len(later) >= 20 and later["y"].nunique() == 2 else None

    out["auc_frozen"], out["auc_retrained"] = auc("p_frozen"), auc("p_fresh")
    out["verdict"], out["explanation"] = comparison_verdict(cmp)
    return out
