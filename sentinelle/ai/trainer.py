"""Entraînement de l'IA avec une validation honnête.

Principe (« walk-forward ») : on ne teste jamais l'IA sur des données qu'elle a
vues pendant son apprentissage.

1. Les candidats sont triés dans le temps. Les 20 % les plus récents sont mis
   de côté (« holdout ») et ne servent qu'à l'évaluation finale.
2. Sur le reste, on entraîne sur le passé et on teste sur la période suivante,
   plusieurs fois de suite. Les candidats dont le résultat n'était pas encore
   connu au début de la période de test sont retirés de l'apprentissage
   (« purge ») : sinon l'IA tricherait sans le savoir.
3. Ces prédictions hors échantillon servent à calibrer les probabilités et à
   choisir le seuil d'espérance minimal.
4. Évaluation finale sur le holdout, comparée à « prendre tous les signaux ».
5. La version déployable conserve les paramètres effectivement évalués.
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .features import compute_features
from .labeling import MODEL_COLUMNS, label_candidates, model_matrix, non_overlapping
from .metrics import lower_bound, proven_positive, trade_metrics
from .model import DEFAULT_RECIPE, MetaModel, payoff_table
from .strategies import STRATEGIES

log = logging.getLogger("sentinelle.trainer")

THRESHOLD_GRID = [-0.05, 0.0, 0.02, 0.05, 0.08, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4]
MIN_RESOLVED = 300
TRIAL_MARGIN = 0.05  # « à l'essai » : au moins +0,05 R par trade, et au moins +0,05 R de mieux que « tout prendre »


@dataclass
class TrainResult:
    ok: bool
    verdict: str
    reasons: list[str]
    final_model: MetaModel | None = None
    eval_model: MetaModel | None = None
    report: dict = field(default_factory=dict)
    holdout_start: int | None = None
    trial: bool = False  # prometteur mais pas prouvé : utilisable en argent fictif uniquement


def build_dataset(
    histories: dict[str, pd.DataFrame],
    timeframe: str,
    enabled: list[str] | None = None,
    fee_rate: float = 0.001,
    slip: float = 0.0005,
    btc: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Calcule features et candidats étiquetés pour chaque crypto."""
    btc_sym = next((s for s in histories if s.split("/")[0] == "BTC"), None)
    btc_df = btc if btc is not None else (histories.get(btc_sym) if btc_sym else None)
    parts, feats = [], {}
    for sym, df in histories.items():
        if len(df) < 300:
            continue
        ctx = None if (btc_sym == sym and btc is None) else btc_df
        feat = compute_features(df.reset_index(drop=True), timeframe, btc=ctx)
        feats[sym] = feat
        cand = label_candidates(feat, sym, timeframe, enabled, fee_rate, slip)
        cand = cand[cand["trend_200d"].notna()]  # historique suffisant uniquement
        parts.append(cand)
    if not parts:
        return pd.DataFrame(columns=["symbol", "strategy", "decision_ts", "outcome", "outcome_r", "exit_ts", *MODEL_COLUMNS]), feats
    cand = pd.concat(parts, ignore_index=True).sort_values(["decision_ts", "symbol", "strategy"]).reset_index(drop=True)
    return cand, feats


def _r(x: float) -> str:
    """Résultat en R au format français (+0,189)."""
    return f"{x:+.3f}".replace(".", ",").replace("-", "−")


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    if len(y) < 20 or len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, p))


def _calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 5) -> list[dict]:
    if len(p) < bins * 10:
        return []
    qs = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for k in range(bins):
        lo, hi = qs[k], qs[k + 1]
        m = (p >= lo) & (p <= hi if k == bins - 1 else p < hi)
        if m.sum() == 0:
            continue
        out.append({"p_mean": float(p[m].mean()), "win_rate": float(y[m].mean()), "n": int(m.sum())})
    return out


def _curve(ts: np.ndarray, r: np.ndarray, max_points: int = 300) -> list[list[float]]:
    if len(r) == 0:
        return []
    cum = np.cumsum(r)
    idx = np.unique(np.linspace(0, len(r) - 1, min(max_points, len(r))).astype(int))
    return [[int(ts[i]), float(cum[i])] for i in idx]


