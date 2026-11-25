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
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import calibration, hive, moteur, signaux

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


def _clocks(frame: pd.DataFrame, historical: bool) -> pd.DataFrame:
    """Dates exprimées en clôtures UTC. Une réception absente ne constitue pas une observation."""
    data = frame.copy()
    features = data["day"]
    maturity = data["day"] + hive.HORIZON
    if historical:
        data["input_at"] = features
        data["label_at"] = maturity.where(data["y"].notna())
    else:
        for source, earliest, target in (("features_received_day", features, "input_at"),
                                         ("label_received_day", maturity, "label_at")):
            received = pd.to_numeric(data.get(source, pd.Series(np.nan, index=data.index)), errors="coerce")
            data[target] = received.where(np.isfinite(received) & (received >= earliest))
        data["label_at"] = data[["input_at", "label_at"]].max(axis=1, skipna=False).where(data["y"].notna())
    return data


def real(frame: pd.DataFrame, *, as_of_day: int, historical: bool = False,
         h: float = CUSUM_H, lam: float = PH_LAMBDA) -> dict:
    """Rejoue les cohortes disponibles au cutoff. Les bornes théoriques de clôture sont nommées à part."""
    frame = _clocks(frame.loc[frame["day"] <= as_of_day], historical)
    out = {}
    prefix = "borne_cloture_" if historical else "alerte_"
    for year in hive.TEST_YEARS:
        train, test = hive.split(frame, year)
        train = train[train["label_at"] < moteur._day(f"{year}-01-01")]
        if test.empty or train.empty:
            continue
        X = train[hive.FEATURES].to_numpy()
        model = hive.Logit().fit(X, train["y"].to_numpy())
        ptr = model.proba(X)
        res_w = (train["y"] - ptr).groupby(train["day"]).mean()
        loss_w = pd.Series(hive.logloss(ptr, train["y"]), index=train.index).groupby(train["day"]).mean()
        inputs, outcomes = [], []
        for day, group in test.groupby("day"):
            received = group["input_at"]
            if not received.notna().all() or received.max() > as_of_day:
                continue
            x = group[hive.FEATURES].to_numpy()
            inputs.append((int(received.max()), int(day), x))
            available = group["label_at"]
            if not available.notna().all() or available.max() > as_of_day:
                continue
            pr = model.proba(x)
            labels = group["y"].to_numpy()
            outcomes.append((int(available.max()), int(day), float((labels - pr).mean()),
                             float(hive.logloss(pr, labels).mean())))
        inputs.sort(key=lambda item: item[:2])
        outcomes.sort(key=lambda item: item[:2])
        input_days = [r[0] for r in inputs]
        label_days = [r[0] for r in outcomes]
        bias = np.array([r[2] for r in outcomes])
        loss = np.array([r[3] for r in outcomes])
        a_in = psi_alarm(X, [r[2] for r in inputs])
        a_b = cusum(bias, float(res_w.std()), h=h)
        a_l = page_hinkley(loss, float(loss_w.mean()), float(loss_w.std()), lam=lam)
        out[year] = {"semaines_entrees": len(inputs), "semaines_resultats": len(outcomes),
                     "perte_moyenne": float(loss.mean()) if len(loss) else None,
                     "perte_apprentissage": float(loss_w.mean()), "biais_moyen": float(bias.mean()) if len(bias) else None,
                     prefix + "entrees": week_date(input_days, a_in), prefix + "biais": week_date(label_days, a_b),
                     prefix + "erreurs": week_date(label_days, a_l)}
    return out


def compare_archive(out, field, filename):
    """Compare signal-week dates in v1 with availability bounds in v2 without modifying the archive."""
    raw = (Path(__file__).resolve().parents[2] / "research/scalp-v1" / filename).read_bytes()
    previous = json.loads(raw)
    changes = []
    for year, row in out[field].items():
        for detector in ("entrees", "biais", "erreurs"):
            v1 = previous[field].get(str(year), {}).get("alerte_" + detector)
            v2 = row.get("borne_cloture_" + detector)
            if v1 is not None or v2 is not None:
                changes.append({"annee": int(year), "detecteur": detector, "date_v1": v1, "borne_cloture_v2": v2})
    return {"schema": previous["schema"], "sha256": hashlib.sha256(raw).hexdigest(), "dates": changes}


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA D — dégradation des modèles et détecteurs (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/omega-pulse-v2.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    if Path(args.out).exists():
        raise FileExistsError(f"Résultat déjà présent : {args.out}. Choisir un autre --out pour le rejeu.")
    t0 = time.time()
    synth = synthetic()
    t_synth = round(time.time() - t0)
    p, manifest_sha = hive.frozen_panel(Path(args.cache))
    frame = hive.decision_samples(p, signaux.members(p), hive.features(p), moteur._day(signaux.FIRST))
    reel = real(frame, as_of_day=hive.quoted_days(p["c"]).max(), historical=True)
    out = {"schema": "scalp-omega-pulse-v2", "manifeste_sha256": manifest_sha,
           "horloge_reelle": "Bornes théoriques à la clôture UTC ; réception historique inconnue, aucune alerte opérationnelle datable", "status": "synthétique reconstruit puis descriptif sur l'historique déjà consulté ; aucune quarantaine appliquée",
           "omega": ["PULSE", "QUARANTINE"], "capacites": ["C084", "C082", "C083"],
           "parametres": {"caracteristiques": K, "par_semaine": PER_WEEK, "apprentissage_semaines": TRAIN_WEEKS, "surveillance_semaines": MONITOR_WEEKS,
                          "rupture_semaine": BREAK_AT, "repetitions": REPS, "psi": [PSI_WINDOW, PSI_ALERT], "cusum": [CUSUM_K, CUSUM_H], "page_hinkley": [PH_DELTA, PH_LAMBDA]},
           "synthetique": synth, "reel_modele_collectif": reel, "secondes_synthetique": t_synth, "seconds": round(time.time() - t0)}
    for sc, d in synth.items():
        print(sc, json.dumps(d, ensure_ascii=False))
    for y, d in reel.items():
        print(y, json.dumps(d, ensure_ascii=False))
    out["comparaison_v1"] = compare_archive(out, "reel_modele_collectif", "omega-pulse-v1.json")
    with Path(args.out).open("x", encoding="utf-8") as target:
        target.write(json.dumps(out, ensure_ascii=False, default=float, allow_nan=False) + "\n")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-pulse-v2", "synthetique": synth,
                                 "reel": {y: {k: v for k, v in d.items() if k.startswith("borne_cloture_")} for y, d in reel.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
