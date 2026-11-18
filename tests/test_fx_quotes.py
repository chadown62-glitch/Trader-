from decimal import Decimal
import pytest
from sentinelle.market.fx import FXBook, FXError, FXQuote


def test_direct_inverse_and_depeg_are_explicit():
    book = FXBook([FXQuote('USDC', 'USD', Decimal('0.97'), Decimal('0.98'), 100, 'fake')])
    assert book.convert(10, 'USDC', 'USD', now=110, max_age=20)['amount'] == Decimal('9.70')
    assert book.convert(Decimal('9.8'), 'USD', 'USDC', now=110, max_age=20)['amount'] == Decimal('10')
    with pytest.raises(FXError, match='manquant'):
        book.convert(10, 'EUR', 'USDT', now=110, max_age=20)


def test_explicit_two_leg_conversion_and_fees():
    book = FXBook([FXQuote('EUR', 'USD', Decimal('1.10'), Decimal('1.11'), 100, 'A'),
                   FXQuote('USDT', 'USD', Decimal('0.98'), Decimal('1.00'), 105, 'B')])
    out = book.convert(10, 'EUR', 'USDT', via='USD', now=110, max_age=20, fee_rate=Decimal('0.01'))
    assert out['amount'] == Decimal('10') * Decimal('1.10') * Decimal('0.99') * Decimal('0.99')
    assert [leg['source'] for leg in out['legs']] == ['A', 'B']


@pytest.mark.parametrize('ts', [80, 111])
def test_old_or_future_quote_fails_closed(ts):
    book = FXBook([FXQuote('EUR', 'USD', 1, 1.01, ts, 'fake')])
    with pytest.raises(FXError, match='périmé ou futur'):
        book.convert(10, 'EUR', 'USD', now=110, max_age=20)


@pytest.mark.parametrize('bid,ask', [('nan', '1'), ('0', '1'), ('1.1', '1')])
def test_invalid_spread_refused(bid, ask):
    with pytest.raises(FXError):
        FXQuote('EUR', 'USD', bid, ask, 100, 'fake')
