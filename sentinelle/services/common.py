"""Éléments partagés par les services (erreurs, constantes, structures)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..execution.positions import PositionManager
from ..risk.manager import RiskManager

LIVE_CONFIRM = "JE COMPRENDS LES RISQUES"
EMERGENCY_CONFIRM = "URGENCE"

AGENTS = {
    "ia_entrainement": {"mode": "training", "role": "ai_training", "label": "IA — Entraînement (argent fictif)"},
    "ia_reel": {"mode": "live", "role": "live", "label": "IA — Portefeuille réel"},
}


class UserError(Exception):
    """Erreur à afficher telle quelle à l'utilisateur."""


@dataclass
class AccountRuntime:
    account: dict
    broker: Any
    positions: PositionManager
    risk: RiskManager


class LivePrices:
    """Prix de l'exchange réel (ou testnet) : peuvent différer de la source de données."""

    def __init__(self, exchange, ttl: float = 4.0):
        self.ex = exchange
        self.ttl = ttl
        self.cache: dict[str, tuple[float, float]] = {}

    def __call__(self, symbol: str) -> float | None:
        hit = self.cache.get(symbol)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        try:
            t = self.ex.fetch_ticker(symbol)
            px = float(t.get("last") or t.get("close") or 0) or None
        except Exception:
            return hit[1] if hit else None
        if px:
            self.cache[symbol] = (time.time(), px)
        return px
