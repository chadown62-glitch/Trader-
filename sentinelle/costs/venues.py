"""Grilles tarifaires et contraintes de marché des plateformes (frais, pas de prix et de quantité, minimums).

- Les contraintes de marché (pas de prix, pas de quantité, montant minimal) sont lues dans le catalogue
  public de la plateforme : OBSERVED, datées et hachées.
- La grille de frais n'est pas exposée par l'interface publique de Binance sans clé. Elle est donc
  DECLARED si la page publique a pu être archivée, sinon HYPOTHETICAL (valeur publique connue, non
  vérifiée ici). Le rapport dit lequel des deux s'applique.
- Règle de l'actif des frais (Binance, comptant) : sans paiement en BNB, la commission est prélevée
  dans l'actif REÇU (l'actif acheté pour un achat, la devise de cotation pour une vente). C'est ce qui
  crée des reliquats invendables (voir ``inventory``). Elle est traitée comme une hypothèse déclarée.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from .evidence import Value


@dataclass(frozen=True)
class FeeSchedule:
    venue: str
    tier: str
    maker: Value  # points de base
    taker: Value
    discounted_maker: Value | None  # paiement des frais dans le jeton de la plateforme
    discounted_taker: Value | None
    fee_asset_rule: Value  # 1 = actif reçu, 0 = jeton de la plateforme (valeur symbolique, la règle est dans ``note``)

    def rate(self, *, maker: bool, discounted: bool = False) -> float:
        v = (self.discounted_maker if maker else self.discounted_taker) if discounted else (self.maker if maker else self.taker)
        if v is None:
            raise ValueError("Remise non disponible sur cette plateforme")
        return v.value / 1e4


def binance_spot_vip0(*, page_sha256: str | None = None, as_of: str | None = None) -> FeeSchedule:
    """Niveau standard (VIP 0) du marché au comptant de Binance.

    Sans archive de la page publique : HYPOTHETICAL (valeurs publiques connues au 16/11/2026, non
    vérifiées ici). Avec archive (empreinte et date) : DECLARED.
    """
    src = "https://www.binance.com/en/fee/schedule"
    if page_sha256 and as_of:
        def val(x, note):
            return Value(x, "bps", "DECLARED", src, as_of=as_of, sha256=page_sha256, note=note)
    else:
        def val(x, note):
            return Value(x, "bps", "HYPOTHETICAL", src, note=note + " ; grille publique connue, non vérifiée dans cet environnement")
    return FeeSchedule(
        "binance", "spot VIP 0",
        maker=val(10.0, "commission maker 0,10 %"), taker=val(10.0, "commission taker 0,10 %"),
        discounted_maker=val(7.5, "remise de 25 % si les frais sont payés en BNB"),
        discounted_taker=val(7.5, "remise de 25 % si les frais sont payés en BNB"),
        fee_asset_rule=Value(1.0, "rule", "HYPOTHETICAL", "https://www.binance.com/en/support/faq",
                             note="sans BNB, la commission est prélevée dans l'actif reçu (achat : actif acheté ; vente : devise de cotation)"),
    )


@dataclass(frozen=True)
class MarketFilters:
    symbol: str
    base: str
    quote: str
    status: str
    tick: Decimal  # pas de prix
    step: Decimal  # pas de quantité (ordres à cours limité)
    market_step: Decimal  # pas de quantité des ordres au marché
    min_qty: Decimal
    min_notional: Decimal  # montant minimal d'un ordre, en devise de cotation
    min_notional_applies_to_market: bool
    evidence: Value

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "base": self.base, "quote": self.quote, "status": self.status, "tick": str(self.tick),
                "step": str(self.step), "market_step": str(self.market_step), "min_qty": str(self.min_qty),
                "min_notional": str(self.min_notional), "min_notional_applies_to_market": self.min_notional_applies_to_market,
                "evidence": self.evidence.to_dict()}


def parse_exchange_info(raw: bytes, *, as_of: str, symbols: set[str] | None = None) -> dict[str, MarketFilters]:
    """Filtres de chaque paire, lus dans la réponse brute ``exchangeInfo`` (empreinte conservée)."""
    digest = hashlib.sha256(raw).hexdigest()
    info = json.loads(raw)
    out = {}
    for s in info["symbols"]:
        if symbols is not None and s["symbol"] not in symbols:
            continue
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        mlot = f.get("MARKET_LOT_SIZE", {})
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        step = Decimal(lot.get("stepSize", "0"))
        mstep = Decimal(mlot.get("stepSize", "0")) or step
        if step <= 0 or Decimal(f.get("PRICE_FILTER", {}).get("tickSize", "0")) <= 0:
            continue
        out[s["symbol"]] = MarketFilters(
            s["symbol"], s["baseAsset"], s["quoteAsset"], s["status"], Decimal(f["PRICE_FILTER"]["tickSize"]), step, mstep,
            Decimal(lot.get("minQty", "0")), Decimal(notional.get("minNotional", "0")),
            bool(notional.get("applyMinToMarket", notional.get("applyToMarket", True))),
            Value(1.0, "filters", "OBSERVED", "Binance exchangeInfo (public)", as_of=as_of, sha256=digest))
    return out
