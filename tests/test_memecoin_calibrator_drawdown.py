"""Stakes reduced in drawdown, brain v2 loop, iteration 21 (report only).

Variant pre-registered after reading iteration 20 and before any trial on its seeds: every new stake computed by the
session's exact rule is multiplied by equity / peak equity (measured before the decision), the session's minimums kept.
It runs on the representative bench of iteration 20 against the engine and CASH, with the loss-streak breaker replayed as
an independent replication, on validation seeds 401-440 never used before. The decision follows ``bench_decision``.
Nothing is promoted: the session, its Kelly and its profiles are unchanged. Synthetic, offline."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_memecoin_calibrator_bench import flat


def run(returns, close, gap=20.0, stakes=(1.0,), **kw):
    """Portfolio on flat paths where ``session_stake`` returns ``stakes`` in turn (the last one repeated)."""
    import unittest.mock as mock
    paths, lots = flat(returns)
    calls = iter(list(stakes) + [stakes[-1]] * len(returns))
    with mock.patch.object(C, "session_stake", lambda **_: next(calls)):
        return C._portfolio_session(paths, np.array(returns), multiplier=0.5, close_steps=np.array(close), arrival_gap=gap,
                                    lots=lots, **kw)


def test_the_stake_is_scaled_by_equity_over_peak_before_the_decision():
    # lot 0 loses half its stake and closes at minute 2; lot 1 arrives at minute 20 with equity 29.5 and peak 30
    plain, scaled = run([-0.5, 0.1], [2, 2]), run([-0.5, 0.1], [2, 2], drawdown_scaling=True)
    assert plain["final_equity"] == pytest.approx(30 - 0.5 + 0.1)
    assert scaled["final_equity"] == pytest.approx(30 - 0.5 + 0.1 * 29.5 / 30)


def test_at_the_peak_nothing_changes_and_the_session_minimums_hold():
    wins = [0.2] * 6
    assert run(wins, [2] * 6, drawdown_scaling=True) == run(wins, [2] * 6)
    # after a 6 USD loss (equity 24, peak 30) a 0.06 USD stake becomes 0.048, under the session's 0.05 minimum: no trade
    small = run([-1.0, 0.1], [2, 2], stakes=(6.0, 0.06), drawdown_scaling=True)
    assert (small["trades"], small["skipped_stakes"]) == (1, 1)
    assert run([-1.0, 0.1], [2, 2], stakes=(6.0, 0.06))["trades"] == 2
    # every later stake follows the equity: 30 -> 26.4 -> 26.928 with the peak still at 30
    deep = run([-0.6, 0.1, 0.1], [2, 2, 2], stakes=(6.0,), drawdown_scaling=True)
    second = 6.0 * 26.4 / 30
    third = 6.0 * (26.4 + 0.1 * second) / 30
    assert deep["trades"] == 3 and deep["final_equity"] == pytest.approx(26.4 + 0.1 * second + 0.1 * third)


def test_the_bench_always_compares_with_the_unchanged_engine():
    with pytest.raises(ValueError):
        C.bench(seeds=(9,), candidates={"engine": {"breaker": True}})
    with pytest.raises(ValueError):
        C.bench(seeds=(9,), candidates={"drawdown": {"drawdown_scaling": True}})
    table = C.bench(seeds=(9,), populations=("m58",), mixes=("normal",), orders=("independent",), candidates=C.DRAWDOWN_CANDIDATES)
    assert set(table[("m58", "normal", "independent")]) == {"engine", "drawdown", "breaker", "cash"}


def test_the_validation_seeds_were_never_used_and_nothing_is_promoted():
    used = set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS) | set(C.ROBUSTNESS_SEEDS) | set(C.BENCH_SEEDS) | {9}
    assert len(C.DRAWDOWN_SEEDS) == 40 and not used & set(C.DRAWDOWN_SEEDS)
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005 and C.BENCH_CANDIDATES["engine"] == {}
    assert "drawdown" not in C.BENCH_CANDIDATES  # the record of iteration 20 is unchanged


@pytest.fixture(scope="module")
def m58():
    return C.bench(seeds=C.DRAWDOWN_SEEDS, populations=("m58",), candidates=C.DRAWDOWN_CANDIDATES)


def test_the_validation_bench_reproduces_its_record_and_its_decision(m58):
    for key, cell in C.RECORDED_DRAWDOWN.items():
        if key[0] == "m58":
            for name, recorded in cell.items():
                assert C.same_bench_row(C.bench_row(m58[key][name]), recorded), (key, name)
    assert C.bench_decision(m58, "drawdown")["decision"] == C.RECORDED_DRAWDOWN_DECISIONS["m58"] == "NON DÉMONTRÉE"
    assert C.bench_decision(m58, "breaker")["checks"] == C.RECORDED_BREAKER_REPLICATION


def test_what_the_reduced_stakes_gain_and_cost(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    checks = C.bench_decision(m58, "drawdown")["checks"]
    # not one survival failure left, no significant harm, but the no-explosive waves are not significantly better
    assert all(c["drawdown"]["failures"] == 0 for c in m58.values()) and sum(c["engine"]["failures"] for c in m58.values()) == 29
    assert checks["A2_normal_not_degraded"] and checks["A3_independent_not_degraded"] and checks["A5_activity"]
    assert not checks["A1_waves_improved"]
    assert cell("rug_stress", "waves")["drawdown"]["paired"]["p_improved"] <= C.IMPROVED_P
    assert cell("no_explosive", "waves")["drawdown"]["paired"]["p_improved"] > C.IMPROVED_P
    normal = cell("normal", "independent")
    assert 0.85 < normal["drawdown"]["net_return_median"] / normal["engine"]["net_return_median"] < 0.9  # about -12 %


def test_the_breaker_replication_keeps_its_wave_gain_and_its_cost_in_the_normal_mix(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    assert all(cell(mix, "waves")["breaker"]["paired"]["p_improved"] <= C.IMPROVED_P for mix in C.BENCH_MIXES)
    normal = cell("normal", "independent")
    assert normal["breaker"]["net_return_median"] < 0.45 * normal["engine"]["net_return_median"]  # about -58 %, as on 301-340
    assert C.RECORDED_BENCH_DECISIONS["m58"]["breaker"] == "NON DÉMONTRÉE"  # the pre-registered decision stands


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
