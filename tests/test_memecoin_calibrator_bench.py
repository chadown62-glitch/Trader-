"""Representative bench of the brain v2 loop, iteration 20 (report only).

The current engine and each report-only variant (bounded exploration, loss-streak breaker, liquidation at the loss stop)
run on the same causal paths, capital, costs, liquidity, profile, exit policy, Kelly and cadence, against CASH. The
representative population keeps only the signals the real M58 session could receive; every synthetic signal is a
separate, extreme stress. Confirmation seeds 301-340, never used before; the decision follows the pre-registered rule
(``bench_decision``). Nothing is promoted: the session, its Kelly and its profiles are unchanged. Synthetic, offline."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen


def flat(returns, regimes=None):
    n = len(returns)
    paths = C.Paths(np.zeros(n, int), np.array(regimes if regimes is not None else [0] * n), np.ones((n, 6)),
                    np.full((n, 6), 1e6), np.full((n, 6), 0.002), np.ones(n))
    lots = C.LotPaths(np.zeros((n, 6)), np.ones((n, 6)), np.zeros((n, 6), int), np.ones((n, 6)))
    return paths, lots


def test_the_measurements_only_count_what_happened(monkeypatch):
    monkeypatch.setattr(C, "session_stake", lambda **kw: 1.0)
    returns = [-0.1, -0.1, 0.2, -0.1, -0.1, -0.1, 0.3, math.nan]
    paths, lots = flat(returns, regimes=[0, 0, 3, 0, 1, 1, 5, 0])
    run = C._portfolio_session(paths, np.array(returns), multiplier=0.5, close_steps=np.full(8, 2), arrival_gap=20.0, lots=lots)
    assert run["trades"] == len(run["opened"]) == 7 and run["opened"] == (0, 1, 2, 3, 4, 5, 6)  # the unfilled entry is no decision
    assert run["max_loss_streak"] == 3 and run["abstentions"] == run["refused_open_lots"] + run["skipped_stakes"] == 0
    assert sum(v["pnl_usd"] for v in run["by_regime"].values()) == pytest.approx(run["final_equity"] - C.INITIAL)
    assert run["by_regime"]["masked_rug"] == {"trades": 3, "pnl_usd": pytest.approx(-0.3)}
    assert run["by_regime"]["slippage_spike"]["trades"] == 2 and run["by_regime"]["explosive"]["pnl_usd"] == pytest.approx(0.3)
    monkeypatch.setattr(C, "session_stake", lambda **kw: 0.0)
    run = C._portfolio_session(paths, np.array(returns), multiplier=0.5, close_steps=np.full(8, 2), arrival_gap=20.0, lots=lots)
    assert (run["trades"], run["skipped_stakes"], run["abstentions"], run["opened"]) == (0, 7, 7, ())


def test_every_filled_signal_is_either_a_trade_or_an_abstention():
    out = C.bench_run(C.bench_paths(9, "rug_stress", "waves", "extreme"))
    for name in ("engine", "bounded", "breaker", "liquidation", "cash"):
        run = out[name]
        assert run["trades"] + run["abstentions"] == run["signals"] and len(run["opened"]) == run["trades"]
        assert len(out["engine"]["opened"]) - len(run["avoided"]) + len(run["added"]) == run["trades"]
    assert out["cash"]["score"] == 0.0 and out["cash"]["avoided"] and not out["cash"]["added"]


def test_the_representative_population_is_exactly_the_m58_admission_in_its_order():
    p = C.generate(9)
    _, _, _, lots = C.lot_paths(p, ms.CALIBRATED)
    keep = np.array([C.admitted(p, lots, i) for i in range(len(p.strategy))])
    q = C.m58_signals(p)
    assert np.array_equal(q.price, p.price[keep]) and np.array_equal(q.strategy, p.strategy[keep])
    sniper = ms.NAMES.index("sniper")
    assert (q.strategy == sniper).sum() <= 2 < (p.strategy == sniper).sum()  # synthetic sniper pools are under 20 000 $
    waves = C.bench_paths(9, "normal", "waves", "m58")
    assert sorted(map(bytes, waves.price)) == sorted(map(bytes, q.price))  # waves reorder the admissible signals only
    with pytest.raises(ValueError):
        C.bench_paths(9, "normal", "waves", "all")


def test_score_and_sign_test():
    assert C.score({"final_equity": C.INITIAL, "max_drawdown": 0.0}) == 0.0
    assert C.score({"final_equity": C.INITIAL * math.e, "max_drawdown": 0.5}) == pytest.approx(2.0)
    assert C.score({"final_equity": C.INITIAL / math.e, "max_drawdown": 0.001}) == pytest.approx(-100.0)  # 1 % floor
    assert C.sign_test(0, 0) == 1.0 and C.sign_test(5, 0) == 1 / 32 and C.sign_test(0, 5) == 1.0
    assert C.sign_test(28, 12) == pytest.approx(0.00829, abs=1e-5) and C.sign_test(27, 13) > C.IMPROVED_P  # 40 seeds
    assert C.sign_test(26, 14) == pytest.approx(0.04035, abs=1e-5)
    assert C.sign_test(26, 14) <= C.DEGRADED_P < C.sign_test(25, 15)


def fake_table(change):
    """A table where the variant equals the engine, then ``change(cell, mix, order)`` edits the variant's summaries."""
    table = {}
    for mix in C.BENCH_MIXES:
        for order in C.BENCH_ORDERS:
            engine = {"failures": 2, "trades": 1000, "net_return_median": 1.0}
            variant = dict(engine, paired={"p_improved": 0.5, "p_degraded": 0.5})
            change(variant, mix, order)
            table[("m58", mix, order)] = {"engine": engine, "v": variant}
    return table