def permutation_importance(model: MetaModel, data: pd.DataFrame, y: np.ndarray, n_repeats: int = 3, seed: int = 0) -> list[dict]:
    """Ce que l'IA regarde le plus : baisse du score quand on mélange une caractéristique (données jamais vues)."""
    X = model_matrix(data, model.feature_names)
    base = roc_auc_score(y, model.raw_proba(X))
    rng = np.random.default_rng(seed)
    out = []
    for col in model.feature_names:
        drops = []
        for _ in range(n_repeats):
            Xp = X.copy()
            Xp[col] = rng.permutation(Xp[col].to_numpy())
            drops.append(base - roc_auc_score(y, model.raw_proba(Xp)))
        out.append({"feature": col, "value": float(np.mean(drops))})
    return sorted(out, key=lambda d: -d["value"])


def select_threshold(oos: pd.DataFrame, er: np.ndarray, forced: float | None = None) -> tuple[float, list[dict]]:
    base_take = non_overlapping(oos)
    n_base = int(base_take.sum())
    min_n = max(20, int(0.15 * n_base))
    table = []
    best_thr, best_lb = THRESHOLD_GRID[1], -np.inf
    grid = [forced] if forced is not None else THRESHOLD_GRID
    for thr in grid:
        take = non_overlapping(oos, er >= thr)
        m = trade_metrics(oos.loc[take, "outcome_r"].to_numpy())
        lb = lower_bound(m)
        table.append({"threshold": thr, **{k: m[k] for k in ("n", "win_rate", "mean_r", "sum_r", "profit_factor")}, "lower_bound": lb if np.isfinite(lb) else None})
        if m["n"] >= min_n and lb > best_lb:
            best_thr, best_lb = thr, lb
    if forced is not None:
        best_thr = forced
    return best_thr, table


def trial_eligible(approved, metrics, baseline):
    return bool(not approved and metrics["n"] >= 30 and (metrics["mean_r"] or 0) >= TRIAL_MARGIN and
                baseline["mean_r"] is not None and metrics["mean_r"] - baseline["mean_r"] >= TRIAL_MARGIN)


