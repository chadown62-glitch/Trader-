"""What the calibration selects under the bounded-exploration variant (brain v2 loop, iteration 9).

Report only, for the human review of the exploration floor: with the variant (the floor explores only below
``memecoin_learner.MIN_TRADES`` evaluated lots) and the session's stakes, the calibration keeps the stop and the tiers,
selects a trailing base of 0.30 and quarter Kelly, survives its constraints and has no failure on the 20 robustness
seeds (pre-registered criterion: at most one). A variant label, never a version of the session; nothing is promoted."""

import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


@pytest.fixture(scope="module")
def variant():
    cal = C.MemecoinCalibrator(exploration="bounded")
    return cal, cal.calibrate()


def test_the_variant_selection_is_recorded_coherent_and_not_promoted(variant):
    cal, result = variant
    rec = C.RECORDED_SELECTIONS["variant-bounded-exploration"]
    sel = result["selected"]
    assert result["version"] == "variant-bounded-exploration" and result["exploration"] == "bounded"
    assert sel["policy"] == rec["policy"] and sel["kelly_multiplier"] == rec["kelly_multiplier"] == 0.25 and sel["survives"]
    assert (sel["policy"]["stop_loss"], sel["policy"]["tiers"]) == (ms.CALIBRATED.stop_loss, list(ms.CALIBRATED.tiers))
    dev = rec["development"]
    assert sel["calibration"]["compound_return"]["median"] == pytest.approx(dev["compound_return_median"], rel=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] == pytest.approx(dev["max_drawdown_max"], abs=1e-4)
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(dev["stress_worst_equity_min"], abs=1e-4)
    assert all(c["survives"] for c in result["candidates"])  # under the variant every candidate meets the constraints
    profile = cal.survival_profile(result["_policy"], multipliers=(0.25,))[0.25]
    assert profile["failures"] == rec["robustness"]["failures"] == 0
    assert profile["worst"] == pytest.approx(rec["robustness"]["worst_equity_ratio"], abs=1e-4)
    assert rec["in_session"] is False and ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005
    assert sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
