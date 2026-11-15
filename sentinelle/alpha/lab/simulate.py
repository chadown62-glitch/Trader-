"""Simulateur causal d'un portefeuille fictif : achat seulement, sans levier, coûts et minimum d'ordre.

Chronologie d'une journée ``t`` (contrat d'exécution du projet) :
1. à la clôture de ``t``, la stratégie reçoit un contexte qui ne contient que les journées ``<= t`` ;
2. ses poids cibles sont exécutés à l'ouverture de ``t + 1``, au prix d'ouverture aggravé du
   demi-écart et du glissement, frais sur chaque ordre ;
3. le portefeuille est valorisé à la clôture de ``t + 1``.

Contraintes : jamais de position négative ; jamais plus d'achats que de liquidités (aucun levier) ;
un ordre sous le minimum de la plateforme n'est pas passé (un reliquat trop petit reste bloqué) ;
un actif dont la cotation s'arrête est vendu à sa dernière clôture connue, coûts compris, et
compté. Le dernier jour, les positions restantes sont valorisées nettes de leur coût de sortie.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from . import CAPITAL
from .costs import CostScenario
from .data import MAJORS, Panel
from .strategies import PEG_WINDOW, VOLUME_WINDOW, Context, Spec


class Features:
    """Grandeurs causales du panneau : la ligne ``t`` ne dépend que des lignes ``<= t`` (vérifié par test de troncature)."""

    def __init__(self, panel: Panel, window: int = VOLUME_WINDOW):
        present = np.isfinite(panel.close)
        age = np.zeros(present.shape, dtype=np.int64)
        run = np.zeros(present.shape[1], dtype=np.int64)
        for t in range(len(panel)):
            run = np.where(present[t], run + 1, 0)
            age[t] = run
        med = np.full(present.shape, np.nan)
        if len(panel) >= window:
            for lo in range(0, len(panel) - window + 1, 256):  # par tranches : mémoire bornée
                hi = min(lo + 256, len(panel) - window + 1)
                w = sliding_window_view(panel.qvol[lo:hi + window - 1], window, axis=0)
                med[lo + window - 1:hi + window - 1] = np.median(w, axis=2)
        med[age < window] = np.nan
        # volatilité quotidienne sur 90 rendements, par sommes cumulées (causal) ; NaN tant que l'historique manque
        with np.errstate(invalid="ignore", divide="ignore"):
            r = panel.close[1:] / panel.close[:-1] - 1
        r = np.vstack([np.zeros((1, r.shape[1])), np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)])
        c1, c2 = np.cumsum(r, axis=0), np.cumsum(r * r, axis=0)
        vol = np.full(present.shape, np.nan)
        n = PEG_WINDOW
        if len(panel) > n:
            s1, s2 = c1[n:] - c1[:-n], c2[n:] - c2[:-n]
            vol[n:] = np.sqrt(np.maximum(s2 - s1 * s1 / n, 0.0) / (n - 1))
        vol[age < n + 1] = np.nan
        self.age, self.medvol, self.vol90 = age, med, vol


@dataclass
class SimResult:
    name: str
    days: np.ndarray  # jours évalués
    equity: np.ndarray  # valeur à la clôture de chaque jour évalué
    returns: np.ndarray  # rendement quotidien net
    exposure: np.ndarray  # part investie à la clôture
    initial: float
    trades: int = 0
    turnover: float = 0.0  # montant échangé / capital initial
    fees: float = 0.0  # commissions et conversion
    spread_slippage: float = 0.0  # demi-écart et glissement payés
    blocked: dict = field(default_factory=dict)  # ordres non passés, par motif
    forced_exits: int = 0  # ventes imposées par une fin de cotation
    by_asset: dict = field(default_factory=dict)  # résultat net par actif
    holdings_days: int = 0

    @property
    def net_pnl(self) -> float:
        return float(self.equity[-1] - self.initial) if len(self.equity) else 0.0


def is_decision_day(spec: Spec, panel: Panel, t: int) -> bool:
    """Calendrier des décisions : chaque clôture, la clôture du dimanche, ou la dernière clôture du mois."""
    if spec.rebalance == "daily":
        return True
    if spec.rebalance == "weekly":
        return (int(panel.days[t]) + 3) % 7 == 6
    if spec.rebalance == "monthly":
        return panel.date(t)[:7] != str(np.datetime64(int(panel.days[t]) + 1, "D"))[:7]
    raise ValueError(spec.rebalance)


def simulate(panel: Panel, spec: Spec, cost: CostScenario, *, start: int, end: int, capital: float = CAPITAL,
             features: Features | None = None, exclude: frozenset = frozenset(), majors: tuple = MAJORS) -> SimResult:
    """Évalue ``spec`` sur les jours ``start..end`` (inclus). La première décision est prise à la clôture de ``start - 1``
    pour tous (mise en place), les suivantes selon le calendrier de la règle."""
    if not 1 <= start <= end < len(panel):
        raise ValueError("Fenêtre invalide")
    if start - 1 < spec.warmup:
        raise ValueError(f"Historique insuffisant avant le début pour {spec.name}")
    feats = features or Features(panel)
    col = {a: j for j, a in enumerate(panel.assets)}
    cash = float(capital)
    qty: dict[str, float] = {}
    info: dict[str, dict] = {}
    flows: dict[str, float] = {}
    res = SimResult(spec.name, panel.days[start:end + 1].copy(), np.zeros(end - start + 1), np.zeros(end - start + 1),
                    np.zeros(end - start + 1), capital)
    last = capital

    def sell(asset: str, amount: float, price: float) -> float:
        """Vend ``amount`` unités au prix de référence ``price`` ; renvoie les liquidités reçues."""
        nonlocal cash
        base = panel.base(asset)
        gross = amount * price
        got = gross * (1 - cost.worse(base)) * (1 - cost.fee)
        res.spread_slippage += gross * cost.worse(base)
        res.fees += gross * (1 - cost.worse(base)) * cost.fee
        res.turnover += gross / capital
        res.trades += 1
        cash += got
        flows[asset] = flows.get(asset, 0.0) + got
        qty[asset] -= amount
        if qty[asset] <= 1e-15:
            del qty[asset], info[asset]
        return got

    def deny(reason: str) -> None:
        res.blocked[reason] = res.blocked.get(reason, 0) + 1

    for t in range(start - 1, end):
        nxt = t + 1
        target = None
        if t == start - 1 or is_decision_day(spec, panel, t):  # mise en place à la veille du début, puis calendrier de la règle
            held = {a: {**v, "value": qty[a] * float(panel.close[t, col[a]])} for a, v in info.items()}
            ctx = Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t], held, exclude, majors)
            target = spec.fn(ctx, spec.params)
            total = sum(target.values())
            if any((not np.isfinite(w)) or w < 0 for w in target.values()) or total > 1 + 1e-9:
                raise ValueError(f"{spec.name} : poids interdits (négatifs ou somme > 1)")
            if any(a not in col for a in target):
                raise ValueError(f"{spec.name} : actif inconnu")
        opens = panel.open[nxt]
        # 0) fin de cotation : vente à la dernière clôture connue (on ne détient jamais un actif sans prix)
        for asset in sorted(qty):
            if not np.isfinite(opens[col[asset]]):
                sell(asset, qty[asset], float(panel.close[t, col[asset]]))
                res.forced_exits += 1
        if target is not None:
            value = cash + sum(q * float(opens[col[a]]) for a, q in qty.items())
            want = {a: w * value for a, w in target.items() if np.isfinite(opens[col[a]])}
            if len(want) < len(target):
                deny("actif_sans_cotation_le_lendemain")
            # 1) ventes d'abord (elles libèrent les liquidités des achats)
            for asset in sorted(qty):
                price = float(opens[col[asset]])
                have, goal = qty[asset] * price, want.get(asset, 0.0)
                if goal <= 0:
                    if have >= cost.min_notional:
                        sell(asset, qty[asset], price)
                    else:
                        deny("reliquat_sous_le_minimum")
                elif have - goal >= cost.min_notional and goal >= cost.min_notional:
                    sell(asset, (have - goal) / price, price)
            # 2) achats, dans la limite des liquidités
            for asset in sorted(want):
                price = float(opens[col[asset]])
                delta = want[asset] - qty.get(asset, 0.0) * price
                if delta < cost.min_notional:
                    if delta > 0 and asset not in qty:
                        deny("achat_sous_le_minimum")
                    continue
                spend = min(delta, cash / (1 + cost.fee))
                if spend < cost.min_notional:
                    deny("liquidités_insuffisantes")
                    continue
                base = panel.base(asset)
                paid = price * (1 + cost.worse(base))
                bought = spend / paid
                cash -= spend * (1 + cost.fee)
                res.fees += spend * cost.fee
                res.spread_slippage += bought * price * cost.worse(base)
                res.turnover += spend / capital
                res.trades += 1
                flows[asset] = flows.get(asset, 0.0) - spend * (1 + cost.fee)
                if asset not in qty:
                    info[asset] = {"days": 0, "entry": paid}
                qty[asset] = qty.get(asset, 0.0) + bought
            if cash < -1e-9:
                raise AssertionError("Liquidités négatives : levier interdit")
        # 3) valorisation à la clôture
        closes = panel.close[nxt]
        invested = sum(q * float(closes[col[a]]) for a, q in qty.items())
        if nxt == end:  # dernier jour : valeur nette du coût de sortie des positions restantes
            invested = sum(q * float(closes[col[a]]) * (1 - cost.worse(panel.base(a))) * (1 - cost.fee) for a, q in qty.items())
        equity = cash + invested
        k = nxt - start
        res.equity[k], res.returns[k] = equity, equity / last - 1
        res.exposure[k] = invested / equity if equity > 0 else 0.0
        res.holdings_days += len(qty)
        last = equity
        for v in info.values():
            v["days"] += 1
    final = panel.close[end]
    for asset, q in qty.items():
        flows[asset] = flows.get(asset, 0.0) + q * float(final[col[asset]]) * (1 - cost.worse(panel.base(asset))) * (1 - cost.fee)
    res.by_asset = {a: float(v) for a, v in sorted(flows.items())}
    return res
