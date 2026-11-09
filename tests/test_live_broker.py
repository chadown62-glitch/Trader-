"""Tests du broker réel contre un faux exchange qui imite le comportement de Binance spot."""

import itertools

import ccxt
import pytest

from sentinelle.execution.base import OrderError
from sentinelle.execution.live import LiveBroker
from sentinelle.execution.positions import PositionManager

from .conftest import market_fn


class FakeExchange:
    """Exchange spot minimal : soldes bloqués par ordre, frais d'achat prélevés en crypto."""

    def __init__(self, prices, fee=0.001):
        self.prices = prices
        self.fee = fee
        self.total = {"USDC": 10_000.0}
        self.locked = {}
        self.orders = {}
        self.ids = itertools.count(1)
        self.fail_next_create = None  # exception à lever au prochain create_order
        self.calls = []

    # -- utilitaires --
    def _lock(self, asset, amt):
        self.locked[asset] = self.locked.get(asset, 0.0) + amt

    def _free(self, asset):
        return self.total.get(asset, 0.0) - self.locked.get(asset, 0.0)

    def _execute(self, o, price):
        base, quote = o["symbol"].split("/")
        amt = o["amount"]
        if o["side"] == "buy":
            self.total[quote] -= amt * price
            fee = amt * self.fee  # frais en crypto (comme Binance sans BNB)
            self.total[base] = self.total.get(base, 0.0) + amt - fee
            o["fee"] = {"cost": fee, "currency": base}
        else:
            if o.get("locked"):
                self.locked[base] -= amt
            self.total[base] -= amt
            fee = amt * price * self.fee
            self.total[quote] += amt * price - fee
            o["fee"] = {"cost": fee, "currency": quote}
        o.update(status="closed", filled=amt, average=price)

    # -- API CCXT --
    def create_order(self, symbol, type, side, amount, price=None, params=None):
        params = params or {}
        self.calls.append(("create", symbol, type, side, amount, params))
        if self.fail_next_create is not None:
            exc, self.fail_next_create = self.fail_next_create, None
            if isinstance(exc, tuple):  # (exception, l'ordre a quand même été créé)
                exc, really = exc
                if really:
                    self._create(symbol, type, side, amount, price, params)
            raise exc
        return dict(self._create(symbol, type, side, amount, price, params))

    def _create(self, symbol, type, side, amount, price, params):
        base, quote = symbol.split("/")
        oid = str(next(self.ids))
        o = {"id": oid, "clientOrderId": params.get("clientOrderId"), "symbol": symbol, "type": type, "side": side,
             "amount": amount, "price": price, "status": "open", "filled": 0.0, "average": None, "fee": None,
             "stopPrice": params.get("stopLossPrice")}
        last = self.prices[symbol]
        if side == "sell" and self._free(base) + 1e-12 < amount:
            raise ccxt.InsufficientFunds("Account has insufficient balance")
        if side == "buy" and self._free(quote) + 1e-9 < amount * (price or last):
            raise ccxt.InsufficientFunds("Account has insufficient balance")
        self.orders[oid] = o
        if o["stopPrice"] is not None:
            if side == "sell" and last <= o["stopPrice"]:
                del self.orders[oid]
                raise ccxt.InvalidOrder('binance {"code":-2010,"msg":"Stop price would trigger immediately."}')
            self._lock(base, amount)
            o["locked"] = True
            return o
        if type == "market":
            self._execute(o, last)
        elif side == "sell":
            self._lock(base, amount)
            o["locked"] = True
        return o

    def fetch_order(self, id, symbol=None, params=None):
        params = params or {}
        if id is None:
            cid = params.get("origClientOrderId") or params.get("clientOrderId")
            for o in self.orders.values():
                if o["clientOrderId"] == cid:
                    return dict(o)
            raise ccxt.OrderNotFound("Order does not exist.")
        if id not in self.orders:
            raise ccxt.OrderNotFound("Order does not exist.")
        return dict(self.orders[id])

    def cancel_order(self, id, symbol=None, params=None):
        o = self.orders.get(id)
        if o is None or o["status"] != "open":
            raise ccxt.OrderNotFound("Unknown order sent.")
        if o.get("locked"):
            base = o["symbol"].split("/")[0]
            self.locked[base] -= o["amount"]
        o["status"] = "canceled"
        return dict(o)

    def fetch_balance(self):
        free = {a: self._free(a) for a in self.total}
        return {"total": dict(self.total), "free": free}

    # -- simulation du marché côté exchange --
    def move(self, symbol, price):
        self.prices[symbol] = price
        for o in list(self.orders.values()):
            if o["symbol"] == symbol and o["status"] == "open" and o["stopPrice"] is not None:
                if o["side"] == "sell" and price <= o["stopPrice"]:
                    self._execute(o, price)


@pytest.fixture
def fake(prices):
    return FakeExchange(prices)


@pytest.fixture
def live(db, fake, prices, clock):
    acc = db.insert("accounts", {"name": "réel", "kind": "live", "role": "live", "quote": "USDC", "created_at": clock()})
    return LiveBroker(db, acc, "USDC", fake, market_fn, prices, clock=clock)


@pytest.fixture
def lpm(db, live, clock):
    return PositionManager(db, live, clock=clock)


