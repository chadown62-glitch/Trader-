"""Deterministic calibration of the four paper memecoin strategies, survival under rug pulls, asymmetric exits,
anti-dump smart-wallet filter, asset-domain isolation and integrity of the frozen PRs #54 and #55. Offline only."""

import json
from dataclasses import asdict

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_strategies as ms, smart_wallets as sw
from tests import test_configurable_capital as frozen

NOW = 1_800_000_000


def one(price, liquidity=50_000.0, network=0.002, strategy=0):
    price = np.asarray(price, float)
    n = len(price)
    return C.Paths(np.array([strategy]), np.array([0]), price[None, :], np.full((1, n), liquidity) * np.sqrt(price)[None, :],
                   np.full((1, n), network), np.array([1.0]))


@pytest.fixture(scope="module")
def calibration():
    cal = C.MemecoinCalibrator()
    result = cal.calibrate()
    return cal, result, cal.compare(ms.CALIBRATED, ms.KELLY_MULTIPLIER)


# -- calibration -----------------------------------------------------------------------------------------------------
def test_scenarios_are_deterministic_and_cover_every_regime():
    a, b, c = C.generate(5, paths_per_strategy=40), C.generate(5, paths_per_strategy=40), C.generate(6, paths_per_strategy=40)
    assert all(np.array_equal(getattr(a, f), getattr(b, f)) for f in ("strategy", "regime", "price", "liquidity", "network", "confidence"))
    assert not np.array_equal(a.price, c.price)
    assert set(a.regime.tolist()) == set(range(len(C.REGIMES)))
    rugs = a.price[a.regime == C.REGIMES.index("masked_rug")]
    assert (np.min(rugs[:, 1:] / rugs[:, :-1], axis=1) < 0.06).all()  # every masked rug is drained in one step


def test_a_small_calibration_is_reproducible_bit_for_bit():
    grid = {"stop_loss": (0.15, 0.20), "cover_ratio": (1.1,), "cover_fraction": (0.25,), "tiers": ((2.0, 4.0, 8.0),),
            "trail_base": (0.2, 0.3), "trail_slope": (0.1,)}
    runs = [C.MemecoinCalibrator(grid=grid, kelly_grid=(0.25,), calibration_seeds=(1, 2), holdout_seeds=(3,), paths_per_strategy=30,
                                 top_exits=2).calibrate() for _ in range(2)]
    strip = [json.dumps({k: v for k, v in r.items() if k != "_policy"}, sort_keys=True) for r in runs]
    assert strip[0] == strip[1] and runs[0]["grid_size"] == 4


def test_the_default_calibration_selects_the_policy_shipped_in_the_strategies(calibration):
    _, result, _ = calibration
    sel = result["selected"]
    assert sel["policy"] == C._policy_dict(ms.CALIBRATED) and sel["kelly_multiplier"] == ms.KELLY_MULTIPLIER
    assert sel["survives"] and 0.15 <= sel["policy"]["stop_loss"] <= 0.20 and result["asset_domain"] == "memecoin"
    assert sel["calibration"]["max_drawdown"]["max"] <= C.MAX_DRAWDOWN
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] >= C.MIN_STRESS_EQUITY


def test_calibrated_beats_legacy_on_held_out_seeds_with_and_without_explosive_runs(calibration):
    _, _, cmp = calibration
    for label in ("holdout", "no_explosive"):
        before, after = cmp[label]["before"], cmp[label]["after"]
        assert after["compound_return"]["median"] > before["compound_return"]["median"], label
        assert after["profit_factor"]["median"] > 1.0 > before["profit_factor"]["median"], label
    assert cmp["holdout"]["trade_returns"]["after_mean"] > cmp["holdout"]["trade_returns"]["before_mean"]


def test_the_portfolio_survives_intense_rug_pull_stress(calibration):
    _, _, cmp = calibration
    stress = cmp["rug_stress"]["after"]
    assert stress["worst_equity_ratio"]["min"] >= 0.5 and stress["max_drawdown"]["max"] <= 0.5
    all_rugs = {n: {"masked_rug": 1.0} for n in ms.NAMES}
    for seed in (101, 102):
        p = C.generate(seed, mixes=all_rugs)
        run = C.portfolio(p, C.replay(p, ms.CALIBRATED)[0], multiplier=ms.KELLY_MULTIPLIER)
        assert run["worst_equity_ratio"] >= 0.6 and run["compound_return"] > -0.4  # every trade is a rug: Kelly stops the bleed


# -- asymmetric exits ------------------------------------------------------------------------------------------------
def test_tiered_take_profit_then_adaptive_trailing_stop():
    price = np.r_[np.linspace(1.0, 4.0, 101), np.linspace(4.0, 1.5, 101)[1:]]
    r, events = C.trade_return(price, np.full(len(price), 50_000.0) * np.sqrt(price), np.full(len(price), 0.002), 1.5)
    reasons = [e[1] for e in events]
    assert reasons[:2] == ["take_profit", "take_profit"] and reasons[-1] == "trailing_stop"
    assert events[0][2] == ms.CALIBRATED.cover_fraction and 1.25 <= events[0][3] < 1.3  # first slice once costs are covered
    assert events[1][3] >= 3.0  # second slice at x3
    assert r > 1.0  # the trailing stop kept most of the run


