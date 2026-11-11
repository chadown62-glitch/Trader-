"""Known temporal discrepancies: strict expected failures, not validated features."""
from copy import deepcopy

import pandas as pd
import pytest

from sentinelle.agent.agent import TradingAgent
from sentinelle.backtest import engine
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.execution.paper import PaperBroker
from sentinelle.market.markets import MarketInfo
from sentinelle.market.sources import SyntheticSource


@pytest.mark.xfail(strict=True, reason="Backtest currently fills at the signal close instead of the next open")
def test_backtest_market_entry_uses_next_bar_open(monkeypatch):
    bars = pd.DataFrame([
        {"ts": 0, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0},
        {"ts": 3600000, "open": 120.0, "high": 131.0, "low": 119.0, "close": 130.0},
    ])
    settings = deepcopy(DEFAULT_SETTINGS)
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


@pytest.mark.xfail(strict=True, reason="Synthetic partial candle uses its pre-generated final close")
def test_partial_synthetic_candle_does_not_reveal_its_future_close():
    source = SyntheticSource(["BTC/EUR"], seed=7, days=1, clock=lambda: 1700000000.0)
    before = source.fetch_ohlcv("BTC/EUR", "1m", limit=1)[-1][4]
    # Change only the not-yet-revealed final close of the current minute.
    source._data["BTC/EUR"]["c"][-1] += 1000
    after = source.fetch_ohlcv("BTC/EUR", "1m", limit=1)[-1][4]
    assert before == pytest.approx(after)
