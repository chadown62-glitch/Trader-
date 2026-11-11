import json

import numpy as np
import pytest

from sentinelle.ai import trainer
from sentinelle.ai.bench import make_candidates
from sentinelle.ai.evaluation import block_interval, compare_portfolios, daily_returns
from tools.validation.production import evaluate, market_frame, reports


def test_mutating_unseen_issues_leaves_frozen_model_identical():
    candidates, _ = make_candidates(1800, "simple", seed=19)
    first = trainer.train_walk_forward(candidates, "4h", with_importance=False)
    period = first.report["period"]
    changed = candidates.copy()
    for boundary in ("calibration_start", "selection_start", "holdout_start"):
        cut = period[boundary]
        changed.loc[(changed.decision_ts < cut) & (changed.exit_ts >= cut), "outcome_r"] *= -100
    changed.loc[changed.decision_ts >= period["holdout_start"], "outcome_r"] *= -10
    second = trainer.train_walk_forward(changed, "4h", with_importance=False)
    a, b = first.eval_model, second.eval_model
    assert a.calib == b.calib and a.threshold == b.threshold and a.payoffs == b.payoffs
    np.testing.assert_array_equal(a.predict(candidates)[0], b.predict(candidates)[0])
    assert first.report["holdout"]["auc"] != second.report["holdout"]["auc"]


@pytest.mark.parametrize("exit_offset", [0, 1, None])
def test_equal_late_and_unresolved_exits_do_not_enter_calibration(exit_offset):
    c, _ = make_candidates(1200, "simple", seed=22)
    start, end = c.decision_ts.min(), c.decision_ts.max()
    hold = int(start+(end-start)*.8)
    cut = int(start+(hold-start)*.8)
    idx = c[c.decision_ts < cut].index[-1]
    c.loc[idx,"exit_ts"] = np.nan if exit_offset is None else cut+exit_offset
    c.loc[idx,"outcome_r"] = 99999
    first = trainer.train_walk_forward(c, "4h", with_importance=False)
    c.loc[idx,"outcome_r"] = -99999
    second = trainer.train_walk_forward(c, "4h", with_importance=False)
    assert first.eval_model.calib == second.eval_model.calib and first.eval_model.threshold == second.eval_model.threshold


def test_no_promotion_on_too_few_trades_or_only_relative_advantage():
    days = {str(i):.002 for i in range(70)}
    cash = {"daily":dict.fromkeys(days,0.), "net_return":0., "n_trades":0, "net_eur_per_trade":None}
    candidate = {"daily":days,"net_return":.14,"n_trades":20,"net_eur_per_trade":.02}
    assert compare_portfolios(candidate,cash)["verdict"] == "TESTS INCOMPLETS"
    candidate["n_trades"] = 50
    assert compare_portfolios(candidate,cash)["verdict"] == "MODÈLE APPROUVÉ POUR PAPER TRADING"
    candidate.update(daily=dict.fromkeys(days,-.001),net_return=-.08,net_eur_per_trade=-.01)
    assert compare_portfolios(candidate,{**cash, "daily":dict.fromkeys(days,-.003)})["verdict"] == "AVANTAGE NET NON DÉMONTRÉ"
    assert block_interval([0., .1],block=7) is None
    with pytest.raises(ValueError):
        compare_portfolios(candidate,{**cash, "daily":{}})


def test_daily_journal_includes_idle_days_at_utc_boundaries():
    d = daily_returns([[86400000,30],[3*86400000,31.5]],30)
    assert list(d.values()) == pytest.approx([0,0,.05])


def test_generator_is_deterministic_and_reports_empty_experience(tmp_path):
    a, fee = market_frame("noise", 8, 1800)
    b, _ = market_frame("noise", 8, 1800)
    assert a.equals(b) and fee > 0
    run = evaluate("noise", 8, bars=1800, test_bars=60, sizes=(100,))
    assert run["learning_curve"][0]["status"] == "NON TESTÉ"
    report = reports([run], tmp_path, revision="test")
    assert json.loads((tmp_path/"production.json").read_text()) == report
    assert {p.name for p in tmp_path.iterdir()} == {"production.json","production.csv","production.md"}
    assert run["strategies"]["cash"]["capital_eur"] == 30


def test_dashboard_reads_persisted_data_and_preserves_unobserved_days(tmp_path):
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.core import App
    app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2),
              settings_override={"market":{"source":"synthetic"}, "general":{"quote":"EUR","watchlist":["BTC/EUR"]}})
    try:
        aid = app.main_account()["id"]
        assert app.evaluation_dashboard()["verdict"] == "NON TESTÉ"
        for day, value in [(10, 30.), (11, 29.), (14, 32.)]:
            app.db.execute("INSERT INTO equity(account_id,ts,equity,cash,unrealized) VALUES(?,?,?,?,?)",
                           (aid, day*86400+1, value, 20, value-30))
        report = app.evaluation_dashboard()
        assert report["equity"] == 32 and report["unrealized"] == 2
        assert report["max_drawdown"] == pytest.approx(1/30)
        assert [d["net_return"] for d in report["days"]] == [None, pytest.approx(-1/30), None]
    finally:
        app.db.close()


def test_subcent_overdraft_is_refused_and_journaled(tmp_path):
    from tests.test_wallet_book_execution import wallet
    from sentinelle.execution.base import OrderError
    db, broker, *_ = wallet(tmp_path / "dust.db")
    broker.set_balance("EUR", 10.0099998)
    broker.slip = 0
    with pytest.raises(OrderError, match="Fonds insuffisants"):
        broker.place_order("ABC/EUR", "buy", "limit", .1, price=100.)
    assert broker._total("EUR") == 10.0099998 and broker._total("ABC") == 0
    assert not broker.open_orders() and db.scalar("SELECT COUNT(*) FROM paper_refusals") == 1
    db.close()


def test_used_evaluation_days_cannot_be_reused(db, tmp_path):
    from sentinelle.services.models import ModelStore
    registry = ModelStore(db, tmp_path)
    assert registry.reserve_evaluation("test-v1", "1h", 100, 180, "all_signals") == 100
    registry.record_evaluation("test-v1", {"verdict": "TESTS INCOMPLETS"})
    assert registry.reserve_evaluation("test-v2", "1h", 90, 160, "all_signals") is None
    assert registry.reserve_evaluation("test-v3", "1h", 130, 240, "all_signals") == 180
    with pytest.raises(ValueError):
        registry.record_evaluation("test-v1", {"verdict": "MODÈLE APPROUVÉ POUR PAPER TRADING"})
