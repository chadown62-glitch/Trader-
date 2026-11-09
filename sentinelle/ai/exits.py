"""Règles de sortie d'une position, partagées entre l'apprentissage et le trading.

Le même code sert à étiqueter l'historique (« qu'aurait donné ce trade ? ») et
à gérer les positions en direct : ce que l'IA apprend correspond exactement à
ce qu'elle fera.

Ordre de traitement d'une bougie (prudent) :
1. stop touché ? (en premier, hypothèse pessimiste) ;
2. objectif touché ? ;
3. fin de bougie : passage au point mort éventuel (appliqué à partir de la
   bougie suivante), puis sortie si la durée maximale est atteinte.

En direct, les étapes 1 et 2 sont réalisées en temps réel par le broker
(ordres stop et objectif) ; l'agent n'applique que l'étape 3 à chaque clôture.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExitState:
    entry: float
    stop: float
    target: float | None
    initial_stop: float
    horizon_bars: int
    breakeven_r: float | None = None
    fee_rate: float = 0.001
    slip: float = 0.0005
    bars: int = 0
    breakeven_done: bool = False
    trail_atr: float | None = None  # stop suiveur : plus haut de clôture − k × ATR d'entrée
    atr_entry: float = 0.0
    peak: float = 0.0

    @property
    def risk_unit(self) -> float:
        return self.entry - self.initial_stop


def check_hits(state: ExitState, o: float, h: float, l: float) -> tuple[float | None, str | None]:
    """Étapes 1-2 : stop puis objectif. Renvoie (prix de sortie, motif) ou (None, None)."""
    if l <= state.stop:
        if state.trail_atr and state.stop > state.initial_stop:
            reason = "trailing"
        else:
            reason = "breakeven" if state.breakeven_done else "stop"
        return min(state.stop, o) * (1 - state.slip), reason
    if state.target is not None and h >= state.target:
        return (o if o > state.target else state.target), "target"
    return None, None


def end_of_bar(state: ExitState, h: float, c: float) -> tuple[bool, bool]:
    """Étape 3. Renvoie (stop_modifié, sortie_par_durée)."""
    state.bars += 1
    moved = False
    if state.trail_atr and state.atr_entry > 0:
        state.peak = max(state.peak or state.entry, c)
        trail = state.peak - state.trail_atr * state.atr_entry
        if state.stop < trail < c:
            state.stop = trail
            moved = True
    if state.breakeven_r and not state.breakeven_done and state.risk_unit > 0:
        if h >= state.entry + state.breakeven_r * state.risk_unit:
            be = state.entry * (1 + 2 * state.fee_rate + state.slip)
            if state.stop < be < c:
                state.stop = be
                state.breakeven_done = True
                moved = True
    return moved, state.bars >= state.horizon_bars


def step(state: ExitState, o: float, h: float, l: float, c: float) -> tuple[float | None, str | None]:
    """Bougie complète (historique). Renvoie (prix de sortie | None, motif | None)."""
    px, reason = check_hits(state, o, h, l)
    if px is not None:
        return px, reason
    _, timed_out = end_of_bar(state, h, c)
    if timed_out:
        return c * (1 - state.slip), "time"
    return None, None


def r_multiple(entry_fill: float, exit_fill: float, initial_stop: float, fee_rate: float) -> float:
    """Résultat net (frais compris) exprimé en multiple du risque initial."""
    risk = entry_fill - initial_stop
    if risk <= 0:
        return float("nan")
    pnl = exit_fill * (1 - fee_rate) - entry_fill * (1 + fee_rate)
    return pnl / risk


EXIT_LABELS = {
    "stop": "Stop-loss",
    "breakeven": "Stop au point mort",
    "trailing": "Stop suiveur",
    "target": "Objectif",
    "time": "Durée maximale",
}
