"""Shadow Council : six spécialistes proposent, un coordinateur tranche — ou s'abstient.

Garanties :
- aucun accès au courtier, aux services, à l'API ou au portefeuille de l'application (aucun import,
  vérifié par analyse du code dans les tests) ; les propositions et les plans sont des objets
  figés et inertes ;
- chaque agent reçoit un ``Context`` qui ne contient que les données connues à la clôture de la
  bougie de décision (tranches ``[: i + 1]``) ; l'équipe rouge vérifie la provenance de chaque
  proposition et un audit recalcule des propositions sur un historique tronqué ;
- les désaccords restent visibles ; chaque décision répond à « pourquoi attendre ? »,
  « pourquoi sortir ? » et « et si le carnet est faux ? » ; les refus du risque sont journalisés ;
- les décisions sont écrites dans le registre AVANT que leur issue ne soit connue.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from .bars import Bars, Indicators, Universe, indicators
from .dataset import Situations
from .execution import CostModel, Plan, PortfolioRules
from .learners import ExitLearner, ProjectMixte, climate, teacher_payoffs, teacher_rates
from .markets import MacroEvent, macro_view
from .teachers import teacher

ENTER, WAIT, ABSTAIN, VETO = "ENTER", "WAIT", "ABSTAIN", "VETO"


@dataclass(frozen=True)
class Proposal:
    agent: str
    symbol: str
    as_of: int
    action: str
    data_as_of: int
    provenance: tuple[str, ...]
    reasons: tuple[str, ...]
    teacher: str | None = None
    price: float | None = None
    p_win: float | None = None  # None = inconnue (jamais inventée)
    p_source: str = "inconnue"
    expected_r: float | None = None
    cost_r: float | None = None
    uncertainty: str = "élevée"
    plan: Plan | None = None


@dataclass(frozen=True)
class Context:
    """Ce qu'un agent a le droit de voir à la clôture de la bougie ``i``."""
    symbol: str
    i: int
    as_of: int
    close: np.ndarray
    ind: dict[str, np.ndarray]
    candidates: tuple  # TeacherDecision proposées à cet instant
    features: dict[str, np.ndarray]  # caractéristiques de chaque candidat (ligne as_of)
    open_symbols: frozenset[str]
    equity: float
    day_pnl: float
    macro: dict[str, MacroEvent]


def make_context(b: Bars, ind: Indicators, i: int, candidates, features, *, open_symbols=frozenset(), equity=30.0,
                 day_pnl=0.0, macro_events: tuple[MacroEvent, ...] = ()) -> Context:
    as_of = b.close_time(i)
    sliced = {k: getattr(ind, k)[: i + 1] for k in ind.__dataclass_fields__}
    return Context(b.symbol, i, as_of, b.close[: i + 1], sliced, tuple(candidates), features, frozenset(open_symbols),
                   equity, day_pnl, macro_view(macro_events, as_of))


def _p(ctx: Context, agent: str, action: str, reasons, **kw) -> Proposal:
    return Proposal(agent, ctx.symbol, ctx.as_of, action, ctx.as_of, (f"bougies {ctx.symbol} jusqu'à l'indice {ctx.i}",),
                    tuple(reasons), **kw)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class Agent:
    name = "agent"

    def propose(self, ctx: Context) -> list[Proposal]:
        raise NotImplementedError


