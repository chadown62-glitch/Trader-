"""Append-only causal journal of the paper session (campaign guarantee 5, SPEC.md, Sprint 5 extension).

Every cycle appends one trace, in the same transaction as the session state: quotes and their provenance, signals,
Guardian verdicts, fleet decisions, decided and resolved orders, fills, valuation and errors. Rows are never updated or
deleted, and they are enough to rebuild the results. Offline and deterministic."""

import json

import pytest

from sentinelle.dmh import memecoin_simulator as sim
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT
from tests.test_memecoin_engine import ACCELERATION, causal_session

KEY = "momentum/" + MINT


def trading_session(db):
    """Baseline, momentum decision, fill, stop decided, stop filled: five cycles with every kind of journal entry."""
    feed, clock, step = causal_session(db)
    step(0)
    step(60, **ACCELERATION)
    step(60, priceUsd="1.02")
    step(60, priceUsd="0.7")
    step(60, priceUsd="0.3")
    return feed, clock, step


def test_every_cycle_appends_one_trace_that_rebuilds_the_results(db):
    trading_session(db)
    state = sim.load(db)[0]
    rows = sim.journal(db)
    assert [r["revision"] for r in rows] == list(range(1, state["revision"] + 1))  # one row per cycle, none missing
    rebuilt = sim.replay(rows)
    assert rebuilt["fills"] == state["fills"] and rebuilt["curve"] == state["curve"] and rebuilt["fleet"] == state["fleet"]
    assert rebuilt["order_log"] == state["order_log"] and rebuilt["cash"] == pytest.approx(state["cash"])
    assert [(f["side"], f["strategy"]) for f in rebuilt["fills"]][:2] == [("buy", "momentum"), ("sell", "momentum")]
    assert state["completed"][0]["exit"] == "stop_loss"
    decision, fill = rows[1], rows[2]
    [oid] = decision["decided"]
    assert oid.endswith(":buy:" + KEY) and decision["evaluations"][MINT]["signals"] == ["momentum"]
    assert decision["evaluations"][MINT]["allowed"] is True and decision["evaluations"][MINT]["gap_s"] == 60
    quote = fill["quotes"][MINT]  # the execution quote with its provenance
    assert quote["price"] == 1.02 and len(quote["pool_sha256"]) == 64 and isinstance(quote["received_ms"], int)
    assert [f["order_id"] for f in fill["fills"]] == [oid] and fill["resolved"][0]["status"] == "executed"
    assert fill["valuation"]["status"] == "validated" and fill["valuation"]["entry_gate"]["open"]
    assert all(r["asset_domain"] == "memecoin" and r["network_fee_usd"] > 0 for r in rows)


def test_the_journal_is_append_only(db):
    trading_session(db)
    with pytest.raises(Exception, match="ajout seul"):
        db.execute("UPDATE memecoin_journal SET data='{}' WHERE revision=1")
    with pytest.raises(Exception, match="ajout seul"):
        db.execute("DELETE FROM memecoin_journal")
    assert len(sim.journal(db)) == sim.load(db)[0]["revision"]


def test_a_cycle_that_fails_to_commit_leaves_neither_state_nor_trace(db, monkeypatch):
    feed, clock, step = causal_session(db)
    step(0)
    before, rows = sim.load(db)[0], sim.journal(db)
    real_get_meta, reads = db.get_meta, []

    def get_meta(key, default=None):  # another worker commits between this tick's load and its own commit
        value = real_get_meta(key, default)
        if key == sim.KEY:
            reads.append(1)
            if len(reads) == 2:  # the revision check inside the commit transaction
                raw = json.loads(value)
                raw["revision"] += 1
                return json.dumps(raw)
        return value

    monkeypatch.setattr(db, "get_meta", get_meta)
    with pytest.raises(ValueError, match="session modifiée"):
        step(60, priceUsd="1.01")
    monkeypatch.setattr(db, "get_meta", real_get_meta)
    assert sim.journal(db) == rows and sim.load(db)[0] == before  # rolled back together: no trace without its state


def test_the_journal_keeps_what_the_session_logs_cap(db, monkeypatch):
    monkeypatch.setattr(sim, "ORDER_LOG", 1)  # the in-session log keeps a single resolved order...
    trading_session(db)
    state = sim.load(db)[0]
    assert len(state["order_log"]) == 1
    resolved = sim.replay(sim.journal(db))["order_log"]  # ...the journal keeps all of them
    mine = [r["status"] for r in resolved if r["key"] == KEY]  # the momentum entry and its stop, both filled
    assert mine == ["executed", "executed"] and len(resolved) >= 2 and resolved[-1] == state["order_log"][-1]


def test_a_cycle_without_network_fee_or_quotes_is_still_traced(db, monkeypatch):
    feed, clock, step = causal_session(db)
    step(0)
    monkeypatch.setattr(sim, "network_fee", lambda client: (_ for _ in ()).throw(ValueError("frais réseau indisponibles")))
    feed.pair["pairAddress"] = "GONEPOOL"
    step(60)
    last = sim.journal(db)[-1]
    assert last["network_fee_usd"] is None and last["quotes"][MINT] == {"error": "paire exacte indisponible"}
    assert "frais réseau indisponibles" in last["errors"] and last["valuation"]["entry_gate"]["open"] is False


def test_a_session_recorded_before_the_journal_gets_the_table_on_load(db):
    db.execute("DROP TRIGGER IF EXISTS memecoin_journal_no_update")
    db.execute("DROP TRIGGER IF EXISTS memecoin_journal_no_delete")
    db.execute("DROP TABLE IF EXISTS memecoin_journal")
    assert sim.journal(db) == []  # recreated, empty: no history is invented for the cycles before it
    assert db.scalar("SELECT count(*) FROM sqlite_master WHERE name LIKE 'memecoin_journal%'") == 3


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
