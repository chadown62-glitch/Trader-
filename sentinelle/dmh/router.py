"""Smart Execution Router : compare les routes d'une opération fictive sur leur résultat net APRÈS tous les coûts.

Principes :
- une route est toujours chiffrée ALLER ET RETOUR (entrée + sortie) : une comparaison sans sortie est refusée ;
- chaque composante a sa valeur centrale, une fourchette (bas, haut) et son niveau de preuve ; les totaux
  additionnent les bornes (prudent) ;
- aucune route n'est supposée moins chère par principe (pas plus un réseau décentralisé qu'une plateforme) :
  pour 5 à 30 €, les frais fixes (retraits, gaz, dépôts) peuvent rendre une route absurde, et le calcul le montre ;
- le choix se fait sur le résultat net attendu = avantage brut annoncé (hypothèse de la stratégie, jamais
  inventé ici) − coût aller-retour ; un ordre à cours limité compte ses non-exécutions (occasion manquée) ;
- si aucune route n'est fiable ou rentable : ABSTENTION.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..costs.evidence import weakest
from . import assumptions as A
from .cex import Rules
from .net import SourceUnavailable
from .observe import Book, dex_vs_reference
from .tokens import TokenId, TokenRegistry
from .view import MarketView


class RouteError(ValueError):
    pass


KINDS = ("frais", "immobilise", "risque")
SCENARIOS = ("central", "commissions_basses", "commissions_hautes", "defavorable")


@dataclass(frozen=True)
class Cost:
    """Une composante : `kind` = frais réellement payés, capital immobilisé (reliquat, provision) ou risque (espérance).

    `unverified_fee` : commission ou frais de retrait HYPOTHÉTIQUES (aucune grille vérifiable) ; les décisions qui en
    dépendent sont signalées.
    """
    name: str
    eur: float
    low: float
    high: float
    level: str
    source: str
    note: str = ""
    kind: str = "frais"
    unverified_fee: bool = False

    def __post_init__(self):
        if not all(math.isfinite(x) for x in (self.eur, self.low, self.high)) or not self.low <= self.eur <= self.high + 1e-12:
            raise RouteError(f"composante de coût incohérente : {self.name} ({self.low}, {self.eur}, {self.high})")
        if self.kind not in KINDS:
            raise RouteError(f"nature de coût inconnue : {self.kind}")

    def at(self, scenario: str) -> float:
        """Valeur de la composante dans un scénario : les commissions non vérifiées seules à leurs bornes, ou tout au pire."""
        if scenario == "central":
            return self.eur
        if scenario == "defavorable":
            return self.high
        if scenario == "commissions_basses":
            return self.low if self.unverified_fee else self.eur
        if scenario == "commissions_hautes":
            return self.high if self.unverified_fee else self.eur
        raise RouteError(f"scénario inconnu : {scenario}")

    def to_dict(self):
        return {"name": self.name, "eur": self.eur, "low": self.low, "high": self.high, "level": self.level, "source": self.source,
                "note": self.note, "kind": self.kind, "unverified_fee": self.unverified_fee}


def cost(name, eur, low=None, high=None, *, level, source, note="", kind="frais", unverified_fee=None) -> Cost:
    lo = eur if low is None else low
    hi = eur if high is None else high
    unv = (level == "HYPOTHETICAL" and kind == "frais") if unverified_fee is None else unverified_fee
    return Cost(name, float(eur), float(min(lo, eur)), float(max(hi, eur)), level, source, note, kind, bool(unv))


@dataclass
class Leg:
    step: str
    kind: str  # cex | dex | transfer
    venue: str
    network: str | None
    side: str  # buy | sell | transfer
    asset_in: str
    loc_in: str
    qty_in: float
    asset_out: str
    loc_out: str
    qty_out: float
    eur_in: float
    costs: list[Cost]
    fill_probability: float = 1.0
    failure: tuple[float, float, float] = (0.0, 0.0, 0.0)
    latency_ms: tuple[int, int] = (0, 0)
    dust_eur: float = 0.0
    deposit_eur: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def total(self) -> tuple[float, float, float]:
        return (sum(c.eur for c in self.costs), sum(c.low for c in self.costs), sum(c.high for c in self.costs))

    def to_dict(self):
        c, lo, hi = self.total()
        return {"step": self.step, "kind": self.kind, "venue": self.venue, "network": self.network, "side": self.side,
                "asset_in": self.asset_in, "loc_in": self.loc_in, "qty_in": self.qty_in, "asset_out": self.asset_out,
                "loc_out": self.loc_out, "qty_out": self.qty_out, "eur_in": self.eur_in, "costs": [x.to_dict() for x in self.costs],
                "cost_eur": c, "cost_low": lo, "cost_high": hi, "fill_probability": self.fill_probability,
                "failure": list(self.failure), "latency_ms": list(self.latency_ms), "dust_eur": self.dust_eur,
                "deposit_eur": self.deposit_eur, "evidence": self.evidence, "data": self.data}


@dataclass
class Route:
    id: str
    label: str
    kind: str  # cex_direct | cex_via_stable | dex_funded | dex_onchain | cex_limit
    notional_eur: float
    entry: list[Leg]
    exit: list[Leg] | None
    notes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # motifs d'inéligibilité

    @property
    def legs(self) -> list[Leg]:
        return self.entry + (self.exit or [])

    def totals(self) -> dict:
        if self.exit is None:
            raise RouteError("route sans sortie : comparaison refusée (un devis qui ignore la sortie surestime le gain)")
        c = lo = hi = 0.0
        for leg in self.legs:
            a, b, d = leg.total()
            c, lo, hi = c + a, lo + b, hi + d
        n = self.notional_eur
        fill = 1.0
        for leg in self.entry:
            fill *= leg.fill_probability
        fail = 1.0 - math.prod(1 - leg.failure[0] for leg in self.legs)
        levels = [x.level for leg in self.legs for x in leg.costs] or ["HYPOTHETICAL"]
        comps = [x for leg in self.legs for x in leg.costs]
        by_kind = {k: sum(x.eur for x in comps if x.kind == k) for k in KINDS}
        deposits = sum(leg.deposit_eur for leg in self.legs)
        immobilized = by_kind["immobilise"] + deposits
        scen = {name: sum(x.at(name) for x in comps) for name in SCENARIOS}
        unverified = sum(x.eur for x in comps if x.unverified_fee)
        return {"cost_eur": c, "cost_low_eur": lo, "cost_high_eur": hi, "cost_bps": c / n * 1e4, "cost_low_bps": lo / n * 1e4,
                "cost_high_bps": hi / n * 1e4, "entry_fill_probability": fill, "failure_probability": fail,
                "latency_ms": sum(leg.latency_ms[0] for leg in self.entry), "finality_ms": max((leg.latency_ms[1] for leg in self.entry), default=0),
                "dust_eur": sum(leg.dust_eur for leg in self.legs), "deposit_eur": deposits,
                "fees_paid_eur": by_kind["frais"], "immobilized_eur": immobilized, "immobilized_pct": immobilized / n * 100,
                "risk_eur": by_kind["risque"], "unverified_fees_eur": unverified,
                "unverified_fee_share": unverified / c if c > 0 else 0.0,
                "scenarios_eur": scen, "scenarios_bps": {k: v / n * 1e4 for k, v in scen.items()},
                "level": weakest(levels)}

    def net(self, edge_bps: float) -> dict:
        """Résultat net attendu d'un avantage brut HYPOTHÉTIQUE ; non rempli = rien gagné, rien perdu (occasion manquée)."""
        t = self.totals()
        p = t["entry_fill_probability"]
        gross = edge_bps / 1e4 * self.notional_eur
        central = p * (gross - t["cost_eur"])
        pessimistic = min(p, 1.0) * (gross - t["cost_high_eur"])
        scen = {k: p * (gross - v) for k, v in t["scenarios_eur"].items()}
        return {"edge_bps": edge_bps, "gross_eur": gross, "net_eur": central, "net_bps": central / self.notional_eur * 1e4,
                "net_pessimistic_eur": pessimistic, "missed_opportunity_eur": (1 - p) * max(0.0, gross - t["cost_eur"]),
                "break_even_bps": t["cost_bps"], "net_by_scenario_eur": scen}

    def to_dict(self, edge_bps: float | None = None):
        d = {"id": self.id, "label": self.label, "kind": self.kind, "notional_eur": self.notional_eur,
             "entry": [x.to_dict() for x in self.entry], "exit": [x.to_dict() for x in self.exit] if self.exit is not None else None,
             "notes": self.notes, "reasons": self.reasons, "eligible": not self.reasons}
        if self.exit is not None and not self.reasons and self.entry:
            d["totals"] = self.totals()
            if edge_bps is not None:
                d["net"] = self.net(edge_bps)
        return d


