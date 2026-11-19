"""Mission 52 — portefeuille partagé : hub et broker fictif principal (réconciliation, concurrence, pertes, redémarrage,
valorisation). Application complète sur un marché simulé, données de marché du hub factices : aucun réseau."""
import logging
import threading
import time

import pytest

from sentinelle.dmh.ledger import Blocked, Ledger, LedgerError, MainAccountCash
from sentinelle.dmh.net import PublicClient
from sentinelle.dmh.service import HubService
from sentinelle.execution.base import OrderError
from sentinelle.execution.paper import PaperBroker
from sentinelle.market.markets import generic_market
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App
from sentinelle.storage.db import Database

from tests import dmh_support as S

logging.disable(logging.CRITICAL)


def make_app(path):
    src = SyntheticSource(["BTC/USDC", "ETH/USDC"], days=40)
    app = App(path, source=src, settings_override={"market": {"source": "synthetic", "poll_seconds": 2, "history_days": 120},
                                                   "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}, "ai": {"timeframe": "1h"},
                                                   "portfolio": {"initial": 30.0}})
    assert app.engine.ready.wait(30)
    return app


def attach(app, view):
    hub = HubService.for_app(app)
    app.dmh = hub
    hub.view_factory = lambda: view
    hub.registry = S.registry()
    hub.risk = S.FakeRisk()
    hub.client = PublicClient(transport=S.no_network)
    return hub


def live_view():
    v = S.market()
    v.t = int(time.time() * 1000)
    for (venue, pair), b in list(v.books.items()):
        v.set_book(venue, pair, b.mid, min_notional=v.rule[(venue, pair)].min_notional, taker=v.fee[venue]["taker"].value,
                   maker=v.fee[venue]["maker"].value, step=v.rule[(venue, pair)].step)
    return v


def go(hub, body, rid):
    pv = hub.preview(body)
    return hub.execute({"mode": "paper", "preview_id": pv["id"], "request_id": rid, "confirm": True})


def test_hub_reconciles_with_the_main_paper_broker_and_detects_divergence(tmp_path):
    app = make_app(tmp_path / "a")
    try:
        hub = attach(app, live_view())
        broker = app.runtime(app.main_account()["id"]).broker
        assert go(hub, S.buy(), "pf-0000001")["state"] == "FILLED"
        pos = hub.positions()["positions"][0]
        sell = hub.preview({"mode": "paper", "side": "sell", "holding_asset": pos["asset"], "holding_location": pos["location"], "fraction": 1})
        assert hub.execute({"mode": "paper", "preview_id": sell["id"], "request_id": "pf-0000002", "confirm": True})["state"] == "FILLED"
        rep = hub.ledger.reconcile()
        assert rep["ok"], rep
        assert hub.ledger.eur_flows() == pytest.approx(broker._total("EUR") - 30.0, abs=1e-9)  # mêmes euros, au centime près
        # écriture parasite dans le grand livre du portefeuille principal : divergence détectée, nouvelles opérations bloquées
        app.db.insert("ledger", {"account_id": broker.account_id, "ts": time.time(), "kind": "hub_frais", "asset": "EUR", "amount": -1.0,
                                 "balance_after": broker._total("EUR"), "note": "test"})
        rep = hub.ledger.reconcile()
        assert not rep["ok"] and any("grand livre" in p for p in rep["problems"])
        with pytest.raises(Blocked):
            hub.preview(S.buy())
    finally:
        app.close()


def test_restart_recovers_reservations_hooks_and_value(tmp_path):
    path = tmp_path / "b"
    app = make_app(path)
    hub = attach(app, live_view())
    assert go(hub, S.buy(), "rs-0000001")["state"] == "FILLED"
    hub.ledger.open_operation("rs-crash-01", origin="user", side="buy", asset="ETH", intent={}, route={}, verdict={}, config_hash="x",
                              evidence_mode="test", reserve=("EUR", "cash", 5.0))  # arrêt brutal avant toute exécution
    broker = app.runtime(app.main_account()["id"]).broker
    assert broker.available("EUR") == pytest.approx(broker._total("EUR") - 5.0)
    app.close()
    app2 = make_app(path)
    try:
        hub2 = attach(app2, live_view())
        assert hub2.recovered["abandoned"] == 1 and hub2.ledger.operation("rs-crash-01")["state"] == "ABANDONED"
        b2 = app2.runtime(app2.main_account()["id"]).broker
        assert b2.external_reserved is not None and b2.external_value is not None
        assert b2.available("EUR") == pytest.approx(b2._total("EUR"))  # réservation libérée après le redémarrage
        equity = b2.equity()[0]
        assert equity == pytest.approx(30.0, abs=0.1)  # 20 € + position du hub (valeur prudente ou coût)
        assert hub2.ledger.reconcile()["ok"]
    finally:
        app2.close()


