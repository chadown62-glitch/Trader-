"""Exécution simulée des plans (contrat d'exécution du projet) et portefeuille de 30 € fictifs.

Aucun courtier : uniquement les formules de ``sentinelle.execution.contract`` (fonctions pures).
Règles reprises : décision à la clôture de ``i`` ; entrée au premier prix suivant (ouverture
``i+1``) aggravé du demi-écart et du glissement ; entrée abandonnée si ce prix est déjà sous le
stop ; stop prioritaire sur l'objectif dans la même bougie ; stop exécuté au pire du stop et
de l'ouverture ; sorties de règle et de durée décidées à la clôture, exécutées à l'ouverture
suivante ; frais sur chaque ordre. Un résultat n'est « disponible » qu'à la clôture de la bougie
de sortie (``available_at``) : avant, c'est une issue inconnue.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..execution.contract import entry_possible, limit_fill, market_fill, stop_fill, valid_bar
from .bars import Bars, Indicators


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001  # 0,1 % par ordre (hypothèse du projet)
    slippage_bps: float = 5.0  # glissement des ordres au marché et des stops
    default_spread_bps: float = 4.0  # si la bougie ne fournit pas d'écart observé
    fx_bps: float = 0.0  # conversion vers l'euro par ordre (0 si la paire est cotée en EUR)

    @property
    def slip(self) -> float:
        return self.slippage_bps / 1e4

    def half_spread(self, b: Bars, j: int) -> float:
        s = float(b.spread_bps[j]) if b.spread_bps is not None else self.default_spread_bps
        return max(0.0, s) / 2e4

    def stressed(self, factor: float) -> "CostModel":
        """« Et si le carnet est faux ? » : écart et glissement multipliés."""
        return CostModel(self.fee_rate, self.slippage_bps * factor, self.default_spread_bps * factor, self.fx_bps)

    @property
    def fee(self) -> float:
        return self.fee_rate + self.fx_bps / 1e4


@dataclass(frozen=True)
class Plan:
    """Plan inerte : il décrit une entrée possible, il ne peut rien exécuter."""
    teacher: str
    stop_price: float
    max_bars: int
    target_price: float | None = None
    trail_atr: float | None = None
    exit_rule: str | None = None
    exit_variant: str = "teacher"


@dataclass(frozen=True)
class Outcome:
    status: str  # filled | abandoned | unresolved | invalid
    decided_at: int
    available_at: int | None = None
    entry_index: int | None = None
    exit_index: int | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    stop_price: float | None = None
    exit_reason: str | None = None
    gross_r: float | None = None
    cost_r: float | None = None
    net_r: float | None = None
    net_return: float | None = None  # rendement net du montant engagé, frais compris
    bars_held: int | None = None

    @property
    def resolved(self) -> bool:
        return self.status in ("filled", "abandoned")


def simulate_trade(b: Bars, ind: Indicators, i: int, plan: Plan, cost: CostModel,
                   exit_signal: np.ndarray | None = None, *, horizon_end: int | None = None) -> Outcome:
    """Issue du plan décidé à la clôture de ``i``. Ne lit les bougies ``> i`` que pour l'issue (étiquette)."""
    n = len(b) if horizon_end is None else min(len(b), horizon_end)
    decided = b.close_time(i)
    if i + 1 >= n:
        return Outcome("unresolved", decided)
    o = float(b.open[i + 1])
    if not valid_bar(o, b.high[i + 1], b.low[i + 1], b.close[i + 1]) or not math.isfinite(plan.stop_price):
        return Outcome("invalid", decided)
    if not entry_possible(o, plan.stop_price):
        return Outcome("abandoned", decided, available_at=int(b.ts[i + 1]), exit_reason="ouverture déjà sous le stop", net_r=0.0, net_return=0.0)
    fill = market_fill(o, "buy", cost.slip, cost.half_spread(b, i + 1))
    risk = fill - plan.stop_price
    if risk <= 0:
        return Outcome("abandoned", decided, available_at=int(b.ts[i + 1]), exit_reason="glissement au-delà du stop", net_r=0.0, net_return=0.0)
    stop = plan.stop_price
    best_close = fill
    exit_px = raw_exit = reason = j_exit = None
    for j in range(i + 1, n):
        oj, hj, lj, cj = float(b.open[j]), float(b.high[j]), float(b.low[j]), float(b.close[j])
        if not valid_bar(oj, hj, lj, cj):
            continue
        if lj <= stop:  # stop prioritaire, exécuté au pire du stop et de l'ouverture
            raw_exit, exit_px, reason, j_exit = min(stop, oj), stop_fill(stop, oj, "sell", cost.slip), "stop", j
            break
        if plan.target_price is not None and hj >= plan.target_price:
            raw_exit = exit_px = limit_fill(plan.target_price, oj, "sell")
            reason, j_exit = "objectif", j
            break
        best_close = max(best_close, cj)
        rule = exit_signal is not None and bool(exit_signal[j])
        if rule or j - i >= plan.max_bars:
            if j + 1 >= n:
                break
            raw_exit = float(b.open[j + 1])
            exit_px = market_fill(raw_exit, "sell", cost.slip, cost.half_spread(b, j + 1))
            reason, j_exit = ("règle de sortie" if rule else "durée maximale"), j + 1
            break
        if plan.trail_atr is not None and math.isfinite(ind.atr14[j]):
            stop = max(stop, best_close - plan.trail_atr * float(ind.atr14[j]))  # actif dès la bougie suivante
    if exit_px is None:
        return Outcome("unresolved", decided, entry_index=i + 1, entry_price=fill, stop_price=plan.stop_price)
    fees = cost.fee * (fill + exit_px)
    gross_r = (raw_exit - o) / risk  # mouvement du marché, avant tout coût
    cost_r = (fees + (fill - o) + (raw_exit - exit_px)) / risk  # frais + demi-écart + glissement
    net_r = (exit_px - fill - fees) / risk  # = gross_r - cost_r
    net_return = (exit_px * (1 - cost.fee)) / (fill * (1 + cost.fee)) - 1
    return Outcome("filled", decided, available_at=b.close_time(j_exit), entry_index=i + 1, exit_index=j_exit,
                   entry_price=fill, exit_price=exit_px, stop_price=plan.stop_price, exit_reason=reason,
                   gross_r=gross_r, cost_r=cost_r, net_r=net_r, net_return=net_return, bars_held=j_exit - i)


