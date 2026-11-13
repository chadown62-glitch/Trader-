from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

import pytest

from sentinelle.execution.base import OrderError
from sentinelle.execution.liquidity import BookSnapshot
from sentinelle.execution.paper import PaperBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.market.markets import MarketInfo
from sentinelle.storage.db import Database


def snapshot(at, asks=((100, ".07"), (105, ".04")), bids=((95, ".20"), (90, ".20"))):
    return BookSnapshot(bids, asks, at, "replay-l2")


def wallet(path):
    db = Database(path)
    if not db.one("SELECT id FROM accounts"):
        db.insert("accounts", {"name": "euro", "kind": "paper", "role": "main", "quote": "EUR", "initial_balance": 30})
    clock = [1700000000.]
    current = [snapshot(clock[0])]
    market = MarketInfo("ABC/EUR", "ABC", "EUR", amount_step=.001, min_amount=.001, min_notional=1)
    broker = PaperBroker(db, 1, "EUR", lambda _: market, lambda _: 100, fee_rate=.001,
                         clock=lambda: clock[0], book_fn=lambda _: current[0])
    if not db.one("SELECT * FROM balances"):
        broker.set_balance("EUR", 30, "depot")
    return db, broker, PositionManager(db, broker, clock=broker.clock), clock, current


def check_ledger(db, broker):
    for row in db.query("SELECT asset, SUM(amount) AS total FROM ledger GROUP BY asset"):
        assert broker._total(row["asset"]) == pytest.approx(row["total"], abs=1e-10)
        assert row["total"] >= -1e-12


def test_partial_limit_fills_restart_and_consumed_depth(tmp_path):
    path = tmp_path / "book.db"
    db, b, pm, t, books = wallet(path)
    pos = pm.open("ABC/EUR", .2, origin="manual", entry_type="limit", limit_price=106, stop=80)
    order = db.one("SELECT * FROM orders WHERE purpose='entry'")
    assert pos["status"] == "open" and pos["qty"] == pytest.approx(.11)
    assert order["status"] == "partially_filled" and order["avg_price"] == pytest.approx(11.2/.11)
    assert b.reserved()["EUR"] == pytest.approx(.09 * 106 * (1 + b.fee_rate + b.slip))
    assert b.reserved()["ABC"] == pytest.approx(.11)
    assert db.scalar("SELECT COUNT(*) FROM fills") == 2
    assert b._total("EUR") == pytest.approx(30 - 11.2 * 1.001)
    check_ledger(db, b)
    db.close()
    db, b, pm, t, books = wallet(path)
    assert b.process_book("ABC/EUR") == 0
    t[0] += 1
    books[0] = snapshot(t[0], asks=((101, ".10"),))
    assert b.process_book("ABC/EUR") == 1
    assert pm.get(pos["id"])["qty"] == pytest.approx(.2)
    assert b.get_order(order["id"])["status"] == "filled"
    assert b.reserved() == {"ABC": pytest.approx(.2)}
    fills = db.query("SELECT * FROM fills ORDER BY id")
    assert [(r["price"], r["amount"]) for r in fills] == [(100, .07), (105, .04), (101, .09)]
    assert all(r["price_source"] == "replay-l2" and r["price_ts"] <= r["ts"] for r in fills)
    check_ledger(db, b)
    db.close()


def test_partial_stop_gap_cancels_entry_and_preserves_sibling(tmp_path):
    db, b, pm, t, books = wallet(tmp_path / "book.db")
    pos = pm.open("ABC/EUR", .2, origin="manual", stop=90, target=120)
    t[0] += 1
    books[0] = snapshot(t[0], bids=((70, ".04"),), asks=((110, 2),))
    assert b.process_book("ABC/EUR") == 1
    assert pm.get(pos["id"])["qty"] == pytest.approx(.07)
    assert db.scalar("SELECT status FROM orders WHERE purpose='entry'") == "cancelled"
    assert b.reserved() == {"ABC": pytest.approx(.07)}
    t[0] += 1
    books[0] = snapshot(t[0], bids=((94, ".2"),), asks=((102, 2),))
    assert b.process_book("ABC/EUR") == 1  # trigger persists through a rebound
    assert pm.get(pos["id"])["status"] == "closed" and not b.open_orders()
    assert b._total("EUR") == pytest.approx(30 - 11.2*1.001 + (.04*70 + .07*94)*.999)
    check_ledger(db, b)
    db.close()


def test_expiry_and_cancel_release_only_unfilled_cash(tmp_path):
    db, b, pm, t, books = wallet(tmp_path / "book.db")
    pos = pm.open("ABC/EUR", .16, origin="manual", entry_type="limit", limit_price=102, expires_at=t[0]+2)
    assert pm.get(pos["id"])["qty"] == pytest.approx(.07)
    t[0] += 2
    b.process_book("ABC/EUR")
    o = db.one("SELECT * FROM orders WHERE purpose='entry'")
    assert o["status"] == "expired" and o["filled"] == .07
    assert not b.reserved() and b.cancel_order(o["id"])["status"] == "expired"
    check_ledger(db, b)
    db.close()


