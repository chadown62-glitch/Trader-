"""Calibration probabiliste, abstention par incertitude et dérive des données de l'IA principale (lot 2 : C082, C083, C084).

L'IA principale (brain.PrincipalAI) est réapprise chaque mois sur les seules alertes dénouées avant le mois (embargo d'un
jour, comme campaign.year_test), puis juge les alertes du mois. Sur ces jugements hors échantillon :
- C082 : score de Brier de la probabilité calibrée contre la référence « taux de gain appris » (score de compétence),
  erreur de calibration par tranches (ECE) avant et après recalage isotonique ;
- C083 : désaccord entre les deux modèles de l'IA (arbres et linéaire). Parmi les trades que l'IA prendrait, la règle
  fixée a priori s'abstient quand le désaccord dépasse 0,20 ; on compare le R moyen des trades gardés à celui de tous ;
- C084 : dérive de chaque caractéristique entre l'apprentissage et le mois jugé (indice de stabilité de population, PSI,
  tranches = déciles de l'apprentissage) ; on teste si les mois les plus dérivés sont les moins bien calibrés.
Intervalles et p-valeurs par rééchantillonnage des mois (les alertes d'un même mois ne sont pas indépendantes), Holm sur
les trois tests. Mesure seulement : rien n'entre dans une décision.

Lancement : python -m sentinelle.scalp.calibration --cache data/scalp-cache
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import campaign, essais
from .brain import PrincipalAI, matrix
from .data import month_bounds, months

VARIANT = "v4"  # IA principale avec tendance journalière apprise et frais en BNB (campaign.VARIANTS)
FIRST, LAST = "2023-09", "2026-08"  # alertes 1 minute ; la période réservée n'est pas chargée (data.guard)
DISAGREEMENT = 0.20
PSI_ALERT = 0.25
BINS = 10
DRAWS, SEED = 2000, 0


def training_set(cands: pd.DataFrame, month: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apprentissage : alertes dénouées avant le mois moins un jour ; jugement : alertes décidées pendant le mois."""
    start, end = month_bounds(month)
    train = cands[cands["exit_ms"] < start - campaign.EMBARGO_MS]
    test = cands[(cands["decided_at"] >= start) & (cands["decided_at"] <= end)]
    return train, test


def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def ece(p, y, bins: int = BINS) -> tuple[float, list[dict]]:
    """Erreur de calibration : écart moyen, pondéré par l'effectif, entre probabilité moyenne et fréquence observée."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    k = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    table, total = [], 0.0
    for b in range(bins):
        sel = k == b
        if sel.any():
            gap = abs(p[sel].mean() - y[sel].mean())
            total += sel.mean() * gap
            table.append({"tranche": f"{edges[b]:.1f}–{edges[b + 1]:.1f}", "n": int(sel.sum()), "p_moyenne": round(float(p[sel].mean()), 4),
                          "frequence": round(float(y[sel].mean()), 4)})
    return float(total), table


def psi(train: np.ndarray, test: np.ndarray, bins: int = BINS) -> float:
    """Indice de stabilité de population ; tranches = déciles de l'apprentissage (valeurs absentes ignorées)."""
    train, test = train[np.isfinite(train)], test[np.isfinite(test)]
    if len(train) < bins or len(test) == 0:
        return float("nan")
    edges = np.unique(np.quantile(train, np.linspace(0, 1, bins + 1)[1:-1]))
    a = np.bincount(np.searchsorted(edges, train, side="right"), minlength=len(edges) + 1) / len(train)
    b = np.bincount(np.searchsorted(edges, test, side="right"), minlength=len(edges) + 1) / len(test)
    a, b = np.clip(a, 1e-4, None), np.clip(b, 1e-4, None)
    return float(np.sum((b - a) * np.log(b / a)))


