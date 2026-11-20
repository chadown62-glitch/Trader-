"""Flotte de 12 agents (M57) intégrée à la session memecoin (M58) — données synthétiques, aucun réseau, aucun service."""
import re
from pathlib import Path

import pytest

from sentinelle.alpha.shadow.protocol import read_journal
from sentinelle.dmh import memecoin_simulator as sim
from sentinelle.fleet import AUTO_PROMOTION, AUTO_RETRAIN, CAN_TRADE, agents as A, coordinator as K

ROOT = Path(__file__).resolve().parent.parent
T0 = 1_800_000_000_000
CLEAN = {"allowed": True, "vetoes": []}


def candidate(**kw):
    data = {"asset_domain": "memecoin", "observed_ms": T0, "price": 1.0, "signals": ["momentum"], "guardian": CLEAN,
            "round_trip_loss": 0.02, "liquidity_usd": 50_000.0, "notional_usd": 3.0, "impact": 3.0 / 25_003.0,
            "round_trip_cost": 0.008, "open_positions": 0, "equity_usd": 30.0, "initial_usd": 30.0} | kw
    return A.Event("memecoin_candidate", "MINT", T0 + 1_000, data)


def test_the_fleet_cannot_trade_retrain_or_promote_and_imports_no_execution_path():
    assert CAN_TRADE is False and AUTO_RETRAIN is False and AUTO_PROMOTION is False
    for path in (ROOT / "sentinelle" / "fleet").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"from \.\.(execution|services|api|agent)\b|PaperBroker|LiveBroker|\.fit\(|promote|champion|sendTransaction"
                             r"|client\.(get|rpc)\(", text), path
    names = [a.name for a in A.fleet()]
    assert len(names) == 12 == len(set(names)) and all(a.reuses and a.tasks for a in A.fleet())


def test_a_clean_candidate_with_a_signal_is_only_a_simulated_proposal(tmp_path):
    k = K.Coordinator(journal=tmp_path / "j.jsonl", clock=lambda: T0 / 1000)
    r = k.decide(candidate(), {})
    assert r["decision"] == K.PROPOSAL and r["requires_independent_veto"] is True
    assert {f["agent"] for f in r["findings"]} >= {"Alpha Hunter", "Rug Pull Guardian", "Cost Router", "Risk Commander", "Liquidity Scout"}
    assert [ln["kind"] for ln in read_journal(tmp_path / "j.jsonl")] == ["fleet_decision"]


@pytest.mark.parametrize(("change", "decision", "fragment"), [
    ({"guardian": {"allowed": False, "vetoes": ["Mutable metadata"]}}, K.VETOED, "Mutable metadata"),
    ({"round_trip_loss": 0.25}, K.VETOED, "aller-retour 25.0%"),
    ({"round_trip_loss": None}, K.WAIT, "revendabilité non confirmée"),
    ({"signals": []}, K.WAIT, "aucun signal"),
    ({"impact": 0.05}, K.WAIT, "impact estimé"),
    ({"round_trip_cost": 0.15}, K.WAIT, "aller-retour estimé"),
    ({"notional_usd": 0.0}, K.REFUSED, "aucune taille"),
    ({"open_positions": 12}, K.REFUSED, "12 positions"),
    ({"equity_usd": 14.0}, K.REFUSED, "perte de session"),
    ({"observed_ms": T0 - 3_600_000}, K.ABSTENTION, "âge"),
])
def test_veto_risk_alerts_and_objections_always_win_over_a_signal(change, decision, fragment):
    r = K.Coordinator().decide(candidate(**change), {})
    assert r["decision"] == decision and any(fragment in w for w in r["why"]), r["why"]
    if decision == K.VETOED:
        assert r["disagreements"][0]["against"] == "Rug Pull Guardian" and r["contribution"]["Rug Pull Guardian"] is True


def test_exits_follow_the_calibrated_policy_and_are_never_blocked_by_risk():
    def position(**d):
        return A.Event("position", "MINT", T0, {"observed_ms": T0, "entry_price": 1.0, **d})

    ctx = {"portfolio": {"daily_loss_eur": 99.0}}  # le risque refuserait toute entrée nouvelle
    assert K.Coordinator().decide(position(price=0.05, ratio=0.05), ctx)["decision"] == K.EXIT
    stop = K.Coordinator().decide(position(price=0.79, ratio=0.79), ctx)
    assert stop["decision"] == K.EXIT and "stop_loss" in stop["why"][0]  # stop à −20 % de la valeur nette
    assert K.Coordinator().decide(position(price=1.1, ratio=1.1), ctx)["decision"] == K.HOLD
    assert "take_profit" in K.Coordinator().decide(position(price=1.3, ratio=1.3), ctx)["why"][0]  # 20 % vendus à 1,25×


def test_a_broken_journal_makes_the_fleet_abstain(tmp_path):
    j = tmp_path / "broken.jsonl"
    j.write_text('{"seq": 0, "prev": "x", "sha": "y", "kind": "k", "body": {}, "at_ms": 1}\n', encoding="utf-8")
    r = K.Coordinator().decide(candidate(), {"journal_path": j, "verify_each_event": True})
    assert r["decision"] == K.ABSTENTION and any("journal" in w for w in r["why"])
    assert K.Coordinator().decide(A.Event("health", "système", T0, {}), {"journal_path": j})["decision"] == K.ABSTENTION


