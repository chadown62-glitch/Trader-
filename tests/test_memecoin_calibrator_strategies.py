"""Which strategy carries the losses of the mix without explosive runs under the session's stakes? (iteration 14)

Report only. Summed over the held-out seeds at half Kelly, the sniper (-7.89 fictitious USD) and momentum (-7.29) lose
while dip_hunter and whale_tail gain; the bounded-exploration variant cuts the sniper's loss by 85 %. The pre-registered
hypothesis (largest loss on the sniper, cut by half or more) is confirmed; nothing in the strategies, the session or
the fleet changes. Synthetic, offline, paper only."""

import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


@pytest.fixture(scope="module")
def pnl():
    out = {}
    for mode in C.EXPLORATIONS:
        cal = C.MemecoinCalibrator(exploration=mode)
        total = {name: 0.0 for name in ms.NAMES}
        for p in cal._paths(C.HOLDOUT_SEEDS, C.NO_EXPLOSIVE):
            run = cal._portfolio(p, cal._replayed(p, ms.CALIBRATED), 0.5)
            for name in ms.NAMES:
                total[name] += run["by_strategy"][name]["pnl_usd"]
        out[mode] = total
    return out


def test_per_strategy_pnl_adds_up_to_the_portfolio():
    cal = C.MemecoinCalibrator()
    p = C.generate(101, mixes=C.NO_EXPLOSIVE)
    run = cal._portfolio(p, cal._replayed(p, ms.CALIBRATED), 0.5)
    assert sum(v["pnl_usd"] for v in run["by_strategy"].values()) == pytest.approx(run["final_equity"] - C.INITIAL, abs=1e-9)


def test_recorded_strategy_pnl_is_reproduced_and_the_hypothesis_is_confirmed(pnl):
    for mode, total in pnl.items():
        for name, value in total.items():
            assert value == pytest.approx(C.RECORDED_STRATEGY_PNL[mode][name], abs=2e-3), (mode, name)
    session, bounded = pnl["session"], pnl["bounded"]
    assert min(session, key=session.get) == "sniper"  # the largest loss
    assert bounded["sniper"] >= 0.5 * session["sniper"]  # cut by half or more (both negative)
    assert session["momentum"] < 0 and abs(session["momentum"] - session["sniper"]) < 1.0  # momentum is almost as bad


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
