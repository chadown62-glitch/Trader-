"""Campagne d'essai du moteur court terme : veilleurs -> IA principale -> pupitre de risque, sur un mois puis sur un an.

Essai sur un mois : l'IA apprend sur les alertes dont l'issue était connue avant le mois (embargo d'un jour), puis le
mois est rejoué minute par minute. Stabilisation sur un an : la même chose, mois après mois, chaque mois appris
uniquement sur son passé. Chaque essai est ajouté au registre (essais négatifs compris).
Simulation exclusive ; la période réservée ne se charge pas (data.guard).

Lancement : python -m sentinelle.scalp.campaign --month 2026-08
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import core, desk
from .brain import PrincipalAI
from .data import download_month, load_bars, month_bounds, months
from .features import DAILY, compute, five_minute_bars
from .trade import INVALID, manage
from .watchers import ROUND_TRIP, alerts

SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "DOGEUSDC", "ADAUSDC", "LINKUSDC"]
EMBARGO_MS = 86_400_000


@dataclass(frozen=True)
class Variant:
    """Version du moteur essayée. Chaque amélioration est décidée avant d'être mesurée, puis consignée."""
    name: str
    round_trip: float = ROUND_TRIP
    daily_trend: bool = False
    extra: tuple = ()
    core_share: float = 0.0  # part du capital confiée à la position de fond (core.py), le reste au trading court
    core_rules: core.CoreRules = core.CoreRules()
    note: str = ""


VARIANTS = {
    "v1": Variant("v1", note="4 veilleurs, IA mixte, marge 0,10 R, profil prudent, frais standard 0,1 %"),
    "v2": Variant("v2", round_trip=2 * (0.00075 + 0.0001), note="v1 + frais payés en BNB (0,075 % par côté)"),
    "v3": Variant("v3", daily_trend=True, extra=tuple(DAILY), note="v1 + tendance de fond journalière exigée et apprise"),
    "v4": Variant("v4", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), note="v2 + v3"),
    "v5": Variant("v5", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=1.0,
                  note="position de fond seule : double élan journalier (20 jours, 3 meneuses sur 14 jours), frais BNB"),
    "v6": Variant("v6", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=0.7,
                  note="70 % position de fond (v5) + 30 % trading court (v4)"),
    "v7": Variant("v7", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=1.0,
                  core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10),
                  note="v5 + filtre de marché BTC + stop suiveur de 10 % (3 jours sans rachat)"),
    "v8": Variant("v8", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=0.7,
                  core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10),
                  note="70 % position de fond (v7) + 30 % trading court (v4)"),
    "v9": Variant("v9", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=1.0,
                  core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10, keep_rank=5),
                  note="v7 + maintien tant que la crypto reste parmi les 5 plus fortes (moins d'allers-retours)"),
    "v10": Variant("v10", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=1.0,
                   core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10, keep_rank=5, trend_days=50),
                   note="v9 + tendance de fond : cours au-dessus de la moyenne de 50 jours (crypto et BTC)"),
    "v11": Variant("v11", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=1.0,
                   core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10, keep_rank=5, trend_days=50,
                                             vol_target=0.5),
                   note="v10 + taille selon la volatilité (au plus 50 % de volatilité annuelle par place)"),
    "v12": Variant("v12", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=0.7,
                   core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10, keep_rank=5, trend_days=50,
                                             vol_target=0.5),
                   note="70 % position de fond (v11) + 30 % trading court (v4)"),
    "v13": Variant("v13", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), core_share=1.0,
                   core_rules=core.CoreRules(market_filter="BTCUSDC", trailing_stop=0.10, keep_rank=5, trend_days=50,
                                             vol_target=0.3),
                   note="v11 au réglage de risque prudent : au plus 30 % de volatilité annuelle par place (même règle, mises plus petites)"),
}


