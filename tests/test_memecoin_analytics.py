import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from scripts import check_memecoin_pc as pc
from sentinelle.dmh import memecoin_analytics as analytics
from sentinelle.dmh import memecoin_simulator as sim


def save(path, state):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES (?,?)", (sim.KEY, json.dumps(state)))
        conn.commit()


def session(results):
    cash = 100.0
    state = dict(initial=cash, revision=1, curve=[], positions={}, completed=[], fills=[], errors=[])
    for i, pnl in enumerate(results):
        strategy = sim.NAMES[i % 4]
        mint = "A" if strategy == "sniper" else "B"
        for side, value in (("buy", 0), ("sell", 1), ("sell", pnl - 1)):
            state["fills"].append(dict(asset_domain="memecoin", simulated=True, side=side, mint=mint, strategy=strategy, pnl=value))
        state["completed"].append(dict(strategy=strategy, mint=mint, **{"return": pnl / 100}))
        cash += pnl
        state["curve"].append({"equity": cash})
    state["cash"] = cash
    return state


def test_absent_empty_and_read_only(tmp_path, monkeypatch):
    path = tmp_path / "session #é?.db" if os.name != "nt" else tmp_path / "session #é.db"
    assert analytics.build_analytics_report(path)["status"] == "empty"
    assert not path.exists()
    path.touch()
    empty = analytics.build_analytics_report(path)
    assert empty["status"] == "empty" and empty["avg_profit_pct"] is None
    assert empty["open_positions"] == empty["recent_closed_positions"] == []
    save(path, session([]))
    connect = sqlite3.connect

    def checked(database, **kwargs):
        conn = connect(database, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM meta")
        return conn

    monkeypatch.setattr(sqlite3, "connect", checked)
    result = analytics.build_analytics_report(path)
    assert result["equity"] == 100 and result["win_rate"] is None


@pytest.mark.parametrize("results,win,pf,dd", [
    ([20], 1, None, 0), ([-10], 0, 0, 10), ([20, -12, 0, 0, 4], 0.4, 2, 10),
])
def test_closed_positions_and_drawdown(tmp_path, results, win, pf, dd):
    path = tmp_path / "paper.sqlite"
    save(path, session(results))
    r = analytics.build_analytics_report(path)
    assert r["win_rate"] == win and r["profit_factor"] == pf
    assert r["closed_trades"] == len(results) and r["max_drawdown_pct"] == pytest.approx(dd)
    assert r["net_pnl"] == r["net_pnl_pct"] == sum(results)
    assert r["avg_profit_pct"] == pytest.approx(sum(results) / len(results))  # return = pnl / 100 USD staked
    for i, name in enumerate(sim.NAMES):
        mine = results[i::4]
        assert r["strategies"][name] == {"pnl": sum(mine), "trades": len(mine),
                                         "avg_profit_pct": pytest.approx(sum(mine) / len(mine)) if mine else None}
    assert [lot["pnl_usd"] for lot in r["recent_closed_positions"]] == results[::-1]
    assert r["open_positions"] == []
    if len(results) == 5:
        assert r["top_tokens"] == [("A", 24)] and r["flop_tokens"] == [("B", -12)]


def test_stale_open_partial_and_foreign_domain(tmp_path):
    state = session([10])
    state["fills"].extend(dict(side=side, pnl=v, strategy="sniper", mint="A")
                          for side, v in (("buy", 0), ("sell", -3)))
    state["positions"]["sniper/A"] = dict(strategy="sniper", mint="A", qty=2, initial=10, mark=5, basis=7, pnl=-3, stage=1)
    state["stale_marks"] = ["A"]
    path = tmp_path / "paper.sqlite"
    save(path, state)
    report = analytics.build_analytics_report(path)
    assert report["equity"] is report["net_pnl"] is None
    assert report["gross_profit"] == 10 and report["gross_loss"] == 0
    assert report["avg_profit_pct"] == pytest.approx(10)  # closed lots only: the open one does not count
    [lot] = report["open_positions"]
    assert (lot["value_usd"], lot["unrealized_pnl_usd"], lot["realized_pnl_usd"], lot["stale"]) == (5, -2, -3, True)
    assert lot["unrealized_pnl_pct"] == pytest.approx(-200 / 7)
    state["fills"][0]["asset_domain"] = "majors"
    save(tmp_path / "mixed.db", state)
    with pytest.raises(ValueError, match="domaine"):
        analytics.build_analytics_report(tmp_path / "mixed.db")


def lot_session():
    """Unequal stakes, partial exits and interleaved lots: the average is per lot, not weighted by size."""
    fills = []

    def fill(ts, strategy, mint, side, pnl=0.0, reason="signal", **costs):
        fills.append(dict(asset_domain="memecoin", simulated=True, ts=ts, strategy=strategy, mint=mint, side=side,
                          pnl=pnl, reason=reason, **costs))

    fill(1, "sniper", "A", "buy")                           # stake 10 USD
    fill(2, "momentum", "B", "buy")                         # stake 5 USD
    fill(3, "sniper", "A", "sell", 2, "palier 1,25x")
    fill(4, "momentum", "B", "sell", 1, "prise de profit")  # momentum/B closed: +1 / 5 = +20 %
    fill(5, "sniper", "A", "sell", 3, "stop suiveur")       # sniper/A closed: (2 + 3) / 10 = +50 %
    fill(6, "sniper", "A", "buy")                           # stake 4 USD
    fill(7, "sniper", "A", "sell", -1, "stop -20 %")        # sniper/A closed again: -1 / 4 = -25 %
    # stake 8 USD: 75 tokens at 0.10 USD once 0.20 + 0.05 + 0.25 USD of DEX fee, network fee and slippage are paid
    fill(8, "whale_tail", "C", "buy", quantity=75, dex_fee_usd=0.2, network_fee_usd=0.05, slippage_usd=0.25)
    fill(9, "whale_tail", "C", "sell", 0.5, "palier 1,25x")  # 20 % sold at 1.25x (calibrated-v1), still open
    fill(10, "dip_hunter", "D", "buy")                      # stake 4 USD, legacy fill without costs, quote not refreshed
    completed = [dict(strategy="momentum", mint="B", **{"return": 0.2}), dict(strategy="sniper", mint="A", **{"return": 0.5}),
                 dict(strategy="sniper", mint="A", **{"return": -0.25})]
    positions = {
        "whale_tail/C": dict(asset_domain="memecoin", strategy="whale_tail", mint="C", qty=60, initial=8, basis=6.4, mark=7.2,
                             pnl=0.5, stage=1, high=1.3, exit_policy=sim.CALIBRATED_EXITS),
        "dip_hunter/D": dict(asset_domain="memecoin", strategy="dip_hunter", mint="D", qty=40, initial=4, basis=4, mark=3,
                             pnl=0.0, stage=0, high=1.0),  # opened under the legacy exits
    }
    assets = {"C": {"symbol": "C", "last": {"price": 0.125, "ts": 9}}, "D": {"symbol": "D"}}
    return dict(initial=100, cash=88.5, revision=10, curve=[{"equity": 101}, {"equity": 99}], positions=positions,
                completed=completed, fills=fills, assets=assets, errors=[], stale_marks=["D"])


def test_average_profit_pct_and_open_positions_are_exact(tmp_path):
    path = tmp_path / "paper.sqlite"
    save(path, lot_session())
    r = analytics.build_analytics_report(path)
    assert r["status"] == "stale" and r["equity"] is None  # D is not marked: no live equity, the averages remain exact
    assert r["avg_profit_pct"] == pytest.approx((20 + 50 - 25) / 3)
    averages = {name: stats["avg_profit_pct"] for name, stats in r["strategies"].items()}
    assert averages == {"sniper": pytest.approx(12.5), "momentum": pytest.approx(20), "dip_hunter": None, "whale_tail": None}
    assert (r["closed_trades"], r["gross_profit"], r["gross_loss"], r["profit_factor"]) == (3, 6, 1, 6)
    assert r["recent_closed_positions"] == [
        {"strategy": "sniper", "mint": "A", "pnl_usd": -1, "profit_pct": pytest.approx(-25), "opened_at": 6, "closed_at": 7,
         "exit_reason": "stop -20 %", "exits": 1},
        {"strategy": "sniper", "mint": "A", "pnl_usd": 5, "profit_pct": pytest.approx(50), "opened_at": 1, "closed_at": 5,
         "exit_reason": "stop suiveur", "exits": 2},
        {"strategy": "momentum", "mint": "B", "pnl_usd": 1, "profit_pct": pytest.approx(20), "opened_at": 2, "closed_at": 4,
         "exit_reason": "prise de profit", "exits": 1},
    ]
    assert r["open_positions"] == [
        {"strategy": "whale_tail", "mint": "C", "quantity": 60, "entry_price_usd": pytest.approx(0.1), "price_usd": 0.125,
         "initial_usd": 8, "cost_basis_usd": 6.4, "value_usd": 7.2, "unrealized_pnl_usd": pytest.approx(0.8),
         "unrealized_pnl_pct": pytest.approx(12.5), "realized_pnl_usd": 0.5, "exits": 1, "exit_policy": "calibrated-v1",
         "tier_reached": 1.25, "next_tier": 3.0, "peak_ratio": 1.3, "marked_at": 9, "opened_at": 8, "stale": False},
        {"strategy": "dip_hunter", "mint": "D", "quantity": 40, "entry_price_usd": None, "price_usd": None,
         "initial_usd": 4, "cost_basis_usd": 4, "value_usd": 3, "unrealized_pnl_usd": -1,
         "unrealized_pnl_pct": pytest.approx(-25), "realized_pnl_usd": 0, "exits": 0, "exit_policy": "legacy",
         "tier_reached": None, "next_tier": 1.5, "peak_ratio": 1.0, "marked_at": None, "opened_at": 10, "stale": True},
    ]
    text = analytics.render_report(r)
    assert "Profit moyen par clôture : 15.00 %" in text and "sniper : 4.00 | 2 | 12.50" in text
    assert "whale_tail C : entrée 0.1 → 0.125 USD | valeur 7.20 USD | latent 0.80 USD (12.50 %) | palier 1,25x" in text
    assert ("dip_hunter D : entrée n/d → n/d USD | valeur 3.00 USD | latent -1.00 USD (-25.00 %) | palier aucun"
            " | cotation en attente") in text


@pytest.mark.parametrize("policy,stage,reached,upcoming", [
    ("calibrated-v1", 0, None, 1.25), ("calibrated-v1", 1, 1.25, 3), ("calibrated-v1", 2, 3, 6),
    ("calibrated-v1", 3, 6, 10), ("calibrated-v1", 4, 10, None),  # after x10 only the trailing stop remains
    ("legacy", 0, None, 1.5), ("legacy", 1, 1.5, 2), ("legacy", 2, 2, None), ("inconnue", 1, None, None),
])
def test_tier_reached_follows_the_exit_policy_of_the_lot(policy, stage, reached, upcoming):
    assert analytics._tier({"exit_policy": policy, "stage": stage}) == (policy, reached, upcoming)


def test_closed_lot_without_matching_record_is_refused(tmp_path):
    state = lot_session()
    state["completed"][0]["strategy"] = "whale_tail"  # same count, but no whale_tail/B lot was ever closed
    save(tmp_path / "paper.sqlite", state)
    with pytest.raises(ValueError, match="incomplet"):
        analytics.build_analytics_report(tmp_path / "paper.sqlite")


@pytest.mark.parametrize("number,base,head,expected", pc.FROZEN)
def test_frozen_manifests(number, base, head, expected):
    assert pc.manifest(base, head) == expected


def test_cli(tmp_path):
    path = tmp_path / "absent.sqlite"
    result = subprocess.run([sys.executable, str(pc.ROOT / "scripts/check_memecoin_pc.py"), "--db-path", str(path)],
                            cwd=tmp_path, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, encoding="utf-8")
    assert "Aucune session enregistrée" in result.stdout and not path.exists()
    assert "[OK] Manifeste SHA-256 #55" in result.stdout
    assert "python -m sentinelle --demo --port 8000" in result.stdout
    assert result.returncode == int("[ÉCHEC]" in result.stdout)


def test_read_during_worker_transaction(tmp_path):
    path = tmp_path / "active.db"
    save(path, session([2]))
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE meta SET value=?", (json.dumps(session([7])),))
        assert analytics.build_analytics_report(path)["net_pnl"] == 2
        writer.commit()
        assert analytics.build_analytics_report(path)["net_pnl"] == 7


def test_pnl_eur_uses_each_own_dated_book_and_never_the_deposit(tmp_path):
    from dataclasses import asdict
    from sentinelle.market.fx import FXBook, FXQuote, FxQuote
    from sentinelle.storage.db import Database, dumps

    db = Database(tmp_path / "session.sqlite")
    try:
        state = lot_session()
        state["deposit"] = {"currency": "EUR", "amount": 30, "fx": {"rate": 17}}
        state["assets"]["C"]["last"]["received_ms"] = 10001
        state["positions"]["whale_tail/C"]["marked_at"] = 9
        for fill in state["fills"]:
            fill["execution_received_ms"] = (10001 if fill["ts"] == 9 else fill["ts"] * 1000)
        db.set_meta(sim.KEY, dumps(state))
        # Choix de taux intentionnellement distincts pour reconnaître la date de chaque mouvement.
        for ts, bid, ask in [(2, 1, 2), (4, 2, 4), (8, 4, 8), (10, 8, 16)]:
            book = FXBook([FXQuote("EUR", "USD", bid, ask, ts, "carnet de test", 100, 100)])
            quote = FxQuote.from_book("EUR", "USD", book, 3)
            db.execute("INSERT INTO fx_snapshots VALUES(?,?,?,?,?)", ("EUR", "USD", ts, quote.source, dumps(asdict(quote))))
        result = analytics.build_analytics_report(db.path)
        open_lot, stale = result["open_positions"]
        assert open_lot["realized_eur"]["amount"] == pytest.approx(0.5 / 16)
        assert open_lot["unrealized_eur"]["amount"] == pytest.approx(0.8 / 16)
        assert open_lot["realized_eur"]["legs"] == [{"usd": 0.5, "event_at": 10.001, "rate": 0.0625,
                                                   "eur": 0.03125, "source": "carnet de test", "quote_at": 10}]
        assert "unrealized_eur" not in stale
        newest, first, _ = result["recent_closed_positions"]
        assert newest["realized_eur"]["amount"] == pytest.approx(-1 / 2)  # perte : rachat d'USD au bid EUR/USD
        assert first["realized_eur"]["amount"] == pytest.approx(2 / 2 + 3 / 4)
        assert [leg["event_at"] for leg in first["realized_eur"]["legs"]] == [3, 5]
        assert open_lot["marked_at"] == open_lot["quote_received_at"] == 10.001
        state["assets"]["C"]["last"].pop("received_ms")
        for fill in state["fills"]:
            fill.pop("execution_received_ms")
        db.set_meta(sim.KEY, dumps(state))
        old = analytics.build_analytics_report(db.path)
        assert all("realized_eur" not in lot for lot in old["recent_closed_positions"] + old["open_positions"])
        assert "unrealized_eur" not in old["open_positions"][0]
        for fill in state["fills"]:
            fill["execution_received_ms"] = fill["ts"] * 1000
        db.set_meta(sim.KEY, dumps(state))
        db.execute("DELETE FROM fx_snapshots WHERE ts < 10")
        missing = analytics.build_analytics_report(db.path)
        assert all("realized_eur" not in lot for lot in missing["recent_closed_positions"] + missing["open_positions"])
        assert missing["open_positions"][0]["realized_pnl_usd"] == 0.5
        assert db.get_meta(sim.KEY) == dumps(state)
    finally:
        db.close()


def test_filled_memecoin_lot_is_reported_without_debiting_the_main_paper_ledger(tmp_path):
    from tests.test_memecoin_engine import causal_session, ACCELERATION, MINT
    from tests.test_portfolio import make_app

    app = make_app(tmp_path)
    try:
        main = app.main_account()["id"]
        ledger = app.ledger(main)
        feed, _, step = causal_session(app.db)
        feed.pair["baseToken"].update(name="Token synthétique", symbol="TST")
        step(0)
        step(60, **ACCELERATION)
        bought = step(60, priceUsd="1.02")
        assert bought["activity"][-1]["side"] == "buy"
        assert app.account_summary(main)["cash"] == 30 and app.ledger(main) == ledger
        rep = analytics.build_analytics_report(app.db.path)
        assert rep["tokens"][MINT]["name"] == "Token synthétique" and rep["tokens"][MINT]["symbol"] == "TST"
        [lot] = rep["open_positions"]
        assert lot["quote_received_at"] == bought["activity"][-1]["execution_received_ms"] / 1000
        deciding = step(60, priceUsd="0.7")
        pending = next(x for x in deciding["pending_orders"] if x["kind"] == "sell")
        assert pending["detail"] == "stop_loss" and not any(f["side"] == "sell" for f in deciding["activity"])
        sold = step(60, priceUsd="0.3")
        assert any(f["side"] == "sell" for f in sold["activity"])
        assert app.account_summary(main)["cash"] == 30 and app.ledger(main) == ledger
        rep = analytics.build_analytics_report(app.db.path)
        assert rep["recent_closed_positions"][0]["exit_reason"] == "stop_loss"
    finally:
        app.close()
