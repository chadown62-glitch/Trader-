"""The five experimental profiles stay ordered in risk under the session's stakes (brain v2 loop, iteration 15).

Report only. The session-sized portfolio applies a profile's lots, loss stop, stake scale and caps, and, like
``memecoin_simulator.profile_refusal``, only its pool limits stricter than the hard caps (SAFE's 50,000 USD depth and
5 % round trip, approximated by the entry's net ratio). Under rug-pull stress at half Kelly the worst equity never rises
from SAFE to MAX (pre-registered criterion, tolerance 0.005): met. MODERATE is the default and changes nothing; the
profiles' pre-registered values are not touched. Synthetic, offline, paper only."""

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

ORDER = ("SAFE", "MODERATE", "RISK", "ULTRA_RISK", "MAX")


def one_signal(liquidity, ratio1=0.97, n=6):
    paths = C.Paths(np.zeros(1, int), np.zeros(1, int), np.ones((1, n)), np.full((1, n), liquidity), np.full((1, n), 0.002), np.ones(1))
    lots = C.LotPaths(np.zeros((1, n)), np.ones((1, n)), np.zeros((1, n), int), np.full((1, n), ratio1))
    return paths, lots


@pytest.mark.parametrize("liquidity,ratio1,safe_trades", [(1e6, 0.97, 1), (30_000.0, 0.97, 0), (1e6, 0.93, 0)])
def test_only_the_limits_stricter_than_the_hard_caps_are_checked(liquidity, ratio1, safe_trades):
    paths, lots = one_signal(liquidity, ratio1)
    run = {pid: C._portfolio_session(paths, np.array([0.1]), multiplier=0.5, close_steps=np.array([4]), arrival_gap=20.0, lots=lots,
                                     profile=pid) for pid in ("SAFE", "MODERATE")}
    assert run["MODERATE"]["trades"] == 1  # the M58 filters are assumed met by every synthetic signal
    assert run["SAFE"]["trades"] == safe_trades  # 50,000 USD depth and a 5 % round trip


def test_moderate_is_the_default_and_an_unknown_profile_is_refused():
    assert C.MemecoinCalibrator().profile == ms.DEFAULT_PROFILE == "MODERATE"
    with pytest.raises(ValueError):
        C.MemecoinCalibrator(profile="YOLO")
    p = C.generate(14, mixes=C.RUG_STRESS)
    r, _, steps, lots = C.lot_paths(p, ms.CALIBRATED)
    a = C._portfolio_session(p, r, multiplier=0.5, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN, lots=lots)
    b = C._portfolio_session(p, r, multiplier=0.5, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN, lots=lots, profile="MODERATE")
    assert a == b


@pytest.fixture(scope="module")
def measured():
    out = {}
    for pid in ORDER:
        seeds = C.MemecoinCalibrator(profile=pid).survival_profile(ms.CALIBRATED, multipliers=(0.5,))[0.5]["seeds"]
        out[pid] = [r["worst_equity_ratio"] for r in seeds]
    return out


def test_recorded_profiles_are_reproduced_and_stay_ordered_in_risk(measured):
    for pid in ORDER:
        worst, median, below = C.RECORDED_PROFILES[pid]
        values = measured[pid]
        assert min(values) == pytest.approx(worst, abs=1e-4) and float(np.median(values)) == pytest.approx(median, abs=1e-4)
        assert sum(v < 1 - ms.PROFILES[pid].session_loss_stop for v in values) == below
    for safer, riskier in zip(ORDER, ORDER[1:]):
        assert min(measured[riskier]) <= min(measured[safer]) + 0.005, (safer, riskier)
        assert float(np.median(measured[riskier])) <= float(np.median(measured[safer])) + 0.005, (safer, riskier)
    assert ms.PROFILES_VERSION == "profiles-v1-proposed" and ms.PROFILES["MAX"].stake_scale == 2.0


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