class StructureAgent(Agent):
    """Tendance, range, volatilité : soutient les configurations cohérentes avec la structure."""
    name = "structure"

    def propose(self, ctx):
        e50, e200, rank = ctx.ind["ema50"][-1], ctx.ind["ema200"][-1], ctx.ind["atr_rank"][-1]
        out = []
        if not (np.isfinite(e50) and np.isfinite(e200)):
            return [_p(ctx, self.name, ABSTAIN, ["structure inconnue : historique trop court"], teacher=c.teacher) for c in ctx.candidates]
        up = e50 > e200
        slope = (ctx.ind["ema50"][-1] / ctx.ind["ema50"][-7] - 1) if len(ctx.close) > 7 and np.isfinite(ctx.ind["ema50"][-7]) else 0.0
        state = "haussière" if up and slope > 0 else ("baissière" if not up and slope < 0 else "sans direction (range)")
        for c in ctx.candidates:
            fits = (c.teacher in ("trend", "breakout", "pullback") and state == "haussière") or \
                   (c.teacher == "mean_reversion" and state != "baissière")
            reasons = [f"structure {state} (moyenne 50 {'>' if up else '<'} moyenne 200, pente 50 {slope:+.2%})"]
            if np.isfinite(rank) and rank > 0.9:
                reasons.append("volatilité dans les 10 % les plus hautes")
            out.append(_p(ctx, self.name, ENTER if fits else ABSTAIN, reasons +
                          ([] if fits else [f"l'enseignant « {c.teacher} » ne correspond pas à cette structure (achat seul, pas de vente à découvert)"]),
                          teacher=c.teacher, uncertainty="moyenne"))
        return out


class EntryExitAgent(Agent):
    """Moment d'entrée (maintenant ou attendre), plan de sortie, espérance nette."""
    name = "entry_exit"

    def __init__(self, model: ProjectMixte | None, payoffs: dict, rates: dict, exits: ExitLearner | None,
                 situations_lookup: Callable[[str, int, str], int | None] | None = None, eval_situations: Situations | None = None):
        self.model, self.payoffs, self.rates, self.exits = model, payoffs, rates, exits
        self.lookup, self.s = situations_lookup, eval_situations

    def propose(self, ctx):
        out = []
        c0, e20, a = ctx.close[-1], ctx.ind["ema20"][-1], ctx.ind["atr14"][-1]
        stretch = (c0 - e20) / a if np.isfinite(e20) and np.isfinite(a) and a > 0 else 0.0
        for c in ctx.candidates:
            k = self.lookup(ctx.symbol, ctx.i, c.teacher) if self.lookup else None
            if self.model is not None and k is not None:
                sub = self.s.take(np.isin(np.arange(len(self.s)), [k]))
                p = float(self.model.proba(sub)[0])
                src = "modèle « mixte » calibré sur la fenêtre de calibration"
            else:
                p, src = self.rates.get(c.teacher), "taux de gain historique de l'enseignant (non conditionnel)"
            w, lo = self.payoffs.get(c.teacher, (1.0, -1.0))
            ev = None if p is None else p * w + (1 - p) * lo
            variant = "teacher"
            if self.exits is not None and k is not None:
                variant = self.exits.choice.get((c.teacher, int(climate(self.s.take(np.isin(np.arange(len(self.s)), [k])))[0])), "teacher")
            plan = replace(c.plan, exit_variant=variant)
            why_exit = teacher(c.teacher).invalidation() if variant == "teacher" else {
                "tight": "stop suiveur serré à 1,5 ATR (sorties plus rapides apprises dans ce climat)",
                "time": "sortie à durée fixe (apprise dans ce climat)",
                "target2r": "objectif fixe à 2 R (appris dans ce climat)"}[variant]
            reasons = [f"plan de sortie : {why_exit}"]
            if stretch > 2.5 and c.teacher in ("trend", "breakout"):
                out.append(_p(ctx, self.name, WAIT, reasons + [f"pourquoi attendre ? prix étiré de {stretch:.1f} ATR au-dessus de la moyenne 20 : entrée tardive"],
                              teacher=c.teacher, price=float(c0), p_win=p, p_source=src, expected_r=ev, plan=plan, uncertainty="moyenne"))
                continue
            out.append(_p(ctx, self.name, ENTER if (ev is not None and ev > 0) else ABSTAIN,
                          reasons + ([f"espérance nette {ev:+.2f} R"] if ev is not None else ["espérance inconnue"]),
                          teacher=c.teacher, price=float(c0), p_win=p, p_source=src, expected_r=ev, plan=plan,
                          uncertainty="moyenne" if self.model is not None else "élevée"))
        return out


