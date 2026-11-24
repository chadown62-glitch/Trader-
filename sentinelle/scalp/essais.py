"""Correction pour essais multiples (essais-multiples-v1) : ce qui reste démontré une fois comptés tous les essais.

Protocole consigné dans research/scalp-v1/trials.jsonl avant calcul (commit 49a4613) :
- familles de signaux (signaux-v1) : p-valeur unilatérale de l'écart, par rééchantillonnage par blocs centré, corrigée par
  Holm-Bonferroni (principale) et Benjamini-Hochberg (secondaire) sur les 11 familles ;
- ratio de Sharpe dégonflé (Bailey et López de Prado, 2014) sur les rendements hebdomadaires nets : probabilité que le
  ratio réel soit positif, compte tenu du nombre de réglages essayés (N) et de la dispersion de leurs résultats (V) ;
- appliqué au meilleur réglage sur la période de mise au point (N = 3 888), aux courbes hors échantillon (N = 4 variantes
  de sélection), aux stratégies de l'étude des signaux (N = 30) et à v10/v13 (N = 13 versions ; V estimée sur les 6
  versions recalculables en données journalières, v5, v7, v9, v10, v11, v13, écart consigné).
Simple contrôle statistique des résultats publiés : aucune stratégie, aucun réglage n'est modifié.

Lancement : python -m sentinelle.scalp.essais
"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import moteur, recherche, signaux

DAY_MS = moteur.DAY_MS
EULER = 0.5772156649015329
ALPHA = 0.05


# --- Outils statistiques ------------------------------------------------------------------------------------------------

def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_ppf(p: float) -> float:
    """Quantile de la loi normale (Acklam), précision ~1e-9, sans dépendance externe."""
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    if not 0 < p < 1:
        raise ValueError(p)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -norm_ppf(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def bootstrap_pvalue(x: np.ndarray, block: int = signaux.BOOT_BLOCK, draws: int = signaux.BOOT_DRAWS, seed: int = 0) -> float:
    """p-valeur unilatérale de « moyenne > 0 » : part des moyennes rééchantillonnées (par blocs circulaires, série
    centrée, donc sous l'hypothèse nulle) au moins égales à la moyenne observée."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(draws, -(-n // block)))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(draws, -1)[:, :n] % n
    centered = x - x.mean()
    means = centered[idx].mean(axis=1)
    return float((1 + (means >= x.mean()).sum()) / (draws + 1))


def holm(p: list[float]) -> list[float]:
    """p-valeurs ajustées de Holm-Bonferroni (contrôle du risque de se tromper au moins une fois)."""
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p[i]))
        adj[i] = running
    return adj.tolist()


def benjamini_hochberg(p: list[float]) -> list[float]:
    """p-valeurs ajustées de Benjamini-Hochberg (contrôle de la part de fausses découvertes)."""
    m = len(p)
    order = np.argsort(p)[::-1]
    adj = np.empty(m)
    running = 1.0
    for k, i in enumerate(order):
        rank = m - k
        running = min(running, p[i] * m / rank)
        adj[i] = running
    return adj.tolist()


def weekly(r: np.ndarray, days: np.ndarray) -> np.ndarray:
    """Rendements hebdomadaires (du lundi au dimanche UTC) à partir des rendements journaliers."""
    eq = np.cumprod(1 + np.asarray(r, dtype=float))
    week = (np.asarray(days) + 3) // 7
    last = np.r_[week[1:] != week[:-1], True]
    w = eq[last]
    return w / np.r_[1.0, w[:-1]] - 1


def sharpe(w: np.ndarray) -> dict:
    w = np.asarray(w, dtype=float)
    sd = w.std(ddof=1)
    sr = float(w.mean() / sd) if sd > 0 else 0.0
    z = (w - w.mean()) / sd if sd > 0 else np.zeros_like(w)
    return {"sr_week": sr, "sr_annual": sr * math.sqrt(52), "skew": float((z ** 3).mean()), "kurtosis": float((z ** 4).mean()), "weeks": int(len(w))}


def expected_max_sr(variance: float, n: int) -> float:
    """Meilleur ratio de Sharpe attendu par hasard parmi n essais sans avantage (dispersion V entre essais)."""
    if n <= 1 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * ((1 - EULER) * norm_ppf(1 - 1 / n) + EULER * norm_ppf(1 - 1 / (n * math.e)))


