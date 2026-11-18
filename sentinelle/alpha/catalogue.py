"""Catalogue versionné des sources pédagogiques et porte de preuve.

Chaque entrée a un niveau de preuve (voir ``catalogue_v1.json``). Une source ne peut servir :
- de **règle enseignante** que si elle est au moins ``public_heuristic`` (idée) ou ``documented_method`` ;
- d'**étiquette de gain** que si elle est ``verified_fill`` ET qu'un audit archivé existe.
Un champ « verified » auto-déclaré n'élève jamais le niveau (test adversarial).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..ai.expert_lab import ProvenanceAudit, audit_problem

LEVELS = ("claimed_result", "public_heuristic", "documented_method", "timestamped_decision", "verified_fill", "counterfactual")
TEACHING_LEVELS = ("public_heuristic", "documented_method", "timestamped_decision", "verified_fill")
CATALOGUE_PATH = Path(__file__).with_name("catalogue_v1.json")
REQUIRED = ("id", "title", "kind", "citation", "level", "rights", "used_by", "limits")


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    kind: str
    citation: str
    level: str
    rights: str
    used_by: tuple[str, ...]
    limits: str


@dataclass(frozen=True)
class Catalogue:
    version: str
    sha256: str
    sources: dict[str, Source]
    policy: tuple[str, ...]

    def teaching_sources(self, teacher: str) -> list[Source]:
        return [s for s in self.sources.values() if teacher in s.used_by]

    def can_teach_rules(self, source_id: str) -> bool:
        s = self.sources.get(source_id)
        return s is not None and s.level in TEACHING_LEVELS

    def can_label_profit(self, source_id: str, audit: ProvenanceAudit | None, *, cutoff: int) -> tuple[bool, str]:
        """Une source peut-elle fournir des gains RÉALISÉS ? Exige verified_fill + audit indépendant."""
        s = self.sources.get(source_id)
        if s is None:
            return False, "source_absente_du_catalogue"
        if s.level != "verified_fill":
            return False, f"niveau_{s.level}_insuffisant"
        problem = audit_problem(audit if audit is None or audit.source_id == source_id else None,
                                needed="verified_fill", cutoff=cutoff)
        return (problem is None), (problem or "ok")


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_catalogue(data: dict) -> Catalogue:
    if data.get("schema") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError("Catalogue : schéma inconnu")
    sources: dict[str, Source] = {}
    for e in data["entries"]:
        missing = [k for k in REQUIRED if k not in e]
        if missing:
            raise ValueError(f"Catalogue : champs manquants {missing}")
        if any(k in e for k in ("verified", "source_verified", "performance", "win_rate", "returns")):
            raise ValueError(f"Catalogue : {e['id']} déclare une vérification ou une performance ; refusé sans audit archivé")
        if e["level"] not in LEVELS:
            raise ValueError(f"Catalogue : niveau inconnu {e['level']}")
        if e["id"] in sources:
            raise ValueError(f"Catalogue : identifiant en double {e['id']}")
        if e["level"] == "claimed_result" and e["used_by"]:
            raise ValueError(f"Catalogue : {e['id']} est un résultat déclaré et ne peut enseigner")
        sources[e["id"]] = Source(e["id"], e["title"], e["kind"], e["citation"], e["level"], e["rights"],
                                  tuple(e["used_by"]), e["limits"])
    return Catalogue(str(data.get("version")), hashlib.sha256(canonical(data)).hexdigest(), sources,
                     tuple(data.get("policy", [])))


def load_catalogue(path: Path = CATALOGUE_PATH) -> Catalogue:
    return parse_catalogue(json.loads(Path(path).read_text(encoding="utf-8")))