class LiquidityAgent(Agent):
    """Coût d'exécution estimé en R ; refus si le marché est trop mince."""
    name = "liquidity"

    def __init__(self, cost: CostModel, max_cost_r: float = 0.2, max_spread_bps: float = 25.0):
        self.cost, self.max_cost_r, self.max_spread = cost, max_cost_r, max_spread_bps

    def cost_r(self, ctx: Context, plan: Plan, factor: float = 1.0) -> float:
        c0 = ctx.close[-1]
        spread = ctx.ind["spread_bps"][-1] * factor
        frac = 2 * self.cost.fee + spread / 1e4 + 2 * self.cost.slip * factor
        return float(frac * c0 / max(c0 - plan.stop_price, 1e-12))

    def propose(self, ctx):
        out = []
        spread = ctx.ind["spread_bps"][-1]
        for c in ctx.candidates:
            cr = self.cost_r(ctx, c.plan)
            stressed = self.cost_r(ctx, c.plan, 3.0)
            reasons = [f"coût estimé {cr:.2f} R (écart {spread:.0f} bps)", f"et si le carnet est faux ? coût ×3 = {stressed:.2f} R"]
            bad = cr > self.max_cost_r or spread > self.max_spread
            out.append(_p(ctx, self.name, VETO if bad else ENTER, reasons + (["refus : marché trop mince ou trop cher"] if bad else []),
                          teacher=c.teacher, cost_r=cr, uncertainty="faible" if not bad else "moyenne"))
        return out


class MacroAgent(Agent):
    """Annonces datées : seules les versions publiées à l'instant comptent (jamais une révision future)."""
    name = "macro"

    def __init__(self, before_ms: int = 12 * 3_600_000, after_ms: int = 4 * 3_600_000):
        self.before, self.after = before_ms, after_ms

    def propose(self, ctx):
        near = [e for e in ctx.macro.values() if e.impact == "high" and -self.after <= e.scheduled_at - ctx.as_of <= self.before]
        if not near:
            return [_p(ctx, self.name, ENTER, ["aucune annonce à fort impact connue dans la fenêtre"], teacher=c.teacher, uncertainty="moyenne")
                    for c in ctx.candidates]
        e = min(near, key=lambda x: abs(x.scheduled_at - ctx.as_of))
        hours = (e.scheduled_at - ctx.as_of) / 3_600_000
        detail = f"annonce {e.event_id} à fort impact {'dans' if hours >= 0 else 'il y a'} {abs(hours):.1f} h (version {e.version} connue)"
        return [_p(ctx, self.name, ABSTAIN, [f"pourquoi attendre ? {detail}"], teacher=c.teacher, uncertainty="élevée")
                for c in ctx.candidates]


class RiskAgent(Agent):
    """Droit de veto : budget commun du portefeuille unique de 30 € fictifs, sans levier."""
    name = "risk"

    def __init__(self, rules: PortfolioRules, daily_loss_limit: float = 0.03):
        self.rules, self.daily = rules, daily_loss_limit

    def propose(self, ctx):
        out = []
        for c in ctx.candidates:
            c0 = ctx.close[-1]
            risk_unit = (c0 - c.plan.stop_price) / c0
            notional = min(self.rules.risk_frac * ctx.equity / risk_unit, self.rules.max_position_frac * ctx.equity) if risk_unit > 0 else 0
            reasons, veto = [], False
            if ctx.symbol in ctx.open_symbols:
                veto, reasons = True, reasons + ["position déjà ouverte sur cet actif"]
            if len(ctx.open_symbols) >= self.rules.max_positions:
                veto, reasons = True, reasons + [f"déjà {len(ctx.open_symbols)} positions (maximum {self.rules.max_positions})"]
            if notional < self.rules.min_notional and self.rules.min_notional * risk_unit > self.rules.min_order_max_risk * ctx.equity:
                veto, reasons = True, reasons + ["minimum de 5 € impossible sans risquer plus de 1 % du capital"]
            if ctx.day_pnl < -self.daily * ctx.equity:
                veto, reasons = True, reasons + [f"perte du jour {ctx.day_pnl:.2f} € au-delà de la limite de {self.daily:.0%}"]
            out.append(_p(ctx, self.name, VETO if veto else ENTER, reasons or ["risque dans le budget (0,5 % par trade, pas de levier)"],
                          teacher=c.teacher, uncertainty="faible"))
        return out


