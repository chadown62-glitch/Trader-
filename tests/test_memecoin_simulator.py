"""Paper memecoin simulator: time exit of stagnant lots (D9), opening times and the exploration floor at sizing."""

import pytest

from sentinelle.dmh import memecoin_learner as learner, memecoin_simulator as sim
from tests import test_configurable_capital as frozen

H = 3600
POINT = {"price": 1.0, "liquidity_usd": 1_000_000.0, "volume_h1_usd": 10_000.0}  # net ratio ~0.99: no stop, no slice


def lot(**extra):
    return {"mint": "T", "strategy": "momentum", "qty": 1.0, "basis": 1.0, "initial": 1.0, "mark": 1.0, "high": 1.0,
            "stage": 0, "pnl": 0.0, "exit_policy": sim.CALIBRATED_EXITS, "asset_domain": "memecoin", "opened_at": 0, **extra}


def paper(position, **extra):
    return {"revision": 0, "cash": 99.0, "initial": 100.0, "assets": {"T": {"symbol": "T"}}, "positions": {"momentum/T": position},
            "fills": [], "completed": [], "curve": [], "errors": [], "exit_policy": sim.CALIBRATED_EXITS, **extra}


def mark(state, ts, price=1.0):
    sim.process(state, "T", dict(POINT, price=price, ts=ts), [], 0.001)


def test_a_stagnant_lot_is_closed_after_24_hours_and_frees_its_budget():
    state = paper(lot())
    mark(state, 24 * H - 1)
    assert "momentum/T" in state["positions"] and not state["fills"]  # one second short: still held
    cash = state["cash"]
    mark(state, 24 * H)
    assert "momentum/T" not in state["positions"] and state["cash"] > cash
    [fill] = state["fills"]
    assert (fill["side"], fill["detail"], fill["reason"], fill["asset_domain"], fill["simulated"]) == (
        "sell", "time_exit", "time_exit", "memecoin", True)
    [closed] = state["completed"]
    assert (closed["exit"], closed["opened_at"], closed["closed_at"]) == ("time_exit", 0, 24 * H)
    assert closed["return"] == pytest.approx(fill["pnl"]) and closed["return"] < 0  # exit costs only, no market move
    assert sim.MAX_HOLD_S == 24 * H


def test_a_lot_that_sold_a_slice_is_left_to_its_floor_and_trailing_stop():
    state = paper(lot(stage=1, high=1.1))
    mark(state, 48 * H, price=1.1)  # above the break-even floor, under the next tier, far from the trailing stop
    assert "momentum/T" in state["positions"] and not state["completed"]


def test_the_stop_keeps_priority_over_the_time_exit():
    state = paper(lot())
    mark(state, 30 * H, price=0.5)
    assert state["completed"][0]["exit"] == "stop_loss" and state["fills"][0]["detail"] == "stop_loss"


@pytest.mark.parametrize("limit,closed_at", [(H, H), (6 * H, 6 * H), (0, None), (-5, None), ("24h", None)])
def test_the_maximum_holding_time_is_configurable_per_session(limit, closed_at):
    state = paper(lot(), max_hold_s=limit)
    for ts in (H, 6 * H, 48 * H):
        mark(state, ts)
    if closed_at is None:  # zero, negative or not a number: time exit disabled
        assert "momentum/T" in state["positions"]
    else:
        assert state["completed"][0]["closed_at"] == closed_at


def test_lots_opened_before_opening_times_use_their_latest_buy_fill():
    old = {k: v for k, v in lot().items() if k != "opened_at"}
    state = paper(old)
    state["fills"].append({"asset_domain": "memecoin", "simulated": True, "ts": 1000, "strategy": "momentum", "mint": "T",
                           "side": "buy", "pnl": 0.0})
    assert sim.opened_at(state, "momentum/T", old) == 1000
    mark(state, 1000 + 24 * H - 1)
    assert "momentum/T" in state["positions"]
    mark(state, 1000 + 24 * H)
    assert state["completed"][0]["exit"] == "time_exit" and "opened_at" not in state["completed"][0]


def test_a_new_lot_records_its_opening_time_and_its_exploration_floor():
    state = paper(lot())
    del state["positions"]["momentum/T"]
    state["cash"] = 100.0
    sim.process(state, "T", dict(POINT, ts=500), ["momentum"], 0.001)
    pos = state["positions"]["momentum/T"]
    assert pos["opened_at"] == 500 and pos["sizing"]["scoring"] == learner.SCORING
    assert pos["sizing"]["exploration_floor_usd"] == pytest.approx(learner.exploration_floor(100.0, pos["sizing"]["multiplier"]))
    assert pos["initial"] >= pos["sizing"]["exploration_floor_usd"]


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
