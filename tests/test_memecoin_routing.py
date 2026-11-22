"""Multi-strategy routing: one decision per market event, replayed on the journal (experimental, INACTIVE).

Reproduced on main c87da1a: a burst that triggers the sniper also triggers momentum (the sniper's rules imply
momentum's), so one market event opens two lots, decided and filled at the same second and sold at the same moments by
the same exit policy: the stake on the event is doubled and the learner receives two identical samples. Changing that
would change the active sizing, which needs an independent evaluation first. The router of ``memecoin_strategies.route``
was fixed before any real flow signal existed (one lot per event, labelled by the first signal in ``NAMES``, families
kept as confirmations, the engine's refusals never lifted, no size or threshold of its own); ``audit_routing`` replays it
on the journal and measures the double counting. The session never calls it. Offline and deterministic."""

import pytest

from sentinelle.dmh import memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT, NOW
from tests.test_memecoin_engine import ACCELERATION, causal_session, entered

BURST = {"priceUsd": "1.05", "volume": {"m5": 2000, "h1": 12_000}, "txns": {"m5": {"buys": 12, "sells": 3}}}


@pytest.mark.parametrize(("signals", "refusal", "expected"), [
    ([], None, ("abstain", None, [], "aucun signal")),
    (["momentum", "sniper"], None, ("enter", "sniper", ["flow"], None)),  # one family, one lot, the specific label
    (["dip_hunter", "momentum"], None, ("enter", "momentum", ["flow", "price"], None)),  # two confirmations
    (["whale_tail"], None, ("enter", "whale_tail", ["wallets"], None)),
    (["momentum", "whale_tail"], "régime de marché", ("abstain", None, ["flow", "wallets"], "régime de marché")),
])
def test_the_pre_registered_router_rules(signals, refusal, expected):
    routed = ms.route(signals, refusal)
    assert (routed["decision"], routed["strategy"], routed["families"], routed["reason"]) == expected
    assert routed["confirmations"] == len(expected[2])
    assert set(ms.FAMILIES) == set(ms.NAMES)


def burst(db):
    feed, clock, step = causal_session(db)
    feed.pair.update(volume={"m5": 500, "h1": 12_000}, txns={"m5": {"buys": 6, "sells": 3}})
    step(0)
    step(60, **BURST)  # one market event: sniper and momentum together
    step(60, priceUsd="1.06")  # both lots filled on the same later quote
    for price in ("1.1", "1.3", "1.5", "1.2", "0.9", "0.8"):  # 1.2 is 80 % of the high: a dip, a second event
        step(60, priceUsd=price)
    return sim.load(db)[0]


def test_a_sniper_burst_is_one_event_with_two_lots_and_identical_outcomes(db):
    state = burst(db)
    first = sim.journal(db)[1]["evaluations"][MINT]
    assert first["signals"] == ["momentum", "sniper"]
    buys = [f for f in state["fills"] if f["side"] == "buy"]
    assert [(f["strategy"], f["decided_at"], f["ts"]) for f in buys[:2]] == [("momentum", NOW + 60, NOW + 120),
                                                                            ("sniper", NOW + 60, NOW + 120)]
    sells = {(f["ts"], f["reason"]) for f in state["fills"] if f["side"] == "sell" and f["strategy"] == "momentum"}
    assert sells == {(f["ts"], f["reason"]) for f in state["fills"] if f["side"] == "sell" and f["strategy"] == "sniper"}
    closed = {t["strategy"]: t["return"] for t in state["completed"]}
    assert closed["momentum"] == pytest.approx(closed["sniper"], abs=1e-4)  # two samples, one outcome
    audit = sim.audit_routing(sim.journal(db))
    assert {k: audit[k] for k in ("events", "engine_lots", "router_lots", "extra_lots", "multi_strategy_events",
                                  "router_without_engine_lot", "pairs", "abstentions")} == {
        "events": 2, "engine_lots": 3, "router_lots": 2, "extra_lots": 1, "multi_strategy_events": 1,
        "router_without_engine_lot": 0, "pairs": {"momentum+sniper": 1}, "abstentions": {}}
    [same] = audit["same_event_pnl"]
    assert same["decided_at"] == NOW + 60 and set(same["lots"]) == {"momentum", "sniper"}
    assert same["lots"]["momentum"] == pytest.approx(same["lots"]["sniper"], rel=0.01)


def test_a_single_strategy_event_counts_no_extra_lot(db):
    entered(db)
    audit = sim.audit_routing(sim.journal(db))
    assert (audit["events"], audit["engine_lots"], audit["router_lots"], audit["extra_lots"]) == (1, 1, 1, 0)
    assert audit["pairs"] == {} and audit["same_event_pnl"] == []


def test_the_router_abstains_with_the_engines_own_reason_when_the_gate_is_closed(db, monkeypatch):
    monkeypatch.setattr(sim, "market_regime", lambda client: {"status": "risk_off", "sol_change_h1_pct": -6.0})
    feed, clock, step = causal_session(db)
    step(0)
    step(60, **ACCELERATION)
    row = sim.journal(db)[-1]
    assert row["evaluations"][MINT]["signals"] == ["momentum"] and row["decided"] == []  # the engine abstained
    audit = sim.audit_routing(sim.journal(db))
    assert (audit["events"], audit["engine_lots"], audit["router_lots"]) == (1, 0, 0)
    assert audit["abstentions"] == {row["valuation"]["entry_gate"]["reason"]: 1}
    assert "régime de marché" in row["valuation"]["entry_gate"]["reason"]


def test_the_router_abstains_on_a_profile_refusal(db):
    sim.select_profile(db, "SAFE")
    feed, clock, step = causal_session(db)
    feed.pair["liquidity"] = {"usd": 40_000}  # admitted by M58 (20 000 $), under SAFE's 50 000 $
    step(0)
    step(60, **ACCELERATION)
    row = sim.journal(db)[-1]
    assert row["decided"] == [] and row["evaluations"][MINT]["profile_refusal"] == "profil SAFE : liquidité sous 50 000 $"
    assert sim.audit_routing(sim.journal(db))["abstentions"] == {"profil SAFE : liquidité sous 50 000 $": 1}


def test_the_router_is_inactive_the_session_never_calls_it(db, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("le routeur expérimental ne décide rien")

    monkeypatch.setattr(ms, "route", forbidden)
    monkeypatch.setattr(sim, "route", forbidden)
    state = burst(db)  # the whole session runs, the router untouched
    decided = {o.split(":buy:", 1)[1].split("/", 1)[0] for r in sim.journal(db) for o in r["decided"] if ":buy:" in o}
    assert decided == {"momentum", "sniper", "dip_hunter"} and len(state["completed"]) == 2
    assert ms.KELLY_MULTIPLIER == 0.5 and sorted(ms.PROFILES) == ["MAX", "MODERATE", "RISK", "SAFE", "ULTRA_RISK"]


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
