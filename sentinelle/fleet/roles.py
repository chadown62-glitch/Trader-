"""Préparation de la consolidation des douze agents en quatre rôles actifs (décision du propriétaire du 25 novembre 2026).

Lecture seule : ce module ne change ni la flotte, ni l'ordre de décision du coordinateur, ni aucune protection. Il range
chaque agent dans un rôle, présente une décision du coordinateur par rôle et mesure l'apport de chaque rôle, pour préparer
une consolidation qui sera soumise au propriétaire avant toute modification du code de la flotte.

Rôles :
- veille : santé du système, fraîcheur des données, régime de marché, dépêches (alertes et abstention) ;
- signal : signaux d'entrée et état des protocoles de recherche (lecture seule) ;
- garde : veto anti-arnaque, limites du portefeuille fictif, sorties (protections : jamais sautées ni désactivées) ;
- exécution : profondeur, coût complet et taille compatible avec le risque.
"""
from __future__ import annotations

from collections import defaultdict

from .agents import Agent
from .coordinator import ESSENTIAL

ROLES = {
    "veille": ("System Doctor", "Market Sentinel", "Regime Detector", "News & Event Analyst"),
    "signal": ("Alpha Hunter", "Challenger Lab"),
    "garde": ("Rug Pull Guardian", "Risk Commander", "Exit Guardian"),
    "execution": ("Liquidity Scout", "Cost Router", "Portfolio Optimizer"),
}
ROLE_OF = {name: role for role, names in ROLES.items() for name in names}
PROTECTIONS = ("Rug Pull Guardian", "Risk Commander", "Exit Guardian", "System Doctor", "Market Sentinel")


def check(agents: list[Agent]) -> list[str]:
    """Problèmes de rangement : agent sans rôle, rôle vide, nom inconnu, protection hors de la veille ou de la garde."""
    names = [a.name for a in agents]
    problems = [f"agent sans rôle : {n}" for n in names if n not in ROLE_OF]
    problems += [f"nom inconnu dans les rôles : {n}" for n in ROLE_OF if n not in names]
    problems += [f"rôle vide : {r}" for r, members in ROLES.items() if not members]
    problems += [f"protection hors veille et garde : {n}" for n in PROTECTIONS if ROLE_OF.get(n) not in ("veille", "garde")]
    problems += [f"protection non essentielle pour le coordinateur : {n}" for n in PROTECTIONS if n not in ESSENTIAL]
    return problems


def by_role(record: dict) -> dict:
    """Décision du coordinateur présentée par rôle ; la décision elle-même n'est jamais recalculée."""
    findings = defaultdict(list)
    for f in record["findings"]:
        findings[ROLE_OF.get(f["agent"], "hors rôle")].append(f)
    decisive = sorted({ROLE_OF.get(a, "hors rôle") for a, pivotal in record["contribution"].items() if pivotal})
    return {"decision": record["decision"], "roles_decisifs": decisive,
            "par_role": {r: [{"agent": f["agent"], "avis": f["stance"], "resume": f["summary"]} for f in fs] for r, fs in findings.items()}}


def role_report(stats: dict) -> dict:
    """Coût et apport cumulés par rôle, à partir des statistiques par agent du coordinateur."""
    out = {r: {"agents": len(members), "executions": 0, "temps_ms": 0.0, "decisif": 0, "memoire_max_ko": 0.0} for r, members in ROLES.items()}
    for name, st in stats.items():
        r = out.get(ROLE_OF.get(name))
        if r is None:
            continue
        r["executions"] += st.runs
        r["temps_ms"] += st.wall_ms
        r["decisif"] += st.pivotal
        r["memoire_max_ko"] = max(r["memoire_max_ko"], st.peak_kb)
    return out
