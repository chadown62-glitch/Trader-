"""Offline paper-only fixtures. No RPC, key, model or account is shared with the major-asset engine.

M58: the session draws its candidates from established pools (filters, Jupiter round trip, then the veto)."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from sentinelle.dmh import established_pools as est, memecoin_simulator as sim, smart_wallets as sw
from sentinelle.dmh.dex import SPL_TOKEN
from sentinelle.dmh.memecoin_strategies import NAMES, MemecoinIsolatedLearner, evaluate
from sentinelle.dmh.net import PublicClient
from sentinelle.services.bus import EventBus
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


# -- pools établis (M58) --------------------------------------------------------------------------------------------
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


class EstablishedFeed(Feed):
    """Un pool établi (GeckoTerminal), ses devis Jupiter sans portefeuille et le rapport Rugcheck complet."""

    def __init__(self):
        super().__init__()
        self.pair["priceUsd"] = "1"
        self.pool = {"reserve_in_usd": "50000", "pool_created_at": "2027-01-15T05:00:00Z", "volume_usd": {"h1": "12000", "m5": "600"},
                     "transactions": {"h1": {"buys": 40, "sells": 30}, "m5": {"buys": 6, "sells": 3}}, "base_token_price_usd": "1",
                     "market_cap_usd": "100000", "name": "TEST / SOL", "address": POOL}
        self.loss = 0.02  # aller-retour Jupiter
        self.sell_route = True
        self.owner = SPL_TOKEN
        self.extensions = []
        self.rug.update(topHolders=[{"address": POOL, "owner": "amm", "pct": 60.0}, {"address": "h1", "owner": "w1", "pct": 8.0},
                                    {"address": "h2", "owner": "w2", "pct": 5.0}], knownAccounts={"amm": {"type": "AMM"}})
        self.urls = []

    def transport(self, method, url, headers, body, timeout):
        parts = urlsplit(url)
        self.urls.append(parts.hostname + parts.path)
        if parts.hostname == "api.geckoterminal.com":
            item = {"attributes": self.pool, "relationships": {"base_token": {"data": {"id": "solana_" + MINT}},
                                                                "quote_token": {"data": {"id": "solana_" + WSOL}},
                                                                "dex": {"data": {"id": "pumpswap"}}}}
            return 200, {}, json.dumps({"data": [item]}).encode("utf-8")
        if parts.hostname == "api.jup.ag":
            q = {k: v[0] for k, v in parse_qs(parts.query).items()}
            amount = int(q["amount"])
            if q["inputMint"] == WSOL:
                out = amount * 1000
            elif not self.sell_route:
                return 200, {}, json.dumps({"error": "No routes found"}).encode("utf-8")
            else:
                out = int(amount / 1000 * (1 - self.loss))
            quote = {"inputMint": q["inputMint"], "outputMint": q["outputMint"], "inAmount": str(amount), "outAmount": str(out),
                     "routePlan": [{"swapInfo": {"ammKey": POOL, "label": "PumpSwap"}, "percent": 100}]}
            return 200, {}, json.dumps(quote).encode("utf-8")
        if url.endswith("/" + WSOL):
            data = [{"chainId": "solana", "baseToken": {"address": WSOL}, "liquidity": {"usd": 100000}, "priceUsd": "120",
                     "quoteToken": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}}]
            return 200, {}, json.dumps(data).encode("utf-8")
        if method == "POST":
            name = json.loads(body)["method"]
            if name == "getRecentPrioritizationFees":
                return 200, {}, json.dumps({"jsonrpc": "2.0", "result": [{"prioritizationFee": 200}]}).encode("utf-8")
            if name == "getAccountInfo":
                info = {**self.info, "extensions": [{"extension": e} for e in self.extensions]}
                value = {"owner": self.owner, "data": {"parsed": {"type": "mint", "info": info}}}
                return 200, {}, json.dumps({"jsonrpc": "2.0", "result": {"value": value}}).encode("utf-8")
            if name == "getTokenLargestAccounts":
                return 403, {}, b"forbidden"  # comme le RPC public : la lecture des plus gros comptes est refusée
        return super().transport(method, url, headers, body, timeout)


def established(db, feed, now=NOW):
    feed.client.clock = lambda: now
    return est.scan(feed.client, db, EventBus(db))


@pytest.mark.parametrize(("case", "stage", "fragment"), [
    ("accepted", "accepted", "retenu"),
    ("thin", "liquidity", "liquidité 1 600 $"),
    ("young", "age", "trop récent"),
    ("quiet", "activity", "peu active"),
    ("one_sided", "activity", "0 ventes"),
    ("unsellable", "round_trip", "aucune route de revente"),
    ("costly", "round_trip", "aller-retour immédiat"),
    ("token_2022_risky", "guardian", "permanentDelegate"),
    ("concentrated", "guardian", "Premier détenteur"),
    ("rug_flag", "guardian", "LP Unlocked"),
])
def test_established_pools_filter_then_veto(db, case, stage, fragment):
    feed = EstablishedFeed()
    if case == "thin":
        feed.pool["reserve_in_usd"] = "1600"
    elif case == "young":
        feed.pool["pool_created_at"] = "2027-01-15T07:30:00Z"
    elif case == "quiet":
        feed.pool["volume_usd"]["h1"] = "900"
    elif case == "one_sided":
        feed.pool["transactions"]["h1"]["sells"] = 0
    elif case == "unsellable":
        feed.sell_route = False
    elif case == "costly":
        feed.loss = 0.25
    elif case == "token_2022_risky":
        feed.owner, feed.extensions = TOKEN_2022, ["tokenMetadata", "permanentDelegate"]
    elif case == "concentrated":
        feed.rug["topHolders"][1]["pct"] = 22.0
    elif case == "rug_flag":
        feed.rug["risks"] = [{"level": "danger", "name": "LP Unlocked"}]
    result = established(db, feed)
    event, = result["decisions"]
    assert (event["stage"], event["asset_domain"]) == (stage, "memecoin") and fragment in event["message"]
    assert event["kind"] == ("observation_accepted" if case == "accepted" else "observation_refused")
    assert result["funnel"]["seen"] == 1 and result["funnel"][stage] == 1
    cheap = stage in ("liquidity", "age", "activity")
    assert any(u.startswith("api.jup.ag") for u in feed.urls) is not cheap  # filtres gratuits avant tout devis
    assert db.scalar("SELECT COUNT(*) FROM orders") == db.scalar("SELECT COUNT(*) FROM fills") == 0


def test_guardian_reads_real_holders_and_admits_metadata_only_token_2022(db):
    feed = EstablishedFeed()
    feed.owner, feed.extensions = TOKEN_2022, ["metadataPointer", "tokenMetadata"]
    event, = established(db, feed)["decisions"]
    holders = event["guardian"]["evidence"]["concentration"]
    assert event["kind"] == "observation_accepted" and holders["source"] == "rugcheck"
    assert holders["top10_share"] == pytest.approx(0.13) and holders["excluded_pools_or_lockers"] == 1  # pool hors calcul
    assert not any(u.endswith("getTokenLargestAccounts") for u in feed.urls)
    # Sans liste exploitable chez Rugcheck, le Guardian revient à la chaîne ; refusée elle aussi : indisponible, donc veto.
    feed = EstablishedFeed()
    del feed.rug["topHolders"]
    event, = established(db, feed, NOW + est.COOLDOWN_S)["decisions"]
    assert event["stage"] == "guardian" and "concentration_unavailable" in {v["code"] for v in event["guardian"]["vetoes"]}


def test_cooldown_bounds_and_funnel_summary(db):
    feed = EstablishedFeed()
    assert established(db, feed)["funnel"]["accepted"] == 1
    again = established(db, feed, NOW + 60)
    assert again["decisions"] == [] and again["funnel"]["cooldown"] == 1  # pas de nouveau contrôle avant six heures
    assert established(db, feed, NOW + est.COOLDOWN_S)["funnel"]["accepted"] == 1
    summary = est.summary(db, 0)
    assert summary["tokens"] == summary["accepted"] == 1 and summary["median_liquidity_usd"] == 50000
    assert summary["candidates"][0]["round_trip_loss"] == pytest.approx(0.02, abs=1e-3)
    assert summary["thresholds"]["min_liquidity_usd"] == 20000 and summary["asset_domain"] == "memecoin"
    outage = EstablishedFeed()
    outage.transport = lambda *a: (503, {}, b"down")
    outage.client = PublicClient(transport=outage.transport, clock=lambda: NOW, sleep=lambda _: None, retries=0)
    down = est.scan(outage.client, db, EventBus(db))
    assert down["decisions"] == [] and down["errors"][0]["source"] == est.GT


def test_authenticated_cycle_on_established_pools(api_setup, monkeypatch):
    app, c, _ = api_setup
    url = "/api/dmh/memecoin/session"
    assert TestClient(c.app).get(url).status_code == 401
    assert c.post(url + "/tick", json={"mode": "paper"}).status_code == 403
    feed = EstablishedFeed()
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
        first = response.json()
        asset = sim.load(app.db)[0]["assets"][MINT]
        assert asset["dex_id"] == "pumpswap" and first["activity"] == []  # un pool d'une heure n'est pas un « sniper »
        feed.pair.update(volume={"m5": 2000}, txns={"m5": {"buys": 12, "sells": 3}})  # volume et pression acheteuse accélèrent
        state = sim.tick(app.dmh, app.db, app.bus)
        assert {f["strategy"] for f in state["activity"]} == {"momentum"}
        assert state["asset_domain"] == "memecoin" and balances() == before
        assert {"memecoin_fill", "memecoin_session", "decision"} <= set(events)
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
