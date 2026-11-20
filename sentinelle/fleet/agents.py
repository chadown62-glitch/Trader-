"""Les douze agents. Chacun déclare ce qu'il réutilise, les tâches qui le concernent et son coût ; aucun n'a de pouvoir d'ordre.

Dans la session memecoin (M58), un candidat arrive avec ce que la session a déjà établi : signaux des stratégies
(``memecoin_strategies.evaluate``), verdict du veto (``tokenrisk.assess_candidate``), aller-retour Jupiter mesuré à la
sélection du pool (``established_pools``), liquidité, et coûts calculés avec le modèle du simulateur. Les agents jugent
ces éléments ; ils n'appellent aucune source et ne modifient rien.
"""
from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..dmh import security
from ..dmh.memecoin_strategies import CALIBRATED, exit_decision

SUPPORT, OPPOSE, VETO, ABSTAIN, INFO, ALERT = "SOUTIENT", "S'OPPOSE", "VETO", "ABSTENTION", "INFO", "ALERTE"
TASKS = ("memecoin_candidate", "cex_order", "position", "market_tick", "health", "research")
MAX_ROUND_TRIP_LOSS = 0.10  # même seuil que la sélection des pools établis


@dataclass(frozen=True)
class Event:
    kind: str
    key: str
    ts_ms: int
    data: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Finding:
    agent: str
    stance: str
    summary: str
    reasons: tuple = ()
    severity: int = 0  # 0 information, 1 réserve, 2 bloquant
    evidence: dict = field(default_factory=dict, compare=False)


class Agent:
    name = "agent"
    role = ""
    reuses: tuple[str, ...] = ()
    tasks: frozenset = frozenset()
    heavy = False  # analyse lourde : seulement si l'événement est pertinent et le budget le permet
    model: str | None = None  # modèle local utilisé (compte dans la limite de modèles simultanés)
    ram_mb = 5

    def relevance(self, ev: Event, ctx: dict) -> float:
        return 1.0 if ev.kind in self.tasks else 0.0

    def run(self, ev: Event, ctx: dict) -> Finding:  # pragma: no cover - interface
        raise NotImplementedError

    def f(self, stance: str, summary: str, *reasons, severity: int = 0, **evidence) -> Finding:
        return Finding(self.name, stance, summary, tuple(reasons), severity, evidence)

    def describe(self) -> dict:
        return {"name": self.name, "role": self.role, "reuses": list(self.reuses), "tasks": sorted(self.tasks), "heavy": self.heavy,
                "model": self.model, "ram_mb": self.ram_mb}


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class MarketSentinel(Agent):
    name, role = "Market Sentinel", "fraîcheur et cohérence des données de marché"
    reuses = ("horodatage des cotations du simulateur memecoin", "sentinelle.dmh.observe (fraîcheur des carnets)")
    tasks = frozenset({"memecoin_candidate", "cex_order", "market_tick", "position"})
    MAX_AGE_MS = {"cex_order": 30_000, "market_tick": 120_000, "position": 120_000, "memecoin_candidate": 900_000}

    def run(self, ev, ctx):
        obs = ev.data.get("observed_ms")
        if obs is None:
            return self.f(ALERT, "donnée sans horodatage", "aucune date d'observation", severity=2)
        age = ev.ts_ms - obs
        limit = self.MAX_AGE_MS.get(ev.kind, 120_000)
        if age > limit:
            return self.f(ALERT, f"donnée périmée ({age / 1000:.0f} s)", f"âge > {limit / 1000:.0f} s", severity=2, age_ms=age)
        if age < -5_000:
            return self.f(ALERT, "donnée datée du futur", "horloge incohérente", severity=2, age_ms=age)
        price = ev.data.get("price")
        if price is not None and not (_finite(price) and price > 0):
            return self.f(ALERT, "prix invalide", severity=2)
        return self.f(INFO, "données fraîches", age_ms=age)


class RegimeDetector(Agent):
    name, role = "Regime Detector", "régime de marché (tendance et volatilité du BTC), information de la veille"
    reuses = ("règle des régimes de la mission 55 (moyenne 200 jours, volatilité 30 jours)",)
    tasks = frozenset({"market_tick", "cex_order", "research"})

    def run(self, ev, ctx):
        c = np.asarray(ev.data.get("btc_closes") or ctx.get("btc_closes") or [], float)
        if len(c) < 201:
            return self.f(ABSTAIN, "historique BTC insuffisant (200 jours)")
        trend = "haussier" if c[-1] > c[-200:].mean() else "baissier"
        vol = float(np.std(np.diff(c[-31:]) / c[-31:-1], ddof=1))
        return self.f(INFO, f"régime {trend}, volatilité quotidienne {vol:.1%}", regime=trend, volatility=vol)


