"""Moteur de sécurité du hub : indépendant de l'IA de stratégie, il a un droit de VETO absolu.

Trois familles de contrôles, chacun motivé (code, message, couche) :
- financière : taille, exposition, pertes du jour et cumulées, baisse depuis le plus haut, réserve de liquidités,
  minimum, solde négatif, capital disponible, levier et vente à découvert, corrélation, coûts d'entrée ET de sortie,
  opérations concurrentes sur le même actif ;
- tokens et réseaux décentralisés : identité vérifiée, imitation, impossibilité de revendre, taxes, autorités
  d'émission et de gel, concentration, liquidité, contrats inconnus sur la route, contradiction des sources,
  mauvais réseau, ponts ;
- technique : fraîcheur, horloge, coupe-circuit, frais de réseau excessifs, prévisualisation expirée, réglages
  modifiés pendant la décision, prix qui a bougé avant l'exécution.

Les PLAFONDS ABSOLUS sont des ratios constants du capital initial : ni l'API, ni le LLM, ni un réglage ne peut les dépasser. Les
limites réglables dans le hub doivent rester à l'intérieur (et toute demande d'origine IA est refusée).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from types import MappingProxyType

from . import assumptions as A

ABSOLUTE = MappingProxyType({
    "capital_eur": 30.0,
    "max_position_eur": 15.0,
    "max_exposure_pct": 90.0,
    "min_cash_reserve_eur": 3.0,
    "max_daily_loss_eur": 3.0,
    "max_total_loss_eur": 9.0,
    "max_drawdown_pct": 30.0,
    "max_memecoin_exposure_pct": 50.0,
    "max_round_trip_cost_bps": 300.0,
    "max_spread_bps": 100.0,
    "max_price_impact_bps": 100.0,
    "max_network_fee_eur": 0.50,
    "max_slippage_tolerance_bps": 100.0,
    "max_book_age_ms": 5_000,
    "max_quote_age_ms": 15_000,
    "max_price_move_bps": 50.0,
    "max_open_positions": 6,
    "max_orders_per_minute": 6,
    "max_clock_skew_ms": 2_000,
    "min_order_eur": 1.0,
    "preview_ttl_ms": 60_000,
    "max_immobilized_pct": 5.0,
})
# sens de la contrainte : « max » = la limite réglable doit être ≤ au plafond ; « min » = ≥ au plancher
_DIRECTION = {k: ("min" if k in ("min_cash_reserve_eur", "min_order_eur") else "max") for k in ABSOLUTE if k != "capital_eur"}
DEFAULT_LIMITS = MappingProxyType({
    "max_position_eur": 10.0, "max_exposure_pct": 80.0, "min_cash_reserve_eur": 6.0, "max_daily_loss_eur": 1.5,
    "max_total_loss_eur": 6.0, "max_drawdown_pct": 20.0, "max_memecoin_exposure_pct": 30.0, "max_round_trip_cost_bps": 150.0,
    "max_spread_bps": 50.0, "max_price_impact_bps": 50.0, "max_network_fee_eur": 0.20, "max_slippage_tolerance_bps": 50.0,
    "max_book_age_ms": 3_000, "max_quote_age_ms": 10_000, "max_price_move_bps": 30.0, "max_open_positions": 4,
    "max_orders_per_minute": 3, "max_clock_skew_ms": 1_500, "preview_ttl_ms": 30_000,
    # Mission 52 : refus par défaut sous 10 € tant que l'exécution économique n'est pas démontrée (minimums des
    # plateformes, arrondis, reliquats) ; capital immobilisé (reliquats, provision de gaz, dépôts) ≤ 1 % du montant.
    "min_order_eur": 10.0, "max_immobilized_pct": 1.0,
})
HUMAN_ORIGINS = frozenset({"user"})
AI_ORIGINS = frozenset({"ai", "llm", "agent", "strategy"})
# Montants de référence à 30 EUR : 10/30 (un tiers, sans arrondi à 33,33 %), 20 %, 5 %, 20 %.
# Les plafonds humains historiques restent 15/30, 3/30, 3/30, 9/30 ; les minimums de marché ne sont pas multipliés.
CAPITAL_LIMITS = frozenset({"max_position_eur", "min_cash_reserve_eur", "max_daily_loss_eur", "max_total_loss_eur"})


def scale_limits(limits: dict, capital_eur: float, reference_eur: float = 30.0) -> dict:
    """Rebase les seuls budgets de risque ; frais de réseau et minimums d'ordre restent des montants d'instrument."""
    for capital in (capital_eur, reference_eur):
        if isinstance(capital, bool) or not isinstance(capital, (int, float)) or not math.isfinite(capital) or capital <= 0:
            raise SecurityRefusal("capital fictif fini et strictement positif attendu")
    return {k: v * capital_eur / reference_eur if k in CAPITAL_LIMITS and isinstance(v, (int, float)) and not isinstance(v, bool) else v
            for k, v in limits.items()}


def absolute_limits(capital_eur: float = 30.0) -> dict:
    """ABSOLUTE reste la référence historique immuable ; les plafonds actifs suivent le capital SQLite."""
    return {**scale_limits(dict(ABSOLUTE), capital_eur), "capital_eur": capital_eur}


class SecurityRefusal(ValueError):
    code = "dmh_security"

    def __init__(self, message: str, reasons: list | None = None):
        super().__init__(message)
        self.message = message
        self.reasons = reasons or []


@dataclass(frozen=True)
class Reason:
    code: str
    message: str
    layer: str  # financier | token | technique | economique
    veto: bool = True

    def to_dict(self):
        return {"code": self.code, "message": self.message, "layer": self.layer, "veto": self.veto}


@dataclass
class Verdict:
    reasons: list[Reason] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not any(r.veto for r in self.reasons)

    def veto(self, code, message, layer):
        self.reasons.append(Reason(code, message, layer, True))

    def warn(self, code, message, layer):
        self.reasons.append(Reason(code, message, layer, False))

    def to_dict(self):
        return {"allowed": self.allowed, "vetoes": [r.to_dict() for r in self.reasons if r.veto],
                "warnings": [r.to_dict() for r in self.reasons if not r.veto]}


def validate_limits(patch: dict, current: dict, origin: str, capital_eur: float = 30.0) -> dict:
    """Nouvelles limites réglables ; refus si la demande vient de l'IA ou franchit un plafond absolu."""
    if origin not in HUMAN_ORIGINS:
        raise SecurityRefusal(f"modification des limites refusée : origine « {origin} » (seul l'utilisateur, jamais l'IA)")
    if not isinstance(patch, dict) or not patch:
        raise SecurityRefusal("aucune limite à modifier")
    out = dict(current)
    caps = absolute_limits(capital_eur)
    for k, v in patch.items():
        if k == "capital_eur" or k not in _DIRECTION:
            raise SecurityRefusal(f"limite inconnue ou non réglable : {k}")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise SecurityRefusal(f"{k} : nombre positif attendu")
        cap = caps[k]
        if _DIRECTION[k] == "max" and v > cap:
            raise SecurityRefusal(f"{k} = {v} dépasse le plafond absolu {cap} (non contournable)")
        if _DIRECTION[k] == "min" and v < cap:
            raise SecurityRefusal(f"{k} = {v} est sous le plancher absolu {cap} (non contournable)")
        out[k] = type(DEFAULT_LIMITS[k])(v)
    return out