# ---------------------------------------------------------------------------------------------------------------
# Carnet : parcours d'un ordre au marché
# ---------------------------------------------------------------------------------------------------------------
def walk(book: Book, side: str, *, qty: float | None = None, quote: float | None = None, price_limit: float | None = None):
    """Parcourt le carnet : achat (côté vente) ou vente (côté achat). Renvoie (quantité, montant, prix moyen, complet)."""
    levels = book.asks if side == "buy" else book.bids
    got_q = spent = 0.0
    for p, q in levels:
        if price_limit is not None and ((side == "buy" and p > price_limit) or (side == "sell" and p < price_limit)):
            break
        take = q
        if qty is not None:
            take = min(q, qty - got_q)
        if quote is not None:
            take = min(take, (quote - spent) / p)
        if take <= 0:
            break
        got_q += take
        spent += take * p
        if (qty is not None and got_q >= qty - 1e-15) or (quote is not None and spent >= quote - 1e-12):
            break
    complete = (qty is not None and got_q >= qty - 1e-12) or (quote is not None and spent >= quote * (1 - 1e-9))
    return got_q, spent, (spent / got_q if got_q else None), bool(complete)


def _fee(view: MarketView, venue: str, kind: str = "taker"):
    v = view.fees(venue)[kind]
    lo, hi = v.interval or (v.value, v.value)
    return v.value / 1e4, lo / 1e4, hi / 1e4, v


