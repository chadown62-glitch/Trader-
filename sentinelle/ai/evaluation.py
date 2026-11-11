"""Portfolio evidence from full simulated journals. All returns are fractions."""
from datetime import datetime, timezone

import numpy as np


DAY_MS = 86_400_000


def daily_returns(curve, initial):
    """UTC day of observation. A mark at midnight closes the preceding interval."""
    closes = {}
    for stamp, equity in curve:
        closes[(int(stamp) - 1) // DAY_MS] = float(equity)
    if not closes:
        return {}
    last = float(initial)
    returns = {}
    for day in range(min(closes), max(closes) + 1):
        value = closes.get(day, last)
        date = datetime.fromtimestamp(day * 86400, timezone.utc).date().isoformat()
        returns[date] = value / last - 1 if last > 0 else 0.
        last = value
    return returns


def portfolio_metrics(replay):
    initial = replay["initial"]
    closed = replay["closed_positions"]
    pnl = np.asarray([p["realized_pnl"] for p in closed], dtype=float)
    from ..execution.positions import settlement_rate
    bases = np.asarray([p["initial_qty"] * p["entry_price"] * settlement_rate(p["symbol"], replay["currency"], p.get("entry_fx"))
                        + p["entry_fees"] for p in closed])
    eq = np.array([initial] + [v for _, v in replay["equity"]])
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    return {
        "capital_eur": float(eq[-1]), "net_return": float(eq[-1] / initial - 1),
        "n_trades": len(closed), "n_fills": len(replay["fills"]), "refused": replay["agent_stats"]["refused"],
        "net_eur_per_trade": float(pnl.mean()) if len(pnl) else None,
        "mean_net_trade_return": float(np.mean(pnl / bases)) if len(pnl) else None,
        "mean_contribution_to_initial": float(pnl.mean() / initial) if len(pnl) else None,
        "mean_win_eur": float(wins.mean()) if len(wins) else None,
        "mean_loss_eur": float(losses.mean()) if len(losses) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "realized_eur": float(pnl.sum()), "fees_eur": replay["fees_paid"],
        "max_drawdown": float(np.max(1 - eq / np.maximum.accumulate(eq))),
        "daily": daily_returns(replay["equity"], initial),
        "assets": {s: float(sum(p["realized_pnl"] for p in closed if p["symbol"] == s)) for s in sorted({p["symbol"] for p in closed})},
    }


def block_interval(values, *, block=7, seed=701, reps=2000, confidence=.95):
    """Circular moving-block bootstrap of the mean of paired calendar-day returns."""
    x = np.asarray(values, dtype=float)
    if (not np.isfinite(x).all() or block < 1 or reps < 100 or not 0 < confidence < 1 or len(x) < 4 * block):
        return None
    rng = np.random.default_rng(seed)
    draws = np.empty(reps)
    for i in range(reps):
        starts = rng.integers(len(x), size=(len(x) + block - 1) // block)
        idx = (starts[:, None] + np.arange(block)) % len(x)
        draws[i] = x[idx.ravel()[:len(x)]].mean()
    q = (1 - confidence) / 2
    return [float(v) for v in np.quantile(draws, [q, 1-q])]


def compare_portfolios(candidate, reference, *, seed=701, block=7, trials=1):
    """Predesignate one candidate. Both positive absolute performance and paired advantage are required."""
    if sorted(candidate["daily"]) != sorted(reference["daily"]) or trials < 1:
        raise ValueError("Les périodes de comparaison doivent coïncider")
    days = sorted(candidate["daily"])
    x = np.array([candidate["daily"][day] for day in days])
    y = np.array([reference["daily"][day] for day in days])
    confidence = 1 - .05 / (2 * trials)
    difference = block_interval(x-y, block=block, seed=seed, confidence=confidence)
    absolute = block_interval(x, block=block, seed=seed, confidence=confidence)
    enough = candidate["n_trades"] >= 30 and len(days) >= 4 * block
    verdict = "AVANTAGE NET NON DÉMONTRÉ"
    if not enough:
        verdict = "TESTS INCOMPLETS"
    elif difference and difference[1] < 0:
        verdict = "RÉGRESSION DÉTECTÉE"
    elif (difference and absolute and min(difference[0], absolute[0]) > 0 and candidate["net_return"] > 0
          and (candidate["net_eur_per_trade"] or 0) > 0):
        verdict = "MODÈLE APPROUVÉ POUR PAPER TRADING"
    return {"verdict": verdict, "paired_daily_interval": difference, "absolute_daily_interval": absolute,
            "confidence_per_interval": confidence, "days": len(days), "block_days": block, "seed": seed,
            "minimum_closed_trades": 30, "trials": trials}
