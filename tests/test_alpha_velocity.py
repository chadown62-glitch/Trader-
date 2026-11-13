"""ALPHA FACTORY — Learning Velocity Challenge, Shadow Tournament, ablations : protocole et honnêteté."""
import json

import numpy as np
import pytest

from sentinelle.alpha.experiments import council_ablation, summarize_ablation, tournament
from sentinelle.alpha.learners import Learner, TakeAll, default_learners
from sentinelle.alpha.stats import auc, brier, ece, logloss, replication_verdict
from sentinelle.alpha.velocity import ChallengeConfig, market_run, preregister, run_challenge

SMALL = ChallengeConfig(seeds=(1, 2, 3), markets=(("noise", 0.0),), n_bars=2500, budgets=(100, None))


@pytest.fixture(scope="module")
def noise_report():
    return run_challenge(SMALL)


def test_p0_random_noise_is_never_announced_profitable(noise_report):
    for fam, table in noise_report["summary"].items():
        assert fam.startswith("noise")
        for key, row in table.items():
            assert row["verdict"] == "non démontré", key
    assert noise_report["can_trade"] is False


def test_p0_tournament_on_noise_does_not_claim_a_winner(noise_report):
    t = tournament(noise_report["_markets_raw"], [])
    for fam, v in t["verdicts"].items():
        assert v["verdict"] == "non démontré", fam


def test_budgets_are_equal_and_respected(noise_report):
    for m in noise_report["markets"]:
        for r in m["runs"]:
            if r["budget_label"] != "tout":
                assert r["observations"] <= int(r["budget_label"])
        by_budget = {}
        for r in m["runs"]:
            by_budget.setdefault(r["budget_label"], set()).add(r["observations"])
        assert all(len(v) == 1 for v in by_budget.values())  # mêmes observations pour tous les concurrents


def test_report_separates_predictive_and_financial_metrics_and_costs(noise_report):
    run = next(r for m in noise_report["markets"] for r in m["runs"] if r["learner"] == "project_mixte")
    assert {"auc", "brier", "logloss", "ece", "brier_skill_vs_teacher_rate"} <= set(run["predictive"])
    fin = run["financial"]
    assert {"net_pnl_eur", "trades", "fees_eur", "max_drawdown", "turnover", "abstention", "by_regime", "by_asset"} <= set(fin)
    assert run["cpu_s"] >= 0 and run["peak_mb"] >= 0
    row = noise_report["summary"]["noise@0.0"]["project_mixte|tout"]
    assert {"delta_eur_per_100_obs", "delta_eur_per_cpu_minute", "delta_eur_per_mb"} <= set(row)


def test_market_runs_are_deterministic():
    cfg = ChallengeConfig(seeds=(4,), markets=(("regimes", 1.0),), n_bars=2000, budgets=(None,))
    a = market_run("regimes", 1.0, 4, cfg, lambda: [TakeAll()])
    b = market_run("regimes", 1.0, 4, cfg, lambda: [TakeAll()])
    assert a["baseline"] == b["baseline"] and a["runs"][0]["financial"] == b["runs"][0]["financial"]


class Spy(Learner):
    name = "spy"
    seen: list = []

    def fit(self, train, cal):
        super().fit(train, cal)
        Spy.seen += [("fit", int(train.decided_at.max()) if len(train) else 0), ("cal", int(cal.decided_at.max()) if len(cal) else 0)]

    def select(self, sel):
        Spy.seen.append(("select", int(sel.decided_at.max()) if len(sel) else 0))


def test_learners_never_learn_on_the_evaluation_window():
    cfg = ChallengeConfig(seeds=(4,), markets=(("regimes", 1.0),), n_bars=2000, budgets=(None,))
    Spy.seen = []
    m = market_run("regimes", 1.0, 4, cfg, lambda: [Spy()])
    eval_start = m["windows"]["bounds"]["evaluation"][0]
    assert Spy.seen and all(t < eval_start for _, t in Spy.seen)


def test_preregistration_is_written_first_and_cannot_be_silently_replaced(tmp_path):
    out = tmp_path / "run"
    plan = preregister(SMALL, out, ["take_all"])
    assert json.loads((out / "preregistration.json").read_text())["config_sha256"] == plan["config_sha256"]
    other = ChallengeConfig(seeds=(9,), markets=(("noise", 0.0),), n_bars=2500, budgets=(None,))
    with pytest.raises(ValueError):
        preregister(other, out, ["take_all"])
    assert plan["trials_per_market"] == len(SMALL.budgets) and plan["can_trade"] is False