def cex_leg(view: MarketView, venue: str, pair: str, side: str, *, quote_budget: float | None = None, base_qty: float | None = None,
            asset_in: str, loc_in: str, asset_out: str, loc_out: str, eur_per_quote: float, cls: str, exit_leg: bool = False,
            book: Book | None = None, rules: Rules | None = None) -> Leg:
    """Ordre au marché sur une plateforme : coûts mesurés sur le carnet courant + a priori de COST OPTIMIZER."""
    book = book or view.book(venue, pair)
    rules = rules or view.rules(venue, pair)
    if not rules.trading:
        raise SourceUnavailable(venue, f"{pair} suspendu")
    fee, fee_lo, fee_hi, fee_v = _fee(view, venue)
    mid = book.mid
    base, quote = pair.split("/")
    if side == "buy":
        if quote_budget is None:
            raise RouteError("achat : budget en devise de cotation requis")
        q_est, _, avg, enough = walk(book, "buy", quote=quote_budget)
        if not enough:  # jamais de réduction silencieuse de la taille : le carnet doit couvrir tout le montant demandé
            raise RouteError(f"profondeur insuffisante sur {venue} {pair} pour {quote_budget:.2f} {quote}")
        qty = rules.floor_qty(q_est)
        got, spent, avg, complete = walk(book, "buy", qty=qty)
        if qty <= 0 or not complete:
            raise RouteError(f"profondeur insuffisante sur {venue} {pair}")
        notional_q = spent
        received = got * (1 - fee)  # commission prélevée dans l'actif reçu (cas le plus fréquent sans jeton de remise)
        qty_in, qty_out = spent, received
    else:
        if base_qty is None:
            raise RouteError("vente : quantité requise")
        qty = rules.floor_qty(base_qty)
        if qty <= 0:
            raise RouteError(f"quantité inférieure au pas de {venue} {pair}")
        got, spent, avg, complete = walk(book, "sell", qty=qty)
        if not complete:
            raise RouteError(f"profondeur insuffisante sur {venue} {pair}")
        notional_q = spent
        qty_in, qty_out = qty, spent * (1 - fee)
    if notional_q < rules.min_notional:
        raise RouteError(f"montant inférieur au minimum de {venue} ({notional_q:.2f} < {rules.min_notional:g} {quote})")
    if rules.min_qty and (qty < rules.min_qty):
        raise RouteError(f"quantité inférieure au minimum de {venue}")
    eur = notional_q * eur_per_quote
    impact = abs(avg - mid) / mid * eur
    stress_hs = A.CEX_EXIT_HALF_SPREAD_STRESS_BPS.get(cls, 5.0) / 1e4 * eur
    slip_c, slip_lo, slip_hi = A.CEX_SLIPPAGE_BPS["eur" if quote == "EUR" else ("major" if base in A.MAJORS else "other")]
    costs = [
        cost("commission", fee * eur, fee_lo * eur, fee_hi * eur, level=fee_v.level, source=fee_v.source, note=fee_v.note),
        cost("écart et profondeur du carnet", impact, impact, max(impact, stress_hs) if exit_leg else impact,
             level="ESTIMATED", source=f"carnet {venue} {pair} reçu à {book.received_ms} (empreinte {book.sha256[:12]})",
             note="parcours du carnet courant" + ("; sortie future : jusqu'à l'écart de tension mesuré par COST OPTIMIZER" if exit_leg else "")),
        cost("glissement d'exécution", slip_c / 1e4 * eur, slip_lo / 1e4 * eur, slip_hi / 1e4 * eur, level="ESTIMATED",
             source="COST OPTIMIZER cost_contract_v1 (glissement au-delà de l'écart, latence 1 à 5 s)",
             note="principal 0 (mesuré ≤ 0), tension au 90e centile"),
    ]
    dust_eur = 0.0
    data = {"venue": venue, "pair": pair, "side": side, "qty": qty, "fee": fee, "mid": mid, "avg": avg, "step": rules.step,
            "min_notional": rules.min_notional, "book_sha256": book.sha256, "book_received_ms": book.received_ms}
    if side == "buy":
        sellable = rules.floor_qty(qty_out)
        dust = qty_out - sellable
        dust_eur = dust * book.bid * eur_per_quote
        if dust_eur > 0:  # capital immobilisé : compté comme perdu (règle prudente de la mission 52), affiché à part
            costs.append(cost("reliquat invendable", dust_eur, 0.0, dust_eur, level="ESTIMATED", kind="immobilise",
                              source=f"pas de quantité {rules.step:g} ({venue})",
                              note=f"{dust_eur:.4f} € conservés mais invendables seuls (commission prélevée dans l'actif reçu, "
                                   "quantité revendable arrondie au pas)"))
        data["sellable"] = sellable
    leg = Leg(f"{'achat' if side == 'buy' else 'vente'} {base} sur {venue} ({pair}, ordre au marché)", "cex", venue, None, side,
              asset_in, loc_in, qty_in, asset_out, loc_out, qty_out, eur, costs, 1.0, A.FAILURE_PROB["cex"], A.LATENCY_MS["cex"],
              dust_eur, 0.0, [book.evidence or {"sha256": book.sha256, "received_ms": book.received_ms}, rules.evidence], data)
    return leg


def economic_minimum(rules: Rules, price: float, fee: float, *, eur_per_quote: float = 1.0, floor_eur: float = 10.0,
                     max_immobilized_pct: float = 1.0) -> dict:
    """Montant minimal économique d'un achat sur une plateforme (mission 52), en euros.

    - minimum de la plateforme, plus un pas de quantité de marge d'arrondi, commission comprise ;
    - reliquat : la commission prélevée dans l'actif reçu laisse jusqu'à un pas de quantité invendable ; pour qu'il reste
      sous `max_immobilized_pct` % du montant, il faut un montant ≥ valeur d'un pas / limite ;
    - plancher prudent de 10 € tant que l'exécution économique n'est pas démontrée.
    """
    step_value = rules.step * price * eur_per_quote
    venue_min = (rules.min_notional * eur_per_quote + step_value) * (1 + fee)
    dust_min = step_value / (max_immobilized_pct / 100) if step_value > 0 else 0.0
    return {"venue": rules.venue, "pair": rules.pair, "step": rules.step, "step_value_eur": step_value, "venue_min_eur": venue_min,
            "dust_min_eur": dust_min, "floor_eur": floor_eur, "economic_min_eur": max(floor_eur, venue_min, dust_min),
            "binding": max((("plancher prudent", floor_eur), ("minimum de la plateforme", venue_min), ("reliquat", dust_min)),
                           key=lambda x: x[1])[0]}


# ---------------------------------------------------------------------------------------------------------------
# Réseaux décentralisés
# ---------------------------------------------------------------------------------------------------------------
def _native_eur(view: MarketView, network: str) -> float:
    asset = "SOL" if network == "solana" else "ETH"
    px, _ = view.eur_reference(asset)
    if px is None:
        raise SourceUnavailable(network, f"prix de référence {asset}/EUR indisponible")
    return px


