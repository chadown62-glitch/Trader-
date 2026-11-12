"""Entraînement de l'IA avec une validation honnête.

Principe (« walk-forward ») : on ne teste jamais l'IA sur des données qu'elle a
vues pendant son apprentissage.

1. Les candidats sont triés dans le temps. Les 20 % les plus récents sont mis
   de côté (« période de contrôle ») et ne servent qu'à l'évaluation finale.
   Un candidat décidé avant cette période mais dénoué pendant elle est écarté
   de tout le reste : son résultat dépend de prix de la période de contrôle.
2. Sur le reste, on entraîne sur le passé et on teste sur la période suivante,
   plusieurs fois de suite. Les candidats dont le résultat n'était pas encore
   connu au début de la période de test sont retirés de l'apprentissage
   (« purge ») : sinon l'IA tricherait sans le savoir.
3. Ces prédictions hors échantillon sont coupées en deux dans le temps : la
   première moitié sert à calibrer les probabilités, la seconde à choisir le
   seuil d'espérance minimal. Le seuil n'est donc pas choisi sur les exemples
   qui ont servi à la calibration.
4. Évaluation finale sur la période de contrôle, comparée à « prendre tous les
   signaux ». L'incertitude est calculée par blocs de temps (les trades proches
   ne sont pas supposés indépendants), et l'exigence monte quand plusieurs
   configurations ont été essayées sur la même période.
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
from .metrics import MIN_BLOCKS, lower_bound, proven_positive, trade_metrics
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


def feature_bins(data: pd.DataFrame, columns: list[str], bins: int = 10) -> dict[str, list[float]]:
    """Bornes des déciles de chaque caractéristique sur les données d'apprentissage.

    Elles servent plus tard à mesurer si le marché « ne ressemble plus » à ce que le modèle a appris."""
    out = {}
    for col in columns:
        if col not in data.columns:
            continue
        x = pd.to_numeric(data[col], errors="coerce").to_numpy(dtype=float)
        x = x[np.isfinite(x)]
        if len(x) < 50 or len(np.unique(x)) < 5:
            continue
        edges = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1))[1:-1])
        if len(edges) >= 2:
            out[col] = [float(e) for e in edges]
    return out


def outcome_mix(res: pd.DataFrame) -> dict[str, dict]:
    """Par stratégie : comment se sont terminés les exemples passés (sert à décrire les scénarios d'un trade)."""
    out = {}
    for name, sub in res.groupby("strategy"):
        r = sub["outcome_r"].to_numpy(dtype=float)
        outcome = sub["outcome"].to_numpy()
        wins, losses = r[r > 0], r[r <= 0]
        stop = np.isin(outcome, ["stop"])
        out[str(name)] = {
            "n": int(len(sub)),
            "win_rate": float(len(wins) / len(r)) if len(r) else None,
            "mean_win": float(wins.mean()) if len(wins) else None,
            "mean_loss": float(losses.mean()) if len(losses) else None,
            "best": float(np.percentile(wins, 90)) if len(wins) else None,
            # parmi les pertes : part de stops francs ; le reste = sorties proches de zéro (point mort, durée maximale)
            "stop_share_of_losses": float(np.mean(stop[r <= 0])) if len(losses) else None,
            "shares": {str(k): float(v) for k, v in pd.Series(outcome).value_counts(normalize=True).items()},
        }
    return out


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
    rules_fallback: bool = True,
    recipe: str = DEFAULT_RECIPE,
    half_life_days: float | None = None,
    with_importance: bool = True,
    alpha: float = 0.05,
) -> TrainResult:
    """`alpha` : risque d'erreur accepté pour déclarer l'espérance positive (5 % par défaut ; abaissé quand
    plusieurs configurations ont été essayées sur la même période de contrôle)."""
    t_start = time.time()

    def new_model() -> MetaModel:
        return MetaModel(timeframe, recipe=recipe, half_life_days=half_life_days)

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
    before = res[res["decision_ts"] < hold_start]
    # Un candidat décidé avant la période de contrôle mais dénoué pendant elle dépend de prix de cette période :
    # il est écarté de l'apprentissage, de la calibration et du choix du seuil (pas de fuite d'information).
    dev = before[before["exit_ts"] < hold_start]
    hold = res[res["decision_ts"] >= hold_start].reset_index(drop=True)
    report["period"] = {"start": t0, "end": t1, "holdout_start": hold_start}
    report["purged_at_boundary"] = int(len(before) - len(dev))
    report["alpha"] = alpha

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
        m = new_model().fit(train)
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
    # Calibration sur la première moitié (dans le temps) des prédictions hors échantillon, seuil choisi sur la
    # seconde : le seuil n'est pas choisi sur les exemples qui ont servi à calibrer.
    split_ts = int(oos["decision_ts"].iloc[len(oos) // 2])
    cal = oos[oos["exit_ts"] < split_ts]
    sel = oos[oos["decision_ts"] >= split_ts].reset_index(drop=True)
    y_cal = (cal["outcome_r"].to_numpy() > 0).astype(int)
    independent = len(cal) >= 100 and len(sel) >= 100 and len(np.unique(y_cal)) == 2
    if not independent:  # trop peu d'exemples pour séparer : même jeu pour les deux (signalé dans le rapport)
        cal, y_cal, sel = oos, y_oos, oos
    calib_model = new_model()
    calib_model.fit_calibration(cal["p_raw"].to_numpy(), y_cal)
    calib_model.payoffs = payoff_table(dev[dev["exit_ts"] < split_ts] if independent else dev)
    p_oos = calib_model.calibrate(oos["p_raw"].to_numpy())
    er_sel = calib_model.expected_r(calib_model.calibrate(sel["p_raw"].to_numpy()), sel["strategy"].to_numpy())
    thr, thr_table = select_threshold(sel, er_sel, min_edge)
    report["threshold"] = thr
    report["threshold_table"] = thr_table
    report["selection"] = {"independent": bool(independent), "split_ts": split_ts, "n_calibration": int(len(cal)), "n_threshold": int(len(sel))}
    report["oos_auc"] = _auc(y_oos, p_oos)
    report["calibration_oos"] = _calibration_table(p_oos, y_oos)

    # -- évaluation finale sur des données jamais vues ------------------------------
    prog(0.75, "Évaluation sur la période de contrôle")
    eval_train = dev[dev["exit_ts"] < hold_start]
    eval_model = new_model().fit(eval_train)
    eval_model.calib = calib_model.calib
    eval_model.threshold = thr
    p_h, er_h = eval_model.predict(hold)
    y_h = (hold["outcome_r"].to_numpy() > 0).astype(int)
    take_h = non_overlapping(hold, er_h >= thr)
    base_h = non_overlapping(hold)
    # incertitude par blocs de temps : les trades proches ne sont pas supposés indépendants
    m_model = trade_metrics(hold.loc[take_h, "outcome_r"].to_numpy(), ts=hold.loc[take_h, "decision_ts"].to_numpy())
    m_base = trade_metrics(hold.loc[base_h, "outcome_r"].to_numpy(), ts=hold.loc[base_h, "decision_ts"].to_numpy())
    brier = float(np.mean((p_h - y_h) ** 2)) if len(y_h) else None
    brier_ref = float(np.mean((y_h.mean() - y_h) ** 2)) if len(y_h) else None  # prévision « toujours le taux moyen »
    report["holdout"] = {
        "n_candidates": int(len(hold)),
        "auc": _auc(y_h, p_h),
        "brier": brier,
        "brier_reference": brier_ref,
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
    elif not proven_positive(m_model, alpha):
        ok = False
        if (m_model.get("blocks") or 0) < MIN_BLOCKS:
            reasons.append(f"Période de contrôle trop courte pour prouver quoi que ce soit : {m_model.get('blocks') or 0} mois de trades "
                           f"(minimum {MIN_BLOCKS}).")
        else:
            level = f"{100 * (1 - alpha):.4g}".replace(".", ",")
            reasons.append("Résultat positif mais trop incertain statistiquement : il pourrait être dû au hasard "
                           f"(confiance inférieure à {level} %, trades proches dans le temps non supposés indépendants).")
    if m_base["mean_r"] is not None and m_model["mean_r"] is not None and m_model["n"] >= 30:
        if m_model["mean_r"] > m_base["mean_r"]:
            reasons.append(f"L'IA fait mieux que « prendre tous les signaux » ({_r(m_model['mean_r'])} R contre {_r(m_base['mean_r'])} R par trade).")
        else:
            reasons.append(f"L'IA ne fait pas mieux que « prendre tous les signaux » ({_r(m_model['mean_r'])} R contre {_r(m_base['mean_r'])} R).")
    if ok:
        reasons.insert(0, f"Espérance positive sur des données jamais vues : {_r(m_model['mean_r'])} R par trade sur {m_model['n']} trades.")
    mode = "ia"
    if not ok and rules_fallback and m_base["n"] >= 30 and proven_positive(m_base, alpha):
        # Le filtre IA n'apporte pas de preuve, mais les règles seules en apportent : on les valide telles quelles.
        ok, mode, thr = True, "règles seules", -1e9
        reasons.insert(0, f"Validé en « règles seules » : prendre tous les signaux donne {_r(m_base['mean_r'])} R par trade "
                          f"sur {m_base['n']} trades jamais vus ; le filtre IA est désactivé car il n'a pas fait mieux.")
        eval_model.threshold = thr

    # Prometteur sans être prouvé : nettement positif sur les données jamais vues et nettement meilleur que « tout prendre ».
    # Un tel modèle est mis « à l'essai » : l'IA d'entraînement s'en sert en argent fictif (taille réduite),
    # ce qui permet de le juger sur des données vraiment nouvelles. Jamais en réel.
    trial = bool(not ok and m_model["n"] >= 30 and (m_model["mean_r"] or 0) >= TRIAL_MARGIN
                 and m_base["mean_r"] is not None and m_model["mean_r"] - m_base["mean_r"] >= TRIAL_MARGIN)
    if trial:
        reasons.append("Modèle mis à l'essai en argent fictif : prometteur, mais pas encore prouvé. Il ne peut pas trader en réel.")

    # -- modèle final -------------------------------------------------------------------
    prog(0.9, "Entraînement du modèle final sur toutes les données")
    final = new_model().fit(res)
    final.calib = calib_model.calib
    final.threshold = thr
    final.version = time.strftime("m%Y%m%d-%H%M%S-") + timeframe
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
    # Fiche d'identité : ce que le modèle a vu, et comment il a été fabriqué (sert aux comparaisons entre versions)
    known = {"recipe": recipe, "half_life_days": half_life_days, "timeframe": timeframe, "threshold": thr, "alpha": alpha,
             "strategies": sorted(res["strategy"].unique().tolist()), "symbols": report["symbols"], "holdout_start": hold_start}
    # calibration mesurée sur la période de contrôle : sert à annoncer la marge d'erreur des probabilités
    known["calibration"] = report["holdout"]["calibration"]
    final.meta = {**known, "train_start": t0, "train_end": int(res["exit_ts"].max()), "n_train": int(len(res))}
    eval_model.meta = {**known, "train_start": t0, "train_end": hold_start, "n_train": int(len(eval_train))}
    final.feature_bins = feature_bins(res, final.feature_names)
    final.outcome_mix = eval_model.outcome_mix = outcome_mix(res)
    report["summary"] = summary
    report["meta"] = final.meta
    report["learner"] = {"recipe": recipe, "chosen": final.chosen, "selection": final.selection, "half_life_days": half_life_days}
    report["duration_s"] = round(time.time() - t_start, 1)
    report["base_rate"] = float(y_all.mean())
    prog(1.0, "Terminé")
    return TrainResult(ok, "validé" if ok else "non validé", reasons, final, eval_model, report, hold_start, trial)
