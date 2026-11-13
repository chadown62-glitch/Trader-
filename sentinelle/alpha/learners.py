"""Concurrents du « Learning Velocity Challenge ».

Tous respectent le même protocole : ``fit(train, calibration)`` sur des issues déjà connues,
``select(selection)`` pour leur unique réglage (seuil), puis ``policy(evaluation)`` qui rend,
pour chaque situation, l'entrée retenue et la variante de sortie. Aucun ne voit la vérité des
marchés synthétiques, ni les issues de la fenêtre d'évaluation (mode « figé »).

- ``TakeAll`` : toutes les entrées des enseignants non bloquées par l'abstention (référence) ;
- ``SingleTeacher`` : un seul enseignant ; ``Cash`` : ne rien faire ;
- ``ProjectMixte`` : la recette « mixte » du projet (arbres simples + linéaire), calibrée (Platt) ;
- ``ErrorReplay`` (nouveau) : même modèle, mais réentraîné en insistant sur ses erreurs passées
  mesurées HORS échantillon (plis chronologiques), y compris sur les entrées refusées dont
  l'issue est connue (« apprendre des occasions refusées ») ;
- ``RegimeRouter`` (nouveau) : choisit l'enseignant à suivre selon le climat connu à l'instant
  (tendance × volatilité), par moyenne d'issues mûres rétrécie vers zéro ; quasiment gratuit en calcul ;
- ``ExitLearner`` (nouveau) : apprend la meilleure variante de SORTIE par enseignant et par climat,
  à partir des issues contrefactuelles mûres (même entrée, autres sorties).
"""
from __future__ import annotations

import numpy as np

from ..ai.model import MetaModel
from .dataset import Situations
from .teachers import EXIT_VARIANTS, TEACHER_NAMES

EV_GRID = (-0.05, 0.0, 0.05, 0.1, 0.15, 0.2, 0.3)  # seuils d'espérance nette (en R)
MIN_TAKEN = 15
TREND_COL, ATR_RANK_COL = 10, 12


def labels(s: Situations) -> tuple[np.ndarray, np.ndarray]:
    r = s.net_r["teacher"]
    ok = np.isfinite(r)
    return ok, (r > 0).astype(int)


def climate(s: Situations) -> np.ndarray:
    """Climat connu à la décision : signe de tendance × tercile de volatilité (6 cases)."""
    trend = (s.X[:, TREND_COL] > 0).astype(int)
    rank = np.nan_to_num(s.X[:, ATR_RANK_COL], nan=0.5)
    vol = np.digitize(rank, (1 / 3, 2 / 3))
    return trend * 3 + vol


def teacher_rates(*parts: Situations) -> dict[str, float]:
    """Taux de gain de chaque enseignant sur des issues déjà connues (référence prédictive honnête)."""
    out = {}
    for t in TEACHER_NAMES:
        ys = [labels(s)[1][labels(s)[0] & (s.teacher == t)] for s in parts]
        y = np.concatenate(ys) if ys else np.zeros(0)
        out[t] = float((y.sum() + 1) / (len(y) + 2))  # lissage de Laplace
    return out


def teacher_payoffs(*parts: Situations) -> dict[str, tuple[float, float]]:
    """Gain moyen (R) des gagnants et perte moyenne des perdants, par enseignant."""
    out = {}
    for t in TEACHER_NAMES:
        r = np.concatenate([s.net_r["teacher"][(s.teacher == t) & np.isfinite(s.net_r["teacher"])] for s in parts])
        w, lo = r[r > 0], r[r <= 0]
        out[t] = (float(w.mean()) if len(w) else 1.0, float(lo.mean()) if len(lo) else -1.0)
    return out