def config_hash(limits: dict, assumptions_digest: str, registry_sha: str | None) -> str:
    return hashlib.sha256(json.dumps({"limits": limits, "assumptions": assumptions_digest, "registry": registry_sha},
                                     sort_keys=True).encode()).hexdigest()


def effective(limits: dict, capital_eur: float = 30.0) -> dict:
    """Les limites appliquées ne dépassent JAMAIS les plafonds, même si la base contenait une valeur trafiquée."""
    out = {}
    defaults = scale_limits(dict(DEFAULT_LIMITS), capital_eur)
    for k, cap in absolute_limits(capital_eur).items():
        if k == "capital_eur":
            continue
        v = limits.get(k, defaults[k])
        try:
            if isinstance(v, bool):
                raise ValueError("limite booléenne")
            v = float(v)
            if not math.isfinite(v):
                raise ValueError("limite non finie")
        except (TypeError, ValueError):
            v = defaults[k]
        out[k] = min(v, cap) if _DIRECTION[k] == "max" else max(v, cap)
    return out


@dataclass
class Portfolio:
    """État du portefeuille fictif vu par la sécurité (euros)."""
    cash_total: float
    cash_available: float
    positions: dict[str, float]  # actif logique -> valeur
    equity: float
    day_start_equity: float
    peak_equity: float
    initial: float = 30.0
    open_ops_assets: tuple[str, ...] = ()
    recent_orders: int = 0