def network_fee(view: MarketView, network: str, kind: str, gas_units: int | None = None) -> tuple[float, float, float, dict]:
    """Frais de réseau d'une transaction en euros (central, bas, haut) et leur preuve."""
    px = _native_eur(view, network)
    if network == "solana":
        f = view.solana()
        cu = A.SOLANA_SWAP_CU if kind == "swap" else A.SOLANA_TRANSFER_CU
        lam = f.base_fee_lamports + f.priority_micro_lamports_p50 * cu[0] / 1e6
        lam_hi = f.base_fee_lamports + max(f.priority_micro_lamports_p90, 1.0) * cu[2] / 1e6
        return lam / 1e9 * px, f.base_fee_lamports / 1e9 * px, lam_hi / 1e9 * px, {"sha256": f.sha256, "received_ms": f.received_ms,
                                                                                      "source": "getRecentPrioritizationFees"}
    g = view.gas(network)
    units = gas_units or A.GAS_UNITS["erc20_transfer"][0]
    units_hi = int(units * 1.5) if gas_units else A.GAS_UNITS["erc20_transfer"][2]
    c = (units * g.price_wei("p50") + g.l1_fee_wei_800b) / 1e18 * px
    lo = (units * g.base_fee_wei + g.l1_fee_wei_800b) / 1e18 * px
    hi = (units_hi * g.price_wei("p90") + g.l1_fee_wei_1500b) / 1e18 * px
    return c, lo, hi, {"sha256": g.sha256, "received_ms": g.received_ms, "block": g.block, "source": "eth_feeHistory"}


def transfer_leg(view: MarketView, network: str, asset: str, qty: float, eur_per_unit: float, direction: str, venue: str,
                 token_key: str) -> Leg:
    """Retrait d'une plateforme vers la chaîne (frais de retrait hypothétiques) ou dépôt (frais de réseau mesurés)."""
    if direction == "withdraw":
        c, lo, hi, note = A.WITHDRAWAL_EUR.get((network, asset), (2.0, 0.5, 10.0, "réseau ou actif sans barème connu"))
        fee_eur = c
        costs = [cost("frais de retrait", c, lo, hi, level="HYPOTHETICAL", source=f"barème de retrait {venue} (non public)", note=note)]
        qty_out = max(0.0, qty - fee_eur / eur_per_unit)
        return Leg(f"retrait de {asset} de {venue} vers {network}", "transfer", venue, network, "transfer", asset, f"cex:{venue}", qty,
                   token_key, f"chain:{network}", qty_out, qty * eur_per_unit, costs, 1.0, (0.0, 0.0, 0.02),
                   (A.LATENCY_MS[network][1], A.LATENCY_MS[network][1]), data={"fee_eur": fee_eur, "direction": direction})
    c, lo, hi, ev = network_fee(view, network, "transfer")
    costs = [cost("frais de réseau (dépôt)", c, lo, hi, level="ESTIMATED", source=f"{network} : {ev['source']}", note="transfert vers la plateforme")]
    return Leg(f"dépôt de {asset} de {network} vers {venue}", "transfer", venue, network, "transfer", token_key, f"chain:{network}", qty,
               asset, f"cex:{venue}", qty, qty * eur_per_unit, costs, 1.0, (0.0, 0.0, 0.02),
               (A.LATENCY_MS[network][1], A.LATENCY_MS[network][1]), evidence=[ev], data={"fee_eur": c, "direction": direction})


