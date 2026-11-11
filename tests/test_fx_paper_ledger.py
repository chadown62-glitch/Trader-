"""Fictitious FX conversion with timestamped fake books; no external access."""
import threading
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.execution.fx_ledger import PaperFXLedger
from sentinelle.execution.paper import PaperBroker
from sentinelle.market.fx import FXError
from sentinelle.market.fx_provider import PublicFXProvider
from sentinelle.market.markets import MarketInfo
from sentinelle.market.sources import SyntheticSource
from sentinelle.safety import RealTradingForbidden
from sentinelle.services.common import UserError
from sentinelle.services.core import App


NOW = 1700000000.0


class FakeBookSource:
    name = 'fake-public-source'
    def __init__(self):
        self.books = {
            'EUR/USDC': {'bids': [[1.08, 100]], 'asks': [[1.09, 100]], 'timestamp': NOW * 1000},
            'USDC/USD': {'bids': [[0.98, 100]], 'asks': [[0.99, 100]], 'timestamp': NOW * 1000},
            'USD/USDT': {'bids': [[1.00, 100]], 'asks': [[1.01, 100]], 'timestamp': NOW * 1000},
        }
        self.calls = []
    def fetch_order_book(self, symbol, limit=5):
        self.calls.append(symbol)
        return self.books[symbol]


@pytest.fixture
def fx_env(db):
    aid = db.insert('accounts', {'name': 'principal', 'kind': 'paper', 'role': 'principal', 'quote': 'EUR', 'initial_balance': 30})
    market = MarketInfo('BTC/EUR', 'BTC', 'EUR', amount_step=0.00001, min_notional=5)
    broker = PaperBroker(db, aid, 'EUR', lambda _: market, lambda _: 50000., clock=lambda: NOW)
    broker.set_balance('EUR', 30)
    source = FakeBookSource()
    markets = {s: MarketInfo(s, *s.split('/')) for s in source.books}
    provider = PublicFXProvider(source, markets, lambda: NOW)
    return broker, source, provider, PaperFXLedger(broker, lambda: NOW)


def test_atomic_conversion_roundtrip_records_spread_fees_and_provenance(fx_env, db):
    broker, source, provider, ledger = fx_env
    buy = ledger.convert('EUR', 'USDC', '10', provider.snapshot('EUR', 'USDC'), fee_rate='0.001')
    assert buy['amount_out'] == '10.78920000'
    assert broker.balances()['EUR'] == pytest.approx(20)
    assert broker.balances()['USDC'] == pytest.approx(10.7892)
    assert buy['legs'][0]['source'] == source.name
    sell = ledger.convert('USDC', 'EUR', buy['amount_out'], provider.snapshot('USDC', 'EUR'), fee_rate='0.001')
    assert broker.balances().get('USDC', 0) == pytest.approx(0)
    assert 29.8 < broker.balances()['EUR'] < 30
    rows = db.query('SELECT * FROM fx_operations ORDER BY id')
    assert len(rows) == 2 and rows[0]['amount_in'] == '10' and rows[1]['amount_out'] == sell['amount_out']


def test_two_leg_conversion_refuses_invented_stablecoin_parity(fx_env):
    broker, source, provider, ledger = fx_env
    book = provider.snapshot('EUR', 'USD', via='USDC')
    out = ledger.convert('EUR', 'USD', '10', book, via='USDC', fee_rate=0)
    assert Decimal(out['amount_out']) == Decimal('10.58400000')
    assert broker.balances()['USD'] == pytest.approx(10.584)
    assert source.calls == ['EUR/USDC', 'USDC/USD']


def test_two_leg_conversion_rejects_desynchronised_rates(fx_env):
    broker, source, provider, ledger = fx_env
    source.books['USDC/USD']['timestamp'] -= 6000
    with pytest.raises(FXError, match='Horodatages'):
        provider.snapshot('EUR', 'USD', via='USDC')
    assert broker.balances() == {'EUR': 30}


