"""IA principale : elle n'intervient que sur alerte d'un veilleur, juge le plan et s'abstient par défaut.

- Apprentissage : uniquement sur des alertes passées dont l'issue était connue avant le début de la période jugée
  (embargo d'un jour). Modèle « mixte » du projet : arbres peu profonds et modèle linéaire, moyennés.
- Calibration : les probabilités sont recalées sur la dernière tranche chronologique de l'apprentissage, jamais
  sur la période jugée.
- Décision : espérance nette en R = p × gain moyen − (1 − p) × perte moyenne de la configuration, frais compris.
  Le trade n'est pris que si elle dépasse la marge fixée à l'avance. Sinon : abstention, avec la raison.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from .features import FEATURES
from .watchers import SETUPS

MIN_TRAIN = 300


def matrix(cands: pd.DataFrame, extra: tuple = ()) -> pd.DataFrame:
    X = cands.reindex(columns=FEATURES + list(extra) + ["cost_r", "risk_pct"]).astype(float)
    X = X.replace([np.inf, -np.inf], np.nan)  # une valeur infinie est traitée comme absente
    for s in SETUPS:
        X[f"is_{s}"] = (cands["setup"] == s).astype(float).to_numpy()
    return X


class PrincipalAI:
    def __init__(self, margin_r: float = 0.10, seed: int = 0, extra: tuple = ()):
        self.margin_r, self.seed, self.extra = margin_r, seed, tuple(extra)
        self.ready = False

    def fit(self, train: pd.DataFrame) -> "PrincipalAI":
        train = train.sort_values("decided_at")
        self.n_train = len(train)
        if len(train) < MIN_TRAIN:
            self.reason = f"apprentissage impossible : {len(train)} alertes dénouées (minimum {MIN_TRAIN})"
            return self
        X, y = matrix(train, self.extra), (train["r"].to_numpy() > 0).astype(int)
        cut = int(len(train) * 0.8)
        self.empty = [c for c in X.columns if X[c].iloc[:cut].isna().all()]  # colonnes sans aucune valeur
        self.median = X.iloc[:cut].median().fillna(0.0)  # colonne entièrement absente : valeur neutre
        Xf = X.fillna(self.median)
        self.mu, self.sd = Xf.iloc[:cut].mean(), Xf.iloc[:cut].std().replace(0, 1)
        self.trees = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05, l2_regularization=1.0,
                                                    min_samples_leaf=40, random_state=self.seed).fit(self._trees_input(X.iloc[:cut]), y[:cut])
        self.linear = LogisticRegression(C=0.5, max_iter=2000).fit(((Xf - self.mu) / self.sd).iloc[:cut], y[:cut])
        self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(self._raw(X.iloc[cut:]), y[cut:])
        r = train["r"]
        self.payoff = {}
        for s in SETUPS:
            rs = r[train["setup"] == s]
            win, loss = rs[rs > 0], rs[rs <= 0]  # le point mort (0 R) n'est pas un gain
            self.payoff[s] = (float(win.mean()) if len(win) else 0.0, float(-loss.mean()) if len(loss) else 1.0)
        self.base_rate = float(y.mean())
        self.ready = True
        return self

    def _trees_input(self, X: pd.DataFrame) -> pd.DataFrame:
        """Les arbres gèrent les valeurs absentes, sauf une colonne vide : elle reçoit une valeur neutre."""
        return X.assign(**{c: 0.0 for c in self.empty}) if self.empty else X

    def _raw(self, X: pd.DataFrame) -> np.ndarray:
        Xf = X.fillna(self.median)
        return 0.5 * self.trees.predict_proba(self._trees_input(X))[:, 1] + 0.5 * self.linear.predict_proba((Xf - self.mu) / self.sd)[:, 1]

    def judge(self, cands: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=cands.index)
        if not self.ready or cands.empty:
            out["p"], out["expected_r"], out["take"] = np.nan, np.nan, False
            out["reasoning"] = getattr(self, "reason", "aucune alerte")
            return out
        p = self.iso.predict(self._raw(matrix(cands, self.extra)))
        win = cands["setup"].map(lambda s: self.payoff[s][0]).to_numpy()
        loss = cands["setup"].map(lambda s: self.payoff[s][1]).to_numpy()
        er = p * win - (1 - p) * loss
        out["p"], out["expected_r"], out["take"] = p, er, er >= self.margin_r
        out["reasoning"] = [
            f"{th} IA principale : {pp:.0%} de chances de gain (moyenne apprise {self.base_rate:.0%}), espérance "
            f"{e:+.2f} R après frais : {'trade pris' if e >= self.margin_r else 'abstention, avantage insuffisant'}."
            for th, pp, e in zip(cands["thesis"], p, er)]
        return out
