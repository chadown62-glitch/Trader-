"""Évaluation unique du système figé sur la période réservée (1er septembre - 22 novembre 2026).

Le système (research/scalp-v1/frozen-system-v1.json) a été figé et publié avant le chargement de ces données. Ce module
vérifie que les réglages du code sont exactement ceux du fichier figé, puis rejoue la période minute par minute avec les
mêmes règles que le développement. Aucun réglage n'est permis après ce test.

Lancement : python -m sentinelle.scalp.reserve
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from . import core, desk, rapide, systeme
from .campaign import _listed_from
from .data import RESERVED_FROM, RESERVED_TO, load_bars

FROZEN = Path(__file__).resolve().parents[2] / "research" / "scalp-v1" / "frozen-system-v1.json"
CORE_RULES = systeme.BASE
FAST_RULES = rapide.SUIVEUSE
LAST_MONTH, LAST_DAY = "2026-11", "2026-11-22"


def _same(a, b) -> bool:
    """Égalité stricte, sauf pour les nombres réels comparés à l'arrondi machine près."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15)
    return a == b


def check_frozen(path: Path = FROZEN) -> tuple[dict, str]:
    """Refuse toute évaluation si le code ne correspond plus exactement au système figé."""
    raw = path.read_bytes()
    frozen = json.loads(raw)
    assert _same(frozen["core"]["rules"], asdict(CORE_RULES)), "règles de la position de fond différentes du système figé"
    fast = asdict(FAST_RULES)
    fast.pop("regime")
    fast["setups"] = list(fast["setups"])
    assert _same(frozen["fast"]["rules"], fast), "règles du moteur rapide différentes du système figé"
    p = desk.Profile()
    assert _same(frozen["fast"]["desk"], {"risk_per_trade": p.risk_per_trade, "max_open": p.max_open, "max_position": p.max_position,
                                      "daily_loss_limit": p.daily_loss_limit, "cooldown_losses": p.cooldown_losses,
                                      "cooldown_hours": p.cooldown_ms / 3_600_000}), "pupitre différent du système figé"
    assert FAST_RULES.regime == core.CoreRules(market_filter="BTCUSDC", trend_days=50)
    assert frozen["fast"]["universe"] == rapide.UNIVERSE
    assert (frozen["evaluation"]["period"]["from_utc"], frozen["evaluation"]["period"]["to_utc"]) == (RESERVED_FROM, RESERVED_TO)
    return frozen, hashlib.sha256(raw).hexdigest()


def measured_slippage(path) -> dict:
    """Glissement par côté mesuré sur le carnet public pour un ordre de 330 USDC (pire de l'achat et de la vente),
    jamais sous l'hypothèse du système figé (0,01 %)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["rows"]
    return {r["symbol"]: max(0.0001, max(r["buy_330"], r["sell_330"]) / 100) for r in rows}


def with_costs(cands: pd.DataFrame, slip: dict, base: float = 0.0001) -> pd.DataFrame:
    """Mêmes trades, frais d'exécution mesurés : les sorties du stop suiveur ne dépendent pas des frais."""
    extra = cands["symbol"].map(lambda s: 2 * (slip.get(s, base) - base))
    net = cands["net_return"] - extra
    return cands.assign(net_return=net, r=net / cands["risk_pct"])


def sensitivity(tables, cands, take, start, end, slip: dict, core_symbols, share) -> dict:
    core_rules = replace(CORE_RULES, slippage_per_side=max(slip.get(s, 0.0001) for s in core_symbols))
    adjusted = with_costs(cands, slip)
    out = {name: systeme.evaluate(tables, adjusted, take, core_rules, sh, start, end)
           for name, sh in (("systeme", share), ("fond_seul", 1.0), ("rapide_seul", 0.0))}
    out["core_slippage_per_side"] = core_rules.slippage_per_side
    return out


