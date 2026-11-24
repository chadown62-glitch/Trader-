"""Position de fond : « double élan » journalier, la règle d'un gérant qui veut faire mieux que le marché.

Chaque jour à 00:00 UTC, avec les seules clôtures déjà connues (jusqu'à la veille) :
1. élan absolu : une crypto n'est éligible que si son cours est au-dessus de sa moyenne de 20 jours, elle-même
   montante (sinon le capital reste en USDC : on évite les marchés baissiers) ;
2. élan relatif : parmi les éligibles, on détient les 3 plus fortes sur 14 jours (on suit les meneuses).
Une crypto sort quand elle quitte ce groupe ; une place libérée est rachetée avec la trésorerie disponible.
Exécution à l'ouverture de 00:00 UTC, frais BNB et glissement compris à chaque achat et vente.

Protections du profil prudent (variantes v7 et v8), fixées avant mesure :
- filtre de marché : si le BTC n'est pas lui-même en tendance (cours sous sa moyenne de 20 jours ou moyenne
  descendante), rien n'est détenu ;
- stop suiveur : une position est vendue dans la journée si le cours touche 10 % sous son plus haut de clôture depuis
  l'achat (exécution au stop, ou à l'ouverture si elle est déjà plus basse) ; pas de rachat pendant 3 jours.

Stabilité (variantes v9 à v12), fixées avant mesure :
- maintien : une crypto détenue est gardée tant qu'elle reste éligible et parmi les 5 plus fortes ; une nouvelle
  entrée doit être parmi les 3 plus fortes (moins d'allers-retours, donc moins de frais) ;
- tendance de fond : l'éligibilité exige aussi un cours au-dessus de la moyenne de 50 jours (crypto et BTC), pour
  ne pas acheter les faux rebonds d'un marché baissier ;
- volatilité : à l'achat, une place reçoit au plus (capital / 3) × 50 % / volatilité annuelle sur 20 jours ; une crypto
  très agitée reçoit moins, le reste demeure en USDC.
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
    market_filter: str | None = None
    trailing_stop: float | None = None
    cooldown_days: int = 3
    keep_rank: int | None = None
    trend_days: int | None = None
    vol_target: float | None = None
    vol_days: int = 20


def daily_table(minutes_by_symbol: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Ouverture de 00:00 UTC, dernière clôture et plus bas de chaque jour, par crypto (index : numéro du jour)."""
    opens, closes, lows = {}, {}, {}
    for s, m in minutes_by_symbol.items():
        day = m["t"].to_numpy("int64") // DAY_MS
        g = m.assign(day=day).groupby("day")
        opens[s], closes[s], lows[s] = g["o"].first(), g["c"].last(), g["l"].min()
    return pd.DataFrame(opens), pd.DataFrame(closes), pd.DataFrame(lows)


def signals(closes: pd.DataFrame, rules: CoreRules = CoreRules(),
            members: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rang d'élan des cryptos éligibles (NaN sinon) et volatilité annuelle, connus au jour d (clôtures jusqu'à d − 1).

    « members » (facultatif) : univers défini à la date (vrai si la crypto en fait partie à la clôture du jour) ; hors de
    l'univers, une crypto n'est pas classée (donc ni achetée ni gardée). Le filtre de marché n'en dépend pas."""
    ema = closes.ewm(span=rules.ema_days, adjust=False, min_periods=rules.ema_days).mean()
    eligible = (closes > ema) & (ema > ema.shift(1))
    if rules.trend_days:
        slow = closes.ewm(span=rules.trend_days, adjust=False, min_periods=rules.trend_days).mean()
        eligible &= closes > slow
    momentum = closes / closes.shift(rules.lookback_days) - 1
    ranked = eligible if members is None else eligible & members.reindex(index=eligible.index, columns=eligible.columns, fill_value=False).astype(bool)
    rank = momentum.where(ranked).rank(axis=1, ascending=False, method="first")
    if rules.market_filter:
        rank = rank.mul(eligible[rules.market_filter].astype(float).replace(0.0, np.nan), axis=0)
    vol = np.log(closes).diff().rolling(rules.vol_days).std() * np.sqrt(365)
    return rank.shift(1), vol.shift(1)


def targets(closes: pd.DataFrame, rules: CoreRules = CoreRules()) -> pd.DataFrame:
    """Pour chaque jour d : cryptos à détenir, décidées sur les clôtures jusqu'à d − 1 (décalage d'un jour)."""
    rank, _ = signals(closes, rules)
    return (rank <= rules.top).fillna(False).astype(bool)


def run(opens: pd.DataFrame, closes: pd.DataFrame, start_ms: int, end_ms: int, capital: float,
        rules: CoreRules = CoreRules(), lows: pd.DataFrame | None = None, members: pd.DataFrame | None = None) -> dict:
    side_cost = rules.fee_per_side + rules.slippage_per_side
    rank, vol = signals(closes, rules, members)
    keep_limit = rules.keep_rank or rules.top
    days = [d for d in closes.index if start_ms // DAY_MS <= d < -(-end_ms // DAY_MS)]
    cash, units, trades, curve, peak, blocked = capital, {}, [], [], {}, {}
    for d in days:
        r = rank.loc[d]
        free = {s for s in rank.columns if blocked.get(s, -1) < d}
        keep = {s for s in units if s in free and r[s] <= keep_limit}
        target = keep | {s for s in free if r[s] <= rules.top}
        for s in [s for s in units if s not in keep]:
            price = opens.at[d, s]
            if not np.isfinite(price):
                continue
            peak.pop(s, None)
            proceeds = units.pop(s) * price * (1 - side_cost)
            cash += proceeds
            trades.append({"day": int(d), "symbol": s, "side": "vente", "value": proceeds})
        new = sorted((s for s in target if s not in units and np.isfinite(opens.at[d, s])), key=lambda s: r[s])
        slots = rules.top - len(units)
        worth = cash + sum(u * opens.at[d, s] for s, u in units.items() if np.isfinite(opens.at[d, s]))
        for s in new[:max(0, slots)]:
            budget = cash / max(1, slots)
            if rules.vol_target:
                v = vol.at[d, s]
                scale = min(1.0, rules.vol_target / v) if np.isfinite(v) and v > 0 else 0.0
                budget = min(budget, worth / rules.top * scale)
            if budget <= 0:
                continue
            units[s] = budget * (1 - side_cost) / opens.at[d, s]
            cash -= budget
            slots -= 1
            peak[s] = opens.at[d, s]
            trades.append({"day": int(d), "symbol": s, "side": "achat", "value": budget})
        if rules.trailing_stop and lows is not None:
            for s in list(units):
                stop = (1 - rules.trailing_stop) * peak[s]
                if np.isfinite(lows.at[d, s]) and lows.at[d, s] <= stop:
                    proceeds = units.pop(s) * min(stop, opens.at[d, s]) * (1 - side_cost)
                    cash += proceeds
                    blocked[s] = d + rules.cooldown_days
                    trades.append({"day": int(d), "symbol": s, "side": "stop", "value": proceeds})
        for s in units:
            if np.isfinite(closes.at[d, s]):
                peak[s] = max(peak[s], closes.at[d, s])
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
