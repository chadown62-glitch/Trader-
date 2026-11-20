"""HTTP contract for the authenticated, read-only paper analytics panel."""

import json

import pytest
from fastapi.testclient import TestClient

from sentinelle.dmh import memecoin_simulator as sim
from tests import test_configurable_capital as frozen, test_dmh_api

api_setup = test_dmh_api.setup
URL = "/api/dmh/memecoin/analytics"


@pytest.fixture
def session_api(api_setup):
    app, client, _ = api_setup
    app.db.execute("DELETE FROM meta WHERE key=?", (sim.KEY,))
    yield app.db, client
    app.db.execute("DELETE FROM meta WHERE key=?", (sim.KEY,))


def test_requires_login(session_api):
    _, client = session_api
    with TestClient(client.app) as anonymous:
        assert anonymous.get(URL).status_code == 401


def test_empty_ignores_other_portfolios(session_api):
    db, client = session_api
    db.set_meta("majors:paper-session:v1", json.dumps({"initial": 98765}))
    response = client.get(URL)
    assert response.status_code == 200
    report = response.json()
    assert report["status"] == "empty" and report["asset_domain"] == "memecoin" and report["paper_only"]
    assert report["equity"] is report["win_rate"] is report["max_drawdown_pct"] is None
    assert set(report["strategies"]) == set(sim.NAMES)
    assert db.get_meta(sim.KEY) is None


def test_report_and_strategy_denominators(session_api):
    db, client = session_api
    trades = [("sniper", "A", 4), ("sniper", "A", -2), ("momentum", "B", -1)]
    state = dict(initial=100, cash=101, revision=3, curve=[{"equity": 104}, {"equity": 101}],
                 positions={}, errors=[], completed=[], fills=[])
    for strategy, mint, pnl in trades:
        state["completed"].append({"strategy": strategy, "mint": mint, "return": pnl / 10})
        for side, value in (("buy", 0), ("sell", pnl)):
            state["fills"].append(dict(strategy=strategy, mint=mint, side=side, pnl=value,
                                       asset_domain="memecoin", simulated=True))
    raw = json.dumps(state)
    db.set_meta(sim.KEY, raw)
    response = client.get(URL)
    assert response.status_code == 200
    r = response.json()
    assert (r["asset_domain"], r["currency"], r["initial_capital"], r["equity"]) == ("memecoin", "USD", 100, 101)
    assert r["net_pnl"] == r["net_pnl_pct"] == 1
    assert r["win_rate"] == pytest.approx(1 / 3) and r["profit_factor"] == pytest.approx(4 / 3)
    assert r["max_drawdown_pct"] == pytest.approx(300 / 104)
    assert r["strategies"]["sniper"] == {"trades": 2, "pnl": 2, "closed_trades": 2, "win_rate": 0.5}
    assert r["strategies"]["momentum"]["win_rate"] == 0
    assert r["strategies"]["whale_tail"]["win_rate"] is None
    assert r["top_tokens"] == [["A", 2]] and r["flop_tokens"] == [["B", -1]]
    assert db.get_meta(sim.KEY) == raw
    state["fills"][0]["asset_domain"] = "majors"
    db.set_meta(sim.KEY, json.dumps(state))
    assert client.get(URL).status_code == 400


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