def test_explosive_runs_are_allowed_to_run_and_a_covered_winner_never_turns_into_a_loss():
    price = np.r_[1.0, np.linspace(1.0, 12.0, 200)]
    r, events = C.trade_return(price, np.full(len(price), 50_000.0) * np.sqrt(price), np.full(len(price), 0.002), 1.5)
    assert r > 5.0 and [e[3] >= t for e, t in zip(events[1:4], (3.0, 6.0, 10.0))] == [True, True, True]
    fade = np.r_[np.linspace(1.0, 1.35, 30), np.linspace(1.35, 0.9, 30)[1:]]  # cover sold, then back below the entry
    r2, ev2 = C.trade_return(fade, np.full(len(fade), 50_000.0) * np.sqrt(fade), np.full(len(fade), 0.002), 1.5)
    assert [e[1] for e in ev2] == ["take_profit", "break_even_stop"] and r2 > 0


def test_the_stop_cuts_losses_at_20_percent_but_a_rug_gaps_through_it():
    bleed = np.linspace(1.0, 0.5, 120)
    r, ev = C.trade_return(bleed, np.full(120, 50_000.0) * np.sqrt(bleed), np.full(120, 0.002), 1.5)
    assert ev[0][1] == "stop_loss" and -0.22 < r < -0.19
    rug = np.r_[np.linspace(1.0, 1.1, 20), np.full(20, 0.02)]
    r2, ev2 = C.trade_return(rug, np.r_[np.full(20, 50_000.0), np.full(20, 300.0)], np.full(40, 0.002), 1.5)
    assert ev2[0][1] == "stop_loss" and r2 < -0.9  # a stop is a trigger, not a guarantee: the size limit protects the capital


def test_vectorised_and_scalar_replays_agree():
    p = C.generate(9, paths_per_strategy=10)
    r, _ = C.replay(p, ms.CALIBRATED)
    for i in range(len(r)):
        ri, _ = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
        assert r[i] == pytest.approx(ri, abs=1e-9), i


def test_the_stop_is_bounded_and_tiers_must_increase():
    with pytest.raises(ValueError):
        ms.ExitPolicy(0.30, 1.2, 0.25, (2.0, 4.0), 0.25, 0.2, 0.1)
    with pytest.raises(ValueError):
        ms.ExitPolicy(0.18, 1.2, 0.25, (4.0, 2.0), 0.25, 0.2, 0.1)


# -- sizing and reallocation -----------------------------------------------------------------------------------------
def test_capped_kelly_scaled_by_confidence_and_liquidity():
    assert ms.kelly(0.6, 3.0, 1.0) == ms.MAX_FRACTION and ms.kelly(0.3, 1.0) == 0.0
    assert ms.kelly(0.4, 2.0, 0.5) == pytest.approx(0.5 * (0.4 - 0.6 / 2.0))  # half Kelly, under the cap
    assert ms.kelly(0.55, 2.0, 0.5) == ms.MAX_FRACTION  # 0.1625 is capped at 10 %
    learner = ms.MemecoinIsolatedLearner([{"strategy": "momentum", "return": r} for r in (0.8, -0.2, 0.6, -0.2, 1.2)])
    big = learner.size("momentum", 100.0, confidence_score=1.0, liquidity_usd=1_000_000)
    assert 0 < big <= 0.10 * 100.0
    assert learner.size("momentum", 100.0, confidence_score=0.0, liquidity_usd=1_000_000) == 0.0
    assert learner.size("momentum", 100.0, confidence_score=1.0, liquidity_usd=5_000) < big  # thin pool: smaller bet
    assert learner.size("momentum", 1_000_000.0, confidence_score=1.0, liquidity_usd=60_000) <= ms.impact_cap_usd(60_000) + 1e-9
    assert ms.confidence(["momentum"], {"allowed": False}) == 0.0 < ms.confidence(["momentum"]) < ms.confidence(["momentum", "whale_tail"])


def test_reallocation_follows_the_recent_window_and_keeps_an_exploration_floor():
    old_wins = [{"strategy": "sniper", "return": 1.0} for _ in range(10)]
    recent_losses = [{"strategy": "sniper", "return": -0.2} for _ in range(12)]
    recent_wins = [{"strategy": "momentum", "return": 0.5} for _ in range(12)]
    learner = ms.MemecoinIsolatedLearner(old_wins + recent_losses + recent_wins)
    assert learner.weights["momentum"] > learner.weights["sniper"]  # the recent window matters more than old wins
    assert min(learner.weights.values()) >= ms.MIN_WEIGHT / (1 + ms.MIN_WEIGHT * len(ms.NAMES)) and sum(learner.weights.values()) == pytest.approx(1)
    probe = ms.MemecoinIsolatedLearner([{"strategy": "dip_hunter", "return": -0.2}])
    assert probe.fractions["dip_hunter"] == ms.PROBE_FRACTION  # a losing small sample keeps learning with a 1 % probe
    other = ms.MemecoinIsolatedLearner([{"strategy": "momentum", "return": 5.0, "asset_domain": "major"}])
    assert other.stats["momentum"]["trades"] == 0  # completed trades of another asset domain are ignored


