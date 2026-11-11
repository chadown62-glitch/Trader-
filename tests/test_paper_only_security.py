"""Paper-only security contract. Run in an OS-isolated, networkless environment.

These tests deliberately fail against the pre-lockdown implementation. All
exchange objects and credentials below are fakes; no actual exchange is used.
"""

import socket
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.execution.base import OrderError
from sentinelle.execution.live import LiveBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.market.sources import SyntheticSource
from sentinelle.notify.telegram import TelegramService
from sentinelle.services.common import UserError
from sentinelle.services.core import App

from .conftest import market_fn


DENIAL = (OrderError, UserError, PermissionError, RuntimeError)
SECURITY_REASON = r"(?i)(paper|fictif|simul|s.cur|interdit|d.sactiv|bloqu|live|r.el|security|disabled|prohibit|forbid|unknown|inconnu)"
HEADERS = {"X-Sentinelle": "1"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Defense in depth; an OS-level egress block is still mandatory."""
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in paper-only tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def fake_exchange():
    ex = Mock(name="fake_exchange")
    ex.fetch_balance.return_value = {"total": {}, "free": {}}
    ex.create_order.return_value = {}
    return ex


@pytest.fixture
def app(tmp_path, monkeypatch, fake_exchange):
    import sentinelle.services.live_ops as live_ops

    # Must be in place before startup or any restoration of legacy secrets.
    monkeypatch.setattr(live_ops, "make_ccxt_exchange", Mock(return_value=fake_exchange))
    source = SyntheticSource(["BTC/USDC"], days=5)
    instance = App(tmp_path, source=source, start=False, settings_override={
        "market": {"source": "synthetic"},
        "general": {"watchlist": ["BTC/USDC"]},
    })
    yield instance
    instance.db.close()


def assert_no_private(ex):
    for name in ("create_order", "cancel_order", "fetch_balance", "fetch_order"):
        getattr(ex, name).assert_not_called()


def test_live_broker_constructor_refused(db, fake_exchange):
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        LiveBroker(db, 42, "USDC", fake_exchange, market_fn, lambda _: 50000.0)
    assert_no_private(fake_exchange)


@pytest.mark.parametrize("action", ["place", "cancel"])
def test_direct_broker_methods_fail_closed_even_if_object_exists(db, fake_exchange, action):
    # Simulate an object restored or retained before the constructor was locked.
    broker = object.__new__(LiveBroker)
    broker.db, broker.account_id, broker.quote = db, 42, "USDC"
    broker.ex, broker.market_fn, broker.price_fn = fake_exchange, market_fn, lambda _: 50000.0
    broker.clock, broker.fee_rate, broker.listeners = lambda: 1700000000.0, 0.001, []
    broker._balance, broker.last_error = (0.0, None), None
    if action == "cancel":
        db.insert("orders", {"id": 1, "account_id": 42, "symbol": "BTC/USDC", "side": "buy",
                             "type": "limit", "amount": 0.001, "status": "open", "exchange_id": "fake"})
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        if action == "place":
            broker.place_order("BTC/USDC", "buy", "market", 0.001)
        else:
            broker.cancel_order(1)
    assert_no_private(fake_exchange)


def test_startup_legacy_keys_never_connects(app, fake_exchange, monkeypatch):
    import sentinelle.services.live_ops as live_ops

    app.secrets.set({"exchange_api_key": "FAKE-KEY", "exchange_secret": "FAKE-SECRET"})
    app.settings["live"]["testnet"] = False
    # Avoid background agent jobs and market I/O. The actual startup path runs.
    monkeypatch.setattr(app, "_load_markets", lambda: None)
    monkeypatch.setattr(app, "_refresh_watch", lambda: None)
    app._startup()
    live_ops.make_ccxt_exchange.assert_not_called()
    assert_no_private(fake_exchange)


@pytest.fixture
def legacy_runtime(app):
    account_id = app.db.insert("accounts", {"name": "legacy fake", "kind": "live", "role": "live",
                                            "quote": "USDC", "created_at": 1})
    account = app.db.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
    broker = Mock(name="legacy_broker")
    broker.account_id, broker.quote, broker.listeners = account_id, "USDC", []
    broker.market_fn.side_effect = market_fn
    broker.price_fn.return_value = 50000.0
    broker.fee_rate = 0.001
    positions, risk = Mock(name="positions"), Mock(name="risk")
    risk.manual_warnings.return_value = []
    positions.open.return_value = {"id": 7}
    positions.open_positions.return_value = [{"id": 7, "symbol": "BTC/USDC", "origin": "ai"}]
    runtime = SimpleNamespace(account=account, broker=broker, positions=positions, risk=risk)
    app.runtimes[account_id] = runtime
    app.live_status = {"connected": True}
    app.snapshots[account_id] = {"equity": 1000.0}
    return runtime


def test_confirmed_manual_live_order_refused(app, legacy_runtime, monkeypatch):
    monkeypatch.setattr(app.shield, "check_entry", lambda *args: None)
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        app.place_manual_order({"account_id": legacy_runtime.account["id"], "symbol": "BTC/USDC",
                                "side": "buy", "type": "market", "amount": 0.001, "confirm": True})
    legacy_runtime.positions.open.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()


@pytest.mark.parametrize("auto", [False, True])
def test_real_agent_refused_even_when_ready(app, legacy_runtime, monkeypatch, auto):
    monkeypatch.setattr(app.models, "champion", lambda _: object())
    monkeypatch.setattr(app, "readiness", lambda: {"unlocked": True, "criteria": []})
    submit = Mock()
    monkeypatch.setattr(app.jobs, "submit", submit)
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        app.start_agent("ia_reel", confirm="JE COMPRENDS LES RISQUES", auto=auto)
    submit.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()


@pytest.fixture
def client(app):
    with TestClient(create_api(app)) as c:
        response = c.post("/api/auth/setup", json={"password": "fake-test-password"}, headers=HEADERS)
        assert response.status_code == 200
        yield c


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/orders", {"symbol": "BTC/USDC", "side": "buy", "amount": 0.001, "confirm": True}),
    ("delete", "/api/orders/{order}", None),
    ("post", "/api/positions/{position}/close", None),
    ("patch", "/api/positions/{position}", {"stop": 40000}),
    ("post", "/api/emergency", {"action": "flatten", "scope": "all", "confirm": "URGENCE"}),
])
def test_api_does_not_operate_on_legacy_live_account(app, client, legacy_runtime, monkeypatch, method, path, body):
    monkeypatch.setattr(app.shield, "check_entry", lambda *args: None)
    aid = legacy_runtime.account["id"]
    pid = app.db.insert("positions", {"account_id": aid, "symbol": "BTC/USDC", "origin": "ai", "status": "open", "qty": 0.001})
    oid = app.db.insert("orders", {"account_id": aid, "symbol": "BTC/USDC", "side": "sell",
                                   "type": "stop", "amount": 0.001, "status": "open",
                                   "exchange_id": "fake-existing-order", "position_id": pid, "purpose": "stop"})
    existing = app.db.one("SELECT * FROM orders WHERE id = ?", (oid,))
    assert existing and existing["account_id"] == aid and existing["exchange_id"]
    legacy_runtime.broker.get_order.return_value = existing
    legacy_runtime.broker.cancel_order.return_value = existing
    path = path.format(position=pid, order=oid)
    if path == "/api/orders":
        body = {**body, "account_id": aid}
    if method == "delete":
        response = client.delete(path, params={"account_id": aid}, headers=HEADERS)
    else:
        response = getattr(client, method)(path, json=body, headers=HEADERS)
    # Emergency may succeed for paper accounts while excluding the legacy one.
    assert response.status_code in ((200, 400, 403, 409) if path == "/api/emergency" else (400, 403, 409))
    if response.status_code != 200:
        detail = str(response.json().get("detail", "")).lower()
        assert re.search(r"paper|fictif|simul|sécur|secur|interdit|désactiv|desactiv|bloqu|live|réel|reel", detail), detail
    legacy_runtime.positions.open.assert_not_called()
    legacy_runtime.positions.close.assert_not_called()
    legacy_runtime.positions.modify.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()
    legacy_runtime.broker.cancel_order.assert_not_called()


def test_reconnect_and_setting_change_do_not_create_private_client(app, client, fake_exchange):
    import sentinelle.services.live_ops as live_ops

    app.secrets.set({"exchange_api_key": "FAKE-KEY", "exchange_secret": "FAKE-SECRET"})
    r = client.put("/api/settings", json={"live": {"testnet": False}}, headers=HEADERS)
    assert r.status_code in (200, 400, 403, 409)
    client.post("/api/live/connect", headers=HEADERS)
    live_ops.make_ccxt_exchange.assert_not_called()
    assert_no_private(fake_exchange)


def test_retained_live_runtime_cannot_trade_after_settings_change(app, legacy_runtime, monkeypatch):
    monkeypatch.setattr(app.shield, "check_entry", lambda *args: None)
    # A previously connected runtime must not survive a settings transition.
    app.update_settings({"live": {"testnet": True}})
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        app.place_manual_order({"account_id": legacy_runtime.account["id"], "symbol": "BTC/USDC",
                                "side": "buy", "amount": 0.001, "confirm": True})
    legacy_runtime.positions.open.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()


def test_unknown_execution_kind_fails_closed(app, legacy_runtime, monkeypatch):
    monkeypatch.setattr(app.shield, "check_entry", lambda *args: None)
    legacy_runtime.account["kind"] = "unknown"
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        app.place_manual_order({"account_id": legacy_runtime.account["id"], "symbol": "BTC/USDC",
                                "side": "buy", "amount": 0.001, "confirm": True})
    legacy_runtime.positions.open.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()


def test_telegram_emergency_never_closes_legacy_live(app, legacy_runtime, monkeypatch):
    app.settings["telegram"]["chat_id"] = "42"
    telegram = TelegramService(app)
    monkeypatch.setattr(telegram, "_call", lambda *a, **kw: None)
    monkeypatch.setattr(telegram, "send", lambda *a, **kw: True)
    telegram.handle_update({"callback_query": {"id": "fake", "data": "urgence_ok", "message": {"chat": {"id": 42}}}})
    legacy_runtime.positions.close.assert_not_called()
    legacy_runtime.broker.cancel_order.assert_not_called()


@pytest.mark.parametrize("task", ["_poll_live", "_sync_live", "_reconcile_live"])
def test_periodic_tasks_do_not_use_legacy_live_runtime(app, legacy_runtime, task):
    # No restored runtime should be polled, reconciled, or allowed to alter orders.
    app.live_prices = Mock(return_value=50000.0)
    app.live_prices.cache = {"BTC/USDC": (0, 50000.0)}
    legacy_runtime.broker.open_orders.return_value = [{"symbol": "BTC/USDC", "exchange_id": "virtual"}]
    legacy_runtime.broker.reconcile.return_value = []
    getattr(app, task)()
    legacy_runtime.broker.process_bar.assert_not_called()
    legacy_runtime.broker.sync.assert_not_called()
    legacy_runtime.broker.reconcile.assert_not_called()


def test_paper_order_still_updates_fictitious_balances(paper):
    before = paper.balances()
    order = paper.place_order("BTC/USDC", "buy", "market", 0.001)
    after = paper.balances()
    assert order["status"] == "filled"
    assert after["BTC"] == pytest.approx(0.001)
    assert after["USDC"] < before["USDC"]


def retained_live_broker(db, fake_exchange):
    """Legacy object without calling a constructor that must itself be blocked."""
    broker = object.__new__(LiveBroker)
    broker.db, broker.account_id, broker.quote = db, 42, "USDC"
    broker.ex, broker.market_fn, broker.price_fn = fake_exchange, market_fn, lambda _: 50000.0
    broker.clock, broker.fee_rate, broker.listeners = lambda: 1700000000.0, 0.001, []
    broker._balance, broker.last_error = (0.0, None), None
    return broker


def test_reconciliation_with_existing_position_refuses_private_access(db, fake_exchange):
    broker = retained_live_broker(db, fake_exchange)
    pm = Mock()
    pm.open_positions.return_value = [{"id": 1, "symbol": "BTC/USDC", "status": "open",
                                       "qty": 0.001, "stop_price": 45000.0}]
    pm.protective_orders.return_value = []
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        broker.reconcile(pm)
    assert_no_private(fake_exchange)
    pm._place_protections.assert_not_called()


def test_virtual_target_cannot_cancel_stop_or_send_sell(db, fake_exchange):
    broker = retained_live_broker(db, fake_exchange)
    for exchange_id, purpose, price in [("virtual", "target", 51000.0), ("fake-stop", "stop", None)]:
        db.insert("orders", {"account_id": 42, "symbol": "BTC/USDC", "side": "sell",
                             "type": "limit" if purpose == "target" else "stop", "amount": 0.001,
                             "price": price, "stop_price": 45000.0 if purpose == "stop" else None,
                             "status": "open", "exchange_id": exchange_id, "oco_group": "fake-group",
                             "purpose": purpose, "created_at": 1.0, "origin": "ai"})
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        broker.process_bar("BTC/USDC", 50000, 52000, 49000, 51000, 3000)
    assert_no_private(fake_exchange)


def test_stop_replacement_does_not_cancel_existing_live_stop(app, legacy_runtime):
    aid = legacy_runtime.account["id"]
    pid = app.db.insert("positions", {"account_id": aid, "symbol": "BTC/USDC", "origin": "ai",
                                     "status": "open", "qty": 0.001, "entry_price": 50000,
                                     "stop_price": 45000})
    oid = app.db.insert("orders", {"account_id": aid, "position_id": pid, "symbol": "BTC/USDC",
                                   "side": "sell", "type": "stop", "amount": 0.001,
                                   "status": "open", "purpose": "stop", "exchange_id": "fake-stop"})
    # Simulate a retained manager from before the constructor was locked.
    manager = object.__new__(PositionManager)
    manager.db, manager.broker = app.db, legacy_runtime.broker
    manager.account_id, manager.quote = aid, "USDC"
    with pytest.raises(DENIAL, match=SECURITY_REASON):
        manager.modify(pid, stop=46000)
    legacy_runtime.broker.cancel_order.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()
    assert app.db.one("SELECT status FROM orders WHERE id = ?", (oid,))["status"] == "open"


def test_runtime_lookup_failure_does_not_fall_back_to_trading(app, legacy_runtime, monkeypatch):
    def broken_lookup(_account_id):
        raise RuntimeError("injected security lookup failure")

    monkeypatch.setattr(app, "runtime", broken_lookup)
    with pytest.raises(RuntimeError, match="security lookup failure"):
        app.place_manual_order({"account_id": legacy_runtime.account["id"], "symbol": "BTC/USDC",
                                "side": "buy", "amount": 0.001, "confirm": True})
    legacy_runtime.positions.open.assert_not_called()
    legacy_runtime.broker.place_order.assert_not_called()
