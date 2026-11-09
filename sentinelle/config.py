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
        "quote": "USDC",
        # Cryptos parmi les plus liquides (univers utilisé pour valider la configuration, voir docs/RESULTATS.md)
        "watchlist": ["BTC/USDC", "ETH/USDC", "SOL/USDC", "BNB/USDC", "XRP/USDC", "DOGE/USDC", "ADA/USDC", "LINK/USDC"],
    },
    "market": {
        # Source des données de marché publiques (prix en direct, historique).
        # 'synthetic' = marché simulé hors-ligne (démo / tests).
        "source": "binance",
        # Miroir officiel de Binance pour les données publiques (sans compte).
        "binance_public_mirror": True,
        "poll_seconds": 5,
        "history_days": 1460,
    },
    "paper": {
        "fee_rate": 0.001,  # 0,1 % par ordre (hypothèse prudente)
        "slippage_bps": 5.0,  # glissement de prix simulé : 0,05 %
        "initial_balance": 10000.0,
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
        "exchange": "binance",
        # Testnet = plateforme de test officielle de l'exchange (argent fictif).
        "testnet": True,
        # Budget maximal que l'IA peut utiliser en réel, même si le compte contient plus.
        "budget": 200.0,
        # Protection contre les erreurs de saisie en trading manuel réel.
        "manual_max_order_value": 500.0,
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
        ],
    },
    "telegram": {
        "enabled": False,
        "chat_id": "",
        "notify_level": "important",  # 'all' | 'important' | 'critical'
        "daily_summary_hour": 20,
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
    if clean["risk"]["weekly_loss_limit"] < clean["risk"]["daily_loss_limit"]:
        raise SettingsError("La limite de perte hebdomadaire doit être ≥ à la limite journalière")
    if clean["risk"]["max_exposure_per_asset"] > clean["risk"]["max_exposure"]:
        raise SettingsError("L'exposition par crypto ne peut pas dépasser l'exposition totale")
    return clean


def apply_update(current: dict, patch: dict) -> dict:
    """Applique une mise à jour partielle et renvoie les réglages validés."""
    return validate_settings(deep_merge(current, patch))


def timeframe_minutes(tf: str) -> int:
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    return int(tf[:-1]) * units[tf[-1]]


def timeframe_ms(tf: str) -> int:
    return timeframe_minutes(tf) * 60_000
