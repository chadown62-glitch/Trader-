"""Statistical wave breaker, brain v2 loop, iteration 22 (report only).

Pre-registered after reading iterations 20 and 21, before any trial on its seeds: entries pause for PAUSE_MIN minutes
when the current losing streak would be improbable (at most 5 %) if lot outcomes were independent at the observed
(Laplace) loss rate, never under 3 losses. It runs on the representative bench against the engine and CASH, with the
breaker of iteration 18 replayed in report, on validation seeds 501-540 never used before; ``bench_decision`` decides.
Nothing is promoted: the session, its Kelly, its profiles and the fleet are unchanged. Synthetic, offline."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_memecoin_calibrator_drawdown import run


@pytest.mark.parametrize("rate", [0.05, 0.3, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 0.97])
def test_the_threshold_is_the_shortest_improbable_streak(rate):
    k = C.wave_threshold(rate)
    assert k >= C.LOSS_STREAK and rate ** k <= C.WAVE_SIGNIFICANCE
    assert k == C.LOSS_STREAK or rate ** (k - 1) > C.WAVE_SIGNIFICANCE
    assert (C.wave_threshold(0.5), C.wave_threshold(0.6), C.wave_threshold(0.7)) == (5, 6, 9)


ALTERNATING = [0.1, -0.1] * 20  # 20 wins, 20 losses: a base rate near one half
WAVE = [-0.1] * 6  # then a wave of 6 losses, then 4 wins
AFTER = [0.1] * 4


def test_a_wave_after_a_base_rate_pauses_entries_where_the_fixed_breaker_pauses_earlier():
    returns = ALTERNATING + WAVE + AFTER
    close = [2] * len(returns)  # every lot closes 2 min after its arrival, 18 min before the next signal
    wave = run(returns, close, wave_breaker=True)
    fixed = run(returns, close, breaker=True)
    # the alternating block ends with a loss (index 39): at the 4th loss of the wave (index 43) the streak is 5, the
    # Laplace rate (24 + 1) / (44 + 2) = 0.543 gives a threshold of 5 (0.543^5 = 0.047): the next 3 arrivals are paused
    assert (wave["trades"], wave["refused_open_lots"]) == (47, 3)
    assert set(range(50)) - set(wave["opened"]) == {44, 45, 46}
    # one loss earlier (index 42: streak 4, rate 24 / 45 = 0.533, threshold 5) nothing happened
    # the fixed breaker of iteration 18 pauses after 3 losses (indices 39 to 41), during arrivals 42 to 44
    assert set(range(50)) - set(fixed["opened"]) == {42, 43, 44}
    plain = run(returns, close)
    assert plain["trades"] == 50 and plain["refused_open_lots"] == 0


def test_alternating_outcomes_and_a_run_of_losses_from_the_start_never_pause():
    close = [2] * 40
    assert run(ALTERNATING, close, wave_breaker=True)["refused_open_lots"] == 0
    assert run(ALTERNATING, close, breaker=True)["refused_open_lots"] == 0
    # without any base rate a run of losses is no evidence of clustering: the threshold grows with the observed rate
    losses = run([-0.01] * 40, close, wave_breaker=True)
    assert losses["trades"] == 40 and losses["refused_open_lots"] == 0
    assert run([-0.01] * 40, close, breaker=True)["refused_open_lots"] > 0


def test_the_validation_seeds_were_never_used_and_nothing_is_promoted():
    used = (set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS) | set(C.ROBUSTNESS_SEEDS) | set(C.BENCH_SEEDS)
            | set(C.DRAWDOWN_SEEDS) | {9})
    assert len(C.WAVE_SEEDS) == 40 and not used & set(C.WAVE_SEEDS)
    assert C.WAVE_CANDIDATES["engine"] == {} and "wave_breaker" not in C.BENCH_CANDIDATES
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005 and not C.MemecoinCalibrator().breaker


@pytest.fixture(scope="module")
def m58():
    return C.bench(seeds=C.WAVE_SEEDS, populations=("m58",), candidates=C.WAVE_CANDIDATES)


def test_the_validation_bench_reproduces_its_record_and_its_decision(m58):
    for key, cell in C.RECORDED_WAVE_BREAKER.items():
        if key[0] == "m58":
            for name, recorded in cell.items():
                assert C.same_bench_row(C.bench_row(m58[key][name]), recorded), (key, name)
    assert C.bench_decision(m58, "wave_breaker")["decision"] == C.RECORDED_WAVE_BREAKER_DECISIONS["m58"] == "NON DÉMONTRÉE"
    assert C.bench_decision(m58, "breaker")["checks"] == C.RECORDED_BREAKER_THIRD_SAMPLE


def test_the_hypothesis_is_refuted_the_statistical_breaker_acts_too_late(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    checks = C.bench_decision(m58, "wave_breaker")["checks"]
    assert not checks["A1_waves_improved"] and not checks["A2_normal_not_degraded"]
    waves = cell("rug_stress", "waves")
    # it keeps only part of the fixed breaker's protection in adverse waves (19 -> 14 failures against 19 -> 5)
    assert waves["wave_breaker"]["paired"]["p_improved"] > C.IMPROVED_P >= waves["breaker"]["paired"]["p_improved"]
    assert waves["engine"]["failures"] > waves["wave_breaker"]["failures"] > waves["breaker"]["failures"]
    # and its cost in the normal mix is smaller than the fixed breaker's, but still significant on the score
    normal = cell("normal", "independent")
    assert normal["breaker"]["net_return_median"] < normal["wave_breaker"]["net_return_median"] < normal["engine"]["net_return_median"]
    assert normal["wave_breaker"]["paired"]["p_degraded"] <= C.DEGRADED_P


def test_the_fixed_breaker_replicates_on_a_third_sample(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    assert all(cell(mix, "waves")["breaker"]["paired"]["p_improved"] <= C.IMPROVED_P for mix in ("rug_stress", "no_explosive"))
    normal = cell("normal", "independent")
    assert normal["breaker"]["net_return_median"] < 0.45 * normal["engine"]["net_return_median"]  # about -62 %
    assert C.RECORDED_BENCH_DECISIONS["m58"]["breaker"] == "NON DÉMONTRÉE"


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
