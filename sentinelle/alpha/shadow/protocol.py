"""Manifeste immuable du protocole ``alpha-shadow-r1`` et journal des essais.

Le manifeste fixe AVANT toute observation future : l'hypothèse, le candidat unique (empreinte de
son artefact), les bras de comparaison, l'univers, les coûts, la fenêtre (date la plus précoce,
durée), le plan d'analyse, les critères d'arrêt, le nombre d'essais (un) et l'empreinte de chaque
fichier de code qui décide ou simule. Rien n'y est modifiable : tout changement impose un NOUVEAU
protocole, sur une NOUVELLE fenêtre future, inscrit au journal des essais.

Le journal (``trials.jsonl``) est en ajout seul et chaîné : gel, enregistrement, démarrage,
arrêt, rapport. Un second démarrage prospectif du même protocole y est refusé.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from ..execution import CostModel, PortfolioRules
from . import CONTRACT
from .analysis import DEFAULT_PLAN
from .contract import VERDICTS, canonical, sha256
from .engine import Limits, SessionConfig
from .ledger import GENESIS, line_sha

PROTOCOL_ID = "alpha-shadow-r1-p1"
DIRECTORY = "research/alpha-shadow-r1"
IMPLEMENTATION = (
    "sentinelle/alpha/shadow/__init__.py", "sentinelle/alpha/shadow/contract.py", "sentinelle/alpha/shadow/online.py",
    "sentinelle/alpha/shadow/r1.py", "sentinelle/alpha/shadow/ledger.py", "sentinelle/alpha/shadow/engine.py",
    "sentinelle/alpha/shadow/analysis.py", "sentinelle/alpha/shadow/reference.py", "sentinelle/alpha/shadow/protocol.py",
    "sentinelle/alpha/bars.py", "sentinelle/alpha/teachers.py", "sentinelle/alpha/execution.py",
    "sentinelle/execution/contract.py", "sentinelle/ai/evaluation.py",
)


class ProtocolError(ValueError):
    pass


def file_sha(path: Path) -> str:
    """Empreinte indépendante des fins de ligne (un dépôt extrait sous Windows garde la même empreinte)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def binary_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def implementation_sha256(root: Path) -> dict[str, str]:
    return {name: file_sha(Path(root) / name) for name in IMPLEMENTATION}


def build_manifest(*, root: Path, model_sha256: str, reference_sha256: str | None, development_sha256: str, registered_at_ms: int,
                   registered_at: str, not_before_ms: int, horizon_days: int, min_r1_entries: int, symbols: list[str],
                   cost: CostModel, rules: PortfolioRules, limits: Limits, power: dict, data: dict) -> dict:
    return {
        "schema": 1, "contract": CONTRACT, "protocol_id": PROTOCOL_ID,
        "name": "RegimeRouter-R1 : preuve prospective shadow à candidat unique",
        "mode": "shadow_only", "can_trade": False, "promotion_allowed": False, "no_permanent_service_authorized": True,
        "status": "PREREGISTERED_NOT_STARTED",
        "distinct_from": ["research/prospective-v2", "research/macro-shadow-v3"],
        "registered_at": registered_at, "registered_at_ms": registered_at_ms,
        "hypothesis": {
            "primary": "La politique figée RegimeRouter-R1 améliore l'espérance nette du portefeuille fictif de 30 EUR : "
                       "rendement quotidien net > 0 ET supérieur à celui de la référence « toutes les entrées », limites de risque respectées.",
            "falsified_if": "à la fin de la fenêtre, l'une des deux bornes basses n'est pas strictement positive, ou le critère d'arrêt est atteint",
            "secondary_distinct": "vitesse et coût d'apprentissage : mesurés à part, jamais une preuve de rendement",
        },
        "candidate": {"name": "RegimeRouter-R1", "count": 1, "model_file": "r1-model.json", "model_sha256": model_sha256,
                      "frozen": "algorithme, hyperparamètres, table, marge, règles des enseignants ; aucun réapprentissage pendant la fenêtre"},
        "arms": {"candidate": "r1", "primary_reference": "take_all",
                 "descriptive": ["cash", "teacher:<référence fixée dans l'artefact>", "project_mixte", "buy_and_hold"],
                 "isolation": "un portefeuille fictif de 30 EUR par bras ; aucun lien avec le portefeuille principal ni avec un courtier",
                 "project_mixte_file": "reference-project-mixte.joblib", "project_mixte_sha256": reference_sha256},
        "universe": {"symbols": symbols, "timeframe_ms": 14_400_000,
                     "decision_inputs": "bougies publiques closes uniquement (recalculables par tous) ; les carnets ne servent qu'au prix d'exécution"},
        "window": {"not_before_ms": not_before_ms, "horizon_days": horizon_days,
                   "start": "une minuit UTC >= not_before, choisie à l'activation ; la session doit être créée et ancrée AVANT ce début",
                   "end": "début + horizon_days ; une seule lecture des résultats, à la fin",
                   "warmup_bars": limits.warmup_bars},
        "execution": {"cost": asdict(cost), "rules": asdict(rules), "limits": asdict(limits),
                      "entry": "premier carnet reçu après l'écriture de la décision, dans le délai ; à défaut ouverture suivante "
                               "+ demi-écart + glissement (approximation, non confirmatoire)",
                      "exit": "contrat d'exécution du projet (stop au pire du stop et de l'ouverture, règle et durée à l'ouverture suivante)",
                      "open_at_end": "valorisées à la dernière clôture, frais de sortie estimés déduits (résultat latent)",
                      "refused_or_abstained": "aucun résultat attribué"},
        "analysis": {**DEFAULT_PLAN, "min_r1_entries": min_r1_entries, "trials": 1, "interim_looks": "couverture et sécurité seulement",
                     "method": "rendements quotidiens UTC ; bootstrap circulaire par blocs de 7 jours ; gain absolu ET écart apparié",
                     "power": power},
        "abort_criteria": [f"baisse maximale du bras R1 >= {limits.max_drawdown:.0%} : plus aucune entrée, verdict REJECTED",
                           "empreinte du manifeste, du modèle ou du code différente : FAILED_INTEGRITY",
                           "registre incohérent avec ses ancres : FAILED_INTEGRITY"],
        "trials": {"count": 1, "journal": "trials.jsonl",
                   "amendments": "aucun ; toute modification = nouveau protocole, nouvelle fenêtre future, nouvelle ligne au journal"},
        "verdicts": list(VERDICTS),
        "decision_rule": "sans amélioration robuste à la fin de la fenêtre, R1 est rejeté et son rapport intégral conservé",
        "development": {"data": data, "report_file": "development-report.json", "report_sha256": development_sha256,
                        "status": "périodes déjà étudiées : exploratoire, aucune valeur de preuve"},
        "implementation_sha256": implementation_sha256(root),
        "requirements_before_start": [
            "revue de cette PR et revue commune du contrat avec l'export de Mission Control (#39)",
            "validation Windows du collecteur par GPT Work, sur le PC du propriétaire",
            "accord explicite du propriétaire pour une observation à durée finie (aucun service permanent)",
            "flux de bougies 4 h closes reçues en moins de 5 minutes, avec au moins 320 bougies d'historique par actif",
            "carnets horodatés reçus après chaque décision, sinon le verdict restera INSUFFICIENT_EVIDENCE",
            "publication datée des ancres du registre (au moins une avant le début, puis une par jour)",
        ],
    }


