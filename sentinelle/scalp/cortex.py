"""OMEGA priorité B — CORTEX (complémentarité des modèles) et ECHO (erreurs corrélées, biais communs, information partagée).

Les modèles se complètent-ils vraiment ? On le mesure sur le même échantillon que HIVE (hive.samples : dimanches, 42 cryptos,
membres à la date, cible « 7 jours suivants positifs »), avec le même walk-forward annuel 2020 → 2026 et le même embargo.
Distinct de C086 (redondance des entrées de l'IA principale, #164) et de C087 (ensemble arbres + linéaire de l'IA principale
sur alertes d'une minute) : ici, des familles de modèles différentes, et l'on mesure leurs erreurs, pas leurs entrées.
Modèles (tous appris sur l'échantillon collectif, sans réglage sur les années jugées) :
- LR : régression logistique (hive.Logit, C = 0,5) ;
- GB : arbres à gradient (profondeur 3, 100 itérations, pas 0,05, au moins 50 observations par feuille) ;
- MOM : règle calibrée, taux de semaines positives appris selon le signe du rendement sur 28 jours ;
- référence : taux appris ;
- ENS : moyenne des logits de LR, GB et MOM, poids égaux fixés a priori (aucun apprentissage de poids).
ECHO : corrélation deux à deux des erreurs brutes (y − p, dominées par l'issue commune) et des écarts à la référence
(p − p_base, ce que les modèles partagent au-delà du taux) ; apport marginal de chaque modèle sur l'erreur de la référence,
puis sachant LR ; biais de chaque modèle (moyenne des résidus) par année et corrélation de
ces biais entre modèles ; information partagée : R² de la régression du logit de l'un sur le logit de l'autre.
CORTEX : l'ensemble bat-il chaque modèle et la référence ? Perte logarithmique appariée, rééchantillonnage des semaines,
bilatéral, Holm sur 5 tests (ENS − LR, ENS − GB, ENS − MOM, ENS − référence, GB − LR).
Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.cortex
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from . import essais, hive, moteur, recherche, rejeu, signaux

MODELS = ("lr", "gb", "mom", "base", "ens")
GB_PARAMS = {"max_depth": 3, "max_iter": 100, "learning_rate": 0.05, "min_samples_leaf": 50, "random_state": 0}


class Momentum:
    """Taux de semaines positives appris séparément quand le rendement sur 28 jours est positif ou non."""

    def fit(self, r28: np.ndarray, y: np.ndarray) -> "Momentum":
        overall = float(np.clip(y.mean(), hive.EPS, 1 - hive.EPS)) if len(y) else 0.5
        self.rates = {}
        for k in (True, False):
            sel = (r28 > 0) == k
            self.rates[k] = float(np.clip(y[sel].mean(), hive.EPS, 1 - hive.EPS)) if sel.any() else overall
        return self

    def proba(self, r28: np.ndarray) -> np.ndarray:
        return np.where(r28 > 0, self.rates[True], self.rates[False])


def ensemble(*probas: np.ndarray) -> np.ndarray:
    """Moyenne des logits à poids égaux."""
    z = np.mean([hive.logit(p) for p in probas], axis=0)
    return 1 / (1 + np.exp(-z))


def judge_year(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    X, y = train[hive.FEATURES].to_numpy(), train["y"].to_numpy()
    Xt = test[hive.FEATURES].to_numpy()
    out = test[["day", "asset", "ret", "y"]].copy()
    lr = hive.Logit().fit(X, y)
    out["p_base"] = lr.rate
    out["p_lr"] = lr.proba(Xt)
    if len(np.unique(y)) == 2:
        out["p_gb"] = HistGradientBoostingClassifier(**GB_PARAMS).fit(X, y).predict_proba(Xt)[:, 1]
    else:
        out["p_gb"] = lr.rate
    k = hive.FEATURES.index("r28")
    out["p_mom"] = Momentum().fit(X[:, k], y).proba(Xt[:, k])
    out["p_ens"] = ensemble(out["p_lr"].to_numpy(), out["p_gb"].to_numpy(), out["p_mom"].to_numpy())
    return out


def residual_correlation(frame: pd.DataFrame, models=("lr", "gb", "mom")) -> dict:
    """Corrélations deux à deux (1) des erreurs brutes y − p, dominées par l'issue commune à tous les modèles quand y vaut
    0 ou 1, et (2) des écarts à la référence p − p_base, qui seuls mesurent ce que les modèles ont en commun au-delà du taux."""
    raw = pd.DataFrame({m: frame["y"] - frame[f"p_{m}"] for m in models}).corr()
    dev = pd.DataFrame({m: frame[f"p_{m}"] - frame["p_base"] for m in models}).corr()
    pairs = [(a, b) for i, a in enumerate(models) for b in models[i + 1:]]
    return {"erreurs_brutes": {f"{a}–{b}": float(raw.at[a, b]) for a, b in pairs},
            "ecarts_a_la_reference": {f"{a}–{b}": float(dev.at[a, b]) if np.isfinite(dev.at[a, b]) else 0.0 for a, b in pairs}}


def marginal_contribution(frame: pd.DataFrame, models=("lr", "gb", "mom")) -> dict:
    """Ce que chaque modèle explique de l'erreur de la référence (y − p_base) : corrélation de son écart à la référence avec
    elle, puis corrélation partielle une fois retiré ce qu'explique l'écart de LR."""
    target = (frame["y"] - frame["p_base"]).to_numpy()
    dev = {m: (frame[f"p_{m}"] - frame["p_base"]).to_numpy() for m in models}

    def corr(a, b):
        return float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else 0.0

    def residual(a, b):
        if np.std(b) == 0:
            return a - a.mean()
        beta = np.polyfit(b, a, 1)
        return a - np.polyval(beta, b)

    out = {m: {"correlation_avec_l_erreur_de_reference": corr(dev[m], target)} for m in models}
    for m in models:
        if m != "lr":
            out[m]["partielle_sachant_lr"] = corr(residual(dev[m], dev["lr"]), residual(target, dev["lr"]))
    return out


def shared_information(frame: pd.DataFrame, models=("lr", "gb", "mom")) -> dict:
    """R² de la régression linéaire du logit d'un modèle sur le logit d'un autre (symétrique : carré de la corrélation)."""
    z = pd.DataFrame({m: hive.logit(frame[f"p_{m}"].to_numpy()) for m in models})
    c = z.corr()
    return {f"{a}–{b}": float(c.at[a, b] ** 2) if np.isfinite(c.at[a, b]) else 0.0 for i, a in enumerate(models) for b in models[i + 1:]}


def biases(frame: pd.DataFrame, models=("lr", "gb", "mom", "base")) -> dict:
    """Biais (moyenne de y − p) par année et par modèle, et corrélation de ces biais annuels entre modèles."""
    year = hive.year_of(frame["day"])
    table = pd.DataFrame({m: (frame["y"] - frame[f"p_{m}"]).groupby(year).mean() for m in models})
    c = table.corr()
    return {"par_annee": {int(k): {m: float(v) for m, v in row.items()} for k, row in table.iterrows()},
            "correlation_des_biais": {f"{a}–{b}": float(c.at[a, b]) for i, a in enumerate(models) for b in models[i + 1:]}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA B — complémentarité des modèles et erreurs corrélées (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/omega-cortex-rejeu.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    rejeu.check_paths(args.out, args.trials)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    sources = rejeu.provenance("cortex", "99a1b4414cde7f9a9627a3df43420072daedc58a", loaded,
                              ("cortex", "hive", "signaux", "moteur", "recherche", "essais", "data", "flux"))
    p = signaux.panel(loaded)
    member = signaux.members(p)
    frame = hive.samples(p, member, hive.features(p), moteur._day(signaux.FIRST))
    judged = pd.concat([judge_year(*hive.split(frame, y)) for y in hive.TEST_YEARS], ignore_index=True)
    ll = {m: hive.logloss(judged[f"p_{m}"], judged["y"]) for m in MODELS}
    weeks = judged["day"].to_numpy()
    pairs = {"ensemble − LR": ("ens", "lr"), "ensemble − GB": ("ens", "gb"), "ensemble − MOM": ("ens", "mom"),
             "ensemble − référence": ("ens", "base"), "GB − LR": ("gb", "lr")}
    tests = {k: hive.week_bootstrap(ll[a] - ll[b], weeks) for k, (a, b) in pairs.items()}
    for t, ph in zip(tests.values(), essais.holm([t["p_bilaterale"] for t in tests.values()])):
        t["p_holm"], t["significatif_holm_5pct"] = ph, bool(ph < 0.05)
    out = {"schema": "scalp-omega-cortex-rejeu-v1", "provenance": sources, "status": "mesure hors échantillon (walk-forward annuel) sur historique déjà consulté ; aucune intégration",
           "omega": ["CORTEX", "ECHO"], "capacites": ["C087", "C083"], "distinct_de": {"C086": "#164 (redondance des entrées)", "C087": "ensemble de l'IA principale"},
           "parametres": {"gb": GB_PARAMS, "ensemble": "moyenne des logits LR, GB, MOM à poids égaux", "echantillon": "hive.samples", "annees": hive.TEST_YEARS},
           "predictions": int(len(judged)), "perte_log_moyenne": {m: float(v.mean()) for m, v in ll.items()},
           "correlation_des_residus": residual_correlation(judged), "information_partagee_r2": shared_information(judged),
           "apport_marginal": marginal_contribution(judged),
           "biais": biases(judged), "tests": tests, "seconds": round(time.time() - t0)}
    print({k: out[k] for k in ("predictions", "perte_log_moyenne", "correlation_des_residus", "information_partagee_r2")})
    print("apport marginal :", out["apport_marginal"])
    print("corrélation des biais annuels :", out["biais"]["correlation_des_biais"])
    for k, t in tests.items():
        print(f"{k:24} {t['ecart']:+.5f} IC [{t['ic95'][0]:+.5f} ; {t['ic95'][1]:+.5f}] p {t['p_bilaterale']:.3f} Holm {t['p_holm']:.3f}")
    result_sha = rejeu.write_result(args.out, out)
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-cortex-rejeu-v1", "resultat_sha256": result_sha,
                                 "perte_log_moyenne": out["perte_log_moyenne"], "correlation_des_residus": out["correlation_des_residus"],
                                 "tests": {k: {"ecart": t["ecart"], "p_holm": t["p_holm"]} for k, t in tests.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
