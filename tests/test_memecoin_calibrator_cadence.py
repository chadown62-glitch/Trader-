"""Does the exploration-floor finding depend on how often signals arrive? (brain v2 loop, iteration 8)

Report only. Under the session's exact stakes, the 50 % session-loss stop is reached on many robustness seeds at
half Kelly for one signal every 20, 60 or 120 minutes (the pre-registered criterion, >= 5 of 20 for each, is met); at
one signal every 5 minutes the fleet's 12-lot limit caps the exposure. The bounded-exploration variant has no failure
from 20 to 120 minutes. Nothing in the session or in the calibrator's defaults changes. Synthetic, offline, paper only."""

import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


@pytest.fixture(scope="module")
def cadences():
    return {(mode, gap): C.MemecoinCalibrator(arrival_gap=gap, exploration=mode).survival_profile(ms.CALIBRATED, multipliers=(0.25, 0.5))
            for mode in C.EXPLORATIONS for gap in C.RECORDED_CADENCE["session"]}


def test_the_survival_profile_returns_plain_numbers():
    profile = C.MemecoinCalibrator().survival_profile(ms.CALIBRATED, multipliers=(0.5,), seeds=(11,))
    assert type(profile[0.5]["failures"]) is int and type(profile[0.5]["worst"]) is float


def test_recorded_cadences_are_reproduced(cadences):
    for (mode, gap), profile in cadences.items():
        assert (profile[0.25]["failures"], profile[0.5]["failures"]) == C.RECORDED_CADENCE[mode][gap], (mode, gap)


def test_the_finding_is_robust_to_the_cadence_by_the_pre_registered_criterion(cadences):
    assert all(cadences[("session", gap)][0.5]["failures"] >= 5 for gap in (20.0, 60.0, 120.0))
    assert cadences[("session", 5.0)][0.5]["failures"] < 5  # the 12-lot limit binds at one signal every 5 minutes
    assert all(cadences[("bounded", gap)][0.5]["failures"] == 0 for gap in (20.0, 60.0, 120.0))
    assert C.MemecoinCalibrator().arrival_gap == C.ARRIVAL_GAP_MIN == 20 and ml.EXPLORATION_FRACTION == 0.005


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
