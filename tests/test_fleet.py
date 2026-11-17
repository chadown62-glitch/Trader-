"""Mission 57 — flotte de 12 agents et coordinateur local (données SYNTHÉTIQUES, aucun réseau, aucun processus permanent)."""
import re
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.shadow.protocol import read_journal
from sentinelle.fleet import AUTO_PROMOTION, AUTO_RETRAIN, CAN_TRADE, agents as A, coordinator as K
from tests.test_memecoin import grid, pool, sec

ROOT = Path(__file__).resolve().parent.parent
T0 = 1_794_900_000_000


def candidate(signal=True, **kw):
    px = np.r_[np.full(70, 1.0), np.linspace(1.0, 1.2, 10)] if signal else np.full(80, 1.0)
    g = grid(px)
    if signal:
        g[70:80, 5] = 5000.0
    data = {"pool": pool(), "security": sec(), "grid": g.tolist(), "observed_ms": T0, "notional_eur": 10.0} | kw
    return A.Event("memecoin_candidate", "P", T0 + 60_000, data)


CTX = {"portfolio": {"equity_eur": 30.0, "cash_eur": 30.0, "open_positions": 0, "daily_loss_eur": 0.0}, "sol_usd": 72.0}


def test_the_fleet_cannot_trade_retrain_or_promote_and_imports_no_execution_path():
    assert CAN_TRADE is False and AUTO_RETRAIN is False and AUTO_PROMOTION is False
    for path in (ROOT / "sentinelle" / "fleet").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"from \.\.(execution|services|api|agent)\b|PaperBroker|LiveBroker|\.fit\(|promote|champion|sendTransaction", text), path
    names = [a.name for a in A.fleet()]
    assert len(names) == 12 == len(set(names)) and all(a.reuses and a.tasks for a in A.fleet())


def test_a_clean_token_with_a_signal_is_only_a_simulated_proposal_for_the_independent_veto(tmp_path):
    k = K.Coordinator(journal=tmp_path / "j.jsonl", clock=lambda: T0 / 1000)
    r = k.decide(candidate(), CTX)
    assert r["decision"] == K.PROPOSAL and r["requires_independent_veto"] is True
    assert {f["agent"] for f in r["findings"]} >= {"Alpha Hunter", "Rug Pull Guardian", "Cost Router", "Risk Commander"}
    assert [ln["kind"] for ln in read_journal(tmp_path / "j.jsonl")] == ["fleet_decision"]


def test_the_guardian_veto_wins_over_a_signal_and_the_disagreement_is_kept():
    r = K.Coordinator().decide(candidate(security=sec(round_trip={"sell_unavailable": True})), CTX)
    assert r["decision"] == K.VETOED and r["disagreements"] and r["disagreements"][0]["against"] == "Rug Pull Guardian"
    assert r["contribution"]["Rug Pull Guardian"] is True  # sans lui, la décision changerait


def test_no_signal_means_wait_and_a_small_order_is_refused_by_risk():
    assert K.Coordinator().decide(candidate(signal=False), CTX)["decision"] == K.WAIT
    r = K.Coordinator().decide(candidate(notional_eur=5.0), CTX)
    assert r["decision"] == K.REFUSED and any("minimum" in w for w in r["why"])


def test_an_exit_is_proposed_on_collapse_even_when_risk_would_refuse_new_trades():
    ev = A.Event("position", "P", T0, {"entry_price": 1.0, "price": 0.05, "minutes": 3, "strategy": "momentum_15", "observed_ms": T0})
    r = K.Coordinator().decide(ev, CTX | {"portfolio": {"daily_loss_eur": 99.0}})
    assert r["decision"] == K.EXIT and "effondrement" in r["why"][0]
    hold = A.Event("position", "P", T0, {"entry_price": 1.0, "price": 1.02, "minutes": 3, "strategy": "momentum_15", "observed_ms": T0})
    assert K.Coordinator().decide(hold, CTX)["decision"] == K.HOLD


def test_stale_data_or_a_broken_journal_makes_the_fleet_abstain(tmp_path):
    stale = candidate(observed_ms=T0 - 3_600_000)
    assert K.Coordinator().decide(stale, CTX)["decision"] == K.ABSTENTION
    j = tmp_path / "broken.jsonl"
    j.write_text('{"seq": 0, "prev": "x", "sha": "y", "kind": "k", "body": {}, "at_ms": 1}\n', encoding="utf-8")
    r = K.Coordinator().decide(candidate(), CTX | {"journal_path": j})
    assert r["decision"] == K.ABSTENTION and any("journal" in w for w in r["why"])


def test_heavy_analysis_only_when_relevant_and_within_the_model_budget():
    k = K.Coordinator()
    agents, skipped = k.plan(candidate(), CTX)
    assert "News & Event Analyst" not in [a.name for a in agents] and any("lourde non justifiée" in s["why"] for s in skipped)
    news = candidate(headlines=["Exploit drains the pool"])
    assert "News & Event Analyst" in [a.name for a in k.plan(news, CTX)[0]]
    r = k.decide(news, CTX)
    assert r["decision"] == K.WAIT and any("dépêche à risque" in w for w in r["why"])
    tight = K.Coordinator(budget=K.Budget(max_models=0))
    assert any("modèles simultanés" in s["why"] for s in tight.plan(news, CTX)[1])


class Broken(A.Agent):
    name, role, reuses, tasks = "Broken", "test", ("rien",), frozenset({"memecoin_candidate"})

    def run(self, ev, ctx):
        raise RuntimeError("panne")


def test_a_failing_agent_is_quarantined_and_never_brings_the_fleet_down():
    k = K.Coordinator(A.fleet() + [Broken()])
    for _ in range(4):
        r = k.decide(candidate(), CTX)
    assert k.stats["Broken"].quarantined and k.stats["Broken"].failures == 3
    assert any(s["agent"] == "Broken" and s["why"] == "quarantaine" for s in r["skipped"])
    k.enable("Broken")
    assert not k.stats["Broken"].quarantined


def test_useless_non_essential_agents_are_disabled_for_the_session_and_essentials_never_are():
    k = K.Coordinator(disable_after_runs=5)
    for _ in range(6):
        k.decide(candidate(), CTX)
    st = k.stats
    assert st["Portfolio Optimizer"].disabled and "jamais décisif" in st["Portfolio Optimizer"].disabled_reason
    assert not any(st[n].disabled for n in K.ESSENTIAL)
    rep = k.report()
    assert rep["events"] == 6 and rep["agents"]["Alpha Hunter"]["pivotal"] >= 1 and rep["agents"]["Alpha Hunter"]["cpu_ms"] > 0


def test_the_cpu_budget_skips_only_non_essential_agents():
    k = K.Coordinator(budget=K.Budget(cpu_ms_per_event=0.0))
    r = k.decide(candidate(), CTX)
    ran = {f["agent"] for f in r["findings"]}
    assert ran >= {"System Doctor", "Rug Pull Guardian", "Risk Commander"} and any("budget de calcul" in s["why"] for s in r["skipped"])


def test_portfolio_optimizer_flags_a_risk_size_that_the_minimum_order_makes_impossible():
    ev = A.Event("cex_order", "BTC", T0, {"strategy_drawdown": 0.38, "lines": 2, "notional_eur": 10.0, "observed_ms": T0,
                                         "depth_usd": 1e6, "route_cost_bps": 40.0})
    f = A.PortfolioOptimizer().run(ev, CTX)
    assert f.stance == A.OPPOSE and f.evidence["capital_needed"] == pytest.approx(76.0, abs=0.1)  # même constat que la mission 55