def test_concurrent_orders_share_persistent_depth(tmp_path):
    db, b, _, t, books = wallet(tmp_path / "book.db")
    with ThreadPoolExecutor(2) as pool:
        orders = list(pool.map(lambda _: b.place_order("ABC/EUR", "buy", "limit", .12, price=106), range(2)))
    assert sum(o["filled"] for o in orders) == pytest.approx(.11)
    assert b.available("EUR") >= 0 and 0 < b.reserved()["EUR"] < b._total("EUR")
    before = b.balances()
    books[0] = snapshot(t[0], asks=((100, "100"),))
    with pytest.raises(OrderError, match="même horodatage"):
        b.process_book("ABC/EUR")
    assert b.balances() == before
    t[0] += 70
    with pytest.raises(OrderError, match="périmé"):
        b.process_book("ABC/EUR")
    check_ledger(db, b)
    db.close()


def test_process_death_during_fill_rolls_back_wallet_and_depth(tmp_path):
    path = tmp_path / "crash.db"
    db, *_ = wallet(path)
    db.close()
    script = """
import os, sys
from tests.test_wallet_book_execution import wallet
db, b, pm, t, books = wallet(sys.argv[1])
b.listeners.append(lambda *args: os._exit(23))
pm.open("ABC/EUR", .12, origin="manual")
"""
    child = subprocess.run([sys.executable, "-c", script, str(path)], check=False)
    assert child.returncode == 23
    db, b, pm, t, books = wallet(path)
    assert b.balances() == {"EUR": 30} and not b.open_orders()
    for table in ("positions", "fills", "paper_books", "paper_book_events"):
        assert db.scalar(f"SELECT COUNT(*) FROM {table}") == 0
    assert pm.open("ABC/EUR", .12, origin="manual")["qty"] == pytest.approx(.11)
    check_ledger(db, b)
    db.close()


def test_application_manual_orders_use_the_public_book_adapter(tmp_path):
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.core import App
    from sentinelle.services.common import UserError

    class RecordedMarket:
        name = "enregistrement public local"
        def __init__(self):
            self.clock = lambda: 1700000000.
            self.recording = SyntheticSource(["BTC/EUR"], days=2, clock=self.clock)
            self.book_time = int(self.clock()*1000)
        def now_ms(self):
            return self.recording.now_ms()
        def load_markets(self):
            return {"BTC/EUR":MarketInfo("BTC/EUR","BTC","EUR",amount_step=.001,min_notional=1)}
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
            return [[r[0],100.,101.,99.,100.,r[5]] for r in self.recording.fetch_ohlcv(symbol,timeframe,since,limit)]
        def fetch_order_book(self, symbol, limit=20):
            return {"bids":[[99.,.08]],"asks":[[101.,.07]],"timestamp":self.book_time}

    source = RecordedMarket()
    app = App(tmp_path, source=source, start=False, settings_override={"market":{"source":"synthetic"},
                                                                       "general":{"quote":"EUR","watchlist":["BTC/EUR"]}})
    try:
        app._startup()
        app.hub.poll()
        account = app.main_account()["id"]
        result = app.place_manual_order({"account_id":account,"symbol":"BTC/EUR","side":"buy","amount":.12})
        assert result["position"]["qty"] == pytest.approx(.07)
        fill = app.db.one("SELECT * FROM fills")
        assert fill["price"] == 101 and fill["price_source"] == source.name and fill["price_ts"] == source.clock()
        before = app.runtime(account).broker.balances()
        source.book_time = None
        with pytest.raises(UserError, match="Carnet"):
            app.place_manual_order({"account_id":account,"symbol":"BTC/EUR","side":"buy","amount":.05})
        assert app.runtime(account).broker.balances() == before and app.ledger_check(account)["ok"]
    finally:
        app.close()


def test_exchange_identity_preserves_consumed_depth_after_new_receipt_and_restart(tmp_path):
    path=tmp_path/'book.db'
    db,b,pm,t,books=wallet(path)
    books[0]=BookSnapshot(((95,'.2'),),((100,'.07'),),t[0],'public-l2',42)
    pos=pm.open('ABC/EUR',.2,origin='manual',entry_type='limit',limit_price=106,stop=80)
    assert pm.get(pos['id'])['qty']==pytest.approx(.07)
    n=db.scalar('SELECT COUNT(*) FROM fills');db.close()
    db,b,pm,t,books=wallet(path)
    t[0]+=1
    books[0]=BookSnapshot(((95,'.2'),),((100,'.07'),),t[0],'public-l2',42)
    assert b.process_book('ABC/EUR')==0
    assert db.scalar('SELECT COUNT(*) FROM fills')==n
    books[0]=BookSnapshot(((95,'.2'),),((100,'.07'),),t[0]+1,'public-l2',43)
    t[0]+=1
    assert b.process_book('ABC/EUR')==1
    check_ledger(db,b);db.close()
