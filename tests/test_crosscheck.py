"""Contrôle croisé sur deux cotations synthétiques, y compris au point d'entrée."""
from types import SimpleNamespace

import pytest

from sentinelle.agent.agent import TradingAgent
from sentinelle.market.crosscheck import CrossChecker, Quote, QuoteState
from sentinelle.market.hub import STALE_SECONDS
from sentinelle.services.agents import AgentsMixin
from tests.test_risk_agent_backtest import _feat_with_signal, risk, settings

SYMBOL = "ETH/USDC"
NOW_MS = 1_700_000_000_000


class Ticker:
    def __init__(self, clock):
        self.data = {"last": 100.0, "timestamp": clock() * 1000}
        self.calls = 0

    def load_markets(self):
        return {SYMBOL: {}}

    def fetch_ticker(self, symbol):
        self.calls += 1
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


@pytest.fixture
def cross(clock):
    ticker = Ticker(clock)
    return CrossChecker(exchange=ticker, clock=clock), ticker


@pytest.mark.parametrize("changes,state", [
    ({}, "VERIFIED"), ({"last": 80}, "INCONSISTENT"),
    ({"last": None}, "UNVERIFIED"), ({"last": 0}, "UNVERIFIED"), ({"last": -1}, "UNVERIFIED"),
    ({"last": float("nan")}, "UNVERIFIED"), ({"last": float("inf")}, "UNVERIFIED"),
    ({"timestamp": None}, "UNVERIFIED"), ({"timestamp": NOW_MS + 1}, "UNVERIFIED"),
    ({"timestamp": NOW_MS - (STALE_SECONDS + 1) * 1000}, "STALE"),
    ({"last": None, "close": 100}, "VERIFIED"), ({"last": 0, "close": 100}, "UNVERIFIED"),
    ({"symbol": "BTC/USDC"}, "UNVERIFIED"), ({"timestamp": float("nan")}, "UNVERIFIED"),
    ({"timestamp": float("inf")}, "UNVERIFIED"), ({"timestamp": -2}, "UNVERIFIED"),
    ({"last": True}, "UNVERIFIED"),
], ids=["concordant", "divergent", "no_price", "zero", "negative", "nan", "infinite",
        "no_timestamp", "future", "stale", "close_only", "zero_with_close", "wrong_pair",
        "nan_time", "infinite_time", "negative_time", "bool_price"])
def test_reference_cases(cross, clock, changes, state):
    cc, ticker = cross
    ticker.data.update(changes)
    result = cc.check(SYMBOL, Quote(100, clock(), clock()), 2)
    assert result.state.value == state
    assert (result.veto is None) == (state == "VERIFIED")
    assert cc.status == {"source": "kraken", "symbol": SYMBOL, "state": state, "last_error": result.veto}


def test_exact_gap_limit_is_accepted(cross, clock):
    cc, _ = cross
    assert cc.check(SYMBOL, Quote(102, clock(), clock()), 2).state is QuoteState.VERIFIED


@pytest.mark.parametrize("field,value,state", [
    ("price", None, "UNVERIFIED"), ("price", float("nan"), "UNVERIFIED"),
    ("price", float("inf"), "UNVERIFIED"), ("price", -1, "UNVERIFIED"), ("price", False, "UNVERIFIED"),
    ("quoted_at", None, "UNVERIFIED"), ("received_at", None, "UNVERIFIED"),
    ("quoted_at", 1, "UNVERIFIED"), ("received_at", 1, "UNVERIFIED"),
    ("quoted_at", -STALE_SECONDS-1, "STALE"), ("received_at", -1, "UNVERIFIED"),
])
def test_primary_quote(cross, clock, field, value, state):
    cc, _ = cross
    fields = {"price": 100, "quoted_at": clock(), "received_at": clock()}
    fields[field] = clock() + value if field != "price" and value is not None else value
    assert cc.check(SYMBOL, Quote(**fields), 2).state.value == state


def test_cached_quote_rechecked_as_time_passes(cross, clock):
    cc, ticker = cross
    ticker.data["timestamp"] = (clock() - STALE_SECONDS) * 1000
    assert cc.check(SYMBOL, Quote(100, clock(), clock()), 2).state is QuoteState.VERIFIED
    clock.advance(1)
    assert cc.check(SYMBOL, Quote(100, clock(), clock()), 2).state is QuoteState.STALE
    assert ticker.calls == 1
    clock.advance(cc.ttl)
    ticker.data["timestamp"] = clock() * 1000
    assert cc.check(SYMBOL, Quote(100, clock(), clock()), 2).state is QuoteState.VERIFIED
    assert ticker.calls == 2


def test_future_at_reception_stays_unverified_in_cache(cross, clock):
    cc, ticker = cross
    ticker.data["timestamp"] += 1000
    for _ in range(2):
        assert cc.check(SYMBOL, Quote(100, clock(), clock()), 2).state is QuoteState.UNVERIFIED
        clock.advance(2)
    assert ticker.calls == 1


