"""Niveaux de preuve des composantes de coût : une valeur sans provenance est refusée."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

LEVELS = ("OBSERVED", "ESTIMATED", "DECLARED", "HYPOTHETICAL")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class Value:
    """Une composante de coût et sa provenance.

    ``value`` est dans l'unité ``unit`` (points de base du montant de l'ordre, sauf mention contraire).
    ``interval`` : intervalle de confiance (estimations) ou fourchette (hypothèses).
    """
    value: float
    unit: str
    level: str
    source: str
    as_of: str | None = None  # date de l'observation ou de la publication (UTC)
    sha256: str | None = None  # empreinte de la donnée brute
    method: str | None = None  # méthode d'estimation
    n: int | None = None  # nombre d'observations derrière l'estimation
    interval: tuple[float, float] | None = None
    note: str = ""

    def __post_init__(self):
        if self.level not in LEVELS:
            raise EvidenceError(f"Niveau de preuve inconnu : {self.level}")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise EvidenceError("Valeur non finie")
        if not self.unit or not self.source:
            raise EvidenceError("Unité et source obligatoires")
        if self.interval is not None:
            lo, hi = self.interval
            if not (math.isfinite(lo) and math.isfinite(hi) and lo <= hi):
                raise EvidenceError("Intervalle invalide")
        if self.level == "OBSERVED" and not (self.sha256 and _SHA.match(self.sha256) and self.as_of and _DATE.match(self.as_of)):
            raise EvidenceError("Une observation exige une date et l'empreinte de la donnée brute")
        if self.level == "ESTIMATED" and not (self.method and isinstance(self.n, int) and self.n > 0):
            raise EvidenceError("Une estimation exige sa méthode et son nombre d'observations")
        if self.level == "DECLARED" and not (self.as_of and _DATE.match(self.as_of)):
            raise EvidenceError("Une valeur publiée exige sa date")
        if self.level == "HYPOTHETICAL" and not self.note:
            raise EvidenceError("Une hypothèse exige une justification")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["interval"] = list(self.interval) if self.interval else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Value":
        extra = set(d) - {f for f in cls.__dataclass_fields__}
        if extra:
            raise EvidenceError("Champ inconnu : " + ", ".join(sorted(extra)))
        d = dict(d)
        if d.get("interval") is not None:
            d["interval"] = tuple(d["interval"])
        return cls(**d)


def hypothesis(value: float, note: str, *, unit: str = "bps", low: float | None = None, high: float | None = None,
               source: str = "hypothèse du laboratoire") -> Value:
    return Value(value, unit, "HYPOTHETICAL", source, interval=None if low is None else (low, high), note=note)


def weakest(levels: list[str]) -> str:
    """Le niveau d'un total est celui de sa composante la moins prouvée."""
    if not levels:
        raise EvidenceError("Aucune composante")
    return max(levels, key=LEVELS.index)
