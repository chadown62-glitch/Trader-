"""C082 — correction expérimentale de la calibration de l'IA principale (décision du propriétaire du 25 novembre 2026).

Constat de calibration-v1 (#157) : le recalage isotonique de l'IA principale est trop confiant aux extrêmes (probabilités
de 0,01 et 0,998 pour des fréquences observées de 0,27 et 0,40) ; son score de Brier (0,1953) est moins bon que celui des
probabilités brutes (0,1910) et du taux de gain appris (0,1890).

Correction préenregistrée, sans rien changer au moteur : recalage logistique à deux paramètres (Platt) des probabilités
brutes, appris sur la même tranche de calibration que le recalage isotonique (les 20 % les plus récents de l'apprentissage
du mois), puis appliqué aux alertes du mois jugé. Même protocole que calibration-v1 : IA réapprise chaque mois sur les
alertes dénouées avant le mois (embargo d'un jour), rééchantillonnage des mois, Holm.
Descriptif : mélange à parts égales du recalage isotonique et du taux appris ; décisions (espérance ≥ marge) qu'entraîneraient
les probabilités corrigées. Rien n'entre dans une décision sans validation humaine.

Lancement : python -m sentinelle.scalp.recalage --cache data/scalp-cache
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from . import calibration, campaign, essais
from .brain import PrincipalAI, matrix
from .data import months

EPS = 1e-4


def logit(p) -> np.ndarray:
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def platt_fit(raw, y) -> tuple[float, float]:
    """Paramètres (a, b) de p = σ(a + b·logit(brut)), par maximum de vraisemblance (régularisation négligeable)."""
    y = np.asarray(y, int)
    if len(np.unique(y)) < 2:
        rate = float(np.clip(y.mean(), EPS, 1 - EPS))
        return float(np.log(rate / (1 - rate))), 0.0
    m = LogisticRegression(C=1e6, max_iter=1000).fit(logit(raw).reshape(-1, 1), y)
    return float(m.intercept_[0]), float(m.coef_[0, 0])


def platt_apply(raw, ab: tuple[float, float]) -> np.ndarray:
    a, b = ab
    return 1 / (1 + np.exp(-(a + b * logit(raw))))


def calibration_slice(train: pd.DataFrame) -> pd.DataFrame:
    """La tranche que PrincipalAI.fit réserve au recalage : les 20 % les plus récents de l'apprentissage."""
    ordered = train.sort_values("decided_at")
    return ordered.iloc[int(len(ordered) * 0.8):]


def walk_forward(cands: pd.DataFrame, extra: tuple, tested: list[str]) -> pd.DataFrame:
    rows = []
    for m in tested:
        train, test = calibration.training_set(cands, m)
        ai = PrincipalAI(extra=extra).fit(train)
        if not ai.ready or test.empty:
            continue
        cal = calibration_slice(train)
        ab = platt_fit(ai._raw(matrix(cal, extra)), (cal["r"].to_numpy() > 0).astype(int))
        raw = ai._raw(matrix(test, extra))
        p_iso, p_platt = ai.iso.predict(raw), platt_apply(raw, ab)
        win = test["setup"].map(lambda s, ai=ai: ai.payoff[s][0]).to_numpy()
        loss = test["setup"].map(lambda s, ai=ai: ai.payoff[s][1]).to_numpy()
        rows.append(pd.DataFrame({"month": m, "y": (test["r"].to_numpy() > 0).astype(int), "r": test["r"].to_numpy(), "base_rate": ai.base_rate,
                                  "p_raw": raw, "p_iso": p_iso, "p_platt": p_platt, "p_melange": 0.5 * p_iso + 0.5 * ai.base_rate,
                                  "take_iso": p_iso * win - (1 - p_iso) * loss >= ai.margin_r,
                                  "take_platt": p_platt * win - (1 - p_platt) * loss >= ai.margin_r, "a": ab[0], "b": ab[1]}))
    return pd.concat(rows, ignore_index=True)


