import json
import sqlite3
from pathlib import Path

import pytest

from sentinelle.dmh import memecoin_cloud_runner as runner
from sentinelle.dmh import memecoin_simulator as sim
from tests import test_configurable_capital as frozen


def test_cli_ticks_restart_and_domain(tmp_path, monkeypatch):
    db = tmp_path / "session.sqlite"
    output = tmp_path / "report.json"
    calls = []

    def scan(client, database, bus, registry):
        calls.append(1)
        return {"errors": []}

    monkeypatch.setattr(sim, "scan_once", scan)
    args = ["--db-path", str(db), "--report-path", str(output)]
    runner.main(args + ["--ticks", "2", "--initial-capital", "100"])
    first = json.loads(output.read_text(encoding="utf-8"))
    runner.main(args)
    second = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 3 and second["revision"] == first["revision"] + 1
    assert second["initial_capital"] == second["final_equity"] == 100
    assert second["asset_domain"] == sim.DOMAIN and second["win_rate"] is None
    assert second["net_pnl"] == second["total_trades"] == 0 and second["paper_only"]
    assert set(second["strategies"]) == {"sniper", "momentum", "dip_hunter", "whale_tail"}
    assert "Classement par token" in output.with_suffix(".txt").read_text(encoding="utf-8")
    dbh = runner.open_session(db)
    try:
        assert len(sim.load(dbh)[0]["curve"]) == 3
        assert dbh.scalar("SELECT count(*) FROM accounts") == dbh.scalar("SELECT count(*) FROM fills") == 0
    finally:
        dbh.close()
    for n in ("0", str(runner.MAX_TICKS + 1)):
        with pytest.raises(SystemExit):
            runner.main(args + ["--ticks", n])
    assert len(calls) == 3


def test_closed_results_and_stale_rpc(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite"
    output = tmp_path / "report.json"
    db = runner.open_session(path)
    try:
        state, _ = sim.load(db)
        fills = [
            {"mint": "test", "strategy": "momentum", "side": side, "pnl": pnl}
            for realized in (3.0, -1.5, 0.0)
            for side, pnl in (("buy", 0), ("sell", realized))
        ]
        state.update(
            cash=31.5, revision=2, fills=fills, completed=[{"mint": "test", "strategy": "momentum", "return": r} for r in (0.3, -0.15, 0)]
        )
        db.set_meta(sim.KEY, json.dumps(state))
    finally:
        db.close()

    def unavailable(*args, **kwargs):
        raise runner.SourceUnavailable("Solana", "source indisponible")

    monkeypatch.setattr(sim, "scan_once", unavailable)
    result = runner.run(3, path, output)
    assert result["ticks_completed"] == 0 and len(result["source_errors"]) == 1
    assert result["win_rate"] == 1 / 3 and result["closed_trades"] == result["total_trades"] == 3
    assert result["final_equity"] == 31.5 and result["net_pnl"] == 1.5
    assert result["strategies"]["momentum"]["pnl"] == 1.5 and result["token_ranking"] == [("test", 1.5)]
    db = runner.open_session(path)
    try:
        state, _ = sim.load(db)
        state["positions"]["momentum/test"] = {"mark": 2, "basis": 2, "mint": "test", "strategy": "momentum", "pnl": 0}
        db.set_meta(sim.KEY, json.dumps(state))
    finally:
        db.close()
    latest = runner.run(1, path, output)
    assert latest["final_equity"] is None and latest["net_pnl"] is None
    assert latest["stale_marks"] == ["test"]


def test_foreign_db_is_not_adopted(tmp_path):
    path = tmp_path / "cockpit.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE accounts(id INTEGER)")
    with pytest.raises(ValueError, match="dédiée"):
        runner.run(1, path, tmp_path / "report.json")


def test_devcontainer_private_start():
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".devcontainer/devcontainer.json").read_text(encoding="utf-8"))
    assert "3.12" in cfg["image"] and "requirements.txt" in cfg["postCreateCommand"]
    assert cfg["forwardPorts"] == [8000]
    assert cfg["portsAttributes"]["8000"]["visibility"] == "private"
    assert cfg["portsAttributes"]["8000"]["onAutoForward"] == "openBrowser"
    assert "python -m sentinelle --remote --port 8000 --no-browser" in cfg["postStartCommand"][-1]


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_file_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
