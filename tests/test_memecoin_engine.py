"""Offline paper-only fixtures. No RPC, key, model or account is shared with the major-asset engine."""

import json

import pytest
from fastapi.testclient import TestClient

from sentinelle.dmh import memecoin_simulator as sim, smart_wallets as sw
from sentinelle.dmh.memecoin_strategies import NAMES, MemecoinIsolatedLearner, evaluate
from tests import test_configurable_capital as frozen
from tests import test_dmh_api
from tests.test_dmh_scanner import Feed, MINT, NOW, POOL, WSOL, b58

api_setup = test_dmh_api.setup


def asset():
    return {"symbol": MINT, "pool": POOL, "quote_mint": WSOL, "creation": {"program": sw.AMM4, "signature": "init", "block_time": NOW - 60}}


def test_wallet_discovery_and_independence(monkeypatch):
    a = asset()
    wallets, txs, rows = {}, {}, {POOL: [], MINT: []}

    def tx(sig, owner, ix, ts):
        t = {
            "blockTime": ts,
            "transaction": {"signatures": [sig], "message": {"accountKeys": [{"pubkey": owner, "signer": True}], "instructions": [ix]}},
            "meta": {"err": None, "fee": 5000, "preBalances": [0]},
        }
        txs[sig] = t
        return t

    tx("init", "creator", {"programId": sw.AMM4, "accounts": [POOL] * 17 + ["creator"]}, NOW - 60)
    tx(
        "deploy",
        "deployer",
        {"program": "spl-token", "parsed": {"type": "initializeMint2", "info": {"mint": MINT, "mintAuthority": "deployer"}}},
        NOW - 61,
    )
    rows[MINT].append({"signature": "deploy"})

    def swap(owner, i, buy, ts):
        sig = owner + str(i)
        t = tx(sig, owner, {"programId": sw.AMM4, "accounts": [POOL], "data": b58(b"\x09" + bytes(16))}, ts)
        vals = [(MINT, 10, 20), (WSOL, 20, 19)] if buy else [(MINT, 20, 10), (WSOL, 19, 23)]
        for j, key in enumerate(("preTokenBalances", "postTokenBalances"), 1):
            t["meta"][key] = [
                {"owner": owner, "mint": m, "uiTokenAmount": {"amount": str((pre, post)[j - 1]), "decimals": 0}} for m, pre, post in vals
            ]
        rows[POOL].append({"signature": sig, "slot": ts, "blockTime": ts})

    for owner in ("alice", "bob", "creator", "deployer"):
        tx(
            owner,
            "source-" + owner,
            {"program": "system", "parsed": {"type": "transfer", "info": {"destination": owner, "source": "source-" + owner}}},
            NOW - 90,
        )["transaction"]["message"]["accountKeys"].append({"pubkey": owner, "signer": False})
        txs[owner]["meta"]["preBalances"] = [100, 0]
        rows[owner] = [{"signature": owner, "blockTime": NOW - 90, "slot": 1}]
        for i in range(4):
            swap(owner, i, i % 2 == 0, NOW - 30 + i)
    monkeypatch.setattr(sw, "transaction", lambda _, sig: txs[sig])
    monkeypatch.setattr(sw, "recent", lambda _, address, limit=6, **options: rows.get(address, []))
    assert not sw.follow(None, a, wallets, {MINT}, NOW)
    assert set(wallets) == {"alice", "bob"}
    assert all(w["win_rate"] == 1 and w["qualified"] for w in wallets.values())
    swap("alice", 4, True, NOW + 1)
    assert not sw.follow(None, a, wallets, {MINT}, NOW + 2)
    swap("bob", 4, True, NOW + 3)
    assert sw.follow(None, a, wallets, {MINT}, NOW + 4)
    prior = dict(wallets["alice"]["lots"][MINT])
    sw.record(wallets, MINT, {"wallet": "alice", "signature": "late", "ts": NOW - 5, "qty": -1, "sol": 1}, NOW - 60, True)
    assert wallets["alice"]["lots"][MINT] == prior
    wallets["bob"]["origin"] = wallets["alice"]["origin"]
    assert not sw.confluence(wallets, MINT, NOW + 4, a["excluded"])
    assert not sw.confluence(wallets, MINT, NOW + 300, a["excluded"])


