"""Le méta-modèle de l'IA : estime la probabilité qu'un candidat soit gagnant.

- modèles : gradient boosting d'arbres, peu profonds et régularisés (moins de
  risque d'apprendre « par cœur » le passé) ;
- complexité adaptée aux données (recette « auto ») : trois niveaux de modèle
  sont essayés sur la partie la plus récente de l'historique d'apprentissage,
  jamais vue par eux ; le plus fiable est retenu. Un signal faible demande un
  modèle simple, une règle riche et beaucoup de données permettent un modèle
  plus fin ;
- oubli progressif (facultatif) : les situations récentes pèsent plus que les
  anciennes, pour s'adapter quand le marché change ;
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
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import QuantileTransformer

from .labeling import MODEL_COLUMNS, model_matrix

# Trois niveaux de complexité, du plus simple au plus fin.
PRESETS = {
    "prudent": lambda n: {"learning_rate": 0.04, "max_iter": 80, "max_depth": 2, "min_samples_leaf": max(30, n // 40), "l2_regularization": 1.0},
    "moyen": lambda n: {"learning_rate": 0.05, "max_iter": 120, "max_depth": 3, "min_samples_leaf": max(30, n // 60), "l2_regularization": 1.0},
    "riche": lambda n: {"learning_rate": 0.05, "max_iter": 200, "max_leaf_nodes": 15, "min_samples_leaf": max(20, n // 100), "l2_regularization": 1.0},
}
# Recettes : quels membres composent le modèle (leurs avis sont moyennés). « auto » choisit parmi elles.
# « lin » est un modèle linéaire (régression logistique sur des caractéristiques ramenées à la même échelle) :
# il ne voit que les effets simples, ce qui le rend très résistant au hasard quand le signal est faible.
RECIPES = {
    "mixte": ["prudent", "lin"],
    "prudent": ["prudent"],
    "lineaire": ["lin"],
    "moyen": ["moyen"],
    "riche": ["riche"],
    "comite": ["prudent", "moyen", "riche"],
}
RECIPE_LABELS = {"mixte": "mixte (arbres simples + modèle linéaire)", "prudent": "simple", "lineaire": "linéaire", "moyen": "intermédiaire",
                 "riche": "fin", "comite": "comité de trois modèles", "forcé": "paramètres imposés"}
MEMBERS = ("prudent", "moyen", "riche", "lin")
# Mesures de novembre 2026 (docs/RESULTATS.md) :
# - banc d'essai (règles cachées) : « mixte » retrouve 86 % des règles, « prudent » 87 %, l'ancien modèle « riche » 75 %,
#   le modèle linéaire seul 62 % (il ne voit pas les règles en cloche) ;
# - données réelles (8 cryptos, validation chronologique) : score 0,561 pour « mixte », 0,538 pour « prudent », 0,534 pour « riche ».
# « mixte » est donc la recette de référence : aussi bonne sur les règles riches, nettement meilleure quand le signal est faible.
DEFAULT_RECIPE = "mixte"


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


@dataclass
class MetaModel:
    timeframe: str
    params: dict = field(default_factory=dict)  # paramètres imposés (un seul modèle) ; vide = recette
    clf: HistGradientBoostingClassifier | None = None
    calib: tuple[float, float] = (1.0, 0.0)
    payoffs: dict[str, tuple[float, float]] = field(default_factory=dict)
    threshold: float = 0.05
    version: str = ""
    feature_names: list[str] = field(default_factory=lambda: list(MODEL_COLUMNS))
    trained_until: int | None = None
    metrics: dict = field(default_factory=dict)
    recipe: str = DEFAULT_RECIPE
    half_life_days: float | None = None
    members: list = field(default_factory=list)
    chosen: str | None = None  # niveau de complexité réellement retenu
    selection: dict = field(default_factory=dict)  # scores de validation interne de chaque recette
    meta: dict = field(default_factory=dict)  # fiche d'identité : période vue, recette, seuil, stratégies…
    feature_bins: dict = field(default_factory=dict)  # déciles des caractéristiques à l'apprentissage (détection de dérive)
    outcome_mix: dict = field(default_factory=dict)  # issues passées par stratégie (scénarios d'un trade)

    def __setstate__(self, state: dict) -> None:
        # modèles enregistrés par une version précédente : un seul modèle « riche »
        self.__dict__.update({"recipe": "riche", "half_life_days": None, "members": [], "chosen": "riche", "selection": {},
                              "meta": {}, "feature_bins": {}, "outcome_mix": {}})
        self.__dict__.update(state)

    @staticmethod
    def default_params(n_samples: int) -> dict:
        return {**PRESETS["riche"](n_samples), "early_stopping": False, "random_state": 0}

    # -- apprentissage ---------------------------------------------------------------
    def _weights(self, cand: pd.DataFrame) -> np.ndarray | None:
        if not self.half_life_days:
            return None
        ts = cand["decision_ts"].to_numpy(dtype=float)
        age_days = (ts.max() - ts) / 86_400_000
        return 0.5 ** (age_days / float(self.half_life_days))

    @staticmethod
    def _fit_members(names: list[str], X: pd.DataFrame, y: np.ndarray, w: np.ndarray | None) -> list:
        out = []
        for name in names:
            if name == "lin":
                m = make_pipeline(SimpleImputer(strategy="median"),
                                  QuantileTransformer(n_quantiles=min(200, len(X)), output_distribution="normal", random_state=0),
                                  LogisticRegression(C=0.05, max_iter=500))
                out.append(m.fit(X, y, logisticregression__sample_weight=w))
            else:
                m = HistGradientBoostingClassifier(early_stopping=False, random_state=0, **PRESETS[name](len(X)))
                out.append(m.fit(X, y, sample_weight=w))
        return out

    def _choose(self, cand: pd.DataFrame, X: pd.DataFrame, y: np.ndarray, w: np.ndarray | None) -> str:
        """Recette « auto » : essai de chaque niveau sur le quart le plus récent, jamais vu (avec purge)."""
        dec = cand["decision_ts"].to_numpy()
        cut = np.quantile(dec, 0.75)
        val = dec >= cut
        tr = cand["exit_ts"].to_numpy() < cut
        if val.sum() < 100 or tr.sum() < 300 or len(np.unique(y[val])) < 2 or len(np.unique(y[tr])) < 2:
            return DEFAULT_RECIPE  # trop peu de données pour comparer : la recette de référence
        scores = {}
        fitted = {name: self._fit_members([name], X[tr], y[tr], None if w is None else w[tr])[0] for name in MEMBERS}
        for recipe, names in RECIPES.items():
            p = np.mean([fitted[n].predict_proba(X[val])[:, 1] for n in names], axis=0)
            scores[recipe] = float(roc_auc_score(y[val], p))
        self.selection = {k: round(v, 4) for k, v in scores.items()}
        # à score égal (à 0,005 près), on garde le plus simple : il résiste mieux aux changements de marché
        best = max(scores.values())
        for recipe in RECIPES:
            if scores[recipe] >= best - 0.005:
                return recipe
        return DEFAULT_RECIPE

    def fit(self, cand: pd.DataFrame) -> "MetaModel":
        cand = cand[cand["outcome"].notna()]
        self.feature_names = list(MODEL_COLUMNS)
        X = model_matrix(cand, self.feature_names)
        y = (cand["outcome_r"].to_numpy() > 0).astype(int)
        w = self._weights(cand)
        if self.params:
            self.members = [HistGradientBoostingClassifier(**self.params).fit(X, y, sample_weight=w)]
            self.chosen = "forcé"
        else:
            self.chosen = self._choose(cand, X, y, w) if self.recipe == "auto" else self.recipe
            self.members = self._fit_members(RECIPES[self.chosen], X, y, w)
        self.clf = self.members[0]
        self.payoffs = payoff_table(cand)
        if len(cand):
            self.trained_until = int(cand["exit_ts"].max())
        return self

    def raw_proba(self, X: pd.DataFrame) -> np.ndarray:
        members = self.members or ([self.clf] if self.clf is not None else [])
        assert members, "modèle non entraîné"
        if len(members) == 1:
            return members[0].predict_proba(X)[:, 1]
        return np.mean([m.predict_proba(X)[:, 1] for m in members], axis=0)

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
        p = self.calibrate(self.raw_proba(model_matrix(cand, self.feature_names)))
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
