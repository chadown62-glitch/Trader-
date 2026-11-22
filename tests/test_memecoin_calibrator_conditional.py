"""Exploration floor conditional on the recent record, brain v2 loop, iteration 24 (report only).

Pre-registered after reading iterations 20 to 23, before any trial on its seeds: the session's exploration floor applies
while a strategy has fewer than MIN_TRADES evaluated lots, or while its recent mean profit (the session learner's own
``avg_profit_pct``) is not negative; otherwise its Kelly target alone sizes the stake. It runs on the representative bench
against the engine and CASH, with bounded exploration replayed in report, on validation seeds 701-740 never used before;
``bench_decision`` decides. Nothing is promoted. Synthetic, offline."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_memecoin_calibrator_bench import flat


def explore_flags(monkeypatch, exploration, evaluated_lots, avg_profit_pct, n=3):
    """The ``explore`` argument the portfolio passes to ``session_stake`` when the learner reports the given record."""
    seen = []
    monkeypatch.setattr(C, "session_stake", lambda **kw: seen.append(kw["explore"]) or 0.0)
    real = ml.multipliers

    def fake(completed, evaluated=()):
        out = real(completed, evaluated)
        for name in out:
            out[name] = {**out[name], "evaluated_lots": evaluated_lots, "avg_profit_pct": avg_profit_pct}
        return out

    monkeypatch.setattr(ml, "multipliers", fake)
    paths, lots = flat([0.1] * n)
    C._portfolio_session(paths, np.full(n, 0.1), multiplier=0.5, close_steps=np.full(n, 2), arrival_gap=20.0, lots=lots,
                         exploration=exploration)
    return seen


@pytest.mark.parametrize(("lots_", "avg", "session", "bounded", "conditional"), [
    (3, -40.0, True, True, True),  # under the minimal sample every rule explores
    (12, 15.0, True, False, True),  # a winning record keeps the floor under the conditional rule
    (12, 0.0, True, False, True),  # a flat record is not negative
    (12, -5.0, True, False, False),  # a losing record: only the Kelly target sizes the stake
    (12, None, True, False, True),  # no recent mean (no window): not negative
])
def test_the_floor_follows_the_recent_record(monkeypatch, lots_, avg, session, bounded, conditional):
    for rule, expected in (("session", session), ("bounded", bounded), ("conditional", conditional)):
        assert set(explore_flags(monkeypatch, rule, lots_, avg)) == {expected}, rule


def test_the_variant_lives_on_the_bench_only_and_the_defaults_are_unchanged():
    assert C.EXPLORATIONS == ("session", "bounded") and C.MemecoinCalibrator().exploration == "session"
    with pytest.raises(ValueError):
        C.MemecoinCalibrator(exploration="conditional")  # never a calibration setting, let alone a session one
    assert C.CONDITIONAL_CANDIDATES["engine"] == {} and "conditional" not in C.BENCH_CANDIDATES


def test_the_validation_seeds_were_never_used_and_nothing_is_promoted():
    used = (set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS) | set(C.ROBUSTNESS_SEEDS) | set(C.BENCH_SEEDS)
            | set(C.DRAWDOWN_SEEDS) | set(C.WAVE_SEEDS) | set(C.KELLY_SEEDS) | {9})
    assert len(C.CONDITIONAL_SEEDS) == 40 and not used & set(C.CONDITIONAL_SEEDS)
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005 and ml.MIN_TRADES == 8


@pytest.fixture(scope="module")
def m58():
    return C.bench(seeds=C.CONDITIONAL_SEEDS, populations=("m58",), candidates=C.CONDITIONAL_CANDIDATES)


def test_the_validation_bench_reproduces_its_record_and_its_decision(m58):
    for key, cell in C.RECORDED_CONDITIONAL.items():
        if key[0] == "m58":
            for name, recorded in cell.items():
                assert C.same_bench_row(C.bench_row(m58[key][name]), recorded), (key, name)
    assert C.bench_decision(m58, "conditional")["decision"] == C.RECORDED_CONDITIONAL_DECISIONS["m58"] == "REJETÉE"


def test_the_hypothesis_is_refuted_the_recent_record_does_not_select(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    # significantly worse in the adverse waves without explosive runs: the pre-registered rejection
    assert cell("no_explosive", "waves")["conditional"]["paired"]["p_degraded"] <= C.DEGRADED_P
    # it behaves like bounded exploration: the trades it avoids in the normal mix were good ones on average
    normal = cell("normal", "independent")
    assert normal["conditional"]["avoided"]["mean_return"] > 0.5
    assert abs(normal["conditional"]["trades"] - normal["bounded"]["trades"]) < 0.1 * normal["engine"]["trades"]
    # and its abstentions are not selective: rugs and explosive runs are cut in the same proportion
    waves = cell("rug_stress", "waves")
    regimes = lambda name, regime: waves[name]["by_regime"][regime]["trades"]
    cut = lambda regime: 1 - regimes("conditional", regime) / regimes("engine", regime)
    assert abs(cut("masked_rug") - cut("explosive")) < 0.05
    assert C.RECORDED_BENCH_DECISIONS["m58"]["bounded"] == "NON DÉMONTRÉE"  # the replay does not change iteration 20


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
