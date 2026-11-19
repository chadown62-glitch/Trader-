"""ALPHA SHADOW — « Fast Learner » : mesure du coût de calcul et courbes d'apprentissage (critère secondaire, lot C)."""
import json

import numpy as np
import pytest

from sentinelle.alpha import fastlearner
from sentinelle.alpha.dataset import build_situations, split, time_windows
from sentinelle.alpha.execution import CostModel, PortfolioRules, run_portfolio
from sentinelle.alpha.learners import RegimeRouter
from sentinelle.alpha.markets import synthetic_universe
from sentinelle.alpha.velocity import intents_for, recent_budget, window_indices
from tools import alpha_shadow
from tools.alpha_bench import bars_only

U = bars_only(synthetic_universe(31, kind="regimes", n_bars=2500))


@pytest.fixture(scope="module")
def bench():
    return fastlearner.bench_market("essai", U, budgets=(50, None), repeats=2)


def test_time_and_memory_are_measured_in_separate_passes():
    s = build_situations(U, CostModel(), variants=("teacher",))
    masks, _ = split(s, time_windows(U, fastlearner.FRACTIONS, embargo_bars=fastlearner.EMBARGO_BARS))
    parts = {k: s.take(v) for k, v in masks.items()}
    learner, timing = fastlearner.measure(RegimeRouter, parts["train"], parts["calibration"], parts["selection"], parts["evaluation"], repeats=3)
    assert len(timing["cpu_s"]) == len(timing["wall_s"]) == 3 and min(timing["wall_s"]) > 0
    assert timing["peak_mb"] > 0 and timing["cpu_s_under_memory_tracer"] > 0
    assert hasattr(learner, "margin")  # l'apprenant rendu est bien appris et réglé


def test_ratio_interval_is_deterministic_and_refuses_degenerate_inputs():
    slow, fast = [1.0, 1.1, 0.9, 1.05, 1.2], [0.010, 0.011, 0.009, 0.012, 0.010]
    r = fastlearner.ratio_interval(slow, fast)
    assert r == fastlearner.ratio_interval(slow, fast) and r["ratio"] == pytest.approx(1.05 / 0.010)
    assert r["ci_low"] <= r["ratio"] <= r["ci_high"]
    assert fastlearner.ratio_interval(slow, [0.0, 0.0])["ratio"] is None and fastlearner.ratio_interval([], fast)["ratio"] is None
    sp = fastlearner.spread([1.0, 2.0, 3.0, 10.0])
    assert sp["median"] == 2.5 and sp["min"] == 1.0 and sp["max"] == 10.0 and sp["n"] == 4 and 3.0 < sp["p95"] <= 10.0


def test_bench_keeps_predictive_financial_and_cost_measures_apart(bench):
    assert set(bench["fixed"]) == {"cash", "take_all", "teacher:trend", "teacher:breakout", "teacher:pullback", "teacher:mean_reversion"}
    assert bench["fixed"]["cash"]["net_pnl_eur"] == 0.0 and bench["fixed"]["cash"]["fills"] == 0
    assert bench["best_fixed_in_hindsight"] in bench["fixed"]
    for cell in bench["budgets"]:
        assert set(cell["learners"]) == {"regime_router", "project_mixte"}
        for row in cell["learners"].values():
            assert {"auc", "auc_teacher_identity", "brier", "ece", "brier_skill_vs_teacher_rate"} <= set(row["predictive"])
            fin = row["financial"]
            assert {"net_pnl_eur", "fills", "false_signals", "abstentions", "vetoed", "max_drawdown", "by_asset", "by_climate"} <= set(fin)
            assert 0 <= fin["false_signals"] <= fin["fills"] and set(fin["by_climate"]) == {str(g) for g in range(6)}
            assert row["cpu_s"]["n"] == 2 and row["wall_s"]["median"] > 0 and row["peak_mb"] > 0
            assert row["delta_vs_take_all_eur"] == pytest.approx(fin["net_pnl_eur"] - bench["fixed"]["take_all"]["net_pnl_eur"])
            assert row["regret_vs_best_fixed_eur"] == pytest.approx(bench["fixed"][bench["best_fixed_in_hindsight"]]["net_pnl_eur"] - fin["net_pnl_eur"])
            assert set(row["selection_to_evaluation"]) == {"selection_mean_net_r", "evaluation_mean_net_r", "optimism_r"}
        ratio = cell["ratio_project_over_router"]
        assert ratio["cpu"]["ratio"] > 1 and ratio["wall"]["ratio"] > 1 and ratio["cpu_as_measured_in_pr40"] > 0