def test_exit_decision_holds_between_thresholds():
    pos = {"high": 1.0, "stage": 0}
    assert ms.exit_decision(pos, 1.1) is None and ms.exit_decision(pos, 0.79) == ("stop_loss", 1.0)


# -- smart wallets ---------------------------------------------------------------------------------------------------
def trade(wallet, ts, qty, sol, sig=None):
    return {"wallet": wallet, "ts": ts, "qty": qty, "sol": sol, "signature": sig or f"{wallet}-{ts}-{qty}"}


def cycle(wallets, wallet, mint, t_buy, t_sell, created, pnl=1.0):
    sw.record(wallets, mint, trade(wallet, t_buy, 10, -1.0), created, False)
    sw.record(wallets, mint, trade(wallet, t_sell, -10, 1.0 + pnl), created, False)


def test_anti_dump_flags_a_wallet_that_resells_into_its_followers_within_seconds():
    wallets = {}
    sw.record(wallets, "M", trade("whale", NOW, 10, -1.0), NOW - 10, False)
    sw.record(wallets, "M", trade("follower", NOW + 5, 3, -0.5), NOW - 10, False)
    sw.record(wallets, "M", trade("whale", NOW + 12, -10, 2.5), NOW - 10, False)  # dumps on the follower 12 s after buying
    w = wallets["whale"]
    assert w["dumper"] and w["dumps"][0]["followers"] == ["follower"]
    cycle(wallets, "whale", "N", NOW + 100, NOW + 400, NOW + 50)
    assert not sw.profitable(w, {"M", "N"}, NOW + 500) and not sw.profitable(w, {"M", "N"})
    w.update(qualified=True, origin="o1")
    assert "whale" not in sw.ranking(wallets, NOW + 500)
    assert not sw.confluence(wallets | {"x": {"qualified": True, "origin": "o2", "asset_domain": "memecoin", "last_buy": {"M": NOW}}},
                             "M", NOW + 10, set())


def test_a_quick_round_trip_without_followers_is_not_a_dump():
    wallets = {}
    sw.record(wallets, "M", trade("alice", NOW, 10, -1.0), NOW - 60, False)
    sw.record(wallets, "M", trade("bob", NOW, 10, -1.0), NOW - 60, False)  # same second: not a follower
    sw.record(wallets, "M", trade("alice", NOW + 1, -10, 1.5), NOW - 60, False)
    assert not wallets["alice"].get("dumper")


def test_eligibility_is_weighted_toward_the_last_24_to_72_hours():
    assert sw.recency_weight(0) == 1.0 and sw.recency_weight(24 * 3600) == pytest.approx(0.5)
    assert sw.recency_weight(73 * 3600) == 0.0 and sw.recency_weight(-5) == 0.0
    old, fresh = {}, {}
    for i in range(2):
        cycle(old, "w", f"T{i}", NOW - 80 * 3600 + i * 60, NOW - 80 * 3600 + i * 60 + 600, NOW - 80 * 3600 + i * 60 - 30)
        cycle(fresh, "w", f"T{i}", NOW - 2 * 3600 + i * 60, NOW - 2 * 3600 + i * 60 + 600, NOW - 2 * 3600 + i * 60 - 30)
    winners = {"T0", "T1"}
    assert sw.profitable(old["w"], winners) and not sw.profitable(old["w"], winners, NOW)  # good, but more than 72 h ago
    assert sw.profitable(fresh["w"], winners, NOW) and fresh["w"]["recent"]["recent_cycles"] == 2
    fresh["w"].update(qualified=True)
    best = {"a": {**fresh["w"]}, "b": {**fresh["w"], "cycles": [dict(c, pnl_sol=c["pnl_sol"] * 3) for c in fresh["w"]["cycles"]]}}
    assert sw.ranking(best, NOW) == ["b", "a"]


# -- isolation and frozen work ---------------------------------------------------------------------------------------
def test_everything_stays_in_the_memecoin_domain():
    assert C.ASSET_DOMAIN == ms.ASSET_DOMAIN == sw.DOMAIN == "memecoin" == ms.CALIBRATED.asset_domain
    assert C.MemecoinCalibrator().asset_domain == ms.MemecoinIsolatedLearner([]).asset_domain == "memecoin"
    major = {"qualified": True, "origin": "o", "asset_domain": "major", "cycles": [], "last_buy": {"M": NOW}}
    assert not sw.profitable(major, set()) and sw.ranking({"m": major}, NOW) == []
    assert asdict(ms.CALIBRATED)["asset_domain"] == "memecoin"


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_pr54_and_pr55_are_intact(base, head, expected):
    assert head in ("5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18", "18b31bb901f656d6126b370f47c510775f135c57")
    frozen.test_frozen_scientific_files(base, head, expected)