@pytest.mark.parametrize('bad', [None, NOW * 1000 - 61000, NOW * 1000 + 1])
def test_missing_stale_and_future_timestamps_refused_without_mutation(fx_env, db, bad):
    broker, source, provider, ledger = fx_env
    source.books['EUR/USDC']['timestamp'] = bad
    with pytest.raises(FXError, match='horodatage|Horodatage|périmé|futur'):
        provider.snapshot('EUR', 'USDC')
    assert broker.balances() == {'EUR': 30}
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 0


@pytest.mark.parametrize('book', [
    {'bids': [], 'asks': [[1.09, 100]], 'timestamp': NOW * 1000},
    {'bids': [[1.1, 100]], 'asks': [[1.09, 100]], 'timestamp': NOW * 1000},
    {'bids': [[1.0, 100]], 'asks': [[1.1, 100]], 'timestamp': NOW * 1000},
    {'bids': [[1.08, 0]], 'asks': [[1.09, 100]], 'timestamp': NOW * 1000},
])
def test_bad_or_wide_books_are_rejected(fx_env, book):
    broker, source, provider, ledger = fx_env
    source.books['EUR/USDC'] = book
    with pytest.raises(FXError):
        provider.snapshot('EUR', 'USDC')
    assert broker.balances() == {'EUR': 30}


def test_conversion_refuses_insufficient_top_of_book_depth(fx_env, db):
    broker, source, provider, ledger = fx_env
    source.books['EUR/USDC']['bids'][0][1] = 5
    with pytest.raises(FXError, match='Liquidité'):
        ledger.convert('EUR', 'USDC', '10', provider.snapshot('EUR', 'USDC'))
    assert broker.balances() == {'EUR': 30}
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 0


def test_reserved_cash_cannot_be_spent_twice(fx_env):
    broker, source, provider, ledger = fx_env
    broker.place_order('BTC/EUR', 'buy', 'limit', 0.0004, price=49000)
    with pytest.raises(FXError, match='insuffisant'):
        ledger.convert('EUR', 'USDC', '11', provider.snapshot('EUR', 'USDC'))
    assert broker.balances() == {'EUR': 30}


def test_concurrent_conversions_cannot_spend_same_cash(fx_env, db):
    broker, source, provider, ledger = fx_env
    book = provider.snapshot('EUR', 'USDC')
    barrier, outcomes = threading.Barrier(2), []
    def attempt():
        barrier.wait()
        try:
            ledger.convert('EUR', 'USDC', '20', book)
            outcomes.append('ok')
        except FXError:
            outcomes.append('refused')
    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)
    assert sorted(outcomes) == ['ok', 'refused']
    assert broker.balances()['EUR'] == pytest.approx(10)
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 1


def test_concurrent_retries_with_same_request_are_idempotent(fx_env, db):
    broker, source, provider, ledger = fx_env
    book = provider.snapshot('EUR', 'USDC')
    barrier, results = threading.Barrier(2), []
    def attempt():
        barrier.wait()
        results.append(ledger.convert('EUR', 'USDC', '10', book, request_id='unique_fx_request_123'))
    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)
    assert len(results) == 2 and results[0]['id'] == results[1]['id']
    assert sorted(r['replayed'] for r in results) == [False, True]
    assert broker.balances()['EUR'] == pytest.approx(20)
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 1


def test_failure_after_debit_rolls_back_balances_and_history(fx_env, db, monkeypatch):
    broker, source, provider, ledger = fx_env
    original = broker._adjust
    def fail_on_credit(asset, delta):
        if asset == 'USDC':
            raise RuntimeError('panne fictive')
        return original(asset, delta)
    monkeypatch.setattr(broker, '_adjust', fail_on_credit)
    with pytest.raises(RuntimeError, match='panne'):
        ledger.convert('EUR', 'USDC', '10', provider.snapshot('EUR', 'USDC'))
    assert broker.balances() == {'EUR': 30}
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 0


