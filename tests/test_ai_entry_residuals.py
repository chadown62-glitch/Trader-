"""Fills de la session du cockpit sur des carnets datés synthétiques ; aucun appel externe."""
import pytest

from sentinelle.execution.base import OrderError
from sentinelle.market.markets import MarketInfo
from sentinelle.services.core import App
from tests.test_wallet_book_execution import check_ledger

SYMBOL = "BTC/EUR"
AGENT = "ia_entrainement"


class RecordedSource:
    name = "binance"

    def __init__(self):
        self.now = 1_700_000_000
        self.ask_qty = 0.05
        self.bid = 99

    def now_ms(self):
        return self.now * 1000

    def load_markets(self):
        return {SYMBOL: MarketInfo(SYMBOL, "BTC", "EUR", amount_step=0.001, min_notional=1)}

    def fetch_order_book(self, symbol, limit):
        return {"timestamp": self.now_ms(), "asks": [[100, self.ask_qty]], "bids": [[self.bid, 1]]}


def open_app(path, source, enabled=True):
    settings = {"market": {"source": "binance", "cross_check": enabled},
                "general": {"quote": "EUR", "watchlist": [SYMBOL]}}
    app = App(path, source=source, start=False, settings_override=settings)
    app.hub.load_markets()
    app.hub.prices[SYMBOL] = 100
    app.hub.price_ts[SYMBOL] = app.hub.price_source_ts[SYMBOL] = source.now
    rt = app.runtime(app.main_account()["id"])
    return app, rt.positions, rt.broker


@pytest.mark.parametrize("enabled,origin,agent,cancelled", [
    (True, "ai", AGENT, True),
    (True, "manual", None, False),
    (True, "ai", "prospective-frozen", False),
    (False, "ai", AGENT, False),
])
def test_one_book_scope(tmp_path, enabled, origin, agent, cancelled):
    source = RecordedSource()
    app, positions, broker = open_app(tmp_path, source, enabled)
    try:
        pos = positions.open(SYMBOL, 0.1, origin=origin, agent=agent, stop=90)
        buy = app.db.one("SELECT * FROM orders WHERE purpose='entry'")
        assert buy["filled"] == pytest.approx(0.05)
        assert buy["status"] == ("cancelled" if cancelled else "partially_filled")
        if cancelled:
            assert "UNVERIFIED" in buy["error"]
            assert broker.reserved().get("EUR", 0) == 0
        source.now += 1
        broker.process_book(SYMBOL)
        assert positions.get(pos["id"])["qty"] == pytest.approx(0.05 if cancelled else 0.1)
        source.bid = 85
        source.now += 1
        assert broker.process_book(SYMBOL) == 1
        assert positions.get(pos["id"])["status"] == "closed"
        check_ledger(app.db, broker)
    finally:
        app.close()


def test_restart_cancels_an_older_partial_once_before_any_further_fill(tmp_path):
    source = RecordedSource()
    app, positions, _ = open_app(tmp_path, source, enabled=False)
    try:
        pos = positions.open(SYMBOL, 0.1, origin="ai", agent=AGENT, stop=90)
        assert pos["qty"] == pytest.approx(0.05)
        buy_id = app.db.scalar("SELECT id FROM orders WHERE purpose='entry'")
        prior_fills = app.db.query("SELECT * FROM fills")
    finally:
        app.close()
    source.now += 1
    app, positions, broker = open_app(tmp_path, source, enabled=True)
    try:
        assert broker.process_book(SYMBOL) == broker.process_book(SYMBOL) == 0
        assert broker.get_order(buy_id)["status"] == "cancelled"
        assert positions.get(pos["id"])["qty"] == pytest.approx(0.05)
        assert app.db.query("SELECT * FROM fills") == prior_fills
        assert broker.reserved() == {"BTC": pytest.approx(0.05)}
        check_ledger(app.db, broker)
    finally:
        app.close()


def test_no_depth_refuses_entry_without_recording_a_position_as_taken(tmp_path):
    source = RecordedSource()
    app, positions, broker = open_app(tmp_path, source)
    try:
        positions.open(SYMBOL, 0.05, origin="manual")  # consomme la profondeur disponible
        with pytest.raises(OrderError, match="UNVERIFIED") as err:
            positions.open(SYMBOL, 0.05, origin="ai", agent=AGENT)
        assert err.value.code == "entry_cancelled"
        assert app.db.scalar("SELECT status FROM positions WHERE agent=?", (AGENT,)) == "cancelled"
        fills = app.db.query("SELECT * FROM fills")
        assert len(fills) == 1
        source.now += 1
        assert broker.process_book(SYMBOL) == 0
        assert app.db.query("SELECT * FROM fills") == fills
        check_ledger(app.db, broker)
    finally:
        app.close()
