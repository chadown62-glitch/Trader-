"""DIRECT MARKET HUB — API de l'application complète : verrou fictif impossible à contourner, parcours de bout en bout."""
import logging
import time

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.dmh.net import PublicClient
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App

from tests import dmh_support as S

logging.disable(logging.CRITICAL)
H = {"X-Sentinelle": "1"}
PASSWORD = "un-mot-de-passe-solide"


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    d = tmp_path_factory.mktemp("dmh-app")
    src = SyntheticSource(["BTC/USDC", "ETH/USDC"], days=40)
    app = App(d, source=src, settings_override={"market": {"source": "synthetic", "poll_seconds": 2, "history_days": 120},
                                                "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}, "ai": {"timeframe": "1h"},
                                                "portfolio": {"initial": 30.0}})
    assert app.engine.ready.wait(30)
    api = create_api(app)
    hub = app.dmh
    view = S.market()
    hub.view_factory = lambda: view
    hub.registry = S.registry()
    hub.risk = S.FakeRisk()
    hub.client = PublicClient(transport=S.no_network)
    with TestClient(api) as c:
        assert c.post("/api/auth/setup", json={"password": PASSWORD}, headers=H).status_code == 200
        yield app, c, view
    app.close()


def test_hub_requires_authentication_and_csrf(setup):
    app, c, _ = setup
    anon = TestClient(create_api(app))
    assert anon.get("/api/dmh/status").status_code == 401
    assert c.post("/api/dmh/preview", json=S.buy()).status_code == 403  # en-tête anti-CSRF absent


@pytest.mark.parametrize("path,body", [
    ("/api/dmh/preview", S.buy() | {"mode": "live"}),
    ("/api/dmh/preview", S.buy() | {"private_key": "0x" + "1f" * 32}),
    ("/api/dmh/preview", S.buy() | {"notes": "legal winner thank year wave sausage worth useful legal winner thank yellow"}),
    ("/api/dmh/orders", {"mode": "paper", "preview_id": "x", "request_id": "abcdefgh", "confirm": True, "leverage": 3}),
    ("/api/dmh/orders", {"mode": "real", "preview_id": "x", "request_id": "abcdefgh", "confirm": True}),
    ("/api/dmh/orders", {"mode": "paper", "preview_id": "x", "request_id": "abcdefgh", "confirm": True, "wallet": "0x" + "2" * 40}),
    ("/api/dmh/halt", {"reason": "pas de mode"}),
])
def test_02_the_paper_lock_cannot_be_bypassed_through_the_api(setup, path, body):
    _, c, _ = setup
    r = c.post(path, json=body, headers=H)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "paper_only"


def test_absolute_caps_cannot_be_raised_through_the_api(setup):
    _, c, _ = setup
    r = c.put("/api/dmh/limits", json={"mode": "paper", "limits": {"max_position_eur": 1000}}, headers=H)
    assert r.status_code == 403 and "plafond" in r.json()["detail"]
    r = c.put("/api/dmh/limits", json={"mode": "paper", "limits": {"max_position_eur": 9}}, headers=H)
    assert r.status_code == 200 and r.json()["max_position_eur"] == 9
    c.put("/api/dmh/limits", json={"mode": "paper", "limits": {"max_position_eur": 10}}, headers=H)


def test_end_to_end_paper_trade_on_the_single_shared_portfolio(setup):
    app, c, view = setup
    st = c.get("/api/dmh/status").json()
    assert st["label"] == "SIMULATION — ARGENT FICTIF" and st["paper_only"] and not st["can_sign"] and not st["can_broadcast"]
    aid = app.main_account()["id"]
    broker = app.runtime(aid).broker
    before = broker.balances()["EUR"]
    view.t = int(time.time() * 1000)
    for (venue, pair), b in list(view.books.items()):
        view.set_book(venue, pair, b.mid, min_notional=view.rule[(venue, pair)].min_notional,
                      taker=view.fee[venue]["taker"].value, maker=view.fee[venue]["maker"].value, step=view.rule[(venue, pair)].step)
    pv = c.post("/api/dmh/preview", json=S.buy(n=10.0), headers=H).json()
    assert pv["decision"] == "ROUTE" and pv["verdict"]["allowed"], pv["explanation"]
    rid = "e2e-request-001"
    op = c.post("/api/dmh/orders", json={"mode": "paper", "preview_id": pv["id"], "request_id": rid, "confirm": True}, headers=H).json()
    assert op["state"] == "FILLED", op
    again = c.post("/api/dmh/orders", json={"mode": "paper", "preview_id": pv["id"], "request_id": rid, "confirm": True}, headers=H).json()
    assert again["replayed"] and again["state"] == "FILLED"  # rejouer ne ré-exécute rien
    assert broker.balances()["EUR"] == pytest.approx(before - 10.0, abs=0.01)  # les euros du hub sont ceux du portefeuille principal
    assert c.get("/api/dmh/positions").json()["positions"]
    assert c.post("/api/dmh/reconcile", json={"mode": "paper"}, headers=H).json()["ok"]
    obs = c.get("/api/dmh/observatory").json()
    assert obs["operations"].get("FILLED") == 1 and "note" in obs["savings_validated"]
    assert c.post("/api/dmh/halt", json={"mode": "paper", "reason": "test"}, headers=H).status_code == 200
    blocked = c.post("/api/dmh/preview", json=S.buy(n=10.0), headers=H)
    assert blocked.status_code == 409 and "arrêtées" in blocked.json()["detail"]
    assert c.post("/api/dmh/resume", json={"mode": "paper"}, headers=H).status_code == 200


def test_the_panel_shows_the_simulation_banner_and_registers_the_hub_view(setup):
    _, c, _ = setup
    js = c.get("/js/views/dmh.js").text
    assert "SIMULATION — ARGENT FICTIF" in js and "data-sim-banner" in js and "Arrêt immédiat" in js
    main = c.get("/js/main.js").text
    assert "views/dmh.js" in main and "id: 'dmh'" in main


def test_caps_are_enforced_end_to_end_through_the_api(setup):
    app, c, view = setup
    broker = app.runtime(app.main_account()["id"]).broker
    before = broker.balances()["EUR"]
    view.t = int(time.time() * 1000)
    for (venue, pair), b in list(view.books.items()):
        view.set_book(venue, pair, b.mid, min_notional=view.rule[(venue, pair)].min_notional,
                      taker=view.fee[venue]["taker"].value, maker=view.fee[venue]["maker"].value, step=view.rule[(venue, pair)].step)
    assert c.post("/api/dmh/preview", json=S.buy(n=16.0), headers=H).status_code == 400  # au-delà du plafond absolu
    for n, code in ((12.0, "position"), (9.0, "minimum")):
        pv = c.post("/api/dmh/preview", json=S.buy(n=n), headers=H).json()
        assert code in {v["code"] for v in pv["verdict"]["vetoes"]}, pv["verdict"]
        op = c.post("/api/dmh/orders", json={"mode": "paper", "preview_id": pv["id"], "request_id": f"cap-api-{int(n):03d}", "confirm": True},
                    headers=H).json()
        assert op["state"] == "REJECTED"
    assert broker.balances()["EUR"] == pytest.approx(before)  # rien n'a bougé