def test_revalidate_snapshot_after_it_becomes_stale(fx_env, db):
    broker, source, provider, ledger = fx_env
    snapshot = provider.snapshot('EUR', 'USDC')
    ledger.clock = lambda: NOW + 61
    with pytest.raises(FXError, match='périmé'):
        ledger.convert('EUR', 'USDC', '10', snapshot)
    assert broker.balances() == {'EUR': 30}
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 0


def test_legacy_live_account_refused_before_any_balance_write(db):
    aid = db.insert('accounts', {'name': 'legacy', 'kind': 'live', 'role': 'principal', 'quote': 'EUR'})
    broker = PaperBroker(db, aid, 'EUR', lambda _: None, lambda _: None, clock=lambda: NOW)
    broker.set_balance('EUR', 30)
    source = FakeBookSource()
    provider = PublicFXProvider(source, {'EUR/USDC': MarketInfo('EUR/USDC', 'EUR', 'USDC')}, lambda: NOW)
    with pytest.raises(RealTradingForbidden):
        PaperFXLedger(broker, lambda: NOW).convert('EUR', 'USDC', '10', provider.snapshot('EUR', 'USDC'))
    assert broker.balances() == {'EUR': 30}


def test_app_conversion_uses_public_data_and_values_portfolio(tmp_path, monkeypatch):
    source = SyntheticSource(['BTC/EUR', 'EUR/USDC'], days=3)
    books = FakeBookSource()
    monkeypatch.setattr(source, 'fetch_order_book', lambda symbol, limit=5: {**books.fetch_order_book(symbol, limit), 'timestamp': source.now_ms()})
    app = App(tmp_path, source=source, start=False, settings_override={'market': {'source': 'synthetic'}, 'general': {'watchlist': ['BTC/EUR']}})
    app._startup()
    aid = app.db.one("SELECT id FROM accounts WHERE role='principal'")['id']
    result = app.convert_paper_currency(aid, 'EUR', 'USDC', Decimal('10'))
    assert result['simulated'] is True
    summary = app.account_summary(aid)
    assert 29.8 < summary['equity'] < 30
    assert summary['cash'] == pytest.approx(20)
    assert len(app.paper_fx_history(aid)) == 1
    app.db.close()


def test_api_conversion_auth_csrf_and_live_guard(tmp_path, monkeypatch):
    source = SyntheticSource(['BTC/EUR', 'EUR/USDC'], days=3)
    books = FakeBookSource()
    monkeypatch.setattr(source, 'fetch_order_book', lambda symbol, limit=5: {**books.fetch_order_book(symbol, limit), 'timestamp': source.now_ms()})
    app = App(tmp_path, source=source, settings_override={'market': {'source': 'synthetic'}, 'general': {'watchlist': ['BTC/EUR']}})
    assert app.engine.ready.wait(10)
    aid = app.db.one("SELECT id FROM accounts WHERE role='principal'")['id']
    live_id = app.db.insert('accounts', {'name': 'legacy', 'kind': 'live', 'role': 'live', 'quote': 'EUR'})
    body = {'base': 'EUR', 'target': 'USDC', 'amount': '10', 'request_id': 'fake_request_123456'}
    with TestClient(create_api(app)) as client:
        assert client.post(f'/api/accounts/{aid}/fx', json=body, headers={'X-Sentinelle': '1'}).status_code == 401
        assert client.post('/api/auth/setup', json={'password': 'fake-test-password'}, headers={'X-Sentinelle': '1'}).status_code == 200
        assert client.post(f'/api/accounts/{aid}/fx', json=body).status_code == 403
        calls = len(books.calls)
        assert client.post(f'/api/accounts/{live_id}/fx', json=body, headers={'X-Sentinelle': '1'}).status_code == 403
        assert len(books.calls) == calls
        assert client.post(f'/api/accounts/{aid}/fx', json={**body, 'rate': 9999}, headers={'X-Sentinelle': '1'}).status_code == 422
        r = client.post(f'/api/accounts/{aid}/fx', json=body, headers={'X-Sentinelle': '1'})
        assert r.status_code == 200, r.text
        assert r.json()['simulated'] is True and Decimal(r.json()['amount_out']) == Decimal('10.78920000')
        books.books.clear()  # A retry must use the committed operation, even if prices disappear.
        again = client.post(f'/api/accounts/{aid}/fx', json=body, headers={'X-Sentinelle': '1'})
        assert again.status_code == 200 and again.json()['replayed'] is True and again.json()['id'] == r.json()['id']
        conflict = client.post(f'/api/accounts/{aid}/fx', json={**body, 'amount': '11'}, headers={'X-Sentinelle': '1'})
        assert conflict.status_code == 400
        history = client.get(f'/api/accounts/{aid}/fx').json()
        assert len(history) == 1 and history[0]['legs'][0]['source'] == source.name
    app.close()


