"""Cinq enseignants explicites, non personnels, calculés uniquement sur des données « as_of ».

Chaque enseignant transforme les bougies en une séquence interprétable de décisions :
entrée (avec plan : stop, sortie, durée maximale, taille simulée), absence d'entrée (avec la
raison), et condition d'invalidation. Aucune étiquette de « trade gagnant » n'est calculée ici :
l'issue n'est connue qu'après exécution simulée (``execution.simulate_trade``), à sa date.

Les règles viennent du catalogue (``catalogue_v1.json``) ; leurs paramètres ont été fixés AVANT
tout test et ne sont pas optimisés.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bars import Bars, Indicators
from .execution import Plan

ABSTENTION_SPREAD_BPS = 25.0
ABSTENTION_ATR_RANK = 0.97
ABSTENTION_VOLUME_RATIO = 0.25


@dataclass(frozen=True)
class TeacherDecision:
    teacher: str
    symbol: str
    index: int
    decided_at: int  # ms, clôture de la bougie
    action: str  # "enter" | "skip"
    plan: Plan | None
    reasons: tuple[str, ...]
    invalidation: str
    entry_zone: tuple[float, float]
    simulated_size_eur: float | None
    sources: tuple[str, ...]


def _transition(cond: np.ndarray) -> np.ndarray:
    prev = np.concatenate([[False], cond[:-1]])
    return cond & ~prev


def _ok(*arrays: np.ndarray) -> np.ndarray:
    m = np.ones(len(arrays[0]), dtype=bool)
    for a in arrays:
        m &= np.isfinite(a)
    return m


def abstention_mask(b: Bars, ind: Indicators) -> tuple[np.ndarray, list[tuple[str, ...]]]:
    """Enseignant « abstention / liquidité » : les bougies où il ne faut PAS entrer, et pourquoi."""
    n = len(b)
    veto = np.zeros(n, dtype=bool)
    reasons: list[tuple[str, ...]] = [()] * n
    spread = ind.spread_bps
    for i in range(n):
        r = []
        if not np.isfinite(spread[i]) or spread[i] > ABSTENTION_SPREAD_BPS:
            r.append(f"écart de {spread[i]:.0f} bps : marché trop mince")
        if np.isfinite(ind.atr_rank[i]) and ind.atr_rank[i] >= ABSTENTION_ATR_RANK:
            r.append("volatilité extrême (3 % les plus hautes des 120 dernières bougies)")
        if np.isfinite(ind.volume_ratio[i]) and ind.volume_ratio[i] < ABSTENTION_VOLUME_RATIO:
            r.append("volume inhabituellement faible")
        if r:
            veto[i] = True
            reasons[i] = tuple(r)
    return veto, reasons


def exit_signals(b: Bars, ind: Indicators) -> dict[str, np.ndarray]:
    """Conditions de sortie évaluées à la clôture (exécution à l'ouverture suivante)."""
    c = b.close
    with np.errstate(invalid="ignore"):
        return {
            "close_below_ema50": c < ind.ema50,
            "close_below_low10": c < ind.low10,
            "close_above_ema20": c > ind.ema20,
            "close_above_sma5": c > ind.sma5,
        }


@dataclass(frozen=True)
class Teacher:
    name: str
    label: str
    sources: tuple[str, ...]
    stop_atr: float
    max_bars: int
    exit_rule: str | None
    trail_atr: float | None = None
    target_atr: float | None = None

    def setup(self, b: Bars, ind: Indicators) -> np.ndarray:
        c = b.close
        with np.errstate(invalid="ignore"):
            if self.name == "trend":
                cond = (ind.ema50 > ind.ema200) & (c > ind.ema50) & (ind.ret24 > 0)
                cond &= _ok(ind.ema200, ind.ret24, ind.atr14)
            elif self.name == "breakout":
                cond = (c > ind.high20) & _ok(ind.high20, ind.atr20)
            elif self.name == "pullback":
                cond = (ind.ema50 > ind.ema200) & (c < ind.ema20) & (ind.rsi3 < 30) & _ok(ind.ema200, ind.rsi3, ind.atr14)
            elif self.name == "mean_reversion":
                cond = (ind.rsi2 < 10) & (c > ind.sma200) & _ok(ind.sma200, ind.rsi2, ind.atr14)
            else:
                raise ValueError(self.name)
        return _transition(cond)

    def reasons(self, b: Bars, ind: Indicators, i: int) -> tuple[str, ...]:
        c = float(b.close[i])
        if self.name == "trend":
            return (f"moyenne 50 au-dessus de la moyenne 200 ({ind.ema50[i]:.4g} > {ind.ema200[i]:.4g})",
                    f"clôture {c:.4g} au-dessus de la moyenne 50", f"rendement sur 24 bougies {ind.ret24[i]:+.2%}")
        if self.name == "breakout":
            return (f"clôture {c:.4g} au-dessus du plus haut des 20 bougies précédentes ({ind.high20[i]:.4g})",)
        if self.name == "pullback":
            return ("tendance longue haussière (moyenne 50 > moyenne 200)", f"repli sous la moyenne 20, RSI(3) = {ind.rsi3[i]:.0f}")
        return (f"RSI(2) = {ind.rsi2[i]:.1f} < 10", f"clôture {c:.4g} au-dessus de la moyenne 200 ({ind.sma200[i]:.4g})")

    def invalidation(self) -> str:
        return {"trend": "clôture sous la moyenne 50, ou stop suiveur à 3 ATR du plus haut",
                "breakout": "clôture sous le plus bas des 10 bougies précédentes, ou stop à 2 ATR",
                "pullback": "stop à 1,5 ATR ; objectif atteint quand la clôture repasse au-dessus de la moyenne 20",
                "mean_reversion": "stop à 3 ATR ; sortie dès la clôture au-dessus de la moyenne 5"}[self.name]

    def plan(self, b: Bars, ind: Indicators, i: int, variant: str = "teacher") -> Plan | None:
        a = ind.atr20[i] if self.name == "breakout" else ind.atr14[i]
        c = float(b.close[i])
        if not np.isfinite(a) or a <= 0:
            return None
        stop_atr, trail, target, rule, max_bars = self.stop_atr, self.trail_atr, self.target_atr, self.exit_rule, self.max_bars
        if variant == "tight":  # sortie rapide : stop suiveur serré
            trail, rule = 1.5, None
        elif variant == "time":  # sortie à la seule durée (référence)
            trail, target, rule, max_bars = None, None, None, max(6, self.max_bars // 3)
        elif variant == "target2r":  # objectif fixe à 2 R
            target, rule = 2.0 * stop_atr, None
        elif variant != "teacher":
            raise ValueError(variant)
        return Plan(self.name, c - stop_atr * a, max_bars, None if target is None else c + target * a, trail, rule, variant)

    def decisions(self, b: Bars, ind: Indicators, veto: np.ndarray, veto_reasons: list, *,
                  equity_eur: float = 30.0, risk_frac: float = 0.005) -> list[TeacherDecision]:
        out = []
        for i in np.flatnonzero(self.setup(b, ind)):
            plan = self.plan(b, ind, int(i))
            if plan is None:
                continue
            c = float(b.close[i])
            risk_unit = (c - plan.stop_price) / c
            size = risk_frac * equity_eur / risk_unit if risk_unit > 0 else None
            zone = (c * 0.999, c * 1.005)
            if veto[i]:
                out.append(TeacherDecision(self.name, b.symbol, int(i), b.close_time(int(i)), "skip", plan,
                                           self.reasons(b, ind, int(i)) + tuple("ABSTENTION : " + r for r in veto_reasons[i]),
                                           self.invalidation(), zone, size, self.sources))
            else:
                out.append(TeacherDecision(self.name, b.symbol, int(i), b.close_time(int(i)), "enter", plan,
                                           self.reasons(b, ind, int(i)), self.invalidation(), zone, size, self.sources))
        return out


TEACHERS = (
    Teacher("trend", "Suivi de tendance", ("tsmom-moskowitz-2012", "wilder-1978", "fixed-fractional-risk"),
            stop_atr=3.0, max_bars=180, exit_rule="close_below_ema50", trail_atr=3.0),
    Teacher("breakout", "Cassure de canal", ("donchian-channel", "turtle-rules-faith-2007", "wilder-1978", "fixed-fractional-risk"),
            stop_atr=2.0, max_bars=120, exit_rule="close_below_low10"),
    Teacher("pullback", "Repli dans la tendance", ("pullback-in-trend", "wilder-1978", "fixed-fractional-risk"),
            stop_atr=1.5, max_bars=30, exit_rule="close_above_ema20"),
    Teacher("mean_reversion", "Retour à la moyenne", ("connors-rsi2-2008", "wilder-1978", "fixed-fractional-risk"),
            stop_atr=3.0, max_bars=15, exit_rule="close_above_sma5"),
)
ABSTENTION = ("abstention", "Abstention et liquidité", ("amihud-2002-illiquidity", "wilder-1978"))
TEACHER_NAMES = tuple(t.name for t in TEACHERS)
EXIT_VARIANTS = ("teacher", "tight", "time", "target2r")


def teacher(name: str) -> Teacher:
    for t in TEACHERS:
        if t.name == name:
            return t
    raise KeyError(name)
