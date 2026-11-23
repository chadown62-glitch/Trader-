"""Pupitre de risque et portefeuille fictif : la discipline d'un trader professionnel, profil prudent.

- Taille : 0,5 % du capital risqué par trade (distance au stop), au plus 30 % du capital par position, jamais de
  levier (la somme des positions ne dépasse pas le capital).
- Au plus 3 positions ouvertes, une seule par crypto.
- Perte du jour de 1,5 % atteinte : plus aucune entrée avant le jour suivant (UTC).
- 3 pertes d'affilée : pause de 12 heures (pas de « revanche ») ; un trade au point mort ne compte pas.
Les décisions de l'IA principale passent toutes par ce pupitre, qui a le dernier mot.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

DAY_MS, WEEK_MS = 86_400_000, 7 * 86_400_000


@dataclass(frozen=True)
class Profile:
    name: str = "prudent"
    risk_per_trade: float = 0.005
    max_open: int = 3
    max_position: float = 0.30
    daily_loss_limit: float = 0.015
    cooldown_losses: int = 3
    cooldown_ms: int = 12 * 3_600_000


def run(cands: pd.DataFrame, take: np.ndarray, start_ms: int, end_ms: int, profile: Profile = Profile(),
        capital: float = 1000.0) -> dict:
    """Rejoue chronologiquement les alertes de la période ; « take » est l'avis de l'IA (ou tout True)."""
    c = cands.assign(take=take)
    c = c[(c["decided_at"] >= start_ms) & (c["decided_at"] < end_ms)].sort_values("decided_at")
    equity, open_pos, trades, refusals = capital, [], [], {}
    day, day_start_equity, streak, pause_until = None, capital, 0, -1
    curve = [(start_ms, capital)]

    def close_until(t):
        nonlocal equity, streak, pause_until
        for p in sorted([p for p in open_pos if p["exit_ms"] <= t], key=lambda p: p["exit_ms"]):
            open_pos.remove(p)
            equity += p["pnl"]
            curve.append((p["exit_ms"], equity))
            if p["pnl"] < 0:
                streak += 1
            elif p["pnl"] > 0:
                streak = 0  # un trade au point mort n'est ni un gain ni une perte
            if streak >= profile.cooldown_losses:
                pause_until, streak = p["exit_ms"] + profile.cooldown_ms, 0

    def refuse(reason):
        refusals[reason] = refusals.get(reason, 0) + 1

    for row in c.itertuples():
        t = row.decided_at
        close_until(t)
        if day != t // DAY_MS:
            day, day_start_equity = t // DAY_MS, equity
        if not row.take:
            refuse("abstention de l'IA principale")
            continue
        if any(p["symbol"] == row.symbol for p in open_pos):
            refuse("position déjà ouverte sur cette crypto")
            continue
        if len(open_pos) >= profile.max_open:
            refuse("nombre maximal de positions")
            continue
        realized_today = equity - day_start_equity
        if realized_today <= -profile.daily_loss_limit * day_start_equity:
            refuse("perte maximale du jour atteinte")
            continue
        if t < pause_until:
            refuse("pause après 3 pertes d'affilée")
            continue
        committed = sum(p["notional"] for p in open_pos)
        notional = min(profile.risk_per_trade * equity / row.risk_pct, profile.max_position * equity, equity - committed)
        if notional < 10:
            refuse("capital disponible insuffisant")
            continue
        pos = {"symbol": row.symbol, "setup": row.setup, "decided_at": t, "exit_ms": row.exit_ms, "notional": notional,
               "net_return": row.net_return, "r": row.r, "outcome": row.outcome, "pnl": notional * row.net_return,
               "reasoning": getattr(row, "reasoning", "")}
        open_pos.append(pos)
        trades.append(pos)
    close_until(10**15)
    return {"profile": asdict(profile), "capital": capital, "final_equity": equity, "trades": trades,
            "refusals": refusals, "curve": curve, "metrics": metrics(trades, curve, capital, start_ms, end_ms)}


def metrics(trades, curve, capital, start_ms, end_ms) -> dict:
    eq = np.array([e for _, e in curve])
    peak = np.maximum.accumulate(eq)
    pnl = np.array([t["pnl"] for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    weeks = {}
    for t in trades:
        k = (t["exit_ms"] - start_ms) // WEEK_MS
        weeks[k] = weeks.get(k, 0) + t["pnl"]
    n_weeks = max(1, int(np.ceil((end_ms - start_ms) / WEEK_MS)))
    by_setup = {}
    for t in trades:
        s = by_setup.setdefault(t["setup"], {"trades": 0, "wins": 0, "pnl": 0.0})
        s["trades"] += 1
        s["wins"] += int(t["pnl"] > 0)
        s["pnl"] += t["pnl"]
    return {
        "return_pct": 100 * (eq[-1] / capital - 1),
        "trades": len(trades),
        "trades_per_week": len(trades) / max(1e-9, (end_ms - start_ms) / WEEK_MS),
        "win_rate": float(len(wins) / len(pnl)) if len(pnl) else None,
        "breakeven_trades": int((pnl == 0).sum()),
        "avg_win_pct_equity": float(100 * wins.mean() / capital) if len(wins) else None,
        "avg_loss_pct_equity": float(100 * losses.mean() / capital) if len(losses) else None,
        "avg_trade_r": float(np.mean([t["r"] for t in trades])) if trades else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "max_drawdown_pct": float(100 * np.max(1 - eq / peak)),
        "positive_weeks": f"{sum(1 for v in weeks.values() if v > 0)}/{n_weeks}",
        "by_setup": by_setup,
    }
