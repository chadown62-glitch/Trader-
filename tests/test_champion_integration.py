"""Integration of #26 on #27: deterministic synthetic fixtures, no network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sentinelle.ai.bench import make_candidates
from sentinelle.ai.model import MetaModel, payoff_table
from sentinelle.api.server import create_api
from sentinelle.config import DEFAULT_SETTINGS, timeframe_ms
from sentinelle.services.champion import RegisteredComparison, register_pair
from sentinelle.services.models import ModelStore
from sentinelle.storage.db import Database, dumps, loads
from tests.conftest import FakeClock

START = 1_800_000_000.
DAY = 86400


@pytest.fixture(scope="module")
def base_model():
    rows = make_candidates(450, "simple", seed=40)[0]
    m = MetaModel("4h", recipe="prudent").fit(rows)
    m.payoffs = payoff_table(rows)
    m.trained_until = 1_600_000_000_000
    m.metrics = {"verdict": "validé"}
    return m, rows


def result(base_model, version, ok=True):
    m = deepcopy(base_model[0]); m.version = version
    return SimpleNamespace(final_model=m, ok=ok, trial=False, reasons=["synthetic fixture"])


@pytest.fixture
def registry(tmp_path, base_model):
    db = Database(tmp_path / "registry.db")
    store = ModelStore(db, tmp_path / "models")
    yield db, store
    db.close()


def setup_pair(db, store, base_model):
    cfg = deepcopy(DEFAULT_SETTINGS)
    cfg["general"]["quote"] = "EUR"
    cfg["general"]["watchlist"] = ["BTC/EUR"]
    cfg["market"]["source"] = "synthetic"
    for version in ("fixture-a", "fixture-b"):
        store.register(result(base_model, version), {})
    aid = db.insert("accounts", {"name": "isolated EUR30 test", "kind": "paper", "quote": "EUR", "initial_balance": 30})
    p = register_pair(db, store, champion="fixture-a", challenger="fixture-b", settings=cfg,
                      start=START, end=START+90*DAY, now=START-10)
    return cfg, aid, p


def candidate(db, aid, features, *, at=START, strategy="trend_follow", decision="préparé"):
    features = {**features, "prediction_recorded_at": at, "fee_rate": .001, "slippage": .0005,
                "cost_r": .04, "contexte": {"source": "deterministic fixture"}}
    cid = db.insert("candidates", {"symbol": "BTC/EUR", "timeframe": "4h", "ts": int(at*1000-timeframe_ms("4h")),
                    "strategy": strategy, "source": "live", "account_id": aid, "features": dumps(features),
                    "entry": 100., "stop": 95., "target": 110., "decision": decision})
    return db.one("SELECT * FROM candidates WHERE id=?", (cid,))


def state(at=START):
    return {"equity_eur": 30., "exposure_eur": 0., "available_at": at}


def test_immutable_model_version_and_duplicate_does_not_touch_file(registry, base_model):
    db, store = registry
    row = store.register(result(base_model, "version-a"), {"negative_result": True})
    raw = Path(row["path"]).read_bytes()
    assert row["sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["params"]["trained_until"] == base_model[0].trained_until
    with pytest.raises(ValueError, match="déjà enregistrée"):
        store.register(result(base_model, "version-a"), {"replacement": True})
    assert Path(row["path"]).read_bytes() == raw and store.report("version-a") == {"negative_result": True}
    assert db.scalar("SELECT COUNT(*) FROM model_events") == 1


@pytest.mark.parametrize("raw", [b"truncated joblib", b""])
def test_cache_cannot_hide_modified_or_unreadable_model(registry, base_model, raw):
    db, store = registry
    row = store.register(result(base_model, "cached-a"), {})
    assert store.by_version("cached-a").version == "cached-a"
    Path(row["path"]).write_bytes(raw)
    assert store.by_version("cached-a") is None
    assert store.get("cached-a")["sha256"] == row["sha256"]
    assert db.scalar("SELECT COUNT(*) FROM models") == 1


def test_promotion_requires_evidence_and_reference_and_is_idempotent(registry, base_model):
    db, store = registry
    store.register(result(base_model, "a"), {})
    with pytest.raises(ValueError, match="Validation appariée"):
        store.promote("a")
    store.reserve_evaluation("a", "4h", 100, 200, "all_signals")
    store.record_evaluation("a", {"verdict": "MODÈLE APPROUVÉ POUR PAPER TRADING"})
    store.promote("a"); store.promote("a")
    assert len([e for e in store.history() if e["event"] == "promu"]) == 1
    store.register(result(base_model, "b"), {})
    store.reserve_evaluation("b", "4h", 200, 300, "changed-reference")
    store.record_evaluation("b", {"verdict": "MODÈLE APPROUVÉ POUR PAPER TRADING"})
    with pytest.raises(ValueError, match="référence modifié"):
        store.promote("b")
    assert store.champion("4h").version == "a"
    with pytest.raises(ValueError, match="déjà utilisée"):
        store.record_evaluation("b", {"verdict": "TESTS INCOMPLETS"})


def test_registered_protocol_blocks_promotion_and_retirement_across_restart(registry, base_model, tmp_path):
    db, store = registry
    _, _, p = setup_pair(db, store, base_model)
    store2 = ModelStore(db, tmp_path / "models")
    for fn in (store2.promote, store2.retire):
        with pytest.raises(ValueError, match="Politique figée"):
            fn("fixture-a")
    with pytest.raises(ValueError, match="déjà figé"):
        store2.lock_policy(p["sha256"], START, START+100*DAY, "replacement")


def test_legacy_migration_preserves_models_accounts_and_unverified_bytes(tmp_path, base_model):
    path = tmp_path / "old.db"
    model = result(base_model, "legacy").final_model
    artifact = tmp_path / "legacy.joblib"; model.save(artifact)
    original = artifact.read_bytes()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE models(version TEXT PRIMARY KEY,created_at REAL,status TEXT,timeframe TEXT,metrics TEXT,path TEXT,notes TEXT)")
    conn.execute("INSERT INTO models VALUES(?,?,?,?,?,?,?)", ("legacy", 1, "champion", "4h", "{}", str(artifact), "keep"))
    conn.commit(); conn.close()
    for _ in range(2):
        db = Database(path)
        store = ModelStore(db, tmp_path)
        assert store.get("legacy")["sha256"] is None
        assert store.champion("4h").version == "legacy" and artifact.read_bytes() == original
        assert db.scalar("SELECT COUNT(*) FROM models") == 1
        if not db.scalar("SELECT COUNT(*) FROM accounts"):
            for n in (1, 2):
                db.insert("accounts", {"name": f"old{n}", "kind": "paper", "quote": "USDC", "initial_balance": 10000})
        assert db.scalar("SELECT SUM(initial_balance) FROM accounts") == 20000
        db.close()


def test_future_registration_rejects_overlap_same_model_and_unknown_train_end(registry, base_model):
    db, store = registry
    cfg, _, _ = setup_pair(db, store, base_model)
    with pytest.raises(ValueError, match="déjà enregistrée"):
        register_pair(db, store, champion="fixture-a", challenger="fixture-b", settings=cfg, start=START+DAY, end=START+100*DAY, now=START-2)
    with pytest.raises(ValueError, match="distinctes"):
        register_pair(db, store, champion="fixture-a", challenger="fixture-a", settings=cfg, start=START, end=START+DAY, now=START-2)
    m = result(base_model, "unknown"); m.final_model.trained_until = None; store.register(m, {})
    with pytest.raises(ValueError, match="période vue"):
        register_pair(db, store, champion="fixture-a", challenger="unknown", settings=cfg, start=START+100*DAY, end=START+200*DAY, now=START-2)
    assert db.scalar("SELECT COUNT(*) FROM learning_protocols") == 1


def test_common_forecasts_precede_outcome_idempotent_and_causal(registry, base_model):
    db, store = registry
    cfg, aid, p = setup_pair(db, store, base_model)
    clock = FakeClock(START)
    run = RegisteredComparison(db, store, p["sha256"], clock=clock)
    c = candidate(db, aid, base_model[1].iloc[0].to_dict())
    key = run.record(c, cfg, state())
    original = run.report(); assert original["n"] == 1 and original["pending"] == 1
    assert run.record(c, cfg, state()) == key
    payload = loads(db.scalar("SELECT payload FROM paired_predictions"))
    assert set(payload["forecasts"]) == {"champion", "challenger"}
    assert all(f["wallet_pnl"] is None and f["executed"] is False for f in payload["forecasts"].values())
    clock.advance(7200)
    db.update("candidates", c["id"], {"outcome": "stop", "outcome_r": -1.04, "exit_ts": int(clock()*1000), "resolved_at": clock()})
    assert run.resolve() == 1 and run.resolve() == 0
    current = run.report(); assert current["resolved"] == 1 and current["diagnostics"]["champion"]["brier"] is not None
    assert current["financial_comparison"] is None and current["verdict"] == "insuffisant"
    assert run.report(as_of=START) == original
    assert RegisteredComparison(db, store, p["sha256"], clock=clock).report() == current


@pytest.mark.parametrize("mutation", ["late", "resolved", "settings", "price", "model", "future_state"])
def test_conflicts_fail_closed(registry, base_model, mutation):
    db, store = registry
    cfg, aid, p = setup_pair(db, store, base_model)
    clock = FakeClock(START)
    run = RegisteredComparison(db, store, p["sha256"], clock=clock)
    c = candidate(db, aid, base_model[1].iloc[0].to_dict())
    if mutation == "price":
        run.record(c, cfg, state()); c["entry"] += 1
    if mutation == "late": clock.advance(181)
    if mutation == "resolved": c["outcome"] = "stop"
    if mutation == "settings": cfg["paper"]["fee_rate"] += .01
    if mutation == "model": Path(store.get("fixture-a")["path"]).write_bytes(b"corrupt")
    s = state(START+1) if mutation == "future_state" else state()
    with pytest.raises(ValueError): run.record(c, cfg, s)


def test_predictions_outcomes_and_protocols_are_immutable(registry, base_model):
    db, store = registry
    cfg, aid, p = setup_pair(db, store, base_model)
    run = RegisteredComparison(db, store, p["sha256"], clock=lambda:START)
    run.record(candidate(db, aid, base_model[1].iloc[0].to_dict()), cfg, state())
    for table in ("learning_protocols", "paired_predictions", "model_events"):
        with pytest.raises(sqlite3.IntegrityError): db.execute(f"DELETE FROM {table}")
    json.dumps(run.report(), allow_nan=False)


def test_no_financial_advantage_claim_from_cancelled_signals(registry, base_model):
    db, store = registry
    cfg, aid, p = setup_pair(db, store, base_model)
    clock = FakeClock(START)
    run = RegisteredComparison(db, store, p["sha256"], clock=clock)
    for day in range(30):
        clock.t = START + day*DAY
        c = candidate(db, aid, base_model[1].iloc[day].to_dict(), at=clock())
        run.record(c, cfg, state(clock()))
        clock.advance(3600)
        db.update("candidates", c["id"], {"outcome": "stop", "outcome_r": -2., "exit_ts": int(clock()*1000), "resolved_at": clock()})
    run.resolve()
    report = run.report()
    assert report["resolved"] == 30 and report["observed_days"] == 30
    assert report["brier_improvement_daily_interval"] == [0., 0.]
    assert report["financial_comparison"] is None and not report["promotion_allowed"]
    assert db.scalar("SELECT COUNT(*) FROM fills") == 0


def test_learning_api_is_authenticated_read_only_and_finite(tmp_path):
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.core import App
    app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2))
    with TestClient(create_api(app)) as client:
        assert client.get("/api/ai/champion").status_code == 401
        assert client.post("/api/auth/setup", json={"password": "isolated-champion-test-password"}, headers={"X-Sentinelle":"1"}).status_code == 200
        status = client.get("/api/ai/champion").json()
        assert not status["review"]["automatic_promotion"] and status["comparisons"] == []
        for url in ("/api/ai/champion", "/api/ai/journal", "/api/ai/experiments"):
            response = client.get(url); assert response.status_code == 200
            json.dumps(response.json(), allow_nan=False)
        assert client.post("/api/ai/rollback", json={"confirm": True}, headers={"X-Sentinelle":"1"}).status_code == 405
        assert app.db.scalar("SELECT COUNT(*) FROM fills") == 0


def test_journal_never_turns_a_theoretical_label_into_wallet_pnl(tmp_path):
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource
    app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2))
    try:
        aid = app.main_account()["id"]
        c = candidate(app.db, aid, {}, decision="pris")
        app.db.update("candidates", c["id"], {"outcome": "target", "outcome_r": 3.})
        row = app.decision_journal()["rows"][0]
        assert row["theoretical"]["r"] == 3. and row["theoretical"]["wallet_pnl"] is None
        assert row["actual"] is None and row["link_missing"] and row["plan"] is None
    finally: app.db.close()


def test_journal_eur_partial_sales_use_accounted_fills_and_fees(tmp_path):
    from sentinelle.services.journal import JournalMixin
    from tests.test_wallet_book_execution import wallet, snapshot, check_ledger
    db, broker, pm, clock, book = wallet(tmp_path/"partial.db")
    try:
        cid = db.insert("candidates", {"symbol":"ABC/EUR","timeframe":"4h","ts":int(clock[0]*1000),
                        "strategy":"trend_follow","source":"live","account_id":1,"decision":"pris",
                        "outcome":"target","outcome_r":99.})
        pos = pm.open("ABC/EUR", .1, origin="ai", strategy="trend_follow", timeframe="4h", candidate_id=cid)
        assert pos["status"] == "open" and pos["qty"] == pytest.approx(.1)
        entry_cost = sum(f["amount"]*f["price"]+f["fee"] for f in db.query("SELECT * FROM fills"))
        clock[0] += 1; book[0] = snapshot(clock[0], bids=((110, ".2"),), asks=((112, ".2"),))
        broker.place_order("ABC/EUR","sell","market",.04,position_id=pos["id"],purpose="exit")
        journal = JournalMixin()
        journal.db = db
        journal.ai_account = lambda:db.one("SELECT * FROM accounts WHERE id=1")
        row = journal.decision_journal()["rows"][0]
        assert row["actual"]["partial_realization"]
        assert row["actual"]["pnl_r"] is None and row["theoretical"]["r"] == 99.
        assert row["actual"]["realized_pnl"] == pytest.approx(.04*110*.999-entry_cost*.4)
        assert row["actual"]["fees"] == pytest.approx(sum(f["fee"] for f in db.query("SELECT fee FROM fills")))
        clock[0] += 1; book[0] = snapshot(clock[0], bids=((110, ".2"),), asks=((112, ".2"),))
        pm.close(pos["id"], "end fixture")
        row = journal.decision_journal()["rows"][0]
        assert row["actual"]["status"] == "closed"
        assert row["actual"]["realized_pnl"] == pytest.approx(.1*110*.999-entry_cost)
        assert row["actual"]["currency"] == "EUR" and not row["link_missing"]
        check_ledger(db,broker)
    finally: db.close()


def test_plan_does_not_invent_loss_split_or_individual_confidence():
    from sentinelle.agent.plan import build_scenarios, probability_margin
    m = SimpleNamespace()
    scenarios = build_scenarios("trend_follow", .6, m, .04, 2.)
    assert scenarios[0]["probabilite"] == .6
    assert all(s["probabilite"] is None for s in scenarios[1:])
    assert probability_margin(m,.6)["marge"] is None
    assert all(s["probabilite"] is None for s in build_scenarios("trend_follow",None,None,.04,2.))


def test_real_frozen_artifact_import_is_exact_and_attachable_without_starting(tmp_path, base_model):
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource
    from tools.prospective import SupervisedObserver, check
    from tools.champion_shadow import ComparisonAttachment
    root = Path(__file__).resolve().parents[1]/"research/prospective-v2"
    protocol = check(root)
    cfg = json.loads((root/"settings.json").read_text())
    app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2), settings_override=cfg)
    try:
        app._startup()
        baseline = app.models.import_artifact(root/"model.joblib", protocol["model_sha256"])
        assert app.models.import_artifact(root/"model.joblib", protocol["model_sha256"])["version"] == baseline["version"]
        assert Path(baseline["path"]).read_bytes() == (root/"model.joblib").read_bytes()
        assert baseline["status"] == "rejected"  # import is never approval
        challenger = app.models.register(result(base_model,"attachment-fixture"), {})
        start = START
        registration = register_pair(app.db,app.models,champion=baseline["version"],challenger=challenger["version"],
                                     settings=cfg,start=start,end=start+DAY,now=start-10)
        observer = SupervisedObserver(app,root)
        attachment = ComparisonAttachment(observer,registration["sha256"])
        assert observer.agent.shadow_fn is not None
        attachment.close(); assert observer.agent.shadow_fn is None
        assert app.db.scalar("SELECT COUNT(*) FROM fills") == 0
        assert app.db.scalar("SELECT COUNT(*) FROM paired_predictions") == 0
        assert check(root) == protocol
    finally: app.db.close()


def test_optional_pair_recorder_failure_prevents_any_order_and_rolls_back_intent(tmp_path):
    from tests.test_prospective_atomicity import scenario
    # Inject a journal failure at the actual prepare callback, not a mocked broker.
    from unittest.mock import patch
    from sentinelle.agent.agent import TradingAgent
    original = TradingAgent.__init__
    def init(agent,*args,**kwargs):
        original(agent,*args,**kwargs)
        def fail(_):
            raise RuntimeError("paired journal unavailable")
        agent.shadow_fn = fail
    path = tmp_path/"recorder-failure.db"
    with patch.object(TradingAgent,"__init__",init):
        with pytest.raises(RuntimeError,match="journal unavailable"): scenario(path,"ok")
    db = Database(path)
    assert db.scalar("SELECT COUNT(*) FROM fills")==0
    assert db.scalar("SELECT COUNT(*) FROM positions")==0
    assert db.scalar("SELECT COUNT(*) FROM candidates")==0
    db.close()
