"""EUR principal account: isolated simulation, migration and ledger invariants."""
import threading

import pytest

from sentinelle.execution.base import OrderError
from sentinelle.execution.paper import PaperBroker
from sentinelle.market.markets import MarketInfo
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.common import UserError
from sentinelle.services.core import App
from sentinelle.storage.db import Database


@pytest.fixture
def eur_app(tmp_path):
    source = SyntheticSource(["BTC/EUR"], days=5)
    app = App(tmp_path, source=source, start=False, settings_override={
        "market": {"source": "synthetic"},
        "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]},
    })
    app._startup()
    app.hub.prices["BTC/EUR"] = 50000.0
    app.hub.price_ts["BTC/EUR"] = source.now_ms() / 1000
    app.hub.price_source_ts["BTC/EUR"] = source.now_ms() / 1000
    yield app
    app.db.close()


def principal(app):
    return app.db.one("SELECT * FROM accounts WHERE role = 'principal' AND archived = 0")


def test_new_principal_is_exactly_30_eur_shared_by_agent(eur_app):
    account = principal(eur_app)
    assert account["kind"] == "paper" and account["quote"] == "EUR"
    assert account["initial_balance"] == 30.0
    assert eur_app.runtime(account["id"]).broker.balances() == {"EUR": 30.0}
    assert eur_app.agents_status()[0]["account_id"] == account["id"]
    assert eur_app.db.scalar("SELECT COUNT(*) FROM accounts WHERE role = 'principal'", (), 0) == 1


def test_manual_trade_and_ai_use_same_account(eur_app, monkeypatch):
    acc = principal(eur_app)
    monkeypatch.setattr(eur_app.shield, "check_entry", lambda *args: None)
    result = eur_app.place_manual_order({"account_id": acc["id"], "symbol": "BTC/EUR", "side": "buy", "amount": 0.0002})
    assert result["position"]["account_id"] == acc["id"]
    assert eur_app.agents_status()[0]["account_id"] == acc["id"]
    assert eur_app.runtime(acc["id"]).broker.balances()["EUR"] < 30


def test_incompatible_currency_refused_without_implicit_conversion(eur_app):
    broker = eur_app.runtime(principal(eur_app)["id"]).broker
    before = broker.balances()
    for symbol in ("BTC/USDC", "BTC/USD", "BTC/USDT"):
        with pytest.raises(OrderError, match="conversion"):
            broker.place_order(symbol, "buy", "market", 0.0002)
    assert broker.balances() == before


def test_missing_and_stale_price_cannot_value_or_execute(eur_app):
    broker = eur_app.runtime(principal(eur_app)["id"]).broker
    broker.place_order("BTC/EUR", "buy", "market", 0.0002)
    eur_app.hub.price_ts["BTC/EUR"] -= 500
    with pytest.raises(OrderError, match="Valorisation impossible"):
        broker.equity()
    with pytest.raises(OrderError, match="Prix indisponible"):
        broker.place_order("BTC/EUR", "buy", "market", 0.0002)


def test_polling_old_candle_does_not_refresh_an_expired_price(eur_app, monkeypatch):
    broker = eur_app.runtime(principal(eur_app)["id"]).broker
    now = eur_app.hub.source.now_ms()
    eur_app.hub.price_ts["BTC/EUR"] = now / 1000 - 500
    old_candle = [now - 600_000, 50000, 51000, 49000, 50000, 1]
    monkeypatch.setattr(eur_app.hub.source, "fetch_ohlcv", lambda *args, **kwargs: [old_candle])
    eur_app.hub.poll()
    assert not eur_app.hub.is_fresh("BTC/EUR", 180)
    with pytest.raises(OrderError, match="Prix indisponible"):
        broker.place_order("BTC/EUR", "buy", "market", 0.0002)


