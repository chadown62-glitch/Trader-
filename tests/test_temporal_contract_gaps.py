"""Causal entry and exit regressions on the shared execution contract."""
from copy import deepcopy

import pandas as pd
import pytest

from sentinelle.agent.agent import TradingAgent
from sentinelle.backtest import engine
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.execution.paper import PaperBroker
from sentinelle.market.markets import MarketInfo
from sentinelle.market.sources import SyntheticSource


def test_backtest_market_entry_uses_next_bar_open(monkeypatch):
    bars = pd.DataFrame([
        {"ts": 0, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"ts": 3600000, "open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},
    ])
    settings = deepcopy(DEFAULT_SETTINGS)
    settings["general"]["quote"] = "EUR"
    settings["ai"]["strategies"] = {name: False for name in settings["ai"]["strategies"]}
    monkeypatch.setattr(engine, "compute_features", lambda frame, *args, **kwargs: frame)
    def decide_at_first_close(agent, symbol, feat, idx=None, sig_at=None):
        if idx == 0:
            agent.positions.open(symbol, 0.1, origin="ai")
    monkeypatch.setattr(TradingAgent, "on_bar", decide_at_first_close)
    fills = []
    original = PaperBroker._fill
    def capture(broker, order, price, maker):
        fills.append(price)
        return original(broker, order, price, maker)
    monkeypatch.setattr(PaperBroker, "_fill", capture)
    market = MarketInfo("BTC/EUR", "BTC", "EUR", amount_step=0.00001, min_notional=5)
    engine.run_backtest({"BTC/EUR": bars}, "1h", settings, None, markets={"BTC/EUR": market})
    assert len(fills) == 1
    assert fills[0] == pytest.approx(120.0 * (1 + settings["paper"]["slippage_bps"] / 10000))


def test_partial_synthetic_candle_does_not_reveal_its_future_close():
    source = SyntheticSource(["BTC/EUR"], seed=7, days=1, clock=lambda: 1700000000.0)
    before = source.fetch_ohlcv("BTC/EUR", "1m", limit=1)[-1]
    # Change the entire not-yet-observable future of the current minute.
    for key in ("h", "l", "c", "v"):
        source._data["BTC/EUR"][key][-1] += 1000
    after = source.fetch_ohlcv("BTC/EUR", "1m", limit=1)[-1]
    assert before == pytest.approx(after)

@pytest.mark.parametrize('opening', [80.0, 120.0])
def test_deferred_market_gap_uses_next_open_with_costs(db, opening):
    from sentinelle.execution.positions import PositionManager
    market = MarketInfo('BTC/EUR', 'BTC', 'EUR', amount_step=0.001, min_notional=5)
    aid = db.insert('accounts', {'name': 'test', 'kind': 'paper', 'role': 'principal', 'quote': 'EUR'})
    price = {'BTC/EUR': 100.0}
    now = [1000.0]
    broker = PaperBroker(db, aid, 'EUR', lambda _: market, price.get, fee_rate=0.001,
                         slippage_bps=5, clock=lambda: now[0], defer_market=True)
    broker.set_balance('EUR', 30)
    pm = PositionManager(db, broker, clock=lambda: now[0])
    pos = pm.open('BTC/EUR', 0.1, origin='ai', stop=70.0)
    assert pos['status'] == 'pending' and broker.balances() == {'EUR': 30}
    now[0] += 60
    price['BTC/EUR'] = opening
    assert broker.process_bar('BTC/EUR', opening, opening * 1.01, opening * 0.99, opening, int(now[0] * 1000)) == 1
    pos = pm.get(pos['id'])
    assert pos['entry_price'] == pytest.approx(opening * 1.0005)
    assert broker.balances()['EUR'] == pytest.approx(30 - 0.1 * opening * 1.0005 * 1.001)


def test_gap_below_planned_stop_cancels_deferred_entry(db):
    from sentinelle.execution.positions import PositionManager
    market = MarketInfo('BTC/EUR', 'BTC', 'EUR', amount_step=0.001, min_notional=5)
    aid = db.insert('accounts', {'name': 'test', 'kind': 'paper', 'role': 'principal', 'quote': 'EUR'})
    price = {'BTC/EUR': 100.0}
    now = [1000.0]
    broker = PaperBroker(db, aid, 'EUR', lambda _: market, price.get, clock=lambda: now[0], defer_market=True)
    broker.set_balance('EUR', 30)
    pm = PositionManager(db, broker, clock=lambda: now[0])
    pos = pm.open('BTC/EUR', 0.1, origin='ai', stop=90)
    now[0] += 60
    price['BTC/EUR'] = 80.0
    assert broker.process_bar('BTC/EUR', 80, 82, 78, 80, int(now[0] * 1000)) == 0
    assert pm.get(pos['id'])['status'] == 'cancelled'
    assert broker.balances() == {'EUR': 30}
    assert not broker.open_orders()


def test_deferred_entry_stop_wins_when_target_also_hit(db):
    from sentinelle.execution.positions import PositionManager
    market = MarketInfo('BTC/EUR', 'BTC', 'EUR', amount_step=0.001, min_notional=5)
    aid = db.insert('accounts', {'name': 'test', 'kind': 'paper', 'role': 'principal', 'quote': 'EUR'})
    price = {'BTC/EUR': 100.0}
    now = [1000.0]
    broker = PaperBroker(db, aid, 'EUR', lambda _: market, price.get, clock=lambda: now[0], defer_market=True)
    broker.set_balance('EUR', 30)
    pm = PositionManager(db, broker, clock=lambda: now[0])
    pos = pm.open('BTC/EUR', 0.1, origin='ai', stop=90, target=130)
    now[0] += 60
    price['BTC/EUR'] = 120.0
    assert broker.process_bar('BTC/EUR', 120, 140, 80, 110, int(now[0] * 1000)) == 2
    assert pm.get(pos['id'])['status'] == 'closed'
    assert pm.get(pos['id'])['reason_close'] == 'Stop-loss touché'
    assert broker.balances().get('BTC', 0) == pytest.approx(0)


def test_deferred_exit_keeps_stop_until_next_fill(db):
    from sentinelle.execution.positions import PositionManager
    market = MarketInfo('BTC/EUR', 'BTC', 'EUR', amount_step=0.001, min_notional=5)
    aid = db.insert('accounts', {'name': 'test', 'kind': 'paper', 'role': 'principal', 'quote': 'EUR'})
    price = {'BTC/EUR': 100.0}
    now = [1000.0]
    broker = PaperBroker(db, aid, 'EUR', lambda _: market, price.get, clock=lambda: now[0], defer_market=True)
    broker.set_balance('EUR', 30)
    pm = PositionManager(db, broker, clock=lambda: now[0])
    pos = pm.open('BTC/EUR', 0.1, origin='ai', stop=90)
    now[0] += 60
    broker.process_bar('BTC/EUR', 100, 105, 99, 100, int(now[0] * 1000))
    pm.close(pos['id'])
    assert any(o['purpose'] == 'stop' for o in pm.protective_orders(pos['id']))
    pm.close(pos['id'])
    assert sum(o['purpose'] == 'exit' for o in broker.open_orders()) == 1
    now[0] += 60
    price['BTC/EUR'] = 95
    broker.process_bar('BTC/EUR', 95, 96, 94, 95, int(now[0] * 1000))
    assert pm.get(pos['id'])['status'] == 'closed'
    assert not broker.open_orders()


def test_backtest_does_not_pass_future_rows_to_agent(monkeypatch):
    bars = pd.DataFrame([
        {'ts': 0, 'open': 100., 'high': 101., 'low': 99., 'close': 100.},
        {'ts': 3600000, 'open': 100., 'high': 102., 'low': 99., 'close': 101.},
        {'ts': 7200000, 'open': 101., 'high': 105., 'low': 100., 'close': 104.},
    ])
    cfg = deepcopy(DEFAULT_SETTINGS)
    cfg['ai']['strategies'] = {name: False for name in cfg['ai']['strategies']}
    monkeypatch.setattr(engine, 'compute_features', lambda frame, *args, **kw: frame)
    visible = []
    def inspect_rows(agent, symbol, feat, idx=None, sig_at=None):
        visible.append((idx, list(feat['ts'])))
    monkeypatch.setattr(TradingAgent, 'on_bar', inspect_rows)
    engine.run_backtest({'BTC/EUR': bars}, '1h', cfg, None)
    assert visible == [(0, [0]), (1, [0, 3600000])]  # no successor for the final signal