def deflated_sharpe(s: dict, variance: float, n: int) -> dict:
    """Probabilité que le ratio de Sharpe réel soit positif, une fois retiré le meilleur résultat attendu par hasard."""
    sr0 = expected_max_sr(variance, n)
    sr, t = s["sr_week"], s["weeks"]
    denom = 1 - s["skew"] * sr + (s["kurtosis"] - 1) / 4 * sr ** 2
    z = (sr - sr0) * math.sqrt(max(1, t - 1)) / math.sqrt(max(1e-12, denom))
    return {"dsr": norm_cdf(z), "sr_week": sr, "sr_annual": s["sr_annual"], "sr0_week": sr0, "n_trials": int(n),
            "variance_trials": float(variance), "weeks": t, "probable": bool(norm_cdf(z) >= 0.95)}


# --- Applications -------------------------------------------------------------------------------------------------------

def family_tests(p: dict) -> dict:
    member, r = signaux.members(p), signaux.asset_returns(p)
    fam = signaux.families(p)
    first = moteur._day(signaux.FIRST)
    raw = {}
    for name in signaux.FAMILIES:
        s, m, rr = fam[name].loc[first:], member.loc[first:], r.loc[first:]
        on = s.shift(1, fill_value=False) & m & rr.notna()
        univ = m & rr.notna()
        diff = (rr.where(on).mean(axis=1) - rr.where(univ).mean(axis=1)).dropna().to_numpy()
        raw[name] = bootstrap_pvalue(diff) if len(diff) > 2 * signaux.BOOT_BLOCK and np.abs(diff).sum() > 0 else 1.0
    names = list(raw)
    ph, pb = holm([raw[n] for n in names]), benjamini_hochberg([raw[n] for n in names])
    return {n: {"p_brute": raw[n], "p_holm": ph[i], "p_bh": pb[i], "significative_holm": ph[i] < ALPHA, "significative_bh": pb[i] < ALPHA}
            for i, n in enumerate(names)}


def signal_strategies(p: dict, prices: pd.DataFrame) -> dict:
    """Les 30 stratégies de signaux-v1 dont le résultat a été vu, en rendements journaliers nets (2018 → 2026)."""
    member, r = signaux.members(p), signaux.asset_returns(p)
    fam = signaux.families(p)
    first = moteur._day(signaux.FIRST)
    grid = p["c"].index
    out = {}
    for name in signaux.FAMILIES:
        out[f"{name} seule"] = signaux.net(fam[name], member, p, r, signaux.COST)[0].loc[first:]
        out[f"{name} filtre de F1"] = signaux.net(fam[name] & fam["F1_momentum"], member, p, r, signaux.COST)[0].loc[first:]
    rg = signaux.etf_returns(prices, grid).loc[first:]
    passed = ["F1_momentum", "F6_volume_pression", "F8_momentum_ajuste"]  # familles réussies de signaux-v1
    stack = np.stack([fam[n].to_numpy() for n in passed])
    for cname, arr in (("C_ET", stack.all(axis=0)), ("C_VOTE", stack.sum(axis=0) >= 2)):
        sig = pd.DataFrame(arr, index=grid, columns=p["c"].columns)
        for size, vt in (("1/10 par place", None), ("volatilité visée 30 %", 0.30)):
            rc = signaux.net(sig, member, p, r, signaux.COST, vt)[0].loc[first:]
            out[f"{cname}, {size}"] = rc
            out[f"{cname}, {size}, avec fonds 35/65"] = signaux.mix(rc, rg)
    return out