@pytest.mark.parametrize("action", ["close", "modify"])
def test_stale_price_does_not_remove_position_protection(eur_app, action):
    rt = eur_app.runtime(principal(eur_app)["id"])
    position = rt.positions.open("BTC/EUR", 0.0002, origin="manual", stop=45000)
    before = rt.positions.protective_orders(position["id"])
    assert before and before[0]["type"] == "stop"
    eur_app.hub.price_ts["BTC/EUR"] -= 500
    with pytest.raises(OrderError, match="Prix indisponible"):
        if action == "close":
            rt.positions.close(position["id"])
        else:
            rt.positions.modify(position["id"], stop=44000)
    assert rt.positions.get(position["id"])["status"] == "open"
    assert rt.positions.protective_orders(position["id"]) == before


def test_failed_stop_replacement_restores_previous_protection(eur_app, monkeypatch):
    rt = eur_app.runtime(principal(eur_app)["id"])
    position = rt.positions.open("BTC/EUR", 0.0002, origin="manual", stop=45000)
    before = rt.positions.protective_orders(position["id"])
    original = rt.broker.place_order
    def broken(*args, **kwargs):
        if kwargs.get("purpose") == "stop":
            raise OrderError("Échec fictif de pose du stop")
        return original(*args, **kwargs)
    monkeypatch.setattr(rt.broker, "place_order", broken)
    with pytest.raises(OrderError, match="stop"):
        rt.positions.modify(position["id"], stop=44000)
    assert rt.positions.get(position["id"])["stop_price"] == 45000
    assert rt.positions.protective_orders(position["id"]) == before


def test_failed_exit_preserves_open_position_and_stop(eur_app, monkeypatch):
    rt = eur_app.runtime(principal(eur_app)["id"])
    position = rt.positions.open("BTC/EUR", 0.0002, origin="manual", stop=45000)
    before = rt.positions.protective_orders(position["id"])
    original = rt.broker.place_order
    def broken(*args, **kwargs):
        if kwargs.get("purpose") == "exit":
            raise OrderError("Échec fictif de vente")
        return original(*args, **kwargs)
    monkeypatch.setattr(rt.broker, "place_order", broken)
    with pytest.raises(OrderError, match="vente"):
        rt.positions.close(position["id"])
    assert rt.positions.get(position["id"])["status"] == "open"
    assert rt.positions.protective_orders(position["id"]) == before


def test_unsellable_position_is_not_falsely_marked_closed(eur_app):
    rt = eur_app.runtime(principal(eur_app)["id"])
    position = rt.positions.open("BTC/EUR", 0.0002, origin="manual", stop=45000)
    before = rt.positions.protective_orders(position["id"])
    eur_app.hub.markets["BTC/EUR"] = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=100)
    with pytest.raises(OrderError, match="minimum"):
        rt.positions.close(position["id"])
    assert rt.positions.get(position["id"])["status"] == "open"
    assert rt.positions.protective_orders(position["id"]) == before


def test_legacy_accounts_and_orders_are_preserved_and_inert(tmp_path):
    db = Database(tmp_path / "sentinelle.db")
    old = db.insert("accounts", {"name": "legacy", "kind": "paper", "role": "ai_training", "quote": "USDC", "initial_balance": 10000})
    db.insert("balances", {"account_id": old, "asset": "USDC", "total": 7777})
    oid = db.insert("orders", {"account_id": old, "symbol": "BTC/USDC", "side": "buy", "type": "limit", "amount": 0.1, "price": 40000, "status": "open"})
    db.execute("INSERT INTO agents(name, account_id, enabled) VALUES (?, ?, 1)", ("ia_entrainement", old))
    before = db.one("SELECT * FROM accounts WHERE id = ?", (old,))
    db.close()
    source = SyntheticSource(["BTC/EUR"], days=5)
    app = App(tmp_path, source=source, start=False, settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}})
    app._startup()
    assert app.db.one("SELECT * FROM accounts WHERE id = ?", (old,)) == before
    assert app.db.scalar("SELECT total FROM balances WHERE account_id = ? AND asset = 'USDC'", (old,)) == 7777
    assert app.db.one("SELECT * FROM orders WHERE id = ?", (oid,))["status"] == "open"
    assert app.db.one("SELECT enabled FROM agents WHERE name = 'ia_entrainement'")["enabled"] == 0
    assert app.account_summary(old)["legacy"] is True
    with pytest.raises(UserError, match="lecture seule"):
        app.runtime(old)
    assert old not in app.runtimes
    first = principal(app)["id"]
    app.db.close()
    reopened = App(tmp_path, source=source, start=False, settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}})
    assert principal(reopened)["id"] == first
    assert reopened.db.scalar("SELECT total FROM balances WHERE account_id = ? AND asset = 'EUR'", (first,)) == 30
    reopened.db.close()


