"""ALPHA FACTORY — Shadow Council : aucune capacité d'ordre, causalité, désaccords, registre chaîné."""
import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.bars import indicators
from sentinelle.alpha.council import (AGENTS, ABSTAIN, ENTER, VETO, LiquidityAgent, MacroAgent, Proposal, RedTeamAgent,
                                      RiskAgent, build_council, make_context, run_council)
from sentinelle.alpha.dataset import build_situations, split, time_windows
from sentinelle.alpha.execution import CostModel, Plan, PortfolioRules
from sentinelle.alpha.ledger import LedgerError, ShadowLedger
from sentinelle.alpha.markets import MacroEvent, synthetic_macro_calendar, synthetic_universe
from sentinelle.alpha.velocity import window_indices

ROOT = Path(__file__).resolve().parent.parent
ALPHA = ROOT / "sentinelle" / "alpha"
FORBIDDEN_MODULES = ("sentinelle.execution.paper", "sentinelle.execution.positions", "sentinelle.execution.live",
                     "sentinelle.execution.base", "sentinelle.execution.book_execution", "sentinelle.services", "sentinelle.api",
                     "sentinelle.notify", "sentinelle.market.sources", "ccxt", "httpx", "requests", "socket", "urllib",
                     "aiohttp", "subprocess", "telethon")
U = synthetic_universe(21, n_bars=2400)
COST, RULES = CostModel(), PortfolioRules()
S = build_situations(U, COST)
W = time_windows(U)
MASKS, _ = split(S, W)
P = {k: S.take(v) for k, v in MASKS.items()}
EV = P["evaluation"]
WIN = window_indices(U, W.bounds["evaluation"])


# ---------------------------------------------------------------------------
# Zéro ordre possible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(ALPHA.glob("*.py")), ids=lambda p: p.name)
def test_alpha_modules_cannot_reach_a_broker_network_or_the_app(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # import relatif depuis sentinelle.alpha
                base = ("sentinelle." if node.level == 2 else "sentinelle.alpha.") + base
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        for name in names:
            assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN_MODULES), f"{path.name} importe {name}"
        if isinstance(node, (ast.Attribute, ast.Name)):
            ident = node.attr if isinstance(node, ast.Attribute) else node.id
            assert ident not in ("place_order", "create_order", "submit_order", "cancel_order", "fetch_balance", "withdraw"), path.name


def test_importing_the_lab_loads_no_broker_module():
    code = ("import sys, sentinelle.alpha.council, sentinelle.alpha.experiments, sentinelle.alpha.velocity;"
            "bad=[m for m in sys.modules if m.startswith(('sentinelle.execution.paper','sentinelle.execution.positions',"
            "'sentinelle.execution.live','sentinelle.services','sentinelle.api','ccxt'))];print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_proposals_plans_and_decisions_are_inert_frozen_objects():
    council = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV)
    intents, decisions = run_council(U, council, EV, WIN, cost=COST)
    assert decisions
    d = decisions[0]
    for obj in (d, d.proposals[0], d.plan):
        with pytest.raises(dataclasses.FrozenInstanceError):
            obj.action = ENTER if hasattr(obj, "action") else None  # type: ignore[misc]
    callables = [n for n in dir(d.plan) if not n.startswith("_") and callable(getattr(d.plan, n))]
    assert callables == []  # un plan ne sait rien faire
    assert all(isinstance(i[2], Plan) for i in intents)


# ---------------------------------------------------------------------------
# Causalité de bout en bout
# ---------------------------------------------------------------------------


def test_council_decisions_are_identical_when_the_future_is_removed():
    council = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV)
    ks = [k for k in range(len(EV)) if not EV.vetoed[k]][:12]
    assert ks
    for k in ks:
        i, sym = int(EV.index[k]), EV.symbol[k]
        b = U.bars[sym]
        cands = [EV.decisions[k]]
        full = council.decide(make_context(b, indicators(b), i, cands, {}))
        cut = b.prefix(i + 1)
        part = council.decide(make_context(cut, indicators(cut), i, cands, {}))
        assert [(x.action, x.score, x.vetoes) for x in full] == [(x.action, x.score, x.vetoes) for x in part]


def test_red_team_vetoes_a_proposal_that_uses_future_data():
    b = U.bars["AAA/EUR"]
    k = next(k for k in range(len(EV)) if EV.symbol[k] == "AAA/EUR")
    ctx = make_context(b, indicators(b), int(EV.index[k]), [EV.decisions[k]], {})
    leak = Proposal("tricheur", ctx.symbol, ctx.as_of, ENTER, ctx.as_of + 14_400_000, ("bougie suivante",), ("je connais la suite",),
                    teacher=EV.decisions[k].teacher)
    review = RedTeamAgent(LiquidityAgent(COST)).review(ctx, [leak])
    assert review[0].action == VETO and "FUITE" in review[0].reasons[0]


# ---------------------------------------------------------------------------
# Spécialistes et coordinateur
# ---------------------------------------------------------------------------


def _ctx(**kw):
    k = next(k for k in range(len(EV)) if not EV.vetoed[k])
    b = U.bars[EV.symbol[k]]
    return make_context(b, indicators(b), int(EV.index[k]), [EV.decisions[k]], {}, **kw), k