def dsr_table(series: dict, n: int | None = None) -> dict:
    stats = {k: sharpe(weekly(v.to_numpy() if hasattr(v, "to_numpy") else v,
                              v.index.to_numpy() if hasattr(v, "index") else None)) for k, v in series.items()}
    variance = float(np.var([s["sr_week"] for s in stats.values()], ddof=1)) if len(stats) > 1 else 0.0
    n = n or len(series)
    return {k: deflated_sharpe(s, variance, n) for k, s in stats.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Correction pour essais multiples (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/essais-multiples-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    out = {"schema": "scalp-essais-multiples-v1", "status": "contrôle statistique des résultats publiés ; protocole 49a4613"}
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
        prices = moteur.etf_prices(args.cache, signaux.LAST)
        p = signaux.panel(loaded)
        out["familles"] = family_tests(p)
        strategies = signal_strategies(p, prices)
        out["signaux_v1_N30"] = dsr_table(strategies, n=30)
        # Recherche v1 : grille de 3 888 réglages, période A, et courbes hors échantillon (univers 2026).
        symbols = list(dict.fromkeys(recherche.UNIVERSES["8 cryptos"] + recherche.UNIVERSES["BTC + ETH"]))
        tables, _ = moteur.crypto_tables(symbols, recherche.LAST, args.cache, client)
        configs, R, days, switch, _ = recherche.build(tables, prices)
        a = recherche.mask(days, "A")
        grid_stats = [sharpe(weekly(R[k][a], days[a])) for k in range(len(R))]
        variance_a = float(np.var([s["sr_week"] for s in grid_stats], ddof=1))
        best = recherche.choose([recherche.window_stats(R[k][a], days[a]) for k in range(len(R))], "R1")
        out["meilleur_sur_A_N3888"] = {"reglage": recherche.label(configs[best]["universe"], configs[best]["rules"], configs[best]["mix"], configs[best]["brake"]),
                                       "sur_A": deflated_sharpe(grid_stats[best], variance_a, len(R)),
                                       "sur_B_sans_correction": sharpe(weekly(R[best][recherche.mask(days, "B")], days[recherche.mask(days, "B")]))}
        oos = {}
        for rule in ("R1", "R2"):
            for window in ("toutes", "deux dernières"):
                res = recherche.walk_forward(R, days, rule, window, switch)
                oos[f"univers 2026, {rule}, fenêtre {window}"] = pd.Series(res["returns"], index=res["days"])
        keep = {}
        recherche.survie(args.cache, client, configs, R, days, switch, keep_series=keep)
        for k, res in keep["oos"].items():
            oos[f"univers à la date, {k}"] = pd.Series(res["returns"], index=res["days"])
    out["hors_echantillon_N4"] = {**dsr_table({k: v for k, v in oos.items() if k.startswith("univers 2026")}, n=4),
                                  **dsr_table({k: v for k, v in oos.items() if k.startswith("univers à la date")}, n=4)}
    # v5 à v13 recalculables (position de fond) sur la période A, 8 cryptos.
    base = recherche.V10.__class__(market_filter=None)
    versions = {"v5": base, "v7": replace(base, market_filter="BTCUSDT", trailing_stop=0.10),
                "v9": replace(base, market_filter="BTCUSDT", trailing_stop=0.10, keep_rank=5),
                "v10": recherche.V10, "v11": replace(recherche.V10, vol_target=0.5), "v13": recherche.V13}
    grid = pd.RangeIndex(moteur._day(recherche.WARMUP), moteur._day(recherche.LAST) + 1)
    vs = {}
    for name, rules in versions.items():
        rc = recherche.core_returns(tables, recherche.UNIVERSES["8 cryptos"], rules, grid)
        d = grid.to_numpy()
        m = (d >= moteur._day("2024-09-01")) & (d <= moteur._day("2026-08-31"))
        vs[name] = pd.Series(rc[m], index=d[m])
    out["versions_v5_v13_sur_A_N13"] = dsr_table(vs, n=13)
    out["ecart_consigne"] = "v1 à v4 et les mélanges v6, v8, v12 demandent les bougies d'une minute (plus en cache) : V estimée sur 6 versions, N = 13 conservé"
    out["seconds"] = round(time.time() - t0)
    for k, v in out["familles"].items():
        print(f"{k:26} p brute {v['p_brute']:.4f} | Holm {v['p_holm']:.4f} {'OUI' if v['significative_holm'] else 'non'} | BH {v['p_bh']:.4f} {'OUI' if v['significative_bh'] else 'non'}")
    for group in ("signaux_v1_N30", "hors_echantillon_N4", "versions_v5_v13_sur_A_N13"):
        for k, v in out[group].items():
            print(f"{group:26} {k:52} SR/an {v['sr_annual']:+.2f} | DSR {v['dsr']:.3f} {'probable' if v['probable'] else 'non démontré'}")
    b = out["meilleur_sur_A_N3888"]
    print("meilleur sur A :", b["reglage"], "| DSR", round(b["sur_A"]["dsr"], 3), "| SR/an sur A", round(b["sur_A"]["sr_annual"], 2),
          "| SR/an sur B", round(b["sur_B_sans_correction"]["sr_annual"], 2))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "essais-multiples-v1",
                                 "familles_significatives_holm": [k for k, v in out["familles"].items() if v["significative_holm"]],
                                 "familles_significatives_bh": [k for k, v in out["familles"].items() if v["significative_bh"]],
                                 "dsr_probables": [f"{g} : {k}" for g in ("signaux_v1_N30", "hors_echantillon_N4", "versions_v5_v13_sur_A_N13")
                                                   for k, v in out[g].items() if v["probable"]],
                                 "meilleur_sur_A_dsr": out["meilleur_sur_A_N3888"]["sur_A"]["dsr"]}, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