def test_completed_trade_balances_and_history_survive_restart(tmp_path):
    source = SyntheticSource(["BTC/EUR"], days=5)
    settings = {"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}}
    app = App(tmp_path, source=source, start=False, settings_override=settings)
    app._startup()
    app.hub.poll()
    account = principal(app)
    broker = app.runtime(account["id"]).broker
    broker.place_order("BTC/EUR", "buy", "market", 0.0002)
    before = broker.balances()
    fills = app.db.query("SELECT * FROM fills WHERE account_id = ?", (account["id"],))
    assert fills and before["EUR"] < 30 and before["BTC"] > 0
    app.db.close()
    restored = App(tmp_path, source=source, start=False, settings_override=settings)
    restored._startup()
    assert principal(restored)["id"] == account["id"]
    assert restored.runtime(account["id"]).broker.balances() == before
    assert restored.db.query("SELECT * FROM fills WHERE account_id = ?", (account["id"],)) == fills
    restored.db.close()


def test_history_loader_does_not_splice_usdt_prices_into_eur(eur_app, monkeypatch):
    from .helpers import make_series

    eur_app.hub.markets["BTC/USDT"] = MarketInfo("BTC/USDT", "BTC", "USDT")
    calls = []
    def load(symbol, timeframe, days):
        calls.append(symbol)
        return make_series(100, seed=2, start=50000)
    monkeypatch.setattr(eur_app.history, "update", load)
    eur_app.settings["ai"]["extended_history"] = True
    eur_app._histories(eur_app.settings, "1h", 30, lambda *_: None)
    assert calls == ["BTC/EUR"]


def test_default_backtest_starts_at_30_without_touching_principal(eur_app):
    from sentinelle.backtest.engine import run_backtest

    account = principal(eur_app)
    before = eur_app.runtime(account["id"]).broker.balances()
    result = run_backtest({}, "1h", eur_app.settings, None)
    assert result["initial"] == 30 and result["final_equity"] == 30
    assert eur_app.runtime(account["id"]).broker.balances() == before


def test_reset_rejects_arbitrary_funding(eur_app):
    account = principal(eur_app)
    with pytest.raises(UserError, match="30"):
        eur_app.reset_paper_account(account["id"], 10000)
    assert eur_app.runtime(account["id"]).broker.balances() == {"EUR": 30.0}


def test_two_concurrent_orders_cannot_spend_the_same_euros(db):
    aid = db.insert("accounts", {"name": "test", "kind": "paper", "role": "principal", "quote": "EUR", "initial_balance": 30})
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    prices = lambda _: 50000.0
    brokers = [PaperBroker(db, aid, "EUR", lambda _: market, prices) for _ in range(2)]
    brokers[0].set_balance("EUR", 30)
    barrier, outcomes = threading.Barrier(2), []
    def buy(broker):
        barrier.wait()
        try:
            outcomes.append(broker.place_order("BTC/EUR", "buy", "market", 0.0004)["status"])
        except OrderError as exc:
            outcomes.append(exc.code)
    threads = [threading.Thread(target=buy, args=(broker,)) for broker in brokers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["filled", "insufficient_funds"]
    balances = brokers[0].balances()
    assert balances["EUR"] >= 0 and balances["BTC"] == pytest.approx(0.0004)


def test_limit_reservations_prevent_overspending(db):
    aid = db.insert("accounts", {"name": "test", "kind": "paper", "role": "principal", "quote": "EUR"})
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    broker = PaperBroker(db, aid, "EUR", lambda _: market, lambda _: 50000.0)
    broker.set_balance("EUR", 30)
    first = broker.place_order("BTC/EUR", "buy", "limit", 0.0004, price=49000)
    assert first["status"] == "open"
    assert broker.available("EUR") < 11
    with pytest.raises(OrderError, match="insuffisants"):
        broker.place_order("BTC/EUR", "buy", "limit", 0.0004, price=49000)
    broker.cancel_order(first["id"])
    assert broker.available("EUR") == pytest.approx(30)


def test_entry_and_stop_gap_charge_costs_and_conserve_cash(db):
    aid = db.insert("accounts", {"name": "test", "kind": "paper", "role": "principal", "quote": "EUR"})
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    broker = PaperBroker(db, aid, "EUR", lambda _: market, lambda _: 50000.0,
                         fee_rate=0.001, slippage_bps=5, clock=lambda: 1700000000.0)
    broker.set_balance("EUR", 30)
    buy = broker.place_order("BTC/EUR", "buy", "market", 0.0002)
    stop = broker.place_order("BTC/EUR", "sell", "stop", 0.0002, stop_price=45000)
    assert buy["avg_price"] == pytest.approx(50000 * 1.0005)
    assert broker.process_bar("BTC/EUR", 40000, 41000, 39000, 40000, 1700000001000) == 1
    stop = broker.get_order(stop["id"])
    assert stop["avg_price"] == pytest.approx(40000 * 0.9995)
    expected = 30 - 0.0002 * buy["avg_price"] * 1.001 + 0.0002 * stop["avg_price"] * 0.999
    assert broker.balances().get("BTC", 0) == pytest.approx(0)
    assert broker.balances()["EUR"] == pytest.approx(expected)


def test_buy_stop_gap_larger_than_cash_is_rejected_and_releases_reservation(db):
    aid = db.insert("accounts", {"name": "test", "kind": "paper", "role": "principal", "quote": "EUR"})
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    broker = PaperBroker(db, aid, "EUR", lambda _: market, lambda _: 50000.0, clock=lambda: 1700000000.0)
    broker.set_balance("EUR", 30)
    stop = broker.place_order("BTC/EUR", "buy", "stop", 0.0005, stop_price=55000)
    assert broker.available("EUR") < 3
    assert broker.process_bar("BTC/EUR", 70000, 71000, 69000, 70000, 1700000001000) == 0
    assert broker.get_order(stop["id"])["status"] == "rejected"
    assert broker.available("EUR") == pytest.approx(30)
    assert broker.balances() == {"EUR": 30}


def test_inconsistent_candle_cannot_trigger_pending_order(db):
    aid = db.insert("accounts", {"name": "test", "kind": "paper", "role": "principal", "quote": "EUR"})
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    broker = PaperBroker(db, aid, "EUR", lambda _: market, lambda _: 50000.0, clock=lambda: 1700000000.0)
    broker.set_balance("EUR", 30)
    order = broker.place_order("BTC/EUR", "buy", "limit", 0.0002, price=49000)
    for bar in ((48000, 45000, 47000, 49000), (float("nan"), 50000, 47000, 49000)):
        with pytest.raises(OrderError, match="Bougie incohérente"):
            broker.process_bar("BTC/EUR", *bar, 1700000001000)
    assert broker.get_order(order["id"])["status"] == "open"
    assert broker.balances() == {"EUR": 30}


def test_concurrent_bar_processing_cannot_fill_same_order_twice(db, monkeypatch):
    aid = db.insert("accounts", {"name": "test", "kind": "paper", "role": "principal", "quote": "EUR"})
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    broker = PaperBroker(db, aid, "EUR", lambda _: market, lambda _: 50000.0, clock=lambda: 1700000000.0)
    broker.set_balance("EUR", 30)
    order = broker.place_order("BTC/EUR", "buy", "limit", 0.0002, price=49000)
    barrier = threading.Barrier(2)
    original = broker._fill
    def simultaneous_fill(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(broker, "_fill", simultaneous_fill)
    outcomes = []
    def process():
        outcomes.append(broker.process_bar("BTC/EUR", 48000, 50000, 47000, 49000, 1700000001000))
    threads = [threading.Thread(target=process) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == [0, 1]
    assert db.scalar("SELECT COUNT(*) FROM fills WHERE order_id = ?", (order["id"],)) == 1
    assert broker.balances()["BTC"] == pytest.approx(0.0002)
