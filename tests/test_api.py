"""Tests d'intégration : application complète + API, sur un marché simulé."""

import time

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App

H = {"X-Sentinelle": "1"}
PASSWORD = "un-mot-de-passe-solide"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    src = SyntheticSource(["BTC/USDC", "ETH/USDC", "SOL/USDC"], days=40)
    a = App(d, source=src, settings_override={"market": {"source": "synthetic", "poll_seconds": 2, "history_days": 120},
                                              "general": {"watchlist": ["BTC/USDC", "ETH/USDC", "SOL/USDC"]},
                                              "ai": {"timeframe": "1h"}})
    assert a.engine.ready.wait(30)
    deadline = time.time() + 20
    while time.time() < deadline and len(a.hub.prices) < 3:
        time.sleep(0.2)
    yield a
    a.close()


@pytest.fixture(scope="module")
def client(app):
    api = create_api(app)
    with TestClient(api) as c:
        yield c


def _login(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=H)
    assert r.status_code == 200, r.text


def test_setup_login_and_protection(client):
    assert client.get("/api/health").json()["setup_required"] is True
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/setup", json={"password": "court"}, headers=H).status_code == 400
    assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 403  # en-tête CSRF absent
    r = client.post("/api/auth/setup", json={"password": PASSWORD}, headers=H)
    assert r.status_code == 200
    assert client.post("/api/auth/setup", json={"password": PASSWORD}, headers=H).status_code == 409
    assert client.get("/api/overview").status_code == 200
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/overview").status_code == 401
    assert client.post("/api/auth/login", json={"password": "mauvais-mot-de-passe"}, headers=H).status_code == 401
    _login(client)


def test_host_header_protection(app):
    api = create_api(app)
    with TestClient(api, base_url="http://evil.example.com") as c:
        assert c.get("/api/health").status_code == 400


def test_overview_and_accounts(client):
    _login(client)
    ov = client.get("/api/overview").json()
    roles = {a["role"] for a in ov["accounts"]}
    assert {"ai_training", "manual"} <= roles
    assert ov["engine_alive"] is True
    assert {a["name"] for a in ov["agents"]} == {"ia_entrainement", "ia_reel"}
    m = client.get("/api/markets").json()
    assert "BTC/USDC" in m["prices"]


def _manual_account(client):
    return next(a for a in client.get("/api/accounts").json() if a["role"] == "manual")


def test_manual_trade_round_trip(client, app):
    _login(client)
    acc = _manual_account(client)
    price = app.hub.price("ETH/USDC")
    body = {"account_id": acc["id"], "symbol": "ETH/USDC", "side": "buy", "type": "market", "quote_amount": 500,
            "stop_loss": round(price * 0.95, 2), "take_profit": round(price * 1.10, 2)}
    prev = client.post("/api/orders/preview", json=body, headers=H).json()
    assert prev["error"] is None and prev["qty"] > 0
    r = client.post("/api/orders", json=body, headers=H)
    assert r.status_code == 200, r.text
    pos = r.json()["position"]
    assert pos["status"] == "open"
    detail = client.get(f"/api/accounts/{acc['id']}").json()
    assert any(p["id"] == pos["id"] for p in detail["positions"])
    assert len(detail["orders"]) == 2  # stop + objectif
    r = client.patch(f"/api/positions/{pos['id']}", json={"stop": round(price * 0.97, 2)}, headers=H)
    assert r.status_code == 200 and r.json()["stop_price"] == pytest.approx(round(price * 0.97, 2))
    r = client.post(f"/api/positions/{pos['id']}/close", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "closed"
    trades = client.get("/api/trades", params={"account_id": acc["id"]}).json()
    assert trades and trades[0]["id"] == pos["id"]
    csv = client.get("/api/trades.csv", params={"account_id": acc["id"]})
    assert csv.status_code == 200 and "ETH/USDC" in csv.text


def test_order_errors_are_readable(client):
    _login(client)
    acc = _manual_account(client)
    r = client.post("/api/orders", json={"account_id": acc["id"], "symbol": "ETH/USDC", "side": "buy", "quote_amount": 10_000_000}, headers=H)
    assert r.status_code == 400 and "insuffisant" in r.json()["detail"].lower()
    r = client.post("/api/orders", json={"account_id": acc["id"], "symbol": "XYZ/USDC", "side": "buy", "quote_amount": 50}, headers=H)
    assert r.status_code == 400


def test_settings_validation_and_secrets(client):
    _login(client)
    r = client.put("/api/settings", json={"risk": {"risk_per_trade": 0.5}}, headers=H)
    assert r.status_code == 400  # au-delà du plafond de sécurité (2 %)
    r = client.put("/api/settings", json={"risk": {"risk_per_trade": 0.004}}, headers=H)
    assert r.status_code == 200 and r.json()["risk"]["risk_per_trade"] == 0.004
    r = client.put("/api/secrets", json={"values": {"telegram_token": "123456:ABCDEFGHIJKLMNOP"}}, headers=H)
    st = r.json()["telegram_token"]
    assert st["set"] is True and st["hint"] == "…MNOP"
    assert "ABCDEFGHIJ" not in client.get("/api/secrets").text
    client.put("/api/secrets", json={"values": {"telegram_token": None}}, headers=H)


def test_live_agent_locked_without_readiness(client):
    _login(client)
    r = client.post("/api/agents/ia_reel/start", json={"confirm": "JE COMPRENDS LES RISQUES"}, headers=H)
    assert r.status_code == 400
    rd = client.get("/api/ai/readiness").json()
    assert rd["unlocked"] is False and rd["criteria"]


def test_training_agent_start_stop(client, app):
    _login(client)
    r = client.post("/api/agents/ia_entrainement/start", json={}, headers=H)
    assert r.status_code == 200
    deadline = time.time() + 60
    while time.time() < deadline and "ia_entrainement" not in app.agents:
        time.sleep(0.3)
    assert "ia_entrainement" in app.agents
    st = {a["name"]: a for a in client.get("/api/agents").json()}
    assert st["ia_entrainement"]["running"] is True
    r = client.post("/api/agents/ia_entrainement/stop", json={}, headers=H)
    assert r.status_code == 200 and "ia_entrainement" not in app.agents


def test_emergency_requires_confirmation(client):
    _login(client)
    r = client.post("/api/emergency", json={"action": "flatten"}, headers=H)
    assert r.status_code == 400
    r = client.post("/api/emergency", json={"action": "flatten", "confirm": "URGENCE"}, headers=H)
    assert r.status_code == 200


def test_websocket_requires_session(client):
    _login(client)
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json()["topic"] == "hello"