def train_walk_forward(
    cand: pd.DataFrame,
    timeframe: str,
    n_folds: int = 4,
    holdout_frac: float = 0.2,
    min_edge: float | None = None,
    progress: Callable[[float, str], None] | None = None,
    rules_fallback: bool = True,
    recipe: str = DEFAULT_RECIPE,
    half_life_days: float | None = None,
    with_importance: bool = True,
) -> TrainResult:
    t_start = time.time()

    def new_model() -> MetaModel:
        return MetaModel(timeframe, recipe=recipe, half_life_days=half_life_days)

    def prog(x: float, msg: str) -> None:
        if progress:
            progress(x, msg)

    if not 0 < holdout_frac < 1 or n_folds < 1:
        raise ValueError("Découpage chronologique invalide")
    if cand.empty:
        return TrainResult(False, "données insuffisantes", ["Aucun candidat."])
    decisions = pd.to_numeric(cand["decision_ts"], errors="coerce")
    exits = pd.to_numeric(cand["exit_ts"], errors="coerce")
    times = decisions[np.isfinite(decisions)]
    if times.empty:
        return TrainResult(False, "données insuffisantes", ["Aucune date de décision valide."])
    # Boundaries depend on the observation clock, including unresolved decisions.
    t0, t1 = int(times.min()), int(times.max())
    resolved = (cand["outcome"].notna() & np.isfinite(cand["outcome_r"]) & np.isfinite(decisions)
                & np.isfinite(exits) & (exits >= decisions) & (exits < t1))
    res = cand.loc[resolved].sort_values("decision_ts", kind="stable").reset_index(drop=True)
    report: dict = {
        "timeframe": timeframe,
        "n_candidates": int(len(cand)),
        "n_resolved": int(len(res)),
        "symbols": sorted(res["symbol"].unique().tolist()) if len(res) else [],
        "by_strategy": {s: int((res["strategy"] == s).sum()) for s in STRATEGIES},
    }
    if len(res) < MIN_RESOLVED:
        return TrainResult(False, "données insuffisantes",
                           [f"Seulement {len(res)} situations étiquetées (minimum {MIN_RESOLVED}). Ajoutez de l'historique ou des cryptos."],
                           report=report)
    y_all = (res["outcome_r"].to_numpy() > 0).astype(int)
    hold_start = int(t0 + (t1 - t0) * (1 - holdout_frac))
    calibration_start = int(t0 + (hold_start - t0) * 0.60)
    selection_start = int(t0 + (hold_start - t0) * 0.80)

    def window(start, end):
        return res[(res.decision_ts >= start) & (res.decision_ts < end) & (res.exit_ts < end)]

    dev = window(t0, calibration_start)
    calibration = window(calibration_start, selection_start)
    selection = window(selection_start, hold_start)
    hold = window(hold_start, t1)
    report["period"] = {"start": t0, "end": t1, "calibration_start": calibration_start,
                        "selection_start": selection_start, "holdout_start": hold_start}
    report["split_counts"] = dict(fit=len(dev), calibration=len(calibration), selection=len(selection),
                                  holdout=len(hold), censored=len(cand)-sum(map(len, (dev, calibration, selection, hold))))
    if len(dev) < 150 or dev.outcome_r.gt(0).nunique() < 2 or min(map(len, (calibration, selection, hold))) < 20:
        return TrainResult(False, "données insuffisantes", ["Fenêtres chronologiques purgées insuffisantes."], report=report)

    edges = np.linspace(t0, calibration_start, n_folds + 2)
    folds = []
    for k in range(1, n_folds + 1):
        prog(0.1 + 0.5 * k / n_folds, f"Validation sur la période {k}/{n_folds}")
        test_start, test_end = edges[k], edges[k + 1]
        train = dev[dev["exit_ts"] < test_start]  # purge : résultats connus avant le test
        test = window(test_start, test_end)
        y_tr = (train["outcome_r"].to_numpy() > 0).astype(int)
        if len(train) < 150 or len(test) < 20 or len(np.unique(y_tr)) < 2:
            continue
        m = new_model().fit(train)
        part = test.copy()
        part["p_raw"] = m.raw_proba(model_matrix(test, m.feature_names))
        yt = (test["outcome_r"].to_numpy() > 0).astype(int)
        folds.append({
            "fold": k, "train": int(len(train)), "test": int(len(test)),
            "start": int(test_start), "end": int(test_end),
            "auc": _auc(yt, part["p_raw"].to_numpy()),
            "base_mean_r": float(test["outcome_r"].mean()),
        })
    report["folds"] = folds
    # Freeze the predictor before generating calibration, selection or holdout scores.
    prog(0.65, "Calibration sur une période réservée")
    eval_model = new_model().fit(dev)
    raw = eval_model.raw_proba(model_matrix(calibration, eval_model.feature_names))
    eval_model.fit_calibration(raw, calibration.outcome_r.gt(0).astype(int).to_numpy())
    eval_model.payoffs = payoff_table(dev)
    p_oos, er_oos = eval_model.predict(selection)
    y_oos = selection.outcome_r.gt(0).astype(int).to_numpy()
    thr, thr_table = select_threshold(selection, er_oos, min_edge)
    selected = trade_metrics(selection.loc[non_overlapping(selection, er_oos >= thr), "outcome_r"].to_numpy())
    rules = trade_metrics(selection.loc[non_overlapping(selection), "outcome_r"].to_numpy())
    mode = "ia"
    if rules_fallback and not proven_positive(selected) and proven_positive(rules):
        mode, thr = "règles seules", -1e9
    report["threshold"] = thr
    report["threshold_table"] = thr_table
    report["oos_auc"] = _auc(y_oos, p_oos)
    report["calibration_oos"] = _calibration_table(p_oos, y_oos)

    # -- évaluation finale sur des données jamais vues ------------------------------
    prog(0.75, "Évaluation sur la période de contrôle")
    eval_model.threshold = thr
    eval_model.trained_until = int(selection.exit_ts.max())
    p_h, er_h = eval_model.predict(hold)
    y_h = (hold["outcome_r"].to_numpy() > 0).astype(int)
    take_h = non_overlapping(hold, er_h >= thr)
    base_h = non_overlapping(hold)
    m_model = trade_metrics(hold.loc[take_h, "outcome_r"].to_numpy())
    m_base = trade_metrics(hold.loc[base_h, "outcome_r"].to_numpy())
    report["holdout"] = {
        "n_candidates": int(len(hold)),
        "auc": _auc(y_h, p_h),
        "brier": float(np.mean((p_h - y_h)**2)),
        "model": m_model,
        "baseline": m_base,
        "calibration": _calibration_table(p_h, y_h),
        "curve_model": _curve(hold.loc[take_h, "decision_ts"].to_numpy(), hold.loc[take_h, "outcome_r"].to_numpy()),
        "curve_baseline": _curve(hold.loc[base_h, "decision_ts"].to_numpy(), hold.loc[base_h, "outcome_r"].to_numpy()),
        "by_strategy": {},
    }
    for s in STRATEGIES:
        ms = take_h & (hold["strategy"].to_numpy() == s)
        bs = base_h & (hold["strategy"].to_numpy() == s)
        report["holdout"]["by_strategy"][s] = {
            "model": trade_metrics(hold.loc[ms, "outcome_r"].to_numpy()),
            "baseline": trade_metrics(hold.loc[bs, "outcome_r"].to_numpy()),
        }
    # Ce que l'IA regarde le plus (importance par permutation, sur le holdout)
    report["importance"] = []
    if with_importance and len(hold) >= 100 and len(np.unique(y_h)) == 2:
        try:
            report["importance"] = permutation_importance(eval_model, hold, y_h)[:10]
        except Exception:  # pragma: no cover - information facultative
            log.exception("Importance des variables indisponible")

    # -- verdict -------------------------------------------------------------------
    reasons = []
    ok = True
    if m_model["n"] < 30:
        ok = False
        reasons.append(f"Trop peu de trades sur la période de contrôle ({m_model['n']}, minimum 30).")
    if m_model["mean_r"] is None or m_model["mean_r"] <= 0:
        ok = False
        reasons.append("Espérance négative ou nulle après frais sur des données jamais vues.")
    elif not proven_positive(m_model):
        ok = False
        reasons.append("Résultat positif mais trop incertain statistiquement : il pourrait être dû au hasard (confiance inférieure à 95 %).")
    if m_base["mean_r"] is not None and m_model["mean_r"] is not None and m_model["n"] >= 30:
        if m_model["mean_r"] > m_base["mean_r"]:
            reasons.append(f"L'IA fait mieux que « prendre tous les signaux » ({_r(m_model['mean_r'])} R contre {_r(m_base['mean_r'])} R par trade).")
        else:
            reasons.append(f"L'IA ne fait pas mieux que « prendre tous les signaux » ({_r(m_model['mean_r'])} R contre {_r(m_base['mean_r'])} R).")
    if ok:
        reasons.insert(0, f"Espérance positive sur des données jamais vues : {_r(m_model['mean_r'])} R par trade sur {m_model['n']} trades.")
    if mode == "règles seules":
        reasons.insert(0, "Les règles seules ont été sélectionnées avant la période de contrôle.")

    # Prometteur sans être prouvé : nettement positif sur les données jamais vues et nettement meilleur que « tout prendre ».
    # Un tel modèle est mis « à l'essai » : l'IA d'entraînement s'en sert en argent fictif (taille réduite),
    # ce qui permet de le juger sur des données vraiment nouvelles. Jamais en réel.
    trial = trial_eligible(ok, m_model, m_base)
    if trial:
        reasons.append("Modèle mis à l'essai en argent fictif : prometteur, mais pas encore prouvé. Il ne peut pas trader en réel.")

    # -- modèle final -------------------------------------------------------------------
    prog(0.9, "Conservation du modèle évalué")
    final = copy.deepcopy(eval_model)
    final.version = time.strftime("m%Y%m%d-%H%M%S-") + timeframe + "-" + uuid.uuid4().hex[:8]
    eval_model.version = final.version + "-eval"
    summary = {
        "verdict": "validé" if ok else "non validé",
        "trial": trial,
        "mode": mode,
        "holdout_mean_r": (m_base if mode == "règles seules" else m_model)["mean_r"],
        "holdout_n": (m_base if mode == "règles seules" else m_model)["n"],
        "holdout_win_rate": (m_base if mode == "règles seules" else m_model)["win_rate"],
        "holdout_ci": [(m_base if mode == "règles seules" else m_model)["ci_low"], (m_base if mode == "règles seules" else m_model)["ci_high"]],
        "baseline_mean_r": m_base["mean_r"],
        "auc": report["holdout"]["auc"],
        "threshold": thr,
    }
    final.metrics = summary
    eval_model.metrics = summary
    report["summary"] = summary
    report["learner"] = {"recipe": recipe, "chosen": final.chosen, "selection": final.selection, "half_life_days": half_life_days}
    report["duration_s"] = round(time.time() - t_start, 1)
    report["base_rate"] = float(y_all.mean())
    prog(1.0, "Terminé")
    return TrainResult(ok, "validé" if ok else "non validé", reasons, final, eval_model, report, hold_start, trial)
