"""Detecting waves of losses, brain v2 loop, iteration 25 (report only).

Pre-registered before any trial on its seeds: a one-sided Wald-Wolfowitz runs test on the outcomes of the lots (loss or
gain, in order of arrival) tells independent signals from adverse waves; its power is measured on the first N fillable
M58 signals (N = 30, 60, 120, 240) on seeds 801-840 never used before. It is the tool the human decision on the
loss-streak breaker needs, to apply to real closed positions once they exist. Changes nothing. Synthetic, offline."""

import math

import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def test_the_runs_test_on_constructed_sequences():
    z, p = C.runs_test([True] * 10 + [False] * 10)  # 2 runs where 11 are expected
    assert z == pytest.approx((2 - 11) / math.sqrt(36000 / 7600)) and p < 1e-4
    z, p = C.runs_test([True, False] * 10)  # 20 runs: the opposite of clustering
    assert z > 3 and p > 0.99
    assert C.runs_test([True] * 8) == (pytest.approx(math.nan, nan_ok=True), 1.0) and C.runs_test([]) [1] == 1.0
    # one-sided: the p-value is the normal lower tail of z
    z, p = C.runs_test([True, True, False, True, False, False, True, False, True, True, False, False])
    assert p == pytest.approx(0.5 * (1 + math.erf(z / math.sqrt(2))))


def test_the_verdict_rule_is_the_pre_registered_one():
    good = {(m, o): {n: (0.05 if o == "independent" else 0.9) for n in C.RUNS_SIZES} for m in C.BENCH_MIXES for o in C.BENCH_ORDERS}
    assert C.runs_verdict(good) == {"valid": True, "minimal_positions": 30}
    late = {k: dict(v) for k, v in good.items()}
    for m in C.BENCH_MIXES:
        late[(m, "waves")].update({30: 0.3, 60: 0.79})
    assert C.runs_verdict(late) == {"valid": True, "minimal_positions": 120}
    noisy = {k: dict(v) for k, v in good.items()}
    noisy[("normal", "independent")][240] = 0.2  # too many false positives at the largest sample
    assert C.runs_verdict(noisy)["valid"] is False


def test_the_seeds_were_never_used_and_nothing_changes():
    used = (set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS) | set(C.ROBUSTNESS_SEEDS) | set(C.BENCH_SEEDS)
            | set(C.DRAWDOWN_SEEDS) | set(C.WAVE_SEEDS) | set(C.KELLY_SEEDS) | set(C.CONDITIONAL_SEEDS) | {9})
    assert len(C.RUNS_SEEDS) == 40 and not used & set(C.RUNS_SEEDS)
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005 and not C.MemecoinCalibrator().breaker


def test_the_measurement_reproduces_its_record_and_the_tool_is_valid_from_120_positions():
    table = C.serial_dependence()
    for key, recorded in C.RECORDED_RUNS.items():
        assert {n: round(table[key][n], 4) for n in C.RUNS_SIZES} == recorded, key
    assert C.runs_verdict(table) == C.RECORDED_RUNS_VERDICT == {"valid": True, "minimal_positions": 120}
    # independent signals: false positives near the nominal 5 %; waves: every seed detected from 120 positions on
    assert all(table[(m, "independent")][n] <= C.RUNS_MAX_FALSE_POSITIVES for m in C.BENCH_MIXES for n in C.RUNS_SIZES)
    assert all(table[(m, "waves")][120] == 1.0 for m in C.BENCH_MIXES) and table[("normal", "waves")][60] < C.RUNS_MIN_DETECTION


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