def check_financial(v: Verdict, lim: dict, pf: Portfolio, *, side: str, asset: str, notional: float, route_totals: dict | None,
                    held_qty_value: float | None = None, cash_needed: float | None = None) -> None:
    """`notional` : valeur de l'opération en euros ; `cash_needed` : euros réellement dépensés (0 si payé en USDC détenus)."""
    cash_needed = notional if cash_needed is None else cash_needed
    if not (math.isfinite(notional) and notional > 0):
        v.veto("montant", "montant invalide", "financier")
        return
    if side == "sell":
        if held_qty_value is None or notional > held_qty_value * 1.0001:
            v.veto("vente_a_decouvert", "vente supérieure à la quantité détenue : vente à découvert interdite", "financier")
        return
    if notional < lim["min_order_eur"]:
        v.veto("minimum", f"montant {notional:.2f} € inférieur au minimum {lim['min_order_eur']:.2f} €", "financier")
    if notional > lim["max_position_eur"]:
        v.veto("position", f"position {notional:.2f} € > limite {lim['max_position_eur']:.2f} €", "financier")
    after = pf.positions.get(asset, 0.0) + notional
    if after > lim["max_position_eur"]:
        v.veto("position_cumulee", f"exposition sur {asset} {after:.2f} € > limite {lim['max_position_eur']:.2f} €", "financier")
    if cash_needed > pf.cash_available + 1e-9:
        v.veto("capital", f"capital disponible insuffisant ({pf.cash_available:.2f} € disponibles, réservations déduites)", "financier")
    if cash_needed > 0 and pf.cash_total - cash_needed < lim["min_cash_reserve_eur"] - 1e-9:
        v.veto("reserve", f"la réserve de liquidités tomberait sous {lim['min_cash_reserve_eur']:.2f} €", "financier")
    exposure = (sum(pf.positions.values()) + notional) / max(pf.equity, 1e-9) * 100
    if exposure > lim["max_exposure_pct"]:
        v.veto("exposition", f"exposition totale {exposure:.0f} % > {lim['max_exposure_pct']:.0f} %", "financier")
    # corrélation : toutes les cryptos non stables évoluent ensemble ; les memecoins ont un plafond propre
    if asset in A.MEMECOINS:
        meme = (sum(x for a, x in pf.positions.items() if a in A.MEMECOINS) + notional) / max(pf.equity, 1e-9) * 100
        if meme > lim["max_memecoin_exposure_pct"]:
            v.veto("correlation_memecoins", f"exposition memecoins {meme:.0f} % > {lim['max_memecoin_exposure_pct']:.0f} %", "financier")
    if len([a for a, x in pf.positions.items() if x > 0]) >= lim["max_open_positions"] and asset not in pf.positions:
        v.veto("positions", f"déjà {int(lim['max_open_positions'])} positions ouvertes", "financier")
    if asset in pf.open_ops_assets:
        v.veto("concurrence", f"une autre opération sur {asset} est en cours : positions incompatibles évitées", "financier")
    if pf.recent_orders >= lim["max_orders_per_minute"]:
        v.veto("cadence", "trop d'ordres fictifs dans la dernière minute", "financier")
    day_loss = pf.day_start_equity - pf.equity
    if day_loss >= lim["max_daily_loss_eur"]:
        v.veto("perte_jour", f"perte du jour {day_loss:.2f} € ≥ limite {lim['max_daily_loss_eur']:.2f} €", "financier")
    total_loss = pf.initial - pf.equity
    if total_loss >= lim["max_total_loss_eur"]:
        v.veto("perte_totale", f"perte cumulée {total_loss:.2f} € ≥ limite {lim['max_total_loss_eur']:.2f} €", "financier")
    dd = (pf.peak_equity - pf.equity) / max(pf.peak_equity, 1e-9) * 100
    if dd >= lim["max_drawdown_pct"]:
        v.veto("baisse", f"baisse depuis le plus haut {dd:.1f} % ≥ {lim['max_drawdown_pct']:.0f} %", "financier")
    if route_totals is not None:
        if route_totals.get("cost_bps") is None:
            v.veto("sortie_absente", "coût de sortie inconnu : opération refusée", "economique")
        elif route_totals["cost_bps"] > lim["max_round_trip_cost_bps"]:
            v.veto("cout", f"coût aller-retour {route_totals['cost_bps']:.1f} pb > {lim['max_round_trip_cost_bps']:.0f} pb", "economique")


