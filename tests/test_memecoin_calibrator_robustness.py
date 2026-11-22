"""Robustness of the rug-stress survival constraint over 20 synthetic seeds (brain v2 loop, iteration 4).

Report only: ``survival_profile`` selects nothing and changes no parameter. Under the contract faithful to the paper
session (causal fills, entry drift guard, overlapping lots), half Kelly falls under the survival constraint on 3 seeds
out of 20 (15 %, one of them at 0.4998) and quarter Kelly on none; the former sequential portfolio hid every failure.
The pre-registered threshold for recommending a quarter Kelly (>= 20 % failures at half Kelly, < 5 % at quarter) is not
met: NOT DEMONSTRATED, nothing promoted. Synthetic regimes, offline, deterministic, paper only."""

import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def failing(profile):
    return {m: tuple(r["seed"] for r in v["seeds"] if r["worst_equity_ratio"] < C.MIN_STRESS_EQUITY) for m, v in profile.items()}


def test_robustness_seeds_are_disjoint_from_selection_and_held_out_seeds():
    assert C.ROBUSTNESS_SEEDS == tuple(range(14, 34)) and len(C.ROBUSTNESS_SEEDS) == 20
    assert not set(C.ROBUSTNESS_SEEDS) & (set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS))


def test_the_profile_is_the_portfolio_seed_by_seed():
    cal = C.MemecoinCalibrator()
    profile = cal.survival_profile(ms.CALIBRATED, multipliers=(0.25, 0.5), seeds=(11, 12, 13))
    for m in (0.25, 0.5):
        for row in profile[m]["seeds"]:
            p = C.generate(row["seed"], mixes=C.RUG_STRESS)
            r, _, steps = C.replay(p, ms.CALIBRATED, with_steps=True)
            run = C.portfolio(p, r, multiplier=m, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN)
            assert (row["worst_equity_ratio"], row["max_drawdown"]) == (run["worst_equity_ratio"], run["max_drawdown"])
    assert [round(r["worst_equity_ratio"], 4) for r in profile[0.5]["seeds"]] == [0.5928, 0.2931, 0.9701]  # iteration 3


@pytest.mark.parametrize("version,gap", [("calibration-v3-causal-drift", None), ("calibration-v4-overlap", C.ARRIVAL_GAP_MIN)])
def test_recorded_survival_is_reproduced(version, gap):
    profile = C.MemecoinCalibrator(arrival_gap=gap).survival_profile(ms.CALIBRATED)
    assert failing(profile) == C.RECORDED_SURVIVAL[version]


def test_the_quarter_kelly_recommendation_is_not_demonstrated_and_nothing_is_promoted():
    profile = C.MemecoinCalibrator().survival_profile(ms.CALIBRATED)
    half, quarter = profile[0.5]["failure_rate"], profile[0.25]["failure_rate"]
    assert (half, quarter) == (0.15, 0.0)
    assert not (half >= 0.20 and quarter < 0.05)  # the pre-registered criterion is not met
    assert profile[0.5]["worst"] == pytest.approx(0.2836, abs=1e-3) and profile[0.25]["worst"] == pytest.approx(0.5981, abs=1e-3)
    sequential = C.MemecoinCalibrator(arrival_gap=None).survival_profile(ms.CALIBRATED)
    assert all(v["failures"] == 0 for v in sequential.values())  # the former look-ahead hid every failure
    assert ms.KELLY_MULTIPLIER == 0.5 and sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
