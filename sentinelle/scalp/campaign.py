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

import numpy as np
import pandas as pd

from . import desk
from .brain import PrincipalAI
from .data import load_bars, month_bounds, months
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
    note: str = ""


VARIANTS = {
    "v1": Variant("v1", note="4 veilleurs, IA mixte, marge 0,10 R, profil prudent, frais standard 0,1 %"),
    "v2": Variant("v2", round_trip=2 * (0.00075 + 0.0001), note="v1 + frais payés en BNB (0,075 % par côté)"),
    "v3": Variant("v3", daily_trend=True, extra=tuple(DAILY), note="v1 + tendance de fond journalière exigée et apprise"),
    "v4": Variant("v4", round_trip=2 * (0.00075 + 0.0001), daily_trend=True, extra=tuple(DAILY), note="v2 + v3"),
}


def build(symbols, first, last, cache_dir, *, client=None, variant: Variant = VARIANTS["v1"]) -> tuple[pd.DataFrame, dict]:
    """Toutes les alertes des veilleurs avec l'issue de leur plan géré minute par minute, et les clôtures mensuelles."""
    frames, closes = {}, {}
    for s in symbols:
        minutes, _ = load_bars(s, first, last, cache_dir, client=client)
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
        closes[s] = {m: float(minutes.loc[minutes["t"] < month_bounds(m)[1], "c"].dropna().iloc[-1]) for m in months(first, last)}
    return pd.concat(out, ignore_index=True), closes


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
    bh = [closes[s][month] / closes[s][prev] - 1 for s in closes if prev in closes[s]]
    return {"month": month, "train_alerts": int(len(train)), "month_alerts": int(len(test)),
            "ai_taken": int(test["take"].sum()), "ai_ready": ai.ready,
            "with_ai": {**with_ai["metrics"], "refusals": with_ai["refusals"]},
            "all_alerts": all_alerts["metrics"],
            "buy_and_hold_pct": float(100 * np.mean(bh)) if bh else None, "cash_pct": 0.0,
            "examples": [t["reasoning"] for t in with_ai["trades"][:5]],
            "trades": [{k: t[k] for k in ("symbol", "setup", "decided_at", "exit_ms", "outcome", "r", "pnl")} for t in with_ai["trades"]]}


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "inconnu"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Essai du moteur court terme (simulation exclusive)")
    ap.add_argument("--month", default="2026-08")
    ap.add_argument("--year", action="store_true", help="stabilisation : les 12 mois qui finissent à --month")
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
    tested = months("2000-01", args.month)[-12:] if args.year else [args.month]
    results = [month_test(cands, closes, m, margin_r=args.margin, variant=variant) for m in tested]
    report = {"schema": "scalp-trial-v1", "commit": _commit(), "variant": asdict(variant), "margin_r": args.margin, "symbols": SYMBOLS,
              "data_from": args.first, "months": results}
    for r in results:
        w, a = r["with_ai"], r["all_alerts"]
        print(f"{r['month']} : IA {w['return_pct']:+.2f} % ({w['trades']} trades, gagnants {w['win_rate'] or 0:.0%}, "
              f"baisse max {w['max_drawdown_pct']:.2f} %) | toutes alertes {a['return_pct']:+.2f} % ({a['trades']}) | "
              f"achat-conservation {r['buy_and_hold_pct']:+.2f} %")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commit": report["commit"],
                                 "variant": variant.name, "note": variant.note, "margin_r": args.margin, "months": [
                                     {"month": r["month"], "return_pct": r["with_ai"]["return_pct"], "trades": r["with_ai"]["trades"],
                                      "win_rate": r["with_ai"]["win_rate"], "max_drawdown_pct": r["with_ai"]["max_drawdown_pct"],
                                      "all_alerts_pct": r["all_alerts"]["return_pct"], "buy_and_hold_pct": r["buy_and_hold_pct"]}
                                     for r in results]}, ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":
    main()