def write_manifest(directory: Path, manifest: dict) -> str:
    directory = Path(directory)
    path = directory / "protocol.json"
    if path.exists():
        raise ProtocolError("Un protocole est déjà enregistré ici : il est immuable (créez un nouveau protocole)")
    payload = (json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "protocol.json.sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def load_manifest(directory: Path, *, root: Path | None = None) -> tuple[dict, str, list[str]]:
    """Charge le manifeste ; renvoie aussi son empreinte et la liste des écarts constatés (vide = intact)."""
    directory = Path(directory)
    payload = (directory / "protocol.json").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(payload).hexdigest()
    problems = []
    if digest != (directory / "protocol.json.sha256").read_text(encoding="utf-8").strip():
        problems.append("protocol.json ne correspond plus à son empreinte")
    manifest = json.loads(payload)
    model = directory / manifest["candidate"]["model_file"]
    if sha256(json.loads(model.read_bytes())) != manifest["candidate"]["model_sha256"]:
        problems.append("artefact R1 modifié")
    ref = manifest["arms"]["project_mixte_sha256"]
    if ref is not None and binary_sha(directory / manifest["arms"]["project_mixte_file"]) != ref:
        problems.append("référence « modèle du projet » modifiée")
    if file_sha(directory / manifest["development"]["report_file"]) != manifest["development"]["report_sha256"]:
        problems.append("rapport de développement modifié")
    if root is not None:
        current = implementation_sha256(root)
        problems += [f"code modifié depuis l'enregistrement : {name}" for name, h in manifest["implementation_sha256"].items()
                     if current.get(name) != h]
    return manifest, digest, problems


def session_config(manifest: dict, manifest_sha256: str, *, start_ms: int, mode: str, horizon_days: int | None = None) -> SessionConfig:
    """Configuration d'une session. En mode prospectif la durée est celle du manifeste, sans exception."""
    days = manifest["window"]["horizon_days"]
    if mode == "prospective":
        if horizon_days not in (None, days):
            raise ProtocolError("La durée de la fenêtre prospective est fixée par le manifeste")
        if start_ms % 86_400_000:
            raise ProtocolError("La fenêtre prospective commence à minuit UTC")
    else:
        days = horizon_days or days
    ex = manifest["execution"]
    return SessionConfig(protocol_id=manifest["protocol_id"], manifest_sha256=manifest_sha256, mode=mode,
                         symbols=tuple(manifest["universe"]["symbols"]), start_ms=start_ms, end_ms=start_ms + days * 86_400_000,
                         tf_ms=manifest["universe"]["timeframe_ms"], not_before_ms=manifest["window"]["not_before_ms"],
                         cost=ex["cost"], rules=ex["rules"], limits=ex["limits"])


# ---------------------------------------------------------------------------
# Journal des essais
# ---------------------------------------------------------------------------


def read_journal(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    lines, prev = [], GENESIS
    for k, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        ln = json.loads(raw)
        if ln.get("seq") != k or ln.get("prev") != prev or line_sha(ln) != ln.get("sha"):
            raise ProtocolError(f"Journal des essais altéré à la ligne {k}")
        lines.append(ln)
        prev = ln["sha"]
    return lines


def journal_append(path: Path, kind: str, body: dict, at_ms: int) -> dict:
    lines = read_journal(path)
    if kind == "prospective_session_started" and any(ln["kind"] == kind and ln["body"].get("protocol_id") == body.get("protocol_id")
                                                    for ln in lines):
        raise ProtocolError("Ce protocole a déjà eu sa session prospective : un seul essai est autorisé")
    line = {"seq": len(lines), "prev": lines[-1]["sha"] if lines else GENESIS, "at_ms": int(at_ms), "kind": kind, "body": body}
    line["sha"] = line_sha(line)
    with Path(path).open("ab") as fh:
        fh.write(canonical(line) + b"\n")
    return line
