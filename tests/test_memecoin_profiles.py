"""Five experimental risk profiles of the paper session (campaign guarantee 6, SPEC.md, Sprint 5 extension).

Stable identifiers, explicit limits, versioned proposed values (pre-registered jointly before any trial). A profile only
bounds exposure and stakes: it never lifts the Guardian, the fleet's limits, the M58 filters, the ban on leverage and
short selling, or the causal execution of #91, and no return target is attached to it. MODERATE reproduces the session
limits in force before the profiles. Offline and deterministic."""

import dataclasses
import json

import pytest

from sentinelle.dmh import established_pools, memecoin_simulator as sim, memecoin_strategies as ms
from sentinelle.fleet.agents import RiskCommander
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT, NOW
from tests.test_memecoin_engine import ACCELERATION, causal_session

ORDER = ("SAFE", "MODERATE", "RISK", "ULTRA_RISK", "MAX")


def test_five_stable_profiles_with_explicit_limits_within_the_hard_caps():
    assert tuple(ms.PROFILES) == ORDER and ms.DEFAULT_PROFILE == "MODERATE" and ms.PROFILES_VERSION == "profiles-v1-proposed"
    assert [p.label for p in ms.PROFILES.values()] == ["SAFE", "MODERATE", "RISK", "ULTRA RISK", "MAX"]
    for p in ms.PROFILES.values():
        assert p.max_open <= RiskCommander.MEMECOIN_MAX_OPEN and p.session_loss_stop <= RiskCommander.MEMECOIN_MAX_SESSION_LOSS
        assert p.min_liquidity_usd >= established_pools.MIN_LIQUIDITY_USD
        assert p.max_round_trip_loss <= established_pools.MAX_ROUND_TRIP_LOSS
        assert p.max_exposure + p.cash_reserve <= 1 and p.token_cap <= p.max_exposure  # no leverage, cash never negative
        assert sim.profile_for({"profile": p.id}) is p
    profiles = [ms.PROFILES[i] for i in ORDER]
    for field, rising in (("max_exposure", True), ("token_cap", True), ("cash_reserve", False), ("max_open", True),
                          ("session_loss_stop", True), ("min_liquidity_usd", False), ("max_round_trip_loss", True),
                          ("stake_scale", True)):
        values = [getattr(p, field) for p in profiles]
        assert values == sorted(values, reverse=not rising), field  # monotonic from SAFE to MAX
    moderate = ms.PROFILES["MODERATE"]  # the limits of the session before the profiles
    assert (moderate.token_cap, moderate.cash_reserve, moderate.max_open, moderate.session_loss_stop, moderate.stake_scale) == (
        0.10, 0.25, RiskCommander.MEMECOIN_MAX_OPEN, RiskCommander.MEMECOIN_MAX_SESSION_LOSS, 1.0)


def test_no_return_target_is_attached_to_any_profile():
    fields = {f.name for f in dataclasses.fields(ms.RiskProfile)}
    assert not {f for f in fields if any(w in f for w in ("target", "return", "profit", "yield", "goal", "expected"))}


@pytest.mark.parametrize("bad", [dict(max_exposure=0.9, cash_reserve=0.2), dict(token_cap=0.5, max_exposure=0.3),
                                 dict(max_open=0), dict(session_loss_stop=1.0), dict(stake_scale=0)])
def test_an_inconsistent_profile_is_refused(bad):
    with pytest.raises(ValueError):
        dataclasses.replace(ms.PROFILES["MODERATE"], **bad)


def test_a_profile_beyond_the_fleet_or_the_m58_filter_is_refused(monkeypatch):
    loose = dataclasses.replace(ms.PROFILES["MAX"], id="LOOSE", max_open=RiskCommander.MEMECOIN_MAX_OPEN + 1)
    monkeypatch.setitem(ms.PROFILES, "LOOSE", loose)
    with pytest.raises(ValueError, match="dépasse une limite"):
        sim.profile_for({"profile": "LOOSE"})
    with pytest.raises(ValueError, match="profil inconnu"):
        sim.profile_for({"profile": "YOLO"})


def run(db, profile=None):
    feed, clock, step = causal_session(db)
    if profile:
        sim.select_profile(db, profile)
    step(0)
    step(60, **ACCELERATION)
    return step(60, priceUsd="1.02"), sim.load(db)[0]


def test_a_session_without_profile_is_unchanged_and_moderate_is_that_session(tmp_path):
    from sentinelle.storage.db import Database
    default, moderate = Database(tmp_path / "a.sqlite"), Database(tmp_path / "b.sqlite")
    _, a = run(default)
    _, b = run(moderate, "MODERATE")
    strip = lambda fills: [{k: v for k, v in f.items() if k not in ("order_id",)} for f in fills]
    assert strip(a["fills"]) == strip(b["fills"]) and a["cash"] == b["cash"]
    assert a["positions"]["momentum/" + MINT]["sizing"] == b["positions"]["momentum/" + MINT]["sizing"]
    assert a["positions"]["momentum/" + MINT]["profile"] == "MODERATE" and "profile" not in a and b["profile"] == "MODERATE"