def test_budgets_are_equal_for_both_learners_and_respected(bench):
    small, full = bench["budgets"]
    assert small["budget"] == 50 and small["observations"] == 50 and full["budget"] == "tout" and full["observations"] > 50


def test_financial_results_of_the_bench_are_deterministic(bench):
    again = fastlearner.bench_market("essai", U, budgets=(50, None), repeats=1)
    for a, b in zip(bench["budgets"], again["budgets"]):
        for name in a["learners"]:
            assert a["learners"][name]["financial"] == b["learners"][name]["financial"]
            assert a["learners"][name]["predictive"] == b["learners"][name]["predictive"]
        assert a["tuition_eur"] == b["tuition_eur"]


def test_tuition_is_the_paper_loss_of_following_every_entry_while_the_observations_were_produced():
    cost, rules = CostModel(), PortfolioRules()
    s = build_situations(U, cost, variants=("teacher",))
    w = time_windows(U, fastlearner.FRACTIONS, embargo_bars=fastlearner.EMBARGO_BARS)
    masks, _ = split(s, w)
    train = s.take(masks["train"])
    win = window_indices(U, w.bounds["train"])
    full = fastlearner.tuition(U, train, win, cost, rules)
    direct = run_portfolio(U, intents_for(train, ~train.vetoed, np.array(["teacher"] * len(train), dtype=object)),
                           start=max(win[0], int(np.searchsorted(U.ts + 14_400_000, int(train.decided_at.min())))), end=win[1], rules=rules, cost=cost)
    assert full == pytest.approx(-direct.net_pnl)
    recent = fastlearner.tuition(U, recent_budget(train, 50), win, cost, rules)
    assert recent is not None and recent != full  # moins d'observations : une autre période, un autre coût
    assert fastlearner.tuition(U, train.take(np.zeros(len(train), bool)), win, cost, rules) is None


def test_summary_is_labelled_exploratory_and_never_turns_speed_into_a_return_claim(bench):
    summary = fastlearner.summarize([bench])
    assert summary["status"].startswith("EXPLORATORY") and "aucun avantage de rendement" in summary["status"]
    assert summary["cpu"]["cells"] == 2 and summary["cpu"]["min_ratio"] <= summary["cpu"]["median_ratio"] <= summary["cpu"]["max_ratio"]
    assert set(summary["tracer_slowdown_by_learner"]) == {"regime_router", "project_mixte"}
    assert set(summary["selection_optimism_r"]) == {"regime_router", "project_mixte"}
    info = fastlearner.platform_info()
    assert {"python", "machine", "cpu_count", "numpy", "sklearn"} <= set(info)


def test_bench_command_writes_its_plan_first_and_refuses_to_overwrite(tmp_path, capsys):
    out = tmp_path / "bench"
    assert alpha_shadow.main(["bench", "--output", str(out), "--quick", "--repeats", "1"]) == 0
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((out / "bench.json").read_text(encoding="utf-8"))
    assert plan["status"] == "EXPLORATORY" and plan["seeds"] == [7001] and report["plan"] == plan
    assert report["summary"]["status"].startswith("EXPLORATORY") and len(report["markets"]) == 1
    capsys.readouterr()
    assert alpha_shadow.main(["bench", "--output", str(out), "--quick", "--repeats", "1"]) == 2