def check_route(v: Verdict, lim: dict, route: dict, now_ms: int, registry, token_risks: dict | None = None) -> None:
    """Contrôles techniques et réseaux décentralisés sur une route chiffrée (dict de `Route.to_dict`)."""
    if route.get("exit") is None:
        v.veto("sortie_absente", "devis sans sortie : comparaison rejetée", "economique")
        return
    t = route.get("totals") or {}
    if t.get("immobilized_pct") is not None and t["immobilized_pct"] > lim["max_immobilized_pct"]:
        v.veto("immobilisation", f"capital immobilisé {t['immobilized_eur']:.4f} € ({t['immobilized_pct']:.2f} %) > limite "
               f"{lim['max_immobilized_pct']:g} % (reliquats, provision de gaz, dépôts)", "economique")
    for leg in route["entry"] + route["exit"]:
        d = leg.get("data", {})
        if leg["kind"] == "cex":
            age = now_ms - int(d.get("book_received_ms", 0))
            if age > lim["max_book_age_ms"]:
                v.veto("perime", f"carnet {leg['venue']} vieux de {age / 1000:.1f} s (> {lim['max_book_age_ms'] / 1000:.1f} s)", "technique")
            if d.get("avg") and d.get("mid"):
                spread_bps = abs(d["avg"] / d["mid"] - 1) * 2e4
                if spread_bps > lim["max_spread_bps"]:
                    v.veto("ecart", f"écart effectif {spread_bps:.1f} pb sur {leg['venue']} > {lim['max_spread_bps']:.0f} pb", "technique")
        elif leg["kind"] == "dex":
            age = now_ms - int(d.get("quote_received_ms", 0))
            if age > lim["max_quote_age_ms"]:
                v.veto("devis_perime", f"devis {leg['venue']} vieux de {age / 1000:.1f} s", "technique")
            if d.get("gas_eur", 0) > lim["max_network_fee_eur"]:
                v.veto("gaz", f"frais de réseau {d['gas_eur']:.3f} € > budget {lim['max_network_fee_eur']:.2f} €", "technique")
            if d.get("tolerance_bps", 0) > lim["max_slippage_tolerance_bps"]:
                v.veto("tolerance", "tolérance de glissement au-delà de la limite", "technique")
            if d.get("price_impact_bps") is not None and d["price_impact_bps"] > lim["max_price_impact_bps"]:
                v.veto("impact", f"impact de prix {d['price_impact_bps']:.1f} pb > {lim['max_price_impact_bps']:.0f} pb", "token")
            if not d.get("reference_ok", False):
                dev = d.get("reference_dev_bps")
                v.veto("sources_contradictoires", "prix sur la chaîne incompatible avec les plateformes centralisées"
                       + (f" ({dev:+.0f} pb)" if dev is not None else " (référence indisponible)") + " : token ou prix manipulé ?", "token")
            for hop in d.get("route", []):
                label = hop.get("label")
                if leg["network"] == "solana" and label not in A.KNOWN_SOLANA_AMMS:
                    v.veto("contrat_inconnu", f"la route passe par une place inconnue « {label} »", "token")
                if leg["network"] == "solana":
                    for key in ("input", "output"):
                        mint = hop.get(key)
                        if mint and not _verified(registry, "solana", mint):
                            v.veto("token_intermediaire", f"token intermédiaire non vérifié sur la route ({mint[:8]}…)", "token")
            for key in ("token_in", "token_out"):
                tk = d.get(key)
                if tk and not tk.startswith(leg["network"] + ":"):
                    v.veto("mauvais_reseau", f"token {tk[:20]}… sur un autre réseau que {leg['network']}", "token")
                risk = (token_risks or {}).get(tk)
                if risk is not None and risk.get("status") != "SAFE":
                    v.veto("token_risque", f"token {tk[:24]}… : {', '.join(risk.get('flags', [])) or 'risque inconnu'}", "token")
        elif leg["kind"] == "transfer" and "bridge" in leg.get("step", "").lower():
            v.veto("pont", "les ponts entre réseaux sont exclus", "token")


