"""RegimeRouter-R1 : le candidat UNIQUE, figé dans un artefact JSON dont l'empreinte est préenregistrée.

R1 n'apprend plus : c'est une table (enseignant × climat -> somme et nombre d'issues mûres du
développement), une marge et une règle. À la clôture d'une bougie, pour chaque entrée proposée
par un enseignant et non refusée par l'enseignant d'abstention :

    score = somme / (nombre + a priori)          entrée  <=>  score > marge

Le climat ne dépend que de deux grandeurs connues à la clôture : moyenne 50 au-dessus de la
moyenne 200 (oui / non) et tercile du rang de volatilité sur 120 bougies. Aucun réglage n'est
possible après le gel : un artefact modifié change d'empreinte et est refusé.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from ..bars import Indicators
from ..teachers import ABSTENTION_ATR_RANK, ABSTENTION_SPREAD_BPS, ABSTENTION_VOLUME_RATIO, TEACHERS
from .contract import ContractError, sha256

KIND = "alpha-shadow-r1-model"
HYPERPARAMETERS = {
    "prior": 20.0, "margins": ["-inf", 0.0, 0.05, 0.1, 0.2], "min_taken": 15,
    "climate": "3 * (ema50 > ema200) + tercile(atr_rank_120 ; seuils 1/3 et 2/3 ; inconnu = 0,5)",
    "exit": "sortie de l'enseignant", "one_position_per_symbol": "premier enseignant retenu par ordre alphabétique",
}


def frozen_rules() -> dict:
    """Règles des enseignants telles que le CODE les applique ; comparées à l'artefact à chaque chargement."""
    return {"teachers": {t.name: {"stop_atr": t.stop_atr, "max_bars": t.max_bars, "exit_rule": t.exit_rule,
                                  "trail_atr": t.trail_atr, "target_atr": t.target_atr} for t in TEACHERS},
            "abstention": {"spread_bps_above": ABSTENTION_SPREAD_BPS, "atr_rank_at_least": ABSTENTION_ATR_RANK,
                           "volume_ratio_below": ABSTENTION_VOLUME_RATIO}}


def climate_of(ind: Indicators, i: int) -> int:
    """Même définition que ``learners.climate`` (développement), pour une seule bougie."""
    with np.errstate(invalid="ignore"):
        trend = int(bool(ind.ema50[i] > ind.ema200[i]))
    rank = float(np.nan_to_num(ind.atr_rank[i], nan=0.5))
    return trend * 3 + int(np.digitize(rank, (1 / 3, 2 / 3)))


def _margin(v) -> float:
    if v == "-inf":
        return -math.inf
    if v == "+inf":
        return math.inf
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    raise ContractError("INVALID_FIELD", "margin_r")


def margin_text(v: float):
    return "-inf" if v == -math.inf else "+inf" if v == math.inf else float(v)


@dataclass(frozen=True)
class R1Model:
    cells: dict  # (enseignant, climat) -> (somme des R nets, nombre)
    prior: float
    margin: float
    reference_teacher: str
    sha256: str

    @property
    def takes_nothing(self) -> bool:
        return self.margin == math.inf

    def score(self, teacher: str, climate: int) -> float:
        total, n = self.cells.get((teacher, int(climate)), (0.0, 0))
        return total / (n + self.prior)

    def takes(self, teacher: str, climate: int, vetoed: bool) -> bool:
        return (not vetoed) and self.score(teacher, climate) > self.margin


def artifact_sha256(artifact: dict) -> str:
    return sha256(artifact)


def load_model(artifact: dict, expected_sha256: str) -> R1Model:
    """Refuse tout artefact dont l'empreinte, la forme ou les règles diffèrent de ce qui a été gelé."""
    digest = artifact_sha256(artifact)
    if digest != expected_sha256:
        raise ContractError("MODEL_SHA_MISMATCH", digest)
    if artifact.get("kind") != KIND or artifact.get("schema") != 1:
        raise ContractError("UNKNOWN_KIND", str(artifact.get("kind")))
    if artifact.get("hyperparameters") != HYPERPARAMETERS:
        raise ContractError("HYPERPARAMETERS_CHANGED")
    if artifact.get("rules") != frozen_rules():
        raise ContractError("TEACHER_RULES_CHANGED")
    names = {t.name for t in TEACHERS}
    cells = {}
    for c in artifact["cells"]:
        if set(c) != {"teacher", "climate", "sum_net_r", "n"} or c["teacher"] not in names or c["climate"] not in range(6):
            raise ContractError("INVALID_FIELD", "cells")
        if not (isinstance(c["n"], int) and c["n"] > 0 and math.isfinite(c["sum_net_r"])) or (c["teacher"], c["climate"]) in cells:
            raise ContractError("INVALID_FIELD", "cells")
        cells[(c["teacher"], c["climate"])] = (float(c["sum_net_r"]), int(c["n"]))
    if artifact["reference_teacher"] not in names:
        raise ContractError("INVALID_FIELD", "reference_teacher")
    return R1Model(cells, float(HYPERPARAMETERS["prior"]), _margin(artifact["margin_r"]), artifact["reference_teacher"], digest)


def build_artifact(table: dict, margin: float, reference_teacher: str, training: dict) -> dict:
    cells = [{"teacher": t, "climate": int(c), "sum_net_r": float(total), "n": int(n)}
             for (t, c), (total, n) in sorted(table.items()) if n > 0]
    return {"kind": KIND, "schema": 1, "name": "RegimeRouter-R1", "hyperparameters": HYPERPARAMETERS, "rules": frozen_rules(),
            "cells": cells, "margin_r": margin_text(margin), "reference_teacher": reference_teacher, "training": training}


def describe(model: R1Model) -> dict:
    return {**{k: v for k, v in asdict(model).items() if k != "cells"}, "margin": margin_text(model.margin),
            "cells": {f"{t}|{c}": {"n": n, "score_r": model.score(t, c), "enters": model.takes(t, c, False)}
                      for (t, c), (_, n) in sorted(model.cells.items())}}
