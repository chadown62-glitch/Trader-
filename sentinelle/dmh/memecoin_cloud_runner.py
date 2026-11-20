"""Finite paper simulation. Use a dedicated SQLite file, separate from the cockpit."""

import argparse
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from ..services.bus import EventBus
from ..storage.db import Database
from . import memecoin_simulator as sim
from .net import PublicClient, SourceUnavailable
from .tokens import load_registry

APPLICATION_ID = 0x4D434F49  # identifies the isolated cloud snapshot
MAX_TICKS = 24


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
    if report["source_errors"]:
        lines.append("Sources dégradées ; détails dans le rapport JSON.")
    return "\n".join(lines) + "\n"


def run(ticks, db_path, report_path, initial_capital=None):
    if not 1 <= ticks <= MAX_TICKS:
        raise ValueError(f"--ticks doit être compris entre 1 et {MAX_TICKS}")
    db_path, report_path = Path(db_path).resolve(), Path(report_path).resolve()
    text_path = report_path.with_suffix(".txt")
    if report_path.suffix.lower() != ".json" or db_path in (report_path, text_path):
        raise ValueError("Indiquer un rapport .json distinct de la base SQLite et du résumé .txt")
    hub = SimpleNamespace(client=PublicClient(budget=120, timeout=4, retries=0), registry=load_registry())
    db = open_session(db_path)
    errors = []
    completed = 0
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
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    text_path.write_text(summary(report), encoding="utf-8")
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