def test_tournament_selects_on_the_selection_window_only():
    days = list(range(100, 140))
    good_sel, bad_sel = {d: 0.01 for d in days}, {d: -0.01 for d in days}
    good_eval, bad_eval = {d: 0.02 for d in days}, {d: -0.02 for d in days}
    vm = {"kind": "x", "edge": 1.0, "seed": 1, "_baseline_daily": {d: 0.0 for d in days}, "runs": [
        {"learner": "a", "budget_label": "tout", "_daily_selection": good_sel, "_daily": bad_eval, "financial": {"net_pnl_eur": -1.0}},
        {"learner": "b", "budget_label": "tout", "_daily_selection": bad_sel, "_daily": good_eval, "financial": {"net_pnl_eur": 1.0}}]}
    t = tournament([vm], [])
    assert t["markets"][0]["winner"] == "a|tout"  # l'évaluation n'influence pas le choix
    assert t["markets"][0]["winner_curse"] > 0


def test_council_ablation_reports_marginal_contributions(tmp_path):
    cfg = ChallengeConfig(seeds=(5,), markets=(("noise", 0.0),), n_bars=2000, budgets=(None,))
    m = council_ablation("noise", 0.0, 5, cfg, ledger_path=tmp_path / "ledger.jsonl")
    assert m["results"]["council_full"]["ledger_verified"] is True
    for name in ("council_full", "vote", "single_model", "take_all", "cash", "buy_and_hold", "only:risk", "without:red_team"):
        assert name in m["results"]
    summary = summarize_ablation([m])["noise@0.0"]
    for a in ("structure", "entry_exit", "liquidity", "macro", "red_team", "risk"):
        assert summary[f"without:{a}"]["recommandation"] in ("garder", "retirable (contribution non démontrée)")
    assert summary["council_full"]["vs_take_all"] == "non démontré"  # une seule réplication ne suffit jamais


def test_replication_rule_needs_two_thirds_and_at_least_two():
    assert replication_verdict([0.1])["verdict"] == "non démontré"
    assert replication_verdict([0.1, None, 0.2])["verdict"].startswith("amélioration")
    assert replication_verdict([0.1, -0.1, -0.2])["verdict"] == "non démontré"


def test_metrics_reference_values():
    y = np.array([0, 0, 1, 1])
    assert auc(y, np.array([0.1, 0.4, 0.35, 0.8])) == 0.75
    assert brier(y, np.full(4, 0.5)) == 0.25
    assert abs(logloss(y, np.full(4, 0.5)) - np.log(2)) < 1e-12
    assert ece(y, np.array([0.0, 0.0, 1.0, 1.0])) == 0.0


def test_default_competitors_cover_simple_project_and_new_methods():
    names = {lr.name for lr in default_learners()}
    assert {"cash", "take_all", "project_mixte", "error_replay", "regime_router", "exit_learner", "router_with_exits"} <= names
    assert {f"teacher:{t}" for t in ("trend", "breakout", "pullback", "mean_reversion")} <= names


def test_doing_nothing_against_a_losing_reference_is_not_called_an_improvement():
    from sentinelle.alpha.velocity import strict_verdict

    cash = strict_verdict([0.001, 0.002, 0.001], [None, None, None])  # bat la référence perdante, ne gagne rien
    assert cash["verdict_preregistered"].startswith("amélioration")
    assert cash["verdict"] == "avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés"
    real = strict_verdict([0.001, 0.002, -0.1], [0.0005, 0.001, 0.002])
    assert real["verdict"].startswith("amélioration répliquée")
    assert strict_verdict([0.001, -0.1, -0.1], [0.1, 0.1, 0.1])["verdict"] == "non démontré"


def test_tournament_never_mixes_markets_that_share_a_seed():
    days = list(range(100, 140))
    flat = {d: 0.0 for d in days}

    def vm(edge):
        return {"kind": "hidden_rule", "edge": edge, "seed": 1, "_baseline_daily": flat, "runs": [
            {"learner": "a", "budget_label": "tout", "_daily_selection": flat, "_daily": flat, "financial": {"net_pnl_eur": 0.0}}]}

    def ab(edge, pnl):
        res = {n: {"net_pnl_eur": pnl, "_daily": {d: 0.01 for d in days}} for n in ("council_full", "vote")}
        res.update({n + "@selection": {"_daily": {d: 0.01 if edge == 1.0 else -0.01 for d in days}} for n in ("council_full", "vote")})
        return {"kind": "hidden_rule", "edge": edge, "seed": 1, "results": res}

    t = tournament([vm(1.0), vm(0.5)], [ab(1.0, 30.0), ab(0.5, 13.0)])
    by_edge = {m["edge"]: m for m in t["markets"]}
    assert by_edge[1.0]["winner"] in ("council_full", "vote") and by_edge[1.0]["winner_evaluation_pnl_eur"] == 30.0
    assert by_edge[0.5]["winner"] == "a|tout"  # le conseil du marché 0,5 perd en sélection : il ne gagne pas
