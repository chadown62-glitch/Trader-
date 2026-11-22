"""Cost robustness of the shipped exit policy (brain v2 loop, iteration 5).

Report only: ``cost_profile`` selects nothing and changes no parameter. The default ``Costs`` are the module's
assumptions, so every recorded version is unchanged. Under the contract faithful to the paper session, the policy keeps
a positive compound median and meets the rug-stress survival constraint at quarter Kelly under each pre-registered
stress (DEX fee x2, minimum impact x2, network fee x5, all combined); half Kelly, the session's setting, misses it in
every scenario. Synthetic regimes, offline, deterministic, paper only."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

STRESSES = ("dex_fee_x2", "min_impact_x2", "network_x5", "combined")


def test_the_scenarios_are_the_pre_registered_ones_and_nominal_is_the_module_assumption():
    assert C.NOMINAL == C.Costs(C.DEX_FEE, C.MIN_IMPACT, 1.0) == C.COST_SCENARIOS["nominal"]
    assert C.COST_SCENARIOS["dex_fee_x2"].dex_fee == 0.006 and C.COST_SCENARIOS["min_impact_x2"].min_impact == 0.02
    assert C.COST_SCENARIOS["network_x5"].network_mult == 5.0
    assert C.COST_SCENARIOS["combined"] == C.Costs(0.006, 0.02, 5.0)


def test_nominal_costs_change_nothing():
    p = C.generate(9, paths_per_strategy=10)
    assert C.with_costs(p) is p
    for execution in C.EXECUTIONS:
        assert np.array_equal(C.ratios(p, execution=execution), C.ratios(p, execution=execution, costs=C.NOMINAL), equal_nan=True)
    gross = (1.5 - 0.002) / (1 + C.DEX_FEE)
    assert C.entry(1.5, 1e9, 0.002) == pytest.approx(gross / (1 + C.MIN_IMPACT))  # the module assumptions, as before


@pytest.mark.parametrize("label", STRESSES)
def test_higher_costs_never_give_a_better_net_ratio(label):
    p = C.generate(21, mixes=C.RUG_STRESS, paths_per_strategy=20)
    costs = C.COST_SCENARIOS[label]
    nominal, stressed = C.ratios(p), C.ratios(C.with_costs(p, costs), costs=costs)
    both = np.isfinite(nominal) & np.isfinite(stressed)
    assert both.any() and (stressed[both] <= nominal[both] + 1e-12).all()


@pytest.mark.parametrize("label", STRESSES)
def test_vectorised_and_scalar_replays_agree_under_stressed_costs(label):
    costs = C.COST_SCENARIOS[label]
    p = C.with_costs(C.generate(9, paths_per_strategy=10), costs)
    r, _ = C.replay(p, ms.CALIBRATED, costs=costs)
    for i in range(len(r)):
        ri, _ = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE, costs=costs)
        assert (math.isnan(r[i]) and math.isnan(ri)) or r[i] == pytest.approx(ri, abs=1e-9), i


def test_stressed_costs_are_never_recorded_as_a_calibration_version():
    with pytest.raises(ValueError):
        C.trade_return(np.ones(4), np.full(4, 1e6), np.zeros(4), 1.5, execution="immediate", costs=C.COST_SCENARIOS["combined"])
    small = {"stop_loss": (0.20,), "cover_ratio": (1.25,), "cover_fraction": (0.20,), "tiers": ((3.0, 6.0, 10.0),),
             "trail_base": (0.25,), "trail_slope": (0.15,)}
    result = C.MemecoinCalibrator(grid=small, kelly_grid=(0.25,), calibration_seeds=(1,), paths_per_strategy=10, top_exits=1,
                                  costs=C.COST_SCENARIOS["dex_fee_x2"]).calibrate()
    assert result["version"] == "unrecorded-costs" and result["costs"]["dex_fee"] == 0.006


# (compound median, stress worst equity) at quarter and half Kelly on the calibration seeds, contract v4
RECORDED = {"nominal": ((70.718, 0.508), (81.344, 0.293)), "dex_fee_x2": ((15.877, 0.557), (17.246, 0.461)),
            "min_impact_x2": ((12.841, 0.545), (13.266, 0.453)), "network_x5": ((42.505, 0.562), (14.565, 0.458)),
            "combined": ((11.610, 0.565), (10.777, 0.454))}


@pytest.fixture(scope="module")
def profile():
    return C.MemecoinCalibrator(sizing="learner").cost_profile(ms.CALIBRATED)  # contract v4, as measured


def test_the_cost_profile_is_reproduced(profile):
    for label, ((cm25, st25), (cm50, st50)) in RECORDED.items():
        row = profile[label]
        assert row[0.25]["compound_return_median"] == pytest.approx(cm25, abs=2e-3), label
        assert row[0.25]["stress_worst_equity_min"] == pytest.approx(st25, abs=2e-3), label
        assert row[0.5]["compound_return_median"] == pytest.approx(cm50, abs=2e-3), label
        assert row[0.5]["stress_worst_equity_min"] == pytest.approx(st50, abs=2e-3), label
    assert profile["nominal"][0.25]["stress_worst_equity_min"] == pytest.approx(
        C.RECORDED_SELECTIONS["calibration-v4-overlap"]["development"]["stress_worst_equity_min"], abs=1e-4)


def test_robust_to_costs_at_quarter_kelly_by_the_pre_registered_criterion_and_nothing_is_promoted(profile):
    for label in STRESSES:
        row = profile[label]
        assert row[0.25]["compound_return_median"] > 0 and row[0.25]["survives"], label
        assert not row[0.5]["survives"], label  # half Kelly misses the survival constraint under every stress
        assert row["per_trade_mean"] < profile["nominal"]["per_trade_mean"], label
    assert ms.KELLY_MULTIPLIER == 0.5 and sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
