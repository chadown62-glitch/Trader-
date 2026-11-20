"""Coordinateur local : choisit les agents utiles, respecte les budgets, tranche les désaccords, archive, mesure les apports.

Ordre de décision (fixé d'avance, le plus prudent l'emporte) :
1. alerte bloquante du système ou des données → ABSTENTION ;
2. position ouverte : une règle de sortie déclenchée → SORTIE PROPOSÉE (une sortie n'ajoute pas de risque : ni le veto
   ni le refus du risque ne la bloquent) ; sinon CONSERVER ;
3. veto anti-arnaque → VETO ;
4. refus du risque → REFUS ;
5. aucun signal de l'Alpha Hunter → ATTENDRE ;
6. une objection (coût, liquidité, taille, dépêche) ou des données insuffisantes → ATTENDRE, désaccord consigné ;
7. sinon → PROPOSITION SIMULÉE. Dans la session memecoin, elle n'autorise qu'une entrée fictive.
"""
from __future__ import annotations

import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import AUTO_PROMOTION, AUTO_RETRAIN, CAN_TRADE
from .agents import ABSTAIN, ALERT, INFO, OPPOSE, SUPPORT, VETO, Agent, Event, Finding, fleet
from ..alpha.shadow.protocol import journal_append

ABSTENTION, VETOED, REFUSED, EXIT, HOLD, WAIT, PROPOSAL = (
    "ABSTENTION", "VETO", "REFUS", "SORTIE_PROPOSÉE", "CONSERVER", "ATTENDRE", "PROPOSITION_SIMULÉE")


@dataclass
class Budget:
    ms_per_event: float = 2_000.0  # temps réel ; au-delà, les agents restants (non essentiels) sont sautés
    ram_mb: float = 8_000.0  # mémoire déclarée des agents lourds autorisés simultanément (machine de 16 Go)
    max_models: int = 1  # modèles locaux simultanés
    gpu: bool = False  # un GPU est-il disponible pour les agents lourds ?
    heavy_min_relevance: float = 0.5


@dataclass
class AgentStats:
    runs: int = 0
    failures: int = 0
    cpu_ms: float = 0.0
    wall_ms: float = 0.0
    peak_kb: float = 0.0
    pivotal: int = 0
    skipped: int = 0
    quarantined: bool = False
    disabled: bool = False
    disabled_reason: str | None = None


ESSENTIAL = {"System Doctor", "Market Sentinel", "Rug Pull Guardian", "Risk Commander", "Exit Guardian"}  # jamais sautés ni désactivés


