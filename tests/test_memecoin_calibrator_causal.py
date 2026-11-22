"""Causal execution contract of the calibrator (brain v2 loop, iteration 1): decision on the observation of step T,
fictitious fill on the observation of step T+1, as in the paper session since #91.

- a stop decided on a 0.70 quote is sold on the next quote (0.30), never at the decision price;
- a partial take-profit decided at T is sold at T+1 and the tier advances at the fill;
- a missing or illiquid next quote sells nothing: no fictitious proceeds, the order waits;
- the vectorised replay and the scalar reference agree on every path, gaps included, and no decision reads a later
  observation;
- calibration-v1 (immediate contract) is kept as it was; the causal calibration selects other parameters and is
  recorded as calibration-v2-causal, without promotion; the negative scenarios are kept and their degradation stays
  visible. Synthetic regimes, offline, deterministic, paper only."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

DEEP = 1e12  # the 1 % minimum impact applies: net ratio = price ratio x COST
COST = (1 - C.MIN_IMPACT) * (1 - C.DEX_FEE) / ((1 + C.DEX_FEE) * (1 + C.MIN_IMPACT))
NAN = math.nan


def scalar(price, liquidity=None, execution="causal"):
    price = np.asarray(price, float)
    liquidity = np.full(len(price), DEEP) if liquidity is None else np.asarray(liquidity, float)
    return C.trade_return(price, liquidity, np.zeros(len(price)), C.REFERENCE_SIZE, execution=execution)


def paths(price, liquidity=None):
    price = np.asarray(price, float)
    liquidity = np.full(len(price), DEEP) if liquidity is None else np.asarray(liquidity, float)
    return C.Paths(np.array([0]), np.array([0]), price[None, :], liquidity[None, :], np.zeros((1, len(price))), np.array([1.0]))


def vector(matrix, execution="causal"):
    matrix = np.atleast_2d(np.asarray(matrix, float))
    n = len(matrix)
    p = C.Paths(np.zeros(n, int), np.zeros(n, int), np.ones_like(matrix), np.ones_like(matrix), np.zeros_like(matrix), np.ones(n))
    r, reasons = C.replay(p, ms.CALIBRATED, matrix=matrix, execution=execution)
    return r, reasons


# -- 1. stop -----------------------------------------------------------------------------------------------------------
def test_a_stop_decided_at_070_is_sold_at_030_on_the_next_observation():
    r, reasons = vector([NAN, 0.97, 1.0, 0.70, 0.30, 0.30])
    assert r[0] == pytest.approx(-0.70) and reasons[0] == 1  # sold at 0.30, the next observation
    r_old, _ = vector([NAN, 0.97, 1.0, 0.70, 0.30, 0.30], execution="immediate")
    assert r_old[0] == pytest.approx(-0.30)  # the former contract sold at the decision price: optimistic by 40 points

    r, events = scalar([1.0, 1.0, 1.0, 0.70, 0.30, 0.30])
    assert events == [(4, "stop_loss", 1.0, round(0.30 * COST, 4), 3, round(0.70 * COST, 4))]
    assert r == pytest.approx(0.30 * COST - 1)
    r_old, events_old = scalar([1.0, 1.0, 1.0, 0.70, 0.30, 0.30], execution="immediate")
    assert events_old[0][:2] == (3, "stop_loss") and r_old == pytest.approx(0.70 * COST - 1)


# -- 2. partial take-profit --------------------------------------------------------------------------------------------
def test_a_partial_take_profit_is_decided_at_t_filled_at_t_plus_1_and_the_tier_advances_at_the_fill():
    # 1.35 -> cover decided; 1.30 -> cover sold (still above 1.25: no second cover, the tier advanced at the fill);
    # 3.30 -> first tier decided; 3.00 -> sold below its trigger, at the observation that fills it; 2.60 -> end.
    price = [1.0, 1.0, 1.35, 1.30, 3.30, 3.00, 2.60]
    r, events = scalar(price)
    p = ms.CALIBRATED
    assert events == [(3, "take_profit", p.cover_fraction, round(1.30 * COST, 4), 2, round(1.35 * COST, 4)),
                      (5, "take_profit", p.tier_fraction, round(3.00 * COST, 4), 4, round(3.30 * COST, 4))]
    sold = [p.cover_fraction, (1 - p.cover_fraction) * p.tier_fraction]
    held = 1 - sum(sold)
    assert r == pytest.approx(COST * (sold[0] * 1.30 + sold[1] * 3.00 + held * 2.60) - 1)
    rv, _ = C.replay(paths(price), p)
    assert rv[0] == pytest.approx(r, abs=1e-12)

    r, _ = vector([NAN, 0.97, 1.30, 1.20, 3.2, 2.9, 2.5])
    assert r[0] == pytest.approx(0.2 * 1.20 + 0.2 * 2.9 + 0.6 * 2.5 - 1)  # 1.32, against 1.40 under the former contract
    assert vector([NAN, 0.97, 1.30, 1.20, 3.2, 2.9, 2.5], execution="immediate")[0][0] == pytest.approx(1.40)


def test_a_lot_with_a_pending_sale_is_not_judged_again():
    _, events = scalar([1.0, 1.0, 1.35, NAN, 1.40, 1.40, 1.40])  # cover decided, quote missing, then filled once
    assert [(e[0], e[1], e[4]) for e in events] == [(4, "take_profit", 2)]


# -- 3. missing or illiquid next quote ---------------------------------------------------------------------------------
@pytest.mark.parametrize("row,expected", [
    ([NAN, 0.97, 0.70, NAN, 0.0, 0.40], -0.60),  # missing, then worthless: sold at the first realizable quote
    ([NAN, 0.97, 0.70, NAN, NAN, NAN], -1.0),  # never filled: nothing credited, not 0.70
    ([NAN, 0.97, 0.70, 0.0, 0.0, 0.0], -1.0),  # pool drained: nothing to sell
    ([NAN, 0.97, 1.10, 1.10, 1.10, NAN], -1.0),  # held, last quote missing: prudent floor, as floor_usd
])
def test_a_missing_or_illiquid_next_quote_invents_no_proceeds(row, expected):
    r, _ = vector(row)
    assert r[0] == pytest.approx(expected)


def test_the_scalar_reference_invents_no_proceeds_either():
    r, events = scalar([1.0, 1.0, 0.70, NAN, 0.50, 0.40, 0.40], [DEEP, DEEP, DEEP, DEEP, 0.0, DEEP, DEEP])
    assert events == [(5, "stop_loss", 1.0, round(0.40 * COST, 4), 2, round(0.70 * COST, 4))]
    assert r == pytest.approx(0.40 * COST - 1)
    assert scalar([1.0, 1.0, 0.70, NAN, NAN]) == (-1.0, [])
    assert scalar([1.0, 1.0, 0.70, 0.60, 0.50], [DEEP, DEEP, DEEP, 0.0, 0.0]) == (-1.0, [])


def test_an_entry_without_a_next_quote_is_not_a_trade():
    r, events = scalar([1.0, NAN, 1.2, 1.3])
    assert math.isnan(r) and events == []
    r, reasons = vector([1.0, NAN, 1.2, 1.3])
    assert math.isnan(r[0]) and reasons[0] == C.NO_ENTRY
    p = C.Paths(np.array([0, 0]), np.array([0, 0]), np.ones((2, 4)), np.full((2, 4), 50_000.0), np.full((2, 4), 0.002), np.ones(2))
    run = C.portfolio(p, np.array([NAN, 0.5]), multiplier=ms.KELLY_MULTIPLIER)
    assert run["trades"] == 1 and C.log_growth(np.array([NAN, 0.5])) == C.log_growth(np.array([0.5]))


# -- 4. vectorised = scalar, no look-ahead -----------------------------------------------------------------------------
def with_gaps(p, seed):
    rng = np.random.default_rng(seed)
    price, liquidity = p.price.copy(), p.liquidity.copy()
    price[rng.random(price.shape) < 0.03] = np.nan  # missing quotes, the entry step included
    liquidity[rng.random(liquidity.shape) < 0.02] = 0.0  # drained pool for one step
    return C.Paths(p.strategy, p.regime, price, liquidity, p.network, p.confidence)


@pytest.mark.parametrize("seed,mixes,gaps", [(9, C.MIXES, False), (21, C.RUG_STRESS, False), (9, C.MIXES, True),
                                             (33, C.RUG_STRESS, True)])
def test_vectorised_and_scalar_causal_replays_agree_on_every_path(seed, mixes, gaps):
    p = C.generate(seed, mixes=mixes, paths_per_strategy=12)
    p = with_gaps(p, seed) if gaps else p
    r, reasons = C.replay(p, ms.CALIBRATED)
    for i in range(len(r)):
        ri, _ = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
        if math.isnan(ri):
            assert math.isnan(r[i]) and reasons[i] == C.NO_ENTRY, i
        else:
            assert r[i] == pytest.approx(ri, abs=1e-9), i
    if gaps:
        assert (reasons == C.NO_ENTRY).any() and np.isfinite(r).any()


def test_no_decision_reads_a_later_observation():
    p = C.generate(17, paths_per_strategy=8)
    rng = np.random.default_rng(0)
    for i in range(len(p.strategy)):
        for cut in (5, 40, 120):
            price = p.price[i].copy()
            price[cut + 1:] *= rng.uniform(0.2, 5.0, len(price) - cut - 1)  # rewrite the future after the cut
            _, a = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
            _, b = C.trade_return(price, p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
            assert [e for e in a if e[0] <= cut] == [e for e in b if e[0] <= cut]  # fills up to the cut
            assert [e[1:2] + e[4:] for e in a if e[4] <= cut] == [e[1:2] + e[4:] for e in b if e[4] <= cut]  # decisions
            assert all(e[0] > e[4] for e in a)  # every fill comes after its decision


def test_an_unknown_contract_is_refused():
    with pytest.raises(ValueError):
        C.replay(C.generate(1, paths_per_strategy=1), ms.CALIBRATED, execution="same_bar")
    with pytest.raises(ValueError):
        scalar([1.0, 1.0, 1.0], execution="same_bar")
    with pytest.raises(ValueError):
        C.MemecoinCalibrator(execution="same_bar")
    assert C.MemecoinCalibrator().execution == "causal" and C.EXECUTIONS == ("causal", "immediate")


# -- 5. new version, no promotion, negative scenarios kept --------------------------------------------------------------
@pytest.fixture(scope="module")
def causal():
    cal = C.MemecoinCalibrator()
    return cal.calibrate(), cal.compare(ms.CALIBRATED, ms.KELLY_MULTIPLIER)


def test_the_causal_calibration_is_recorded_as_a_new_version_and_is_not_promoted(causal):
    result, _ = causal
    v1, v2 = C.RECORDED_SELECTIONS["calibration-v1"], C.RECORDED_SELECTIONS["calibration-v2-causal"]
    sel = result["selected"]
    assert result["execution"] == "causal" and result["version"] == "calibration-v2-causal"
    assert sel["policy"] == v2["policy"] != v1["policy"] and sel["kelly_multiplier"] == v2["kelly_multiplier"] and sel["survives"]
    dev = v2["development"]
    assert sel["calibration"]["compound_return"]["median"] == pytest.approx(dev["compound_return_median"], rel=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] == pytest.approx(dev["max_drawdown_max"], abs=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] <= C.MAX_DRAWDOWN
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(dev["stress_worst_equity_min"], abs=1e-4)
    # not promoted: the paper session keeps calibration-v1 until a human validates a change
    assert (v1["in_session"], v2["in_session"]) == (True, False)
    assert C._policy_dict(ms.CALIBRATED) == v1["policy"] and ms.KELLY_MULTIPLIER == v1["kelly_multiplier"]
    assert sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED and sim.CALIBRATED_EXITS == "calibrated-v1"


def test_negative_scenarios_are_kept_and_their_degradation_stays_visible(causal):
    _, cmp = causal
    bleed = np.linspace(1.0, 0.5, 120)
    args = (np.full(120, 50_000.0) * np.sqrt(bleed), np.full(120, 0.002), 1.5)
    r, ev = C.trade_return(bleed, *args)
    r_old, _ = C.trade_return(bleed, *args, execution="immediate")
    assert ev[0][1] == "stop_loss" and ev[0][0] == ev[0][4] + 1 and -0.22 < r < r_old == pytest.approx(-0.2, abs=0.005)
    rug = np.r_[np.linspace(1.0, 1.1, 20), np.full(20, 0.02)]
    r2, ev2 = C.trade_return(rug, np.r_[np.full(20, 50_000.0), np.full(20, 300.0)], np.full(40, 0.002), 1.5)
    assert ev2[0][1] == "stop_loss" and r2 < -0.9  # a stop is a trigger, not a guarantee
    all_rugs = {n: {"masked_rug": 1.0} for n in ms.NAMES}
    for seed in (101, 102):
        p = C.generate(seed, mixes=all_rugs)
        run = C.portfolio(p, C.replay(p, ms.CALIBRATED)[0], multiplier=ms.KELLY_MULTIPLIER)
        assert run["worst_equity_ratio"] >= 0.6 and run["compound_return"] > -0.4
    stress = cmp["rug_stress"]["after"]
    assert stress["worst_equity_ratio"]["min"] >= 0.5
    # documented degradation: under the causal contract the held-out rug-stress drawdown passes the 0.5 bound of v1
    assert stress["max_drawdown"]["max"] == pytest.approx(0.5128, abs=1e-3)
    for label in ("holdout", "no_explosive"):
        before, after = cmp[label]["before"], cmp[label]["after"]
        assert after["compound_return"]["median"] > before["compound_return"]["median"], label
        assert after["profit_factor"]["median"] > 1.0 and after["profit_factor"]["median"] > before["profit_factor"]["median"], label
    assert cmp["holdout"]["before"]["profit_factor"]["median"] < 1.0
    # v1 found the legacy thresholds below 1 without explosive runs; under the causal contract they are just above it
    assert cmp["no_explosive"]["before"]["profit_factor"]["median"] == pytest.approx(1.0158, abs=1e-3)


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