def build(symbols, first, last, cache_dir, *, client=None, variant: Variant = VARIANTS["v1"]) -> tuple[pd.DataFrame, dict]:
    """Toutes les alertes des veilleurs avec l'issue de leur plan géré minute par minute, et les clôtures mensuelles."""
    frames, closes = {}, {}  # « closes » : clôtures mensuelles ; la table journalière est ajoutée sous « _daily »
    for s in symbols:
        start = _listed_from(s, first, last, cache_dir, client)
        if start is None:
            continue
        minutes, _ = load_bars(s, start, last, cache_dir, client=client)
        frames[s] = minutes
    btc = compute(five_minute_bars(frames["BTCUSDC"])) if "BTCUSDC" in frames else None
    out = []
    for s, minutes in frames.items():
        f = btc if s == "BTCUSDC" and btc is not None else compute(five_minute_bars(minutes), btc)
        a = alerts(s, f, round_trip=variant.round_trip, daily_trend=variant.daily_trend)
        if a.empty:
            continue
        o, h, l, c, t = (minutes[k].to_numpy(float) for k in ("o", "h", "l", "c", "t"))
        res = [manage(o, h, l, c, int(m), float(st), int(mx), round_trip=variant.round_trip)
               for m, st, mx in zip(a["m_next"], a["stop"], a["max_minutes"])]
        r = pd.DataFrame(res, index=a.index)
        a = a.join(r.drop(columns=["risk_pct"], errors="ignore"))
        a = a[a["outcome"] != INVALID].copy()
        a["exit_ms"] = t[a["exit_minute"].astype(int)].astype("int64") + 60_000
        out.append(a)
        closes[s] = {m: float(minutes.loc[minutes["t"] < month_bounds(m)[1], "c"].dropna().iloc[-1])
                     for m in months(first, last) if month_bounds(m)[1] >= int(minutes["t"].iloc[0])}
    closes["_daily"] = core.daily_table(frames)
    return pd.concat(out, ignore_index=True), closes


def _listed_from(symbol, first, last, cache_dir, client) -> str | None:
    """Premier mois publié de la paire à partir de « first » : une crypto entre dans l'univers à sa cotation, jamais avant
    (univers défini à la date). Un mois absent après la cotation reste une erreur."""
    for m in months(first, last):
        try:
            download_month(symbol, m, cache_dir, client=client)
            return m
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
    return None


def month_test(cands: pd.DataFrame, closes: dict, month: str, *, margin_r: float = 0.10, profile=desk.Profile(),
               variant: Variant = VARIANTS["v1"]) -> dict:
    start, end = month_bounds(month)
    end += 1
    train = cands[cands["exit_ms"] < start - EMBARGO_MS]
    test = cands[(cands["decided_at"] >= start) & (cands["decided_at"] < end)]
    ai = PrincipalAI(margin_r=margin_r, extra=variant.extra).fit(train)
    verdict = ai.judge(test)
    test = test.join(verdict)
    with_ai = desk.run(test, test["take"].to_numpy(), start, end, profile)
    all_alerts = desk.run(test, np.ones(len(test), bool), start, end, profile)
    prev = months("2000-01", month)[-2]
    bh = [closes[s][month] / closes[s][prev] - 1 for s in closes if not s.startswith("_") and prev in closes[s]]
    capital = 1000.0
    short_capital = capital * (1 - variant.core_share)
    tactical = desk.run(test, test["take"].to_numpy(), start, end, profile, capital=short_capital) if short_capital > 0 else None
    fund = _fund(closes, start, end, capital * variant.core_share, variant) if variant.core_share > 0 else None
    system = _combine(fund, tactical, capital, start, end)
    system.pop("daily")
    return {"month": month, "train_alerts": int(len(train)), "month_alerts": int(len(test)),
            "ai_taken": int(test["take"].sum()), "ai_ready": ai.ready, "core_share": variant.core_share,
            "system": system,
            "core": fund["metrics"] if fund else None,
            "with_ai": {**with_ai["metrics"], "refusals": with_ai["refusals"]},
            "all_alerts": all_alerts["metrics"],
            "buy_and_hold_pct": float(100 * np.mean(bh)) if bh else None, "cash_pct": 0.0,
            "examples": [t["reasoning"] for t in with_ai["trades"][:5]],
            "trades": [{k: t[k] for k in ("symbol", "setup", "decided_at", "exit_ms", "outcome", "r", "pnl")} for t in with_ai["trades"]]}


def _fund(closes, start, end, capital, variant):
    opens, day_closes, lows = closes["_daily"]
    return core.run(opens, day_closes, start, end, capital, variant.core_rules, lows=lows)


