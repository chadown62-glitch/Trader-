import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentinelle.execution.paper import PaperBroker  # noqa: E402
from sentinelle.execution.positions import PositionManager  # noqa: E402
from sentinelle.market.markets import MarketInfo  # noqa: E402
from sentinelle.storage.db import Database  # noqa: E402


class FakeClock:
    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Prices(dict):
    def __call__(self, symbol):
        return self.get(symbol)


MARKETS = {
    "BTC/USDC": MarketInfo("BTC/USDC", "BTC", "USDC", amount_step=1e-5, price_step=0.01, min_amount=1e-5, min_notional=5.0),
    "ETH/USDC": MarketInfo("ETH/USDC", "ETH", "USDC", amount_step=1e-4, price_step=0.01, min_amount=1e-4, min_notional=5.0),
}


def market_fn(symbol):
    if symbol not in MARKETS:
        raise KeyError(symbol)
    return MARKETS[symbol]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def prices():
    return Prices({"BTC/USDC": 50_000.0, "ETH/USDC": 2_500.0})


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def paper(db, prices, clock):
    acc = db.insert("accounts", {"name": "test", "kind": "paper", "role": "manual", "quote": "USDC", "initial_balance": 10_000, "created_at": clock()})
    b = PaperBroker(db, acc, "USDC", market_fn, prices, fee_rate=0.001, slippage_bps=10, clock=clock)
    b.set_balance("USDC", 10_000.0)
    return b


@pytest.fixture
def pm(db, paper, clock):
    return PositionManager(db, paper, clock=clock)
