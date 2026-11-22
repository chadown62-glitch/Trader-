"""The causal journal keeps what ``whale_tail`` saw, pseudonymized, so each whale decision can be re-derived from it alone.

Reproduced on main c87da1a before this change: a cycle decided ``whale_tail`` (and a pending ``whale_tail`` order), but
its journaled evaluation held only the signal's name; neither the pool's attribution status, nor the tracked wallets'
swaps read on that cycle, nor the state the confluence rule read (qualification, funding origins, last buys,
exclusions) was kept, and the ``smart_wallets`` table is rewritten at every cycle. ``audit_signals`` had to leave
``whale_tail`` out and counted that row as matched. Each allowed evaluation now carries a ``whale`` trace; addresses are
replaced by keyed pseudonyms (the key stays in the session state) and signatures by their SHA-256. ``audit_whale``
re-derives the decision with an independent implementation of the confluence rule and flags any evidence dated after
its row. No decision changes. Offline and deterministic."""

import copy
import hashlib
import json

import pytest

from sentinelle.dmh import memecoin_simulator as sim, smart_wallets as sw
from tests import test_configurable_capital as frozen
from tests.test_memecoin_engine import (CREATION_SIG, CREATOR, DEPLOY_SIG, DEPLOYER, NOW, buy, causal_session,
                                        chain_with_history, gecko_asset, qualified)
from tests.test_dmh_scanner import MINT

PRIVATE = ("alice", "bob", "fund-a", "fund-b", CREATOR, DEPLOYER, "payer", "authority", CREATION_SIG, DEPLOY_SIG, "buy-")


def tracked(origin):
    """A qualified wallet whose early, profitable cycles are on this session's own winning mint."""
    w = qualified(origin)
    w["cycles"] = [{**c, "mint": MINT} for c in w["cycles"]]
    return w


def whale_session(db, monkeypatch):
    """An attributable pool (its creation and the mint's deployment are on the chain), two qualified wallets funded
    independently, and their buys a few seconds before the second cycle."""
    chain = chain_with_history()
    monkeypatch.setattr(sw, "_rpc", lambda client, network, method, params=None: chain.rpc(None, method, params))
    feed, clock, step = causal_session(db)
    step(0)  # the pool is attributed; no wallet is tracked yet
    state, _ = sim.load(db)
    state["assets"][MINT]["winner"] = True
    db.set_meta(sim.KEY, json.dumps(state))
    for name, origin in (("alice", "fund-a"), ("bob", "fund-b")):
        db.execute("INSERT OR REPLACE INTO smart_wallets VALUES(?,?,?)", (name, "memecoin", json.dumps(tracked(origin))))
    buy(chain, "alice", NOW + 50)
    buy(chain, "bob", NOW + 55)
    decided = step(60)
    later = step(180)  # the buys are now 185 and 190 s old: outside the 120 s window
    return chain, decided, later


def test_a_whale_decision_carries_its_evidence_and_is_re_derived_from_the_journal_alone(db, monkeypatch):
    _, decided, later = whale_session(db, monkeypatch)
    assert [o["key"] for o in decided["pending_orders"]] == ["whale_tail/" + MINT]
    first, fired, quiet = sim.journal(db)
    assert first["evaluations"][MINT]["whale"]["qualified_wallets"] == 0
    trace = fired["evaluations"][MINT]["whale"]
    assert fired["evaluations"][MINT]["signals"] == ["whale_tail"] and trace["signal"] is True
    assert (trace["identity"], trace["reason"], trace["attributed"], trace["error"]) == ("verified", None, True, None)
    assert trace["qualified_wallets"] == 2 and trace["window_s"] == sw.CONFLUENCE_WINDOW_S == 120
    assert [(s["side"], s["ts"]) for s in trace["swaps"]] == [("buy", NOW + 50), ("buy", NOW + 55)]
    assert trace["swaps"][0]["tx_sha256"] == hashlib.sha256(f"buy-alice-{NOW + 50}".encode()).hexdigest()
    assert sorted(w["last_buy"] for w in trace["wallets"]) == [NOW + 50, NOW + 55]
    assert {s["wallet"] for s in trace["swaps"]} == {w["wallet"] for w in trace["wallets"]}  # one pseudonym per wallet
    assert len({w["origin"] for w in trace["wallets"]}) == 2  # two independent funding origins
    assert quiet["evaluations"][MINT]["whale"]["signal"] is False and quiet["evaluations"][MINT]["whale"]["swaps"] == []
    assert sim.audit_whale(sim.journal(db)) == {"checked": 3, "matched": 3, "mismatches": [], "unverifiable": 0, "future": []}


def test_no_wallet_address_signature_or_key_is_written_to_the_journal(db, monkeypatch):
    whale_session(db, monkeypatch)
    text = json.dumps(sim.journal(db))
    assert not [s for s in PRIVATE if s in text]
    state, wallets = sim.load(db)
    assert {"alice", "bob"} <= set(wallets)  # the session itself still knows the wallets...
    assert state["journal_key"] not in text  # ...and the pseudonymization key, which never reaches the journal
    assert sim._pseudonym(state, "alice") in text and sim._pseudonym(state, "alice") != sim._pseudonym(state, "bob")


def test_a_tampered_or_future_journal_is_reported_and_old_rows_are_unverifiable(db, monkeypatch):
    whale_session(db, monkeypatch)
    rows = sim.journal(db)
    flipped = copy.deepcopy(rows)
    flipped[1]["evaluations"][MINT]["signals"] = []  # a whale decision erased from the journal
    assert sim.audit_whale(flipped)["mismatches"] == [{"revision": rows[1]["revision"], "mint": MINT, "journaled": False,
                                                        "derived": True}]
    leaked = copy.deepcopy(rows)
    leaked[1]["evaluations"][MINT]["whale"]["swaps"][1]["ts"] = rows[1]["ts"] + 30  # evidence from after the decision
    assert sim.audit_whale(leaked)["future"] == [{"revision": rows[1]["revision"], "mint": MINT, "after_s": 30}]
    old = copy.deepcopy(rows)
    for row in old:
        row["evaluations"][MINT].pop("whale")
    assert sim.audit_whale(old) == {"checked": 0, "matched": 0, "mismatches": [], "unverifiable": 3, "future": []}


def test_an_unattributed_pool_records_its_abstention_and_its_reason(db):
    feed, clock, step = causal_session(db)  # the public RPC holds no history for this pool
    step(0)
    step(60)
    for row in sim.journal(db):
        trace = row["evaluations"][MINT]["whale"]
        assert (trace["identity"], trace["attributed"], trace["signal"]) == ("unknown", False, False)
        assert trace["reason"] == "aucune transaction connue pour ce pool"
    assert sim.audit_whale(sim.journal(db))["matched"] == 2


def test_journaling_the_swaps_changes_neither_the_decision_nor_the_reads():
    outcomes = []
    for observed in (None, []):
        chain, asset = chain_with_history(), gecko_asset()
        wallets = {"alice": qualified("fund-a"), "bob": qualified("fund-b")}
        buy(chain, "alice", NOW - 20)
        buy(chain, "bob", NOW - 10)
        fired = sw.follow(chain, asset, wallets, {"WIN"}, NOW, observed)
        outcomes.append((fired, json.dumps(wallets, sort_keys=True), chain.calls))
    assert outcomes[0] == outcomes[1] and outcomes[0][0] is True


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