def dex_leg(view: MarketView, registry: TokenRegistry, network: str, t_in: TokenId, t_out: TokenId, qty_in: float, *,
            eur_in_per_unit: float, eur_out_per_unit: float, approved: bool, has_token_account: bool,
            tolerance_bps: float = A.DEX_SLIPPAGE_TOLERANCE_BPS) -> Leg:
    """Échange sur la chaîne d'après un devis public non signé ; coût = valeur reçue face au prix de référence + frais."""
    i_in, i_out = registry.require(t_in), registry.require(t_out)
    amount = int(qty_in * 10 ** i_in.decimals)
    if amount <= 0:
        raise RouteError("montant à échanger nul")
    quotes = view.dex_quotes(network, t_in, t_out, amount)
    px_native = _native_eur(view, network)
    best, best_net, best_gas = None, None, None
    for q in quotes:
        if network == "solana":
            g = network_fee(view, network, "swap")
        else:
            g = network_fee(view, network, "swap", (q.gas_units or 0) + 60_000)  # transaction de base et routeur : +60 000 hypothétique
        value_out = q.amount_out / 10 ** i_out.decimals * eur_out_per_unit
        net = value_out - g[0]
        if best_net is None or net > best_net:
            best, best_net, best_gas = q, net, g
    q, g = best, best_gas
    value_in = qty_in * eur_in_per_unit
    qty_out = q.amount_out / 10 ** i_out.decimals
    value_out = qty_out * eur_out_per_unit
    eff = value_in - value_out
    ok, dev_bps = dex_vs_reference(eur_in_per_unit * qty_in / qty_out if qty_out else 0.0, eur_out_per_unit)
    costs = []
    platform = q.fee_bps / 1e4 * value_in
    if platform:
        costs.append(cost("frais de la plateforme d'agrégation", platform, level="OBSERVED" if q.sha256 else "ESTIMATED",
                          source=f"devis {q.venue} {q.request_id or ''} (empreinte {q.sha256[:12]})", note="déjà déduits du montant reçu"))
    pool_note = f"palier {q.pool_fee_bps:g} pb" if q.pool_fee_bps is not None else "frais de pool inclus dans le montant reçu"
    costs.append(cost("frais de pool, impact et écart au prix de référence", eff - platform, min(eff - platform, 0.0) if eff < platform else eff - platform,
                      eff - platform, level="ESTIMATED", source=f"devis {q.venue} face au consensus des plateformes en euros",
                      note=pool_note + f" ; écart au consensus {dev_bps:+.1f} pb" if dev_bps is not None else pool_note))
    costs.append(cost("frais de réseau (gaz, priorité)", g[0], g[1], g[2], level="ESTIMATED", source=f"{network} : {g[3]['source']}",
                      note="publication sur Ethereum comprise (Base)" if network == "base" else ""))
    data = {"network": network, "venue": q.venue, "token_in": t_in.key, "token_out": t_out.key, "amount_in": amount,
            "amount_out": q.amount_out, "decimals_out": i_out.decimals, "decimals_in": i_in.decimals,
            "min_out": int(q.amount_out * (1 - tolerance_bps / 1e4)), "tolerance_bps": tolerance_bps, "route": list(q.route),
            "quote_sha256": q.sha256, "quote_received_ms": q.received_ms, "gas_eur": g[0], "reference_dev_bps": dev_bps,
            "reference_ok": ok, "price_impact_bps": q.price_impact_bps, "native_eur": px_native, "gas_units": q.gas_units}
    if network != "solana" and not approved:
        units = A.GAS_UNITS["erc20_approve"]
        gg = view.gas(network)
        ap = units[0] * gg.price_wei("p50") / 1e18 * px_native
        costs.append(cost("approbation du token (simulée, une fois)", ap, units[1] * gg.base_fee_wei / 1e18 * px_native,
                          units[2] * gg.price_wei("p90") / 1e18 * px_native, level="HYPOTHETICAL", source="ERC-20 approve",
                          note="coût simulé uniquement : aucune permission de dépense n'est jamais accordée", unverified_fee=False))
        data["approval"] = True
    mev_hi = A.MEV_SHARE_OF_TOLERANCE[network][2] * tolerance_bps / 1e4 * value_in
    costs.append(cost("robots (MEV, sandwich)", 0.0, 0.0, mev_hi, level="HYPOTHETICAL", source="hypothèse du hub", kind="risque",
                      note="nul en moyenne sur de petits montants ; au pire jusqu'à la tolérance de glissement"))
    pf = A.FAILURE_PROB[network]
    costs.append(cost("transaction échouée (gaz perdu)", pf[0] * g[0], pf[1] * g[1], pf[2] * g[2], level="HYPOTHETICAL",
                      source="hypothèse du hub", note="le gaz est payé même si l'échange échoue", kind="risque"))
    deposit = 0.0
    if network == "solana" and not has_token_account:
        deposit = view.solana().rent_token_account_lamports / 1e9 * px_native
    label = " → ".join(str(r.get("label") or r.get("pool") or "?") for r in q.route)
    leg = Leg(f"échange {i_in.symbol} → {i_out.symbol} sur {network} ({q.venue} : {label})", "dex", q.venue, network, "buy",
              t_in.key, f"chain:{network}", qty_in, t_out.key, f"chain:{network}", qty_out, value_in, costs,
              1 - pf[0], pf, A.LATENCY_MS[network], 0.0, deposit,
              [q.evidence or {"sha256": q.sha256, "received_ms": q.received_ms}, g[3]], data)
    return leg


# ---------------------------------------------------------------------------------------------------------------
# Construction des routes
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Intent:
    asset: str  # actif logique (BTC, ETH, SOL, BONK…)
    notional_eur: float
    edge_bps: float | None  # avantage brut annoncé par la stratégie (hypothèse) ; None = aucun
    networks: tuple[str, ...] = ("solana", "base", "ethereum")
    include_limit: bool = True  # chiffrer aussi l'ordre à cours limité (affiché)
    prefer_limit: bool = False  # le retenir seulement si l'utilisateur le demande : amélioration non démontrée par COST OPTIMIZER


def _stable_eur(view: MarketView, stable: str) -> float:
    px, _ = view.eur_reference(stable)
    if px is None:
        raise SourceUnavailable("référence", f"{stable}/EUR indisponible")
    return px