class RedTeamAgent(Agent):
    """Cherche la fuite, les données périmées, le faux consensus et la fragilité aux coûts."""
    name = "red_team"

    def __init__(self, liquidity: LiquidityAgent, stale_ms: int = 2 * 14_400_000):
        self.liq, self.stale = liquidity, stale_ms

    def review(self, ctx: Context, proposals: list[Proposal]) -> list[Proposal]:
        out = []
        for c in ctx.candidates:
            mine = [p for p in proposals if p.teacher == c.teacher]
            reasons, veto = [], False
            if any(p.data_as_of > ctx.as_of for p in mine):
                veto, reasons = True, reasons + ["FUITE : une proposition utilise des données postérieures à la décision"]
            if any(p.data_as_of < ctx.as_of - self.stale for p in mine):
                veto, reasons = True, reasons + ["données périmées"]
            ee = next((p for p in mine if p.agent == "entry_exit"), None)
            if ee is not None and ee.expected_r is not None:
                stressed = self.liq.cost_r(ctx, c.plan, 3.0) - self.liq.cost_r(ctx, c.plan, 1.0)
                if ee.expected_r - stressed <= 0:
                    veto, reasons = True, reasons + [f"et si le carnet est faux ? avec des coûts ×3 l'espérance ({ee.expected_r:+.2f} R) devient négative"]
                if ee.p_win is not None and "non conditionnel" in ee.p_source:
                    reasons.append("probabilité non conditionnelle : elle ne distingue pas cette situation des autres")
            supporters = {p.agent for p in mine if p.action == ENTER}
            if supporters and (ee is None or ee.expected_r is None):
                reasons.append("consensus fragile : aucun agent n'estime l'espérance de cette entrée")
            out.append(_p(ctx, self.name, VETO if veto else ENTER, reasons or ["aucune anomalie trouvée"], teacher=c.teacher,
                          uncertainty="moyenne"))
        return out


# ---------------------------------------------------------------------------
# Coordinateur
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CouncilDecision:
    symbol: str
    as_of: int
    index: int
    action: str  # ENTER | ABSTAIN
    teacher: str | None
    plan: Plan | None
    score: float | None
    proposals: tuple[Proposal, ...]
    disagreements: tuple[str, ...]
    why_wait: tuple[str, ...]
    why_exit: str | None
    if_book_wrong: tuple[str, ...]
    vetoes: tuple[str, ...]

    def to_record(self) -> dict:
        """Version JSON archivable (sans issue)."""
        return {"symbol": self.symbol, "as_of": self.as_of, "index": self.index, "action": self.action, "teacher": self.teacher,
                "exit_variant": self.plan.exit_variant if self.plan else None, "stop": self.plan.stop_price if self.plan else None,
                "score": self.score, "disagreements": list(self.disagreements), "why_wait": list(self.why_wait),
                "why_exit": self.why_exit, "if_book_wrong": list(self.if_book_wrong), "vetoes": list(self.vetoes),
                "proposals": [{"agent": p.agent, "action": p.action, "teacher": p.teacher, "p_win": p.p_win, "p_source": p.p_source,
                               "expected_r": p.expected_r, "cost_r": p.cost_r, "reasons": list(p.reasons)} for p in self.proposals]}


AGENTS = ("structure", "entry_exit", "liquidity", "macro", "red_team", "risk")