class Learner:
    name = "learner"
    uses_model = False
    variant = "teacher"

    def fit(self, train: Situations, cal: Situations) -> None:
        self.base_rate = float(labels(train)[1][labels(train)[0]].mean()) if labels(train)[0].any() else 0.5
        self.rates = teacher_rates(train, cal)

    def reference_proba(self, s: Situations) -> np.ndarray:
        """Prévision de référence : le taux de gain connu de l'enseignant (aucun modèle)."""
        return np.array([self.rates.get(t, self.base_rate) for t in s.teacher])

    def proba(self, s: Situations) -> np.ndarray:
        return np.full(len(s), getattr(self, "base_rate", 0.5))

    def select(self, sel: Situations) -> None:
        pass

    def policy(self, s: Situations) -> tuple[np.ndarray, np.ndarray]:
        return ~s.vetoed, np.array([self.variant] * len(s), dtype=object)

    def describe(self) -> dict:
        return {"name": self.name}


class Cash(Learner):
    name = "cash"

    def policy(self, s):
        return np.zeros(len(s), bool), np.array(["teacher"] * len(s), dtype=object)


class TakeAll(Learner):
    name = "take_all"


class SingleTeacher(Learner):
    def __init__(self, teacher: str):
        self.teacher = teacher
        self.name = f"teacher:{teacher}"

    def policy(self, s):
        return (~s.vetoed) & (s.teacher == self.teacher), np.array(["teacher"] * len(s), dtype=object)


