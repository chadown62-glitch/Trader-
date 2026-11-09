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
5. Le modèle déployé est ré-entraîné sur toutes les données.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .features import compute_features
from .labeling import MODEL_COLUMNS, label_candidates, model_matrix, non_overlapping
from .metrics import lower_bound, trade_metrics
from .model import MetaModel, payoff_table
from .strategies import STRATEGIES

log = logging.getLogger("sentinelle.trainer")

THRESHOLD_GRID = [-0.05, 0.0, 0.02, 0.05, 0.08, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4]
MIN_RESOLVED = 300


@dataclass
class TrainResult:
    ok: bool
    verdict: str
    reasons: list[str]
    final_model: MetaModel | None = None
    eval_model: MetaModel | None = None
    report: dict = field(default_factory=dict)
    holdout_start: int | None = None


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


def train_walk_forward(
    cand: pd.DataFrame,
    timeframe: str,
    n_folds: int = 4,
    holdout_frac: float = 0.2,
    min_edge: float | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> TrainResult:
    t_start = time.time()

    def prog(x: float, msg: str) -> None:
        if progress:
            progress(x, msg)

    res = cand[cand["outcome"].notna()].sort_values("decision_ts").reset_index(drop=True)
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
    t0, t1 = int(res["decision_ts"].min()), int(res["decision_ts"].max())
    hold_start = int(t0 + (t1 - t0) * (1 - holdout_frac))
    dev = res[res["decision_ts"] < hold_start]
    hold = res[res["decision_ts"] >= hold_start].reset_index(drop=True)
    report["period"] = {"start": t0, "end": t1, "holdout_start": hold_start}

    # -- validation croisée chronologique avec purge ---------------------------
    edges = np.linspace(dev["decision_ts"].min(), hold_start, n_folds + 2)
    oos_parts, folds = [], []
    for k in range(1, n_folds + 1):
        prog(0.1 + 0.5 * k / n_folds, f"Validation sur la période {k}/{n_folds}")
        test_start, test_end = edges[k], edges[k + 1]
        train = dev[dev["exit_ts"] < test_start]  # purge : résultats connus avant le test
        test = dev[(dev["decision_ts"] >= test_start) & (dev["decision_ts"] < test_end)]
        y_tr = (train["outcome_r"].to_numpy() > 0).astype(int)
        if len(train) < 150 or len(test) < 20 or len(np.unique(y_tr)) < 2:
            continue
        m = MetaModel(timeframe).fit(train)
        part = test.copy()
        part["p_raw"] = m.raw_proba(model_matrix(test, m.feature_names))
        oos_parts.append(part)
        yt = (test["outcome_r"].to_numpy() > 0).astype(int)
        folds.append({
            "fold": k, "train": int(len(train)), "test": int(len(test)),
            "start": int(test_start), "end": int(test_end),
            "auc": _auc(yt, part["p_raw"].to_numpy()),
            "base_mean_r": float(test["outcome_r"].mean()),
        })
    report["folds"] = folds
    if not oos_parts:
        return TrainResult(False, "données insuffisantes", ["Pas assez de données pour la validation chronologique."], report=report)
    oos = pd.concat(oos_parts).sort_values("decision_ts").reset_index(drop=True)
    y_oos = (oos["outcome_r"].to_numpy() > 0).astype(int)

    # -- calibration et seuil ----------------------------------------------------
    prog(0.65, "Calibration des probabilités")
    calib_model = MetaModel(timeframe)
    calib_model.fit_calibration(oos["p_raw"].to_numpy(), y_oos)
    calib_model.payoffs = payoff_table(dev)
    p_oos = calib_model.calibrate(oos["p_raw"].to_numpy())
    er_oos = calib_model.expected_r(p_oos, oos["strategy"].to_numpy())
    thr, thr_table = select_threshold(oos, er_oos, min_edge)
    report["threshold"] = thr
    report["threshold_table"] = thr_table
    report["oos_auc"] = _auc(y_oos, p_oos)
    report["calibration_oos"] = _calibration_table(p_oos, y_oos)

    # -- évaluation finale sur des données jamais vues ------------------------------
    prog(0.75, "Évaluation sur la période de contrôle")
    eval_train = dev[dev["exit_ts"] < hold_start]
    eval_model = MetaModel(timeframe).fit(eval_train)
    eval_model.calib = calib_model.calib
    eval_model.threshold = thr
    p_h, er_h = eval_model.predict(hold)
    y_h = (hold["outcome_r"].to_numpy() > 0).astype(int)
    take_h = non_overlapping(hold, er_h >= thr)
    base_h = non_overlapping(hold)
    m_model = trade_metrics(hold.loc[take_h, "outcome_r"].to_numpy())
    m_base = trade_metrics(hold.loc[base_h, "outcome_r"].to_numpy())
    report["holdout"] = {
        "n_candidates": int(len(hold)),
        "auc": _auc(y_h, p_h),
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
    if len(hold) >= 100 and len(np.unique(y_h)) == 2:
        try:
            from sklearn.inspection import permutation_importance

            pi = permutation_importance(eval_model.clf, model_matrix(hold, eval_model.feature_names), y_h, scoring="roc_auc", n_repeats=3, random_state=0)
            order = np.argsort(-pi.importances_mean)[:10]
            report["importance"] = [{"feature": eval_model.feature_names[i], "value": float(pi.importances_mean[i])} for i in order]
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
    elif lower_bound(m_model) <= -0.05:
        ok = False
        reasons.append("Résultat positif mais trop incertain statistiquement (marge d'erreur trop large).")
    if m_base["mean_r"] is not None and m_model["mean_r"] is not None and m_model["n"] >= 30:
        if m_model["mean_r"] > m_base["mean_r"]:
            reasons.append(f"L'IA fait mieux que « prendre tous les signaux » ({m_model['mean_r']:+.3f} R contre {m_base['mean_r']:+.3f} R par trade).")
        else:
            reasons.append(f"L'IA ne fait pas mieux que « prendre tous les signaux » ({m_model['mean_r']:+.3f} R contre {m_base['mean_r']:+.3f} R).")
    if ok:
        reasons.insert(0, f"Espérance positive sur des données jamais vues : {m_model['mean_r']:+.3f} R par trade sur {m_model['n']} trades.")

    # -- modèle final -------------------------------------------------------------------
    prog(0.9, "Entraînement du modèle final sur toutes les données")
    final = MetaModel(timeframe).fit(res)
    final.calib = calib_model.calib
    final.threshold = thr
    final.version = time.strftime("m%Y%m%d-%H%M%S-") + timeframe
    eval_model.version = final.version + "-eval"
    summary = {
        "verdict": "validé" if ok else "non validé",
        "holdout_mean_r": m_model["mean_r"],
        "holdout_n": m_model["n"],
        "holdout_win_rate": m_model["win_rate"],
        "holdout_ci": [m_model["ci_low"], m_model["ci_high"]],
        "baseline_mean_r": m_base["mean_r"],
        "auc": report["holdout"]["auc"],
        "threshold": thr,
    }
    final.metrics = summary
    eval_model.metrics = summary
    report["summary"] = summary
    report["duration_s"] = round(time.time() - t_start, 1)
    report["base_rate"] = float(y_all.mean())
    prog(1.0, "Terminé")
    return TrainResult(ok, "validé" if ok else "non validé", reasons, final, eval_model, report, hold_start)