@dataclass
class ShadowCouncil:
    """Coordinateur. ``enabled`` permet les ablations (retirer un agent, ou n'en garder qu'un)."""
    entry_exit: EntryExitAgent
    liquidity: LiquidityAgent
    risk: RiskAgent
    structure: StructureAgent = field(default_factory=StructureAgent)
    macro: MacroAgent = field(default_factory=MacroAgent)
    enabled: frozenset[str] = frozenset(AGENTS)
    mode: str = "coordinator"  # coordinator | vote
    uncertainty_penalty: float = 0.05

    def decide(self, ctx: Context) -> list[CouncilDecision]:
        props: list[Proposal] = []
        for name in ("structure", "entry_exit", "liquidity", "macro", "risk"):
            if name in self.enabled:
                props += getattr(self, name).propose(ctx)
        if "red_team" in self.enabled:
            props += RedTeamAgent(self.liquidity).review(ctx, props)
        out = []
        for c in ctx.candidates:
            mine = tuple(p for p in props if p.teacher == c.teacher)
            vetoes = tuple(f"{p.agent} : {'; '.join(p.reasons)}" for p in mine if p.action == VETO)
            against = [p for p in mine if p.action in (ABSTAIN, WAIT)]
            pro = [p for p in mine if p.action == ENTER]
            ee = next((p for p in mine if p.agent == "entry_exit"), None)
            dis = tuple(f"{a.agent} dit {a.action} / {b.agent} dit {b.action} : {'; '.join(b.reasons)}"
                        for a in pro for b in mine if b.action != ENTER and a.agent < b.agent)
            why_wait = tuple(r for p in against for r in p.reasons if not r.startswith("plan de sortie"))
            book = tuple(r for p in mine for r in p.reasons if "carnet" in r)
            plan = ee.plan if ee is not None and ee.plan is not None else c.plan
            if self.mode == "vote":
                yes = len(pro) > len(mine) / 2
                action, score = (ENTER if (yes and mine) else ABSTAIN), float(len(pro) - len(against) - len(vetoes))
            else:
                score = None
                if ee is not None and ee.expected_r is not None:
                    score = ee.expected_r - (self.uncertainty_penalty if ee.uncertainty == "élevée" else 0.0)
                elif "entry_exit" not in self.enabled:
                    score = 0.01  # sans estimateur d'espérance : on suit l'enseignant si personne ne s'y oppose
                binding = [p for p in against if p.agent in ("macro", "entry_exit", "structure")]
                action = ENTER if (not vetoes and not binding and score is not None and score > 0) else ABSTAIN
            out.append(CouncilDecision(ctx.symbol, ctx.as_of, ctx.i, action, c.teacher, plan,
                                       score, mine, dis, why_wait, None if plan is None else (
                                           teacher(c.teacher).invalidation() if plan.exit_variant == "teacher" else f"variante de sortie « {plan.exit_variant} »"),
                                       book, vetoes))
        # au plus une entrée par actif et par bougie : la meilleure ; les autres sont des abstentions motivées
        entries = [d for d in out if d.action == ENTER]
        if len(entries) > 1:
            best = max(entries, key=lambda d: (d.score or 0.0, d.teacher))
            out = [d if d is best or d.action != ENTER else replace(d, action=ABSTAIN, why_wait=d.why_wait + (f"une meilleure entrée existe ({best.teacher})",))
                   for d in out]
        return out


def build_council(train: Situations, cal: Situations, cost: CostModel, rules: PortfolioRules, *, with_model: bool = True,
                  eval_situations: Situations | None = None, enabled=frozenset(AGENTS), mode: str = "coordinator",
                  model: ProjectMixte | None = None, exits: ExitLearner | None = None) -> ShadowCouncil:
    """Assemble le conseil à partir de connaissances FIGÉES (apprentissage + calibration)."""
    if with_model and model is None:
        model = ProjectMixte()
        model.fit(train, cal)
    if exits is None:
        exits = ExitLearner()
        exits.fit(train, cal)
    lookup = None
    if eval_situations is not None:
        keys = {(eval_situations.symbol[k], int(eval_situations.index[k]), eval_situations.teacher[k]): k for k in range(len(eval_situations))}
        lookup = lambda sym, i, t: keys.get((sym, i, t))  # noqa: E731
    liq = LiquidityAgent(cost)
    ee = EntryExitAgent(model if with_model else None, teacher_payoffs(train, cal), teacher_rates(train, cal), exits, lookup, eval_situations)
    return ShadowCouncil(ee, liq, RiskAgent(rules), enabled=frozenset(enabled), mode=mode)