def _platt(p_raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Calibration de Platt sur la fenêtre de calibration (jamais sur l'apprentissage)."""
    from sklearn.linear_model import LogisticRegression

    if len(y) < 20 or len(np.unique(y)) < 2:
        return 1.0, 0.0
    z = np.log(np.clip(p_raw, 1e-4, 1 - 1e-4) / (1 - np.clip(p_raw, 1e-4, 1 - 1e-4))).reshape(-1, 1)
    m = LogisticRegression(C=1.0).fit(z, y)
    return float(m.coef_[0, 0]), float(m.intercept_[0])


class ProjectMixte(Learner):
    """Recette « mixte » de ``sentinelle.ai.model`` sur les caractéristiques du laboratoire."""
    name = "project_mixte"
    uses_model = True
    include_refused = False

    def _weights(self, train: Situations, X: np.ndarray, y: np.ndarray) -> np.ndarray | None:
        return None

    def _rows(self, train: Situations) -> np.ndarray:
        ok, _ = labels(train)
        return ok if self.include_refused else ok & ~train.vetoed

    def fit(self, train, cal):
        super().fit(train, cal)
        self.payoffs = teacher_payoffs(train)
        rows = self._rows(train)
        X, y = train.design()[rows], labels(train)[1][rows]
        self.members = None
        if len(y) < 20 or len(np.unique(y)) < 2:
            self.calib = (1.0, 0.0)
            return
        w = self._weights(train.take(rows), X, y)
        self.members = MetaModel._fit_members(["prudent", "lin"], X, y, w)
        ok, yc = labels(cal)
        self.calib = _platt(self._raw(cal.design()[ok]), yc[ok]) if ok.any() else (1.0, 0.0)

    def _raw(self, X: np.ndarray) -> np.ndarray:
        if not self.members or not len(X):
            return np.full(len(X), getattr(self, "base_rate", 0.5))
        return np.mean([m.predict_proba(X)[:, 1] for m in self.members], axis=0)

    def proba(self, s):
        if not self.members:
            return np.full(len(s), getattr(self, "base_rate", 0.5))
        p = np.clip(self._raw(s.design()), 1e-4, 1 - 1e-4)
        a, b = self.calib
        return 1 / (1 + np.exp(-(a * np.log(p / (1 - p)) + b)))

    def expected_r(self, s: Situations) -> np.ndarray:
        """Espérance nette en R = p × gain moyen + (1 − p) × perte moyenne de l'enseignant (comme le projet)."""
        p = self.proba(s)
        w = np.array([self.payoffs[t][0] for t in s.teacher])
        lo = np.array([self.payoffs[t][1] for t in s.teacher])
        return p * w + (1 - p) * lo

    def select(self, sel):
        """Seul réglage : le seuil d'espérance, choisi sur la fenêtre de sélection (jamais l'évaluation)."""
        ok, _ = labels(sel)
        ev = self.expected_r(sel)
        r = sel.net_r["teacher"]
        best, self.threshold = 0.0, np.inf  # par défaut : ne rien prendre
        for t in EV_GRID:
            m = ok & ~sel.vetoed & (ev >= t)
            if m.sum() >= MIN_TAKEN and r[m].mean() > best:
                best, self.threshold = float(r[m].mean()), t
        self.selection_score = best

    def policy(self, s):
        return (~s.vetoed) & (self.expected_r(s) >= getattr(self, "threshold", np.inf)), np.array(["teacher"] * len(s), dtype=object)

    def describe(self):
        t = getattr(self, "threshold", None)
        return {"name": self.name, "ev_threshold_r": None if t is None or not np.isfinite(t) else t,
                "takes_nothing": t is None or not np.isfinite(t), "calibration": getattr(self, "calib", None)}


class ErrorReplay(ProjectMixte):
    """Rejoue les erreurs : poids = 1 + λ × perte logarithmique hors échantillon (plis chronologiques),
    majorée pour les erreurs coûteuses ; inclut les entrées refusées dont l'issue est connue."""
    name = "error_replay"
    include_refused = True
    folds = 3
    lam = 1.0

    def _weights(self, train, X, y):
        n = len(y)
        if n < 80:
            return None
        order = np.argsort(train.decided_at, kind="mergesort")
        err = np.full(n, np.nan)
        chunks = np.array_split(order, self.folds)
        for k in range(1, self.folds):
            past = np.concatenate(chunks[:k])
            if len(np.unique(y[past])) < 2:
                continue
            members = MetaModel._fit_members(["prudent", "lin"], X[past], y[past], None)
            p = np.clip(np.mean([m.predict_proba(X[chunks[k]])[:, 1] for m in members], axis=0), 1e-4, 1 - 1e-4)
            yy = y[chunks[k]]
            err[chunks[k]] = -(yy * np.log(p) + (1 - yy) * np.log(1 - p))
        fill = np.nanmedian(err) if np.isfinite(err).any() else 1.0
        err = np.where(np.isfinite(err), err, fill)
        w = 1 + self.lam * err / max(np.mean(err), 1e-9)
        severity = np.minimum(np.abs(np.nan_to_num(train.net_r["teacher"])), 3.0)
        w *= 1 + 0.25 * severity  # une grosse perte (ou un gros gain manqué) compte davantage
        return np.clip(w, 0.25, 4.0)


class RegimeRouter(Learner):
    """Moyenne d'issues mûres par (enseignant, climat), rétrécie vers 0 : n'entre que si elle reste positive."""
    name = "regime_router"
    prior = 20.0
    margins = (0.0, 0.05, 0.1, 0.2)

    def fit(self, train, cal):
        super().fit(train, cal)
        self.table: dict[tuple[str, int], tuple[float, int]] = {}
        self.wins: dict[tuple[str, int], tuple[float, int]] = {}
        self.update(train)
        self.update(cal)
        self.margin = 0.0

    def update(self, s: Situations) -> None:
        """Ajoute des issues MÛRES (à n'appeler qu'avec des situations dont l'issue est connue)."""
        ok, y = labels(s)
        c = climate(s)
        for k in np.flatnonzero(ok):
            key = (s.teacher[k], int(c[k]))
            tot, n = self.table.get(key, (0.0, 0))
            self.table[key] = (tot + float(s.net_r["teacher"][k]), n + 1)
            w, m = self.wins.get(key, (0.0, 0))
            self.wins[key] = (w + y[k], m + 1)

    def score(self, s: Situations) -> np.ndarray:
        c = climate(s)
        return np.array([self.table.get((s.teacher[k], int(c[k])), (0.0, 0))[0] / (self.table.get((s.teacher[k], int(c[k])), (0.0, 0))[1] + self.prior)
                         for k in range(len(s))])

    def proba(self, s):
        c = climate(s)
        out = []
        for k in range(len(s)):
            base = self.rates.get(s.teacher[k], self.base_rate)
            w, m = self.wins.get((s.teacher[k], int(c[k])), (0.0, 0))
            out.append((w + self.prior * base) / (m + self.prior))
        return np.array(out)

    def select(self, sel):
        ok, _ = labels(sel)
        sc = self.score(sel)
        r = sel.net_r["teacher"]
        best, self.margin = -np.inf, None
        for mg in self.margins:
            m = ok & ~sel.vetoed & (sc > mg)
            if m.sum() >= MIN_TAKEN and r[m].mean() > best:
                best, self.margin = float(r[m].mean()), mg
        if self.margin is None or best <= 0:
            self.margin = np.inf
        self.selection_score = best

    def policy(self, s):
        return (~s.vetoed) & (self.score(s) > self.margin), np.array(["teacher"] * len(s), dtype=object)

    def describe(self):
        return {"name": self.name, "margin_r": self.margin if np.isfinite(self.margin) else None,
                "takes_nothing": not np.isfinite(self.margin), "cells": len(self.table)}


class ExitLearner(Learner):
    """Même entrées que TakeAll ; la sortie est choisie par (enseignant, climat) d'après les issues
    contrefactuelles mûres de chaque variante, rétrécies vers la sortie de l'enseignant."""
    name = "exit_learner"
    prior = 30.0

    def fit(self, train, cal):
        super().fit(train, cal)
        both = [train, cal]
        self.choice: dict[tuple[str, int], str] = {}
        stats: dict[tuple[str, int, str], list[float]] = {}
        for s in both:
            c = climate(s)
            for v in EXIT_VARIANTS:
                r = s.net_r[v]
                for k in np.flatnonzero(np.isfinite(r)):
                    stats.setdefault((s.teacher[k], int(c[k]), v), []).append(float(r[k]))
        for t in TEACHER_NAMES:
            for c in range(6):
                ref = stats.get((t, c, "teacher"), [])
                best, pick = (np.mean(ref) if ref else 0.0), "teacher"
                for v in EXIT_VARIANTS[1:]:
                    xs = stats.get((t, c, v), [])
                    if len(xs) < 10:
                        continue
                    # gain rétréci par rapport à la sortie de l'enseignant
                    gain = (np.sum(xs) - (np.mean(ref) if ref else 0.0) * len(xs)) / (len(xs) + self.prior)
                    if gain > 0 and np.mean(xs) > best:
                        best, pick = float(np.mean(xs)), v
                self.choice[(t, c)] = pick

    def policy(self, s):
        c = climate(s)
        return ~s.vetoed, np.array([self.choice.get((s.teacher[k], int(c[k])), "teacher") for k in range(len(s))], dtype=object)

    def describe(self):
        changed = {f"{k[0]}|{k[1]}": v for k, v in sorted(self.choice.items()) if v != "teacher"}
        return {"name": self.name, "changed_exits": changed}


class RouterWithExits(RegimeRouter):
    """Entrées du routeur + sorties de l'ExitLearner (combinaison préenregistrée, pas cherchée après coup)."""
    name = "router_with_exits"

    def fit(self, train, cal):
        super().fit(train, cal)
        self.exits = ExitLearner()
        self.exits.fit(train, cal)

    def policy(self, s):
        enter, _ = super().policy(s)
        _, variant = self.exits.policy(s)
        return enter, variant


def default_learners() -> list[Learner]:
    return [Cash(), TakeAll(), *[SingleTeacher(t) for t in TEACHER_NAMES], ProjectMixte(), ErrorReplay(),
            RegimeRouter(), ExitLearner(), RouterWithExits()]