def gain_over_isotonic(f: pd.DataFrame) -> float:
    return calibration.brier(f["p_iso"], f["y"]) - calibration.brier(f["p_platt"], f["y"])


def skill_platt(f: pd.DataFrame) -> float:
    return 1.0 - calibration.brier(f["p_platt"], f["y"]) / calibration.brier(f["base_rate"], f["y"])


def summary(frame: pd.DataFrame) -> dict:
    out = {}
    for k in ("p_iso", "p_raw", "p_platt", "p_melange", "base_rate"):
        e, table = calibration.ece(frame[k], frame["y"])
        out[k] = {"brier": calibration.brier(frame[k], frame["y"]), "ece": e, "min": float(frame[k].min()), "max": float(frame[k].max()), "fiabilite": table}
    for k in ("iso", "platt"):
        taken = frame[frame[f"take_{k}"].astype(bool)]
        out[f"decisions_{k}"] = {"trades_pris": int(len(taken)), "r_moyen": float(taken["r"].mean()) if len(taken) else None}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="C082 — correction expérimentale de la calibration de l'IA principale (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/recalage-c082-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    variant = campaign.VARIANTS[calibration.VARIANT]
    store = Path(args.cache) / f"alerts-{variant.name}-{calibration.FIRST}-{calibration.LAST}.pkl"
    if store.exists():
        cands, _ = pickle.loads(store.read_bytes())  # mêmes alertes que calibration-v1
    else:
        cands, closes = campaign.build(campaign.SYMBOLS, calibration.FIRST, calibration.LAST, args.cache, variant=variant)
        store.write_bytes(pickle.dumps((cands, closes)))
    tested = [m for m in months(calibration.FIRST, calibration.LAST) if len(calibration.training_set(cands, m)[0]) >= 300]
    frame = walk_forward(cands, variant.extra, tested)
    boot_gain = calibration.month_bootstrap(gain_over_isotonic, frame)
    boot_skill = calibration.month_bootstrap(skill_platt, frame)
    tests = [{"test": "T1 Brier(isotonique) − Brier(Platt) > 0", "valeur": gain_over_isotonic(frame),
              "ic95": [float(np.percentile(boot_gain, 2.5)), float(np.percentile(boot_gain, 97.5))], "p_unilaterale": float((boot_gain <= 0).mean())},
             {"test": "T2 compétence de Platt sur le taux appris > 0", "valeur": skill_platt(frame),
              "ic95": [float(np.percentile(boot_skill, 2.5)), float(np.percentile(boot_skill, 97.5))], "p_unilaterale": float((boot_skill <= 0).mean())}]
    for t, ph in zip(tests, essais.holm([t["p_unilaterale"] for t in tests])):
        t["p_holm"], t["significatif_holm_5pct"] = ph, bool(ph < 0.05)
    params = frame.groupby("month")[["a", "b"]].first()
    out = {"schema": "scalp-recalage-c082-v1", "status": "correction expérimentale hors échantillon sur historique déjà consulté ; aucune intégration",
           "capacites": ["C082"], "variante": calibration.VARIANT, "mois_juges": [tested[0], tested[-1]], "alertes_jugees": int(len(frame)),
           "taux_de_gain_observe": float(frame["y"].mean()), "mesures": summary(frame), "tests": tests,
           "parametres_platt": {"pente_mediane": float(params["b"].median()), "pente_min": float(params["b"].min()), "pente_max": float(params["b"].max())},
           "seconds": round(time.time() - t0)}
    print({k: (v["brier"], round(v["ece"], 4), round(v["min"], 3), round(v["max"], 3)) for k, v in out["mesures"].items() if "brier" in v})
    print(out["mesures"]["decisions_iso"], out["mesures"]["decisions_platt"], out["parametres_platt"])
    for t in tests:
        print(t)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "recalage-c082-v1",
                                 "brier": {k: v["brier"] for k, v in out["mesures"].items() if "brier" in v},
                                 "tests": {t["test"]: {"valeur": t["valeur"], "p_holm": t["p_holm"]} for t in tests}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
