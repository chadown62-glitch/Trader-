"""Portefeuille global : poche crypto (système figé) et poche fonds indiciels, parts fixées avant mesure.

Les répartitions publiées (crypto / fonds) sont 20/80, 35/65 et 50/50, côte à côte, sans en retenir une après coup
(research/scalp-v1/trials.jsonl). Chaque poche évolue seule (pas de rééquilibrage entre poches), marquée chaque jour à
00:00 UTC ; les fonds, cotés en semaine seulement, gardent leur dernière clôture le week-end.
Simulation exclusive : aucun courtier, aucun ordre.

Lancement : python -m sentinelle.scalp.portefeuille
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import actifs, core, desk, rapide, reserve, systeme
from .data import RESERVED_TO

DAY_MS = core.DAY_MS
SPLITS = [(0.2, 0.8), (0.35, 0.65), (0.5, 0.5)]


def crypto_curve(cache_dir, start: int, end: int) -> list[tuple[int, float]]:
    """Équité journalière du système crypto figé (fond 70 % + rapide 30 %) sur toute la période, réservée comprise."""
    frozen, _ = reserve.check_frozen()
    tables = core.daily_table(reserve.load(frozen["core"]["universe"], frozen["core"]["data_from"], cache_dir))
    cands, closes = [], {}
    for s in rapide.UNIVERSE:
        m = reserve.load([s], frozen["fast"]["data_from"], cache_dir).get(s)
        if m is not None:
            closes[s] = core.daily_table({s: m})[1][s]
            cands.append(rapide.alerts(s, m, reserve.FAST_RULES))
    cands = pd.concat(cands, ignore_index=True)
    take = rapide.gate(cands, rapide.open_markets(pd.DataFrame(closes), reserve.FAST_RULES))
    fund = core.run(*tables[:2], start, end, 1000.0 * frozen["core"]["share"], reserve.CORE_RULES, lows=tables[2])
    fast = desk.run(cands, take, start, end, capital=1000.0 * frozen["fast"]["share"])
    return systeme._combine(fund, fast, 1000.0, start, end)["daily"]


def etf_curve(curve) -> list[tuple[int, float]]:
    """Clôture d'un jour de bourse datée à 00:00 UTC du lendemain (connue après la clôture américaine)."""
    return [(int((pd.Timestamp(d) + pd.Timedelta(days=1)).timestamp() * 1000), float(v)) for d, v in curve]


def on_days(curve, start: int, end: int, capital: float) -> pd.Series:
    s = pd.Series(dict(curve)).sort_index()
    grid = np.arange(start, end + 1, DAY_MS)
    return s.reindex(sorted(set(grid) | set(s.index))).ffill().reindex(grid).fillna(capital) / capital


def stats(daily: pd.Series, start: int, end: int) -> dict:
    curve = [(int(t), float(v) * 1000.0) for t, v in daily.items()]
    w = rapide.weekly_report({"curve": curve, "capital": 1000.0, "trades": []}, start, end)
    eq = daily.to_numpy()
    years = (end - start) / (365.25 * DAY_MS)
    return {"return_pct": 100 * (eq[-1] - 1), "annual_pct": 100 * (eq[-1] ** (1 / years) - 1),
            "max_drawdown_pct": float(100 * np.max(1 - eq / np.maximum.accumulate(eq))),
            "mean_week_pct": w["mean_week_pct"], "positive_weeks": w["positive_weeks"], "negative_weeks": w["negative_weeks"],
            "flat_weeks": w["flat_weeks"], "weeks": w["weeks"], "worst_week_pct": w["worst_week_pct"],
            "positive_months": w["positive_months"], "n_months": w["n_months"], "months": w["months"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Portefeuille global crypto + fonds indiciels (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--from", dest="first", default="2024-09-01")
    ap.add_argument("--to", dest="last", default="2026-11-20")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    start = int(pd.Timestamp(args.first).timestamp() * 1000)
    end = int((pd.Timestamp(args.last) + pd.Timedelta(days=1)).timestamp() * 1000)
    assert end <= int(pd.Timestamp(RESERVED_TO).timestamp() * 1000) + 1000
    prices = pd.DataFrame({t: actifs.download(t, args.cache, end_ts=int(time.time())) for t in actifs.UNIVERSE}).loc[:args.last]
    pockets = {"crypto": on_days(crypto_curve(args.cache, start, end), start, end, 1000.0)}
    for rule in ("G1", "G2"):
        pockets[rule] = on_days(etf_curve(actifs.run(prices, rule, args.first, args.last)["curve"]), start, end, 1000.0)
    rows = {name: stats(series, start, end) for name, series in pockets.items()}
    for rule in ("G1", "G2"):
        for c, f in SPLITS:
            rows[f"crypto {c:.0%} + {rule} {f:.0%}"] = stats(c * pockets["crypto"] + f * pockets[rule], start, end)
    for name, r in rows.items():
        print(f"{name:22} {r['return_pct']:+7.1f} % (par an {r['annual_pct']:+6.1f} %), pire baisse {r['max_drawdown_pct']:5.1f} %, "
              f"semaine moyenne {r['mean_week_pct']:+.2f} %, semaines + {r['positive_weeks']} − {r['negative_weeks']} 0 {r['flat_weeks']}, "
              f"pire semaine {r['worst_week_pct']:+.1f} %, mois + {r['positive_months']}/{r['n_months']}")
    report = {"schema": "scalp-portefeuille-v1", "from": args.first, "to": args.last, "splits": SPLITS, "rows": rows}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "portefeuille",
                                 "from": args.first, "to": args.last,
                                 "rows": {n: {k: r[k] for k in ("return_pct", "annual_pct", "max_drawdown_pct", "mean_week_pct",
                                                               "positive_weeks", "negative_weeks", "positive_months")}
                                          for n, r in rows.items()}}, ensure_ascii=False, default=float) + "\n")
    return report


if __name__ == "__main__":
    main()