def test_fx_operations_and_idempotence_survive_restart(tmp_path):
    from sentinelle.storage.db import Database
    path = tmp_path / 'fx.sqlite'
    db = Database(path)
    aid = db.insert('accounts', {'name': 'p', 'kind': 'paper', 'role': 'principal', 'quote': 'EUR'})
    broker = PaperBroker(db, aid, 'EUR', lambda _: None, lambda _: None, clock=lambda: NOW)
    broker.set_balance('EUR', 30)
    source = FakeBookSource()
    provider = PublicFXProvider(source, {'EUR/USDC': MarketInfo('EUR/USDC', 'EUR', 'USDC')}, lambda: NOW)
    op = PaperFXLedger(broker, lambda: NOW).convert('EUR', 'USDC', '10', provider.snapshot('EUR', 'USDC'), request_id='persisted_request_123')
    db.close()
    reopened = Database(path)
    broker2 = PaperBroker(reopened, aid, 'EUR', lambda _: None, lambda _: None, clock=lambda: NOW + 1000)
    assert broker2.balances()['EUR'] == pytest.approx(20)
    assert broker2.balances()['USDC'] == pytest.approx(10.8)
    replay = PaperFXLedger(broker2, lambda: NOW + 1000).replay('persisted_request_123', 'EUR', 'USDC', '10')
    assert replay['id'] == op['id'] and replay['replayed'] is True
    assert reopened.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 1
    reopened.close()


def test_schema_upgrade_preserves_existing_fx_operation(tmp_path):
    import sqlite3
    from sentinelle.storage.db import Database
    path = tmp_path / 'old.sqlite'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE fx_operations (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, ts REAL NOT NULL, source_currency TEXT NOT NULL, target_currency TEXT NOT NULL, amount_in TEXT NOT NULL, amount_out TEXT NOT NULL, fee_rate TEXT NOT NULL, legs TEXT NOT NULL)')
    conn.execute("INSERT INTO fx_operations VALUES (1, 42, 1, 'EUR', 'USD', '1', '1.1', '0', '[]')")
    conn.commit()
    conn.close()
    db = Database(path)
    row = db.one('SELECT * FROM fx_operations WHERE id=1')
    assert row['account_id'] == 42 and row['request_id'] is None and row['via'] is None
    db.close()


def test_unrepresentable_conversion_fails_without_balance_change(fx_env, db):
    from sentinelle.market.fx import FXBook, FXQuote
    broker, source, provider, ledger = fx_env
    bad = FXBook([FXQuote('EUR', 'USDC', Decimal('1e100'), Decimal('1e100'), NOW, 'fake', Decimal('100'), Decimal('100'))])
    with pytest.raises(FXError, match='représenter'):
        ledger.convert('EUR', 'USDC', '10', bad)
    assert broker.balances() == {'EUR': 30}
    assert db.scalar('SELECT COUNT(*) FROM fx_operations', (), 0) == 0
