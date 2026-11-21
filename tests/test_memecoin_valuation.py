"""Valuation in three states and bounded watching (campaign guarantees 2 and 4, SPEC.md, Sprint 5 extension).

Confirmed liquidation value, unavailable valuation and prudent estimate are never confused; a lot without a valid quote
never freezes the session for good; the watched assets are bounded and those carrying a pending order are quoted first.
Offline and deterministic: the established-pool feed of the engine tests, a clock moved by the test."""

import json

import pytest

from sentinelle.dmh import memecoin_simulator as sim
from sentinelle.fleet import coordinator as fleet_coordinator
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT, NOW, POOL, WSOL
from tests.test_memecoin_engine import ACCELERATION, causal_session, entered

KEY = "momentum/" + MINT


def lot(mint, mark, **extra):
    return {"mint": mint, "strategy": "momentum", "qty": 1.0, "basis": mark, "initial": mark, "mark": mark, "high": 1.0,
            "stage": 0, "pnl": 0.0, "exit_policy": sim.CALIBRATED_EXITS, "asset_domain": "memecoin", "opened_at": 0, **extra}


def test_a_drained_pool_is_a_confirmed_zero_and_never_freezes_the_session(db):
    feed, clock, step, order = entered(db)
    cash = sim.load(db)[0]["cash"]
    state = step(60, priceUsd="0.5", liquidity={"usd": 0})  # the pool is reported with no depth at all: an observation
    pos = state["positions"][KEY]
    assert pos["mark"] == 0.0 and pos["marked_at"] == clock["t"] and "unavailable_since" not in pos
    assert state["equity_status"] == "validated" and state["equity"] == pytest.approx(cash) and state["estimate"] is None
    assert state["stale_marks"] == [] and state["entry_gate"]["open"]  # nothing is frozen: the session goes on
    [sell] = [o for o in state["pending_orders"] if o["kind"] == "sell"]  # the stop is decided on the confirmed value
    state = step(60, priceUsd="0.4", liquidity={"usd": 0})
    assert state["cash"] == pytest.approx(cash) and KEY in state["positions"]  # nothing realizable: no cash credited
    assert "vente impossible" in sim.load(db)[0]["orders"][sell["id"]]["last_refusal"]


def test_a_vanished_pool_blocks_entries_only_during_the_grace_then_is_isolated(db):
    feed, clock, step, order = entered(db)
    before = sim.load(db)[0]
    last_mark, cash = before["positions"][KEY]["mark"], before["cash"]
    feed.pair["pairAddress"] = "GONEPOOL"  # the exact pair disappears from the response
    state = step(60)
    gone_at = clock["t"]
    assert state["equity"] is None and state["equity_status"] == "stale" and state["stale_marks"] == [MINT]
    est = state["estimate"]  # shown apart, explicitly an estimate, never as the equity
    assert est["status"] == "estimate" and est["floor_usd"] == pytest.approx(cash)
    assert est["last_known_usd"] == pytest.approx(cash + last_mark)
    assert est["unavailable_lots"] == [{"key": KEY, "mint": MINT, "last_confirmed_usd": last_mark,
                                        "marked_at": gone_at - 60, "unavailable_since": gone_at}]
    assert not state["entry_gate"]["open"] and state["entry_gate"]["blocking"] == [MINT]  # transient: entries wait
    state = step(sim.UNAVAILABLE_GRACE_S - 60)  # still within the grace
    assert not state["entry_gate"]["open"]
    state = step(60)  # unavailable for the whole grace: durably unavailable, isolated, entries resume
    assert state["entry_gate"] == {"open": True, "reason": None, "blocking": []}
    assert state["equity"] is None and state["estimate"]["floor_usd"] == pytest.approx(cash)  # still never validated
    assert sim.load(db)[0]["positions"][KEY]["unavailable_since"] == gone_at
    feed.pair["pairAddress"] = POOL  # the pool comes back with a new state: confirmed again, as usual
    state = step(60, priceUsd="1.03")
    pos = state["positions"][KEY]
    assert state["equity_status"] == "validated" and state["estimate"] is None and pos["marked_at"] == clock["t"]
    assert "unavailable_since" not in pos and state["equity"] == pytest.approx(state["cash"] + pos["mark"])


def test_new_stakes_count_an_unquoted_lot_for_nothing_and_keep_the_stricter_reserve():
    def session(stale):
        return {"revision": 0, "cash": 20.0, "initial": 30.0, "assets": {"U": {"symbol": "U"}, "N": {"symbol": "N"}},
                "positions": {"momentum/U": lot("U", 10.0)}, "fills": [], "completed": [], "curve": [], "errors": [],
                "exit_policy": sim.CALIBRATED_EXITS, "stale_marks": stale}

    unquoted, quoted = session(["U"]), session([])
    assert (sim.sizing_equity(unquoted), sim.known_equity(unquoted), sim.equity(unquoted)) == (20.0, 30.0, 30.0)
    assert sim.sizing_equity(quoted) == 30.0
    point = {"price": 1.0, "liquidity_usd": 1_000_000.0, "volume_h1_usd": 10_000.0, "ts": NOW}
    for state in (unquoted, quoted):
        sim.process(state, "N", point, ["dip_hunter"], 0.001)  # an entry on another token
    small, large = unquoted["positions"]["dip_hunter/N"]["sizing"], quoted["positions"]["dip_hunter/N"]["sizing"]
    assert small["size_usd"] <= 0.10 * 20.0 + 1e-9 and large["size_usd"] <= 0.10 * 30.0 + 1e-9  # token cap on each basis
    assert small["size_usd"] < large["size_usd"]
    data = sim._candidate(session(["U"]), "N", {"round_trip_loss": 0.02}, point, ["dip_hunter"], {"allowed": True}, 0.001)
    assert data["equity_usd"] == 20.0 and data["notional_usd"] == pytest.approx(min(2.0, 20.0 - 0.25 * 30.0))