def test_strategies_guard_friction_and_exits(db):
    feed = Feed()
    feed.pair["priceUsd"] = "1"
    a = asset()
    p = sim.quote(feed.client, a, NOW)
    a.update(peak=1.4, peak_liquidity=p["liquidity_usd"], last={**p, "volume_usd_per_min_m5": 30, "buy_sell_count_ratio_m5": 1.3})
    signals, guard = evaluate(feed.client, a, p, True)
    assert guard["allowed"] and set(signals) == set(NAMES)
    feed.info["mintAuthority"] = POOL
    assert evaluate(feed.client, a, p, True)[0] == []
    state, _ = sim.load(db)
    state["assets"][MINT] = a
    sim.process(state, MINT, p, signals, 0.001)
    assert len(state["positions"]) == 4 and sim.equity(state) < sim.INITIAL
    assert all(f["network_fee_usd"] and f["slippage_usd"] and f["dex_fee_usd"] for f in state["fills"])
    assert sim.value(1000, {**p, "liquidity_usd": 5000}, 0.001)[1] > 50
    sim.process(state, MINT, {**p, "price": 1.7}, [], 0.001)
    sim.process(state, MINT, {**p, "price": 2.4}, [], 0.001)
    sim.process(state, MINT, {**p, "price": 1.8}, [], 0.001)
    assert [f["reason"] for f in state["fills"]].count("take_profit") == 8
    assert state["fills"][-1]["reason"] == "trailing_stop" and not state["positions"]
    sim.process(state, MINT, p, ["sniper"], 0.001)
    sim.process(state, MINT, {**p, "price": 0.8}, [], 0.001)
    assert state["fills"][-1]["reason"] == "stop_loss"
    stats = sim.report(state, {})
    assert stats["equity"] == pytest.approx(sim.INITIAL + sum(s["pnl"] for s in stats["strategies"].values()))
    assert MemecoinIsolatedLearner(state["completed"]).weights["sniper"] < stats["weights"]["momentum"]


def test_authenticated_cycle_and_isolation(api_setup, monkeypatch):
    app, c, _ = api_setup
    url = "/api/dmh/memecoin/session"
    assert TestClient(c.app).get(url).status_code == 401
    assert c.post(url + "/tick", json={"mode": "paper"}).status_code == 403
    feed = Feed()
    feed.pair["priceUsd"] = "1"
    send = feed.transport

    def transport(method, url, headers, body, timeout):
        if url.endswith("/" + WSOL):
            data = [
                {
                    "chainId": "solana",
                    "baseToken": {"address": WSOL},
                    "quoteToken": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"},
                    "liquidity": {"usd": 100000},
                    "priceUsd": "120",
                }
            ]
        elif body and json.loads(body)["method"] == "getRecentPrioritizationFees":
            data = {"jsonrpc": "2.0", "result": [{"prioritizationFee": 200}]}
        else:
            return send(method, url, headers, body, timeout)
        return 200, {}, json.dumps(data).encode("utf-8")

    feed.client._transport = transport
    monkeypatch.setattr(app.dmh, "client", feed.client)

    def balances():
        return app.db.query("SELECT * FROM balances"), app.db.scalar("SELECT COUNT(*) FROM fills")

    before = balances()
    events = []
    listener = lambda topic, event: events.append(topic)
    app.bus.add_listener(listener)
    try:
        response = c.post(url + "/tick", headers={"X-Sentinelle": "1"}, json={"mode": "paper"})
        assert response.status_code == 200, response.text
        state = c.get(url).json()
        assert state == response.json() and state["activity"][0]["strategy"] == "sniper"
        assert state["asset_domain"] == "memecoin" and balances() == before
        assert {"memecoin_fill", "memecoin_session"} <= set(events)
        assert c.post(url + "/tick", headers={"X-Sentinelle": "1"}, json={"mode": "paper"}).status_code == 429

        def missing_quote(*_):
            raise ValueError("quote unavailable")

        monkeypatch.setattr(sim, "quote", missing_quote)
        result = sim.tick(app.dmh, app.db, app.bus)
        assert result["equity"] is None and result["stale_marks"] == [MINT]
        assert result["last_marked_equity"] == state["equity"] and result["activity"] == state["activity"]
    finally:
        app.bus.remove_listener(listener)


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
