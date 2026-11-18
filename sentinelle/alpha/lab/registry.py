"""Préenregistrement du banc et journal des essais du laboratoire.

Le plan est écrit UNE fois, avant toute exécution sur données réelles : question, données (empreinte de
l'archive), univers, fenêtres, candidats et leurs paramètres, références, hypothèses de coûts
(empreinte), plan d'analyse, règle de décision, nombre d'essais, empreinte du code. Le banc refuse
de tourner si le plan, les coûts, les données ou le code ont changé depuis. Chaque exécution est
inscrite au journal chaîné, résultats négatifs compris.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..shadow.protocol import file_sha, journal_append, read_journal
from . import CAN_TRADE, CAPITAL, LEVERAGE, LONG_ONLY, STATUS
from .costs import CostHypotheses
from .data import LEVERAGED, MAJORS, NOT_LEVERAGED, PEGGED, TOKENIZED_GROUP
from .experiment import FLAGS, REASONS, universe_rule
from .strategies import CANDIDATES, REFERENCES

PLAN_ID = "alpha-factory-v2-e1"
DIRECTORY = "research/alpha-factory-v2"
COST_FILE = "cost-hypotheses-v1.json"
WINDOW = {"start": "2020-01-01", "split": "2023-06-01", "end": "2026-11-12"}
ALREADY_STUDIED = ("ADA", "BNB", "BTC", "DOGE", "ETH", "LINK", "SOL", "XRP")  # les 8 cryptos suivies par le projet (docs/RESULTATS.md)
IMPLEMENTATION = tuple(f"sentinelle/alpha/lab/{name}.py" for name in
                       ("__init__", "data", "costs", "strategies", "simulate", "stats", "experiment", "registry")) + (
    "sentinelle/alpha/shadow/analysis.py", "sentinelle/alpha/shadow/protocol.py", "sentinelle/ai/evaluation.py")


class RegistryError(ValueError):
    pass


def implementation_sha256(root: Path) -> dict[str, str]:
    return {name: file_sha(Path(root) / name) for name in IMPLEMENTATION}


def build_plan(*, root: Path, data: dict, hyp: CostHypotheses, other_pairs: list[list[str]], registered_at: str, registered_at_ms: int) -> dict:
    return {
        "kind": "alpha-lab-preregistration", "schema": 1, "plan_id": PLAN_ID, "status": STATUS, "can_trade": CAN_TRADE,
        "promotion_allowed": False, "no_permanent_service": True, "long_only": LONG_ONLY, "leverage": LEVERAGE, "capital": CAPITAL,
        "registered_at": registered_at, "registered_at_ms": registered_at_ms,
        "question": "Parmi des règles simples et publiées, à paramètres fixés d'avance, en existe-t-il une dont l'avantage NET "
                    "(après coûts) sur un portefeuille fictif de 30 unités, achat seulement et sans levier, soit démontrable ?",
        "evidence_level": "données historiques : tri exploratoire ; un candidat retenu n'est pas prouvé, il mérite seulement un "
                          "protocole prospectif (infrastructure de la PR #48)",
        "data": data,
        "universe": {"rule": universe_rule(), "majors": list(MAJORS),
                     "exclusions": {"pegged": sorted(PEGGED), "leveraged_pattern": LEVERAGED.pattern, "not_leveraged": sorted(NOT_LEVERAGED),
                                    "tokenized_securities_group": TOKENIZED_GROUP},
                     "series": "une colonne = une série continue ; un jour manquant coupe la série ; retrait de la cote = vente à la dernière clôture"},
        "window": dict(WINDOW),
        "window_rationale": "début quand l'univers daté compte au moins 20 actifs assez anciens ; deux périodes indépendantes de durée "
                            "voisine ; fixé d'après le seul nombre d'actifs cotés, sans regarder aucun rendement de stratégie",
        "candidates": [s.name for s in CANDIDATES], "candidate_definitions": {s.name: s.describe() for s in CANDIDATES},
        "references": [s.name for s in REFERENCES], "reference_definitions": {s.name: s.describe() for s in REFERENCES},
        "costs": {"file": COST_FILE, "sha256": hyp.sha256, "version": hyp.version, "primary": hyp.primary, "stress": hyp.stress,
                  "gross": hyp.gross, "status": hyp.status},
        "analysis": {"alpha": 0.05, "block_days": 7, "bootstrap_reps": 2000, "bootstrap_seed": 701, "confidence": 0.95, "risk_limit": 0.10,
                     "familywise": {"block_days": 7, "reps": 5000, "seed": 1701,
                                    "method": "maximum descendant (Romano-Wolf), bootstrap circulaire par blocs, mêmes jours pour tous les candidats"},
                     "relative_reference": "détention de la référence du candidat à son exposition moyenne, sans frais"},
        "decision_rule": {
            "retained_if_all": [
                "gain absolu : résultat net > 0 et probabilité corrigée des essais multiples < 5 %",
                "gain relatif : écart moyen > 0 face à la détention de même exposition, probabilité corrigée < 5 %",
                "tenue dans le temps : résultat net > 0 et écart > 0 sur chacune des deux périodes",
                "tenue aux coûts : résultat net > 0 dans le scénario de tension",
            ],
            "otherwise": "non retenu, avec ses motifs d'échec mesurés", "reasons": REASONS, "flags_not_blocking": FLAGS,
            "retained_means": "candidat à un futur protocole prospectif préenregistré ; jamais « rentable », jamais promu",
        },
        "generalization": {"exclude_already_studied": list(ALREADY_STUDIED), "other_pairs": other_pairs,
                           "other_pairs_rule": "les six actifs non majeurs les plus liquides de l'univers daté à la veille du début, par paires dans l'ordre",
                           "role": "descriptif : n'entre pas dans la règle de décision"},
        "trials": {"candidates": len(CANDIDATES), "families": sorted({s.family for s in CANDIDATES}),
                   "parameter_sets_per_candidate": 1, "tuning": "aucun : paramètres de la source citée",
                   "amendments": "aucun ; tout changement = nouveau plan, inscrit au journal"},
        "known_limits": [
            "les 8 cryptos suivies par le projet ont déjà été étudiées de 2018 à 2026 (docs/RESULTATS.md) : aucune période n'est vierge pour elles",
            "le choix des familles et des règles vient de la littérature, publiée d'après des périodes qui recoupent en partie la fenêtre",
            "pas de carnets d'ordres historiques : écart et glissement sont des hypothèses",
            "la value est un substitut par les prix : aucune donnée fondamentale ou de réseau n'est disponible hors ligne",
        ],
        "implementation_sha256": implementation_sha256(root),
    }


def write_plan(directory: Path, plan: dict) -> str:
    directory = Path(directory)
    path = directory / "preregistration.json"
    if path.exists():
        raise RegistryError("Un plan est déjà enregistré ici : il est immuable (créez un nouveau plan)")
    payload = (json.dumps(plan, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "preregistration.json.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def load_plan(directory: Path, *, root: Path | None = None, hyp: CostHypotheses | None = None, data: dict | None = None) -> tuple[dict, str, list[str]]:
    """Charge le plan ; renvoie aussi son empreinte et les écarts constatés (liste vide = tout est conforme)."""
    directory = Path(directory)
    payload = (directory / "preregistration.json").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(payload).hexdigest()
    problems = []
    if digest != (directory / "preregistration.json.sha256").read_text(encoding="utf-8").strip():
        problems.append("le plan ne correspond plus à son empreinte")
    plan = json.loads(payload)
    if hyp is not None and hyp.sha256 != plan["costs"]["sha256"]:
        problems.append("les hypothèses de coûts ont changé depuis l'enregistrement")
    if data is not None and data.get("archive_manifest_sha256") != plan["data"]["archive_manifest_sha256"]:
        problems.append("l'archive de données n'est pas celle du plan")
    if root is not None:
        current = implementation_sha256(root)
        problems += [f"code modifié depuis l'enregistrement : {name}" for name, h in plan["implementation_sha256"].items() if current.get(name) != h]
    return plan, digest, problems


def record(directory: Path, kind: str, body: dict, at_ms: int) -> dict:
    return journal_append(Path(directory) / "journal.jsonl", kind, body, at_ms)


def journal(directory: Path) -> list[dict]:
    return read_journal(Path(directory) / "journal.jsonl")