# ---------------------------------------------------------------------------
# Portefeuille de 30 € fictifs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioRules:
    capital: float = 30.0
    risk_frac: float = 0.005  # 0,5 % du capital risqué par trade
    min_order_max_risk: float = 0.01  # relèvement au minimum de la plateforme si le risque reste ≤ 1 %
    min_notional: float = 5.0
    max_positions: int = 3
    max_position_frac: float = 0.34


@dataclass
class PortfolioResult:
    equity: list[tuple[int, float]]
    trades: list[dict]
    refused: dict[str, int]
    fees: float
    turnover: float
    final: float
    initial: float
    extra: dict = field(default_factory=dict)

    @property
    def net_pnl(self) -> float:
        return self.final - self.initial

    def daily_returns(self) -> dict[int, float]:
        closes: dict[int, float] = {}
        for t, v in self.equity:
            closes[(t - 1) // 86_400_000] = v
        out, last = {}, self.initial
        for d in sorted(closes):
            out[d] = closes[d] / last - 1 if last > 0 else 0.0
            last = closes[d]
        return out

    def max_drawdown(self) -> float:
        eq = np.array([self.initial] + [v for _, v in self.equity])
        return float(np.max(1 - eq / np.maximum.accumulate(eq))) if len(eq) > 1 else 0.0


def run_portfolio(universe, intents: list[tuple[str, int, Plan, Outcome]], *, start: int, end: int,
                  rules: PortfolioRules = PortfolioRules(), cost: CostModel = CostModel()) -> PortfolioResult:
    """Rejoue des intentions d'entrée (symbole, indice de décision, plan, issue) sur [start, end).

    Un seul portefeuille, aucune position à découvert, aucun levier : taille par le risque, plafonds,
    minimum de la plateforme, liquidités disponibles. Les positions encore ouvertes à ``end`` sont
    valorisées à la dernière clôture (résultat latent, frais de sortie estimés déduits).
    """
    ts = universe.ts
    entries: dict[int, list] = {}
    for sym, i, plan, out in intents:
        if start <= i < end and out.status == "filled" and out.entry_index < end:
            entries.setdefault(out.entry_index, []).append((sym, i, plan, out))
    cash, fees, traded = rules.capital, 0.0, 0.0
    open_pos: list[dict] = []
    closed: list[dict] = []
    refused: dict[str, int] = {}
    equity: list[tuple[int, float]] = []

    def value(k: int) -> float:
        return cash + sum(p["qty"] * float(universe.bars[p["symbol"]].close[k]) * (1 - cost.fee) for p in open_pos)

    for k in range(start, end):
        # 1) entrées à l'ouverture de cette bougie (premier prix après la décision)
        for sym, i, plan, out in sorted(entries.get(k, []), key=lambda x: (x[0], x[1])):
            def deny(reason: str) -> None:
                refused[reason] = refused.get(reason, 0) + 1
            if any(p["symbol"] == sym for p in open_pos):
                deny("position_déjà_ouverte")
                continue
            if len(open_pos) >= rules.max_positions:
                deny("trop_de_positions")
                continue
            eq = value(k - 1 if k > start else k)  # dernière valeur connue avant l'ouverture
            risk_unit = (out.entry_price - out.stop_price) / out.entry_price
            notional = min(rules.risk_frac * eq / risk_unit, rules.max_position_frac * eq)
            if notional < rules.min_notional:
                if rules.min_notional * risk_unit <= rules.min_order_max_risk * eq:
                    notional = rules.min_notional
                else:
                    deny("sous_le_minimum_risque_trop_grand")
                    continue
            if notional * (1 + cost.fee) > cash:
                deny("liquidités_insuffisantes")
                continue
            qty = notional / out.entry_price
            fee = notional * cost.fee
            cash -= notional + fee
            fees += fee
            traded += notional
            open_pos.append({"symbol": sym, "decision_index": i, "teacher": plan.teacher, "qty": qty,
                             "cost": notional + fee, "out": out, "entry_ts": int(ts[k]), "notional": notional})
        # 2) sorties pendant cette bougie (y compris un stop touché dès la bougie d'entrée) ;
        #    l'argent libéré ne sert qu'aux entrées des bougies suivantes (hypothèse prudente)
        for p in [p for p in open_pos if p["out"].exit_index == k]:
            proceeds = p["qty"] * p["out"].exit_price
            fee = proceeds * cost.fee
            cash += proceeds - fee
            fees += fee
            traded += proceeds
            p.update(exit_ts=int(ts[k]), pnl=proceeds - fee - p["cost"])
            closed.append(p)
            open_pos.remove(p)
        equity.append((int(ts[k]) + universe.bars[universe.symbols[0]].tf_ms, value(k)))
    for p in open_pos:
        p.update(exit_ts=None, pnl=p["qty"] * float(universe.bars[p["symbol"]].close[end - 1]) * (1 - cost.fee) - p["cost"], latent=True)
    final = equity[-1][1] if equity else rules.capital
    trades = [{k: v for k, v in p.items() if k != "out"} | {"net_r": p["out"].net_r, "exit_reason": p["out"].exit_reason}
              for p in closed + open_pos]
    avg_eq = float(np.mean([v for _, v in equity])) if equity else rules.capital
    return PortfolioResult(equity, trades, refused, fees, traded / avg_eq if avg_eq > 0 else 0.0, final, rules.capital)
