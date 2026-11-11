"""Éléments partagés par les services (erreurs, constantes, structures)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import UserError  # noqa: F401  (ré-exporté : point d'import historique)
from ..execution.positions import PositionManager
from ..risk.manager import RiskManager
from ..safety import RealTradingForbidden  # noqa: F401  (ré-exporté)

EMERGENCY_CONFIRM = "URGENCE"

# Seule l'IA d'entraînement (argent fictif) existe. L'ancienne « IA — Portefeuille réel »
# a été retirée : toute tentative de la démarrer est refusée (voir sentinelle/safety.py).
AGENTS = {
    "ia_entrainement": {"mode": "training", "role": "principal", "label": "IA — Portefeuille principal fictif"},
}
RETIRED_AGENTS = {"ia_reel": "IA — Portefeuille réel"}


@dataclass
class AccountRuntime:
    account: dict
    broker: Any
    positions: PositionManager
    risk: RiskManager