def improved_in_adverse_waves(v, mix, order):
    if order == "waves" and mix != "normal":
        v["paired"] = {"p_improved": 0.001, "p_degraded": 1.0}
        v["failures"] = 0


def test_the_decision_rule_is_the_pre_registered_one():
    assert C.bench_decision(fake_table(improved_in_adverse_waves), "v")["decision"] == "ACCEPTÉE TECHNIQUEMENT"
    assert C.bench_decision(fake_table(lambda v, mix, order: None), "v")["decision"] == "NON DÉMONTRÉE"

    def suppression(v, mix, order):  # safer only because it barely trades
        improved_in_adverse_waves(v, mix, order)
        v["trades"] = 400

    d = C.bench_decision(fake_table(suppression), "v")
    assert d["decision"] == "NON DÉMONTRÉE" and not d["checks"]["A5_activity"] and d["checks"]["A1_waves_improved"]

    def worse_than_cash(v, mix, order):
        improved_in_adverse_waves(v, mix, order)
        if mix == "normal":
            v["net_return_median"] = 0.0

    assert C.bench_decision(fake_table(worse_than_cash), "v")["decision"] == "NON DÉMONTRÉE"

    def harms_the_normal_mix(v, mix, order):
        improved_in_adverse_waves(v, mix, order)
        if mix == "normal":
            v["paired"] = {"p_improved": 1.0, "p_degraded": 0.01}

    d = C.bench_decision(fake_table(harms_the_normal_mix), "v")
    assert d["decision"] == "NON DÉMONTRÉE" and not d["checks"]["A2_normal_not_degraded"]

    def degraded_in_waves(v, mix, order):
        if (mix, order) == ("rug_stress", "waves"):
            v["paired"] = {"p_improved": 1.0, "p_degraded": 0.04}

    assert C.bench_decision(fake_table(degraded_in_waves), "v")["decision"] == "REJETÉE"

    def fails_more(v, mix, order):
        v["failures"] = 3

    assert C.bench_decision(fake_table(fails_more), "v")["decision"] == "REJETÉE"


def test_the_confirmation_seeds_were_never_used_and_nothing_is_promoted():
    used = set(C.CALIBRATION_SEEDS) | set(C.HOLDOUT_SEEDS) | set(C.ROBUSTNESS_SEEDS) | {9}
    assert len(C.BENCH_SEEDS) == 40 and not used & set(C.BENCH_SEEDS)
    assert ms.KELLY_MULTIPLIER == 0.5 and ml.EXPLORATION_FRACTION == 0.005 and sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED
    assert C.MemecoinCalibrator().exploration == "session" and not C.MemecoinCalibrator().breaker


@pytest.fixture(scope="module")
def m58():
    return C.bench(populations=("m58",))


def test_the_representative_bench_reproduces_its_record_and_its_decisions(m58):
    for key, cell in C.RECORDED_BENCH.items():
        if key[0] == "m58":
            for name, recorded in cell.items():
                assert C.same_bench_row(C.bench_row(m58[key][name]), recorded), (key, name)
    for variant, decision in C.RECORDED_BENCH_DECISIONS["m58"].items():
        assert C.bench_decision(m58, variant)["decision"] == decision == "NON DÉMONTRÉE"


def test_what_each_variant_gains_and_costs_in_the_m58_universe(m58):
    cell = lambda mix, order: m58[("m58", mix, order)]
    # bounded exploration: no failure left in adverse waves, but a worse return/risk in the normal and no-explosive mixes,
    # and the trades it avoids in the normal mix were good ones on average
    assert (cell("rug_stress", "waves")["engine"]["failures"], cell("rug_stress", "waves")["bounded"]["failures"]) == (25, 0)
    checks = C.bench_decision(m58, "bounded")["checks"]
    assert not checks["A1_waves_improved"] and not checks["A2_normal_not_degraded"] and not checks["A5_activity"]
    assert cell("normal", "independent")["bounded"]["avoided"]["mean_return"] > 0.5
    # breaker: a better return/risk in every mix when adverse regimes come in waves, worse with independent signals
    checks = C.bench_decision(m58, "breaker")["checks"]
    assert checks["A1_waves_improved"] and not checks["A2_normal_not_degraded"]
    assert all(cell(mix, "waves")["breaker"]["paired"]["p_improved"] <= C.IMPROVED_P for mix in C.BENCH_MIXES)
    assert cell("normal", "independent")["breaker"]["paired"]["p_degraded"] <= C.DEGRADED_P
    # liquidation at the loss stop: never worse where it changes anything, but not one failure less
    assert all(c["liquidation"]["paired"]["losses"] == 0 for c in m58.values())
    assert all(c["liquidation"]["failures"] == c["engine"]["failures"] for c in m58.values())
    # CASH: better than the engine under the rug-pull stress, worse in the normal mix
    assert all(cell("rug_stress", order)["cash"]["paired"]["p_improved"] <= C.IMPROVED_P for order in C.BENCH_ORDERS)
    assert all(cell("normal", order)["cash"]["paired"]["wins"] == 0 for order in C.BENCH_ORDERS)


def test_the_extreme_stress_outside_the_m58_universe_is_published_apart():
    table = C.bench(populations=("extreme",), mixes=("rug_stress",), orders=("waves",))
    for name, recorded in C.RECORDED_BENCH[("extreme", "rug_stress", "waves")].items():
        assert C.same_bench_row(C.bench_row(table[("extreme", "rug_stress", "waves")][name]), recorded), name
    assert set(C.RECORDED_BENCH_DECISIONS) == {"m58", "extreme"}  # the extreme stress never enters the M58 decision


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