def load(symbols, first, cache_dir):
    out = {}
    for s in symbols:
        start = _listed_from(s, first, "2026-08", cache_dir, None)
        if start is not None:
            out[s] = load_bars(s, start, LAST_MONTH, cache_dir, evaluation=True, last_day=LAST_DAY)[0]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Évaluation unique du système figé (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/reserved-2026-09-2026-11.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    ap.add_argument("--costs", default=None, help="coûts mesurés (live-costs-*.json) : sensibilité publiée à côté du verdict")
    args = ap.parse_args(argv)
    frozen, digest = check_frozen()
    start = int(pd.Timestamp(RESERVED_FROM).timestamp() * 1000)
    end = int(pd.Timestamp(RESERVED_TO).timestamp() * 1000) + 1000
    core_minutes = load(frozen["core"]["universe"], frozen["core"]["data_from"], args.cache)
    tables = core.daily_table(core_minutes)
    integrity = {s: {"bars": int(m["o"].notna().sum()), "missing_minutes": int(m["o"].isna().sum()),
                     "last": str(pd.to_datetime(m["t"].iloc[-1], unit="ms"))} for s, m in core_minutes.items()}
    del core_minutes
    cands, closes = [], {}
    for s in rapide.UNIVERSE:
        m = load([s], frozen["fast"]["data_from"], args.cache).get(s)
        if m is None:
            continue
        closes[s] = core.daily_table({s: m})[1][s]
        cands.append(rapide.alerts(s, m, FAST_RULES))
        integrity.setdefault(s, {"bars": int(m["o"].notna().sum()), "missing_minutes": int(m["o"].isna().sum()),
                                 "last": str(pd.to_datetime(m["t"].iloc[-1], unit="ms"))})
        del m
    cands = pd.concat(cands, ignore_index=True)
    closes = pd.DataFrame(closes)
    take = rapide.gate(cands, rapide.open_markets(closes, FAST_RULES))
    results = {name: systeme.evaluate(tables, cands, take, CORE_RULES, share, start, end)
               for name, share in (("systeme", frozen["core"]["share"]), ("fond_seul", 1.0), ("rapide_seul", 0.0))}
    d0, d1 = start // core.DAY_MS - 1, end // core.DAY_MS - 1  # clôture du 31 août, clôture du 22 novembre
    def bh(table, names):  # noqa: E306
        r = [(table.at[d1, s] / table.at[d0, s] - 1) for s in names if s in table and pd.notna(table.at[d0, s])]
        return 100 * sum(r) / len(r)
    benchmarks = {"cash_pct": 0.0, "btc_pct": bh(tables[1], ["BTCUSDC"]),
                  "buy_and_hold_8_pct": bh(tables[1], frozen["core"]["universe"]),
                  "buy_and_hold_20_pct": bh(closes, rapide.UNIVERSE)}
    s = results["systeme"]
    verdict = "confirmé" if s["return_pct"] >= 0 and s["max_drawdown_pct"] <= 20 else "non confirmé"
    measured = None
    if args.costs:
        measured = sensitivity(tables, cands, take, start, end, measured_slippage(args.costs), frozen["core"]["universe"],
                               frozen["core"]["share"])
    report = {"schema": "scalp-reserved-v1", "frozen_sha256": digest, "period": [RESERVED_FROM, RESERVED_TO],
              "results": results, "benchmarks": benchmarks, "verdict": verdict, "integrity": integrity,
              "measured_costs": measured,
              "known_limit": frozen["evaluation"]["known_limit"]}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float) + "\n", encoding="utf-8")
    for name, r in results.items():
        print(f"{name} : {r['return_pct']:+.2f} %, baisse max {r['max_drawdown_pct']:.2f} %, semaines + {r['positive_weeks']} "
              f"− {r['negative_weeks']} 0 {r['flat_weeks']}, semaine moyenne {r['mean_week_pct']:+.2f} %, pire {r['worst_week_pct']:+.2f} %, "
              f"ordres de fond {r['core_orders']}, trades rapides {r['fast_trades']}")
    print("références :", {k: round(v, 2) for k, v in benchmarks.items()}, "| verdict :", verdict)
    if measured:
        for name in ("systeme", "fond_seul", "rapide_seul"):
            r = measured[name]
            print(f"  coûts mesurés, {name} : {r['return_pct']:+.2f} %, baisse max {r['max_drawdown_pct']:.2f} %, "
                  f"semaine moyenne {r['mean_week_pct']:+.2f} %")
    with open(args.trials, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "reserve",
                             "frozen_sha256": digest, "verdict": verdict, "benchmarks": benchmarks,
                             "measured_costs": None if not measured else {n: measured[n]["return_pct"] for n in ("systeme", "fond_seul", "rapide_seul")},
                             "results": {n: {k: r[k] for k in ("return_pct", "max_drawdown_pct", "mean_week_pct", "positive_weeks",
                                                               "negative_weeks", "flat_weeks", "worst_week_pct")}
                                         for n, r in results.items()}}, ensure_ascii=False, default=float) + "\n")
    return report


if __name__ == "__main__":
    main()