def build_routes(view: MarketView, registry: TokenRegistry, intent: Intent, *, ledger=None, only: str | None = None) -> list[Route]:
    """Toutes les routes de l'intention (ou la seule route `only`, pour la relire juste avant d'exécuter)."""
    asset, n = intent.asset, float(intent.notional_eur)
    if not (n > 0 and math.isfinite(n)):
        raise RouteError("montant invalide")
    routes: list[Route] = []
    cls = A.asset_class(asset)
    if hasattr(view, "prefetch") and only is None:
        pairs = [f"{asset}/EUR", "EUR/USDT", "EUR/USDC", "USDC/EUR", "USDT/EUR", f"{asset}/USDT", f"{asset}/USDC", "SOL/EUR", "ETH/EUR"]
        view.prefetch(pairs)

    def attempt(rid, label, kind, fn):
        if only is not None and rid != only:
            return
        try:
            entry, exit_, notes = fn()
            routes.append(Route(rid, label, kind, n, entry, exit_, notes))
        except (RouteError, SourceUnavailable, KeyError, ValueError) as exc:
            routes.append(Route(rid, label, kind, n, [], [], [], [f"route impossible : {exc}"]))

    for venue in view.venues():
        pair = f"{asset}/EUR"
        if view.supports(venue, pair):
            def direct(venue=venue, pair=pair):
                e = cex_leg(view, venue, pair, "buy", quote_budget=n, asset_in="EUR", loc_in="cash", asset_out=asset,
                            loc_out=f"cex:{venue}", eur_per_quote=1.0, cls=cls)
                x = cex_leg(view, venue, pair, "sell", base_qty=e.data["sellable"], asset_in=asset, loc_in=f"cex:{venue}",
                            asset_out="EUR", loc_out="cash", eur_per_quote=1.0, cls=cls, exit_leg=True)
                return [e], [x], []
            attempt(f"cex:{venue}:{asset}/EUR", f"{venue} — {asset}/EUR direct", "cex_direct", direct)
            if intent.include_limit:
                def limit(venue=venue, pair=pair):
                    e = cex_leg(view, venue, pair, "buy", quote_budget=n, asset_in="EUR", loc_in="cash", asset_out=asset,
                                loc_out=f"cex:{venue}", eur_per_quote=1.0, cls=cls)
                    mk, mk_lo, mk_hi, mk_v = _fee(view, venue, "maker")
                    fill = A.LIMIT_POLICY["fill"]
                    vs = A.LIMIT_POLICY["vs_fee_bps"]
                    eur = e.eur_in
                    e.costs = [cost("commission (teneur de marché)", mk * eur, mk_lo * eur, mk_hi * eur, level=mk_v.level, source=mk_v.source,
                                    note=mk_v.note),
                               cost("prix obtenu face au milieu (règle prudente)", vs[0] / 1e4 * eur, vs[1] / 1e4 * eur, vs[2] / 1e4 * eur,
                                    level="ESTIMATED", source="COST OPTIMIZER (ordre à cours limité, attente 5 min, annulation)",
                                    note=A.LIMIT_POLICY["note"])] + [c for c in e.costs if c.name == "reliquat invendable"]
                    e.fill_probability = fill[0]
                    e.step = e.step.replace("ordre au marché", "ordre à cours limité, attente 5 min, annulé si non rempli")
                    e.data["order_type"] = "limit"
                    x = cex_leg(view, venue, pair, "sell", base_qty=e.data["sellable"], asset_in=asset, loc_in=f"cex:{venue}",
                                asset_out="EUR", loc_out="cash", eur_per_quote=1.0, cls=cls, exit_leg=True)
                    return [e], [x], [f"remplissage {fill[0]:.0%} (fourchette {fill[1]:.0%}–{fill[2]:.0%}) : sinon aucune position"]
                attempt(f"cex-limit:{venue}:{asset}/EUR", f"{venue} — {asset}/EUR à cours limité", "cex_limit", limit)
        for stable in A.STABLECOINS - {"EURC"}:
            conv = (f"EUR/{stable}", "sell") if view.supports(venue, f"EUR/{stable}") else ((f"{stable}/EUR", "buy") if view.supports(venue, f"{stable}/EUR") else None)
            pair2 = f"{asset}/{stable}"
            if conv is None or not view.supports(venue, pair2) or asset in A.STABLECOINS:
                continue

            def via(venue=venue, stable=stable, conv=conv, pair2=pair2):
                s_eur = _stable_eur(view, stable)
                cpair, cside = conv
                if cside == "sell":
                    b = view.book(venue, cpair)
                    c1 = cex_leg(view, venue, cpair, "sell", base_qty=n, asset_in="EUR", loc_in="cash", asset_out=stable,
                                 loc_out=f"cex:{venue}", eur_per_quote=1 / b.mid, cls="stable", book=b)
                else:
                    c1 = cex_leg(view, venue, cpair, "buy", quote_budget=n, asset_in="EUR", loc_in="cash", asset_out=stable,
                                 loc_out=f"cex:{venue}", eur_per_quote=1.0, cls="stable")
                e2 = cex_leg(view, venue, pair2, "buy", quote_budget=c1.qty_out, asset_in=stable, loc_in=f"cex:{venue}", asset_out=asset,
                             loc_out=f"cex:{venue}", eur_per_quote=s_eur, cls=A.asset_class(asset, stable))
                x1 = cex_leg(view, venue, pair2, "sell", base_qty=e2.data["sellable"], asset_in=asset, loc_in=f"cex:{venue}",
                             asset_out=stable, loc_out=f"cex:{venue}", eur_per_quote=s_eur, cls=A.asset_class(asset, stable), exit_leg=True)
                if cside == "sell":
                    b = view.book(venue, cpair)
                    x2 = cex_leg(view, venue, cpair, "buy", quote_budget=x1.qty_out, asset_in=stable, loc_in=f"cex:{venue}",
                                 asset_out="EUR", loc_out="cash", eur_per_quote=1 / b.mid, cls="stable", exit_leg=True, book=b)
                else:
                    x2 = cex_leg(view, venue, cpair, "sell", base_qty=x1.qty_out, asset_in=stable, loc_in=f"cex:{venue}",
                                 asset_out="EUR", loc_out="cash", eur_per_quote=1.0, cls="stable", exit_leg=True)
                return [c1, e2], [x1, x2], [f"deux conversions par sens (EUR ↔ {stable})"]
            attempt(f"cex-via:{venue}:{asset}/{stable}", f"{venue} — EUR → {stable} → {asset}", "cex_via_stable", via)

    for network in intent.networks:
        tok = registry.by_asset(asset, network)
        usdc = registry.by_asset("USDC", network)
        if tok is None or usdc is None or asset in A.STABLECOINS:
            continue
        native_asset = "SOL" if network == "solana" else "ETH"

        def funded(network=network, tok=tok, usdc=usdc, native_asset=native_asset):
            usdc_eur = _stable_eur(view, "USDC")
            a_eur, _ = view.eur_reference(asset)
            if a_eur is None:
                raise SourceUnavailable("référence", f"{asset}/EUR indisponible")
            fund = best_funding(view, n, usdc_eur)
            w = transfer_leg(view, network, "USDC", fund.qty_out, usdc_eur, "withdraw", fund.venue, usdc.id.key)
            legs = [fund, w]
            approved = bool(ledger and ledger.approved(network, usdc.id.key, "router"))
            has_native = bool(ledger and ledger.available_holding(f"native:{network}", f"chain:{network}") > 0)
            if not has_native:
                g = provision_gas(view, network, native_asset)
                legs += g
            s = dex_leg(view, registry, network, usdc.id, tok.id, w.qty_out, eur_in_per_unit=usdc_eur, eur_out_per_unit=a_eur,
                        approved=approved, has_token_account=False)
            legs.append(s)
            back = dex_leg(view, registry, network, tok.id, usdc.id, s.qty_out, eur_in_per_unit=a_eur, eur_out_per_unit=usdc_eur,
                           approved=False, has_token_account=True)
            back.side = "sell"
            dep = transfer_leg(view, network, "USDC", back.qty_out, usdc_eur, "deposit", fund.venue, usdc.id.key)
            off = cex_leg(view, fund.venue, fund.data["pair"], "sell" if fund.data["pair"].startswith("USDC/") else "buy",
                          base_qty=dep.qty_out if fund.data["pair"].startswith("USDC/") else None,
                          quote_budget=None if fund.data["pair"].startswith("USDC/") else dep.qty_out,
                          asset_in="USDC", loc_in=f"cex:{fund.venue}", asset_out="EUR", loc_out="cash",
                          eur_per_quote=1.0 if fund.data["pair"].endswith("/EUR") else 1 / view.book(fund.venue, fund.data["pair"]).mid,
                          cls="stable", exit_leg=True)
            return legs, [back, dep, off], ["financement depuis les euros : achat d'USDC, retrait vers la chaîne et jeton de gaz"]
        attempt(f"dex-funded:{network}:{asset}", f"{network} — EUR → USDC → retrait → échange", "dex_funded", funded)

        if ledger is not None and ledger.available_holding(usdc.id.key, f"chain:{network}") > 0:
            def onchain(network=network, tok=tok, usdc=usdc):
                usdc_eur = _stable_eur(view, "USDC")
                a_eur, _ = view.eur_reference(asset)
                q_usdc = n / usdc_eur
                if ledger.available_holding(usdc.id.key, f"chain:{network}") < q_usdc:
                    raise RouteError("solde d'USDC sur la chaîne insuffisant")
                if ledger.available_holding(f"native:{network}", f"chain:{network}") <= 0:
                    raise RouteError("aucun jeton de gaz sur la chaîne")
                s = dex_leg(view, registry, network, usdc.id, tok.id, q_usdc, eur_in_per_unit=usdc_eur, eur_out_per_unit=a_eur,
                            approved=ledger.approved(network, usdc.id.key, "router"),
                            has_token_account=ledger.available_holding(tok.id.key, f"chain:{network}") > 0)
                back = dex_leg(view, registry, network, tok.id, usdc.id, s.qty_out, eur_in_per_unit=a_eur, eur_out_per_unit=usdc_eur,
                               approved=False, has_token_account=True)
                back.side = "sell"
                return [s], [back], ["suppose des USDC déjà sur la chaîne : aucun coût de financement compté"]
            attempt(f"dex-onchain:{network}:{asset}", f"{network} — USDC détenus → échange", "dex_onchain", onchain)
    return routes