class Coordinator:
    def __init__(self, agents: list[Agent] | None = None, *, budget: Budget | None = None, journal: Path | None = None,
                 clock=time.time, quarantine_after: int = 3, disable_after_runs: int = 30, costly_ms: float = 5.0):
        assert CAN_TRADE is False and AUTO_RETRAIN is False and AUTO_PROMOTION is False
        self.agents = agents or fleet()
        self.budget = budget or Budget()
        self.journal = journal
        self.clock = clock
        self.quarantine_after, self.disable_after_runs, self.costly_ms = quarantine_after, disable_after_runs, costly_ms
        self.stats = {a.name: AgentStats() for a in self.agents}
        self.log: list[dict] = []

    # -- choix des agents ---------------------------------------------------------------------------------------------
    def plan(self, ev: Event, ctx: dict) -> tuple[list[Agent], list[dict]]:
        chosen, skipped, ram, models = [], [], 0.0, 0
        for a in self.agents:
            st = self.stats[a.name]
            if ev.kind not in a.tasks:
                continue
            rel = a.relevance(ev, ctx)
            if st.quarantined or st.disabled:
                skipped.append({"agent": a.name, "why": "quarantaine" if st.quarantined else f"désactivé : {st.disabled_reason}"})
                continue
            if a.heavy:
                if rel < self.budget.heavy_min_relevance:
                    skipped.append({"agent": a.name, "why": f"analyse lourde non justifiée (pertinence {rel:.2f})"})
                    continue
                if a.model and models >= self.budget.max_models:
                    skipped.append({"agent": a.name, "why": "limite de modèles simultanés"})
                    continue
                if ram + a.ram_mb > self.budget.ram_mb:
                    skipped.append({"agent": a.name, "why": "budget mémoire"})
                    continue
                models += 1 if a.model else 0
                ram += a.ram_mb
            chosen.append(a)
        chosen.sort(key=lambda a: (a.name not in ESSENTIAL, a.heavy))  # essentiels d'abord, lourds en dernier
        return chosen, skipped

    # -- un événement -------------------------------------------------------------------------------------------------
    def decide(self, ev: Event, ctx: dict) -> dict:
        agents, skipped = self.plan(ev, ctx)
        findings: list[Finding] = []
        spent = 0.0
        resources = {}
        for a in agents:
            st = self.stats[a.name]
            if spent > self.budget.ms_per_event and a.name not in ESSENTIAL:
                skipped.append({"agent": a.name, "why": "budget de calcul de l'événement épuisé"})
                st.skipped += 1
                continue
            tracemalloc.start()
            c0, w0 = time.process_time(), time.perf_counter()
            try:
                out = a.run(ev, ctx)
                if not isinstance(out, Finding):
                    raise TypeError("constat mal formé")
            except Exception as exc:  # noqa: BLE001 - un agent en échec ne doit jamais faire tomber la flotte
                st.failures += 1
                out = Finding(a.name, ABSTAIN, f"agent en échec ({type(exc).__name__})", (str(exc)[:120],), 1)
                if st.failures >= self.quarantine_after:
                    st.quarantined = True
            finally:
                cpu = (time.process_time() - c0) * 1000
                wall = (time.perf_counter() - w0) * 1000
                peak = tracemalloc.get_traced_memory()[1] / 1024
                tracemalloc.stop()
            spent += wall  # temps réel : l'horloge processeur de Windows avance par pas de 15,6 ms
            st.runs += 1
            st.cpu_ms += cpu
            st.wall_ms += wall
            st.peak_kb = max(st.peak_kb, peak)
            resources[a.name] = {"cpu_ms": round(cpu, 3), "wall_ms": round(wall, 3), "peak_kb": round(peak, 1)}
            findings.append(out)
        decision, why = resolve(ev, findings)
        contribution = {}
        for f in findings:
            alt, _ = resolve(ev, [g for g in findings if g is not f])
            contribution[f.agent] = alt != decision
            if alt != decision:
                self.stats[f.agent].pivotal += 1
        self._prune()
        record = {"event": {"kind": ev.kind, "key": ev.key, "ts_ms": ev.ts_ms}, "decision": decision, "why": why,
                  "requires_independent_veto": decision == PROPOSAL, "findings": [_finding(f) for f in findings],
                  "disagreements": disagreements(findings), "contribution": contribution, "skipped": skipped, "resources": resources}
        if self.journal is not None:
            journal_append(self.journal, "fleet_decision", record, int(self.clock() * 1000))
        self.log.append(record)
        return record

    def run(self, events: list[Event], ctx: dict) -> list[dict]:
        """Cycle fini : traite les événements donnés, puis s'arrête (aucun processus permanent)."""
        return [self.decide(ev, ctx) for ev in events]

    def _prune(self) -> None:
        """Désactive pour la session un agent non essentiel, COÛTEUX et jamais décisif après assez d'essais (réversible,
        consigné). Un agent bon marché reste actif : l'économie serait nulle et son avis peut redevenir utile."""
        for a in self.agents:
            st = self.stats[a.name]
            if a.name in ESSENTIAL or st.disabled or st.runs < self.disable_after_runs or st.pivotal:
                continue
            costly = a.heavy or st.wall_ms / st.runs >= self.costly_ms
            if costly:
                st.disabled, st.disabled_reason = True, f"jamais décisif en {st.runs} essais ({st.wall_ms / st.runs:.1f} ms par essai)"

    def enable(self, name: str) -> None:
        st = self.stats[name]
        st.disabled, st.disabled_reason, st.quarantined, st.failures = False, None, False, 0

    def report(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.log:
            counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        return {"events": len(self.log), "decisions": counts, "agents": {a.describe()["name"]: a.describe() | asdict(self.stats[a.name])
                                                                         for a in self.agents},
                "disagreements": sum(len(r["disagreements"]) for r in self.log), "budget": asdict(self.budget)}


def resolve(ev: Event, findings: list[Finding]) -> tuple[str, list[str]]:
    by = {f.agent: f for f in findings}
    alerts = [f for f in findings if f.stance == ALERT and f.severity >= 2]
    if alerts:
        return ABSTENTION, [f"{f.agent} : {r}" for f in alerts for r in (f.reasons or (f.summary,))]
    if ev.kind == "position":  # une sortie n'ajoute pas de risque : ni le veto ni le refus du risque ne la bloquent
        ex = by.get("Exit Guardian")
        return (EXIT, [ex.summary]) if ex and ex.stance == SUPPORT else (HOLD, [ex.summary if ex else "aucune règle de sortie évaluée"])
    vetoes = [f for f in findings if f.stance == VETO]
    if vetoes:
        return VETOED, [f"{f.agent} : {r}" for f in vetoes for r in (f.reasons or (f.summary,))]
    refusals = [f for f in findings if f.stance == OPPOSE and f.severity >= 2]
    if refusals:
        return REFUSED, [f"{f.agent} : {r}" for f in refusals for r in (f.reasons or (f.summary,))]
    hunter = by.get("Alpha Hunter")
    if ev.kind in ("memecoin_candidate", "market_tick") and (hunter is None or hunter.stance != SUPPORT):
        return WAIT, ["aucun signal d'entrée" if hunter is None else hunter.summary]
    objections = [f for f in findings if f.stance == OPPOSE or (f.stance == ABSTAIN and f.severity >= 1)]
    if objections:
        return WAIT, [f"{f.agent} : {f.summary}" for f in objections]
    if ev.kind in ("health", "research"):
        return INFO, [f.summary for f in findings]
    return PROPOSAL, ["aucune objection : proposition à soumettre au moteur de veto indépendant (simulation)"]


def disagreements(findings: list[Finding]) -> list[dict]:
    pro = [f for f in findings if f.stance == SUPPORT]
    con = [f for f in findings if f.stance in (OPPOSE, VETO)]
    return [{"for": p.agent, "against": c.agent, "against_says": c.summary} for p in pro for c in con]


def _finding(f: Finding) -> dict:
    return {"agent": f.agent, "stance": f.stance, "summary": f.summary, "reasons": list(f.reasons), "severity": f.severity,
            "evidence": {k: v for k, v in f.evidence.items() if isinstance(v, (int, float, str, bool, type(None)))}}