def test_concurrent_broker_orders_and_hub_reservations_never_overcommit(tmp_path):
    db = Database(tmp_path / "c.db")
    db.execute("INSERT INTO accounts(id, name, kind, role, quote, initial_balance, created_at, data) VALUES (1,'P','paper','main','EUR',30,0,'{}')")
    db.execute("INSERT INTO balances(account_id, asset, total) VALUES (1, 'EUR', 30.0)")
    broker = PaperBroker(db, 1, "EUR", lambda s: generic_market(s), lambda s: 60_000.0, 0.001, 5.0)
    led = Ledger(db, 1)
    led.cash = MainAccountCash(lambda: broker)
    broker.external_reserved = led.reserved_by_asset
    broker.external_value = led.liquidation_value
    start = threading.Barrier(8)
    outcomes = []

    def hub(i):
        start.wait()
        try:
            led.open_operation(f"hub-{i:04d}", origin="user", side="buy", asset="BTC", intent={}, route={}, verdict={}, config_hash="h",
                               evidence_mode="test", reserve=("EUR", "cash", 7.0))
            outcomes.append("hub")
        except LedgerError:
            pass

    def agent(i):
        start.wait()
        try:
            broker.place_order("BTC/EUR", "buy", "limit", 0.00011, 59_000.0)  # ≈ 6,5 € réservés
            outcomes.append("broker")
        except OrderError:
            pass
    ts = [threading.Thread(target=hub if i % 2 else agent, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    reserved = broker.reserved().get("EUR", 0.0)
    assert reserved <= 30.0 + 1e-9 and broker.available("EUR") >= 0.0 and len(outcomes) >= 3
    db.close()


def test_losses_trigger_the_daily_loss_limit():
    v = S.market()
    d = Database(":memory:")
    hub = S.service(d, v)
    assert hub.preview(S.buy())["verdict"]["allowed"]  # première lecture du jour : valeur de départ 30 €
    assert go(hub, S.buy(), "loss-00001")["state"] == "FILLED"
    v.set_book("binance", "BTC/EUR", 42_000.0, min_notional=5.0)  # −30 %
    hub._mark(v)
    pf = hub.portfolio()
    assert pf.day_start_equity - pf.equity >= 1.5
    pv = hub.preview(S.buy("ETH"))
    assert "perte_jour" in {x["code"] for x in pv["verdict"]["vetoes"]}


def test_valuation_errors_are_refused_or_handled_conservatively(tmp_path):
    hub = S.service(Database(tmp_path / "v.db"), S.market())
    assert go(hub, S.buy(), "val-000001")["state"] == "FILLED"
    h = hub.ledger.holdings()[0]
    for bad in (float("nan"), float("inf"), -1.0):
        with pytest.raises(LedgerError):
            hub.ledger.set_mark(h["asset"], h["location"], bad, "test")
    hub.db.execute("INSERT OR REPLACE INTO dmh_marks VALUES (?, ?, ?, ?, ?, ?)", (1, h["asset"], h["location"], -5.0, hub.ledger.now_ms(), "trafiqué"))
    v = hub.ledger.valuation()[0]
    assert v["value_eur"] == pytest.approx(h["cost_eur"]) and v["stale"]  # valeur invalide ignorée : coût, signalé
    hub.db.execute("UPDATE dmh_marks SET value_eur = ?, ts_ms = ?", (h["cost_eur"] * 1.5, hub.ledger.now_ms() - 3 * 86_400_000))
    v = hub.ledger.valuation()[0]
    assert v["value_eur"] == pytest.approx(h["cost_eur"]) and v["stale"]  # ancienne : jamais au-dessus du coût
    hub.db.execute("UPDATE dmh_marks SET value_eur = ?, ts_ms = ?", (h["cost_eur"] * 0.5, hub.ledger.now_ms() - 3 * 86_400_000))
    assert hub.ledger.valuation()[0]["value_eur"] == pytest.approx(h["cost_eur"] * 0.5)  # ancienne et plus basse : retenue
