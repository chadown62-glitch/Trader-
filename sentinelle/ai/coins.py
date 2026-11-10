"""Connaissances générales sur les cryptos (faits stables, arrêtés début 2026).

Elles servent à trois choses :
- expliquer à l'utilisateur ce qu'il trade (fiche de chaque crypto dans le panel) ;
- aider l'analyse des actualités à reconnaître de quelle crypto parle un article
  (« Ripple » désigne XRP, « Ether » désigne ETH…) ;
- rappeler les risques propres à chaque crypto.

Elles ne déclenchent jamais d'achat ni de vente : les mesures faites sur
l'historique montrent que le passé d'une crypto ne prédit presque rien de ses
résultats futurs (voir docs/RESULTATS.md).
"""

from __future__ import annotations

import re

KNOWLEDGE_DATE = "début 2026"

# base -> fiche. « aliases » : noms sans ambiguïté utilisés par la presse (en minuscules).
# « ticker_ok » : le sigle seul, en majuscules, est-il sans ambiguïté dans un titre ?
COINS: dict[str, dict] = {
    "BTC": {
        "name": "Bitcoin", "kind": "Réserve de valeur", "since": 2009,
        "what": "Première crypto, créée en 2009. Son offre est limitée à 21 millions d'unités, et la création de nouveaux bitcoins "
                "est divisée par deux environ tous les quatre ans (« halving », le dernier en avril 2024).",
        "drivers": "Taux d'intérêt et décisions de la Fed, entrées et sorties des fonds cotés (ETF au comptant autorisés aux États-Unis "
                   "en janvier 2024), régulation, appétit général pour le risque.",
        "risks": "Sert de référence à tout le marché : quand le Bitcoin chute, presque toutes les autres cryptos chutent davantage.",
        "aliases": ["bitcoin"], "ticker_ok": True,
    },
    "ETH": {
        "name": "Ethereum", "kind": "Plateforme d'applications", "since": 2015,
        "what": "Principale plateforme de « contrats intelligents » (finance décentralisée, jetons, NFT). Le réseau fonctionne par "
                "preuve d'enjeu depuis septembre 2022 et détruit une partie des frais payés.",
        "drivers": "Activité sur le réseau, mises à jour techniques, concurrence d'autres plateformes, fonds cotés (ETF au comptant "
                   "aux États-Unis depuis juillet 2024).",
        "risks": "Concurrence de plateformes plus rapides, complexité des mises à jour, dépendance à l'activité de la finance décentralisée.",
        "aliases": ["ethereum", "ether"], "ticker_ok": True,
    },
    "SOL": {
        "name": "Solana", "kind": "Plateforme d'applications", "since": 2020,
        "what": "Blockchain rapide aux frais très faibles, très utilisée pour les échanges décentralisés et les « memecoins ».",
        "drivers": "Activité sur le réseau, modes spéculatives (memecoins), annonces de l'écosystème.",
        "risks": "Plusieurs pannes complètes du réseau entre 2021 et 2024. Très touchée par la faillite de FTX en novembre 2022. Très volatile.",
        "aliases": ["solana"], "ticker_ok": True,
    },
    "BNB": {
        "name": "BNB", "kind": "Jeton de plateforme", "since": 2017,
        "what": "Jeton de la plateforme d'échange Binance et de la blockchain BNB Chain. Une partie des jetons est détruite chaque trimestre.",
        "drivers": "Santé et volumes de Binance, lancements de nouveaux projets sur la plateforme.",
        "risks": "Dépend d'une seule entreprise : toute difficulté réglementaire ou judiciaire de Binance pèse directement sur le prix "
                 "(accord avec la justice américaine en novembre 2023).",
        "aliases": ["binance coin", "bnb chain"], "ticker_ok": True,
    },
    "XRP": {
        "name": "XRP", "kind": "Paiements", "since": 2012,
        "what": "Jeton associé à l'entreprise Ripple, conçu pour les paiements internationaux rapides.",
        "drivers": "Décisions de justice et de régulation aux États-Unis, partenariats bancaires de Ripple.",
        "risks": "Un long procès avec le régulateur américain (SEC, de 2020 à 2025) a provoqué de très fortes variations. "
                 "Ripple détient une grande partie de l'offre.",
        "aliases": ["ripple"], "ticker_ok": True,
    },
    "DOGE": {
        "name": "Dogecoin", "kind": "Memecoin", "since": 2013,
        "what": "Crypto née comme une plaisanterie en 2013. Son offre n'est pas plafonnée : environ 5 milliards de nouveaux jetons par an.",
        "drivers": "Réseaux sociaux et déclarations de personnalités, en particulier Elon Musk.",
        "risks": "Aucune utilité économique solide : le prix dépend surtout de l'engouement du moment. Mouvements très brusques.",
        "aliases": ["dogecoin"], "ticker_ok": True,
    },
    "ADA": {
        "name": "Cardano", "kind": "Plateforme d'applications", "since": 2017,
        "what": "Blockchain à preuve d'enjeu développée selon une démarche académique. Offre plafonnée à 45 milliards de jetons.",
        "drivers": "Mises à jour du réseau, annonces de son fondateur, cycles généraux du marché.",
        "risks": "Écosystème plus petit que ceux d'Ethereum ou de Solana ; développement jugé lent.",
        "aliases": ["cardano"], "ticker_ok": False,
    },
    "LINK": {
        "name": "Chainlink", "kind": "Infrastructure", "since": 2017,
        "what": "Réseau d'« oracles » : il fournit aux contrats intelligents des données du monde réel (prix, taux…). Offre plafonnée à 1 milliard.",
        "drivers": "Adoption par la finance décentralisée et par des institutions financières, nouveaux services du réseau.",
        "risks": "Dépend de la santé de la finance décentralisée ; concurrence d'autres fournisseurs de données.",
        "aliases": ["chainlink"], "ticker_ok": False,
    },
    "AVAX": {
        "name": "Avalanche", "kind": "Plateforme d'applications", "since": 2020,
        "what": "Plateforme de contrats intelligents qui permet de créer des blockchains spécialisées. Offre plafonnée à 720 millions.",
        "drivers": "Activité de l'écosystème, partenariats institutionnels.",
        "risks": "Forte concurrence entre plateformes ; très sensible aux cycles du marché.",
        "aliases": ["avalanche"], "ticker_ok": True,
    },
    "DOT": {
        "name": "Polkadot", "kind": "Plateforme d'applications", "since": 2020,
        "what": "Réseau qui relie plusieurs blockchains spécialisées entre elles.",
        "drivers": "Décisions de gouvernance, activité des blockchains reliées.",
        "risks": "Adoption inférieure aux attentes de départ ; forte baisse depuis son plus haut de 2021.",
        "aliases": ["polkadot"], "ticker_ok": False,
    },
    "LTC": {
        "name": "Litecoin", "kind": "Paiements", "since": 2011,
        "what": "Une des plus anciennes cryptos, dérivée de Bitcoin, avec des transactions plus rapides. Offre limitée à 84 millions.",
        "drivers": "Suit surtout le Bitcoin ; ses propres « halvings » (le dernier en août 2023).",
        "risks": "Peu d'innovations propres : intérêt en recul face aux projets plus récents.",
        "aliases": ["litecoin"], "ticker_ok": True,
    },
    "BCH": {
        "name": "Bitcoin Cash", "kind": "Paiements", "since": 2017,
        "what": "Scission de Bitcoin (août 2017) avec des blocs plus gros pour des paiements moins chers. Offre limitée à 21 millions.",
        "drivers": "Suit surtout le Bitcoin, avec des mouvements plus amples.",
        "risks": "Usage réel limité ; liquidité plus faible que celle du Bitcoin.",
        "aliases": ["bitcoin cash"], "ticker_ok": True,
    },
    "TRX": {
        "name": "TRON", "kind": "Plateforme d'applications", "since": 2017,
        "what": "Blockchain très utilisée pour les transferts de stablecoins (USDT).",
        "drivers": "Volumes de transferts de stablecoins, annonces de son fondateur Justin Sun.",
        "risks": "Projet très lié à son fondateur, plusieurs fois visé par des régulateurs.",
        "aliases": ["tron"], "ticker_ok": True,
    },
    "NEAR": {
        "name": "NEAR Protocol", "kind": "Plateforme d'applications", "since": 2020,
        "what": "Plateforme de contrats intelligents qui mise sur la simplicité d'usage et, plus récemment, sur l'intelligence artificielle.",
        "drivers": "Thème de l'intelligence artificielle, activité de l'écosystème.",
        "risks": "Concurrence forte ; très sensible aux modes du marché.",
        "aliases": ["near protocol"], "ticker_ok": False,
    },
    "UNI": {
        "name": "Uniswap", "kind": "Finance décentralisée", "since": 2020,
        "what": "Jeton de gouvernance de la principale plateforme d'échange décentralisée.",
        "drivers": "Volumes échangés sur Uniswap, décisions sur le partage des frais, régulation de la finance décentralisée.",
        "risks": "Régulation ; la valeur du jeton dépend des décisions de gouvernance sur le partage des frais.",
        "aliases": ["uniswap"], "ticker_ok": False,
    },
    "AAVE": {
        "name": "Aave", "kind": "Finance décentralisée", "since": 2020,
        "what": "Jeton du principal protocole de prêt et d'emprunt décentralisé.",
        "drivers": "Montants déposés dans le protocole, taux d'intérêt, santé de la finance décentralisée.",
        "risks": "Risque de piratage ou de défaut en chaîne propre aux protocoles de prêt.",
        "aliases": ["aave"], "ticker_ok": True,
    },
    "XLM": {
        "name": "Stellar", "kind": "Paiements", "since": 2014,
        "what": "Réseau de paiements internationaux à faible coût, proche de XRP dans son objectif.",
        "drivers": "Évolue souvent avec XRP ; partenariats dans les paiements.",
        "risks": "Adoption limitée ; forte dépendance à la fondation qui pilote le projet.",
        "aliases": ["stellar"], "ticker_ok": True,
    },
    "ATOM": {
        "name": "Cosmos", "kind": "Plateforme d'applications", "since": 2019,
        "what": "Réseau de blockchains indépendantes capables de communiquer entre elles.",
        "drivers": "Décisions de gouvernance, activité des blockchains de l'écosystème.",
        "risks": "Offre inflationniste ; désaccords fréquents au sein de la communauté.",
        "aliases": ["cosmos hub"], "ticker_ok": False,
    },
    "TON": {
        "name": "Toncoin", "kind": "Plateforme d'applications", "since": 2021,
        "what": "Blockchain liée à l'écosystème de la messagerie Telegram.",
        "drivers": "Annonces de Telegram, popularité des mini-applications et des jeux sur Telegram.",
        "risks": "Très dépendante de Telegram et de son dirigeant (son arrestation en France en août 2024 a fait chuter le prix).",
        "aliases": ["toncoin"], "ticker_ok": False,
    },
    "SHIB": {
        "name": "Shiba Inu", "kind": "Memecoin", "since": 2020,
        "what": "Memecoin lancé en 2020 sur Ethereum, inspiré du Dogecoin.",
        "drivers": "Réseaux sociaux, modes spéculatives.",
        "risks": "Aucune utilité économique solide ; mouvements extrêmes dans les deux sens.",
        "aliases": ["shiba inu"], "ticker_ok": True,
    },
    "PEPE": {
        "name": "Pepe", "kind": "Memecoin", "since": 2023,
        "what": "Memecoin lancé en avril 2023 sur Ethereum.",
        "drivers": "Réseaux sociaux, modes spéculatives.",
        "risks": "Aucune utilité économique ; l'un des actifs les plus volatils du marché.",
        "aliases": ["pepe coin", "pepecoin"], "ticker_ok": True,
    },
    "SUI": {
        "name": "Sui", "kind": "Plateforme d'applications", "since": 2023,
        "what": "Plateforme de contrats intelligents récente (mai 2023), créée par d'anciens ingénieurs de Meta.",
        "drivers": "Croissance de l'écosystème, mises en circulation programmées de nouveaux jetons.",
        "risks": "Projet jeune ; une grande part des jetons arrive progressivement sur le marché, ce qui pèse sur le prix.",
        "aliases": ["sui network"], "ticker_ok": True,
    },
    "APT": {
        "name": "Aptos", "kind": "Plateforme d'applications", "since": 2022,
        "what": "Plateforme de contrats intelligents (octobre 2022) issue des travaux de Meta sur le projet Diem.",
        "drivers": "Partenariats, mises en circulation programmées de nouveaux jetons.",
        "risks": "Projet jeune ; déblocages réguliers de jetons détenus par les investisseurs de départ.",
        "aliases": ["aptos"], "ticker_ok": False,
    },
    "ARB": {
        "name": "Arbitrum", "kind": "Extension d'Ethereum", "since": 2023,
        "what": "Réseau de « seconde couche » qui rend les transactions Ethereum plus rapides et moins chères.",
        "drivers": "Activité sur Ethereum et ses extensions, déblocages de jetons.",
        "risks": "Jeton de gouvernance sans revenu direct ; concurrence d'autres réseaux de seconde couche.",
        "aliases": ["arbitrum"], "ticker_ok": False,
    },
    "OP": {
        "name": "Optimism", "kind": "Extension d'Ethereum", "since": 2022,
        "what": "Réseau de « seconde couche » d'Ethereum, dont la technologie est reprise par d'autres réseaux.",
        "drivers": "Activité sur Ethereum et ses extensions, déblocages de jetons.",
        "risks": "Jeton de gouvernance sans revenu direct ; concurrence d'autres réseaux de seconde couche.",
        "aliases": ["optimism"], "ticker_ok": False,
    },
    "ZEC": {
        "name": "Zcash", "kind": "Confidentialité", "since": 2016,
        "what": "Crypto axée sur la confidentialité des transactions. Offre limitée à 21 millions, avec des « halvings » comme le Bitcoin.",
        "drivers": "Intérêt pour la confidentialité, décisions des plateformes d'échange.",
        "risks": "Les cryptos de confidentialité sont régulièrement retirées de plateformes sous la pression des régulateurs.",
        "aliases": ["zcash"], "ticker_ok": True,
    },
    "HBAR": {
        "name": "Hedera", "kind": "Plateforme d'applications", "since": 2019,
        "what": "Réseau piloté par un conseil de grandes entreprises, qui vise les usages professionnels.",
        "drivers": "Annonces de partenariats avec des entreprises.",
        "risks": "Gouvernance centralisée ; activité réelle modeste par rapport à la valorisation.",
        "aliases": ["hedera"], "ticker_ok": True,
    },
    "ETC": {
        "name": "Ethereum Classic", "kind": "Plateforme d'applications", "since": 2016,
        "what": "Chaîne d'origine d'Ethereum, conservée après la scission de 2016. Fonctionne toujours par preuve de travail.",
        "drivers": "Suit surtout Ethereum et le Bitcoin.",
        "risks": "Peu de développement ; le réseau a subi plusieurs attaques en 2019 et 2020.",
        "aliases": ["ethereum classic"], "ticker_ok": True,
    },
    "FIL": {
        "name": "Filecoin", "kind": "Infrastructure", "since": 2020,
        "what": "Réseau de stockage de fichiers décentralisé.",
        "drivers": "Demande de stockage, thème de l'infrastructure décentralisée.",
        "risks": "Très forte baisse depuis son plus haut de 2021 ; usage réel difficile à mesurer.",
        "aliases": ["filecoin"], "ticker_ok": True,
    },
    "ICP": {
        "name": "Internet Computer", "kind": "Plateforme d'applications", "since": 2021,
        "what": "Blockchain qui veut héberger directement des applications et des sites web.",
        "drivers": "Annonces techniques, thème de l'intelligence artificielle.",
        "risks": "A perdu plus de 90 % dans les mois qui ont suivi son lancement en mai 2021.",
        "aliases": ["internet computer", "dfinity"], "ticker_ok": True,
    },
    "INJ": {
        "name": "Injective", "kind": "Finance décentralisée", "since": 2020,
        "what": "Blockchain spécialisée dans les applications financières.",
        "drivers": "Activité des applications financières, destructions de jetons.",
        "risks": "Capitalisation moyenne : mouvements amples et liquidité plus faible.",
        "aliases": ["injective"], "ticker_ok": True,
    },
}