def year_test(cands: pd.DataFrame, closes: dict, last: str, *, margin_r: float = 0.10, profile=desk.Profile(),
              variant: Variant = VARIANTS["v1"], first: str | None = None) -> dict:
    """Stabilisation sur douze mois (ou de « first » à « last ») : position de fond continue ; trading court réappris
    chaque mois sur son seul passé, son capital passant d'un mois au suivant. Comparaison mois par mois au marché."""
    tested = months(first, last) if first else months("2000-01", last)[-12:]
    start, end = month_bounds(tested[0])[0], month_bounds(tested[-1])[1] + 1
    capital = 1000.0
    fund = _fund(closes, start, end, capital * variant.core_share, variant) if variant.core_share > 0 else None
    short_capital, monthly, tactical_curve, tactical_trades = capital * (1 - variant.core_share), [], [], []
    for m in tested:
        ms, me = month_bounds(m)
        if short_capital > 0:
            train = cands[cands["exit_ms"] < ms - EMBARGO_MS]
            test = cands[(cands["decided_at"] >= ms) & (cands["decided_at"] < me + 1)]
            ai = PrincipalAI(margin_r=margin_r, extra=variant.extra).fit(train)
            test = test.join(ai.judge(test))
            run = desk.run(test, test["take"].to_numpy(), ms, me + 1, profile, capital=short_capital)
            tactical_curve += run["curve"]
            tactical_trades += run["trades"]
            short_capital = run["final_equity"]
        monthly.append({"month": m, "buy_and_hold_pct": _bh(closes, m)})
    tactical = ({"capital": capital * (1 - variant.core_share), "final_equity": short_capital, "trades": tactical_trades,
                 "curve": tactical_curve} if variant.core_share < 1 else None)
    system = _combine(fund, tactical, capital, start, end)
    # rendement mensuel du système à partir de l'équité marquée chaque jour
    eq = system.pop("daily")
    for row in monthly:
        a, b = month_bounds(row["month"])
        before = [v for t, v in eq if t <= a] or [capital]
        inside = [v for t, v in eq if t <= b + 1]
        row["system_pct"] = float(100 * (inside[-1] / before[-1] - 1))
        row["beats_market"] = bool(row["system_pct"] > row["buy_and_hold_pct"])
    first, prev = tested[0], months("2000-01", tested[0])[-2]
    coins = [s for s in closes if not s.startswith("_") and prev in closes[s] and last in closes[s]]
    bh_year = 100 * float(np.mean([closes[s][last] / closes[s][prev] - 1 for s in coins]))
    years = {}
    for i in range(0, len(tested), 12):
        block = tested[i:i + 12]
        a, b = months("2000-01", block[0])[-2], block[-1]
        sys_pct = 100 * (np.prod([1 + r["system_pct"] / 100 for r in monthly[i:i + 12]]) - 1)
        mk = [closes[s][b] / closes[s][a] - 1 for s in closes if not s.startswith("_") and a in closes[s] and b in closes[s]]
        years[f"{block[0]} → {b}"] = {"system_pct": float(sys_pct), "buy_and_hold_pct": float(100 * np.mean(mk)) if mk else None,
                                     "positive_months": int(sum(r["system_pct"] > 0 for r in monthly[i:i + 12]))}
    return {"months": tested, "variant": variant.name, "system": system, "monthly": monthly, "years": years,
            "buy_and_hold_pct": bh_year, "months_beating_market": int(sum(r["beats_market"] for r in monthly)),
            "positive_months": int(sum(r["system_pct"] > 0 for r in monthly)), "n_months": len(tested),
            "core": fund["metrics"] if fund else None, "from": first, "to": last}


def _bh(closes, month):
    prev = months("2000-01", month)[-2]
    coins = [s for s in closes if not s.startswith("_") and prev in closes[s]]
    return float(100 * np.mean([closes[s][month] / closes[s][prev] - 1 for s in coins])) if coins else None


