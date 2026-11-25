"""OMEGA priorité D — PULSE (dégradation des modèles) et QUARANTINE (mise à l'écart d'un modèle dégradé).

Un modèle peut se dégrader alors que ses entrées semblent normales. On distingue trois signaux et on mesure, pour chacun, le
délai de détection, les fausses alertes et les dégradations non détectées :
- dérive des entrées : PSI de chaque caractéristique, 13 dernières semaines contre l'apprentissage ; alerte au-delà de 0,25 ;
- biais des prédictions : CUSUM bilatéral de la moyenne hebdomadaire de y − p, k = 0,5 σ, h = 5 σ ;
- hausse des erreurs : Page-Hinkley sur la perte logarithmique hebdomadaire, δ = 0,5 σ, λ = 5 σ ;
σ estimé sur l'apprentissage seul. Seuils fixés a priori, jamais réglés sur les scénarios.

Synthétique (reconstruction : le registre OMEGA V3 n'est pas accessible) : 5 caractéristiques normales, 20 prédictions par
semaine, 104 semaines d'apprentissage d'une régression logistique, 156 semaines surveillées, rupture à la semaine 76 de la
surveillance, 200 répétitions par scénario :
- aucune rupture (fausses alertes) ;
- rupture nette de la relation (coefficients inversés), entrées inchangées ;
- rupture faible (premier coefficient de 0,6 à 0,2) ;
- rupture progressive (vers les coefficients inversés en 52 semaines) ;
- rupture dépendante (nette, avec un choc commun hebdomadaire autocorrélé, 0,8) ;
- dérive des entrées seule (moyenne de la première caractéristique + 1, relation inchangée : le modèle reste juste).
Réel : les mêmes détecteurs sur les prédictions du modèle collectif de HIVE (walk-forward annuel 2020 → 2026), remis à zéro
à chaque année ; descriptif (la vraie dégradation n'est pas connue).
Les vetos déterministes ne sont pas concernés : rien n'est mis en quarantaine dans le moteur, mesure seulement.

Lancement : python -m sentinelle.scalp.pulse
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import calibration, hive, moteur, recherche, signaux

K, PER_WEEK, TRAIN_WEEKS, MONITOR_WEEKS, BREAK_AT, REPS = 5, 20, 104, 156, 76, 200
BETA = np.array([0.6, -0.4, 0.3, 0.0, 0.0])
PSI_WINDOW, PSI_ALERT = 13, 0.25
CUSUM_K, CUSUM_H, PH_DELTA, PH_LAMBDA = 0.5, 5.0, 0.5, 5.0
SCENARIOS = ("aucune", "nette", "faible", "progressive", "dependante", "entrees_seules")
SEED = 0


# --- Détecteurs -------------------------------------------------------------------------------------------------------

def cusum(x: np.ndarray, sigma: float, k: float = CUSUM_K, h: float = CUSUM_H) -> int | None:
    """Premier indice où la somme cumulée (bilatérale) des écarts au-delà de k·σ dépasse h·σ ; None sans alerte."""
    hi = lo = 0.0
    for i, v in enumerate(x):
        hi = max(0.0, hi + v - k * sigma)
        lo = max(0.0, lo - v - k * sigma)
        if hi > h * sigma or lo > h * sigma:
            return i
    return None


def page_hinkley(x: np.ndarray, reference: float, sigma: float, delta: float = PH_DELTA, lam: float = PH_LAMBDA) -> int | None:
    """Hausse de la moyenne de x au-dessus de la référence d'apprentissage : premier indice d'alerte, None sinon."""
    m = 0.0
    for i, v in enumerate(x):
        m = max(0.0, m + v - reference - delta * sigma)
        if m > lam * sigma:
            return i
    return None


def psi_alarm(train: np.ndarray, weeks: list[np.ndarray], window: int = PSI_WINDOW, alert: float = PSI_ALERT) -> int | None:
    """Premier indice de semaine où le PSI d'une caractéristique (fenêtre glissante) dépasse le seuil ; None sinon."""
    for i in range(window - 1, len(weeks)):
        recent = np.vstack(weeks[i - window + 1: i + 1])
        if max(calibration.psi(train[:, j], recent[:, j]) for j in range(train.shape[1])) > alert:
            return i
    return None


# --- Scénarios synthétiques -------------------------------------------------------------------------------------------

def beta_at(scenario: str, week: int) -> np.ndarray:
    """Coefficients vrais à la semaine de surveillance « week »."""
    if week < BREAK_AT or scenario in ("aucune", "entrees_seules"):
        return BETA
    if scenario in ("nette", "dependante"):
        return -BETA
    if scenario == "faible":
        return BETA * np.array([1 / 3, 1, 1, 1, 1])
    frac = min(1.0, (week - BREAK_AT + 1) / 52)  # progressive
    return BETA * (1 - 2 * frac)