def _verified(registry, network, address) -> bool:
    from .tokens import TokenError, TokenId
    try:
        info = registry.get(TokenId(network, address))
    except TokenError:
        return False
    return bool(info and info.verified)


def check_execution_drift(v: Verdict, lim: dict, preview_route: dict, fresh_route: dict) -> None:
    """Le prix ou le coût a-t-il changé entre la prévisualisation et l'exécution ? Au-delà de la limite : refus."""
    p, f = preview_route.get("totals") or {}, fresh_route.get("totals") or {}
    if not p or not f:
        v.veto("reprise", "impossible de recalculer la route à l'exécution", "technique")
        return
    dc = f["cost_bps"] - p["cost_bps"]
    if dc > lim["max_price_move_bps"]:
        v.veto("cout_change", f"le coût a augmenté de {dc:.1f} pb depuis la prévisualisation : nouvelle confirmation requise", "technique")
    for a, b in zip(preview_route["entry"], fresh_route["entry"]):
        pa, pb = a.get("data", {}).get("mid"), b.get("data", {}).get("mid")
        if pa and pb:
            move = abs(pb / pa - 1) * 1e4
            if move > lim["max_price_move_bps"]:
                v.veto("prix_change", f"le prix a bougé de {move:.1f} pb avant l'exécution : nouvelle confirmation requise", "technique")
        qa, qb = a.get("data", {}).get("amount_out"), b.get("data", {}).get("amount_out")
        if qa and qb and qb < qa * (1 - lim["max_price_move_bps"] / 1e4):
            v.veto("devis_change", "le devis s'est dégradé avant l'exécution : nouvelle confirmation requise", "technique")


def check_clock(v: Verdict, lim: dict, offsets_ms: list[float]) -> None:
    if not offsets_ms:
        v.warn("horloge_inconnue", "décalage d'horloge non mesuré", "technique")
        return
    worst = max(abs(x) for x in offsets_ms)
    if worst > lim["max_clock_skew_ms"]:
        v.veto("horloge", f"horloge locale décalée de {worst:.0f} ms par rapport aux plateformes", "technique")


def assess_token(info, onchain: dict | None, external: dict | None, *, accepted_flags: tuple[str, ...] = ()) -> dict:
    """Synthèse prudente : SAFE seulement si l'identité est vérifiée ET qu'aucune source ne signale de risque."""
    flags: list[str] = []
    if info is None:
        return {"status": "UNKNOWN", "flags": ["token absent du registre vérifié"]}
    if not info.verified:
        flags.append(f"statut {info.status}")
    if onchain is None:
        flags.append("lecture sur la chaîne indisponible")
    else:
        if not onchain.get("exists", False):
            flags.append("contrat ou mint introuvable")
        if onchain.get("decimals") is not None and onchain["decimals"] != info.decimals:
            flags.append("décimales différentes du registre")
        if onchain.get("mint_authority"):
            flags.append("émission encore possible (autorité d'émission)")
        if onchain.get("freeze_authority"):
            flags.append("gel possible (autorité de gel)")
        if onchain.get("transfer_fee") or onchain.get("transfer_hook") or onchain.get("permanent_delegate"):
            flags.append("extension Token-2022 à risque (frais, crochet ou délégué de transfert)")
        if onchain.get("symbol") and onchain["symbol"].upper() != info.symbol.upper():
            flags.append("symbole sur la chaîne différent du registre")
    for name, rep in (external or {}).items():
        if rep is None:
            continue
        if rep.get("honeypot") or rep.get("cannot_sell"):
            flags.append(f"{name} : impossible à revendre (pot de miel)")
        if (rep.get("buy_tax") or 0) > 0 or (rep.get("sell_tax") or 0) > 0:
            flags.append(f"{name} : taxe à l'achat ou à la vente")
        for f in rep.get("flags", []):
            flags.append(f"{name} : {f}")
    remaining = [f for f in flags if not any(a in f for a in accepted_flags)]
    status = "SAFE" if not remaining else "UNSAFE"
    return {"status": status, "flags": remaining, "accepted": [f for f in flags if f not in remaining]}
