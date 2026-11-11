"""Regression tests with a completely fake public exchange; no network."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sentinelle.market.sources import CREDENTIAL_FIELDS, PublicExchange, make_ccxt_exchange
from sentinelle.safety import RealTradingForbidden


def dummy_exchange():
    class FakeExchange:
        def __init__(self):
            self.create_order = Mock()
            self.called_ticker = Mock(return_value={"last": 100})

        def fetch_ticker(self, symbol):
            return self.called_ticker(symbol)

    exchange = FakeExchange()
    for field in CREDENTIAL_FIELDS:
        setattr(exchange, field, None)
    return exchange


def test_underlying_private_client_is_not_reachable_by_normal_attribute_access():
    inner = dummy_exchange()
    facade = PublicExchange(inner)
    with pytest.raises(RealTradingForbidden):
        facade._ex.create_order("BTC/EUR", "market", "buy", 1)
    inner.create_order.assert_not_called()


def test_public_method_reference_cannot_reveal_private_bound_client():
    inner = dummy_exchange()
    method = PublicExchange(inner).fetch_ticker
    assert getattr(method, "__self__", None) is not inner
    assert method("BTC/EUR") == {"last": 100}
    inner.called_ticker.assert_called_once_with("BTC/EUR")


def test_captured_public_method_fails_closed_if_credentials_appear_later():
    inner = dummy_exchange()
    method = PublicExchange(inner).fetch_ticker
    inner.apiKey = "FAKE-KEY"
    with pytest.raises(RealTradingForbidden):
        method("BTC/EUR")
    inner.called_ticker.assert_not_called()


def test_public_factory_with_fake_ccxt_never_receives_credentials(monkeypatch):
    inner = dummy_exchange()
    inner.urls = {"api": {"public": "https://unused.invalid"}}
    constructor = Mock(return_value=inner)
    monkeypatch.setitem(__import__("sys").modules, "ccxt", SimpleNamespace(binance=constructor))
    facade = make_ccxt_exchange("binance", public_mirror=True)
    config = constructor.call_args.args[0]
    assert not set(config).intersection(CREDENTIAL_FIELDS)
    assert facade.fetch_ticker("BTC/EUR") == {"last": 100}
    inner.create_order.assert_not_called()