def test_heavy_analysis_only_when_relevant_and_within_the_model_budget():
    k = K.Coordinator()
    agents, skipped = k.plan(candidate(), {})
    assert "News & Event Analyst" not in [a.name for a in agents] and any("lourde non justifiée" in s["why"] for s in skipped)
    news = candidate(headlines=["Exploit drains the pool"])
    assert "News & Event Analyst" in [a.name for a in k.plan(news, {})[0]]
    r = k.decide(news, {})
    assert r["decision"] == K.WAIT and any("dépêche à risque" in w for w in r["why"])
    assert any("modèles simultanés" in s["why"] for s in K.Coordinator(budget=K.Budget(max_models=0)).plan(news, {})[1])


class Broken(A.Agent):
    name, role, reuses, tasks = "Broken", "test", ("rien",), frozenset({"memecoin_candidate"})

    def run(self, ev, ctx):
        raise RuntimeError("panne")


def test_a_failing_agent_is_quarantined_and_never_brings_the_fleet_down():
    k = K.Coordinator(A.fleet() + [Broken()])
    for _ in range(4):
        r = k.decide(candidate(), {})
    assert k.stats["Broken"].quarantined and k.stats["Broken"].failures == 3
    assert any(s["agent"] == "Broken" and s["why"] == "quarantaine" for s in r["skipped"])
    k.enable("Broken")
    assert not k.stats["Broken"].quarantined


def test_useless_costly_agents_are_disabled_for_the_session_essentials_never_and_budgets_use_wall_time():
    k = K.Coordinator(disable_after_runs=5, costly_ms=0.0)  # tout agent compte comme coûteux pour ce test
    for _ in range(6):
        k.decide(candidate(), {})
    st = k.stats
    assert st["Portfolio Optimizer"].disabled and "jamais décisif" in st["Portfolio Optimizer"].disabled_reason
    assert not any(st[n].disabled for n in K.ESSENTIAL)
    cheap = K.Coordinator(disable_after_runs=5)
    for _ in range(6):
        cheap.decide(candidate(), {})
    assert not cheap.stats["Portfolio Optimizer"].disabled
    rep = k.report()
    assert rep["events"] == 6 and rep["agents"]["Alpha Hunter"]["pivotal"] >= 1 and rep["agents"]["Alpha Hunter"]["wall_ms"] > 0
    r = K.Coordinator(budget=K.Budget(ms_per_event=0.0)).decide(candidate(), {})
    assert {f["agent"] for f in r["findings"]} >= {"System Doctor", "Rug Pull Guardian", "Risk Commander"}
    assert any("budget de calcul" in s["why"] for s in r["skipped"])


def test_portfolio_optimizer_flags_a_risk_size_that_the_minimum_order_makes_impossible():
    ev = A.Event("cex_order", "BTC", T0, {"strategy_drawdown": 0.38, "lines": 2, "notional_eur": 10.0, "observed_ms": T0})
    f = A.PortfolioOptimizer().run(ev, {"portfolio": {"equity_eur": 30.0}})
    assert f.stance == A.OPPOSE and f.evidence["capital_needed"] == pytest.approx(76.0, abs=0.1)  # même constat que M55


def test_the_session_enters_only_on_a_fleet_proposal_and_keeps_its_reasons(db):
    state, _ = sim.load(db)
    point = {"price": 1.0, "liquidity_usd": 50_000.0, "ts": T0 / 1000, "volume_usd_per_min_m5": 50.0, "buy_sell_count_ratio_m5": 2.0}
    k = K.Coordinator(clock=lambda: T0 / 1000)
    state["assets"]["MINT"] = asset = {"symbol": "MINT", "round_trip_loss": None}
    assert sim.consult_fleet(k, state, "MINT", asset, point, ["momentum"], CLEAN, 0.001, T0 / 1000) == []
    assert state["fleet"][-1]["decision"] == K.WAIT and state["fleet"][-1]["asset_domain"] == "memecoin"
    asset["round_trip_loss"] = 0.02
    assert sim.consult_fleet(k, state, "MINT", asset, point, ["momentum"], CLEAN, 0.001, T0 / 1000) == ["momentum"]
    assert state["fleet"][-1]["decision"] == K.PROPOSAL and state["fleet"][-1]["pivotal"] == ["Alpha Hunter"]
    sim.process(state, "MINT", point, ["momentum"], 0.001)
    assert len(state["positions"]) == 1 and sim.report(state, {})["fleet"][-1]["decision"] == K.PROPOSAL
    for _ in range(sim.FLEET_LOG + 5):
        sim.consult_fleet(k, state, "MINT", asset, point, [], CLEAN, 0.001, T0 / 1000)
    assert len(state["fleet"]) == sim.FLEET_LOG
    assert db.scalar("SELECT COUNT(*) FROM orders") == db.scalar("SELECT COUNT(*) FROM fills") == 0