def test_stakes_grow_from_safe_to_max_within_each_cap(tmp_path):
    from sentinelle.storage.db import Database
    sizes = {}
    for profile in ORDER:
        db = Database(tmp_path / f"{profile}.sqlite")
        report, state = run(db, profile)
        pos = state["positions"].get("momentum/" + MINT)
        if pos is None:  # SAFE refuses the 50,000 USD feed pool (its floor is 50,000 USD: admitted) or the round trip
            sizes[profile] = 0.0
            continue
        p = ms.PROFILES[profile]
        assert pos["profile"] == profile and pos["sizing"]["size_usd"] <= p.token_cap * 30.0 + 1e-9
        sizes[profile] = pos["sizing"]["size_usd"]
        assert report["profile"]["id"] == profile and report["profile"]["version"] == ms.PROFILES_VERSION
    values = [sizes[p] for p in ORDER]
    assert values == sorted(values) and values[0] < values[-1]


def test_safe_refuses_entries_its_stricter_limits_exclude(db):
    feed, clock, step = causal_session(db)
    sim.select_profile(db, "SAFE")
    feed.loss = 0.08  # the Jupiter round trip of the scanner: within M58 (10 %), beyond SAFE (5 %)
    step(0)
    state = step(60, **ACCELERATION)
    assert state["pending_orders"] == [] and state["positions"] == {}  # no order: the profile refused before the fleet
    traced = sim.journal(db)[-1]["evaluations"][MINT]  # the refusal is traced in the causal journal
    assert traced["signals"] == ["momentum"] and traced["profile_refusal"].startswith("profil SAFE : aller-retour Jupiter")
    reason = sim.profile_refusal({"profile": "SAFE", "positions": {}, "cash": 30.0, "initial": 30.0},
                                 {"round_trip_loss": 0.08}, {"liquidity_usd": 60_000.0})
    assert reason == "profil SAFE : aller-retour Jupiter non mesuré ou au-delà de 5 %"
    thin = sim.profile_refusal({"profile": "SAFE", "positions": {}, "cash": 30.0, "initial": 30.0},
                               {"round_trip_loss": 0.02}, {"liquidity_usd": 49_000.0})
    assert thin == "profil SAFE : liquidité sous 50 000 $"
    lots = {f"momentum/L{i}": {"mint": f"L{i}", "mark": 1.0} for i in range(4)}
    full = sim.profile_refusal({"profile": "SAFE", "positions": lots, "cash": 26.0, "initial": 30.0},
                               {"round_trip_loss": 0.02}, {"liquidity_usd": 60_000.0})
    assert full == "profil SAFE : 4 lots ouverts au plus"
    lost = sim.profile_refusal({"profile": "SAFE", "positions": {}, "cash": 27.0, "initial": 30.0},
                               {"round_trip_loss": 0.02}, {"liquidity_usd": 60_000.0})
    assert lost == "profil SAFE : perte de session au-delà de 10 %"
    assert sim.profile_refusal({"profile": "MODERATE", "positions": lots, "cash": 10.0, "initial": 30.0},
                               {"round_trip_loss": None}, {"liquidity_usd": 20_000.0}) is None  # fleet and Guardian decide


def test_a_pending_entry_is_cancelled_when_the_profile_refuses_it_at_execution(db):
    feed, clock, step = causal_session(db)
    sim.select_profile(db, "SAFE")
    step(0)
    step(60, **ACCELERATION)
    [(oid, order)] = sim.load(db)[0]["orders"].items()
    state = step(60, priceUsd="1.02", liquidity={"usd": 45_000})  # the pool lost depth below SAFE's 50,000 USD floor
    assert state["positions"] == {}
    [resolved] = [r for r in sim.load(db)[0]["order_log"] if r["id"] == oid]
    assert resolved["status"] == "cancelled" and resolved["reason"] == "profil SAFE : liquidité sous 50 000 $"


def test_the_profile_of_a_session_that_traded_never_changes(db):
    run(db, "RISK")
    with pytest.raises(ValueError, match="ne change pas"):
        sim.select_profile(db, "MAX")
    sim.select_profile(db, "RISK")  # the same profile: accepted, nothing changes
    with pytest.raises(ValueError, match="profil inconnu"):
        sim.select_profile(db, "YOLO")
    assert sim.load(db)[0]["profile"] == "RISK" and json.loads(db.get_meta(sim.KEY))["profiles_version"] == ms.PROFILES_VERSION


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
