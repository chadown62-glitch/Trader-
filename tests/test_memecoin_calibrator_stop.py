"""Selling the open lots at the session-loss stop, measured in the calibrator only (brain v2 loop, iteration 13).

The fleet's 50 % session-loss stop only refuses new entries; lots open when it triggers keep running, so the worst
equity falls below the stop (0.40 with independent signals, 0.34 in waves at half Kelly). The report-only variant sells
every open lot whole on the next observation once the stop is reached. It lifts the worst equity by +0.042 and +0.130:
the pre-registered criterion (+0.05 in both orders) is not met, NOT DEMONSTRATED. The fleet and the session are not
changed. Synthetic, offline, paper only."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_strategies as ms
from sentinelle.fleet.agents import RiskCommander
from tests import test_configurable_capital as frozen


def lots_falling(n, steps=40):
    paths = C.Paths(np.zeros(n, int), np.zeros(n, int), np.ones((n, steps)), np.full((n, steps), 1e6), np.full((n, steps), 0.002),
                    np.ones(n))
    ratio = np.tile(np.linspace(1.0, 0.1, steps), (n, 1))  # every lot loses steadily, held to the horizon
    lots = C.LotPaths(np.zeros((n, steps)), np.ones((n, steps)), np.zeros((n, steps), int), ratio)
    return paths, lots, np.full(n, ratio[0, -1] - 1), np.full(n, steps - 1)


def test_the_variant_sells_every_open_lot_on_the_next_observation(monkeypatch):
    monkeypatch.setattr(C, "session_stake", lambda **kw: 3.0)
    paths, lots, returns, closes = lots_falling(10)
    off = C._portfolio_session(paths, returns, multiplier=0.5, close_steps=closes, arrival_gap=1.0, lots=lots)
    on = C._portfolio_session(paths, returns, multiplier=0.5, close_steps=closes, arrival_gap=1.0, lots=lots, stop_liquidation=True)
    assert on["trades"] == off["trades"] == 10  # the 10 lots opened before the stop could trigger
    assert on["final_equity"] > off["final_equity"] and on["worst_equity_ratio"] > off["worst_equity_ratio"]


def test_by_default_nothing_changes():
    assert C.MemecoinCalibrator().stop_liquidation is False
    p = C.generate(14, mixes=C.RUG_STRESS)
    r, _, steps, lots = C.lot_paths(p, ms.CALIBRATED)
    a = C._portfolio_session(p, r, multiplier=0.5, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN, lots=lots)
    b = C._portfolio_session(p, r, multiplier=0.5, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN, lots=lots, stop_liquidation=False)
    assert a == b


@pytest.fixture(scope="module")
def measured():
    out = {}
    for order, block in (("independent", None), ("waves", C.CLUSTER_BLOCK)):
        for rule, liquidation in (("session", False), ("liquidation", True)):
            out[(order, rule)] = C.MemecoinCalibrator(cluster_block=block, stop_liquidation=liquidation).survival_profile(
                ms.CALIBRATED, multipliers=(0.25, 0.5))
    return out


def test_recorded_worst_equities_are_reproduced_and_the_criterion_is_not_met(measured):
    for (order, rule), profile in measured.items():
        quarter, half = C.RECORDED_STOP[order][rule]
        assert profile[0.25]["worst"] == pytest.approx(quarter, abs=1e-4) and profile[0.5]["worst"] == pytest.approx(half, abs=1e-4)
    gains = {order: measured[(order, "liquidation")][0.5]["worst"] - measured[(order, "session")][0.5]["worst"]
             for order in ("independent", "waves")}
    assert gains["waves"] >= 0.05 > gains["independent"] > 0  # not met in both orders: NOT DEMONSTRATED
    assert all(measured[(o, "liquidation")][0.5]["failures"] == measured[(o, "session")][0.5]["failures"] for o in ("independent", "waves"))
    assert RiskCommander.MEMECOIN_MAX_SESSION_LOSS == 0.50


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
