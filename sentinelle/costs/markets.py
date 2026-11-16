"""Modèles de coûts spécialisés par marché.

Les familles reprennent celles de l'architecture multi-marchés (PR #47) : crypto, memecoin, equity,
forex, etf, commodity. Aucun module de cette PR n'est importé ici ; la correspondance se fait par nom.

- ``crypto`` (plateforme centralisée, comptant) : implémentation complète, nourrie par les mesures ;
- ``memecoin`` :
  * sur plateforme centralisée (DOGE, SHIB, PEPE…) : même moteur que ``crypto``, classe mesurée à part ;
  * sur plateforme décentralisée (DEX, sur la chaîne) : HYPOTHÈSES seulement. Aucune donnée de réseau
    n'est collectée ici ; le modèle décrit les composantes (frais de réseau, frais du pool, impact,
    taxe du jeton, extraction par les robots) avec des fourchettes déclarées hypothétiques, et refuse
    de produire un coût « observé » ;
- ``equity``, ``etf``, ``forex``, ``commodity`` : non implémentés. Le modèle REFUSE d'estimer plutôt
  que d'inventer un chiffre.
"""
from __future__ import annotations

from dataclasses import dataclass

from .evidence import Value, hypothesis

FAMILIES = ("crypto", "memecoin", "equity", "forex", "etf", "commodity")
CRYPTO_CLASSES = ("majors_usdt", "others_usdt", "memecoin_usdt", "eur_direct", "fx_eurusdt")
MAJORS = ("BTC", "ETH")
CEX_MEMECOINS = ("DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF")


class CostModelUnavailable(LookupError):
    """Aucun modèle mesuré pour ce marché : on ne devine pas."""


@dataclass(frozen=True)
class ClassCosts:
    """Composantes d'un aller simple (un ordre) pour une classe d'actifs, en points de base du montant."""
    name: str
    fee_taker: Value
    fee_maker: Value
    half_spread: Value
    slippage: Value  # au-delà du demi-écart : impact et latence
    conversion: Value  # conversion vers la devise du portefeuille, par ordre (0 si inutile)
    dust_share: Value  # part du montant immobilisée en reliquat par aller-retour (frais prélevés dans l'actif reçu)

    def one_way_bps(self, *, maker: bool = False) -> float:
        fee = self.fee_maker if maker else self.fee_taker
        return fee.value + (0.0 if maker else self.half_spread.value + self.slippage.value) + self.conversion.value

    def round_trip_bps(self, *, maker: bool = False) -> float:
        return 2 * self.one_way_bps(maker=maker)

    def to_dict(self) -> dict:
        return {k: getattr(self, k).to_dict() for k in ("fee_taker", "fee_maker", "half_spread", "slippage", "conversion", "dust_share")} | {
            "name": self.name, "round_trip_taker_bps": self.round_trip_bps()}


def classify(base: str, quote: str) -> str:
    """Classe de coûts d'une paire (règle fixe, connue avant toute mesure)."""
    if base == "EUR" and quote == "USDT":
        return "fx_eurusdt"
    if quote == "EUR":
        return "eur_direct"
    if quote != "USDT":
        raise CostModelUnavailable(f"Devise de cotation non couverte : {quote}")
    if base in MAJORS:
        return "majors_usdt"
    if base in CEX_MEMECOINS:
        return "memecoin_usdt"
    return "others_usdt"


def dex_memecoin_hypotheses() -> dict[str, Value]:
    """Composantes d'un échange de memecoin sur la chaîne : fourchettes HYPOTHÉTIQUES, aucune mesure.

    Les fourchettes de frais de réseau sont des ordres de grandeur déclarés comme hypothèses ; avec un
    portefeuille de 30 €, une seule transaction sur une chaîne chère peut coûter plusieurs pour cent.
    """
    return {
        "network_fee_eur_ethereum": hypothesis(1.0, "frais de transaction d'un échange sur Ethereum, très variables selon l'encombrement",
                                               unit="EUR", low=0.2, high=20.0),
        "network_fee_eur_solana": hypothesis(0.002, "frais de base et de priorité d'une transaction Solana", unit="EUR", low=0.0005, high=0.5),
        "network_fee_eur_bsc": hypothesis(0.05, "frais de transaction sur BNB Smart Chain", unit="EUR", low=0.01, high=0.5),
        "pool_fee_bps": hypothesis(30.0, "frais du pool d'échange (souvent 0,25 à 1 %)", low=25.0, high=100.0),
        "token_tax_bps": hypothesis(0.0, "taxe de transfert codée dans certains jetons ; peut être confiscatoire (jetons piégés)",
                                    low=0.0, high=2000.0),
        "mev_bps": hypothesis(0.0, "perte face aux robots qui s'intercalent autour d'un ordre visible", low=0.0, high=200.0),
        "price_impact": hypothesis(0.0, "dépend des réserves du pool, non collectées : non estimable ici", unit="unknown"),
    }


def model_for(family: str, *, venue: str = "cex"):
    """Point d'entrée : le modèle disponible pour une famille, ou un refus explicite."""
    if family not in FAMILIES:
        raise CostModelUnavailable(f"Famille inconnue : {family}")
    if family == "crypto" or (family == "memecoin" and venue == "cex"):
        return "crypto_cex"
    if family == "memecoin" and venue == "dex":
        return "memecoin_dex_hypothetical"
    raise CostModelUnavailable(f"Aucun modèle de coûts mesuré pour « {family} » : non implémenté, aucune estimation fournie")