def simulate(scenario: str, rng: np.random.Generator) -> dict:
    """Une répétition : apprentissage, surveillance, premières alertes de chaque détecteur (indices de semaine)."""
    Xtr = rng.normal(0, 1, (TRAIN_WEEKS * PER_WEEK, K))
    ytr = (rng.random(len(Xtr)) < 1 / (1 + np.exp(-Xtr @ BETA))).astype(int)
    model = hive.Logit().fit(Xtr, ytr)
    ptr = model.proba(Xtr)
    wk = np.repeat(np.arange(TRAIN_WEEKS), PER_WEEK)
    res_w = pd.Series(ytr - ptr).groupby(wk).mean().to_numpy()
    loss_w = pd.Series(hive.logloss(ptr, ytr)).groupby(wk).mean().to_numpy()
    shock, xs, bias, loss = 0.0, [], [], []
    for w in range(MONITOR_WEEKS):
        X = rng.normal(0, 1, (PER_WEEK, K))
        if scenario == "entrees_seules" and w >= BREAK_AT:
            X[:, 0] += 1.0
        shock = 0.8 * shock + rng.normal(0, 0.5) if scenario == "dependante" else 0.0
        z = X @ beta_at(scenario, w) + (shock if scenario == "dependante" and w >= BREAK_AT else 0.0)
        y = (rng.random(PER_WEEK) < 1 / (1 + np.exp(-z))).astype(int)
        p = model.proba(X)
        xs.append(X)
        bias.append(float(np.mean(y - p)))
        loss.append(float(hive.logloss(p, y).mean()))
    return {"entrees": psi_alarm(Xtr, xs), "biais": cusum(np.array(bias), float(res_w.std())),
            "erreurs": page_hinkley(np.array(loss), float(loss_w.mean()), float(loss_w.std()))}


def summarize(alarms: list[int | None], degraded: bool) -> dict:
    """Pour un détecteur et un scénario : fausses alertes (avant la rupture, ou n'importe quand sans dégradation),
    détections après la rupture et délai médian, non-détections."""
    a = np.array([np.nan if x is None else x for x in alarms], dtype=float)
    early = np.isfinite(a) & ((a < BREAK_AT) | (not degraded))
    detected = np.isfinite(a) & (a >= BREAK_AT) & degraded
    delays = a[detected] - BREAK_AT
    return {"fausses_alertes": float(early.mean()), "detections": float(detected.mean()) if degraded else None,
            "non_detections": float((~np.isfinite(a) | (a < BREAK_AT)).mean()) if degraded else None,
            "delai_median_semaines": float(np.median(delays)) if len(delays) else None}


def synthetic(reps: int = REPS, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    out = {}
    for sc in SCENARIOS:
        runs = [simulate(sc, rng) for _ in range(reps)]
        degraded = sc not in ("aucune", "entrees_seules")
        out[sc] = {d: summarize([r[d] for r in runs], degraded) for d in ("entrees", "biais", "erreurs")}
    return out


# --- Réel : modèle collectif de HIVE ----------------------------------------------------------------------------------

def week_date(weeks, i: int | None) -> str | None:
    return None if i is None else str(pd.to_datetime(int(weeks[i]) * moteur.DAY_MS, unit="ms").date())


def real(frame: pd.DataFrame) -> dict:
    """Détecteurs remis à zéro chaque année jugée ; σ et références tirés des prédictions d'apprentissage."""
    out = {}
    for year in hive.TEST_YEARS:
        train, test = hive.split(frame, year)
        if test.empty:
            continue
        model = hive.Logit().fit(train[hive.FEATURES].to_numpy(), train["y"].to_numpy())
        ptr = model.proba(train[hive.FEATURES].to_numpy())
        res_w = (train["y"] - ptr).groupby(train["day"]).mean()
        loss_w = pd.Series(hive.logloss(ptr, train["y"]), index=train.index).groupby(train["day"]).mean()
        pt = model.proba(test[hive.FEATURES].to_numpy())
        weeks = sorted(test["day"].unique())
        g = test.assign(p=pt, l=hive.logloss(pt, test["y"]), r=test["y"] - pt).groupby("day")
        bias, loss = g["r"].mean().reindex(weeks).to_numpy(), g["l"].mean().reindex(weeks).to_numpy()
        xs = [test.loc[test["day"] == d, hive.FEATURES].to_numpy() for d in weeks]
        a_in = psi_alarm(train[hive.FEATURES].to_numpy(), xs)
        a_b = cusum(bias, float(res_w.std()))
        a_l = page_hinkley(loss, float(loss_w.mean()), float(loss_w.std()))
        out[year] = {"semaines": len(weeks), "perte_moyenne": float(np.mean(loss)), "perte_apprentissage": float(loss_w.mean()),
                     "biais_moyen": float(np.mean(bias)), "alerte_entrees": week_date(weeks, a_in), "alerte_biais": week_date(weeks, a_b),
                     "alerte_erreurs": week_date(weeks, a_l)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA D — dégradation des modèles et détecteurs (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/omega-pulse-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    synth = synthetic()
    t_synth = round(time.time() - t0)
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    p = signaux.panel(loaded)
    frame = hive.samples(p, signaux.members(p), hive.features(p), moteur._day(signaux.FIRST))
    reel = real(frame)
    out = {"schema": "scalp-omega-pulse-v1", "status": "synthétique reconstruit puis descriptif sur l'historique déjà consulté ; aucune quarantaine appliquée",
           "omega": ["PULSE", "QUARANTINE"], "capacites": ["C084", "C082", "C083"],
           "parametres": {"caracteristiques": K, "par_semaine": PER_WEEK, "apprentissage_semaines": TRAIN_WEEKS, "surveillance_semaines": MONITOR_WEEKS,
                          "rupture_semaine": BREAK_AT, "repetitions": REPS, "psi": [PSI_WINDOW, PSI_ALERT], "cusum": [CUSUM_K, CUSUM_H], "page_hinkley": [PH_DELTA, PH_LAMBDA]},
           "synthetique": synth, "reel_modele_collectif": reel, "secondes_synthetique": t_synth, "seconds": round(time.time() - t0)}
    for sc, d in synth.items():
        print(sc, json.dumps(d, ensure_ascii=False))
    for y, d in reel.items():
        print(y, json.dumps(d, ensure_ascii=False))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-pulse-v1", "synthetique": synth,
                                 "reel": {y: {k: v for k, v in d.items() if k.startswith("alerte")} for y, d in reel.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