def run_council(universe: Universe, council: ShadowCouncil, s_eval: Situations, window: tuple[int, int], *,
                cost: CostModel, macro_events: tuple[MacroEvent, ...] = (), ledger=None,
                rules: PortfolioRules = PortfolioRules()) -> tuple[list, list[CouncilDecision]]:
    """Fait délibérer le conseil à chaque bougie où un enseignant propose une entrée, dans l'ordre du temps.

    Renvoie les intentions retenues (pour le portefeuille fictif) et toutes les décisions (y compris
    les abstentions). Les issues ne sont écrites au registre qu'à leur date de disponibilité.
    """
    inds = {sym: indicators(universe.bars[sym], cost.default_spread_bps) for sym in universe.symbols}
    by_bar: dict[tuple[int, str], list[int]] = {}
    for k in range(len(s_eval)):
        if not s_eval.vetoed[k] and window[0] <= s_eval.index[k] < window[1]:
            by_bar.setdefault((int(s_eval.index[k]), s_eval.symbol[k]), []).append(k)
    intents, decisions, pending = [], [], []
    open_until: dict[str, int] = {}
    realized: list[tuple[int, float]] = []  # (instant où l'issue devient connue, résultat estimé en €)
    for (i, sym) in sorted(by_bar):
        b = universe.bars[sym]
        as_of = b.close_time(i)
        # issues devenues connues avant cette décision : écrites au registre dans l'ordre du temps
        for item in sorted([x for x in pending if x[0] <= as_of], key=lambda x: x[0]):
            if ledger is not None:
                ledger.record_outcome(item[1], item[0], item[2])
            pending.remove(item)
        open_syms = frozenset(s for s, until in open_until.items() if until > i)  # « pas encore sortie à i » : connu à i
        known = [(t, v) for t, v in realized if t <= as_of]  # seulement les issues déjà connues
        equity = rules.capital + sum(v for _, v in known)
        day_pnl = sum(v for t, v in known if t // 86_400_000 == as_of // 86_400_000)
        ks = by_bar[(i, sym)]
        cands = [s_eval.decisions[k] for k in ks]
        ctx = make_context(b, inds[sym], i, cands, {}, open_symbols=open_syms, equity=equity, day_pnl=day_pnl, macro_events=macro_events)
        for d in council.decide(ctx):
            decisions.append(d)
            h = ledger.record_decision(d.as_of, d.to_record()) if ledger is not None else None
            if d.action != ENTER:
                continue
            k = next(k for k in ks if s_eval.teacher[k] == d.teacher)
            out = s_eval.variant_outcomes[d.plan.exit_variant][k]
            intents.append((sym, i, d.plan, out))
            if out.status == "filled":
                open_until[sym] = out.exit_index
                size = min(rules.max_position_frac * equity,
                           max(rules.min_notional, rules.risk_frac * equity / max(1e-9, (out.entry_price - out.stop_price) / out.entry_price)))
                realized.append((out.available_at, out.net_return * size))  # ne comptera qu'à sa date de disponibilité
            if h is not None and out.resolved and out.available_at is not None:
                pending.append((out.available_at, h, {"status": out.status, "net_r": out.net_r, "exit_reason": out.exit_reason}))
    if ledger is not None:
        for item in sorted(pending, key=lambda x: x[0]):
            ledger.record_outcome(item[1], item[0], item[2])
    return intents, decisions
