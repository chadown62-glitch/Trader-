"""Hypothèses de coûts du hub qui ne peuvent PAS être observées publiquement ici, versionnées et figées.

Chaque valeur a une fourchette prudente et une justification. Le routeur vérifie que son choix résiste aux
deux bornes ; un choix qui en dépend est signalé « sensible aux hypothèses ». Toute modification impose une
nouvelle version (l'empreinte de ce contenu est enregistrée avec chaque décision).

Les mesures de COST OPTIMIZER (#50, contrat `cost_contract_v1`) servent d'a priori pour les ordres sur
plateformes centralisées : glissement au-delà de l'écart (principal 0, tension 2,5 à 5,7 pb), écarts de tension,
taux de remplissage et coût des ordres à cours limité (règle prudente).
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

VERSION = "hub-cost-assumptions-1.0.0"

_W = "barème de retrait des plateformes non consultable sans compte : fourchette prudente"
WITHDRAWAL_EUR = MappingProxyType({  # (réseau, actif) -> (central, bas, haut) en euros, par retrait
    ("solana", "USDC"): (1.0, 0.1, 2.0, _W), ("base", "USDC"): (0.5, 0.0, 1.5, _W), ("ethereum", "USDC"): (4.0, 1.0, 15.0, _W),
    ("solana", "SOL"): (0.5, 0.05, 1.0, _W), ("base", "ETH"): (0.3, 0.0, 1.5, _W), ("ethereum", "ETH"): (2.0, 0.5, 8.0, _W),
})
GAS_PROVISION_EUR = 1.0  # jeton natif acheté pour payer les frais de réseau d'une série d'opérations (reliquat ensuite)
GAS_UNITS = MappingProxyType({  # (central, bas, haut)
    "erc20_approve": (46_000, 30_000, 60_000), "erc20_transfer": (65_000, 45_000, 80_000),
})
SOLANA_SWAP_CU = (300_000, 150_000, 1_400_000)  # unités de calcul d'un échange agrégé
SOLANA_TRANSFER_CU = (20_000, 5_000, 60_000)
FAILURE_PROB = MappingProxyType({  # transaction simulée échouée (congestion, prix parti, devis expiré)
    "ethereum": (0.02, 0.0, 0.10), "base": (0.02, 0.0, 0.10), "solana": (0.05, 0.01, 0.25), "cex": (0.0, 0.0, 0.02),
})
MEV_SHARE_OF_TOLERANCE = MappingProxyType({  # perte face aux robots : 0 en moyenne sur de petits montants, jusqu'à la tolérance
    "ethereum": (0.0, 0.0, 1.0), "base": (0.0, 0.0, 0.5), "solana": (0.0, 0.0, 1.0),
})
LATENCY_MS = MappingProxyType({  # (confirmation, finalité)
    "cex": (500, 500), "solana": (1_000, 13_000), "base": (2_000, 900_000), "ethereum": (12_000, 780_000),
})
DEX_SLIPPAGE_TOLERANCE_BPS = 50.0
# A priori mesurés par COST OPTIMIZER (#50) sur Binance, transposés aux autres plateformes faute de mieux.
CEX_SLIPPAGE_BPS = MappingProxyType({"eur": (0.0, 0.0, 5.39), "major": (0.0, 0.0, 2.55), "other": (0.0, 0.0, 5.73)})
CEX_EXIT_HALF_SPREAD_STRESS_BPS = MappingProxyType({"eur": 3.42, "major": 0.42, "other": 4.19, "memecoin": 7.71, "stable": 4.40})
# Ordres à cours limité, règle prudente, attente 5 min, annulation si non rempli (#50, toutes classes)
LIMIT_POLICY = MappingProxyType({"fill": (0.82, 0.78, 0.86), "vs_fee_bps": (-1.48, -1.80, -1.10),
                                 "note": "COST OPTIMIZER : improve/300s/conservative/cancel ; amélioration face au marché non démontrée"})
MAJORS = frozenset({"BTC", "ETH"})
MEMECOINS = frozenset({"DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF"})
STABLECOINS = frozenset({"USDC", "USDT", "EURC"})
# Places d'échange décentralisées établies et documentées publiquement (étiquettes des routes Jupiter). Toute autre
# étiquette = contrat inconnu : la route est refusée (pas d'interdiction de principe, une absence de preuve).
KNOWN_SOLANA_AMMS = frozenset({"Whirlpool", "Raydium", "Raydium CLMM", "Raydium CP", "Meteora", "Meteora DLMM",
                               "Meteora DAMM v2", "Phoenix", "OpenBook V2", "Lifinity V2", "JupiterZ"})


def asset_class(asset: str, quote: str = "EUR") -> str:
    if asset in STABLECOINS:
        return "stable"
    if asset in MEMECOINS:
        return "memecoin"
    if quote == "EUR":
        return "eur"
    return "major" if asset in MAJORS else "other"


def export() -> dict:
    def conv(v):
        if isinstance(v, MappingProxyType):
            return {(" / ".join(k) if isinstance(k, tuple) else k): conv(x) for k, x in v.items()}
        if isinstance(v, frozenset):
            return sorted(v)
        if isinstance(v, tuple):
            return list(v)
        return v
    names = ["VERSION", "WITHDRAWAL_EUR", "GAS_PROVISION_EUR", "GAS_UNITS", "SOLANA_SWAP_CU", "SOLANA_TRANSFER_CU", "FAILURE_PROB",
             "MEV_SHARE_OF_TOLERANCE", "LATENCY_MS", "DEX_SLIPPAGE_TOLERANCE_BPS", "CEX_SLIPPAGE_BPS",
             "CEX_EXIT_HALF_SPREAD_STRESS_BPS", "LIMIT_POLICY", "MAJORS", "MEMECOINS", "STABLECOINS", "KNOWN_SOLANA_AMMS"]
    return {n: conv(globals()[n]) for n in names}


def digest() -> str:
    return hashlib.sha256(json.dumps(export(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