def best_funding(view: MarketView, n: float, usdc_eur: float) -> Leg:
    """Achat d'USDC avec des euros sur la plateforme la moins chère qui cote la paire."""
    best = None
    for venue in view.venues():
        for pair, side in (("USDC/EUR", "buy"), ("EUR/USDC", "sell")):
            if not view.supports(venue, pair):
                continue
            try:
                if side == "buy":
                    leg = cex_leg(view, venue, pair, "buy", quote_budget=n, asset_in="EUR", loc_in="cash", asset_out="USDC",
                                  loc_out=f"cex:{venue}", eur_per_quote=1.0, cls="stable")
                else:
                    b = view.book(venue, pair)
                    leg = cex_leg(view, venue, pair, "sell", base_qty=n, asset_in="EUR", loc_in="cash", asset_out="USDC",
                                  loc_out=f"cex:{venue}", eur_per_quote=1.0, cls="stable", book=b)
            except (RouteError, SourceUnavailable):
                continue
            if best is None or leg.total()[0] < best.total()[0]:
                best = leg
    if best is None:
        raise SourceUnavailable("financement", "aucune plateforme ne cote USDC contre euro")
    return best


def provision_gas(view: MarketView, network: str, native_asset: str) -> list[Leg]:
    """Jeton natif nécessaire aux frais de réseau : achat sur une plateforme puis retrait (le reste devient un reliquat)."""
    budget = A.GAS_PROVISION_EUR
    best = None
    for venue in view.venues():
        pair = f"{native_asset}/EUR"
        if not view.supports(venue, pair):
            continue
        try:
            leg = cex_leg(view, venue, pair, "buy", quote_budget=budget, asset_in="EUR", loc_in="cash", asset_out=native_asset,
                          loc_out=f"cex:{venue}", eur_per_quote=1.0, cls=A.asset_class(native_asset))
        except (RouteError, SourceUnavailable):
            continue
        if best is None or leg.total()[0] < best.total()[0]:
            best = leg
    if best is None:
        raise RouteError(f"impossible d'acheter du {native_asset} pour les frais de réseau (montant minimal ou cotation)")
    px, _ = view.eur_reference(native_asset)
    w = transfer_leg(view, network, native_asset, best.qty_out, px, "withdraw", best.venue, f"native:{network}")
    left = w.qty_out * px
    w.costs.append(cost("provision de gaz conservée (reliquat)", left, 0.0, left, level="ESTIMATED", kind="immobilise",
                        source="solde natif restant",
                        note="valeur conservée sur la chaîne, difficilement revendable en petite quantité"))
    best.step += " — provision pour les frais de réseau"
    return [best, w]


