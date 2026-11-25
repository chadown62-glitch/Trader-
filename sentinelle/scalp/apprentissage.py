"""Spécialistes par régime (C085) et retrait des caractéristiques redondantes (C086) dans l'IA principale.

Même protocole que calibration.py (C082) : alertes de la variante v4 (bougies d'une minute, 2023-09 → 2026-08), IA
principale (brain.PrincipalAI) réapprise chaque mois sur les seules alertes dénouées avant le mois (embargo d'un jour),
jugement hors échantillon des alertes du mois. Les trois versions jugent exactement les mêmes alertes :
- référence : un modèle unique, toutes les caractéristiques ;
- C086 : sur l'apprentissage du mois seulement, les caractéristiques sont parcourues dans l'ordre de brain.matrix ; une
  caractéristique dont la corrélation de rang absolue avec une caractéristique déjà gardée dépasse 0,90 est retirée
  (mise à valeur absente : neutre pour les arbres comme pour le modèle linéaire) ;
- C085 : deux spécialistes, l'un appris sur les alertes décidées quand le BTC était en tendance à la clôture de la veille
  (efficacité de Kaufman sur 30 jours > 0,3, regimes.efficiency_ratio), l'autre en range ; chacun juge les alertes de
  son régime. Un spécialiste sans 300 alertes d'apprentissage (brain.MIN_TRAIN) et une alerte de régime inconnu sont
  confiés au modèle unique (règles fixées a priori).
Critère principal : écart de score de Brier de la probabilité calibrée, apparié alerte par alerte (négatif = meilleur),
intervalle à 95 % et p unilatérale « meilleur » par rééchantillonnage des mois ; Holm sur les 2. Secondaires :
compétence sur le taux appris, erreur de calibration, dispersion des compétences mensuelles (stabilité).
Mesure seulement : rien n'est modifié dans l'IA principale et rien n'entre dans une décision.

Lancement : python -m sentinelle.scalp.apprentissage --cache data/scalp-cache
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import calibration, campaign, essais, moteur, regimes, signaux
from .brain import MIN_TRAIN, PrincipalAI, matrix
from .data import months

THRESHOLD = 0.90


def dedup_columns(X: pd.DataFrame, threshold: float = THRESHOLD) -> list[str]:
    """Caractéristiques retirées : parcours dans l'ordre des colonnes, retrait de toute colonne trop corrélée (rangs)
    à une colonne déjà gardée. Indicateurs de setup (is_…) et colonnes vides jamais retirés ni comparés."""
    cols = [c for c in X.columns if not c.startswith("is_") and X[c].notna().sum() > 1]
    ranks = X[cols].rank()
    corr = ranks.corr().abs()
    kept, dropped = [], []
    for c in cols:
        if any(corr.at[c, k] > threshold for k in kept if np.isfinite(corr.at[c, k])):
            dropped.append(c)
        else:
            kept.append(c)
    return dropped


def masked(cands: pd.DataFrame, dropped: list[str]) -> pd.DataFrame:
    return cands.assign(**{c: np.nan for c in dropped}) if dropped else cands


def trend_of_previous_day(daily_close: pd.Series, decided_ms: np.ndarray) -> np.ndarray:
    """Régime du BTC à la clôture de la veille de chaque décision : 1 tendance, 0 range, NaN inconnu."""
    er = regimes.efficiency_ratio(daily_close)
    trend = (er > regimes.ER_TREND).astype(float).where(er.notna())
    day = np.asarray(decided_ms, dtype=np.int64) // moteur.DAY_MS
    return trend.reindex(day - 1).to_numpy()


def judge_month(train: pd.DataFrame, test: pd.DataFrame, extra: tuple, reg_train: np.ndarray, reg_test: np.ndarray) -> dict | None:
    """Probabilités calibrées des trois versions sur les alertes du mois (None si le modèle unique ne peut apprendre)."""
    ref = PrincipalAI(extra=extra).fit(train)
    if not ref.ready or test.empty:
        return None
    p_ref = ref.judge(test)["p"].to_numpy()
    dropped = dedup_columns(matrix(train, extra))
    ded = PrincipalAI(extra=extra).fit(masked(train, dropped))
    p_ded = ded.judge(masked(test, dropped))["p"].to_numpy() if ded.ready else p_ref
    p_spec = p_ref.copy()
    fallback = []
    for g in (1.0, 0.0):
        sel = reg_test == g
        if not sel.any():
            continue
        sub = train[reg_train == g]
        spec = PrincipalAI(extra=extra).fit(sub) if len(sub) >= MIN_TRAIN else None
        if spec is not None and spec.ready:
            p_spec[sel] = spec.judge(test[sel])["p"].to_numpy()
        else:
            fallback.append("tendance" if g else "range")
    return {"p_ref": p_ref, "p_dedup": p_ded, "p_spec": p_spec, "base_rate": ref.base_rate, "dropped": dropped, "fallback": fallback}


def paired(frame: pd.DataFrame, col: str) -> dict:
    """Écart de Brier apparié (version − référence) ; intervalle et p unilatérale « meilleur » par rééchantillonnage des mois."""
    def stat(f):
        return float(np.mean((f[col] - f["y"]) ** 2 - (f["p_ref"] - f["y"]) ** 2))
    boot = calibration.month_bootstrap(stat, frame)
    return {"ecart_brier": stat(frame), "ic95": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            "p_unilaterale": float((boot >= 0).mean())}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spécialistes par régime et retrait des caractéristiques redondantes (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache", help="alertes déjà construites (campaign.build) et archives journalières")
    ap.add_argument("--daily-cache", default=None, help="archives journalières, si elles sont ailleurs que --cache")
    ap.add_argument("--out", default="research/scalp-v1/apprentissage-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    variant = campaign.VARIANTS[calibration.VARIANT]
    store = Path(args.cache) / f"alerts-{variant.name}-{calibration.FIRST}-{calibration.LAST}.pkl"
    if store.exists():
        cands, _ = pickle.loads(store.read_bytes())
    else:
        cands, closes = campaign.build(campaign.SYMBOLS, calibration.FIRST, calibration.LAST, args.cache, variant=variant)
        store.write_bytes(pickle.dumps((cands, closes)))
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        bars, _ = signaux.load_bars("BTCUSDT", args.daily_cache or args.cache, client)
    daily = bars["c"]
    regime = pd.Series(trend_of_previous_day(daily, cands["decided_at"].to_numpy()), index=cands.index)
    tested = [m for m in months(calibration.FIRST, calibration.LAST) if len(calibration.training_set(cands, m)[0]) >= MIN_TRAIN]
    parts, log = [], []
    for m in tested:
        train, test = calibration.training_set(cands, m)
        res = judge_month(train, test, variant.extra, regime.loc[train.index].to_numpy(), regime.loc[test.index].to_numpy())
        if res is None:
            continue
        parts.append(pd.DataFrame({"month": m, "y": (test["r"].to_numpy() > 0).astype(int), "p_ref": res["p_ref"], "p_dedup": res["p_dedup"],
                                   "p_spec": res["p_spec"], "base_rate": res["base_rate"], "regime": regime.loc[test.index].to_numpy()}))
        log.append({"month": m, "retirees": res["dropped"], "specialiste_remplace": res["fallback"]})
    frame = pd.concat(parts, ignore_index=True)
    tests = {"C086 retrait des caractéristiques redondantes": paired(frame, "p_dedup"), "C085 spécialistes par régime": paired(frame, "p_spec")}
    for t, ph in zip(tests.values(), essais.holm([t["p_unilaterale"] for t in tests.values()])):
        t["p_holm"], t["significatif_holm_5pct"] = ph, bool(ph < 0.05)
    base = calibration.brier(frame["base_rate"], frame["y"])
    summary = {}
    for name, col in (("reference", "p_ref"), ("dedup", "p_dedup"), ("specialistes", "p_spec")):
        monthly = pd.Series({m: 1 - calibration.brier(g[col], g["y"]) / calibration.brier(g["base_rate"], g["y"]) for m, g in frame.groupby("month")})
        summary[name] = {"brier": calibration.brier(frame[col], frame["y"]), "competence": 1 - calibration.brier(frame[col], frame["y"]) / base,
                         "ece": calibration.ece(frame[col], frame["y"])[0], "ecart_type_competence_mensuelle": float(monthly.std())}
    counts = pd.Series([c for x in log for c in x["retirees"]]).value_counts()
    out = {"schema": "scalp-apprentissage-v1", "status": "mesure hors échantillon mois par mois sur historique déjà consulté ; aucune modification de l'IA",
           "capacites": ["C085", "C086"], "variante": calibration.VARIANT, "mois_juges": [log[0]["month"], log[-1]["month"]], "alertes_jugees": int(len(frame)),
           "seuil_correlation": THRESHOLD, "part_alertes_en_tendance": float(np.nanmean(frame["regime"])), "alertes_regime_inconnu": int(frame["regime"].isna().sum()),
           "caracteristiques_retirees_par_mois": {k: int(v) for k, v in counts.items()}, "mois": log, "versions": summary, "tests": tests,
           "seconds": round(time.time() - t0)}
    print({k: out[k] for k in ("mois_juges", "alertes_jugees", "part_alertes_en_tendance", "alertes_regime_inconnu", "caracteristiques_retirees_par_mois")})
    for k, v in summary.items():
        print(f"{k:13} Brier {v['brier']:.5f} compétence {v['competence']:+.4f} ECE {v['ece']:.4f} écart type mensuel {v['ecart_type_competence_mensuelle']:.4f}")
    for k, v in tests.items():
        print(f"{k:48} écart de Brier {v['ecart_brier']:+.5f} IC [{v['ic95'][0]:+.5f} ; {v['ic95'][1]:+.5f}] p {v['p_unilaterale']:.3f} Holm {v['p_holm']:.3f}")
    print("spécialistes remplacés :", sum(bool(x["specialiste_remplace"]) for x in log), "mois sur", len(log))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "apprentissage-v1",
                                 "tests": {k: {"ecart_brier": v["ecart_brier"], "ic95": v["ic95"], "p_holm": v["p_holm"]} for k, v in tests.items()},
                                 "versions": summary}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