def walk_forward(cands: pd.DataFrame, extra: tuple, tested: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    """Jugements hors échantillon mois par mois, avec les probabilités des deux modèles et la dérive du mois."""
    rows, drift = [], []
    for m in tested:
        train, test = training_set(cands, m)
        ai = PrincipalAI(extra=extra).fit(train)
        if not ai.ready or test.empty:
            continue
        X = matrix(test, extra)
        p_trees = ai.trees.predict_proba(ai._trees_input(X))[:, 1]
        p_lin = ai.linear.predict_proba((X.fillna(ai.median) - ai.mu) / ai.sd)[:, 1]
        verdict = ai.judge(test)
        rows.append(pd.DataFrame({"month": m, "y": (test["r"].to_numpy() > 0).astype(int), "r": test["r"].to_numpy(),
                                  "p": verdict["p"].to_numpy(), "p_raw": 0.5 * p_trees + 0.5 * p_lin, "p_trees": p_trees, "p_lin": p_lin,
                                  "base_rate": ai.base_rate, "take": verdict["take"].to_numpy(), "n_train": len(train)}))
        Xt = matrix(train, extra)
        cols = [c for c in Xt.columns if not c.startswith("is_")]
        values = {c: psi(Xt[c].to_numpy(float), X[c].to_numpy(float)) for c in cols}
        finite = [v for v in values.values() if np.isfinite(v)]
        drift.append({"month": m, "psi_moyen": float(np.mean(finite)), "psi_max": float(np.max(finite)),
                      "caracteristiques_en_derive": sorted(c for c, v in values.items() if np.isfinite(v) and v > PSI_ALERT)})
    return pd.concat(rows, ignore_index=True), drift


def month_bootstrap(stat, frame: pd.DataFrame, draws: int = DRAWS, seed: int = SEED) -> np.ndarray:
    """Distribution d'une statistique en rééchantillonnant les mois avec remise."""
    rng = np.random.default_rng(seed)
    groups = [g for _, g in frame.groupby("month")]
    return np.array([stat(pd.concat([groups[i] for i in rng.integers(0, len(groups), len(groups))], ignore_index=True)) for _ in range(draws)])


def skill(f: pd.DataFrame) -> float:
    return 1.0 - brier(f["p"], f["y"]) / brier(f["base_rate"], f["y"])


def abstention_gain(f: pd.DataFrame) -> float:
    taken = f[f["take"].astype(bool)]
    kept = taken[(taken["p_trees"] - taken["p_lin"]).abs() <= DISAGREEMENT]
    return float(kept["r"].mean() - taken["r"].mean()) if len(kept) and len(taken) else 0.0


def spearman(a, b) -> float:
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibration, incertitude et dérive de l'IA principale (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/calibration-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    variant = campaign.VARIANTS[VARIANT]
    store = Path(args.cache) / f"alerts-{variant.name}-{FIRST}-{LAST}.pkl"
    if store.exists():
        cands, _ = pickle.loads(store.read_bytes())  # alertes déjà construites par campaign.build sur les mêmes archives
    else:
        cands, closes = campaign.build(campaign.SYMBOLS, FIRST, LAST, args.cache, variant=variant)
        store.write_bytes(pickle.dumps((cands, closes)))
    tested = [m for m in months(FIRST, LAST) if len(training_set(cands, m)[0]) >= 300]
    frame, drift = walk_forward(cands, variant.extra, tested)
    taken = frame[frame["take"].astype(bool)]
    disagreement = (frame["p_trees"] - frame["p_lin"]).abs()
    monthly = pd.Series({m: brier(g["p"], g["y"]) - brier(g["base_rate"], g["y"]) for m, g in frame.groupby("month")})
    d = pd.DataFrame(drift).set_index("month").reindex(monthly.index)
    rho = spearman(d["psi_moyen"], monthly)
    rng = np.random.default_rng(SEED)
    perm = np.array([spearman(rng.permutation(d["psi_moyen"].to_numpy()), monthly.to_numpy()) for _ in range(DRAWS)])
    boot_skill, boot_gain = month_bootstrap(skill, frame), month_bootstrap(abstention_gain, frame)
    tests = [{"test": "C082 compétence de la probabilité calibrée sur le taux appris (> 0)", "valeur": skill(frame),
              "ic95": [float(np.percentile(boot_skill, 2.5)), float(np.percentile(boot_skill, 97.5))], "p_unilaterale": float((boot_skill <= 0).mean())},
             {"test": "C083 R moyen des trades gardés moins R moyen de tous les trades pris (> 0)", "valeur": abstention_gain(frame),
              "ic95": [float(np.percentile(boot_gain, 2.5)), float(np.percentile(boot_gain, 97.5))], "p_unilaterale": float((boot_gain <= 0).mean())},
             {"test": "C084 corrélation de rang entre dérive moyenne du mois et dégradation du score de Brier (> 0)", "valeur": rho,
              "p_unilaterale": float((perm >= rho).mean())}]
    for t, ph in zip(tests, essais.holm([t["p_unilaterale"] for t in tests])):
        t["p_holm"], t["significatif_holm_5pct"] = ph, bool(ph < 0.05)
    e_cal, table_cal = ece(frame["p"], frame["y"])
    e_raw, table_raw = ece(frame["p_raw"], frame["y"])
    out = {"schema": "scalp-calibration-v1", "status": "mesure hors échantillon mois par mois sur historique déjà consulté ; aucune intégration",
           "capacites": ["C082", "C083", "C084"], "variante": VARIANT, "mois_juges": [tested[0], tested[-1]], "alertes_jugees": int(len(frame)),
           "trades_pris": int(len(taken)), "taux_de_gain_observe": round(float(frame["y"].mean()), 4),
           "brier": {"calibree": brier(frame["p"], frame["y"]), "brute": brier(frame["p_raw"], frame["y"]), "taux_appris": brier(frame["base_rate"], frame["y"])},
           "ece": {"calibree": e_cal, "brute": e_raw}, "fiabilite_calibree": table_cal, "fiabilite_brute": table_raw,
           "desaccord": {"seuil": DISAGREEMENT, "mediane": float(disagreement.median()), "p90": float(disagreement.quantile(0.9)),
                         "part_des_trades_pris_au_dela": float(((taken["p_trees"] - taken["p_lin"]).abs() > DISAGREEMENT).mean()) if len(taken) else None,
                         "r_moyen_pris": float(taken["r"].mean()) if len(taken) else None},
           "derive": drift, "tests": tests, "seconds": round(time.time() - t0)}
    print({k: out[k] for k in ("mois_juges", "alertes_jugees", "trades_pris", "taux_de_gain_observe", "brier", "ece", "desaccord")})
    for t in tests:
        print(t)
    print("mois en dérive (PSI > 0,25 sur au moins une caractéristique) :", sum(bool(x["caracteristiques_en_derive"]) for x in drift), "sur", len(drift))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "calibration-v1",
                                 "tests": {t["test"]: {"valeur": t["valeur"], "p_holm": t["p_holm"]} for t in tests}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