@pytest.mark.parametrize("value,age,state", [(100, 1, "VERIFIED"), (80, 1, "INCONSISTENT"),
                                             (100, -1, "UNVERIFIED"), (100, 121, "STALE")])
def test_signal_price_and_close_time(cross, clock, value, age, state):
    cc, _ = cross
    result = cc.check(SYMBOL, Quote(100, clock(), clock()), 2, signal=(value, clock() - age))
    assert result.state.value == state


@pytest.mark.parametrize("case,state", [("fresh", "VERIFIED"), ("no_ticker", "UNVERIFIED"),
                                         ("old", "STALE"), ("primary_diverges", "INCONSISTENT"),
                                         ("late", "STALE"), ("checker_missing", "UNVERIFIED"),
                                         ("undated_primary", "UNVERIFIED"), ("early_veto", "STALE")])
def test_paper_entry_gate_and_journal(db, paper, pm, risk, settings, prices, clock, cross, case, state):
    _, feat, indices = _feat_with_signal()
    feat = feat.iloc[:indices[-1] + 1]
    clock.t = (int(feat.ts.iloc[-1]) + 3_600_000) / 1000 + 1
    price = float(feat.close.iloc[-1])
    prices[SYMBOL] = price * (2 if case == "primary_diverges" else 1)
    settings["ai"]["regime_filter"] = False
    settings["ai"]["strategies"] = {"trend_breakout": True}
    settings["risk"]["min_reward_to_fees"] = 1.0
    settings["market"].update(cross_check=True, source="binance")
    cc, ticker = cross
    timestamp = clock() - (STALE_SECONDS + 1 if case == "old" else 0)
    ticker.data = {"last": price, "timestamp": timestamp * 1000}
    if case == "no_ticker":
        ticker.data = TimeoutError()
    hub = SimpleNamespace(price=prices, price_source_ts={SYMBOL: clock()}, price_ts={SYMBOL: clock()})
    app = AgentsMixin()
    app.settings = settings
    app.hub = hub
    app.market_clock = clock
    app.crosscheck = None if case == "checker_missing" else cc
    app.source = SimpleNamespace(name="binance")
    if case == "undated_primary":
        hub.price_source_ts.clear()

    def check(symbol, close, closed_at):
        return AgentsMixin._price_check(app, symbol, close, closed_at)

    def shield(*_):
        if case == "late":
            clock.advance(STALE_SECONDS + 1)

    agent = TradingAgent("ia", "training", db, pm, risk, lambda: settings, lambda: None,
                         timeframe="1h", clock=clock, price_check_fn=check, shield_fn=shield,
                         fresh_fn=(lambda _: False) if case == "early_veto" else app._primary_freshness)
    agent.running = True
    agent.on_bar(SYMBOL, feat)
    row = db.one("SELECT decision, decision_reason FROM candidates")
    assert db.scalar("SELECT COUNT(*) FROM fills") == (1 if state == "VERIFIED" else 0)
    if state == "VERIFIED":
        assert row["decision"] == "pris"
        ticker.data = TimeoutError()
        clock.advance(cc.ttl + 1)
        pm.close(pm.open_positions(SYMBOL)[0]["id"], "Sortie fictive")
        assert db.scalar("SELECT COUNT(*) FROM fills") == 2
        assert ticker.calls == 1
    else:
        assert row["decision"] == "refusé" and state in row["decision_reason"]
        if case in {"early_veto", "undated_primary"}:
            assert ticker.calls == 0


def test_synthetic_exemption_depends_on_actual_market_source():
    app = SimpleNamespace(settings={"market": {"cross_check": True, "source": "synthetic"}},
                          source=SimpleNamespace(name="binance"), crosscheck=None)
    assert "UNVERIFIED" in AgentsMixin._price_check(app, SYMBOL, 100, 1)
    app.source.name = "synthetic"
    assert AgentsMixin._price_check(app, SYMBOL, 100, 1) is None
    app.source.name = "binance"
    app.settings["market"]["cross_check"] = False
    assert AgentsMixin._price_check(app, SYMBOL, 100, 1) is None


@pytest.mark.parametrize("seconds,state", [(STALE_SECONDS + 1, "STALE"), (-1, "UNVERIFIED")])
def test_time_changes_during_fetch(cross, clock, monkeypatch, seconds, state):
    cc, ticker = cross
    primary = Quote(100, clock(), clock())

    def fetch(_):
        clock.advance(seconds)
        return {"last": 100, "timestamp": clock() * 1000}

    monkeypatch.setattr(ticker, "fetch_ticker", fetch)
    assert cc.check(SYMBOL, primary, 2).state.value == state


@pytest.mark.parametrize("limit", [None, float("nan"), float("inf"), -1])
def test_invalid_tolerance_cannot_approve(cross, clock, limit):
    cc, ticker = cross
    assert cc.check(SYMBOL, Quote(100, clock(), clock()), limit).state is QuoteState.UNVERIFIED
    assert ticker.calls == 0
