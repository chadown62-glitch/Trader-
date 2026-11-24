"""Ablations et stress de coûts (C095, C097) : chaque capacité se juge avec et sans elle, à conditions identiques.

- Ablation (C095) : deux séries de rendements journaliers nets, de même calendrier (mêmes jours), même capital de départ
  et mêmes coûts déclarés ; on compare leurs rendements hebdomadaires appariés. L'écart moyen hebdomadaire reçoit un
  intervalle à 95 % par rééchantillonnage par blocs circulaires de 4 semaines (2 000 tirages, graine 0) et une p-valeur
  unilatérale ; les ablations d'un même lot sont corrigées par Holm-Bonferroni (essais multiples, C094).
- Stress (C097) : la même stratégie rejouée avec les frais et le glissement multipliés (× 1, × 2, × 3) et un écart
  supplémentaire ; on publie la stabilité à chaque niveau et si la stratégie bat encore CASH et sa référence.
Mesure seulement : rien n'entre dans une décision ; aucun réglage n'est choisi sur ces résultats.

Lancement : python -m sentinelle.scalp.ablation
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

BLOCK_WEEKS, DRAWS = 4, 2000
COST_LEVELS = {"× 1": 1.0, "× 2": 2.0, "× 3": 3.0}
EXTRA_SPREAD = 0.0010  # écart supplémentaire par côté du dernier niveau de stress


class AblationError(ValueError):
    """Comparaison invalide : calendriers différents, séries vides ou incohérentes."""


def weekly_pairs(base: pd.Series, variant: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Rendements hebdomadaires appariés (même calendrier exigé, sinon la comparaison est refusée)."""
    if len(base) == 0 or not base.index.equals(variant.index):
        raise AblationError("les deux séries doivent couvrir exactement les mêmes jours")
    if not (np.isfinite(base.to_numpy()).all() and np.isfinite(variant.to_numpy()).all()):
        raise AblationError("rendement non fini")
    days = base.index.to_numpy()
    return essais.weekly(base.to_numpy(), days), essais.weekly(variant.to_numpy(), days)