class AlphaHunter(Agent):
    name, role = "Alpha Hunter", "signaux d'entrée (memecoins : stratégies de la session ; BTC/ETH : momentum 4 semaines)"
    reuses = ("sentinelle.dmh.memecoin_strategies.evaluate", "règle mom_ts_28 (ALPHA FACTORY v2, figée)")
    tasks = frozenset({"memecoin_candidate", "market_tick"})

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            hits = sorted(set(ev.data.get("signals") or ()))
            return self.f(SUPPORT, "signal : " + ", ".join(hits), *hits) if hits else self.f(ABSTAIN, "aucun signal d'entrée")
        c = np.asarray(ev.data.get("closes") or [], float)
        if len(c) < 29:
            return self.f(ABSTAIN, "historique insuffisant (28 jours)")
        r = c[-1] / c[-29] - 1
        return self.f(SUPPORT if r > 0 else ABSTAIN, f"momentum 28 jours {r:+.1%}", momentum=float(r))


class RugPullGuardian(Agent):
    name, role = "Rug Pull Guardian", "veto anti-arnaque et revendabilité confirmée"
    reuses = ("sentinelle.dmh.tokenrisk.assess_candidate", "aller-retour Jupiter de sentinelle.dmh.established_pools")
    tasks = frozenset({"memecoin_candidate"})

    def run(self, ev, ctx):
        guard = ev.data.get("guardian")
        if not isinstance(guard, dict):
            return self.f(ABSTAIN, "veto anti-arnaque non évalué", severity=1)
        if not guard.get("allowed"):
            return self.f(VETO, "veto anti-arnaque", *(guard.get("vetoes") or ("refusé",)), severity=2)
        loss = ev.data.get("round_trip_loss")
        if loss is None:
            return self.f(ABSTAIN, "revendabilité non confirmée (aucun aller-retour Jupiter mesuré)", severity=1)
        if not _finite(loss) or loss > MAX_ROUND_TRIP_LOSS:
            return self.f(VETO, "revente trop chère ou impossible", f"aller-retour {loss:.1%} > {MAX_ROUND_TRIP_LOSS:.0%}"
                          if _finite(loss) else "aller-retour illisible", severity=2)
        return self.f(INFO, f"aucun veto ; aller-retour mesuré {loss:.1%} (ne garantit ni l'absence d'arnaque ni un gain)",
                      round_trip_loss=loss)


class LiquidityScout(Agent):
    name, role = "Liquidity Scout", "profondeur et impact de prix d'un ordre"
    reuses = ("modèle d'impact du simulateur memecoin (produit constant)", "profondeur des carnets du hub (sentinelle.dmh.cex)")
    tasks = frozenset({"memecoin_candidate", "cex_order"})
    MAX_IMPACT = 0.02

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            imp = ev.data.get("impact")
        else:
            depth = ev.data.get("depth_usd")
            imp = ev.data.get("notional_usd", 10.0) / depth if depth else None
        if not _finite(imp):
            return self.f(ABSTAIN, "profondeur inconnue", severity=1)
        if imp > self.MAX_IMPACT:
            return self.f(OPPOSE, f"impact estimé {imp:.1%}", f"> {self.MAX_IMPACT:.0%}", severity=1, impact=imp)
        return self.f(INFO, f"impact estimé {imp:.2%}", impact=imp)


class CostRouter(Agent):
    name, role = "Cost Router", "coût complet d'un aller-retour"
    reuses = ("modèle de coûts du simulateur memecoin (frais DEX, impact, réseau)", "sentinelle.dmh.router (routes CEX/DEX)")
    tasks = frozenset({"memecoin_candidate", "cex_order"})
    MAX_RT = 0.10

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            rt = ev.data.get("round_trip_cost")
        else:
            bps = ev.data.get("route_cost_bps")
            rt = bps / 1e4 if _finite(bps) else None
        if not _finite(rt):
            return self.f(ABSTAIN, "coût d'aller-retour inconnu", severity=1)
        edge = ev.data.get("expected_edge")
        if rt > self.MAX_RT or (edge is not None and edge <= rt):
            return self.f(OPPOSE, f"aller-retour estimé {rt:.1%}", "coût supérieur à l'avantage attendu" if edge is not None else "coût excessif",
                          severity=1, round_trip=rt)
        return self.f(INFO, f"aller-retour estimé {rt:.1%}", round_trip=rt)


