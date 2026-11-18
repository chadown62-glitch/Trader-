"""Hypothèses de coûts du laboratoire : un fichier versionné, appliqué tel quel, jamais optimisé ici.

La mission COST OPTIMIZER (GPT Work) est la source de vérité des coûts. Le laboratoire ne duplique
pas son moteur : il consomme un fichier d'hypothèses (frais, glissement, demi-écart par catégorie
d'actif, coût de conversion, minimum d'ordre) regroupées en scénarios nommés. Tant que cette
mission n'a rien publié, le fichier contient les valeurs des réglages du projet, déclarées
provisoires ; il suffira de le remplacer (nouvelle version, nouvelle empreinte, nouveau plan).

Unités : points de base (1 pb = 0,01 %) du montant de l'ordre. Un coût négatif ou inconnu est refusé.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

KIND = "alpha-lab-cost-hypotheses"
_SCENARIO_KEYS = {"fee_bps", "slippage_bps", "half_spread_bps", "fx_bps", "note"}
_TOP_KEYS = {"kind", "schema", "version", "source_of_truth", "status", "unit", "min_notional", "majors", "scenarios", "primary", "stress",
             "gross"}


class CostError(ValueError):
    pass


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee_bps: float  # commission de la plateforme, par ordre
    slippage_bps: float  # glissement des ordres au marché
    half_spread_major_bps: float  # demi-écart achat/vente, actifs majeurs
    half_spread_other_bps: float  # demi-écart, autres actifs
    fx_bps: float  # conversion vers la devise du portefeuille, par ordre
    min_notional: float
    majors: tuple[str, ...]

    def worse(self, base: str) -> float:
        """Dégradation du prix d'un ordre au marché (demi-écart + glissement), en fraction."""
        half = self.half_spread_major_bps if base in self.majors else self.half_spread_other_bps
        return (half + self.slippage_bps) / 1e4

    @property
    def fee(self) -> float:
        return (self.fee_bps + self.fx_bps) / 1e4

    def round_trip(self, base: str) -> float:
        """Coût approximatif d'un aller-retour, en fraction du montant (lecture humaine)."""
        return 2 * (self.worse(base) + self.fee)


@dataclass(frozen=True)
class CostHypotheses:
    version: str
    sha256: str
    scenarios: dict[str, CostScenario]
    primary: str
    stress: str
    gross: str
    status: str
    source_of_truth: str

    def scenario(self, name: str) -> CostScenario:
        if name not in self.scenarios:
            raise CostError(f"Scénario de coûts inconnu : {name}")
        return self.scenarios[name]


def _bps(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > 1000:
        raise CostError(f"Coût invalide : {name}")
    return float(value)


def parse(raw: dict, digest: str = "") -> CostHypotheses:
    if not isinstance(raw, dict) or raw.get("kind") != KIND or raw.get("schema") != 1:
        raise CostError("Fichier d'hypothèses de coûts inattendu")
    if set(raw) - _TOP_KEYS:
        raise CostError("Champ inconnu : " + ", ".join(sorted(set(raw) - _TOP_KEYS)))
    majors = tuple(raw["majors"])
    minimum = raw["min_notional"]
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not 0 < minimum < 1000:
        raise CostError("Minimum d'ordre invalide")
    scenarios = {}
    for name, s in raw["scenarios"].items():
        if set(s) - _SCENARIO_KEYS or set(s["half_spread_bps"]) != {"majors", "others"}:
            raise CostError(f"Scénario mal formé : {name}")
        scenarios[name] = CostScenario(name, _bps(s["fee_bps"], "fee_bps"), _bps(s["slippage_bps"], "slippage_bps"),
                                       _bps(s["half_spread_bps"]["majors"], "half_spread"), _bps(s["half_spread_bps"]["others"], "half_spread"),
                                       _bps(s["fx_bps"], "fx_bps"), float(minimum), majors)
    for key in ("primary", "stress", "gross"):
        if raw.get(key) not in scenarios:
            raise CostError(f"Scénario « {key} » absent")
    gross, primary, stress = scenarios[raw["gross"]], scenarios[raw["primary"]], scenarios[raw["stress"]]
    if gross.fee or gross.worse("BTC") or gross.worse("X"):
        raise CostError("Le scénario brut doit être sans coût")
    for base in ("BTC", "X"):
        if not primary.round_trip(base) <= stress.round_trip(base):
            raise CostError("Le scénario de tension doit coûter au moins autant que le scénario principal")
    if primary.round_trip("X") <= 0:
        raise CostError("Le scénario principal ne peut pas être gratuit")
    return CostHypotheses(str(raw["version"]), digest, scenarios, raw["primary"], raw["stress"], raw["gross"], str(raw["status"]),
                          str(raw["source_of_truth"]))


def load(path: Path) -> CostHypotheses:
    payload = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return parse(json.loads(payload), hashlib.sha256(payload).hexdigest())