GENERIC = {
    "name": None, "kind": "Crypto", "since": None,
    "what": "Pas de fiche descriptive pour cette crypto : seules les statistiques calculées sur son historique sont disponibles.",
    "drivers": "Comme la plupart des cryptos : évolution du Bitcoin, annonces du projet, régulation.",
    "risks": "Les petites cryptos sont en général plus volatiles et moins liquides que le Bitcoin ou Ethereum.",
    "aliases": [], "ticker_ok": False,
}


def coin_info(base: str) -> dict:
    """Fiche générale d'une crypto (fiche générique si elle n'est pas connue)."""
    info = dict(COINS.get(base.upper(), GENERIC))
    info["base"] = base.upper()
    info["known"] = base.upper() in COINS
    info["name"] = info["name"] or base.upper()
    return info


def glossary(bases) -> str:
    """Liste « SIGLE (nom, autres noms) » pour aider l'IA locale à reconnaître les cryptos d'un article."""
    parts = []
    for b in sorted({x.upper() for x in bases}):
        c = COINS.get(b)
        if not c:
            parts.append(b)
            continue
        names = [c["name"]] + [a.title() for a in c["aliases"] if a.lower() != c["name"].lower()]
        parts.append(f"{b} ({', '.join(dict.fromkeys(names))})")
    return ", ".join(parts)


def detect_assets(text: str, allowed) -> list[str]:
    """Cryptos citées sans ambiguïté dans un texte (nom complet, ou sigle en majuscules s'il est sûr)."""
    found = []
    low = (text or "").lower()
    for b in sorted({x.upper() for x in allowed}):
        c = COINS.get(b)
        if not c:
            continue
        names = {c["name"].lower(), *c["aliases"]} - {b.lower()}
        hit = any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", low) for n in names if len(n) >= 4)
        if not hit and c["ticker_ok"]:
            hit = re.search(rf"(?<![A-Za-z0-9])\$?{re.escape(b)}(?![A-Za-z0-9])", text or "") is not None
        if hit:
            found.append(b)
    return found


def resolve_asset(value: str, allowed) -> str | None:
    """Convertit ce que renvoie l'IA locale (« Ripple », « btc », « $SOL ») en sigle autorisé."""
    v = str(value or "").strip().lstrip("$").upper()
    allowed = {x.upper() for x in allowed}
    if v in allowed or v == "*":
        return v if v in allowed else None
    low = v.lower()
    for b in allowed:
        c = COINS.get(b)
        if c and (low == c["name"].lower() or low in c["aliases"]):
            return b
    return None
