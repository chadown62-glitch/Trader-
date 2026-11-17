"""Simulateur causal d'un portefeuille fictif : achat seulement, sans levier, coûts et minimum d'ordre.

Chronologie d'une journée ``t`` (contrat d'exécution du projet) :
1. à la clôture de ``t``, la stratégie reçoit un contexte qui ne contient que les journées ``<= t`` ;
2. ses poids cibles sont exécutés à l'ouverture de ``t + 1``, au prix d'ouverture aggravé du
   demi-écart et du glissement, frais sur chaque ordre ;
3. le portefeuille est valorisé à la clôture de ``t + 1``.

Contraintes : jamais de position négative ; jamais plus d'achats que de liquidités (aucun levier) ;
un ordre sous le minimum de la plateforme n'est pas passé (un reliquat trop petit reste bloqué) ;

Reliquats (mission 52, mesures de COST OPTIMIZER #50) — seulement si les règles de marché sont fournies (`rules`) :
la quantité achetée est arrondie au pas de la plateforme, la commission est prélevée dans l'actif reçu (cas sans
jeton de remise), la conversion est payée en liquidités ; à la vente, seule la part multiple du pas est vendable.
Ce qui reste sous un pas est un RELIQUAT : compté comme PERDU à sa création (règle prudente), retiré du portefeuille
et rapporté à part (`dust_lost`). Sans règles, le comportement est exactement celui d'avant (tests de non-régression).
un actif dont la cotation s'arrête est vendu à sa dernière clôture connue, coûts compris, et
compté. Le dernier jour, les positions restantes sont valorisées nettes de leur coût de sortie.
Avec les règles de marché, une position dont la part vendable reste sous le minimum de la plateforme ne peut
être vendue ni à une fin de cotation ni le dernier jour : elle est comptée comme perdue (mission 54, même
règle prudente que les reliquats ; le simulateur refusait déjà ces ventes les jours de décision).

Analyses de sensibilité (mission 54, descriptives) : ``lag`` exécute à l'ouverture de ``t + 1`` la décision prise
à la clôture de ``t - lag`` (décision retardée) ; ``forced_haircut`` applique une décote au prix de sortie imposée
par une fin de cotation. Valeurs par défaut : comportement inchangé.
"""
from __future__ import annotations

import math
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
    dust_lost: float = 0.0  # valeur des reliquats invendables, comptée comme perdue (modèle de reliquats seulement)
    dust_events: int = 0
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


def _floor(q: float, step: float) -> float:
    return math.floor(q / step + 1e-9) * step if step > 0 else q