class RiskCommander(Agent):
    name, role = "Risk Commander", "limites du portefeuille fictif (n'ajoute que des restrictions ; ne remplace pas le veto)"
    reuses = ("sentinelle.dmh.security (limites par défaut, plafonds)", "état de la session memecoin (liquidités, positions)")
    tasks = frozenset({"memecoin_candidate", "cex_order", "position"})
    MEMECOIN_MAX_OPEN = 12  # 4 stratégies × 3 tokens
    MEMECOIN_MAX_SESSION_LOSS = 0.50  # au-delà, plus aucune entrée nouvelle : ce qui reste du capital fictif est préservé

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            return self._memecoin(ev.data)
        lim = security.effective(ctx.get("limits") or {})
        pf = ctx.get("portfolio") or {}
        notional = ev.data.get("notional_eur", 10.0)
        reasons = []
        if ev.kind != "position":
            if notional < lim.get("min_order_eur", 10.0):
                reasons.append(f"ordre {notional:.2f} € sous le minimum de {lim.get('min_order_eur', 10.0):.0f} €")
            if notional > lim.get("max_position_eur", notional):
                reasons.append("au-dessus du plafond par position")
            if pf.get("cash_eur") is not None and notional > pf["cash_eur"]:
                reasons.append("liquidités fictives insuffisantes")
            if pf.get("open_positions", 0) >= lim.get("max_open_positions", 99):
                reasons.append("nombre maximal de positions atteint")
        if pf.get("daily_loss_eur", 0) >= lim.get("max_daily_loss_eur", float("inf")):
            reasons.append("perte journalière maximale atteinte")
        if reasons:
            return self.f(OPPOSE, "refus du risque", *reasons, severity=2)
        return self.f(INFO, "dans les limites (le moteur de veto indépendant reste à franchir)")

    def _memecoin(self, d):
        reasons = []
        if not _finite(d.get("notional_usd")) or d["notional_usd"] <= 0:
            reasons.append("aucune taille possible (liquidités ou plafonds atteints)")
        if d.get("open_positions", 0) >= self.MEMECOIN_MAX_OPEN:
            reasons.append(f"{self.MEMECOIN_MAX_OPEN} positions ouvertes au plus")
        initial, eq = d.get("initial_usd"), d.get("equity_usd")
        if _finite(initial) and _finite(eq) and initial > 0 and eq <= (1 - self.MEMECOIN_MAX_SESSION_LOSS) * initial:
            reasons.append(f"perte de session au-delà de {self.MEMECOIN_MAX_SESSION_LOSS:.0%} : plus d'entrée nouvelle")
        if reasons:
            return self.f(OPPOSE, "refus du risque", *reasons, severity=2)
        return self.f(INFO, f"dans les limites de la session fictive (jusqu'à {d['notional_usd']:.2f} $)")


class ExitGuardian(Agent):
    name, role = "Exit Guardian", "sorties des positions ouvertes (stop −20 %, tranches, stop suiveur, effondrement)"
    reuses = ("sentinelle.dmh.memecoin_strategies.exit_decision (politique calibrée)",)
    tasks = frozenset({"position"})
    COLLAPSE = 0.10  # prix ≤ 10 % de l'entrée : retrait de liquidité probable

    def run(self, ev, ctx):
        d = ev.data
        entry, price = d.get("entry_price"), d.get("price")
        if _finite(entry) and _finite(price) and entry > 0 and price <= self.COLLAPSE * entry:
            return self.f(SUPPORT, "sortie : effondrement du prix (retrait de liquidité probable)", severity=2, ret=price / entry - 1)
        ratio = d.get("ratio")
        if not _finite(ratio):
            return self.f(ABSTAIN, "valeur nette de la position inconnue", severity=1)
        decision = exit_decision({"high": d.get("high", max(1.0, ratio)), "stage": d.get("stage", 0)}, ratio, CALIBRATED)
        if decision is not None:
            return self.f(SUPPORT, f"sortie : {decision[0]} ({decision[1]:.0%} de la position)", ratio=ratio)
        return self.f(INFO, f"conserver (valeur nette {ratio:.2f} × la mise)", ratio=ratio)


class NewsEventAnalyst(Agent):
    name, role = "News & Event Analyst", "dépêches et événements ; analyse lourde (modèle local) seulement si pertinent"
    reuses = ("sentinelle.news (dépêches, IA locale Ollama)",)
    tasks = frozenset({"memecoin_candidate", "cex_order", "market_tick"})
    heavy, model, ram_mb = True, "modèle local (Ollama)", 4000
    RISK_WORDS = ("hack", "exploit", "rug", "delist", "halt", "lawsuit", "sec ", "drain", "piratage", "retrait de la cote", "faille")

    def relevance(self, ev, ctx):
        if ev.kind not in self.tasks:
            return 0.0
        move = abs(ev.data.get("move_pct") or 0.0)
        return 1.0 if ev.data.get("headlines") else min(1.0, move / 20.0)

    def run(self, ev, ctx):
        heads = [h.lower() for h in ev.data.get("headlines") or []]
        hits = sorted({w.strip() for h in heads for w in self.RISK_WORDS if w in h})
        if hits:
            return self.f(OPPOSE, "dépêche à risque", *hits, severity=1)
        llm = ctx.get("llm")
        if heads and llm is None:
            return self.f(ABSTAIN, "dépêches présentes, modèle local indisponible : pas d'avis")
        if heads:
            verdict = llm(heads)  # fonction locale injectée ; aucun appel distant
            return self.f(OPPOSE if verdict == "risque" else INFO, f"lecture du modèle local : {verdict}")
        return self.f(INFO, "aucune dépêche liée")