def block_ci(diff: np.ndarray, block: int = BLOCK_WEEKS, draws: int = DRAWS, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(diff)
    starts = rng.integers(0, n, size=(draws, -(-n // block)))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(draws, -1)[:, :n] % n
    means = diff[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def compare(base: pd.Series, variant: pd.Series, *, label: str, costs: str, btc_close: pd.Series | None = None) -> dict:
    """Variante (avec la capacité) contre base (sans elle) : écart hebdomadaire moyen, intervalle, p-valeur, stabilités."""
    wb, wv = weekly_pairs(base, variant)
    diff = wv - wb
    lo, hi = block_ci(diff)
    p = essais.bootstrap_pvalue(diff, block=BLOCK_WEEKS, draws=DRAWS) if np.abs(diff).sum() > 0 else 1.0
    sb, sv = signaux.stability(base, btc_close), signaux.stability(variant, btc_close)
    keys = ("geometric_week_pct", "annual_pct", "max_drawdown_pct", "positive_weeks", "worst_week_pct", "longest_losing_streak_weeks")
    return {"ablation": label, "couts": costs, "jours": int(len(base)), "semaines": int(len(diff)),
            "periode": [str(pd.to_datetime(int(base.index[0]) * moteur.DAY_MS, unit="ms").date()),
                        str(pd.to_datetime(int(base.index[-1]) * moteur.DAY_MS, unit="ms").date())],
            "ecart_hebdo_moyen_pct": float(100 * diff.mean()), "ic95_pct": [100 * lo, 100 * hi], "p_unilaterale": float(p),
            "sans": {k: sb[k] for k in keys}, "avec": {k: sv[k] for k in keys},
            "ecart_pire_baisse_pts": float(sv["max_drawdown_pct"] - sb["max_drawdown_pct"])}


def adjust(rows: list[dict]) -> list[dict]:
    """Correction de Holm sur un lot d'ablations ; « significative » seulement après correction."""
    ph = essais.holm([r["p_unilaterale"] for r in rows])
    for r, p in zip(rows, ph):
        r["p_holm"] = p
        r["significative_holm_5pct"] = bool(p < 0.05)
    return rows


def stress(run, *, base_cost: float, reference: pd.Series, btc_close: pd.Series | None = None) -> dict:
    """« run(coût par côté) » rejoue la stratégie ; niveaux × 1, × 2, × 3 et × 3 + écart ; comparés à CASH et à la référence."""
    out = {}
    levels = {**{k: base_cost * m for k, m in COST_LEVELS.items()}, "× 3 + écart 0,10 %": 3 * base_cost + EXTRA_SPREAD}
    ref = signaux.stability(reference, btc_close)
    for name, cost in levels.items():
        s = signaux.stability(run(cost), btc_close)
        out[name] = {"cout_par_cote": cost, "geometric_week_pct": s["geometric_week_pct"], "annual_pct": s["annual_pct"],
                     "max_drawdown_pct": s["max_drawdown_pct"], "positive_weeks": s["positive_weeks"],
                     "bat_cash": bool(s["geometric_week_pct"] > 0), "bat_reference": bool(s["geometric_week_pct"] > ref["geometric_week_pct"])}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ablations et stress de coûts (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/ablation-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    prices = moteur.etf_prices(args.cache, signaux.LAST)
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    btc = p["c"]["BTCUSDT"]
    sl = lambda x: x.loc[first:]  # noqa: E731
    grid = p["c"].index
    rg = sl(signaux.etf_returns(prices, grid))
    cost = signaux.COST
    passed = ["F1_momentum", "F6_volume_pression", "F8_momentum_ajuste"]
    vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in passed]).sum(axis=0) >= 2, index=grid, columns=p["c"].columns)
    b1_novol = sl(signaux.net(vote, member, p, r, cost)[0])
    b1_vol = sl(signaux.net(vote, member, p, r, cost, 0.30)[0])
    b1 = signaux.mix(b1_vol, rg)
    f1 = sl(signaux.net(fam["F1_momentum"], member, p, r, cost)[0])
    rows = [compare(signaux.mix(b1_novol, rg), b1, label="C076 volatilité visée 30 % (B1 avec contre sans)", costs="0,125 %/côté ; fonds 0,10 %", btc_close=btc),
            compare(b1_vol, b1, label="mélange avec les fonds 35/65 (B1 avec contre crypto seule)", costs="0,125 %/côté ; fonds 0,10 %", btc_close=btc),
            compare(b1, pd.Series(recherche.brake_returns(b1.to_numpy(), 0.15, 0.5 * (0.35 * cost + 0.65 * 0.001)), index=b1.index),
                    label="C077 frein de baisse 15 % (B1 avec contre sans)", costs="0,125 %/côté ; fonds 0,10 % ; changement d'exposition payé", btc_close=btc)]
    for name in signaux.FAMILIES:
        if name == "F1_momentum":
            continue
        filt = sl(signaux.net(fam[name] & fam["F1_momentum"], member, p, r, cost)[0])
        rows.append(compare(f1, filt, label=f"{name} en filtre de B0 (momentum simple)", costs="0,125 %/côté", btc_close=btc))
    rows = adjust(rows)
    out = {"schema": "scalp-ablation-v1", "status": "mesure sur historique déjà consulté ; protocole préenregistré ; aucune intégration à la décision",
           "capacites": ["C095", "C097", "C076", "C077"], "ablations": rows,
           "stress_C097": {
               "B0 momentum simple": stress(lambda c: sl(signaux.net(fam["F1_momentum"], member, p, r, c)[0]), base_cost=cost, reference=sl(signaux.equal_weight_monthly(member, r, cost)), btc_close=btc),
               "B1 vote, volatilité visée, avec fonds": stress(lambda c: signaux.mix(sl(signaux.net(vote, member, p, r, c, 0.30)[0]), rg), base_cost=cost,
                                                              reference=sl(signaux.etf_returns(prices, grid)), btc_close=btc)},
           "references_du_stress": {"B0": "univers à poids égaux (rééquilibré chaque mois)", "B1": "fonds G2 seuls"},
           "seconds": round(time.time() - t0)}
    for x in rows:
        print(f"{x['ablation'][:58]:58} écart {x['ecart_hebdo_moyen_pct']:+.3f} %/sem IC [{x['ic95_pct'][0]:+.3f} ; {x['ic95_pct'][1]:+.3f}] "
              f"p {x['p_unilaterale']:.3f} Holm {x['p_holm']:.3f} | baisse {x['sans']['max_drawdown_pct']:.0f} → {x['avec']['max_drawdown_pct']:.0f} %")
    for k, v in out["stress_C097"].items():
        print(k, {n: (round(s["geometric_week_pct"], 3), round(s["max_drawdown_pct"], 1), s["bat_cash"], s["bat_reference"]) for n, s in v.items()})
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "ablation-v1",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"]} for x in rows},
                                 "stress": {k: {n: s["geometric_week_pct"] for n, s in v.items()} for k, v in out["stress_C097"].items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
