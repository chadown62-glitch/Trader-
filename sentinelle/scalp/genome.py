"""OMEGA priorité A — GENOME : profils statistiques par actif (registre OMEGA V3 : stabilité, effectif disponible et erreur
des profils par famille).

Quatre profils par crypto, calculés sur ses semaines cotées (disparues comprises, séries coupées aux recotations) :
- volatilité : écart type du rendement logarithmique hebdomadaire ;
- bêta : sensibilité au rendement hebdomadaire du BTC (BTC exclu) ;
- momentum : corrélation de rang entre le rendement des 28 jours passés et celui de la semaine suivante ;
- dérive : rendement hebdomadaire moyen.
Mesures :
- effectif : semaines cotées par actif ;
- fiabilité entre actifs : 1 − (moyenne des variances d'échantillonnage) / (variance des profils entre actifs), avec des
  erreurs types par rééchantillonnage des semaines ;
- stabilité : corrélation de rang, entre actifs, des profils de la première et de la seconde moitié de chaque historique
  (test unilatéral par permutation, correction de Holm sur les quatre profils) ;
- erreur par famille : prévision du profil de seconde moitié par le profil propre de première moitié, par la moyenne de
  la famille (actif exclu) ou par la moyenne générale (actif exclu) ; erreur quadratique moyenne.
Familles fixées a priori (classement public usuel, avant tout calcul) : paiement, plateformes, infrastructure et
services, memecoins. Mesure seulement, sur un historique déjà consulté.

Lancement : python -m sentinelle.scalp.genome
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

from . import essais, moteur, recherche, signaux

FAMILIES = {
    "paiement": "BTC LTC BCH XRP XLM ZEC DASH XMR ETC",
    "plateformes": "ETH ADA SOL DOT EOS TRX NEO AVAX ATOM NEAR ICP ALGO XTZ WAVES ONT QTUM SUI APT TON LUNA",
    "infrastructure_et_services": "BNB LINK IOTA FTT MATIC UNI FIL VET THETA OMG",
    "memecoins": "DOGE SHIB PEPE",
}
FAMILY = {f"{c}USDT": fam for fam, names in FAMILIES.items() for c in names.split()}
PROFILES = ("volatilite", "beta", "momentum", "derive")
MIN_WEEKS, MIN_HALF, BOOT, PERM, SEED = 52, 52, 500, 5000, 20261129
ANCHOR = "BTCUSDT"


def weekly_table(c: pd.DataFrame) -> dict:
    """Rendements hebdomadaires, rendement des 28 jours passés et rendement de la semaine suivante (grille d'un jour sur sept)."""
    week = c.iloc[::7]
    r = np.log(week / week.shift(1))
    return {"r": r, "r28": np.log(week / week.shift(4)), "next": r.shift(-1)}


def profile(r: pd.Series, r28: pd.Series, nxt: pd.Series, btc: pd.Series | None) -> dict:
    out = {"volatilite": float(r.std(ddof=1)), "derive": float(r.mean())}
    ok = r28.notna() & nxt.notna()
    out["momentum"] = float(r28[ok].rank().corr(nxt[ok].rank())) if ok.sum() >= 10 else float("nan")
    if btc is not None:
        both = r.notna() & btc.notna()
        out["beta"] = float(np.cov(r[both], btc[both])[0, 1] / btc[both].var(ddof=1)) if both.sum() >= 10 else float("nan")
    else:
        out["beta"] = float("nan")
    return out


def asset_rows(t: dict, symbol: str) -> pd.DataFrame:
    btc = t["r"][ANCHOR] if symbol != ANCHOR else None
    frame = pd.DataFrame({"r": t["r"][symbol], "r28": t["r28"][symbol], "next": t["next"][symbol]})
    if btc is not None:
        frame["btc"] = btc
    return frame[frame["r"].notna()]


def of_frame(f: pd.DataFrame) -> dict:
    return profile(f["r"], f["r28"], f["next"], f["btc"] if "btc" in f else None)


def bootstrap_se(f: pd.DataFrame, reps: int, rng: np.random.Generator) -> dict:
    draws = [of_frame(f.iloc[rng.integers(0, len(f), len(f))].reset_index(drop=True)) for _ in range(reps)]
    out = {}
    for k in PROFILES:
        v = np.array([d[k] for d in draws], dtype=float)
        v = v[np.isfinite(v)]
        out[k] = float(v.std(ddof=1)) if len(v) >= 2 else float("nan")
    return out


def reliability(est: pd.DataFrame, se: pd.DataFrame) -> dict:
    out = {}
    for k in PROFILES:
        e, s = est[k].dropna(), se[k].reindex(est[k].dropna().index)
        between = e.var(ddof=1)
        out[k] = {"actifs": int(len(e)), "variance_entre_actifs": float(between), "variance_d_echantillonnage_moyenne": float((s**2).mean()),
                  "fiabilite": float(1 - (s**2).mean() / between) if between > 0 else float("nan")}
    return out


def stability(first: pd.DataFrame, second: pd.DataFrame, perms: int, rng: np.random.Generator) -> dict:
    out = {}
    for k in PROFILES:
        both = first[k].notna() & second[k].notna()
        a, b = first.loc[both, k].rank().to_numpy(), second.loc[both, k].rank().to_numpy()
        rho = float(np.corrcoef(a, b)[0, 1])
        null = np.array([np.corrcoef(a, rng.permutation(b))[0, 1] for _ in range(perms)])
        out[k] = {"actifs": int(both.sum()), "rang_spearman": rho, "p_unilaterale": float((1 + np.sum(null >= rho)) / (1 + perms))}
    holm = essais.holm([out[k]["p_unilaterale"] for k in PROFILES])
    for k, p in zip(PROFILES, holm):
        out[k]["p_holm"] = float(p)
        out[k]["stable_holm_5pct"] = bool(p < 0.05)
    return out


def family_error(first: pd.DataFrame, second: pd.DataFrame) -> dict:
    """Erreur quadratique de prévision du profil de seconde moitié : propre, famille (actif exclu), générale (actif exclu)."""
    out = {}
    for k in PROFILES:
        both = first[k].notna() & second[k].notna()
        f, s = first.loc[both, k], second.loc[both, k]
        fam = pd.Series({a: FAMILY.get(a, "autre") for a in f.index})
        own, family, overall = [], [], []
        for a in f.index:
            peers = f[(fam == fam[a]) & (f.index != a)]
            others = f[f.index != a]
            own.append((f[a] - s[a]) ** 2)
            family.append(((peers.mean() if len(peers) else others.mean()) - s[a]) ** 2)
            overall.append((others.mean() - s[a]) ** 2)
        out[k] = {"actifs": int(both.sum()), "eqm_propre": float(np.mean(own)), "eqm_famille": float(np.mean(family)),
                  "eqm_generale": float(np.mean(overall)), "meilleure": ("propre", "famille", "generale")[int(np.argmin([np.mean(own), np.mean(family), np.mean(overall)]))]}
    return out


def study(c: pd.DataFrame, boot: int = BOOT, perms: int = PERM, seed: int = SEED) -> dict:
    t = weekly_table(c)
    rng = np.random.default_rng(seed)
    est, se, first, second, weeks = {}, {}, {}, {}, {}
    for s in c.columns:
        f = asset_rows(t, s)
        weeks[s] = int(len(f))
        if len(f) < MIN_WEEKS:
            continue
        est[s], se[s] = of_frame(f), bootstrap_se(f, boot, rng)
        if len(f) >= 2 * MIN_HALF:
            half = len(f) // 2
            first[s], second[s] = of_frame(f.iloc[:half]), of_frame(f.iloc[half:])
    est, se = pd.DataFrame(est).T, pd.DataFrame(se).T
    first, second = pd.DataFrame(first).T, pd.DataFrame(second).T
    fams = {}
    for fam in FAMILIES:
        idx = [a for a in est.index if FAMILY.get(a) == fam]
        if len(idx) >= 3:
            fams[fam] = {"actifs": idx, "fiabilite": reliability(est.loc[idx], se.loc[idx]),
                         "moyennes": {k: float(est.loc[idx, k].mean()) for k in PROFILES}}
    w = pd.Series(weeks)
    return {"effectif": {"semaines_par_actif": weeks, "mediane": float(w.median()), "minimum": int(w.min()),
                         "actifs_au_moins_52_semaines": int((w >= MIN_WEEKS).sum()), "actifs_scindes_en_deux_moities": int(len(first))},
            "profils": {a: {k: (None if not np.isfinite(v) else float(v)) for k, v in row.items()} for a, row in est.to_dict("index").items()},
            "fiabilite": reliability(est, se), "par_famille": fams, "stabilite": stability(first, second, perms, rng),
            "erreur_par_famille": family_error(first, second)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA GENOME — profils statistiques par actif (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/omega-genome-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    p = signaux.panel(loaded)
    res = study(p["c"].loc[moteur._day(signaux.FIRST):])
    out = {"schema": "scalp-omega-genome-v1", "status": "mesure descriptive et tests de stabilité sur historique déjà consulté ; aucune intégration",
           "omega": ["GENOME"], "capacites": ["C026", "C085"], "familles": FAMILIES,
           "parametres": {"semaines_minimales": MIN_WEEKS, "moitie_minimale": MIN_HALF, "reechantillonnages": BOOT, "permutations": PERM, "graine": SEED},
           **res, "seconds": round(time.time() - t0)}
    print("effectif :", res["effectif"]["mediane"], res["effectif"]["actifs_au_moins_52_semaines"], res["effectif"]["actifs_scindes_en_deux_moities"])
    for k in PROFILES:
        print(k, {"fiabilite": round(res["fiabilite"][k]["fiabilite"], 3), **{x: round(v, 4) if isinstance(v, float) else v for x, v in res["stabilite"][k].items()}},
              {x: (round(v, 6) if isinstance(v, float) else v) for x, v in res["erreur_par_famille"][k].items()})
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-genome-v1",
                                 "fiabilite": {k: res["fiabilite"][k]["fiabilite"] for k in PROFILES},
                                 "stabilite": {k: {x: res["stabilite"][k][x] for x in ("rang_spearman", "p_holm", "stable_holm_5pct")} for k in PROFILES},
                                 "erreur_par_famille": {k: res["erreur_par_famille"][k]["meilleure"] for k in PROFILES}}, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
