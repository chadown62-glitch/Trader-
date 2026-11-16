"""Mesures du plan, puis passage au contrat de coûts selon les règles écrites dans le plan (aucun réglage après coup).

Entrées, toutes hors ligne : échantillons extraits de l'archive officielle (fenêtres de transactions,
somme de contrôle vérifiée), sondes de carnets récents, filtres de marché observés, archive journalière
d'ALPHA FACTORY v2 (clôtures pour les tailles et les états de marché). Aucun résultat de stratégie.
"""
from __future__ import annotations

import gzip
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import numpy as np

from . import plan as planmod
from .book import estimator_error, measure as book_measure
from .execution import RULES, evaluate_sample
from .inventory import round_trip
from .markets import classify
from .stats import cluster_bootstrap, describe, paired_difference
from .tape import Tape, quote_at
from .venues import FeeSchedule, MarketFilters

PRIMARY_SIZE = 1  # indice de la taille de 10 € dans plan["sizes_eur"]
STRESS_SIZE = 2  # 30 €


def tape_from_rows(symbol: str, rows: list, sha: str) -> Tape:
    rows = sorted(rows, key=lambda r: (r[0], r[4]))
    keep, seen = [], set()
    for r in rows:
        if r[4] in seen:
            continue
        seen.add(r[4])
        keep.append(r)
    return Tape(symbol, np.array([r[0] for r in keep], dtype=np.int64), np.array([float(r[1]) for r in keep]),
                np.array([float(r[2]) for r in keep]), np.array([not r[3] for r in keep], dtype=bool), sha)


def load_samples(root: Path, plan: dict) -> tuple[dict, dict]:
    """Échantillons archivés ; ceux dont la somme officielle ne concorde pas sont comptés, pas utilisés."""
    tapes, status = {}, {"planned": len(plan["samples"]), "archived": 0, "checksum_failed": 0, "missing": 0, "empty": 0}
    for s in plan["samples"]:
        path = Path(root) / "samples" / f"{s['id']}.json.gz"
        if not path.exists():
            status["missing"] += 1
            continue
        body = json.loads(gzip.decompress(path.read_bytes()))
        if not body.get("checksum_ok"):
            status["checksum_failed"] += 1
            continue
        status["archived"] += 1
        if not body["rows"]:
            status["empty"] += 1
        tapes[s["id"]] = tape_from_rows(s["symbol"], body["rows"], body["zip_sha256"])
    return tapes, status


def evaluate(plan: dict, tapes: dict, filters: dict[str, MarketFilters], fees: FeeSchedule) -> list[dict]:
    """Toutes les variantes d'exécution, pour chaque échantillon, taille et sens."""
    taker, maker = fees.rate(maker=False), fees.rate(maker=True)
    rows = []
    for s in plan["samples"]:
        tape = tapes.get(s["id"])
        if tape is None:
            continue
        f = filters.get(s["symbol"])
        tick = float(f.tick) if f else 0.0
        for k, size in enumerate(s["sizes_quote"]):
            for side in plan["sides"]:
                r = evaluate_sample(tape, s["t_ms"], side, size, tick=tick, maker_rate=maker, taker_rate=taker,
                                    latencies_ms=tuple(plan["latencies_ms"]), timeouts_ms=tuple(plan["timeouts_ms"]))
                rows.append({"id": s["id"], "class": s["class"], "symbol": s["symbol"], "day": s["day"], "kind": s["kind"], "split": s["split"],
                             "file_group": s["file_group"], "size_index": k, "size_eur": plan["sizes_eur"][k], "side": side,
                             "trades_in_window": int(len(tape)), **r.to_dict()})
    return rows


def _col(rows, variant, key="shortfall_bps"):
    return np.array([r["results"].get(variant, {}).get(key) if r["results"].get(variant) else np.nan for r in rows], dtype=float)


def _clusters(rows):
    return np.array([r["day"] for r in rows])


