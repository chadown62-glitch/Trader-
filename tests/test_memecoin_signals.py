"""Fewer false signals and bad entries, with parameters fixed before any result (SPEC.md, campaign parameters).

- Momentum compares two observations of the same pool at most ``MOMENTUM_MAX_GAP_S`` (600 s) apart, strictly later.
- A decided entry is cancelled when the price moved by more than ``MAX_ENTRY_DRIFT`` (10 %) before its fill; exits are
  never blocked by it. Offline and deterministic."""

import pytest

from sentinelle.dmh import memecoin_simulator as sim
from sentinelle.dmh import memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import Feed, MINT, NOW
from tests.test_memecoin_engine import ACCELERATION, asset, causal_session, entered

KEY = "momentum/" + MINT


def momentum_on(gap):
    feed = Feed()
    feed.pair["priceUsd"] = "1"
    a = asset()
    point = sim.quote(feed.client, a, NOW)
    point.update(volume_usd_per_min_m5=100.0, buy_sell_count_ratio_m5=3.0)
    a.update(peak=1.0, peak_liquidity=point["liquidity_usd"],
             last={**point, "ts": NOW - gap, "volume_usd_per_min_m5": 30.0, "buy_sell_count_ratio_m5": 1.3})
    signals, guard = ms.evaluate(feed.client, a, point, False)
    assert guard["allowed"]
    return "momentum" in signals


@pytest.mark.parametrize("gap,fires", [(0, False), (-30, False), (1, True), (60, True), (600, True), (601, False), (6 * 3600, False)])
def test_momentum_only_compares_adjacent_observations(gap, fires):
    assert (ms.MOMENTUM_MIN_GAP_S, ms.MOMENTUM_MAX_GAP_S) == (1, 600) == (1, ms.SNIPER_MAX_GAP_S)
    assert momentum_on(gap) is fires


@pytest.mark.parametrize("gap,decided", [(60, True), (ms.MOMENTUM_MAX_GAP_S + 100, False)])
def test_a_pool_revisited_too_late_gets_no_momentum_decision(db, gap, decided):
    feed, clock, step = causal_session(db)
    step(0)
    state = step(gap, **ACCELERATION)
    assert [o["key"] for o in state["pending_orders"] if o["kind"] == "buy"] == ([KEY] if decided else [])


@pytest.mark.parametrize("price,filled", [("1.09", True), ("1.11", False), ("0.91", True), ("0.89", False)])
def test_an_entry_whose_price_drifted_beyond_ten_percent_is_cancelled(db, price, filled):
    feed, clock, step = causal_session(db)
    step(0)
    step(60, **ACCELERATION)  # decided at 1.00
    [(oid, order)] = sim.load(db)[0]["orders"].items()
    state = step(60, priceUsd=price)
    assert (KEY in state["positions"]) is filled
    if not filled:
        [resolved] = [r for r in sim.load(db)[0]["order_log"] if r["id"] == oid]
        assert resolved["status"] == "cancelled" and resolved["reason"].startswith("dérive de prix depuis la décision")
        assert "limite ±10 %" in resolved["reason"] and oid not in sim.load(db)[0]["orders"]
    assert sim.MAX_ENTRY_DRIFT == 0.10


def test_a_protective_exit_is_never_blocked_by_the_drift_guard(db):
    feed, clock, step, order = entered(db)
    step(60, priceUsd="0.7")  # stop decided
    state = step(60, priceUsd="0.3")  # -57 % since the decision: the exit still fills
    assert KEY not in state["positions"] and sim.load(db)[0]["completed"][0]["exit"] == "stop_loss"
    dips = [r for r in sim.load(db)[0]["order_log"] if r["key"].startswith("dip_hunter/")]
    assert dips and all(r["status"] == "cancelled" and "dérive de prix" in r["reason"] for r in dips)  # no falling knife


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
