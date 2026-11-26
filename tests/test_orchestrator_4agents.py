"""Paper-only orchestration, committed event delivery and optional local inference."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import httpx
import pytest
from fastapi.testclient import TestClient

from sentinelle.agent.agent import TradingAgent
from sentinelle.api.server import create_api
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.risk.manager import RiskManager
from sentinelle.dmh.llm import draft_strategy
from sentinelle.services.bus import EventBus
from tests import test_configurable_capital as frozen
from tests.test_portfolio import make_app
from tests.test_risk_agent_backtest import _feat_with_signal


@pytest.mark.parametrize("veto", [False, True])
def test_decision_chain(db, paper, pm, prices, clock, veto):
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    risk = RiskManager(db, paper.account_id, lambda: settings, clock)
    _, features, indices = _feat_with_signal()
    features = features.iloc[:indices[-1] + 1]
    prices["ETH/USDC"] = float(features.close.iloc[-1])
    settings["ai"]["regime_filter"] = False
    settings["ai"]["strategies"] = {"trend_breakout": True}
    settings["risk"]["min_reward_to_fees"] = 1.0
    if veto:
        risk.halt("veto déterministe de test")
    bus = EventBus(db)
    events = []
    bus.add_listener(lambda topic, event: events.append(event["data"]) if topic == "decision" else None)
    agent = TradingAgent("ia_entrainement", "training", db, pm, risk, lambda: settings,
                         lambda: None, timeframe="1h", bus=bus, clock=clock)
    agent.running = True
    agent.on_bar("ETH/USDC", features)
    agent.on_bar("ETH/USDC", features)
    rows = db.query("SELECT agent, data FROM decisions WHERE kind='specialist' ORDER BY id")
    stages = [json.loads(row["data"]) for row in rows]
    live = [event for event in events if event["kind"] == "specialist"]
    assert [x["stage"] for x in stages] == ["regime", "alpha", "risk", "cost"]
    assert [x["verdict"] for x in stages] == ["pass", "pass", "veto" if veto else "pass", "skipped" if veto else "pass"]
    assert [r["agent"] for r in rows] == ["regime_detector", "alpha_hunter", "risk_commander", "cost_router"]
    assert len(live) == 4
    for stage, event in zip(stages, live, strict=True):
        assert event["agent"] == stage["specialist"]
        assert all(event[k] == value for k, value in stage.items())
    assert db.scalar("SELECT COUNT(*) FROM fills") == (0 if veto else 1)
    assert events[-1]["agent"] == "ia_entrainement"
    if not veto:
        assert stages[-1]["fee_rate"] == agent.cost_rate("ETH/USDC")
        assert stages[-1]["cost_r"] > 0


def test_specialists_share_worker_lifecycle(tmp_path):
    app = make_app(tmp_path)
    try:
        def status():
            parent, = app.agents_status()
            assert parent["name"] == "ia_entrainement"
            assert [s["stage"] for s in parent["specialists"]] == ["regime", "alpha", "risk", "cost"]
            return {s["running"] for s in parent["specialists"]}

        assert status() == {False}
        aid = app.main_account()["id"]
        app._activate_agent("ia_entrainement", aid, "1h", [], 1)
        assert list(app.agents) == ["ia_entrainement"]
        assert status() == {True}
        app.stop_agent("ia_entrainement")
        assert status() == {False}
    finally:
        app.close()


def test_authenticated_websocket_receives_persisted_stages(tmp_path):
    app = make_app(tmp_path)
    try:
        aid = app.main_account()["id"]
        app._activate_agent("ia_entrainement", aid, "1h", [], 1)
        agent = app.agents["ia_entrainement"]
        app.settings["ai"]["strategies"] = {"trend_breakout": True}
        _, features, indices = _feat_with_signal()
        with TestClient(create_api(app), headers={"X-Sentinelle": "1"}) as client:
            assert client.post("/api/auth/setup", json={"password": "mot-de-passe-de-test"}).is_success
            with client.websocket_connect("/api/ws") as ws:
                assert ws.receive_json()["topic"] == "hello"
                agent.on_bar("ETH/USDC", features.iloc[:indices[-1] + 1])
                rows = app.db.query("SELECT agent FROM decisions WHERE kind='specialist' ORDER BY id")
                assert len(rows) == 4
                names = []
                while len(names) < len(rows):
                    event = ws.receive_json()
                    assert event["topic"] != "ping", "Une étape validée en base manque au WebSocket"
                    if event["topic"] == "decision" and event["data"]["kind"] == "specialist":
                        names.append(event["data"]["agent"])
                assert names == [row["agent"] for row in rows]
    finally:
        app.close()


def test_ollama_requests_are_serialized_and_request_unload(monkeypatch):
    from sentinelle.dmh import llm
    start = Barrier(3)
    first_entered, release = Event(), Event()
    counts_lock = Lock()
    payloads = []
    active = peak = 0

    real_lock = llm._INFERENCE_LOCK
    both_attempted = Event()
    attempts = 0

    class ObservedLock:
        def __enter__(self):
            nonlocal attempts
            with counts_lock:
                attempts += 1
                if attempts == 2:
                    both_attempted.set()
            real_lock.acquire()

        def __exit__(self, *_):
            real_lock.release()

    monkeypatch.setattr(llm, "_INFERENCE_LOCK", ObservedLock())

    def handler(request):
        nonlocal active, peak
        with counts_lock:
            active += 1
            peak = max(peak, active)
            payloads.append(json.loads(request.content))
        first_entered.set()
        assert release.wait(5)
        with counts_lock:
            active -= 1
        return httpx.Response(503)  # an error must also release the lock

    def call(model):
        start.wait(timeout=5)
        return draft_strategy("test", url="http://localhost:11434", model=model, assets={"BTC"},
                              max_capital=10, transport=httpx.MockTransport(handler))

    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(call, model) for model in ("test-a", "test-b")]
        start.wait(timeout=5)
        assert first_entered.wait(5)
        try:
            assert both_attempted.wait(5)
            assert len(payloads) == 1
        finally:
            release.set()
        assert all(f.result(timeout=5)["available"] is False for f in calls)
    assert peak == 1
    assert {p["model"] for p in payloads} == {"test-a", "test-b"}
    assert all(p["stream"] is False and p["keep_alive"] == 0 for p in payloads)


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_pr_sha256_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)

def test_agent_and_monitor_never_select_offline_trials(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    try:
        def offline_trial(*args):
            raise AssertionError("Le chemin de décision a consulté un modèle à l'essai")
        monkeypatch.setattr(app.models, "trial", offline_trial)
        aid = app.main_account()["id"]
        app._activate_agent("ia_entrainement", aid, "1h", [], 1)
        agent = app.agents["ia_entrainement"]
        assert agent.model_fn() is None
        assert app.agents_status()[0]["model"] is None
        assert app.learning_monitor()["model"] is None
    finally:
        app.close()
