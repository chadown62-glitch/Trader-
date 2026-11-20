"""Read-only analytics for an isolated memecoin paper session (USD)."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from . import memecoin_simulator as sim


def _read_snapshot(path):
    if not path.exists():
        return None
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=0)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            return None
        row = conn.execute("SELECT value FROM meta WHERE key=?", (sim.KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def _closed_results(state):
    # One lot per strategy/mint. A new buy starts the next round trip;
    # partial exits belong to the same lot until it closes.
    latest = {}
    closed = []
    for fill in state["fills"]:
        key = fill["strategy"] + "/" + fill["mint"]
        if fill["side"] == "buy":
            if key in latest:
                closed.append(latest[key])
            latest[key] = 0.0
        elif fill["side"] == "sell":
            latest[key] += fill["pnl"]
        else:
            raise ValueError("Sens d'exécution inconnu")
    closed.extend(value for key, value in latest.items() if key not in state["positions"])
    if len(closed) != len(state["completed"]):
        raise ValueError("Historique des clôtures incomplet")
    return closed


def build_analytics_report(db_path):
    """Win rate is a fraction; drawdown and net return are percentages."""
    state = _read_snapshot(Path(db_path))
    result = {"asset_domain": sim.DOMAIN, "currency": "USD", "paper_only": True}
    if state is None:
        return {**result, "status": "empty"}

    for record in (state, *state["positions"].values(), *state["fills"], *state["completed"]):
        if record.get("asset_domain", sim.DOMAIN) != sim.DOMAIN or record.get("simulated", True) is not True:
            raise ValueError("Session incompatible avec le domaine memecoin simulé")
    snapshot = sim.report(state, {})
    closed = _closed_results(state)
    gains = sum(max(0, pnl) for pnl in closed)
    losses = sum(max(0, -pnl) for pnl in closed)
    initial = snapshot["initial_capital"]
    equity = snapshot["equity"]
    net = None if equity is None else equity - initial
    peak, drawdown = initial, 0.0
    for value in [p["equity"] for p in state["curve"]] + [equity]:
        if value is not None:
            peak = max(peak, value)
            drawdown = max(drawdown, 100 * (peak - value) / peak)
    ranking = snapshot["token_ranking"]
    return {
        **result, "status": "stale" if equity is None else "ok",
        "initial_capital": initial, "equity": equity, "net_pnl": net,
        "net_pnl_pct": None if net is None else 100 * net / initial,
        "closed_trades": len(closed), "win_rate": snapshot["win_rate"],
        "profit_factor": gains / losses if losses else None,
        "gross_profit": gains, "gross_loss": losses, "max_drawdown_pct": drawdown,
        "strategies": snapshot["strategies"],
        "top_tokens": [(mint, pnl) for mint, pnl in ranking if pnl > 0][:5],
        "flop_tokens": [(mint, pnl) for mint, pnl in reversed(ranking) if pnl < 0][:5],
    }


def render_report(report):
    lines = ["Simulation memecoin | USD fictifs"]
    if report["status"] == "empty":
        return lines[0] + "\nAucune session enregistrée."

    def fmt(value):
        return "n/d" if value is None else f"{value:.2f}"

    win = report["win_rate"]
    factor = fmt(report["profit_factor"])
    if report["gross_profit"] and not report["gross_loss"]:
        factor = "infini (aucune perte clôturée)"
    lines.extend([
        f"Capital initial : {fmt(report['initial_capital'])} USD | Équité : {fmt(report['equity'])} USD",
        f"P&L net : {fmt(report['net_pnl'])} USD ({fmt(report['net_pnl_pct'])} %)",
        f"Win rate : {fmt(None if win is None else 100 * win)} % sur {report['closed_trades']} clôtures",
        f"Profit factor : {factor} | Drawdown maximal observé : {fmt(report['max_drawdown_pct'])} %",
        "Stratégies aux dernières valorisations (P&L USD | entrées)",
    ])
    lines.extend(f"{name} : {fmt(stats['pnl'])} | {stats['trades']}" for name, stats in report["strategies"].items())
    for label, key in (("Top", "top_tokens"), ("Flop", "flop_tokens")):
        lines.append(label + " : " + (", ".join(f"{mint} {fmt(pnl)} USD" for mint, pnl in report[key]) or "aucun"))
    if report["status"] == "stale":
        lines.append("Valorisation en attente ; drawdown calculé sur les points disponibles.")
    return "\n".join(lines)
