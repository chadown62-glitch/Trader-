"""Finite paper simulation. Use a dedicated SQLite file, separate from the cockpit.

Each tick runs the complete M58 chain of ``memecoin_simulator.tick``: established pools (liquidity >= 20,000 USD, at
least one hour old, an active last hour, a Jupiter round trip quoted without a wallet), the anti-rug veto, the agent
fleet before any fictitious entry, then the calibrated exits (stop -20 %, 20 % sold at 1.25x, slices at x3/x6/x10,
adaptive trailing stop). The report (JSON, text and Markdown for the GitHub Actions job summary) adds the filter and
veto counters, the fleet decisions and, when ``memecoin_analytics`` is present, its complete analytics.
Paper only: no order, no key, no model promotion; everything stays in ``asset_domain="memecoin"``.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from ..services.bus import EventBus
from ..storage.db import Database
from . import established_pools, memecoin_simulator as sim
from .memecoin_strategies import CALIBRATED
from .net import PublicClient, SourceUnavailable
from .tokens import load_registry

try:  # read-only analytics, delivered by a parallel PR: the runner reports without it rather than failing
    from .memecoin_analytics import build_analytics_report
except ImportError:  # pragma: no cover - depends on merge order
    build_analytics_report = None

APPLICATION_ID = 0x4D434F49  # identifies the isolated cloud snapshot
MAX_TICKS = 24
DAY_S = 86_400
STAGE_LABELS = {"liquidity": "Liquidité sous 20 000 $", "age": "Pool de moins d'une heure", "activity": "Dernière heure inactive",
                "round_trip": "Revente non confirmée (Jupiter)", "guardian": "Veto anti-arnaque"}
DECISION_LABELS = {"PROPOSITION_SIMULÉE": "Entrée fictive autorisée", "ATTENDRE": "Attente", "VETO": "Veto", "REFUS": "Refus du risque",
                   "ABSTENTION": "Abstention"}


def open_session(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        identity = conn.execute("PRAGMA application_id").fetchone()[0]
        if identity != APPLICATION_ID:
            if identity or conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                raise ValueError("Le runner exige une base dédiée à la simulation cloud memecoin")
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        conn.commit()
    finally:
        conn.close()
    return Database(path)


def pipeline(db, since_s, now_s):
    """M58 counters: pools seen and refused by stage (this run and the last 24 h), fleet decisions, exit policy."""
    state, _ = sim.load(db)
    run = established_pools.summary(db, since_s)
    day = established_pools.summary(db, now_s - DAY_S)
    decisions = [d for d in state.get("fleet", []) if d.get("asset_domain", sim.DOMAIN) == sim.DOMAIN and d.get("ts", 0) >= since_s]
    counts = {}
    for decision in decisions:
        counts[decision["decision"]] = counts.get(decision["decision"], 0) + 1

    def window(s):
        return {"pools": s["tokens"], "accepted": s["accepted"], "refused_by_stage": s["refused_by_stage"],
                "median_liquidity_usd": s["median_liquidity_usd"]}

    return {
        "asset_domain": sim.DOMAIN,
        "thresholds": run["thresholds"],
        "run": window(run),
        "last_24h": window(day),
        "vetoes": {"guardian": run["refused_by_stage"]["guardian"], "round_trip": run["refused_by_stage"]["round_trip"],
                   "fleet": counts.get("VETO", 0)},
        "fleet": {"decisions": counts, "recent": [{k: d.get(k) for k in ("ts", "mint", "decision", "why")} for d in decisions[-10:]]},
        "candidates": day["candidates"][:10],
        "exit_policy": {"name": state.get("exit_policy", sim.LEGACY_EXITS), "stop_loss": CALIBRATED.stop_loss,
                        "cover_ratio": CALIBRATED.cover_ratio, "cover_fraction": CALIBRATED.cover_fraction,
                        "tiers": list(CALIBRATED.tiers), "trail_base": CALIBRATED.trail_base, "trail_max": CALIBRATED.trail_max},
    }


def analytics(db_path):
    """Complete analytics of the closed database (read-only); an unavailable module or a refused history is reported."""
    if build_analytics_report is None:
        return {"asset_domain": sim.DOMAIN, "status": "unavailable", "reason": "module memecoin_analytics absent"}
    try:
        return build_analytics_report(db_path)
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return {"asset_domain": sim.DOMAIN, "status": "error", "reason": str(exc)[:200]}


def summary(report):
    def fmt(value, unit=" USD"):
        return "indisponible" if value is None else f"{value:.4f}{unit}"

    lines = [
        "Simulation memecoin · USD fictifs",
        f"Cycles terminés : {report['ticks_completed']}/{report['ticks_requested']}",
        f"Capital initial : {fmt(report['initial_capital'])}",
        f"Équité finale : {fmt(report['final_equity'])}",
        f"P&L net : {fmt(report['net_pnl'])}",
        "Win rate (positions clôturées) : " + fmt(None if report["win_rate"] is None else 100 * report["win_rate"], " %"),
        f"Trades ouverts cumulés : {report['total_trades']} ; positions clôturées : {report['closed_trades']}",
    ]
    for name, stats in report["strategies"].items():
        lines.append(f"{name} : {fmt(stats['pnl'])}, {stats['trades']} entrées")
    lines.append("Classement par token (P&L aux dernières valorisations disponibles)")
    lines.extend(f"{mint} : {fmt(pnl)}" for mint, pnl in report["token_ranking"])
    if not report["token_ranking"]:
        lines.append("Aucun token")
    m58 = report.get("pipeline")
    if m58:
        run = m58["run"]
        lines.append(f"Pools établis examinés : {run['pools']} ; retenus : {run['accepted']} ; "
                     f"vetos anti-arnaque : {m58['vetoes']['guardian']} ; vetos de la flotte : {m58['vetoes']['fleet']}")
    if report["source_errors"]:
        lines.append("Sources dégradées ; détails dans le rapport JSON.")
    return "\n".join(lines) + "\n"


def _usd(value):
    return "n/d" if value is None else f"{value:,.2f} $".replace(",", " ")


def _pct(value):
    return "n/d" if value is None else f"{value:.2f} %"


def markdown(report):
    """Job summary for GitHub Actions: readable at a glance, on a phone too."""
    a, m58 = report["analytics"], report["pipeline"]
    ok = a.get("status") in ("ok", "stale")
    factor = _pct(None)
    if ok:
        factor = "n/d" if a["profit_factor"] is None else f"{a['profit_factor']:.2f}"
        if a["gross_profit"] and not a["gross_loss"]:
            factor = "infini (aucune perte clôturée)"
    win = report["win_rate"] if not ok else a["win_rate"]
    lines = [
        "## Simulation memecoin — paper trading (USD fictifs)", "",
        "> Simulation uniquement : aucun ordre réel, aucune clé, aucune promotion automatique. `asset_domain=\"memecoin\"`.", "",
        "| Indicateur | Valeur |", "|---|---|",
        f"| Cycles terminés | {report['ticks_completed']}/{report['ticks_requested']} |",
        f"| Capital initial | {_usd(report['initial_capital'])} |",
        f"| Équité | {_usd(a['equity'] if ok else report['final_equity'])} |",
        f"| P&L net | {_usd(a['net_pnl'] if ok else report['net_pnl'])}"
        + (f" ({_pct(a['net_pnl_pct'])})" if ok else "") + " |",
        f"| Win rate (positions clôturées) | {_pct(None if win is None else 100 * win)} |",
        f"| Profit factor | {factor} |",
        f"| Drawdown maximal | {_pct(a['max_drawdown_pct']) if ok else 'n/d'} |",
        f"| Positions clôturées | {a['closed_trades'] if ok else report['closed_trades']} |",
        f"| Vetos anti-arnaque (ce passage) | {m58['vetoes']['guardian']} |",
        f"| Vetos de la flotte (ce passage) | {m58['vetoes']['fleet']} |",
    ]
    if not ok:
        lines += ["", f"_Analytique complète indisponible : {a.get('reason', a.get('status'))}._"]
    elif a["status"] == "stale":
        lines += ["", "_Valorisation en attente : drawdown calculé sur les points disponibles._"]
    t = m58["thresholds"]
    lines += ["", "### Filtrage M58 : pools établis", "",
              f"Critères : liquidité ≥ {_usd(t['min_liquidity_usd'])}, pool d'au moins {t['min_age_minutes']:.0f} min, dernière heure "
              f"≥ {_usd(t['min_volume_h1_usd'])} avec {t['min_trades_h1']} achats et ventes, aller-retour Jupiter "
              f"≤ {100 * t['max_round_trip_loss']:.0f} %, puis veto anti-arnaque.", "",
              "| Étape | Ce passage | 24 h |", "|---|---|---|",
              f"| Pools examinés | {m58['run']['pools']} | {m58['last_24h']['pools']} |"]
    for stage, label in STAGE_LABELS.items():
        lines.append(f"| Refusés · {label} | {m58['run']['refused_by_stage'].get(stage, 0)} | {m58['last_24h']['refused_by_stage'].get(stage, 0)} |")
    lines += [f"| Retenus | {m58['run']['accepted']} | {m58['last_24h']['accepted']} |",
              f"| Liquidité médiane | {_usd(m58['run']['median_liquidity_usd'])} | {_usd(m58['last_24h']['median_liquidity_usd'])} |",
              "", "### Flotte d'agents (ce passage)", ""]
    if m58["fleet"]["decisions"]:
        lines += ["| Décision | Nombre |", "|---|---|"]
        lines += [f"| {DECISION_LABELS.get(k, k)} | {v} |" for k, v in sorted(m58["fleet"]["decisions"].items())]
        lines += [f"- `{str(d['mint'])[:8]}…` · {DECISION_LABELS.get(d['decision'], d['decision'])} · {(d.get('why') or [''])[0]}"
                  for d in m58["fleet"]["recent"]]
    else:
        lines.append("Aucune entrée examinée par la flotte : aucun signal sur les pools retenus.")
    e = m58["exit_policy"]
    lines += ["", "### Sorties calibrées", "",
              f"Politique `{e['name']}` : stop −{100 * e['stop_loss']:.0f} %, {100 * e['cover_fraction']:.0f} % vendus à "
              f"{e['cover_ratio']:.2f}× (frais couverts), paliers " + " / ".join(f"x{x:g}" for x in e["tiers"])
              + f", stop suiveur adaptatif ({100 * e['trail_base']:.0f} à {100 * e['trail_max']:.0f} %).",
              "", "### Stratégies", "", "| Stratégie | P&L | Entrées |", "|---|---|---|"]
    strategies = a["strategies"] if ok else report["strategies"]
    lines += [f"| {name} | {_usd(s['pnl'])} | {s['trades']} |" for name, s in strategies.items()]
    if m58["candidates"]:
        lines += ["", "### Pools retenus (24 h)", "", "| Pool | DEX | Liquidité | Volume 1 h | Aller-retour |", "|---|---|---|---|---|"]
        lines += [f"| {c['name'] or c['mint'][:8]} | {c['dex_id']} | {_usd(c['liquidity_usd'])} | {_usd(c['volume_h1_usd'])} | "
                  f"{_pct(None if c['round_trip_loss'] is None else 100 * c['round_trip_loss'])} |" for c in m58["candidates"]]
    if report["source_errors"]:
        lines += ["", f"_Sources dégradées : {len(report['source_errors'])} incident(s), détails dans `report.json`._"]
    return "\n".join(lines) + "\n"


def run(ticks, db_path, report_path, initial_capital=None):
    if not 1 <= ticks <= MAX_TICKS:
        raise ValueError(f"--ticks doit être compris entre 1 et {MAX_TICKS}")
    db_path, report_path = Path(db_path).resolve(), Path(report_path).resolve()
    text_path, md_path = report_path.with_suffix(".txt"), report_path.with_suffix(".md")
    if report_path.suffix.lower() != ".json" or db_path in (report_path, text_path, md_path):
        raise ValueError("Indiquer un rapport .json distinct de la base SQLite et des résumés .txt et .md")
    hub = SimpleNamespace(client=PublicClient(budget=120, timeout=4, retries=0), registry=load_registry())
    db = open_session(db_path)
    errors = []
    completed = 0
    since_s = int(time.time()) - 1
    try:
        sim.configure(db, hub.client, initial_capital, "USD" if initial_capital is not None else None)
        bus = EventBus(db)
        for index in range(ticks):
            try:
                snapshot = sim.tick(hub, db, bus)
            except SourceUnavailable as exc:
                errors.append({"tick": index + 1, "source": str(exc)})
                break
            completed += 1
            errors.extend({"tick": index + 1, "source": e} for e in snapshot["errors"])
        snapshot = sim.report(*sim.load(db))
        m58 = pipeline(db, since_s, int(time.time()))
    finally:
        db.close()
    equity = snapshot["equity"]
    stale_marks = snapshot["stale_marks"]
    if completed < ticks and snapshot["positions"]:
        equity = None
        stale_marks = sorted({pos["mint"] for pos in snapshot["positions"].values()})
    report = {
        "asset_domain": sim.DOMAIN,
        "paper_only": True,
        "currency": "USD",
        "ticks_requested": ticks,
        "ticks_completed": completed,
        "revision": snapshot["revision"],
        "initial_capital": snapshot["initial_capital"],
        "final_equity": equity,
        "net_pnl": None if equity is None else equity - snapshot["initial_capital"],
        "win_rate": snapshot["win_rate"],
        "closed_trades": snapshot["closed_trades"],
        "total_trades": sum(s["trades"] for s in snapshot["strategies"].values()),
        "strategies": snapshot["strategies"],
        "token_ranking": snapshot["token_ranking"],
        "stale_marks": stale_marks,
        "source_errors": errors,
        "pipeline": m58,
        "analytics": analytics(db_path),  # after close: the analytics read the snapshot in read-only mode
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    text_path.write_text(summary(report), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--initial-capital", type=float, help="USD fictifs ; par défaut 30 à la création ou reprise du dépôt existant")
    args = parser.parse_args(argv)
    try:
        result = run(args.ticks, args.db_path, args.report_path, args.initial_capital)
    except ValueError as exc:
        parser.error(str(exc))
    print(summary(result), end="")


if __name__ == "__main__":
    main()
