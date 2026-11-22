"""A loss-streak circuit breaker, measured in the calibrator only (brain v2 loop, iteration 18).

In the universe the paper session really admits (M58 pools), the remaining measured risk is adverse regimes coming in
waves: at half Kelly 11 robustness seeds out of 20 reach the 50 % loss stop. The report-only variant pauses new entries
for ``PAUSE_MIN`` (60) minutes after ``LOSS_STREAK`` (3) consecutive losing closed lots, values fixed before the
measurement. It meets the pre-registered criterion: 3 failures in waves, none with independent signals, a compound
median kept above half. Nothing in the session or the fleet changes. Synthetic, offline, paper only."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def test_the_breaker_values_were_fixed_before_the_measurement():
    assert (C.LOSS_STREAK, C.PAUSE_MIN) == (3, 60.0) and C.PAUSE_MIN * 60 == ml.MATURITY_S
    assert C.MemecoinCalibrator().breaker is False


def test_three_losing_closes_pause_new_entries_for_an_hour(monkeypatch):
    monkeypatch.setattr(C, "session_stake", lambda **kw: 0.5)
    n = 8  # signals every 10 minutes, each lot closed 2 minutes later: the first three lose
    paths = C.Paths(np.zeros(n, int), np.zeros(n, int), np.ones((n, 6)), np.full((n, 6), 1e6), np.full((n, 6), 0.002), np.ones(n))
    lots = C.LotPaths(np.zeros((n, 6)), np.ones((n, 6)), np.zeros((n, 6), int), np.ones((n, 6)))
    returns = np.r_[np.full(3, -0.5), np.full(n - 3, 0.2)]
    kw = dict(multiplier=0.5, close_steps=np.full(n, 2), arrival_gap=10.0, lots=lots)
    off = C._portfolio_session(paths, returns, **kw)
    on = C._portfolio_session(paths, returns, breaker=True, **kw)
    assert off["trades"] == n
    assert on["trades"] == n - 5 and on["refused_open_lots"] == 5  # minutes 30 to 70 are paused (the third loss closed at 22)


@pytest.fixture(scope="module")
def measured():
    out = {}
    for order, block in (("independent", None), ("waves", C.CLUSTER_BLOCK)):
        for state, on in (("off", False), ("on", True)):
            cal = C.MemecoinCalibrator(m58_admission=True, cluster_block=block, breaker=on)
            profile = cal.survival_profile(ms.CALIBRATED, multipliers=(0.25, 0.5))
            dev = C._summary([cal._portfolio(p, cal._replayed(p, ms.CALIBRATED), 0.5) for p in cal._paths(cal.calibration_seeds)])
            out[(order, state)] = ((profile[0.25]["failures"], profile[0.5]["failures"]), dev["compound_return"]["median"])
    return out


def test_recorded_breaker_results_are_reproduced_and_the_criterion_is_met(measured):
    for (order, state), (failures, compound) in measured.items():
        recorded_failures, recorded_compound = C.RECORDED_BREAKER[order][state]
        assert failures == recorded_failures and compound == pytest.approx(recorded_compound, abs=2e-3), (order, state)
    assert measured[("waves", "on")][0][1] <= 6 < measured[("waves", "off")][0][1] == 11
    assert measured[("independent", "on")][0] == (0, 0)
    assert measured[("independent", "on")][1] >= 0.5 * measured[("independent", "off")][1]
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
