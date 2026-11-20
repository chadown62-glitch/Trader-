"""Session isolée, contrôle du cycle de vie et transport WebSocket sur le vrai serveur ASGI."""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from sentinelle.dmh import memecoin_simulator as sim
from sentinelle.dmh.memecoin_worker import MemecoinSessionWorker
from tests import test_configurable_capital as frozen
from tests import test_dmh_api

api_setup = test_dmh_api.setup
ROOT = "/api/dmh/memecoin/worker"
PAPER = {"mode": "paper"}


def test_routes_session_csrf_and_mode(api_setup):
    _, client, _ = api_setup
    anon = TestClient(client.app)
    assert anon.get(ROOT + "/status").status_code == 401
    for action in ("start", "stop"):
        url = ROOT + "/" + action
        assert anon.post(url, headers=test_dmh_api.H, json=PAPER).status_code == 401
        assert client.post(url, json=PAPER).status_code == 403
        assert client.post(url, headers=test_dmh_api.H, json={"mode": "live"}).status_code == 403
    for body in ({"interval_seconds": 0}, {"initial_capital": -1, "currency": "EUR"}, {"private_key": "x"}):
        assert client.post(ROOT + "/start", headers=test_dmh_api.H, json=PAPER | body).status_code in (400, 403)
    assert not client.get(ROOT + "/status").json()["active"]


def test_worker_websocket_session_and_resume(api_setup, monkeypatch):
    app, client, _ = api_setup
    monkeypatch.setattr(sim, "scan_once", lambda *args, **options: {"errors": []})
    before = app.db.query("SELECT * FROM balances")

    def post(action, **options):
        return client.post(ROOT + "/" + action, json=PAPER | options, headers=test_dmh_api.H)

    assert not client.get(ROOT + "/status").json()["active"]
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["topic"] == "hello"
        try:
            assert post("start", initial_capital=100, currency="USD").json()["active"]
            observed = False
            for _ in range(30):
                event = ws.receive_json()
                if event["topic"] == "memecoin_worker" and event["data"]["cycles"] >= 1:
                    assert event["data"]["asset_domain"] == "memecoin"
                    break
                if event["topic"] == "memecoin_session":
                    assert event["data"]["equity"] == 100
                    observed = True
            else:
                pytest.fail("cycle non reçu")
            assert observed
        finally:
            stopped = post("stop").json()
    assert not stopped["active"] and stopped["cycles"] == 1
    assert post("start", initial_capital=50, currency="USD").status_code == 400
    assert post("start").json()["active"]
    assert not post("stop").json()["active"]
    assert app.db.query("SELECT * FROM balances") == before


def test_pause_drains_inflight_tick_and_does_not_restart():
    entered, finish = threading.Event(), threading.Event()

    class Bus:
        def publish(self, *_):
            pass

    def cycle():
        entered.set()
        assert finish.wait(3)

    async def exercise():
        worker = MemecoinSessionWorker(cycle, lambda *_: None, Bus())
        assert (await worker.start(15))["active"]
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await worker.start(15))["active"]
        pause = asyncio.create_task(worker.stop())
        await asyncio.sleep(0)
        assert worker.status()["stopping"] and not pause.done()
        finish.set()
        assert not (await pause)["active"] and worker.cycles == 1
        assert not (await worker.stop())["active"]

    asyncio.run(exercise())


def test_repeated_autonomous_cycles(monkeypatch):
    class Bus:
        def publish(self, *_):
            pass

    async def exercise():
        ticks = []
        worker = MemecoinSessionWorker(lambda: ticks.append(1), lambda *_: None, Bus())
        # Accelerate the periodic timer without changing API bounds.
        original = asyncio.wait_for

        async def fast(wait, timeout):
            return await original(wait, 0.001)

        monkeypatch.setattr(asyncio, "wait_for", fast)
        await worker.start(15)
        async with asyncio.timeout(3):
            while worker.cycles < 2:
                await asyncio.sleep(0.002)
        await worker.stop()
        assert worker.cycles == len(ticks) >= 2

    asyncio.run(exercise())


def test_dated_eur_funding_once_and_fx_failure(db, monkeypatch):
    class Price:
        received_ms = 12345
        sha256 = "c" * 64

        def json(self):
            return {"error": [], "result": {"ZEURZUSD": {"b": ["1.25"]}}}

    class Client:
        calls = 0

        def get(self, host, path, params):
            assert (host, path, params) == ("api.kraken.com", "/0/public/Ticker", {"pair": "EURUSD"})
            self.calls += 1
            return Price()

    client = Client()
    with monkeypatch.context() as bad:
        bad.setattr(Price, "json", lambda _: {"error": ["unavailable"]})
        with pytest.raises(ValueError, match="EUR/USD"):
            sim.configure(db, client, 100, "EUR")
        assert db.get_meta(sim.KEY) is None
    sim.configure(db, client, 100, "EUR")
    sim.configure(db, client, 100, "EUR")
    report = sim.report(*sim.load(db))
    assert client.calls == 2 and report["initial_capital"] == report["equity"] == 125
    assert report["deposit"]["fx"]["observed_ms"] == 12345
    with pytest.raises(ValueError):
        sim.configure(db, client, 125, "USD")
    assert report["win_rate"] is None and report["mean_closed_pnl"] is None


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_scientific_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
