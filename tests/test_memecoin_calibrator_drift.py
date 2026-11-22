"""The calibrator replays the paper session's entry drift guard (brain v2 loop, iteration 2).

Since #97 the session cancels a decided entry whose price moved by more than ``MAX_ENTRY_DRIFT`` (10 %) between the
decision and the fill. The causal calibrator applies the same rule, with the same constant, between the signal (step 0)
and the fill (step 1): no trade. calibration-v1 and calibration-v2-causal stay reproducible; the calibration under the
session's own contract is recorded as calibration-v3-causal-drift and selects the shipped policy again. Synthetic
regimes, offline, deterministic, paper only."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

DEEP = 1e12


def path(step1, rest=1.0, n=6):
    return np.array([1.0, step1] + [rest] * (n - 2))


def scalar(price, **kw):
    price = np.asarray(price, float)
    return C.trade_return(price, np.full(len(price), DEEP), np.zeros(len(price)), C.REFERENCE_SIZE, **kw)


def vector(price, **kw):
    price = np.asarray(price, float)
    p = C.Paths(np.array([0]), np.array([0]), price[None, :], np.full((1, len(price)), DEEP), np.zeros((1, len(price))),
                np.array([1.0]))
    return C.replay(p, ms.CALIBRATED, **kw)


def test_the_calibrator_uses_the_session_constant():
    assert C.SESSION_ENTRY_DRIFT is sim.MAX_ENTRY_DRIFT and sim.MAX_ENTRY_DRIFT == 0.10
    assert C.MemecoinCalibrator().entry_drift == sim.MAX_ENTRY_DRIFT


@pytest.mark.parametrize("step1,traded", [(1.09, True), (0.91, True), (1.11, False), (0.89, False), (1.10, False),
                                          (0.90, True), (1.0999, True), (0.8999, False)])
def test_an_entry_that_drifted_beyond_ten_percent_is_not_a_trade(step1, traded):
    # exactly the session's rule: cancelled when abs(fill price / decided price - 1) > MAX_ENTRY_DRIFT
    assert traded is not (abs(step1 / 1.0 - 1) > sim.MAX_ENTRY_DRIFT)
    r, reasons = vector(path(step1))
    rs, events = scalar(path(step1))
    assert math.isnan(r[0]) is (not traded) and math.isnan(rs) is (not traded)
    if not traded:
        assert reasons[0] == C.NO_ENTRY and events == []
    else:
        assert r[0] == pytest.approx(rs, abs=1e-12)


def test_the_guard_is_absent_from_the_former_contracts():
    r, _ = vector(path(1.30), entry_drift=None)  # calibration-v2-causal: no guard
    assert math.isfinite(r[0])
    r, _ = vector(path(1.30), execution="immediate")  # calibration-v1: filled on the signal itself, no drift
    assert math.isfinite(r[0])
    assert math.isfinite(scalar(path(1.30), entry_drift=None)[0]) and math.isfinite(scalar(path(1.30), execution="immediate")[0])


def test_an_unknown_decision_price_cancels_nothing():
    price = np.array([np.nan, 1.30, 1.30, 1.30, 1.30])  # like the session: no finite decided price, no drift check
    assert math.isfinite(vector(price)[0][0]) and math.isfinite(scalar(price)[0])


@pytest.mark.parametrize("seed,mixes", [(9, C.MIXES), (21, C.RUG_STRESS), (11, C.MIXES)])
def test_vectorised_and_scalar_replays_agree_with_the_guard(seed, mixes):
    p = C.generate(seed, mixes=mixes, paths_per_strategy=40)
    r, reasons = C.replay(p, ms.CALIBRATED)
    for i in range(len(r)):
        ri, _ = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
        if math.isnan(ri):
            assert math.isnan(r[i]) and reasons[i] == C.NO_ENTRY, i
        else:
            assert r[i] == pytest.approx(ri, abs=1e-9), i
    drifted = np.abs(p.price[:, 1] / p.price[:, 0] - 1) > sim.MAX_ENTRY_DRIFT
    assert np.array_equal(reasons == C.NO_ENTRY, drifted)


# -- calibration under the session's own contract ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def v3():
    cal = C.MemecoinCalibrator()
    return cal, cal.calibrate(), cal.compare(ms.CALIBRATED, ms.KELLY_MULTIPLIER)


def test_the_guard_cancels_mostly_dumped_confluences_on_the_development_seeds(v3):
    cal, _, _ = v3
    cancelled = {name: 0 for name in C.REGIMES}
    for p in cal._paths(cal.calibration_seeds):
        _, reasons = C.replay(p, ms.CALIBRATED)
        for k, name in enumerate(C.REGIMES):
            cancelled[name] += int((reasons[p.regime == k] == C.NO_ENTRY).sum())
    assert cancelled == {"masked_rug": 0, "slippage_spike": 0, "false_breakout": 8, "healthy_dip": 0, "smart_confluence": 32,
                         "explosive": 10, "slow_bleed": 0}  # 50 of 1440 entries


def test_the_session_contract_selects_the_shipped_policy_again_and_promotes_nothing(v3):
    _, result, _ = v3
    v1, v2, rec = (C.RECORDED_SELECTIONS[k] for k in ("calibration-v1", "calibration-v2-causal", "calibration-v3-causal-drift"))
    sel = result["selected"]
    assert (result["execution"], result["entry_drift"], result["version"]) == ("causal", 0.10, "calibration-v3-causal-drift")
    assert sel["policy"] == rec["policy"] == v1["policy"] == C._policy_dict(ms.CALIBRATED) != v2["policy"]
    assert sel["kelly_multiplier"] == rec["kelly_multiplier"] == ms.KELLY_MULTIPLIER and sel["survives"]
    dev = rec["development"]
    assert sel["calibration"]["compound_return"]["median"] == pytest.approx(dev["compound_return_median"], rel=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] == pytest.approx(dev["max_drawdown_max"], abs=1e-4)
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(dev["stress_worst_equity_min"], abs=1e-4)
    assert (v1["in_session"], v2["in_session"], rec["in_session"]) == (True, False, False) and rec["same_policy_as"] == "calibration-v1"
    assert sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED and sim.CALIBRATED_EXITS == "calibrated-v1"
    assert C.calibration_version("immediate", 0.10) == "calibration-v1" and C.calibration_version("causal", None) == "calibration-v2-causal"


def test_held_out_report_keeps_the_mixed_effect_of_the_guard_visible(v3):
    _, _, cmp = v3
    stress, flat = cmp["rug_stress"]["after"], cmp["no_explosive"]["after"]
    assert stress["worst_equity_ratio"]["min"] >= 0.5
    assert stress["max_drawdown"]["max"] == pytest.approx(0.5128, abs=1e-3)  # beyond the 0.5 bound of v1, as without the guard
    # without explosive runs the guard costs: max drawdown 0.24 -> 0.35, profit factor 1.23 -> 1.08 (v2 -> v3)
    assert flat["max_drawdown"]["max"] == pytest.approx(0.3472, abs=1e-3)
    assert flat["profit_factor"]["median"] == pytest.approx(1.0779, abs=1e-3)
    assert flat["profit_factor"]["median"] > 1.0
    for label in ("holdout", "no_explosive"):
        assert cmp[label]["after"]["compound_return"]["median"] > cmp[label]["before"]["compound_return"]["median"], label


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
