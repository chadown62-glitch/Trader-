"""Système complet : position de fond sur 20 marchés et moteur rapide, choisis par une règle fixée avant calcul.

Règle (délégation du propriétaire, consignée dans research/scalp-v1/trials.jsonl avant tout calcul) : parmi S1 à S5,
retenir la combinaison de plus forte moyenne hebdomadaire dont la pire baisse, de septembre 2024 à août 2026, est
d'au plus 20 % et dont les deux années sont positives.
- S1, S2, S3 : position de fond aux règles de v13 (filtre de marché BTC, stop suiveur de 10 %, maintien parmi les 5
  plus fortes, moyenne de 50 jours), étendue aux 20 marchés, volatilité visée de 30 %, 40 % ou 50 % par place ;
- S4, S5 : la meilleure de S1 à S3 respectant le plafond, avec 70 % puis 50 % du capital, le reste au moteur rapide
  (plan B : cassures, stop suiveur).
Simulation exclusive ; aucun levier : les deux poches se partagent le capital sans jamais le dépasser.

Lancement : python -m sentinelle.scalp.systeme
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from . import core, desk, rapide
from .campaign import _combine, _listed_from
from .data import load_bars, month_bounds

BASE = core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10, keep_rank=5, trend_days=50, vol_target=0.3)
CORES = {"S1": replace(BASE, vol_target=0.3), "S2": replace(BASE, vol_target=0.4), "S3": replace(BASE, vol_target=0.5)}
DD_CAP = 20.0


def daily_tables(cache_dir, first: str, last: str, universe=rapide.UNIVERSE):
    opens, closes, lows = {}, {}, {}
    for s in universe:
        start = _listed_from(s, first, last, cache_dir, None)
        if start is None:
            continue
        minutes, _ = load_bars(s, start, last, cache_dir)
        o, c, lo = core.daily_table({s: minutes})
        opens[s], closes[s], lows[s] = o[s], c[s], lo[s]
    return pd.DataFrame(opens), pd.DataFrame(closes), pd.DataFrame(lows)


def evaluate(tables, fast_cands, fast_take, rules, core_share, start, end, capital=1000.0) -> dict:
    """Équité marquée chaque jour, rendements par semaine, par mois et par année, pire baisse."""
    opens, closes, lows = tables
    fund = core.run(opens, closes, start, end, capital * core_share, rules, lows=lows) if core_share > 0 else None
    fast = desk.run(fast_cands, fast_take, start, end, capital=capital * (1 - core_share)) if core_share < 1 else None
    system = _combine(fund, fast, capital, start, end)
    daily = system.pop("daily")
    weekly = rapide.weekly_report({"curve": daily, "capital": capital, "trades": []}, start, end)
    years = {}
    for a, b in (("2024-09", "2025-08"), ("2025-09", "2026-08")):
        s, e = month_bounds(a)[0], month_bounds(b)[1] + 1
        if s >= start and e <= end:
            years[f"{a} → {b}"] = rapide.periods(daily, capital, s, e, None)[0]["pct"]
    return {"return_pct": system["return_pct"], "max_drawdown_pct": system["max_drawdown_pct"], "years": years,
            "mean_week_pct": weekly["mean_week_pct"], "median_week_pct": weekly["median_week_pct"],
            "positive_weeks": weekly["positive_weeks"], "negative_weeks": weekly["negative_weeks"],
            "flat_weeks": weekly["flat_weeks"], "weeks": weekly["weeks"], "worst_week_pct": weekly["worst_week_pct"],
            "best_week_pct": weekly["best_week_pct"], "positive_months": weekly["positive_months"],
            "n_months": weekly["n_months"], "months": weekly["months"],
            "core_orders": system["core_orders"], "fast_trades": system["tactical_trades"]}


def admissible(r: dict) -> bool:
    return r["max_drawdown_pct"] <= DD_CAP and all(v > 0 for v in r["years"].values())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Système complet : choix par règle fixée avant calcul (simulation exclusive)")
    ap.add_argument("--from", dest="first_month", default="2024-09")
    ap.add_argument("--to", dest="last_month", default="2026-08")
    ap.add_argument("--data-from", default="2024-01")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    start, end = month_bounds(args.first_month)[0], month_bounds(args.last_month)[1] + 1
    store = Path(args.cache) / f"systeme-tables-{args.data_from}-{args.last_month}.pkl"
    if store.exists():
        tables = pd.read_pickle(store)
    else:
        tables = daily_tables(args.cache, args.data_from, args.last_month)
        pd.to_pickle(tables, store)
    fast_store = Path(args.cache) / f"rapide-suiveur-{args.data_from}-{args.last_month}.pkl"
    if fast_store.exists():
        fast_cands, _, fast_closes = pd.read_pickle(fast_store)
    else:
        fast_cands, _, fast_closes = rapide.build(args.cache, args.data_from, args.last_month, rules=rapide.SUIVEUSE)
        pd.to_pickle((fast_cands, _, fast_closes), fast_store)
    fast_take = rapide.gate(fast_cands, rapide.open_markets(fast_closes))
    results = {}
    for name, rules in CORES.items():
        results[name] = {"rules": asdict(rules), "core_share": 1.0,
                         **evaluate(tables, fast_cands, fast_take, rules, 1.0, start, end)}
    ok = [n for n in CORES if admissible(results[n])]
    best_core = max(ok, key=lambda n: results[n]["mean_week_pct"]) if ok else None
    if best_core:
        for name, share in (("S4", 0.7), ("S5", 0.5)):
            results[name] = {"rules": asdict(CORES[best_core]), "core_share": share, "core_from": best_core,
                             **evaluate(tables, fast_cands, fast_take, CORES[best_core], share, start, end)}
    chosen = [n for n in results if admissible(results[n])]
    winner = max(chosen, key=lambda n: results[n]["mean_week_pct"]) if chosen else None
    for n, r in results.items():
        ys = " | ".join(f"{k} {v:+.1f} %" for k, v in r["years"].items())
        print(f"{n} (fond {r['core_share']:.0%}) : {r['return_pct']:+.1f} %, baisse max {r['max_drawdown_pct']:.1f} %, "
              f"semaine moyenne {r['mean_week_pct']:+.2f} %, semaines + {r['positive_weeks']} − {r['negative_weeks']} "
              f"0 {r['flat_weeks']}, mois + {r['positive_months']}/{r['n_months']} | {ys}{'  ← retenu' if n == winner else ''}")
    report = {"schema": "scalp-systeme-v1", "from": args.first_month, "to": args.last_month, "dd_cap_pct": DD_CAP,
              "universe": sorted(tables[1].columns.tolist()), "results": results, "winner": winner}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "systeme",
                                 "winner": winner, "results": {n: {k: r[k] for k in ("core_share", "return_pct", "max_drawdown_pct",
                                                                                   "mean_week_pct", "positive_weeks", "negative_weeks",
                                                                                   "flat_weeks", "positive_months", "years")}
                                                              for n, r in results.items()}}, ensure_ascii=False, default=float) + "\n")
    return report


if __name__ == "__main__":
    main()