def simulate(panel: Panel, spec: Spec, cost: CostScenario, *, start: int, end: int, capital: float = CAPITAL,
             features: Features | None = None, exclude: frozenset = frozenset(), majors: tuple = MAJORS,
             rules: dict | None = None, lag: int = 0, forced_haircut: float = 0.0) -> SimResult:
    """Évalue ``spec`` sur les jours ``start..end`` (inclus). La première décision est prise à la clôture de ``start - 1``
    pour tous (mise en place), les suivantes selon le calendrier de la règle."""
    if not 1 <= start <= end < len(panel):
        raise ValueError("Fenêtre invalide")
    if lag < 0 or not 0 <= forced_haircut < 1:
        raise ValueError("Paramètres de sensibilité invalides")
    if start - 1 - lag < spec.warmup:
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

    def rule(asset: str) -> dict | None:
        return None if rules is None else rules.get(panel.base(asset))

    def min_notional(asset: str) -> float:
        r = rule(asset)
        return max(cost.min_notional, float(r["min_notional"])) if r else cost.min_notional

    def lose_dust(asset: str, price: float) -> None:
        """Ce qui reste sous un pas est invendable : compté comme perdu (prudent) et retiré du portefeuille."""
        left = qty.pop(asset, 0.0)
        info.pop(asset, None)
        if left > 0:
            res.dust_lost += left * price
            res.dust_events += 1
            flows[asset] = flows.get(asset, 0.0)

    def sell(asset: str, amount: float, price: float, forced: bool = False) -> float:
        """Vend ``amount`` unités au prix de référence ``price`` ; renvoie les liquidités reçues."""
        nonlocal cash
        base = panel.base(asset)
        r = rule(asset)
        if r is not None:  # modèle de reliquats : seule la part multiple du pas est vendable, commission puis conversion
            full = amount >= qty[asset] - 1e-15
            amount = _floor(min(amount, qty[asset]), float(r["step"]))
            if amount <= 0 or amount * price < min_notional(asset):
                if forced:  # invendable sous le minimum à une fin de cotation : perdu (prudent)
                    lose_dust(asset, price)
                elif amount <= 0 and full:
                    lose_dust(asset, price)
                else:
                    deny("vente_sous_le_minimum_après_arrondi")
                return 0.0
            gross = amount * price
            net_px = gross * (1 - cost.worse(base))
            got = net_px * (1 - cost.fee_bps / 1e4) * (1 - cost.fx_bps / 1e4)
            res.spread_slippage += gross * cost.worse(base)
            res.fees += net_px - got
            res.turnover += gross / capital
            res.trades += 1
            cash += got
            flows[asset] = flows.get(asset, 0.0) + got
            qty[asset] -= amount
            if full or qty[asset] < float(r["step"]) - 1e-15:
                lose_dust(asset, price)
            return got
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
        d = t - lag  # jour de la décision exécutée à l'ouverture de t + 1 (d = t sans retard)
        if t == start - 1 or is_decision_day(spec, panel, d):  # mise en place à la veille du début, puis calendrier de la règle
            held = {a: {**v, "value": qty[a] * float(panel.close[d, col[a]])} for a, v in info.items()}
            ctx = Context(panel.view(d), feats.age[d], feats.medvol[d], feats.vol90[d], held, exclude, majors)
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
                sell(asset, qty[asset], float(panel.close[t, col[asset]]) * (1 - forced_haircut), forced=True)
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
                    if have >= min_notional(asset):
                        sell(asset, qty[asset], price)
                    else:
                        deny("reliquat_sous_le_minimum")
                elif have - goal >= min_notional(asset):  # allègement : tout ordre au moins égal au minimum est passé
                    sell(asset, (have - goal) / price, price)
            # 2) achats, dans la limite des liquidités
            for asset in sorted(want):
                price = float(opens[col[asset]])
                delta = want[asset] - qty.get(asset, 0.0) * price
                if delta < min_notional(asset):
                    if delta > 0 and asset not in qty:
                        deny("achat_sous_le_minimum")
                    continue
                spend = min(delta, cash / (1 + cost.fee))
                if spend < min_notional(asset):
                    deny("liquidités_insuffisantes")
                    continue
                base = panel.base(asset)
                paid = price * (1 + cost.worse(base))
                r = rule(asset)
                if r is not None:  # modèle de reliquats : quantité arrondie au pas, commission prélevée dans l'actif reçu
                    order = _floor(min(spend, cash / (1 + cost.fx_bps / 1e4)) / paid, float(r["step"]))
                    if order * price < min_notional(asset):
                        deny("achat_sous_le_minimum_apres_arrondi")
                        continue
                    outlay = order * paid
                    received = order * (1 - cost.fee_bps / 1e4)
                    cash -= outlay * (1 + cost.fx_bps / 1e4)
                    res.fees += outlay * cost.fx_bps / 1e4 + order * paid * cost.fee_bps / 1e4
                    res.spread_slippage += order * price * cost.worse(base)
                    res.turnover += outlay / capital
                    res.trades += 1
                    flows[asset] = flows.get(asset, 0.0) - outlay * (1 + cost.fx_bps / 1e4)
                    if asset not in qty:
                        info[asset] = {"days": 0, "entry": paid}
                    qty[asset] = qty.get(asset, 0.0) + received
                    continue
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
            if rules is None:
                invested = sum(q * float(closes[col[a]]) * (1 - cost.worse(panel.base(a))) * (1 - cost.fee) for a, q in qty.items())
            else:  # seule la part vendable compte ; le reliquat final est perdu (rapporté à part)
                invested = 0.0
                for a, q in qty.items():
                    r = rule(a)
                    px = float(closes[col[a]])
                    sellable = _floor(q, float(r["step"])) if r else q
                    if r and sellable * px < min_notional(a):  # invendable sous le minimum : perdu (prudent)
                        sellable = 0.0
                    invested += sellable * px * (1 - cost.worse(panel.base(a))) * (1 - cost.fee_bps / 1e4) * (1 - cost.fx_bps / 1e4)
                    if q - sellable > 0:
                        res.dust_lost += (q - sellable) * px
                        res.dust_events += 1
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
        r = rule(asset)
        sellable = _floor(q, float(r["step"])) if r else q
        if r and sellable * float(final[col[asset]]) < min_notional(asset):
            sellable = 0.0
        tail = (1 - cost.fee) if r is None else (1 - cost.fee_bps / 1e4) * (1 - cost.fx_bps / 1e4)
        flows[asset] = flows.get(asset, 0.0) + sellable * float(final[col[asset]]) * (1 - cost.worse(panel.base(asset))) * tail
    res.by_asset = {a: float(v) for a, v in sorted(flows.items())}
    return res
