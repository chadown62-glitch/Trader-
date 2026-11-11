"""Timestamp and executable depth of isolated public-book snapshots."""
from decimal import Decimal
import pytest
from sentinelle.market.fx import FXError
from sentinelle.market.fx_provider import PublicFXProvider
from sentinelle.market.markets import MarketInfo


class Source:
    name = 'fixture'
    def __init__(self, timestamp=1700000000000):
        self.book = {'bids': [[1.08, 10]], 'asks': [[1.09, 5]], 'timestamp': timestamp}
    def fetch_order_book(self, symbol, limit=5):
        assert symbol == 'EUR/USDC'
        return self.book


def provider(timestamp=1700000000000):
    return PublicFXProvider(Source(timestamp), {'EUR/USDC': MarketInfo('EUR/USDC', 'EUR', 'USDC')}, lambda: 1700000000.)


def test_inverse_uses_ask_and_quote_denomination_for_depth():
    p = provider()
    book = p.snapshot('USDC', 'EUR')
    out = book.convert('5.45', 'USDC', 'EUR', now=1700000000., max_age=60, require_depth=True)
    assert out['amount'] == Decimal('5.45') / Decimal('1.09')
    with pytest.raises(FXError, match='Liquidité'):
        book.convert('5.46', 'USDC', 'EUR', now=1700000000., max_age=60, require_depth=True)


@pytest.mark.parametrize('timestamp', [None, 1699999939000, 1700000001000])
def test_missing_stale_or_future_exchange_timestamp_is_not_replaced(timestamp):
    with pytest.raises(FXError):
        provider(timestamp).snapshot('EUR', 'USDC')
