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
    assert analytics.build_analytics_report(path)["status"] == "empty"
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
    for i, name in enumerate(sim.NAMES):
        assert r["strategies"][name] == {"pnl": sum(results[i::4]), "trades": len(results[i::4])}
    if len(results) == 5:
        assert r["top_tokens"] == [("A", 24)] and r["flop_tokens"] == [("B", -12)]


def test_stale_open_partial_and_foreign_domain(tmp_path):
    state = session([10])
    state["fills"].extend(dict(side=side, pnl=v, strategy="sniper", mint="A")
                          for side, v in (("buy", 0), ("sell", -3)))
    state["positions"]["sniper/A"] = dict(strategy="sniper", mint="A", mark=5, basis=7, pnl=-3)
    state["stale_marks"] = ["A"]
    path = tmp_path / "paper.sqlite"
    save(path, state)
    report = analytics.build_analytics_report(path)
    assert report["equity"] is report["net_pnl"] is None
    assert report["gross_profit"] == 10 and report["gross_loss"] == 0
    state["fills"][0]["asset_domain"] = "majors"
    save(tmp_path / "mixed.db", state)
    with pytest.raises(ValueError, match="domaine"):
        analytics.build_analytics_report(tmp_path / "mixed.db")


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
