"""Quarter Kelly against the session's half Kelly on the representative bench, brain v2 loop, iteration 23 (report only).

Pre-registered before any trial on its seeds: the same exact session stakes with a Kelly multiplier of 0.25 instead of
the active 0.5, on validation seeds 601-640 never used before; ``bench_decision`` decides. The session's Kelly
(``memecoin_strategies.KELLY_MULTIPLIER``) is not modified, whatever the result. Synthetic, offline."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def test_the_candidate_is_the_session_portfolio_at_quarter_kelly_and_nothing_else():
    paths = C.bench_paths(9, "normal", "independent", "m58")
    out = C.bench_run(paths, candidates=C.KELLY_CANDIDATES)
    r, _, steps, lots = C.lot_paths(paths, ms.CALIBRATED)
    for name, multiplier in (("engine", 0.5), ("quarter_kelly", 0.25)):
        direct = C._portfolio_session(paths, r, multiplier=multiplier, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN, lots=lots)
        assert out[name]["final_equity"] == direct["final_equity"] and out[name]["opened"] == direct["opened"]
    assert out["engine"]["final_equity"] != out["quarter_kelly"]["final_equity"]
    assert C.KELLY_CANDIDATES == {"engine": {}, "quarter_kelly": {"multiplier": 0.25}}


def test_the_validation_seeds_were_never_used_and_the_session_kelly_is_unchanged():
    used = (set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS) | set(C.ROBUSTNESS_SEEDS) | set(C.BENCH_SEEDS)
            | set(C.DRAWDOWN_SEEDS) | set(C.WAVE_SEEDS) | {9})
    assert len(C.KELLY_SEEDS) == 40 and not used & set(C.KELLY_SEEDS)
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005


@pytest.fixture(scope="module")
def m58():
    return C.bench(seeds=C.KELLY_SEEDS, populations=("m58",), candidates=C.KELLY_CANDIDATES)


def test_the_validation_bench_reproduces_its_record_and_its_decision(m58):
    for key, cell in C.RECORDED_KELLY.items():
        if key[0] == "m58":
            for name, recorded in cell.items():
                assert C.same_bench_row(C.bench_row(m58[key][name]), recorded), (key, name)
    assert C.bench_decision(m58, "quarter_kelly")["decision"] == C.RECORDED_KELLY_DECISIONS["m58"] == "NON DÉMONTRÉE"


def test_nothing_justifies_lowering_the_active_kelly_on_this_bench(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    checks = C.bench_decision(m58, "quarter_kelly")["checks"]
    # quarter Kelly is significantly worse on the return/risk score in the normal mix and without explosive runs
    assert not checks["A2_normal_not_degraded"] and not checks["A3_independent_not_degraded"] and not checks["A1_waves_improved"]
    assert all(cell("normal", order)["quarter_kelly"]["paired"]["p_degraded"] <= C.DEGRADED_P for order in C.BENCH_ORDERS)
    # and it removes almost none of the failures in adverse waves: the exploration floor, not Kelly, sizes those stakes
    waves = cell("rug_stress", "waves")
    assert waves["engine"]["failures"] - waves["quarter_kelly"]["failures"] <= 1 and waves["quarter_kelly"]["failures"] >= 15
    assert ms.KELLY_MULTIPLIER == 0.5


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
