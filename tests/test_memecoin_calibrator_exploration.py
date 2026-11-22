"""A bounded exploration floor, measured in the calibrator only (brain v2 loop, iteration 7).

The paper session's exploration floor keeps betting 0.5 % of equity on every signal of a strategy whatever its record;
under its exact stakes the session reaches its 50 % loss stop on 14 robustness seeds out of 20 (iteration 6). The
report-only variant "bounded" applies the floor only while a strategy has fewer than ``memecoin_learner.MIN_TRADES``
evaluated lots (no new parameter). It meets the criterion fixed before the measurement (at most one failure, positive
compound median on the normal mix, a mix without explosive runs no worse): a candidate for human review. Nothing in the
session or in ``memecoin_learner`` changes. Synthetic regimes, offline, deterministic, paper only."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def stake(explore, fraction=0.0):
    return C.session_stake(equity=30.0, known=30.0, cash=30.0, strategy_used=0.0, exposed=0.0, fraction=fraction, weight=0.25,
                           multiplier=1.0, network=0.002, explore=explore)


def test_without_the_floor_only_the_kelly_target_remains():
    assert stake(True) == pytest.approx(ml.exploration_floor(30.0, 1.0)) == pytest.approx(0.15)  # Kelly 0: the floor bets
    assert stake(False) == 0.0  # bounded: nothing once the sample exists
    assert stake(True, 0.04) == stake(False, 0.04) == pytest.approx(30.0 * 0.04 * 0.25)  # above the floor: identical


def test_below_the_minimal_sample_the_variant_is_the_session_rule():
    n = ml.MIN_TRADES - 1  # one strategy, never 8 evaluated lots
    paths = C.Paths(np.zeros(n, int), np.zeros(n, int), np.ones((n, 6)), np.full((n, 6), 1e6), np.full((n, 6), 0.002), np.ones(n))
    lots = C.LotPaths(np.zeros((n, 6)), np.ones((n, 6)), np.zeros((n, 6), int), np.full((n, 6), 0.5))
    runs = [C._portfolio_session(paths, np.full(n, -0.5), multiplier=0.5, close_steps=np.full(n, 3), arrival_gap=20.0, lots=lots,
                                 exploration=mode) for mode in C.EXPLORATIONS]
    assert runs[0] == runs[1] and runs[0]["trades"] == n


def test_the_default_keeps_the_exact_session_rule_and_the_variant_is_never_a_recorded_version():
    assert C.MemecoinCalibrator().exploration == "session" and C.EXPLORATIONS == ("session", "bounded")
    with pytest.raises(ValueError):
        C.MemecoinCalibrator(exploration="none")
    small = {"stop_loss": (0.20,), "cover_ratio": (1.25,), "cover_fraction": (0.20,), "tiers": ((3.0, 6.0, 10.0),),
             "trail_base": (0.25,), "trail_slope": (0.15,)}
    result = C.MemecoinCalibrator(grid=small, kelly_grid=(0.5,), calibration_seeds=(1,), paths_per_strategy=10, top_exits=1,
                                  exploration="bounded").calibrate()
    assert result["version"] == "variant-bounded-exploration" and result["exploration"] == "bounded"


@pytest.fixture(scope="module")
def measured():
    out = {}
    for mode in C.EXPLORATIONS:
        cal = C.MemecoinCalibrator(exploration=mode)
        dev = cal._paths(cal.calibration_seeds)
        out[mode] = {"survival": cal.survival_profile(ms.CALIBRATED),
                     "dev": C._summary([cal._portfolio(p, cal._replayed(p, ms.CALIBRATED), 0.5) for p in dev]),
                     "holdout": cal.compare(ms.CALIBRATED, ms.KELLY_MULTIPLIER)}
    return out


def test_the_bounded_floor_meets_the_pre_registered_criterion(measured):
    session, bounded = measured["session"], measured["bounded"]
    failing = {m: tuple(r["seed"] for r in v["seeds"] if r["worst_equity_ratio"] < C.MIN_STRESS_EQUITY)
               for m, v in bounded["survival"].items()}
    assert failing == C.RECORDED_SURVIVAL["variant-bounded-exploration"]
    assert bounded["survival"][0.5]["failures"] <= 1 < session["survival"][0.5]["failures"] == 14
    assert bounded["survival"][0.5]["worst"] == pytest.approx(0.5208, abs=1e-3)
    assert bounded["dev"]["compound_return"]["median"] == pytest.approx(20.7365, rel=1e-3)
    assert bounded["dev"]["compound_return"]["median"] > 0
    flat_b, flat_s = bounded["holdout"]["no_explosive"]["after"], session["holdout"]["no_explosive"]["after"]
    assert flat_b["compound_return"]["median"] == pytest.approx(0.1452, abs=1e-3)
    assert flat_b["compound_return"]["median"] > flat_s["compound_return"]["median"]
    assert flat_b["profit_factor"]["median"] > 1.0 > flat_s["profit_factor"]["median"]


def test_the_price_of_the_bound_stays_visible_and_nothing_changes_in_the_session(measured):
    session, bounded = measured["session"], measured["bounded"]
    assert bounded["dev"]["trades"] < session["dev"]["trades"] / 2  # far fewer lots
    assert bounded["dev"]["compound_return"]["median"] < session["dev"]["compound_return"]["median"] / 10  # far less upside
    assert ml.EXPLORATION_FRACTION == 0.005 and ml.MIN_TRADES == 8 and ms.KELLY_MULTIPLIER == 0.5
    assert sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