def _combine(fund, tactical, capital, start, end) -> dict:
    """Équité du système (position de fond + trading court), marquée chaque jour à 00:00 UTC."""
    days = range(start // core.DAY_MS + 1, -(-end // core.DAY_MS) + 1)
    fund_curve = dict(fund["curve"]) if fund else {}
    eq = [capital]
    for d in days:
        t = d * core.DAY_MS
        f = fund_curve.get(t, fund["capital"] if fund else 0.0) if fund else 0.0
        if tactical:
            pts = [e for ts, e in tactical["curve"] if ts <= t]
            f += pts[-1] if pts else tactical["capital"]
        eq.append(f)
    if fund:
        eq[-1] = fund["final_equity"] + (tactical["final_equity"] if tactical else 0.0)
    elif tactical:
        eq[-1] = tactical["final_equity"]
    stamps = [start] + [d * core.DAY_MS for d in days]
    daily = list(zip(stamps, eq))
    eq = np.array(eq)
    return {"daily": daily, "return_pct": float(100 * (eq[-1] / capital - 1)),
            "max_drawdown_pct": float(100 * np.max(1 - eq / np.maximum.accumulate(eq))),
            "tactical_trades": len(tactical["trades"]) if tactical else 0,
            "core_orders": len(fund["trades"]) if fund else 0}


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "inconnu"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Essai du moteur court terme (simulation exclusive)")
    ap.add_argument("--month", default="2026-08")
    ap.add_argument("--year", action="store_true", help="stabilisation : les 12 mois qui finissent à --month (inclus)")
    ap.add_argument("--from", dest="period_from", default=None, help="avec --year : premier mois de la période (sinon 12 mois)")
    ap.add_argument("--first", default="2024-09")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    ap.add_argument("--variant", default="v1", choices=sorted(VARIANTS))
    args = ap.parse_args(argv)
    variant = VARIANTS[args.variant]
    store = Path(args.cache) / f"alerts-{variant.name}-{args.first}-{args.month}.pkl"
    if store.exists():
        cands, closes = pickle.loads(store.read_bytes())
    else:
        cands, closes = build(SYMBOLS, args.first, args.month, args.cache, variant=variant)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_bytes(pickle.dumps((cands, closes)))
    if args.year:
        year = year_test(cands, closes, args.month, margin_r=args.margin, variant=variant, first=args.period_from)
        y = year["system"]
        for r in year["monthly"]:
            print(f"  {r['month']} : système {r['system_pct']:+6.2f} % | marché {r['buy_and_hold_pct']:+6.2f} %"
                  f"{'  ✓ mieux que le marché' if r['beats_market'] else ''}")
        print(f"{variant.name} {year['from']} → {year['to']} : système {y['return_pct']:+.2f} % (baisse max {y['max_drawdown_pct']:.2f} %, "
              f"{y['core_orders']} ordres de fond, {y['tactical_trades']} trades courts) | marché {year['buy_and_hold_pct']:+.2f} % | "
              f"mois meilleurs que le marché {year['months_beating_market']}/{year['n_months']}, "
              f"mois positifs {year['positive_months']}/{year['n_months']}")
        for k, v in year["years"].items():
            print(f"    {k} : système {v['system_pct']:+.2f} % | marché {v['buy_and_hold_pct']:+.2f} % | mois positifs {v['positive_months']}")
        report = {"schema": "scalp-year-v1", "commit": _commit(), "margin_r": args.margin, "symbols": SYMBOLS,
                  "data_from": args.first, **year, "variant": asdict(variant)}
        if args.out:
            Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
        if args.trials:
            with open(args.trials, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commit": report["commit"],
                                     "variant": variant.name, "note": variant.note, "test": f"période de {year['n_months']} mois",
                                     "from": year["from"], "to": year["to"], "system_pct": y["return_pct"],
                                     "system_drawdown_pct": y["max_drawdown_pct"], "buy_and_hold_pct": year["buy_and_hold_pct"],
                                     "months_beating_market": year["months_beating_market"], "positive_months": year["positive_months"],
                                     "n_months": year["n_months"], "years": year["years"],
                                     "monthly": year["monthly"]}, ensure_ascii=False) + "\n")
        return report
    tested = [args.month]
    results = [month_test(cands, closes, m, margin_r=args.margin, variant=variant) for m in tested]
    report = {"schema": "scalp-trial-v1", "commit": _commit(), "variant": asdict(variant), "margin_r": args.margin, "symbols": SYMBOLS,
              "data_from": args.first, "months": results}
    for r in results:
        w, a, y = r["with_ai"], r["all_alerts"], r["system"]
        print(f"{r['month']} : système {y['return_pct']:+.2f} % (baisse max {y['max_drawdown_pct']:.2f} %, fond {r['core_share']:.0%}, "
              f"{y['core_orders']} ordres de fond, {y['tactical_trades']} trades courts) | trading court seul {w['return_pct']:+.2f} % "
              f"({w['trades']} trades, gagnants {w['win_rate'] or 0:.0%}) | toutes alertes {a['return_pct']:+.2f} % | "
              f"achat-conservation {r['buy_and_hold_pct']:+.2f} %")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commit": report["commit"],
                                 "variant": variant.name, "note": variant.note, "margin_r": args.margin, "months": [
                                     {"month": r["month"], "system_pct": r["system"]["return_pct"],
                                      "system_drawdown_pct": r["system"]["max_drawdown_pct"], "core_share": r["core_share"],
                                      "return_pct": r["with_ai"]["return_pct"], "trades": r["with_ai"]["trades"],
                                      "win_rate": r["with_ai"]["win_rate"], "max_drawdown_pct": r["with_ai"]["max_drawdown_pct"],
                                      "all_alerts_pct": r["all_alerts"]["return_pct"], "buy_and_hold_pct": r["buy_and_hold_pct"]}
                                     for r in results]}, ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":
    main()
