"""Réglages : valeurs par défaut, bornes de sécurité et validation.

Les réglages sont stockés en base (table `settings`) et modifiables depuis le panel.
Chaque valeur est bornée : certaines limites (risque par trade, levier, baisse
maximale) ont un plafond absolu qu'aucun réglage ne peut dépasser.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Emplacements
# ---------------------------------------------------------------------------


def default_data_dir() -> Path:
    """Dossier des données locales (base, historique, modèles, secrets)."""
    env = os.environ.get("SENTINELLE_DATA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Valeurs par défaut
# ---------------------------------------------------------------------------

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
AI_TIMEFRAMES = ["15m", "1h", "4h", "1d"]
STRATEGY_NAMES = ["trend_breakout", "pullback", "mean_reversion", "momentum", "trend_follow"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "general": {
        "quote": "EUR",
        # Cryptos parmi les plus liquides (univers utilisé pour valider la configuration, voir docs/RESULTATS.md)
        "watchlist": ["BTC/EUR", "ETH/EUR", "SOL/EUR", "BNB/EUR", "XRP/EUR", "DOGE/EUR", "ADA/EUR", "LINK/EUR"],
    },
    "market": {
        # Source des données de marché publiques (prix en direct, historique).
        # 'synthetic' = marché simulé hors-ligne (démo / tests).
        "source": "binance",
        # Miroir officiel de Binance pour les données publiques (sans compte).
        "binance_public_mirror": True,
        "poll_seconds": 5,
        "history_days": 1460,
        # Vérification du prix sur une seconde source (Kraken) avant chaque entrée de l'IA.
        "cross_check": True,
        "cross_check_max_pct": 2.0,
        # Indice Fear & Greed, indices boursiers, taux de financement (affichage et contexte).
        "context": True,
    },
    "paper": {
        "fee_rate": 0.001,  # 0,1 % par ordre (hypothèse prudente)
        "slippage_bps": 5.0,  # glissement de prix simulé : 0,05 %
        "initial_balance": 30.0,
    },
    "ai": {
        # 4 h : meilleur compromis mesuré entre nombre de trades et poids des frais (docs/RESULTATS.md)
        "timeframe": "4h",
        # Seules les stratégies de tendance ont une espérance positive après frais sur l'historique testé.
        "strategies": {name: name in ("trend_follow", "trend_breakout") for name in STRATEGY_NAMES},
        # En entraînement (argent fictif) : tant qu'aucun modèle n'est validé,
        # l'IA prend les signaux de base avec une taille réduite pour apprendre.
        "exploration": True,
        "exploration_risk_factor": 0.5,
        # Apprentissage continu à partir des résultats observés en direct.
        "learning": True,
        "retrain_hours": 24,
        "min_new_samples": 25,
        # Avantage minimal attendu (en R) pour qu'un trade soit pris ;
        # None = valeur choisie automatiquement lors de l'entraînement.
        "min_edge_r": None,
        # Pas de nouvelle position quand le climat du marché est baissier.
        "regime_filter": True,
        # Utiliser le score des actualités comme veto (si le module est actif).
        "use_news": True,
        # Apprentissage sur un historique plus long (paires USDT de mêmes cryptos, cotées plus tôt).
        "extended_history": True,
        # Si le filtre IA n'apporte rien mais que les règles seules sont prouvées, valider « règles seules ».
        "rules_fallback": True,
        # Recette du modèle. « mixte » (arbres simples + modèle linéaire) est celle qui apprend le mieux d'après le
        # banc d'essai et les données réelles ; « auto » choisit à chaque entraînement d'après des données jamais vues.
        "recipe": "mixte",
        # Oubli progressif : poids divisé par deux tous les N jours d'ancienneté (None = tout le passé pèse autant).
        "half_life_days": None,
    },
    "risk": {
        "risk_per_trade": 0.005,  # 0,5 % du capital perdu si le stop est touché
        "max_positions": 3,
        "max_exposure": 0.6,
        "max_exposure_per_asset": 0.3,
        "daily_loss_limit": 0.02,
        "weekly_loss_limit": 0.05,
        "max_drawdown": 0.10,
        "max_consecutive_losses": 4,
        "pause_hours_after_losses": 24,
        "min_reward_to_fees": 3.0,
        "cooldown_minutes_after_loss": 240,
    },
    "live": {
        # SIMULATION UNIQUEMENT. Les quatre réglages ci-dessous viennent de l'ancien « portefeuille réel ».
        # Ils sont conservés pour que d'anciens réglages enregistrés restent lisibles, mais ils sont
        # INERTES : aucun code ne les lit plus et aucune valeur ne peut activer le trading réel.
        "exchange": "binance",
        "testnet": True,
        "budget": 200.0,
        "manual_max_order_value": 500.0,
        # Seuils du bulletin de fiabilité de l'IA (preuves en argent fictif). Ils n'ouvrent aucun mode réel.
        "unlock_min_days": 30,
        "unlock_min_trades": 25,
        "unlock_max_drawdown": 0.10,
    },
    "news": {
        "enabled": False,
        "engine": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "model": "qwen2.5:7b",
        "poll_minutes": 10,
        "max_age_hours": 24,
        "veto_threshold": -0.6,
        "feeds": [
            "https://cointelegraph.com/rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://decrypt.co/feed",
            "https://bitcoinmagazine.com/.rss/full/",
            "https://www.theblock.co/rss.xml",
            # Sources officielles (« source zéro ») : banques centrales et régulateur américain
            "https://www.federalreserve.gov/feeds/press_all.xml",
            "https://www.ecb.europa.eu/rss/press.html",
            "https://www.sec.gov/news/pressreleases.rss",
        ],
        # Canaux Telegram de dépêches (flashs en quelques secondes), analysés dès réception.
        "telegram_channels": [],
    },
    "telegram": {
        "enabled": False,
        "chat_id": "",
        "notify_level": "important",  # 'all' | 'important' | 'critical'
        "daily_summary_hour": 20,
    },
    # Canaux Telegram de signaux, lus en OBSERVATION uniquement (fiabilité mesurée, jamais tradés).
    "signals": {
        "enabled": False,
        "channels": [],
        "horizon_hours": 72,
    },
    # Annonces macroéconomiques : pas de nouvelle position autour de ces heures (UTC).
    "macro": {
        "enabled": True,
        "blackout_minutes": 60,
        # Import automatique du calendrier économique (annonces à fort impact).
        "auto_calendar": True,
        "calendar_countries": ["USD", "EUR"],
        "events": [
            "2026-12-09 19:00 Fed : décision de taux (FOMC)",
        ],
    },
    # Bouclier temps réel : bloque les NOUVELLES positions quand le marché devient dangereux.
    "shield": {
        "enabled": True,
        "volatility_multiple": 6.0,  # bougie 1 min anormale : x fois l'amplitude habituelle
        "volatility_min_pct": 0.8,
        "cooldown_minutes": 30,
        "max_spread_bps": 20.0,  # écart achat/vente maximal (0,20 %)
        "depth_multiple": 3.0,  # liquidité disponible ≥ 3 × la taille de l'ordre
        "liquidations": True,
        # Mesuré sur 24 h réelles (BTC + ETH sur OKX) : 0,1 M$ par quart d'heure en temps normal,
        # 24 M$ lors de la chute de 1,9 % en une minute du 08/11/2026 à 21 h 55 UTC.
        "cascade_multiple": 20.0,
        "cascade_min_musd": 10.0,
        "news_pause": True,
        "news_pause_minutes": 60,
    },
    "monitoring": {
        # Adresse de « ping » d'un service de surveillance externe (ex. healthchecks.io) :
        # si Sentinelle ou le PC s'arrête, ce service vous prévient.
        "heartbeat_url": "",
        "backup_keep_days": 14,
    },
}

# ---------------------------------------------------------------------------
# Bornes de validation : chemin -> spécification
# ---------------------------------------------------------------------------
# ("float", min, max) | ("int", min, max) | ("bool",) | ("str", [choix] | None)
# | ("list_str",) | ("float_or_none", min, max)

BOUNDS: dict[str, tuple] = {
    "general.quote": ("str", ["USDC", "EUR", "USDT", "USD"]),
    "general.watchlist": ("list_str",),
    "market.source": ("str", ["binance", "kraken", "bybit", "okx", "coinbase", "synthetic"]),
    "market.binance_public_mirror": ("bool",),
    "market.poll_seconds": ("int", 2, 60),
    "market.history_days": ("int", 120, 3000),
    "paper.fee_rate": ("float", 0.0, 0.01),
    "paper.slippage_bps": ("float", 0.0, 100.0),
    "paper.initial_balance": ("float", 10.0, 10_000_000.0),
    "ai.timeframe": ("str", AI_TIMEFRAMES),
    "ai.exploration": ("bool",),
    "ai.exploration_risk_factor": ("float", 0.1, 1.0),
    "ai.learning": ("bool",),
    "ai.retrain_hours": ("int", 1, 24 * 14),
    "ai.min_new_samples": ("int", 5, 10_000),
    "ai.min_edge_r": ("float_or_none", 0.0, 2.0),
    "ai.regime_filter": ("bool",),
    "ai.use_news": ("bool",),
    # Plafonds absolus de sécurité (cf. dossier de conception §4.5)
    "risk.risk_per_trade": ("float", 0.001, 0.02),
    "risk.max_positions": ("int", 1, 5),
    "risk.max_exposure": ("float", 0.1, 1.0),
    "risk.max_exposure_per_asset": ("float", 0.05, 0.5),
    "risk.daily_loss_limit": ("float", 0.005, 0.10),
    "risk.weekly_loss_limit": ("float", 0.01, 0.20),
    "risk.max_drawdown": ("float", 0.02, 0.25),
    "risk.max_consecutive_losses": ("int", 2, 20),
    "risk.pause_hours_after_losses": ("int", 1, 24 * 7),
    "risk.min_reward_to_fees": ("float", 1.0, 20.0),
    "risk.cooldown_minutes_after_loss": ("int", 0, 24 * 60 * 7),
    "live.exchange": ("str", ["binance", "kraken", "bybit", "okx", "coinbase"]),
    "live.testnet": ("bool",),
    "live.budget": ("float", 10.0, 10_000_000.0),
    "live.manual_max_order_value": ("float", 5.0, 10_000_000.0),
    "live.unlock_min_days": ("int", 7, 365),
    "live.unlock_min_trades": ("int", 10, 10_000),
    "live.unlock_max_drawdown": ("float", 0.02, 0.25),
    "news.enabled": ("bool",),
    "news.engine": ("str", ["ollama"]),
    "news.ollama_url": ("str", None),
    "news.model": ("str", None),
    "news.poll_minutes": ("int", 2, 240),
    "news.max_age_hours": ("int", 1, 168),
    "news.veto_threshold": ("float", -1.0, 0.0),
    "news.feeds": ("list_str",),
    "telegram.enabled": ("bool",),
    "telegram.chat_id": ("str", None),
    "telegram.notify_level": ("str", ["all", "important", "critical"]),
    "telegram.daily_summary_hour": ("int", 0, 23),
    "market.cross_check": ("bool",),
    "market.cross_check_max_pct": ("float", 0.2, 10.0),
    "market.context": ("bool",),
    "ai.extended_history": ("bool",),
    "ai.rules_fallback": ("bool",),
    "ai.recipe": ("str", ["auto", "mixte", "prudent", "lineaire", "moyen", "riche", "comite"]),
    "ai.half_life_days": ("float_or_none", 90.0, 3650.0),
    "signals.enabled": ("bool",),
    "signals.channels": ("list_str",),
    "signals.horizon_hours": ("int", 4, 24 * 14),
    "macro.enabled": ("bool",),
    "macro.blackout_minutes": ("int", 0, 24 * 60),
    "macro.events": ("list_str",),
    "monitoring.heartbeat_url": ("str", None),
    "news.telegram_channels": ("list_str",),
    "macro.auto_calendar": ("bool",),
    "macro.calendar_countries": ("list_str",),
    "shield.enabled": ("bool",),
    "shield.volatility_multiple": ("float", 2.0, 50.0),
    "shield.volatility_min_pct": ("float", 0.1, 10.0),
    "shield.cooldown_minutes": ("int", 1, 24 * 60),
    "shield.max_spread_bps": ("float", 1.0, 500.0),
    "shield.depth_multiple": ("float", 0.5, 50.0),
    "shield.liquidations": ("bool",),
    "shield.cascade_multiple": ("float", 2.0, 200.0),
    "shield.cascade_min_musd": ("float", 1.0, 10_000.0),
    "shield.news_pause": ("bool",),
    "shield.news_pause_minutes": ("int", 5, 24 * 60),
    "monitoring.backup_keep_days": ("int", 1, 365),
}


class SettingsError(ValueError):
    """Réglage invalide (message lisible par l'utilisateur)."""


def deep_merge(base: dict, override: dict) -> dict:
    """Fusionne récursivement `override` dans une copie de `base`."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _check(path: str, value: Any) -> Any:
    spec = BOUNDS.get(path)
    if spec is None:
        raise SettingsError(f"Réglage inconnu : {path}")
    kind = spec[0]
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{path} doit être vrai/faux")
        return value
    if kind in ("float", "int", "float_or_none"):
        if value is None and kind == "float_or_none":
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{path} doit être un nombre")
        lo, hi = spec[1], spec[2]
        if not (lo <= value <= hi):
            raise SettingsError(f"{path} doit être compris entre {lo} et {hi}")
        return int(value) if kind == "int" else float(value)
    if kind == "str":
        if not isinstance(value, str):
            raise SettingsError(f"{path} doit être un texte")
        choices = spec[1]
        if choices is not None and value not in choices:
            raise SettingsError(f"{path} doit valoir : {', '.join(choices)}")
        if len(value) > 500:
            raise SettingsError(f"{path} est trop long")
        return value
    if kind == "list_str":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SettingsError(f"{path} doit être une liste de textes")
        if len(value) > 50:
            raise SettingsError(f"{path} : 50 éléments au maximum")
        return [v.strip() for v in value if v.strip()]
    raise SettingsError(f"Spécification inconnue pour {path}")  # pragma: no cover


def validate_settings(settings: dict) -> dict:
    """Valide un jeu complet de réglages. Lève SettingsError si invalide."""
    clean = deep_merge(DEFAULT_SETTINGS, {})
    for section, values in settings.items():
        if section not in DEFAULT_SETTINGS:
            raise SettingsError(f"Section inconnue : {section}")
        if not isinstance(values, dict):
            raise SettingsError(f"Section {section} invalide")
        for key, value in values.items():
            path = f"{section}.{key}"
            if path == "ai.strategies":
                if not isinstance(value, dict):
                    raise SettingsError("ai.strategies doit être un dictionnaire")
                strat = {}
                for name, enabled in value.items():
                    if name not in STRATEGY_NAMES:
                        raise SettingsError(f"Stratégie inconnue : {name}")
                    if not isinstance(enabled, bool):
                        raise SettingsError(f"ai.strategies.{name} doit être vrai/faux")
                    strat[name] = enabled
                clean["ai"]["strategies"] = {**clean["ai"]["strategies"], **strat}
                continue
            clean[section][key] = _check(path, value)

    # Cohérences entre réglages
    quote = clean["general"]["quote"]
    wl = clean["general"]["watchlist"]
    if not wl:
        raise SettingsError("La liste des cryptos suivies ne peut pas être vide")
    for sym in wl:
        if "/" not in sym or sym.split("/")[1] != quote:
            raise SettingsError(f"{sym} : chaque paire doit être cotée en {quote} (ex. BTC/{quote})")
    from .risk.macro import parse_event  # import local : évite un cycle

    for ev in clean["macro"]["events"]:
        if parse_event(ev) is None:
            raise SettingsError(f"Événement macro invalide : « {ev} » (format : AAAA-MM-JJ HH:MM description, heure UTC)")
    url = clean["monitoring"]["heartbeat_url"]
    if url and not url.startswith("https://"):
        raise SettingsError("L'adresse de surveillance doit commencer par https://")
    if clean["risk"]["weekly_loss_limit"] < clean["risk"]["daily_loss_limit"]:
        raise SettingsError("La limite de perte hebdomadaire doit être ≥ à la limite journalière")
    if clean["risk"]["max_exposure_per_asset"] > clean["risk"]["max_exposure"]:
        raise SettingsError("L'exposition par crypto ne peut pas dépasser l'exposition totale")
    return clean


# ---------------------------------------------------------------------------
# Migrations des réglages déjà enregistrés (appliquées une seule fois, au démarrage)
# ---------------------------------------------------------------------------
SETTINGS_REV = 2
OFFICIAL_FEEDS = [
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.ecb.europa.eu/rss/press.html",
    "https://www.sec.gov/news/pressreleases.rss",
]


def migrate_settings(stored: dict, rev: int) -> tuple[dict, bool]:
    """Met à niveau des réglages enregistrés par une version précédente. Renvoie (réglages, modifiés ?)."""
    changed = False
    if rev < 2:
        # Révision 2 : sources officielles (Fed, BCE, SEC) ajoutées à la liste de flux existante
        feeds = (stored.get("news") or {}).get("feeds")
        if isinstance(feeds, list):
            for url in OFFICIAL_FEEDS:
                if url not in feeds:
                    feeds.append(url)
                    changed = True
    return stored, changed


def apply_update(current: dict, patch: dict) -> dict:
    """Applique une mise à jour partielle et renvoie les réglages validés."""
    return validate_settings(deep_merge(current, patch))


def timeframe_minutes(tf: str) -> int:
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    return int(tf[:-1]) * units[tf[-1]]


def timeframe_ms(tf: str) -> int:
    return timeframe_minutes(tf) * 60_000