def test_market_buy_records_fill_with_base_fee(live, lpm, fake):
    pos = lpm.open("BTC/USDC", 0.1, origin="manual", stop=48_000)
    assert pos["status"] == "open"
    # frais de 0,1 % prélevés en BTC : quantité nette 0,0999
    assert pos["qty"] == pytest.approx(0.0999)
    assert pos["entry_fees"] == pytest.approx(0.0001 * 50_000)
    stop = [o for o in lpm.protective_orders(pos["id"]) if o["purpose"] == "stop"][0]
    assert stop["exchange_id"] and stop["exchange_id"] != "virtual"
    assert fake.orders[stop["exchange_id"]]["stopPrice"] == 48_000
    assert fake.orders[stop["exchange_id"]]["amount"] == pytest.approx(0.0999)


def test_stop_filled_while_bot_offline_is_detected_by_sync(live, lpm, fake, clock):
    pos = lpm.open("BTC/USDC", 0.1, origin="ai", stop=48_000, target=55_000)
    fake.move("BTC/USDC", 47_900)  # l'exchange exécute le stop pendant que le bot ne regarde pas
    clock.advance(30)
    live.sync()
    p = lpm.get(pos["id"])
    assert p["status"] == "closed"
    assert p["reason_close"] == "Stop-loss touché"
    assert p["pnl_r"] < -1.0
    # l'objectif virtuel a été annulé (OCO)
    assert all(o["status"] != "open" for o in live.open_orders())


def test_virtual_target_triggers_market_sell_and_cancels_stop(live, lpm, fake, prices, clock):
    pos = lpm.open("BTC/USDC", 0.1, origin="ai", stop=48_000, target=52_000)
    target = [o for o in lpm.protective_orders(pos["id"]) if o["purpose"] == "target"][0]
    assert target["exchange_id"] == "virtual"
    clock.advance(10)
    prices["BTC/USDC"] = 52_100
    live.process_bar("BTC/USDC", 51_000, 52_100, 50_900, 52_100, int(clock() * 1000))
    p = lpm.get(pos["id"])
    assert p["status"] == "closed" and p["reason_close"] == "Objectif atteint"
    assert p["realized_pnl"] > 0
    stops = [o for o in fake.orders.values() if o["stopPrice"]]
    assert stops and all(o["status"] == "canceled" for o in stops)
    assert fake.locked.get("BTC", 0) == pytest.approx(0)


def test_network_error_after_order_received_does_not_duplicate(live, fake):
    fake.fail_next_create = (ccxt.RequestTimeout("timeout"), True)  # l'exchange a reçu l'ordre
    o = live.place_order("BTC/USDC", "buy", "market", 0.01)
    assert o["status"] == "filled"
    creates = [c for c in fake.calls if c[0] == "create"]
    assert len(creates) == 1  # pas de second envoi
    assert len(fake.orders) == 1


def test_network_error_order_not_received_is_rejected(live, fake):
    fake.fail_next_create = (ccxt.RequestTimeout("timeout"), False)
    with pytest.raises(OrderError) as e:
        live.place_order("BTC/USDC", "buy", "market", 0.01)
    assert e.value.code == "network"
    assert fake.orders == {}


def test_stop_would_trigger_closes_immediately(live, lpm, fake, prices):
    # le prix chute entre l'achat et la pose du stop
    original = fake._create

    def create_then_drop(*a, **k):
        o = original(*a, **k)
        if a[2] == "buy":
            prices["BTC/USDC"] = 47_000
        return o

    fake._create = create_then_drop
    pos = lpm.open("BTC/USDC", 0.1, origin="ai", stop=48_000)
    p = lpm.get(pos["id"])
    assert p["status"] == "closed"
    assert "immédiate" in p["reason_close"]


def test_auth_error_is_readable(live, fake):
    fake.fail_next_create = ccxt.AuthenticationError("Invalid API-key, IP, or permissions for action.")
    with pytest.raises(OrderError) as e:
        live.place_order("BTC/USDC", "buy", "market", 0.01)
    assert e.value.code == "auth"


def test_reconcile_replaces_missing_stop(live, lpm, fake):
    pos = lpm.open("BTC/USDC", 0.1, origin="ai", stop=48_000)
    stop = [o for o in lpm.protective_orders(pos["id"]) if o["purpose"] == "stop"][0]
    fake.cancel_order(stop["exchange_id"])  # quelqu'un annule le stop sur le site de l'exchange
    issues = live.reconcile(lpm)
    assert any("stop absent" in i for i in issues)
    stops = [o for o in lpm.protective_orders(pos["id"]) if o["purpose"] == "stop"]
    assert len(stops) == 1 and stops[0]["status"] == "open"


def test_manual_close_cancels_stop_then_sells(live, lpm, fake, prices):
    pos = lpm.open("ETH/USDC", 1.0, origin="manual", stop=2_300)
    prices["ETH/USDC"] = 2_600
    p = lpm.close(pos["id"])
    assert p["status"] == "closed"
    assert fake.locked.get("ETH", 0) == pytest.approx(0)
    assert fake.total.get("ETH", 0) == pytest.approx(0.0, abs=1e-3)  # reste éventuellement de la poussière
