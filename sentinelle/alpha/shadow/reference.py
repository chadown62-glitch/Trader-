"""Référence descriptive « modèle du projet » (recette mixte), figée dans un fichier dont l'empreinte est vérifiée.

Ce bras n'est PAS le candidat et n'entre dans aucun verdict : il sert de point de comparaison
descriptif. Le fichier est un artefact local produit par le dépôt lui-même (jamais téléchargé) ;
il n'est chargé que si son empreinte est exactement celle du manifeste. S'il est absent ou si la
bibliothèque d'apprentissage manque, le bras est déclaré « indisponible » : il n'est pas simulé.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import numpy as np

from ..teachers import TEACHER_NAMES


class FrozenProjectMixte:
    def __init__(self, members, calib, payoffs, threshold: float, sha256: str):
        self.members, self.calib, self.payoffs, self.threshold, self.sha256 = members, calib, payoffs, float(threshold), sha256

    def expected_r(self, features: np.ndarray, teacher: str) -> float:
        onehot = np.array([1.0 if teacher == t else 0.0 for t in TEACHER_NAMES])
        x = np.concatenate([np.asarray(features, float), onehot]).reshape(1, -1)
        p = float(np.clip(np.mean([m.predict_proba(x)[:, 1] for m in self.members], axis=0)[0], 1e-4, 1 - 1e-4))
        a, b = self.calib
        p = 1 / (1 + np.exp(-(a * np.log(p / (1 - p)) + b)))
        win, loss = self.payoffs[teacher]
        return float(p * win + (1 - p) * loss)

    def takes(self, features: np.ndarray, teacher: str) -> bool:
        return self.expected_r(features, teacher) >= self.threshold


def dump(learner) -> bytes:
    """Sérialise un ``ProjectMixte`` déjà appris et réglé (développement uniquement)."""
    import joblib

    buffer = io.BytesIO()
    joblib.dump({"members": learner.members, "calib": tuple(learner.calib), "payoffs": dict(learner.payoffs),
                 "threshold": float(learner.threshold)}, buffer)
    return buffer.getvalue()


def load(path: Path, expected_sha256: str) -> FrozenProjectMixte:
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError("Empreinte de la référence « modèle du projet » inattendue : fichier refusé")
    import joblib  # artefact local figé par le dépôt, empreinte vérifiée ci-dessus

    data = joblib.load(io.BytesIO(raw))
    if not data["members"]:
        raise ValueError("Référence « modèle du projet » sans modèle")
    return FrozenProjectMixte(data["members"], data["calib"], data["payoffs"], data["threshold"], digest)
