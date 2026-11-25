"""Read-only analytics for an isolated memecoin paper session (USD)."""

import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

from ..market.fx import FxQuote, FxUnavailable
from . import memecoin_learner, memecoin_simulator as sim
from .memecoin_strategies import LEGACY


def build_analytics_report(db_path):
    """Read session and optional FX evidence in the same read-only SQLite snapshot."""
    path = Path(db_path)
    if not path.exists():
        return _report(None)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0, isolation_level=None)) as conn:
        conn.execute("BEGIN")
        tables = {name for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "meta" not in tables:
            return _report(None)
        row = conn.execute("SELECT value FROM meta WHERE key=?", (sim.KEY,)).fetchone()
        return _report(json.loads(row[0]) if row else None, conn if "fx_snapshots" in tables else None)


def _available_at(cycle, received_ms):
    if not sim._finite(cycle) or cycle <= 0 or not sim._finite(received_ms) or received_ms <= 0:
        return None
    return max(cycle, received_ms / 1000)


def _fill_at(fill):
    return _available_at(fill.get("ts"), fill.get("execution_received_ms"))


def _euro_value(conn, movements):
    """Contre-valeur du P&L en USD à la date de chaque réalisation ou marque, sans opération de change."""
    if conn is None or not movements:
        return None
    legs = []
    for amount, at in movements:
        if not sim._finite(at) or at <= 0 or not sim._finite(amount):
            return None
        row = conn.execute("SELECT ts,source,snapshot FROM fx_snapshots WHERE base='EUR' AND quote='USD' AND ts<=? "
                           "ORDER BY ts DESC, source LIMIT 1", (at,)).fetchone()
        if not row:
            return None
        try:
            quote = FxQuote(**json.loads(row[2]))
            if (quote.base, quote.quote, quote.ts, quote.source) != ("EUR", "USD", row[0], row[1]):
                return None
            rate, _ = quote.cash_leg(abs(amount), "sell" if amount >= 0 else "buy", at)
            eur = amount * float(rate)
            if not math.isfinite(eur):
                return None
        except (TypeError, ValueError, ArithmeticError, FxUnavailable, KeyError):
            return None
        legs.append({"usd": amount, "event_at": at, "rate": float(rate), "eur": eur,
                     "source": quote.source, "quote_at": quote.ts})
    return {"amount": math.fsum(leg["eur"] for leg in legs), "legs": legs}


RECENT_CLOSED = 20  # closed lots detailed in the report, most recent first


def _pct(part, whole):
    return 100 * part / whole if whole else None


def _mean(values):
    return sum(values) / len(values) if values else None


def _closed_lots(state, conn=None):
    """Closed lots in closing order, each with its realized P&L (USD) and its return (%) on the initial stake."""
    # One lot per strategy/mint. A new buy starts the next round trip;
    # partial exits belong to the same lot until it closes.
    latest = {}
    closed = []
    for fill in state["fills"]:
        key = fill["strategy"] + "/" + fill["mint"]
        if fill["side"] == "buy":
            if key in latest:
                closed.append(latest[key])
            latest[key] = {"key": key, "strategy": fill["strategy"], "mint": fill["mint"], "pnl_usd": 0.0,
                           "opened_at": _fill_at(fill) or fill.get("ts"), "closed_at": None, "exit_reason": None, "exits": 0, "_sales": []}
        elif fill["side"] == "sell":
            lot = latest[key]
            lot["pnl_usd"] += fill["pnl"]
            lot["closed_at"] = _fill_at(fill) or fill.get("ts")
            lot["exit_reason"] = fill.get("reason")
            lot["exits"] += 1
            lot["_sales"].append((fill["pnl"], _fill_at(fill)))
        else:
            raise ValueError("Sens d'exécution inconnu")
    closed.extend(lot for key, lot in latest.items() if key not in state["positions"])
    if len(closed) != len(state["completed"]):
        raise ValueError("Historique des clôtures incomplet")
    # The lots of one strategy/mint are sequential: its k-th closed lot is its k-th completed record,
    # whose return is the lot's whole realized P&L (partial exits included) over its initial stake.
    pending = {}
    for lot in closed:
        pending.setdefault(lot.pop("key"), []).append(lot)
    lots = []
    for record in state["completed"]:
        queue = pending.get(record["strategy"] + "/" + record["mint"])
        if not queue:
            raise ValueError("Historique des clôtures incomplet")
        lots.append({**queue.pop(0), "profit_pct": 100 * record["return"]})
    for i, lot in enumerate(lots):
        sales = lot.pop("_sales")
        if i >= len(lots) - RECENT_CLOSED:
            value = _euro_value(conn, sales)
            if value:
                lot["realized_eur"] = value
    return lots


def _ladder(policy_name):
    """Take-profit multiples of an exit policy, in the order the open lot sells them (None if unknown)."""
    if policy_name == sim.LEGACY_EXITS:
        return tuple(multiple for multiple, _ in LEGACY["tiers"])
    policy = sim.EXIT_POLICIES.get(policy_name)
    return None if policy is None else (policy.cover_ratio, *policy.tiers)


def _tier(pos):
    """Last take-profit multiple sold and the next one. Full exits close the lot, so an open lot's stage counts
    its take-profit slices only."""
    policy_name = pos.get("exit_policy", sim.LEGACY_EXITS)
    ladder, stage = _ladder(policy_name), pos["stage"]
    if ladder is None:
        return policy_name, None, None
    return policy_name, ladder[min(stage, len(ladder)) - 1] if stage else None, ladder[stage] if stage < len(ladder) else None


def _entry_price(buy, initial):
    """Market price at entry: the stake less the recorded DEX fee, network fee and slippage, per token bought."""
    costs = [buy.get(k) for k in ("dex_fee_usd", "network_fee_usd", "slippage_usd")]
    quantity = buy.get("quantity")
    if None in costs or not quantity or quantity <= 0:
        return None  # lot opened before these costs were recorded
    return (initial - sum(costs)) / quantity


def _open_positions(state, conn=None):
    """Open lots at their last liquidation mark: entry and current price, value, unrealized P&L in USD and in % of the
    remaining cost, and the take-profit tier reached."""
    stale = set(state.get("stale_marks", []))
    buys, sales = {}, {}
    for fill in state["fills"]:
        key = fill["strategy"] + "/" + fill["mint"]
        if fill["side"] == "buy":
            buys[key], sales[key] = fill, []
        elif key in sales:
            sales[key].append((fill["pnl"], _fill_at(fill)))
    rows = []
    for key, pos in state["positions"].items():
        last = state.get("assets", {}).get(pos["mint"], {}).get("last") or {}
        buy = buys.get(key, {})
        unrealized = pos["mark"] - pos["basis"]
        policy_name, reached, upcoming = _tier(pos)
        rows.append({
            "strategy": pos["strategy"], "mint": pos["mint"], "quantity": pos["qty"],
            "entry_price_usd": _entry_price(buy, pos["initial"]), "price_usd": last.get("price"),
            "initial_usd": pos["initial"], "cost_basis_usd": pos["basis"], "value_usd": pos["mark"],
            "unrealized_pnl_usd": unrealized, "unrealized_pnl_pct": _pct(unrealized, pos["basis"]),
            "realized_pnl_usd": pos["pnl"], "exits": pos["stage"], "exit_policy": policy_name,
            "tier_reached": reached, "next_tier": upcoming, "peak_ratio": pos.get("high"),
            "marked_at": pos.get("marked_at", last.get("ts") if pos["mint"] not in stale else None),
            "opened_at": sim.opened_at(state, key, pos), "stale": pos["mint"] in stale,
        })
        row = rows[-1]
        received = last.get("received_ms")
        mark_at = None
        if sim._finite(received) and received > 0 and last.get("ts") == row["marked_at"]:
            row["quote_received_at"] = received / 1000
            mark_at = _available_at(row["marked_at"], received)
            row["marked_at"] = mark_at
        row["opened_at"] = _fill_at(buy) or row["opened_at"]
        movements = sales.get(key, [])
        if len(movements) == row["exits"] and math.isclose(sum(pnl for pnl, _ in movements), row["realized_pnl_usd"], abs_tol=1e-10):
            realized = _euro_value(conn, movements)
            if realized:
                row["realized_eur"] = realized
        if not row["stale"]:
            latent = _euro_value(conn, [(unrealized, mark_at)])
            if latent:
                row["unrealized_eur"] = latent
    return rows


def _report(state, conn=None):
    """Win rate is a fraction; drawdown, net return and profits are percentages.

    ``avg_profit_pct`` is the arithmetic mean, over closed lots, of each lot's return on its initial stake (net of
    fees, slippage and network costs, partial exits included); every strategy carries its own. ``open_positions``
    values each open lot at the last mark of the paper worker; ``price_usd`` is its current price, ``tier_reached``
    the last take-profit multiple sold (``None`` before the first slice). ``learning`` holds the bounded allocation
    multipliers and the entry-threshold suggestions of ``memecoin_learner``, read from the same snapshot.
    """
    result = {"asset_domain": sim.DOMAIN, "currency": "USD", "paper_only": True}
    if state is None:
        return {**result, "status": "empty", "avg_profit_pct": None, "open_positions": [], "recent_closed_positions": [],
                "learning": memecoin_learner.learn_from_state(None)}

    for record in (state, *state["positions"].values(), *state["fills"], *state["completed"]):
        if record.get("asset_domain", sim.DOMAIN) != sim.DOMAIN or record.get("simulated", True) is not True:
            raise ValueError("Session incompatible avec le domaine memecoin simulé")
    snapshot = sim.report(state, {})
    lots = _closed_lots(state, conn)
    closed = [lot["pnl_usd"] for lot in lots]
    strategies = snapshot["strategies"]
    for name, stats in strategies.items():
        stats["avg_profit_pct"] = _mean([lot["profit_pct"] for lot in lots if lot["strategy"] == name])
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
    tokens = {}
    for mint, asset in state.get("assets", {}).items():
        point = asset.get("last") or {}
        if point.get("token_name") or point.get("token_symbol"):
            tokens[mint] = {"name": point.get("token_name"), "symbol": point.get("token_symbol"),
                            "source": "DexScreener", "received_ms": point.get("received_ms")}
    ranking = snapshot["token_ranking"]
    return {
        **result, "status": "stale" if equity is None else "ok",
        "initial_capital": initial, "equity": equity, "net_pnl": net,
        "net_pnl_pct": None if net is None else 100 * net / initial,
        "closed_trades": len(closed), "win_rate": snapshot["win_rate"],
        "profit_factor": gains / losses if losses else None,
        "gross_profit": gains, "gross_loss": losses, "max_drawdown_pct": drawdown,
        "avg_profit_pct": _mean([lot["profit_pct"] for lot in lots]), "strategies": strategies,
        "tokens": tokens, "revision": state["revision"], "quote_max_age_s": sim.MAX_QUOTE_AGE_S,
        "open_positions": _open_positions(state, conn), "recent_closed_positions": lots[::-1][:RECENT_CLOSED],
        "learning": memecoin_learner.learn_from_state(state),  # same snapshot: bounded multipliers and suggestions
        "top_tokens": [(mint, pnl) for mint, pnl in ranking if pnl > 0][:5],
        "flop_tokens": [(mint, pnl) for mint, pnl in reversed(ranking) if pnl < 0][:5],
    }


def render_report(report):
    lines = ["Simulation memecoin | USD fictifs"]
    if report["status"] == "empty":
        return lines[0] + "\nAucune session enregistrée."

    def fmt(value):
        return "n/d" if value is None else f"{value:.2f}"

    def price(value):  # memecoin prices often sit far below one cent
        return "n/d" if value is None else f"{value:.6g}"

    def tier(value):
        return "aucun" if value is None else f"{value:g}x".replace(".", ",")

    win = report["win_rate"]
    factor = fmt(report["profit_factor"])
    if report["gross_profit"] and not report["gross_loss"]:
        factor = "infini (aucune perte clôturée)"
    lines.extend([
        f"Capital initial : {fmt(report['initial_capital'])} USD | Équité : {fmt(report['equity'])} USD",
        f"P&L net : {fmt(report['net_pnl'])} USD ({fmt(report['net_pnl_pct'])} %)",
        f"Win rate : {fmt(None if win is None else 100 * win)} % sur {report['closed_trades']} clôtures",
        f"Profit factor : {factor} | Drawdown maximal observé : {fmt(report['max_drawdown_pct'])} %",
        f"Profit moyen par clôture : {fmt(report['avg_profit_pct'])} %",
        "Stratégies aux dernières valorisations (P&L USD | entrées | profit moyen par clôture %)",
    ])
    lines.extend(f"{name} : {fmt(stats['pnl'])} | {stats['trades']} | {fmt(stats['avg_profit_pct'])}"
                 for name, stats in report["strategies"].items())
    lines.append(f"Positions ouvertes : {len(report['open_positions'])}")
    lines.extend(f"{p['strategy']} {p['mint']} : entrée {price(p['entry_price_usd'])} → {price(p['price_usd'])} USD | "
                 f"valeur {fmt(p['value_usd'])} USD | latent {fmt(p['unrealized_pnl_usd'])} USD ({fmt(p['unrealized_pnl_pct'])} %) | "
                 f"palier {tier(p['tier_reached'])}" + (" | cotation en attente" if p["stale"] else "")
                 for p in report["open_positions"])
    for label, key in (("Top", "top_tokens"), ("Flop", "flop_tokens")):
        lines.append(label + " : " + (", ".join(f"{mint} {fmt(pnl)} USD" for mint, pnl in report[key]) or "aucun"))
    if report["status"] == "stale":
        lines.append("Valorisation en attente ; drawdown calculé sur les points disponibles.")
    return "\n".join(lines)