class PortfolioOptimizer(Agent):
    name, role = "Portfolio Optimizer", "taille compatible avec la limite de risque et le minimum d'ordre"
    reuses = ("logique de taille de la mission 55", "taille Kelly plafonnée de sentinelle.dmh.memecoin_strategies")
    tasks = frozenset({"cex_order", "memecoin_candidate"})

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            size = ev.data.get("notional_usd")
            return self.f(INFO, f"taille plafonnée {size:.2f} $" if _finite(size) else "taille inconnue", size_usd=size)
        lim = security.effective(ctx.get("limits") or {})
        equity = (ctx.get("portfolio") or {}).get("equity_eur", 30.0)
        dd = ev.data.get("strategy_drawdown")
        limit = ctx.get("risk_limit", 0.10)
        scale = min(1.0, limit / dd) if dd else 1.0
        size = equity * scale / max(1, ev.data.get("lines", 1))
        minimum = lim.get("min_order_eur", 10.0)
        if size < minimum:
            return self.f(OPPOSE, f"taille prudente {size:.2f} € < minimum {minimum:.0f} €", "infaisable sur ce capital", severity=1,
                          size_eur=size, capital_needed=minimum * max(1, ev.data.get("lines", 1)) / scale)
        return self.f(INFO, f"taille proposée {min(size, ev.data.get('notional_eur', size)):.2f} €", size_eur=size)


class ChallengerLab(Agent):
    name, role = "Challenger Lab", "état des protocoles de recherche (lecture seule, aucune promotion)"
    reuses = ("sentinelle.alpha.lab.registry (plan et journal)", "protocole prospectif de la mission 55 quand il est présent")
    tasks = frozenset({"research"})

    def run(self, ev, ctx):
        state = ev.data.get("protocols") or {}
        if not state:
            return self.f(INFO, "aucun protocole à suivre")
        lines = [f"{k} : {v}" for k, v in sorted(state.items())]
        return self.f(INFO, "protocoles suivis (aucune promotion automatique)", *lines)


class SystemDoctor(Agent):
    name, role = "System Doctor", "santé du système : sources, disque, journal, horloge"
    reuses = ("erreurs de source de la session memecoin", "sentinelle.alpha.shadow.protocol.read_journal (chaîne)")
    tasks = frozenset(TASKS)
    MIN_FREE_MB = 500
    MIN_AVAILABILITY = 0.5

    def run(self, ev, ctx):
        problems, notes = [], []
        for src, s in (ctx.get("sources") or {}).items():
            if s.get("requests", 0) >= 3 and s.get("availability", 1.0) < self.MIN_AVAILABILITY:
                notes.append(f"source dégradée : {src} ({s['availability']:.0%})")
        deep = ev.kind == "health" or ctx.get("verify_each_event")  # disque et journal : contrôles plus lents, aux bilans de santé
        root = ctx.get("disk_path")
        if root and deep:
            free = shutil.disk_usage(root).free / 2**20
            if free < self.MIN_FREE_MB:
                problems.append(f"disque presque plein ({free:.0f} Mo libres)")
        if abs(ctx.get("clock_offset_ms", 0)) > 2_000:
            problems.append("horloge décalée de plus de 2 s")
        journal = ctx.get("journal_path")
        if deep and journal and Path(journal).exists():
            from ..alpha.shadow.protocol import read_journal
            try:
                read_journal(Path(journal))
            except Exception as exc:  # noqa: BLE001 - toute rupture de chaîne est un problème bloquant
                problems.append(f"journal des décisions illisible ou altéré ({type(exc).__name__})")
        if problems:
            return self.f(ALERT, "système en défaut", *problems, severity=2)
        if notes:
            return self.f(ALERT, "sources dégradées", *notes, severity=1)
        return self.f(INFO, "système sain")


def fleet() -> list[Agent]:
    return [SystemDoctor(), MarketSentinel(), RegimeDetector(), AlphaHunter(), RugPullGuardian(), LiquidityScout(), CostRouter(),
            RiskCommander(), ExitGuardian(), NewsEventAnalyst(), PortfolioOptimizer(), ChallengerLab()]
