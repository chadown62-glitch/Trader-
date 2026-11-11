from decimal import Decimal
import pytest
from sentinelle.execution.liquidity import BookSnapshot, LiquidityError, simulate_taker


def book(timestamp=100):
    return BookSnapshot(bids=((99, 2), (98, 3)), asks=((101, 2), (102, 3)), timestamp=timestamp, source='fake')


def test_buy_walks_ask_levels_and_reports_partial_after_fees():
    r = simulate_taker(book(), 'buy', 10, now=105, max_age=10, fee_rate='0.01', amount_step=1)
    assert (r['filled'], r['remaining'], r['status']) == (5, 5, 'partial')
    assert r['notional'] == Decimal('508') and r['fee'] == Decimal('5.08')
    assert r['average_price'] == Decimal('101.6') and r['quote_change'] == Decimal('-513.08')


def test_sell_walks_bid_levels_and_fee_is_per_side():
    r = simulate_taker(book(), 'sell', 5, now=105, max_age=10, fee_rate='0.01', amount_step=1)
    assert r['status'] == 'filled' and r['notional'] == 492
    assert r['fee'] == Decimal('4.92') and r['quote_change'] == Decimal('487.08')


def test_limit_order_never_executes_at_worse_price():
    buy = simulate_taker(book(), 'buy', 4, now=105, max_age=10, amount_step=1, limit_price=101)
    sell = simulate_taker(book(), 'sell', 4, now=105, max_age=10, amount_step=1, limit_price=99)
    assert buy['filled'] == sell['filled'] == 2
    assert buy['fills'][0]['price'] == 101 and sell['fills'][0]['price'] == 99
    assert simulate_taker(book(), 'buy', 1, now=105, max_age=10, amount_step=1, limit_price=100)['status'] == 'unfilled'


def test_available_funds_include_fees_and_cap_partial_execution():
    r = simulate_taker(book(), 'buy', 5, now=105, max_age=10, fee_rate='0.01', amount_step=1, available_quote='205')
    assert r['filled'] == 2 and r['remaining'] == 3 and r['quote_change'] == Decimal('-204.02')
    r = simulate_taker(book(), 'sell', 5, now=105, max_age=10, amount_step=1, available_base='3')
    assert r['filled'] == 3 and r['remaining'] == 2


@pytest.mark.parametrize('timestamp', [89, 106])
def test_old_or_future_snapshot_is_refused(timestamp):
    with pytest.raises(LiquidityError, match='périmé ou futur'):
        simulate_taker(book(timestamp), 'buy', 1, now=105, max_age=10)


@pytest.mark.parametrize('bids,asks', [([], [[1, 2]]), ([[101, 1]], [[100, 1]]), ([[99, -1]], [[100, 1]]),
                                        ([[98, 1], [99, 1]], [[100, 1]])])
def test_invalid_or_crossed_books_are_refused(bids, asks):
    with pytest.raises(LiquidityError):
        BookSnapshot(bids=bids, asks=asks, timestamp=100, source='fake')
