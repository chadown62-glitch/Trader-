"""Banc « Fast Learner » : coût de calcul et courbes d'apprentissage (exploratoire, hors ligne, exécution finie).

    python -m tools.alpha_shadow bench --output DOSSIER [--archive DOSSIER] [--repeats 7] [--quick]

Marchés synthétiques à graines jamais utilisées auparavant (7001 à 7003) ; avec ``--archive``, le même
banc sur l'historique public déjà téléchargé. Toutes les mesures portent sur des données déjà vues ou
simulées : critère secondaire, jamais une preuve de rendement.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from sentinelle.alpha import fastlearner
from sentinelle.alpha.bars import Bars, Universe
from sentinelle.alpha.markets import synthetic_universe

SEEDS = (7001, 7002, 7003)  # jamais exécutées avant cette mesure
MARKETS = (("regimes", 1.0), ("hidden_rule", 1.0), ("noise", 0.0))
BUDGETS = (25, 50, 100, 200, 400, 800, None)


def bars_only(universe: Universe) -> Universe:
    """Même univers sans écart observé : les décisions ne dépendent que des bougies (comme le moteur shadow)."""
    return Universe({s: Bars(b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume, None, b.tf_ms, b.provenance)
                     for s, b in universe.bars.items()})


def run_bench(a) -> int:
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "bench.json").exists():
        print("Un résultat existe déjà dans ce dossier : utilisez un nouveau dossier.")
        return 2
    t0 = time.time()
    seeds, markets, budgets, n_bars = (SEEDS[:1], MARKETS[:1], (50, None), 2500) if a.quick else (SEEDS, MARKETS, BUDGETS, 6000)
    plan = {"kind": "alpha-fastlearner-bench-plan", "seeds": list(seeds), "markets": [list(m) for m in markets],
            "budgets": ["tout" if b is None else b for b in budgets], "n_bars": n_bars, "repeats": a.repeats,
            "contenders": list(fastlearner.CONTENDERS), "status": "EXPLORATORY", "registered_at": time.time()}
    (out / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")  # écrit avant toute mesure
    results = []
    for kind, edge in markets:
        for seed in seeds:
            universe = bars_only(synthetic_universe(seed, kind=kind, edge=edge, n_bars=n_bars))
            results.append(fastlearner.bench_market(f"{kind}@{edge}#{seed}", universe, budgets=budgets, repeats=a.repeats))
    if a.archive:
        from tools.alpha_shadow import load_universe

        universe, prov = load_universe(a.archive)
        real = fastlearner.bench_market("public-eur-4h (développement, déjà étudié)", universe, budgets=budgets, repeats=a.repeats)
        real["inputs"] = prov
        results.append(real)
    report = {"kind": "alpha-fastlearner-bench", "plan": plan, "platform": fastlearner.platform_info(), "markets": results,
              "summary": fastlearner.summarize(results), "duration_s": round(time.time() - t0, 1)}
    (out / "bench.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    print(json.dumps({"duration_s": report["duration_s"], "summary": report["summary"]}, ensure_ascii=False, indent=1))
    return 0
