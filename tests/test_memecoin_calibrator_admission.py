"""The M58 admission of the paper session applied to the calibrator's synthetic signals (brain v2 loop, iteration 16).

Report only. Since M58 the session admits only established pools (``established_pools.MIN_LIQUIDITY_USD`` = 20,000 USD,
``MAX_ROUND_TRIP_LOSS`` = 10 %); the synthetic sniper pools (about 8,000 USD) predate it, so every synthetic sniper
signal would be refused. With the admission, the exploration-floor finding holds only when adverse regimes come in waves
(half Kelly: 11 of 20 seeds reach the loss stop, none with independent signals); the bounded variant has none. The
material-gap criterion fixed before the measurement is met: the next calibration version should apply the admission
(human decision). The default (no admission) and every recorded version are unchanged. Synthetic, offline, paper only."""

import numpy as np
import pytest

from sentinelle.dmh import established_pools, memecoin_calibrator as C, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def signal(depth0, depth1, ratio1):
    paths = C.Paths(np.zeros(1, int), np.zeros(1, int), np.ones((1, 4)), np.array([[depth0, depth1, 1e6, 1e6]]), np.zeros((1, 4)),
                    np.ones(1))
    return paths, C.LotPaths(np.zeros((1, 4)), np.ones((1, 4)), np.zeros((1, 4), int), np.full((1, 4), ratio1))


@pytest.mark.parametrize("depth0,depth1,ratio1,ok", [(20_000.0, 20_000.0, 0.90, True), (19_999.0, 1e6, 0.97, False),
                                                     (1e6, 19_999.0, 0.97, False), (1e6, 1e6, 0.899, False)])
def test_the_admission_uses_the_session_filters_at_the_signal_and_at_the_fill(depth0, depth1, ratio1, ok):
    assert (established_pools.MIN_LIQUIDITY_USD, established_pools.MAX_ROUND_TRIP_LOSS) == (20_000.0, 0.10)
    assert C.admitted(*signal(depth0, depth1, ratio1), 0) is ok


def test_every_synthetic_sniper_signal_is_below_the_m58_filters_and_nothing_changes_by_default():
    cal = C.MemecoinCalibrator()
    assert cal.m58_admission is False
    counts = {name: [0, 0] for name in ms.NAMES}
    for p in cal._paths(cal.calibration_seeds):
        r, _, _, lots = C.lot_paths(p, ms.CALIBRATED)
        for i in np.flatnonzero(np.isfinite(r)):
            name = ms.NAMES[int(p.strategy[i])]
            counts[name][1] += 1
            counts[name][0] += not C.admitted(p, lots, i)
    assert {k: tuple(v) for k, v in counts.items()} == C.RECORDED_ADMISSION["refused"]
    assert C.LIQUIDITY0["sniper"] < established_pools.MIN_LIQUIDITY_USD  # the documented origin of the gap


@pytest.fixture(scope="module")
def measured():
    return {(order, mode): C.MemecoinCalibrator(m58_admission=True, exploration=mode, cluster_block=block).survival_profile(
                ms.CALIBRATED, multipliers=(0.25, 0.5))
            for order, block in (("independent", None), ("waves", C.CLUSTER_BLOCK)) for mode in C.EXPLORATIONS}


def test_recorded_admission_results_are_reproduced_and_the_gap_is_material(measured):
    for (order, mode), profile in measured.items():
        assert (profile[0.25]["failures"], profile[0.5]["failures"]) == C.RECORDED_ADMISSION[order][mode], (order, mode)
    without = C.RECORDED_SURVIVAL["calibration-v5-session-sizing"][0.50]
    assert len(without) - measured[("independent", "session")][0.5]["failures"] >= 3  # material: 14 -> 0
    assert measured[("waves", "session")][0.5]["failures"] >= 5  # the floor still matters when adverse regimes cluster


def test_the_selection_under_the_admission_is_recorded_coherent_and_not_promoted():
    cal = C.MemecoinCalibrator(m58_admission=True)
    result = cal.calibrate()
    rec, sel = C.RECORDED_SELECTIONS["candidate-m58-admission"], result["selected"]
    assert result["version"] == "unrecorded-m58-admission"
    assert sel["policy"] == rec["policy"] and sel["kelly_multiplier"] == rec["kelly_multiplier"] == 0.5 and sel["survives"]
    dev = rec["development"]
    assert sel["calibration"]["compound_return"]["median"] == pytest.approx(dev["compound_return_median"], rel=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] == pytest.approx(dev["max_drawdown_max"], abs=1e-4)
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(dev["stress_worst_equity_min"], abs=1e-4)
    for order, block in (("independent", None), ("waves", C.CLUSTER_BLOCK)):
        v = C.MemecoinCalibrator(m58_admission=True, cluster_block=block).survival_profile(result["_policy"], multipliers=(0.5,))[0.5]
        assert (v["failures"], round(v["worst"], 4)) == rec["robustness"][order], order
    assert rec["in_session"] is False and ms.KELLY_MULTIPLIER == 0.5 and C.MemecoinCalibrator().m58_admission is False


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
