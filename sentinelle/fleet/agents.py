"""Les douze agents. Chacun déclare ce qu'il réutilise, les tâches qui le concernent et son coût ; aucun n'a de pouvoir d'ordre."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..dmh import security
from ..memecoin import guardian, lab as mlab

SUPPORT, OPPOSE, VETO, ABSTAIN, INFO, ALERT = "SOUTIENT", "S'OPPOSE", "VETO", "ABSTENTION", "INFO", "ALERTE"
TASKS = ("memecoin_candidate", "cex_order", "position", "market_tick", "health", "research")


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


class MarketSentinel(Agent):
    name, role = "Market Sentinel", "fraîcheur et cohérence des données de marché"
    reuses = ("sentinelle.memecoin.collect (horodatage, empreintes)", "sentinelle.dmh.observe (fraîcheur des carnets)")
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
        if price is not None and not (np.isfinite(price) and price > 0):
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
    name, role = "Alpha Hunter", "signaux des règles figées (memecoins : module C ; BTC/ETH : momentum 4 semaines)"
    reuses = ("sentinelle.memecoin.lab.signals", "règle mom_ts_28 (ALPHA FACTORY v2, figée)")
    tasks = frozenset({"memecoin_candidate", "market_tick"})

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            g = ev.data.get("grid")
            if g is None or len(g) < 61:
                return self.f(ABSTAIN, "moins d'une heure de bougies")
            g = np.asarray(g, float)
            hits = [name for name in mlab.STRATEGIES if mlab.signals(name, g)[-1]]
            return self.f(SUPPORT, "signal : " + ", ".join(hits), *hits) if hits else self.f(ABSTAIN, "aucun signal à la dernière minute")
        c = np.asarray(ev.data.get("closes") or [], float)
        if len(c) < 29:
            return self.f(ABSTAIN, "historique insuffisant (28 jours)")
        r = c[-1] / c[-29] - 1
        return self.f(SUPPORT if r > 0 else ABSTAIN, f"momentum 28 jours {r:+.1%}", momentum=float(r))


class RugPullGuardian(Agent):
    name, role = "Rug Pull Guardian", "veto anti-arnaque (module B)"
    reuses = ("sentinelle.memecoin.guardian.assess",)
    tasks = frozenset({"memecoin_candidate"})

    def run(self, ev, ctx):
        v = guardian.assess(ev.data["pool"], ev.data.get("security"), now_ms=ev.ts_ms)
        if v["status"] == guardian.VETO:
            return self.f(VETO, "veto anti-arnaque", *v["veto"], severity=2)
        if v["status"] == guardian.ABSTAIN:
            return self.f(ABSTAIN, "données insuffisantes ou signaux faibles", *(v["missing"] + v["weak"]), severity=1)
        return self.f(INFO, "aucun veto (ne garantit ni l'absence d'arnaque ni un gain)")


class LiquidityScout(Agent):
    name, role = "Liquidity Scout", "profondeur et impact de prix d'un ordre"
    reuses = ("sentinelle.memecoin.lab.impact (produit constant)", "profondeur des carnets du hub (sentinelle.dmh.cex)")
    tasks = frozenset({"memecoin_candidate", "cex_order"})
    MAX_IMPACT = 0.02

    def run(self, ev, ctx):
        notional = ev.data.get("notional_usd", mlab.ORDER_USD)
        if ev.kind == "memecoin_candidate":
            imp = mlab.impact(notional, ev.data["pool"].get("reserve_usd"))
        else:
            depth = ev.data.get("depth_usd")
            if not depth:
                return self.f(ABSTAIN, "profondeur inconnue", severity=1)
            imp = notional / depth
        if imp > self.MAX_IMPACT:
            return self.f(OPPOSE, f"impact estimé {imp:.1%}", f"> {self.MAX_IMPACT:.0%}", severity=1, impact=imp)
        return self.f(INFO, f"impact estimé {imp:.2%}", impact=imp)


class CostRouter(Agent):
    name, role = "Cost Router", "coût complet d'un aller-retour et route la moins chère"
    reuses = ("sentinelle.memecoin.lab.Costs", "sentinelle.dmh.router (choix robuste des routes CEX/DEX)")
    tasks = frozenset({"memecoin_candidate", "cex_order"})
    MAX_RT = 0.10

    def run(self, ev, ctx):
        if ev.kind == "memecoin_candidate":
            p = ev.data["pool"]
            c = mlab.Costs.for_pool(p, ctx.get("sol_usd", mlab.HYPOTHESES["sol_usd_fallback"]))
            imp = mlab.impact(mlab.ORDER_USD, p.get("reserve_usd"))
            rt = 2 * (c.dex_fee + c.mev + imp) + 2 * c.network_usd_per_swap / mlab.ORDER_USD
        else:
            bps = ev.data.get("route_cost_bps")
            if bps is None:
                return self.f(ABSTAIN, "coût de route inconnu", severity=1)
            rt = bps / 1e4
        edge = ev.data.get("expected_edge")
        if rt > self.MAX_RT or (edge is not None and edge <= rt):
            return self.f(OPPOSE, f"aller-retour estimé {rt:.1%}", "coût supérieur à l'avantage attendu" if edge is not None else "coût excessif",
                          severity=1, round_trip=rt)
        return self.f(INFO, f"aller-retour estimé {rt:.1%}", round_trip=rt)


class RiskCommander(Agent):
    name, role = "Risk Commander", "limites du portefeuille fictif (n'ajoute que des restrictions ; ne remplace pas le moteur de veto)"
    reuses = ("sentinelle.dmh.security (limites par défaut, plafonds)",)
    tasks = frozenset({"memecoin_candidate", "cex_order", "position"})

    def run(self, ev, ctx):
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


class ExitGuardian(Agent):
    name, role = "Exit Guardian", "règles de sortie des positions ouvertes (objectif, stop, durée, effondrement)"
    reuses = ("sentinelle.memecoin.lab.EXITS", "sentinelle.memecoin.lab.COLLAPSE")
    tasks = frozenset({"position"})

    def run(self, ev, ctx):
        d = ev.data
        entry, price, minutes = d["entry_price"], d["price"], d.get("minutes", 0)
        rule = mlab.EXITS.get(d.get("strategy", ""), {"sl": 0.10, "max": 60})
        r = price / entry - 1
        if price <= mlab.COLLAPSE * entry:
            return self.f(SUPPORT, "sortie : effondrement du prix (retrait de liquidité probable)", severity=2, ret=r)
        if rule.get("sl") and r <= -rule["sl"]:
            return self.f(SUPPORT, f"sortie : stop atteint ({r:+.0%})", ret=r)
        if rule.get("tp") and r >= rule["tp"]:
            return self.f(SUPPORT, f"sortie : objectif atteint ({r:+.0%})", ret=r)
        if rule.get("trail") and d.get("peak_price") and price <= d["peak_price"] * (1 - rule["trail"]):
            return self.f(SUPPORT, "sortie : recul depuis le sommet", ret=r)
        if minutes >= rule.get("max", 60):
            return self.f(SUPPORT, "sortie : durée maximale", ret=r)
        return self.f(INFO, f"conserver ({r:+.1%}, {minutes} min)", ret=r)


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
    name, role = "Portfolio Optimizer", "taille compatible avec la limite de risque et le minimum d'ordre (30 € fictifs)"
    reuses = ("logique de taille de la mission 55 (limite de baisse / baisse historique)", "sentinelle.dmh.security (minimum d'ordre)")
    tasks = frozenset({"cex_order", "memecoin_candidate"})

    def run(self, ev, ctx):
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
    name, role = "System Doctor", "santé du système : sources, coupe-circuits, disque, journal, horloge"
    reuses = ("sentinelle.memecoin.collect.Stats (disponibilité des sources)", "sentinelle.alpha.shadow.protocol.read_journal (chaîne)")
    tasks = frozenset(TASKS)
    MIN_FREE_MB = 500
    MIN_AVAILABILITY = 0.5

    def run(self, ev, ctx):
        problems, notes = [], []
        for src, s in (ctx.get("sources") or {}).items():
            if s.get("requests", 0) >= 3 and s.get("availability", 1.0) < self.MIN_AVAILABILITY:
                notes.append(f"source dégradée : {src} ({s['availability']:.0%})")
        root = ctx.get("disk_path")
        if root:
            free = shutil.disk_usage(root).free / 2**20
            if free < self.MIN_FREE_MB:
                problems.append(f"disque presque plein ({free:.0f} Mo libres)")
        if abs(ctx.get("clock_offset_ms", 0)) > 2_000:
            problems.append("horloge décalée de plus de 2 s")
        journal = ctx.get("journal_path")
        if journal and Path(journal).exists():
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
