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

    def fill(ts, strategy, mint, side, pnl=0.0, reason="signal"):
        fills.append(dict(asset_domain="memecoin", simulated=True, ts=ts, strategy=strategy, mint=mint, side=side,
                          pnl=pnl, reason=reason))

    fill(1, "sniper", "A", "buy")                           # stake 10 USD
    fill(2, "momentum", "B", "buy")                         # stake 5 USD
    fill(3, "sniper", "A", "sell", 2, "palier 1,25x")
    fill(4, "momentum", "B", "sell", 1, "prise de profit")  # momentum/B closed: +1 / 5 = +20 %
    fill(5, "sniper", "A", "sell", 3, "stop suiveur")       # sniper/A closed: (2 + 3) / 10 = +50 %
    fill(6, "sniper", "A", "buy")                           # stake 4 USD
    fill(7, "sniper", "A", "sell", -1, "stop -20 %")        # sniper/A closed again: -1 / 4 = -25 %
    fill(8, "whale_tail", "C", "buy")                       # stake 8 USD, still open
    fill(9, "whale_tail", "C", "sell", 0.5, "palier 1,25x")
    fill(10, "dip_hunter", "D", "buy")                      # stake 4 USD, still open, quote not refreshed
    completed = [dict(strategy="momentum", mint="B", **{"return": 0.2}), dict(strategy="sniper", mint="A", **{"return": 0.5}),
                 dict(strategy="sniper", mint="A", **{"return": -0.25})]
    positions = {
        "whale_tail/C": dict(asset_domain="memecoin", strategy="whale_tail", mint="C", qty=60, initial=8, basis=6, mark=7.5,
                             pnl=0.5, stage=1),
        "dip_hunter/D": dict(asset_domain="memecoin", strategy="dip_hunter", mint="D", qty=40, initial=4, basis=4, mark=3,
                             pnl=0.0, stage=0),
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
        {"strategy": "whale_tail", "mint": "C", "quantity": 60, "initial_usd": 8, "cost_basis_usd": 6, "value_usd": 7.5,
         "unrealized_pnl_usd": 1.5, "unrealized_pnl_pct": pytest.approx(25), "realized_pnl_usd": 0.5, "exits": 1,
         "price_usd": 0.125, "marked_at": 9, "opened_at": 8, "stale": False},
        {"strategy": "dip_hunter", "mint": "D", "quantity": 40, "initial_usd": 4, "cost_basis_usd": 4, "value_usd": 3,
         "unrealized_pnl_usd": -1, "unrealized_pnl_pct": pytest.approx(-25), "realized_pnl_usd": 0, "exits": 0,
         "price_usd": None, "marked_at": None, "opened_at": 10, "stale": True},
    ]
    text = analytics.render_report(r)
    assert "Profit moyen par clôture : 15.00 %" in text and "sniper : 4.00 | 2 | 12.50" in text
    assert "whale_tail C : valeur 7.50 USD | latent 1.50 USD (25.00 %)" in text
    assert "dip_hunter D : valeur 3.00 USD | latent -1.00 USD (-25.00 %) | cotation en attente" in text


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