def test_macro_agent_abstains_near_a_known_high_impact_event_only():
    ctx, _ = _ctx()
    soon = MacroEvent("e1", 0, ctx.as_of + 3 * 3_600_000, ctx.as_of - 86_400_000, "high", None)
    later_known = MacroEvent("e2", 0, ctx.as_of + 3 * 3_600_000, ctx.as_of + 60_000, "high", None)  # publié APRÈS la décision
    ctx2, _ = _ctx(macro_events=(soon,))
    assert MacroAgent().propose(ctx2)[0].action == ABSTAIN
    ctx3, _ = _ctx(macro_events=(later_known,))
    assert MacroAgent().propose(ctx3)[0].action == ENTER  # inconnu à l'instant : ne peut pas servir


def test_risk_agent_vetoes_when_positions_are_full_or_the_day_is_lost():
    ctx, _ = _ctx(open_symbols={"X/EUR", "Y/EUR", "Z/EUR"})
    assert RiskAgent(RULES).propose(ctx)[0].action == VETO
    ctx, _ = _ctx(day_pnl=-2.0)
    assert RiskAgent(RULES).propose(ctx)[0].action == VETO
    ctx, _ = _ctx()
    assert RiskAgent(RULES).propose(ctx)[0].action == ENTER


def test_liquidity_agent_refuses_thin_markets_and_answers_the_order_book_question():
    ctx, _ = _ctx()
    ind = dict(ctx.ind)
    ind["spread_bps"] = ind["spread_bps"].copy()
    ind["spread_bps"][-1] = 200.0
    thin = dataclasses.replace(ctx, ind=ind)
    p = LiquidityAgent(COST).propose(thin)[0]
    assert p.action == VETO and any("carnet est faux" in r for r in p.reasons)


def test_coordinator_keeps_disagreements_and_explains_abstentions():
    council = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV)
    _, decisions = run_council(U, council, EV, WIN, cost=COST, macro_events=synthetic_macro_calendar(U, 21))
    abst = [d for d in decisions if d.action == ABSTAIN]
    assert abst and all(d.why_wait or d.vetoes for d in abst)
    assert any(d.disagreements for d in decisions)
    for d in decisions:
        if d.action == ENTER:
            assert d.score is not None and d.score > 0 and not d.vetoes and d.why_exit
        assert {p.agent for p in d.proposals} <= set(AGENTS)
        for p in d.proposals:
            assert p.p_win is None or 0 <= p.p_win <= 1
            if p.p_win is None:
                assert p.p_source == "inconnue"  # jamais de probabilité inventée


def test_ablation_switches_really_remove_agents():
    full = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV)
    without = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV, enabled=frozenset(AGENTS) - {"risk"})
    ctx, _ = _ctx(open_symbols={"X/EUR", "Y/EUR", "Z/EUR"})
    assert full.decide(ctx)[0].action == ABSTAIN  # veto du risque
    assert all(p.agent != "risk" for p in without.decide(ctx)[0].proposals)


# ---------------------------------------------------------------------------
# Registre : décisions archivées AVANT les issues
# ---------------------------------------------------------------------------


def test_ledger_records_decisions_before_outcomes_and_verifies(tmp_path):
    path = tmp_path / "shadow.jsonl"
    led = ShadowLedger(path)
    council = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV)
    run_council(U, council, EV, WIN, cost=COST, ledger=led)
    assert led.verify() and led.records
    kinds = [r["kind"] for r in led.records]
    assert "decision" in kinds and "outcome" in kinds
    seen = {}
    for r in led.records:
        if r["kind"] == "decision":
            seen[r["hash"]] = r["at"]
            assert "net_r" not in r["payload"] and "outcome" not in r["payload"]
        else:
            assert r["decision"] in seen and r["at"] >= seen[r["decision"]]
    again = ShadowLedger(path)  # relecture du fichier : la chaîne est vérifiée au chargement
    assert len(again.records) == len(led.records)


def test_ledger_refuses_hindsight_and_detects_tampering(tmp_path):
    led = ShadowLedger(tmp_path / "l.jsonl")
    h = led.record_decision(1000, {"action": "ENTER"})
    with pytest.raises(LedgerError):
        led.record_outcome(h, 999, {"net_r": 1.0})  # issue avant la décision
    with pytest.raises(LedgerError):
        led.record_outcome("0" * 64, 2000, {})  # décision inexistante
    with pytest.raises(LedgerError):
        led.record_decision(2000, {"action": "ENTER", "net_r": 3.0})  # une décision ne contient pas son issue
    led.record_outcome(h, 2000, {"net_r": -0.5})
    with pytest.raises(LedgerError):
        led.record_decision(1500, {"action": "ENTER"})  # le temps ne recule pas
    lines = (tmp_path / "l.jsonl").read_text().splitlines()
    (tmp_path / "l.jsonl").write_text("\n".join([lines[0].replace("ENTER", "ABSTAIN"), lines[1]]) + "\n")
    with pytest.raises(LedgerError):
        ShadowLedger(tmp_path / "l.jsonl")


def test_vectorized_entry_exit_estimates_equal_row_by_row_estimates():
    council = build_council(P["train"], P["calibration"], COST, RULES, eval_situations=EV)
    ee = council.entry_exit
    for k in [k for k in range(len(EV)) if not EV.vetoed[k]][:15]:
        one = EV.take(np.isin(np.arange(len(EV)), [k]))
        assert abs(float(ee.model.proba(one)[0]) - float(ee._p[k])) < 1e-12  # arrondi flottant lot / ligne seulement
