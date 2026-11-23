"""Position de fond : « double élan » journalier, la règle d'un gérant qui veut faire mieux que le marché.

Chaque jour à 00:00 UTC, avec les seules clôtures déjà connues (jusqu'à la veille) :
1. élan absolu : une crypto n'est éligible que si son cours est au-dessus de sa moyenne de 20 jours, elle-même
   montante (sinon le capital reste en USDC : on évite les marchés baissiers) ;
2. élan relatif : parmi les éligibles, on détient les 3 plus fortes sur 14 jours (on suit les meneuses).
Une crypto sort quand elle quitte ce groupe ; une place libérée est rachetée avec la trésorerie disponible.
Exécution à l'ouverture de 00:00 UTC, frais BNB et glissement compris à chaque achat et vente.
Paramètres fixés avant toute mesure (research/scalp-v1/trials.jsonl) ; aucun levier, aucune vente à découvert.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

DAY_MS = 86_400_000


@dataclass(frozen=True)
class CoreRules:
    lookback_days: int = 14
    top: int = 3
    ema_days: int = 20
    fee_per_side: float = 0.00075
    slippage_per_side: float = 0.0001


def daily_table(minutes_by_symbol: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ouverture de 00:00 UTC et dernière clôture de chaque jour, par crypto (index : numéro du jour)."""
    opens, closes = {}, {}
    for s, m in minutes_by_symbol.items():
        day = m["t"].to_numpy("int64") // DAY_MS
        g = m.assign(day=day).groupby("day")
        opens[s] = g["o"].first()
        closes[s] = g["c"].last()
    return pd.DataFrame(opens), pd.DataFrame(closes)


def targets(closes: pd.DataFrame, rules: CoreRules = CoreRules()) -> pd.DataFrame:
    """Pour chaque jour d : cryptos à détenir, décidées sur les clôtures jusqu'à d − 1 (décalage d'un jour)."""
    ema = closes.ewm(span=rules.ema_days, adjust=False, min_periods=rules.ema_days).mean()
    eligible = (closes > ema) & (ema > ema.shift(1))
    momentum = closes / closes.shift(rules.lookback_days) - 1
    score = momentum.where(eligible)
    rank = score.rank(axis=1, ascending=False, method="first")
    hold = (rank <= rules.top) & score.notna()
    return hold.shift(1, fill_value=False).astype(bool)


def run(opens: pd.DataFrame, closes: pd.DataFrame, start_ms: int, end_ms: int, capital: float,
        rules: CoreRules = CoreRules()) -> dict:
    side_cost = rules.fee_per_side + rules.slippage_per_side
    want = targets(closes, rules)
    days = [d for d in closes.index if start_ms // DAY_MS <= d < -(-end_ms // DAY_MS)]
    cash, units, trades, curve = capital, {}, [], []
    for d in days:
        target = {s for s in want.columns if want.at[d, s]}
        for s in [s for s in units if s not in target]:
            price = opens.at[d, s]
            if not np.isfinite(price):
                continue
            proceeds = units.pop(s) * price * (1 - side_cost)
            cash += proceeds
            trades.append({"day": int(d), "symbol": s, "side": "vente", "value": proceeds})
        new = [s for s in sorted(target) if s not in units and np.isfinite(opens.at[d, s])]
        slots = rules.top - len(units)
        for s in new[:max(0, slots)]:
            budget = cash / max(1, slots)
            units[s] = budget * (1 - side_cost) / opens.at[d, s]
            cash -= budget
            slots -= 1
            trades.append({"day": int(d), "symbol": s, "side": "achat", "value": budget})
        value = cash + sum(u * closes.at[d, s] for s, u in units.items() if np.isfinite(closes.at[d, s]))
        curve.append(((int(d) + 1) * DAY_MS, value, sorted(units)))
    last = curve[-1][1] if curve else capital
    eq = np.array([capital] + [v for _, v, _ in curve])
    invested = np.mean([len(h) > 0 for _, _, h in curve]) if curve else 0.0
    return {"rules": asdict(rules), "capital": capital, "final_equity": float(last), "trades": trades,
            "curve": [(t, v) for t, v, _ in curve], "holdings": [(t, h) for t, _, h in curve],
            "metrics": {"return_pct": 100 * (last / capital - 1), "orders": len(trades),
                        "max_drawdown_pct": float(100 * np.max(1 - eq / np.maximum.accumulate(eq))),
                        "days_invested_share": float(invested)}}
