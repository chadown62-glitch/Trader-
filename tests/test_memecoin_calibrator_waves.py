"""Adverse regimes in waves (brain v2 loop, iteration 11).

Report only. The calibrator draws each signal's regime independently. ``in_waves`` keeps the same signals and regime
proportions but groups them in runs of ``CLUSTER_BLOCK`` (12, the fleet's lot limit) signals of one regime. Under the
session's exact stakes, waves aggravate the exploration-floor finding (half Kelly: 14 -> 19 failures of 20); the
bounded-exploration variant keeps at most one failure. Defaults (independent signals) and every recorded version are
unchanged; nothing is promoted. Synthetic, offline, paper only."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

FIELDS = ("strategy", "regime", "price", "liquidity", "network", "confidence")


def test_waves_only_reorder_whole_signals():
    p = C.generate(14, mixes=C.RUG_STRESS)
    w = C.in_waves(p, C.CLUSTER_BLOCK, 14)
    assert C.CLUSTER_BLOCK == C.MAX_OPEN_LOTS == 12
    keys = lambda q: sorted((int(q.regime[i]), int(q.strategy[i]), float(q.price[i, -1]), float(q.confidence[i])) for i in range(len(q.regime)))
    assert keys(p) == keys(w)  # the same signals, moved whole
    assert int((np.diff(w.regime) != 0).sum()) < int((np.diff(p.regime) != 0).sum()) / 5  # far fewer regime switches
    again = C.in_waves(p, C.CLUSTER_BLOCK, 14)
    assert all(np.array_equal(getattr(w, f), getattr(again, f)) for f in FIELDS)  # deterministic
    runs = np.split(w.regime, np.flatnonzero(np.diff(w.regime)) + 1)
    assert max(len(r) for r in runs) >= C.CLUSTER_BLOCK


def test_independent_signals_stay_the_default_and_waves_are_never_a_recorded_version():
    assert C.MemecoinCalibrator().cluster_block is None
    small = {"stop_loss": (0.20,), "cover_ratio": (1.25,), "cover_fraction": (0.20,), "tiers": ((3.0, 6.0, 10.0),),
             "trail_base": (0.25,), "trail_slope": (0.15,)}
    result = C.MemecoinCalibrator(grid=small, kelly_grid=(0.5,), calibration_seeds=(1,), paths_per_strategy=10, top_exits=1,
                                  cluster_block=C.CLUSTER_BLOCK).calibrate()
    assert result["version"] == "unrecorded-waves"


@pytest.fixture(scope="module")
def waves():
    return {mode: C.MemecoinCalibrator(exploration=mode, cluster_block=C.CLUSTER_BLOCK).survival_profile(ms.CALIBRATED, multipliers=(0.25, 0.5))
            for mode in C.EXPLORATIONS}


def test_recorded_waves_are_reproduced_and_the_criteria_are_met(waves):
    for mode, profile in waves.items():
        failing = {m: tuple(r["seed"] for r in v["seeds"] if r["worst_equity_ratio"] < C.MIN_STRESS_EQUITY) for m, v in profile.items()}
        assert failing == C.RECORDED_WAVES[mode], mode
    independent = C.RECORDED_SURVIVAL["calibration-v5-session-sizing"][0.50]
    assert waves["session"][0.5]["failures"] - len(independent) >= 3  # waves aggravate: 14 -> 19
    assert waves["bounded"][0.5]["failures"] <= 1  # the bounded variant stays robust
    assert ml.EXPLORATION_FRACTION == 0.005 and ms.KELLY_MULTIPLIER == 0.5


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
