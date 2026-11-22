"""Overlapping lots in the calibrator's portfolio (brain v2 loop, iteration 3).

The paper session holds up to 12 lots at once and its learner only sees closed trades. The sequential portfolio of
calibration-v1 to v3 realized each trade before sizing the next one, so the Kelly selection used outcomes that were not
known yet. The overlapping portfolio realizes a trade at its closing step only, keeps open lots at their cost, bounds
the stake by the free cash and the reserve, and refuses a signal met with 12 open lots. calibration-v4-overlap is
recorded, not promoted. Synthetic regimes, offline, deterministic, paper only."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_simulator as sim, memecoin_strategies as ms
from sentinelle.fleet.agents import RiskCommander
from tests import test_configurable_capital as frozen


def flat_paths(n, steps=5, liquidity=1e6):
    return C.Paths(np.zeros(n, int), np.zeros(n, int), np.ones((n, steps)), np.full((n, steps), liquidity),
                   np.full((n, steps), 0.002), np.ones(n))


def test_the_constants_come_from_the_fleet_and_the_horizon():
    assert C.MAX_OPEN_LOTS == RiskCommander.MEMECOIN_MAX_OPEN == 12
    assert C.ARRIVAL_GAP_MIN == C.STEPS / 12 == 20 and C.MemecoinCalibrator().arrival_gap == 20


def test_an_open_trade_is_neither_seen_by_the_learner_nor_counted_in_the_equity():
    p = flat_paths(2)
    returns, closes = np.array([-0.5, 0.2]), np.array([200, 10])
    overlap = C.portfolio(p, returns, multiplier=0.5, close_steps=closes, arrival_gap=20.0)
    s0 = C._stake(p, 0, "sniper", C.INITIAL, [], 0.5, False, True)
    s1 = C._stake(p, 1, "sniper", C.INITIAL, [], 0.5, False, True)  # trade 0 still open at minute 20: nothing known
    assert overlap["final_equity"] == pytest.approx(C.INITIAL + s0 * -0.5 + s1 * 0.2)
    sequential = C.portfolio(p, returns, multiplier=0.5)
    known = [{"strategy": "sniper", "return": -0.5, "asset_domain": "memecoin"}]
    s1_seq = C._stake(p, 1, "sniper", C.INITIAL + s0 * -0.5, known, 0.5, False, True)  # the former look-ahead
    assert sequential["final_equity"] == pytest.approx(C.INITIAL + s0 * -0.5 + s1_seq * 0.2)
    assert s1 != pytest.approx(s1_seq)


def test_a_signal_met_with_twelve_open_lots_is_not_taken():
    n = 13
    run = C.portfolio(flat_paths(n), np.full(n, 0.1), multiplier=0.5, close_steps=np.full(n, 240), arrival_gap=1.0)
    assert (run["trades"], run["refused_open_lots"]) == (12, 1)
    run = C.portfolio(flat_paths(n), np.full(n, 0.1), multiplier=0.5, close_steps=np.full(n, 10), arrival_gap=1.0)
    assert (run["trades"], run["refused_open_lots"]) == (13, 0)  # lots closed from minute 10 on free their slots


def test_open_stakes_never_spend_the_reserve(monkeypatch):
    wins, longs = 20, 12  # twenty quick winners teach the learner to bet the 10 % cap, then twelve lots stay open together
    p = flat_paths(wins + longs)
    returns = np.r_[np.full(wins, 1.0), np.full(longs, 0.1)]
    closes = np.r_[np.full(wins, 2), np.full(longs, 240)]
    equity = C.portfolio(flat_paths(wins), returns[:wins], multiplier=0.5, close_steps=closes[:wins], arrival_gap=20.0)["final_equity"]
    stakes = []
    push = C.heapq.heappush
    monkeypatch.setattr(C.heapq, "heappush", lambda heap, item: (stakes.append(item[2]) if item[1] >= wins else None, push(heap, item)))
    C.portfolio(p, returns, multiplier=0.5, close_steps=closes, arrival_gap=20.0)
    assert max(stakes) == pytest.approx(0.10 * equity)  # the cap per lot, reached
    assert sum(stakes) <= equity - 0.25 * C.INITIAL + 1e-9 < longs * 0.10 * equity  # the reserve binds, never spent


@pytest.mark.parametrize("seed,mixes", [(11, C.MIXES), (12, C.RUG_STRESS), (101, C.NO_EXPLOSIVE)])
def test_without_overlap_the_new_portfolio_is_exactly_the_sequential_one(seed, mixes):
    p = C.generate(seed, mixes=mixes)
    r, _, steps = C.replay(p, ms.CALIBRATED, with_steps=True)
    for m in (0.25, 0.5):
        a = C.portfolio(p, r, multiplier=m)
        b = C.portfolio(p, r, multiplier=m, close_steps=steps, arrival_gap=float(C.STEPS))
        assert b.pop("refused_open_lots") == 0 and a == b


@pytest.mark.parametrize("seed,mixes", [(9, C.MIXES), (21, C.RUG_STRESS)])
def test_vectorised_and_scalar_closing_steps_agree(seed, mixes):
    p = C.generate(seed, mixes=mixes, paths_per_strategy=25)
    r, reasons, steps = C.replay(p, ms.CALIBRATED, with_steps=True)
    assert np.array_equal(r, C.replay(p, ms.CALIBRATED)[0], equal_nan=True)  # asking for the steps changes nothing
    for i in range(len(r)):
        ri, events = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
        if math.isnan(ri):
            assert reasons[i] == C.NO_ENTRY
            continue
        assert C.close_step(events, p.price.shape[1]) == steps[i], i
        assert 2 <= steps[i] <= C.STEPS


def test_the_former_settings_stay_reproducible():
    assert C.calibration_version("immediate", None, None) == "calibration-v1"
    assert C.calibration_version("causal", None, None) == "calibration-v2-causal"
    assert C.calibration_version("causal", 0.10, None) == "calibration-v3-causal-drift"
    assert C.calibration_version("causal", 0.10, 20.0) == "calibration-v4-overlap"
    with pytest.raises(ValueError):
        C.MemecoinCalibrator(execution="immediate")  # an overlapping portfolio needs the causal closing steps
    with pytest.raises(ValueError):
        C.replay(C.generate(1, paths_per_strategy=1), ms.CALIBRATED, execution="immediate", with_steps=True)


@pytest.fixture(scope="module")
def v4():
    cal = C.MemecoinCalibrator()
    return cal.calibrate()


def test_without_look_ahead_the_selection_keeps_the_exits_but_halves_kelly_and_promotes_nothing(v4):
    rec, v1 = C.RECORDED_SELECTIONS["calibration-v4-overlap"], C.RECORDED_SELECTIONS["calibration-v1"]
    sel = v4["selected"]
    assert v4["version"] == "calibration-v4-overlap" and v4["arrival_gap"] == 20
    assert sel["policy"] == rec["policy"] == v1["policy"] == C._policy_dict(ms.CALIBRATED)
    assert sel["kelly_multiplier"] == rec["kelly_multiplier"] == 0.25 != ms.KELLY_MULTIPLIER and sel["survives"]
    dev = rec["development"]
    assert sel["calibration"]["compound_return"]["median"] == pytest.approx(dev["compound_return_median"], rel=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] == pytest.approx(dev["max_drawdown_max"], abs=1e-4)
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(dev["stress_worst_equity_min"], abs=1e-4)
    # half Kelly, the session's setting, breaks the pre-registered survival constraint once trades overlap
    [half] = [c for c in v4["candidates"] if c["policy"] == v1["policy"] and c["kelly_multiplier"] == 0.5]
    assert not half["survives"] and half["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(0.2931, abs=1e-3)
    assert half["rug_stress"]["worst_equity_ratio"]["min"] < C.MIN_STRESS_EQUITY
    # not promoted: the session keeps half Kelly until a human validates a change
    assert ms.KELLY_MULTIPLIER == 0.5 and rec["in_session"] is False and sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