def summarize(plan: dict, rows: list[dict], fees: FeeSchedule) -> dict:
    """Tableaux par classe, comparaison des méthodes (dont l'unique test confirmatoire), états défavorables."""
    taker_bps = fees.taker.value
    lat = plan["latencies_ms"]
    out = {"classes": {}, "policies": {}, "confirmatory": None, "stress": {}}
    by_class = defaultdict(list)
    for r in rows:
        by_class[r["class"]].append(r)
    for cls, rs in sorted(by_class.items()):
        valid = [r for r in rs if r["quote"]]
        opens = [r for r in valid if r["kind"] == "open" and r["size_index"] == PRIMARY_SIZE and r["side"] == "buy"]
        allq = [r for r in valid if r["size_index"] == PRIMARY_SIZE and r["side"] == "buy"]
        half_open = np.array([r["quote"]["half_spread_bps"] for r in opens])
        half_all = np.array([r["quote"]["half_spread_bps"] for r in allq])
        prim = [r for r in valid if r["size_index"] == PRIMARY_SIZE]
        stress = [r for r in valid if r["size_index"] == STRESS_SIZE]
        m1 = _col(prim, f"market@{lat[0]}ms")
        slip1 = m1 - np.array([r["quote"]["half_spread_bps"] for r in prim]) - taker_bps
        m5 = _col(stress, f"market@{lat[1]}ms")
        slip5 = m5 - np.array([r["quote"]["half_spread_bps"] for r in stress]) - taker_bps
        out["classes"][cls] = {
            "samples": len({r["id"] for r in rs}), "with_quote": len({r["id"] for r in valid}),
            "no_quote_share": 1 - len({r["id"] for r in valid}) / max(1, len({r["id"] for r in rs})),
            "symbols": sorted({r["symbol"] for r in rs}),
            "half_spread_open": cluster_bootstrap(half_open, _clusters(opens)), "half_spread_open_describe": describe(half_open),
            "half_spread_all": describe(half_all), "crossed_share": float(np.mean([r["quote"]["crossed"] for r in allq])) if allq else None,
            "market_shortfall_10eur_1s": cluster_bootstrap(m1, _clusters(prim)), "market_shortfall_10eur_1s_describe": describe(m1),
            "slippage_10eur_1s": cluster_bootstrap(slip1, _clusters(prim)), "slippage_30eur_5s": describe(slip5),
            "market_no_evidence_share": float(np.mean([r["results"][f"market@{lat[0]}ms"]["status"] == "no_evidence" for r in prim])) if prim else None,
            "by_kind": {k: describe([r["quote"]["half_spread_bps"] for r in allq if r["kind"] == k]) for k in ("open", "intraday")},
            "by_latency": {f"{x}ms": describe(_col(prim, f"market@{x}ms")) for x in lat},
        }
    valid = [r for r in rows if r["quote"]]
    variants = sorted({v for r in valid for v in r["results"]})
    base = f"market@{lat[0]}ms"
    for v in variants:
        sub = [r for r in valid if r["size_index"] == PRIMARY_SIZE]
        sf = _col(sub, v)
        out["policies"][v] = {"fill_ratio": float(np.nanmean(_col(sub, v, "fill_ratio"))), "passive_ratio": float(np.nanmean(_col(sub, v, "passive_ratio"))),
                              "shortfall": describe(sf), "vs_market": paired_difference(_col(sub, base), sf, _clusters(sub)),
                              "adverse_drift": describe(_col(sub, v, "adverse_drift_bps"))}
    # unique comparaison confirmatoire : choix sur l'estimation, test sur la validation
    cands = [v for v in variants if v.endswith("/conservative/market")]
    est = [r for r in valid if r["size_index"] == PRIMARY_SIZE and r["split"] == "estimation"]
    val = [r for r in valid if r["size_index"] == PRIMARY_SIZE and r["split"] == "validation"]
    if cands and est and val:
        scores = {v: float(np.nanmean(_col(est, v))) for v in cands}
        chosen = min(scores, key=lambda v: (scores[v], v))
        test = paired_difference(_col(val, base), _col(val, chosen), _clusters(val))
        optimistic = chosen.replace("/conservative/", "/optimistic/")
        out["confirmatory"] = {"candidates": cands, "estimation_mean_shortfall": scores, "chosen": chosen, "reference": base,
                               "validation_market_minus_chosen": test,
                               "validation_optimistic_bound": paired_difference(_col(val, base), _col(val, optimistic), _clusters(val)),
                               "demonstrated": bool(test["ci_low"] is not None and test["ci_low"] > 0)}
    return out


