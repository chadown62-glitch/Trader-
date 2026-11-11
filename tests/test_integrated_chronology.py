from copy import deepcopy
import numpy as np
import pandas as pd
import pytest

from sentinelle.ai.features import compute_features
from sentinelle.agent.agent import TradingAgent
from sentinelle.backtest import engine
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.market.fx import FxQuote, FxService, FxUnavailable
from sentinelle.market.fx_book import FXBook, FXQuote
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App
from sentinelle.storage.db import Database
from tests.helpers import make_series


def test_future_close_cannot_veto_an_observed_open(monkeypatch):
    cfg = deepcopy(DEFAULT_SETTINGS)
    cfg["general"]["quote"] = "EUR"
    cfg["ai"]["strategies"] = {}
    frame = make_series(3)
    frame.loc[1, "open"] = 105.
    monkeypatch.setattr(engine, "compute_features", lambda f, *a, **kw: f)
    def decide(agent, symbol, feat, idx, sig_at):
        if idx == 0:
            agent.positions.open(symbol, .1, origin="ai")
    monkeypatch.setattr(TradingAgent, "on_bar", decide)
    def entry():
        result = engine.run_backtest({"BTC/EUR": frame}, "1h", cfg, None, research_trace=True)
        return [(r["ts"], r["price"], r["amount"]) for r in result["fills"]]
    before = entry()
    frame.loc[1, "close"] = np.nan
    assert entry() == before and len(before) == 1


def test_features_and_bitcoin_context_are_prefix_invariant():
    frame, btc = make_series(1700, seed=4), make_series(1700, seed=5)
    ref = compute_features(frame, "4h", btc=btc)
    for end in (1220, 1591):
        observed = compute_features(frame.iloc[:end], "4h", btc=btc.iloc[:end])
        pd.testing.assert_frame_equal(ref.iloc[:end], observed)
        changed = frame.copy()
        changed.loc[end:, ["open", "high", "low", "close", "volume"]] *= 2
        pd.testing.assert_frame_equal(ref.iloc[:end], compute_features(changed, "4h", btc=btc).iloc[:end])


def test_historical_fx_uses_only_known_books_and_never_refreshes(db):
    old, new = 1_609_459_200., 1_767_225_600.
    currency = "USDC"
    fx = FxService("EUR", [lambda *args: pytest.fail("called live provider")], db=db)
    def remember(ts, price):
        book = FXBook([FXQuote("EUR", currency, price, price + .01, ts, "recording", 20, 20)])
        fx._remember(FxQuote.from_book("EUR", currency, book, 60))
    remember(new, 1.60)
    with pytest.raises(FxUnavailable):
        fx.historical(currency, old)
    remember(old, 1.07)
    assert fx.historical(currency, old + 59).ts == old
    with pytest.raises(FxUnavailable):
        fx.historical(currency, old + 61)
    assert fx.historical(currency, new).rate != fx.historical(currency, old).rate


def test_older_principal_keeps_its_id_balance_and_open_orders(tmp_path):
    path = tmp_path / "sentinelle.db"
    db = Database(path)
    aid = db.insert("accounts", {"name":"ancien", "kind":"paper", "role":"principal", "quote":"EUR", "initial_balance":30})
    db.execute("INSERT INTO balances VALUES (?,?,?)", (aid, "EUR", 17.25))
    db.close()
    for _ in range(2):
        app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2),
                  settings_override={"market":{"source":"synthetic"}, "general":{"quote":"EUR","watchlist":["BTC/EUR"]}})
        assert app.main_account()["id"] == aid
        assert app.db.scalar("SELECT total FROM balances WHERE account_id=?", (aid,)) == 17.25
        assert app.ledger_check(aid)["ok"] and app.ledger_check(aid)["entries"] == 1
        app.db.close()