# ---------------------------------------------------------------------------------------------------------------
# Choix
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Choice:
    decision: str  # ROUTE | ABSTENTION
    route: Route | None
    ranked: list[Route]
    reasons: list[str]
    sensitivity: list[str]
    fee_dependent: bool = False
    scenario_winners: dict = field(default_factory=dict)

    def to_dict(self, edge_bps):
        return {"decision": self.decision, "route": self.route.to_dict(edge_bps) if self.route else None,
                "alternatives": [r.to_dict(edge_bps) for r in self.ranked], "reasons": self.reasons, "sensitivity": self.sensitivity,
                "fee_dependent": self.fee_dependent, "scenario_winners": self.scenario_winners}


# Règle de choix PRÉÉTABLIE (mission 52), fixée sans regarder de résultat de stratégie :
# 1. une route n'est éligible que si son capital immobilisé (reliquats, provision de gaz, dépôts) reste sous la limite ;
# 2. on retient la route au meilleur résultat net dans le scénario DÉFAVORABLE (toutes les composantes à leur borne
#    haute), puis au meilleur net central : jamais simplement la moins chère au prix central ;
# 3. on s'abstient si le net central est ≤ 0 ou si le net défavorable est < 0 ;
# 4. si le gagnant change quand les commissions non vérifiées passent à leur borne basse ou haute, la décision est
#    signalée comme dépendante de commissions non vérifiées.
def choose(routes: list[Route], edge_bps: float | None, *, min_fill: float = 0.5, min_net_eur: float = 0.0,
           max_cost_bps: float | None = None, allow_limit: bool = False, max_immobilized_pct: float | None = None,
           robust: bool = True) -> Choice:
    eligible = []
    for r in routes:
        if r.reasons:
            continue
        if r.kind == "cex_limit" and not allow_limit:
            r.notes.append("non retenu d'office : COST OPTIMIZER n'a pas démontré d'amélioration des ordres passifs "
                           "(à choisir explicitement)")
            continue
        if r.exit is None:
            r.reasons.append("route sans sortie : comparaison refusée")
            continue
        t = r.totals()
        if t["entry_fill_probability"] < min_fill:
            r.reasons.append(f"probabilité de remplissage {t['entry_fill_probability']:.0%} < {min_fill:.0%}")
            continue
        if max_immobilized_pct is not None and t["immobilized_pct"] > max_immobilized_pct:
            r.reasons.append(f"capital immobilisé {t['immobilized_eur']:.4f} € ({t['immobilized_pct']:.2f} % du montant) "
                             f"> limite {max_immobilized_pct:g} %")
            continue
        if max_cost_bps is not None and t["cost_bps"] > max_cost_bps:
            r.reasons.append(f"coût aller-retour {t['cost_bps']:.1f} pb > plafond {max_cost_bps:.0f} pb")
            continue
        eligible.append(r)
    edge = 0.0 if edge_bps is None else float(edge_bps)

    def key(r, scenario="defavorable"):
        n = r.net(edge)
        t = r.totals()
        if robust:
            return (-n["net_by_scenario_eur"][scenario], -n["net_eur"], t["cost_high_eur"], t["latency_ms"])
        return (-n["net_eur"], t["cost_high_eur"], t["latency_ms"])
    ranked = sorted(eligible, key=key)
    ranked += [r for r in routes if r not in ranked]
    reasons, sens = [], []
    if not eligible:
        return Choice("ABSTENTION", None, ranked, ["aucune route fiable : toutes sont impossibles ou hors limites"], sens)
    best = ranked[0]
    winners = {}
    for scen in ("central", "commissions_basses", "commissions_hautes", "defavorable"):
        w = min(eligible, key=lambda r: (-r.net(edge)["net_by_scenario_eur"][scen], r.totals()["latency_ms"]))
        winners[scen] = w.label
    fee_dependent = len({winners["commissions_basses"], winners["commissions_hautes"], winners["central"]}) > 1
    if fee_dependent:
        sens.append(f"décision dépendante de commissions NON VÉRIFIÉES : au prix central « {winners['central']} », commissions "
                    f"basses « {winners['commissions_basses']} », commissions hautes « {winners['commissions_hautes']} »")
    t = best.totals()
    if t["unverified_fee_share"] > 0.5:
        sens.append(f"{t['unverified_fee_share']:.0%} du coût de la route retenue vient de commissions non vérifiées")
    if winners["central"] != best.label:
        sens.append(f"la moins chère au prix central (« {winners['central']} ») n'est pas retenue : elle est moins sûre dans le scénario défavorable")
    if edge_bps is None:
        reasons.append("aucun avantage brut annoncé : le résultat net attendu est le coût changé de signe")
    nb = best.net(edge)
    if nb["net_eur"] <= min_net_eur:
        reasons.append(f"résultat net attendu {nb['net_eur']:+.4f} € (avantage {edge:.0f} pb − coût {t['cost_bps']:.1f} pb) : "
                       "opération non rentable")
        return Choice("ABSTENTION", None, ranked, reasons, sens, fee_dependent, winners)
    adverse = nb["net_by_scenario_eur"]["defavorable"]
    if robust and adverse < 0:
        reasons.append(f"résultat net dans le scénario défavorable {adverse:+.4f} € (coût {t['scenarios_bps']['defavorable']:.1f} pb) : "
                       "rentable seulement si les coûts restent centraux — abstention prudente")
        return Choice("ABSTENTION", None, ranked, reasons, sens, fee_dependent, winners)
    return Choice("ROUTE", best, ranked, reasons, sens, fee_dependent, winners)