def stress_states(plan: dict, rows: list[dict], archive: Path) -> dict:
    """Coût au marché dans des états défavorables connus la veille : forte volatilité, lendemain de krach, faible volume."""
    states = {}
    cache = {}
    for r in rows:
        if not r["quote"] or r["size_index"] != PRIMARY_SIZE or r["side"] != "buy":
            continue
        usdt = r["symbol"] if r["symbol"].endswith("USDT") else r["symbol"].replace("EUR", "USDT")
        if usdt not in cache:
            try:
                cache[usdt] = planmod.daily_closes(archive, usdt)
            except (KeyError, FileNotFoundError, ValueError):
                cache[usdt] = None
        closes = cache[usdt]
        if not closes:
            continue
        d = planmod._day(r["day"])
        hist = [closes.get(x) for x in range(d - 31, d)]
        if any(h is None for h in hist):
            continue
        ret = np.diff(np.log(hist))
        states.setdefault("vol30", []).append((float(ret.std(ddof=1)), r))
        states.setdefault("prior_day_move", []).append((abs(float(ret[-1])), r))
    lat0 = f"market@{plan['latencies_ms'][0]}ms"
    out = {}
    for name, pairs in states.items():
        vals = np.array([v for v, _ in pairs])
        cut = np.quantile(vals, 0.75)
        hi = [r for v, r in pairs if v >= cut]
        lo = [r for v, r in pairs if v < cut]
        out[name] = {"threshold": float(cut), "high": describe(_col(hi, lat0)), "rest": describe(_col(lo, lat0)),
                     "high_half_spread": describe([r["quote"]["half_spread_bps"] for r in hi]),
                     "rest_half_spread": describe([r["quote"]["half_spread_bps"] for r in lo])}
    crash = [r for v, r in states.get("prior_day_move", []) if v >= 0.10]
    out["after_10pct_day"] = {"samples": len(crash), "shortfall": describe(_col(crash, lat0)),
                              "half_spread": describe([r["quote"]["half_spread_bps"] for r in crash])}
    return out


def routing(plan: dict, rows: list[dict]) -> dict:
    """EUR → actif : paire en euros directement, ou EUR → USDT puis paire en USDT (deux ordres, deux commissions)."""
    lat0 = f"market@{plan['latencies_ms'][0]}ms"
    idx = {(r["file_group"], r["symbol"], r["kind"], r["size_index"], r["side"]): r for r in rows if r["quote"]}
    out = {}
    for base in ("BTC", "ETH", "SOL"):
        direct, via, days = [], [], []
        for (group, sym, kind, k, side), r in idx.items():
            if sym != f"{base}EUR" or k != PRIMARY_SIZE or side != "buy" or kind != "open":
                continue
            fx = idx.get((group, "EURUSDT", kind, k, "sell"))  # vendre des EUR contre des USDT
            leg = idx.get((group, f"{base}USDT", kind, k, "buy"))
            if not fx or not leg:
                continue
            a, b, c = (x["results"][lat0]["shortfall_bps"] for x in (r, fx, leg))
            if None in (a, b, c):
                continue
            direct.append(a)
            via.append(b + c)
            days.append(r["day"])
        if direct:
            out[base] = {"pairs": len(direct), "direct": describe(direct), "via_usdt": describe(via),
                         "via_minus_direct": paired_difference(np.array(via), np.array(direct), np.array(days))}
    return out


def bar_touch_optimism(plan: dict, rows: list[dict], archive: Path) -> dict:
    """Le moteur sur bougies remplit un ordre à cours limité dès que la bougie touche son prix. Comparaison avec le ruban."""
    out = {}
    cache = {}
    for r in rows:
        if not r["quote"] or r["kind"] != "open" or r["size_index"] != PRIMARY_SIZE or r["side"] != "buy" or not r["symbol"].endswith("USDT"):
            continue
        sym = r["symbol"]
        if sym not in cache:
            try:
                cache[sym] = _daily_lows(archive, sym)
            except (KeyError, FileNotFoundError, ValueError):
                cache[sym] = None
        lows = cache[sym]
        if not lows:
            continue
        d = planmod._day(r["day"])
        if d not in lows:
            continue
        touched = lows[d] <= r["quote"]["bid"]
        for timeout in plan["timeouts_ms"]:
            for rule in RULES:
                key = f"join/{timeout // 1000}s/{rule}/cancel"
                cell = out.setdefault(key, {"bar_touch": 0, "tape_fill": 0, "n": 0})
                cell["n"] += 1
                cell["bar_touch"] += int(touched)
                cell["tape_fill"] += int(r["results"][key]["passive_ratio"] >= 1 - 1e-9)
    for cell in out.values():
        cell["bar_touch_rate"] = cell["bar_touch"] / cell["n"] if cell["n"] else None
        cell["tape_fill_rate"] = cell["tape_fill"] / cell["n"] if cell["n"] else None
    return out