def test_a_lot_without_quote_and_no_network_fee_keep_entries_closed():
    state = {"positions": {"momentum/U": lot("U", 10.0, unavailable_since=NOW - 10_000)}, "stale_marks": ["U"]}
    assert sim.entry_gate(state, NOW, None) == {"open": False, "reason": "frais réseau indisponibles", "blocking": []}
    assert sim.entry_gate(state, NOW, 0.001)["open"]  # durably unavailable, network fee known
    state["positions"]["momentum/U"]["unavailable_since"] = NOW - 10
    assert sim.entry_gate(state, NOW, 0.001) == {"open": False, "reason": "valorisation momentanément indisponible",
                                                 "blocking": ["U"]}


def test_a_day_with_an_unquoted_lot_is_estimated_on_prudent_bounds_never_measured():
    curve = [{"ts": 0, "equity": 30.0, "floor": 30.0, "last_known": 30.0},
             {"ts": 86_400, "equity": None, "floor": 27.0, "last_known": 33.0}]
    day = sim.return_24h({"curve": curve, "stale_marks": ["U"]})
    assert (day["status"], day["pct"], day["reason"]) == ("estimated", None, "valorisation de fin non validée")
    assert day["estimate"]["floor"] == {"pct": pytest.approx(-10.0), "from_ts": 0, "window_s": 86_400}
    assert day["estimate"]["last_known"]["pct"] == pytest.approx(10.0)
    legacy = [{"ts": 0, "equity": 30.0}, {"ts": 86_400, "equity": None}]  # sessions recorded before the bounds existed
    assert sim.return_24h({"curve": legacy, "stale_marks": ["U"]})["status"] == "not_evaluable"
    short = [{"ts": 0, "equity": 30.0, "floor": 30.0, "last_known": 30.0}, {"ts": 600, "equity": None, "floor": 27.0, "last_known": 30.0}]
    assert sim.return_24h({"curve": short, "stale_marks": ["U"]})["status"] == "not_evaluable"  # no day, no estimate either
    measured = sim.return_24h({"curve": [curve[0], {"ts": 86_400, "equity": 31.5, "floor": 31.5, "last_known": 31.5}], "stale_marks": []})
    assert measured["status"] == "measured" and "estimate" not in measured


def test_the_watch_list_is_bounded_to_the_most_recently_admitted_pools():
    assets = {f"A{i:02d}": {"symbol": f"A{i:02d}", "admitted_ms": i, "checked": 100 - i} for i in range(40)}
    rotation = sim.watch_rotation(assets, {"A39"})
    eligible = sorted(assets, key=lambda m: -assets[m]["admitted_ms"])[1:1 + sim.WATCH_LIMIT]  # A39 excluded as priority
    assert len(rotation) == sim.ROTATION == 4 and set(rotation) <= set(eligible)
    assert rotation == sorted(eligible, key=lambda m: assets[m]["checked"])[:4]  # the least recently checked first
    assert sim.WATCH_LIMIT == 24


def test_an_asset_with_a_pending_order_is_quoted_first_whatever_the_rotation(db):
    feed, clock, step = causal_session(db)
    step(0)
    step(60, **ACCELERATION)
    state = sim.load(db)[0]
    [oid] = state["orders"]
    for i in range(30):  # thirty pools admitted after this one: it is now outside the watch list
        state["assets"][f"X{i:02d}"] = {"symbol": f"X{i:02d}", "pool": f"P{i:02d}", "quote_mint": WSOL, "dex_id": "pumpswap",
                                        "creation": {"block_time": NOW - 7200}, "admitted_ms": (NOW + 10_000 + i) * 1000}
    db.set_meta(sim.KEY, json.dumps(state))
    filled = step(60, priceUsd="1.02")
    assert [f["order_id"] for f in filled["activity"] if f["side"] == "buy"] == [oid]  # quoted although out of rotation
    stored = sim.load(db)[0]["assets"]
    assert sum(1 for i in range(30) if stored[f"X{i:02d}"].get("checked") == clock["t"]) == sim.ROTATION


def test_identity_cash_plus_marks_still_holds_with_an_unavailable_lot(db):
    feed, clock, step, order = entered(db)
    feed.pair["pairAddress"] = "GONEPOOL"
    step(60)
    state = sim.load(db)[0]
    assert sim.equity(state) == pytest.approx(state["cash"] + sum(p["mark"] for p in state["positions"].values()))
    assert state["curve"][-1] == {"ts": clock["t"], "equity": None, "floor": pytest.approx(state["cash"]),
                                  "last_known": pytest.approx(sim.equity(state))}


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
