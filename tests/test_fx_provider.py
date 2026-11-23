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


MARKETS = {'EUR/USDC': MarketInfo('EUR/USDC', 'EUR', 'USDC')}
TICKER = {'bid': 1.0916, 'bid_qty': 9160.2, 'ask': 1.0918, 'ask_qty': 32426.1, 'timestamp': 1699999999000}


class TickerSource(Source):
    """Public book without a time, as on Binance spot, and a ticker dated by the exchange."""
    def __init__(self, ticker, book_timestamp=None):
        super().__init__(book_timestamp)
        self.ticker, self.calls = ticker, 0

    def fetch_ticker(self, symbol):
        assert symbol == 'EUR/USDC'
        self.calls += 1
        return self.ticker


def test_a_book_without_time_uses_the_best_prices_of_the_exchange_dated_ticker():
    source = TickerSource(dict(TICKER))
    q, = PublicFXProvider(source, MARKETS, lambda: 1700000000.).snapshot('EUR', 'USDC').quotes.values()
    assert (q.bid, q.ask, q.bid_depth, q.ask_depth) == (Decimal('1.0916'), Decimal('1.0918'), Decimal('9160.2'), Decimal('32426.1'))
    assert q.timestamp == 1699999999.0 and q.source == 'fixture (ticker)'  # exchange time, never the receipt time


@pytest.mark.parametrize('change', [{'timestamp': None}, {'timestamp': 1699999939000}, {'timestamp': 1700000001000},
                                    {'bid_qty': None}, {'ask': 1.0}])
def test_an_undated_stale_future_or_unusable_ticker_is_refused(change):
    with pytest.raises(FXError):
        PublicFXProvider(TickerSource({**TICKER, **change}), MARKETS, lambda: 1700000000.).snapshot('EUR', 'USDC')


def test_a_dated_book_is_used_without_reading_the_ticker():
    source = TickerSource(dict(TICKER), book_timestamp=1700000000000)
    q, = PublicFXProvider(source, MARKETS, lambda: 1700000000.).snapshot('EUR', 'USDC').quotes.values()
    assert source.calls == 0 and (q.bid, q.ask, q.source) == (Decimal('1.08'), Decimal('1.09'), 'fixture')


# Paper execution reads the traded pair's book with the same rule (sentinelle/execution/book_execution.py).
from sentinelle.execution.book_execution import read_public_book  # noqa: E402
from sentinelle.execution.liquidity import LiquidityError  # noqa: E402


class PairSource(TickerSource):
    def fetch_order_book(self, symbol, limit=5):
        return {'bids': [[62950.0, 0.5], [62940.0, 1.0]], 'asks': [[62960.0, 0.4], [62970.0, 2.0]], 'timestamp': self.book['timestamp']}

    def fetch_ticker(self, symbol):
        self.calls += 1
        return self.ticker


def test_paper_execution_uses_a_single_level_from_the_dated_ticker_when_the_book_has_no_time():
    ticker = {'bid': 62950.0, 'bid_qty': 0.5, 'ask': 62960.0, 'ask_qty': 0.4, 'timestamp': 1699999999000}
    book = read_public_book(PairSource(ticker), 'BTC/USDC')
    assert book.bids == ((Decimal('62950.0'), Decimal('0.5')),) and book.asks == ((Decimal('62960.0'), Decimal('0.4')),)
    assert book.timestamp == 1699999999.0 and book.source == 'fixture (ticker)'  # no deeper liquidity is assumed
    with pytest.raises(LiquidityError, match='Horodatage'):
        read_public_book(PairSource({**ticker, 'timestamp': None}), 'BTC/USDC')
    with pytest.raises(LiquidityError, match='Horodatage'):
        read_public_book(Source(None), 'EUR/USDC')  # no ticker at all: refused, as before


def test_paper_execution_keeps_the_full_dated_book_without_reading_the_ticker():
    source = PairSource({}, book_timestamp=1700000000000)
    book = read_public_book(source, 'BTC/USDC')
    assert source.calls == 0 and len(book.asks) == 2 and book.timestamp == 1700000000.0 and book.source == 'fixture'
