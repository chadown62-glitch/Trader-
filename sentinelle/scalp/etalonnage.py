"""OMEGA D — étalonnage de PULSE sur un protocole séparé (décision du propriétaire du 25 novembre 2026).

#174 a montré que les seuils fixés a priori (CUSUM h = 5 σ, Page-Hinkley λ = 5 σ) donnent 31 à 41 % de fausses alertes sur
156 semaines sans rupture. Ici :
1. Étalonnage : sur le générateur de #174 (graine A, scénario sans rupture seulement), on retient le plus petit seuil de la
   grille fixée a priori dont la part de fausses alertes sur 156 semaines ne dépasse pas 10 %.
2. Jugement sur des scénarios indépendants de l'étalonnage :
   - même générateur, autre graine (B) ;
   - autre générateur (8 caractéristiques, 30 prévisions par semaine, autres coefficients, rupture à la semaine 60) ;
   six scénarios chacun (aucune, nette, faible, progressive, dépendante, entrées seules), seuils a priori et étalonnés.
3. Réel (descriptif) : alertes du modèle collectif de HIVE, année par année, avec les seuils étalonnés.
Aucune quarantaine n'est appliquée ; les vetos déterministes ne sont pas concernés.

Lancement : python -m sentinelle.scalp.etalonnage
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import hive, moteur, pulse, signaux

GRID = (5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0)
TARGET_FALSE_ALARMS = 0.10
SEED_A, SEED_B, SEED_C = 20261201, 20261202, 20261203
CAL_REPS, EVAL_REPS = 300, 200


@dataclass(frozen=True)
class Generator:
    name: str
    k: int = pulse.K
    per_week: int = pulse.PER_WEEK
    beta: tuple = tuple(pulse.BETA)
    train_weeks: int = pulse.TRAIN_WEEKS
    monitor_weeks: int = pulse.MONITOR_WEEKS
    break_at: int = pulse.BREAK_AT
    extra: dict = field(default_factory=dict)


G1 = Generator("generateur_de_174")
G2 = Generator("generateur_independant", k=8, per_week=30, beta=(0.5, -0.5, 0.3, 0.2, 0.0, 0.0, 0.0, 0.0), break_at=60)


def beta_at(gen: Generator, scenario: str, week: int) -> np.ndarray:
    b = np.array(gen.beta)
    if week < gen.break_at or scenario in ("aucune", "entrees_seules"):
        return b
    if scenario in ("nette", "dependante"):
        return -b
    if scenario == "faible":
        return b * np.r_[1 / 3, np.ones(gen.k - 1)]
    return b * (1 - 2 * min(1.0, (week - gen.break_at + 1) / 52))


def series(gen: Generator, scenario: str, rng: np.random.Generator) -> dict:
    """Séries hebdomadaires de biais et de perte d'une répétition, avec σ et référence tirés de l'apprentissage."""
    b = np.array(gen.beta)
    Xtr = rng.normal(0, 1, (gen.train_weeks * gen.per_week, gen.k))
    ytr = (rng.random(len(Xtr)) < 1 / (1 + np.exp(-Xtr @ b))).astype(int)
    model = hive.Logit().fit(Xtr, ytr)
    ptr = model.proba(Xtr)
    wk = np.repeat(np.arange(gen.train_weeks), gen.per_week)
    res_w = pd.Series(ytr - ptr).groupby(wk).mean().to_numpy()
    loss_w = pd.Series(hive.logloss(ptr, ytr)).groupby(wk).mean().to_numpy()
    shock, bias, loss = 0.0, [], []
    for w in range(gen.monitor_weeks):
        X = rng.normal(0, 1, (gen.per_week, gen.k))
        if scenario == "entrees_seules" and w >= gen.break_at:
            X[:, 0] += 1.0
        shock = 0.8 * shock + rng.normal(0, 0.5) if scenario == "dependante" else 0.0
        z = X @ beta_at(gen, scenario, w) + (shock if scenario == "dependante" and w >= gen.break_at else 0.0)
        y = (rng.random(gen.per_week) < 1 / (1 + np.exp(-z))).astype(int)
        p = model.proba(X)
        bias.append(float(np.mean(y - p)))
        loss.append(float(hive.logloss(p, y).mean()))
    return {"biais": np.array(bias), "erreurs": np.array(loss), "sigma_biais": float(res_w.std()),
            "reference_perte": float(loss_w.mean()), "sigma_perte": float(loss_w.std())}


def first_alarms(s: dict, h: float, lam: float) -> dict:
    return {"biais": pulse.cusum(s["biais"], s["sigma_biais"], h=h),
            "erreurs": pulse.page_hinkley(s["erreurs"], s["reference_perte"], s["sigma_perte"], lam=lam)}


def summarize(alarms: list, degraded: bool, break_at: int) -> dict:
    a = np.array([np.nan if x is None else x for x in alarms], dtype=float)
    early = np.isfinite(a) & ((a < break_at) | (not degraded))
    detected = np.isfinite(a) & (a >= break_at) & degraded
    delays = a[detected] - break_at
    return {"fausses_alertes": float(early.mean()), "detections": float(detected.mean()) if degraded else None,
            "delai_median_semaines": float(np.median(delays)) if len(delays) else None}


def calibrate(reps: int = CAL_REPS, seed: int = SEED_A, gen: Generator = G1) -> dict:
    """Part de fausses alertes de chaque seuil de la grille sur le scénario sans rupture ; plus petit seuil ≤ 10 %."""
    rng = np.random.default_rng(seed)
    runs = [series(gen, "aucune", rng) for _ in range(reps)]
    table = {"biais": {}, "erreurs": {}}
    for t in GRID:
        table["biais"][t] = float(np.mean([pulse.cusum(s["biais"], s["sigma_biais"], h=t) is not None for s in runs]))
        table["erreurs"][t] = float(np.mean([pulse.page_hinkley(s["erreurs"], s["reference_perte"], s["sigma_perte"], lam=t) is not None for s in runs]))
    chosen = {d: next((t for t in GRID if table[d][t] <= TARGET_FALSE_ALARMS), GRID[-1]) for d in table}
    return {"fausses_alertes_par_seuil": {d: {f"{t:g}": v for t, v in table[d].items()} for d in table}, "seuils": chosen}


def evaluate(gen: Generator, seed: int, thresholds: dict, reps: int = EVAL_REPS) -> dict:
    rng = np.random.default_rng(seed)
    out = {}
    for sc in pulse.SCENARIOS:
        runs = [series(gen, sc, rng) for _ in range(reps)]
        degraded = sc not in ("aucune", "entrees_seules")
        row = {}
        for label, (h, lam) in thresholds.items():
            alarms = [first_alarms(s, h, lam) for s in runs]
            row[label] = {d: summarize([a[d] for a in alarms], degraded, gen.break_at) for d in ("biais", "erreurs")}
        out[sc] = row
    return out


def real(frame: pd.DataFrame, h: float, lam: float, *, as_of_day: int, historical: bool = False) -> dict:
    """Mêmes cohortes et mêmes horloges que PULSE, avec les deux seuils étalonnés."""
    rows = pulse.real(frame, h=h, lam=lam, as_of_day=as_of_day, historical=historical)
    return {year: {k: v for k, v in row.items() if k.endswith(("biais", "erreurs")) or k == "semaines_resultats"}
            for year, row in rows.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA D — étalonnage de PULSE sur scénarios indépendants (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/pulse-etalonnage-v2.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    if Path(args.out).exists():
        raise FileExistsError(f"Résultat déjà présent : {args.out}. Choisir un autre --out pour le rejeu.")
    t0 = time.time()
    cal = calibrate()
    h, lam = cal["seuils"]["biais"], cal["seuils"]["erreurs"]
    thresholds = {"a_priori": (pulse.CUSUM_H, pulse.PH_LAMBDA), "etalonnes": (h, lam)}
    ev = {"generateur_de_174_graine_B": evaluate(G1, SEED_B, thresholds), "generateur_independant_graine_C": evaluate(G2, SEED_C, thresholds)}
    t_synth = round(time.time() - t0)
    p, manifest_sha = hive.frozen_panel(Path(args.cache))
    frame = hive.decision_samples(p, signaux.members(p), hive.features(p), moteur._day(signaux.FIRST))
    reel = real(frame, h, lam, as_of_day=hive.quoted_days(p["c"]).max(), historical=True)
    checks = {}
    for name, e in ev.items():
        fa = max(e["aucune"]["etalonnes"]["biais"]["fausses_alertes"], e["aucune"]["etalonnes"]["erreurs"]["fausses_alertes"])
        checks[name] = {"h1_fausses_alertes_au_plus_15pct": bool(fa <= 0.15), "fausses_alertes_max": fa,
                        "h2_rupture_nette_detectee_au_moins_70pct": bool(e["nette"]["etalonnes"]["erreurs"]["detections"] >= 0.70),
                        "detection_nette_erreurs": e["nette"]["etalonnes"]["erreurs"]["detections"]}
    out = {"schema": "scalp-pulse-etalonnage-v2", "manifeste_sha256": manifest_sha,
           "horloge_reelle": "Bornes théoriques à la clôture UTC ; réception historique inconnue", "status": "étalonnage synthétique puis jugement sur scénarios indépendants ; réel descriptif ; aucune quarantaine",
           "omega": ["PULSE", "QUARANTINE", "REPLAY"], "capacites": ["C084", "C079"], "grille": list(GRID), "cible_fausses_alertes": TARGET_FALSE_ALARMS,
           "graines": {"etalonnage": SEED_A, "jugement_meme_generateur": SEED_B, "jugement_generateur_independant": SEED_C},
           "generateurs": {g.name: {"caracteristiques": g.k, "par_semaine": g.per_week, "coefficients": list(g.beta), "rupture_semaine": g.break_at} for g in (G1, G2)},
           "etalonnage": cal, "jugement": ev, "hypotheses": checks, "reel_seuils_etalonnes": reel, "secondes_synthetique": t_synth, "seconds": round(time.time() - t0)}
    print("seuils :", cal["seuils"], cal["fausses_alertes_par_seuil"])
    for name, e in ev.items():
        for sc, row in e.items():
            print(name, sc, json.dumps(row, ensure_ascii=False))
    print(json.dumps(checks, ensure_ascii=False))
    print(json.dumps(reel, ensure_ascii=False))
    out["comparaison_v1"] = pulse.compare_archive(out, "reel_seuils_etalonnes", "pulse-etalonnage-v1.json")
    with Path(args.out).open("x", encoding="utf-8") as target:
        target.write(json.dumps(out, ensure_ascii=False, default=float, allow_nan=False) + "\n")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "pulse-etalonnage-v2", "seuils": cal["seuils"],
                                 "hypotheses": checks, "reel": reel}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
