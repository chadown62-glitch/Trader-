"""Le méta-modèle de l'IA : estime la probabilité qu'un candidat soit gagnant.

- modèle : gradient boosting d'arbres, volontairement peu profond et régularisé
  (moins de risque d'apprendre « par cœur » le passé) ;
- calibration (Platt) sur des prédictions hors échantillon : une probabilité
  annoncée de 60 % doit correspondre à environ 60 % de gains réels ;
- espérance en R = p × gain moyen + (1 − p) × perte moyenne, par stratégie.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from .labeling import MODEL_COLUMNS, model_matrix


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


@dataclass
class MetaModel:
    timeframe: str
    params: dict = field(default_factory=dict)
    clf: HistGradientBoostingClassifier | None = None
    calib: tuple[float, float] = (1.0, 0.0)
    payoffs: dict[str, tuple[float, float]] = field(default_factory=dict)
    threshold: float = 0.05
    version: str = ""
    feature_names: list[str] = field(default_factory=lambda: list(MODEL_COLUMNS))
    trained_until: int | None = None
    metrics: dict = field(default_factory=dict)

    @staticmethod
    def default_params(n_samples: int) -> dict:
        return {
            "learning_rate": 0.05,
            "max_iter": 200,
            "max_leaf_nodes": 15,
            "min_samples_leaf": max(20, n_samples // 100),
            "l2_regularization": 1.0,
            "early_stopping": False,
            "random_state": 0,
        }

    def fit(self, cand: pd.DataFrame) -> "MetaModel":
        cand = cand[cand["outcome"].notna()]
        X = model_matrix(cand)
        y = (cand["outcome_r"].to_numpy() > 0).astype(int)
        params = self.params or self.default_params(len(cand))
        self.clf = HistGradientBoostingClassifier(**params)
        self.clf.fit(X, y)
        self.payoffs = payoff_table(cand)
        if len(cand):
            self.trained_until = int(cand["exit_ts"].max())
        return self

    def raw_proba(self, X: pd.DataFrame) -> np.ndarray:
        assert self.clf is not None, "modèle non entraîné"
        return self.clf.predict_proba(X)[:, 1]

    def fit_calibration(self, p_raw: np.ndarray, y: np.ndarray) -> None:
        if len(np.unique(y)) < 2 or len(y) < 30:
            self.calib = (1.0, 0.0)
            return
        lr = LogisticRegression(C=1.0)
        lr.fit(_logit(p_raw).reshape(-1, 1), y)
        self.calib = (float(lr.coef_[0][0]), float(lr.intercept_[0]))

    def calibrate(self, p_raw: np.ndarray) -> np.ndarray:
        a, b = self.calib
        return 1.0 / (1.0 + np.exp(-(a * _logit(p_raw) + b)))

    def expected_r(self, p: np.ndarray, strategies) -> np.ndarray:
        out = np.empty(len(p))
        for k, (pk, s) in enumerate(zip(p, strategies)):
            win, loss = self.payoffs.get(s, self.payoffs.get("_all", (1.0, -1.0)))
            out[k] = pk * win + (1 - pk) * loss
        return out

    def predict(self, cand: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """(probabilité de gain calibrée, espérance en R) pour chaque candidat."""
        if len(cand) == 0:
            return np.array([]), np.array([])
        p = self.calibrate(self.raw_proba(model_matrix(cand)))
        return p, self.expected_r(p, cand["strategy"].to_numpy())

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> "MetaModel":
        obj = joblib.load(path)
        if not isinstance(obj, MetaModel):
            raise ValueError("Fichier de modèle invalide")
        return obj


def payoff_table(cand: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """Gain moyen (R>0) et perte moyenne (R≤0) observés, par stratégie."""
    table = {}
    res = cand[cand["outcome"].notna()]

    def stats(r: np.ndarray) -> tuple[float, float]:
        wins, losses = r[r > 0], r[r <= 0]
        w = float(wins.mean()) if len(wins) else 1.0
        lo = float(losses.mean()) if len(losses) else -1.0
        return (w if math.isfinite(w) else 1.0, lo if math.isfinite(lo) else -1.0)

    for s, grp in res.groupby("strategy"):
        if len(grp) >= 10:
            table[s] = stats(grp["outcome_r"].to_numpy())
    if len(res):
        table["_all"] = stats(res["outcome_r"].to_numpy())
    return table