def _daily_lows(archive: Path, symbol: str) -> dict[int, float]:
    import hashlib

    manifest = json.loads((Path(archive) / "manifest.json").read_bytes())
    meta = manifest["symbols"][symbol]
    raw = (Path(archive) / "klines" / f"{symbol}.csv.gz").read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
        raise ValueError(symbol)
    out = {}
    for line in gzip.decompress(raw).decode("utf-8").splitlines()[1:]:
        f = line.split(",")
        out[int(f[0]) // planmod.DAY_MS] = float(f[3])
    return out


def inventory_effects(rows: list[dict], filters: dict[str, MarketFilters], fees: FeeSchedule) -> dict:
    """Reliquat et écart de taille d'un aller-retour de 10 €, avec la règle de frais de Binance, sur les filtres observés."""
    rate = Decimal(str(fees.rate(maker=False)))
    out = defaultdict(lambda: {"dust_share": [], "sizing_error": [], "below_minimum": 0, "n": 0})
    for r in rows:
        if not r["quote"] or r["size_index"] != PRIMARY_SIZE or r["side"] != "buy" or r["kind"] != "open":
            continue
        f = filters.get(r["symbol"])
        if f is None:
            continue
        price = Decimal(str(r["quote"]["ask"]))
        rt = round_trip(price, Decimal(str(r["notional"])), step=f.step, min_notional=f.min_notional, min_qty=f.min_qty,
                        fee_rate=rate, fee_in="received")
        cell = out[r["class"]]
        cell["n"] += 1
        cell["dust_share"].append(rt.dust_share)
        cell["sizing_error"].append(rt.sizing_error)
    return {k: {"n": v["n"], "dust_share": describe(v["dust_share"]), "sizing_error": describe(v["sizing_error"])} for k, v in out.items()}


def probes(root: Path, filters: dict[str, MarketFilters], sizes_quote=(5.0, 10.0, 30.0, 100.0)) -> dict:
    """Carnets récents observés : écart, profondeur, coût d'un ordre au marché, et erreur de l'estimateur par le ruban."""
    from .tape import from_binance

    idx = [json.loads(x) for x in (Path(root) / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    depth = [r for r in idx if r["kind"] == "depth" and r["status"] == 200]
    trades = {(r["symbol"], r["round"]): r for r in idx if r["kind"] == "aggTrades_probe" and r["status"] == 200}
    books, errors = defaultdict(list), defaultdict(list)
    for r in depth:
        raw = (Path(root) / "raw" / r["sha256"]).read_bytes()
        f = filters.get(r["symbol"])
        b = book_measure(raw, r["symbol"], r["received_ms"], sizes_quote, step=str(f.step) if f else "0.00000001")
        books[r["symbol"]].append(b.to_dict())
        t = trades.get((r["symbol"], r["round"]))
        if t:
            tape = from_binance(json.loads((Path(root) / "raw" / t["sha256"]).read_bytes()), r["symbol"])
            e = estimator_error(b, tape)
            if e:
                errors[classify(f.base, f.quote) if f else "unknown"].append(e)
    summary = {}
    for sym, bs in books.items():
        summary[sym] = {"probes": len(bs), "half_spread_bps": describe([b["half_spread_bps"] for b in bs]),
                        "depth_quote_top": describe([b["depth_quote_top"] for b in bs]),
                        "impact_bps": {s: describe([b["impact_bps"][str(s)] for b in bs]) for s in sizes_quote}}
    err = {}
    for cls, es in errors.items():
        e = np.array([x["error_bps"] for x in es])
        err[cls] = {"n": len(es), "error_bps": describe(e), "mean_error": cluster_bootstrap(e, np.arange(len(e))),
                    "underestimates_share": float(np.mean(e < 0))}
    return {"books": summary, "estimator_error": err}


def quote_coverage(rows: list[dict]) -> dict:
    out = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["size_index"] == PRIMARY_SIZE and r["side"] == "buy":
            out[r["class"]][0] += 1
            out[r["class"]][1] += int(r["quote"] is not None)
    return {k: {"samples": a, "with_quote": b, "share": b / a if a else None} for k, (a, b) in out.items()}


__all__ = ["load_samples", "evaluate", "summarize", "stress_states", "routing", "bar_touch_optimism", "inventory_effects", "probes",
           "quote_coverage", "quote_at"]
