"""Offline paper-only fixtures. No RPC, key, model or account is shared with the major-asset engine.

M58: the session draws its candidates from established pools (filters, Jupiter round trip, then the veto)."""

import ast
import json
import math
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from sentinelle.dmh import established_pools as est, memecoin_simulator as sim, memecoin_strategies as ms, smart_wallets as sw
from sentinelle.dmh.dex import SPL_TOKEN
from sentinelle.dmh.memecoin_strategies import NAMES, MemecoinIsolatedLearner, evaluate
from sentinelle.dmh.net import PublicClient, SourceUnavailable
from sentinelle.dmh.tokens import load_registry
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
    assert guard["allowed"] and set(signals) == set(NAMES)  # a fresh-pool candidate keeps the former age rule (display)
    assert set(sim.tradable_signals(a, signals)) == {"momentum", "dip_hunter", "whale_tail"}  # but never a sniper entry (D5)
    feed.info["mintAuthority"] = POOL
    assert evaluate(feed.client, a, p, True)[0] == []
    state, _ = sim.load(db)
    state["assets"][MINT] = a
    sim.process(state, MINT, p, list(NAMES), 0.001)  # the four strategies, to exercise frictions and exits
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


# -- D4 : whale_tail on established pools (GeckoTerminal creation without any signature) ---------------------------------
CREATOR, DEPLOYER, CREATION_SIG, DEPLOY_SIG = "creator", "deployer", "pool-init", "mint-init"


class Chain:
    """A public Solana RPC in miniature: paginated signature histories (newest first) and finalized transactions."""

    def __init__(self):
        self.history, self.txs, self.calls, self.down = {}, {}, [], False

    def rpc(self, host, method, params):
        self.calls.append(method)
        if self.down:
            raise SourceUnavailable(host, "HTTP 429")
        if method == "getSignaturesForAddress":
            address, options = params
            rows = self.history.get(address, [])
            if options.get("before"):
                rows = rows[[r["signature"] for r in rows].index(options["before"]) + 1:]
            return rows[:options["limit"]], None
        if method == "getTransaction":
            return self.txs.get(params[0]), None
        raise AssertionError(f"lecture RPC inattendue : {method}")

    def add(self, address, signature, ts):
        self.history.setdefault(address, []).insert(0, {"signature": signature, "blockTime": ts, "slot": ts, "err": None})


def tx(sig, signers, instructions, ts, **meta):
    keys = [{"pubkey": k, "signer": True} for k in signers]
    return {"blockTime": ts, "slot": ts, "transaction": {"signatures": [sig], "message": {"accountKeys": keys, "instructions": instructions}},
            "meta": {"err": None, "fee": 5000, "preBalances": [0], "preTokenBalances": [], "postTokenBalances": [], **meta}}


def raydium_init(pool=POOL, mint=MINT):
    accounts = ["x"] * 18
    accounts[4], accounts[8], accounts[9], accounts[17] = pool, mint, WSOL, CREATOR  # AMM v4 layout: pool, coin, pc, creator
    return {"programId": sw.AMM4, "accounts": accounts, "data": b58(bytes([1]) + bytes(25))}


def gecko_asset():
    """An M58 pool as established_pools records it: GeckoTerminal creation date, no signature, no program."""
    return {"symbol": MINT, "pool": POOL, "quote_mint": WSOL, "dex_id": "raydium",
            "creation": {"source": "geckoterminal", "block_time": NOW - 3 * 3600}}


def chain_with_history(*, pool_init=True, mint_init=True, pool_padding=0, mint_padding=0, empty_pool=False):
    chain = Chain()
    created = NOW - 3 * 3600
    if pool_init:
        chain.txs[CREATION_SIG] = tx(CREATION_SIG, [CREATOR, "payer"], [raydium_init()], created)
    else:  # first transaction of the pool is a PumpSwap creation: no Raydium parser can verify it
        chain.txs[CREATION_SIG] = tx(CREATION_SIG, [CREATOR], [{"programId": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
                                                                 "accounts": [POOL, MINT, WSOL], "data": b58(bytes(24))}], created)
    chain.add(POOL, CREATION_SIG, created)
    chain.txs[DEPLOY_SIG] = tx(DEPLOY_SIG, [DEPLOYER], [{"program": "spl-token", "parsed": {
        "type": "initializeMint2", "info": {"mint": MINT, "mintAuthority": "authority"}}}] if mint_init else [], created - 60)
    chain.add(MINT, DEPLOY_SIG, created - 60)
    for i in range(pool_padding):
        chain.add(POOL, f"p{i}", created + 1 + i)
    for i in range(mint_padding):
        chain.add(MINT, f"m{i}", created + 1 + i)
    if empty_pool:
        del chain.history[POOL]
    return chain


def qualified(origin):
    """A wallet already qualified on other winners (recent, early and profitable cycles), with a witnessed funding."""
    cycles = [{"mint": "WIN", "pnl_sol": 1.0, "early": True, "ts": NOW - 3600, "opened_ts": NOW - 3700} for _ in range(2)]
    return {"asset_domain": "memecoin", "cycles": cycles, "lots": {}, "last_buy": {}, "qualified": True,
            "qualified_since": NOW - 7200, "origin": origin, "first_trade": "f-" + origin}


def buy(chain, owner, ts):
    sig = f"buy-{owner}-{ts}"
    ix = {"programId": sw.AMM4, "accounts": [POOL], "data": b58(b"\x09" + bytes(16))}
    pre = [{"owner": owner, "mint": MINT, "uiTokenAmount": {"amount": "0", "decimals": 0}},
           {"owner": owner, "mint": WSOL, "uiTokenAmount": {"amount": "20", "decimals": 0}}]
    post = [{"owner": owner, "mint": MINT, "uiTokenAmount": {"amount": "10", "decimals": 0}},
            {"owner": owner, "mint": WSOL, "uiTokenAmount": {"amount": "19", "decimals": 0}}]
    chain.txs[sig] = tx(sig, [owner], [ix], ts, preTokenBalances=pre, postTokenBalances=post)
    chain.add(POOL, sig, ts)


def test_geckoterminal_pool_without_signature_is_attributed_from_its_first_transaction():
    chain, asset = chain_with_history(pool_padding=1500, mint_padding=10), gecko_asset()
    excluded = sw.identities(chain, asset)
    assert excluded == sorted({CREATOR, "payer", DEPLOYER, "authority"})  # pool creator, fee payer, deployer, authority
    assert asset["identity"] == {"status": "verified", "source": sw.FIRST_TRANSACTION, "program": sw.AMM4,
                                 "creation_signature": CREATION_SIG, "excluded": 4}
    assert chain.calls.count("getSignaturesForAddress") == 3  # two pages for the pool (1,501 rows), one for the mint
    assert chain.calls.count("getTransaction") == 2 and "signature" not in asset["creation"]  # nothing invented


@pytest.mark.parametrize("variant,reason", [
    ({"pool_padding": 3000}, "plus de 3000 transactions"),  # the first transaction is beyond the bounded history
    ({"pool_init": False}, "sans initialisation Raydium"),  # PumpSwap creation: not verifiable here
    ({"mint_init": False}, "déployeur inconnu"),  # the mint's first transaction is not its initialisation
    ({"mint_padding": 3000}, "déployeur inconnu"),
])
def test_unknown_identity_abstains_without_error_and_without_false_signal(variant, reason):
    chain, asset = chain_with_history(**variant), gecko_asset()
    wallets = {"alice": qualified("fund-a"), "bob": qualified("fund-b")}
    buy(chain, "alice", NOW - 20)
    buy(chain, "bob", NOW - 10)  # two qualified wallets with independent origins: a confluence, but nobody is attributed
    assert sw.follow(chain, asset, wallets, {"WIN"}, NOW) is False
    assert asset["identity"]["status"] == "unknown" and reason in asset["identity"]["reason"]
    calls = len(chain.calls)
    assert sw.follow(chain, asset, wallets, {"WIN"}, NOW + 60) is False and len(chain.calls) == calls  # cached: no RPC
    assert all(not w["last_buy"] for w in wallets.values())  # their buys were not even read


def test_a_pool_without_any_transaction_is_unknown_not_an_error():
    chain, asset = chain_with_history(empty_pool=True), gecko_asset()
    assert sw.identities(chain, asset) == [] and asset["identity"] == {"status": "unknown", "reason": "aucune transaction connue pour ce pool"}
    assert sw.first_signature(chain, POOL) is None and chain.calls == ["getSignaturesForAddress"] * 2


def test_verified_identity_and_independent_confluence_allow_whale_tail():
    chain, asset = chain_with_history(), gecko_asset()
    wallets = {"alice": qualified("fund-a"), "bob": qualified("fund-b"), CREATOR: qualified("fund-c")}
    buy(chain, "alice", NOW - 20)
    buy(chain, CREATOR, NOW - 15)  # an insider's buy never counts
    assert sw.follow(chain, asset, wallets, {"WIN"}, NOW) is False  # one independent buyer only
    buy(chain, "bob", NOW + 10)
    assert sw.follow(chain, asset, wallets, {"WIN"}, NOW + 20) is True
    assert asset["identity"]["status"] == "verified" and CREATOR in asset["excluded"]
    wallets["bob"]["origin"] = "fund-a"  # same funding origin: not independent
    assert not sw.confluence(wallets, MINT, NOW + 20, asset["excluded"])


def test_rpc_failure_is_reported_then_retried_later():
    chain, asset = chain_with_history(), gecko_asset()
    chain.down = True
    with pytest.raises(SourceUnavailable):
        sw.follow(chain, asset, {}, set(), NOW)
    assert asset["identity"]["status"] == "retry" and asset["identity"]["retry_at"] == NOW + sw.IDENTITY_RETRY_S
    calls = len(chain.calls)
    assert sw.follow(chain, asset, {}, set(), NOW + 60) is False and len(chain.calls) == calls  # reported once, then waits
    chain.down = False
    sw.follow(chain, asset, {}, set(), NOW + sw.IDENTITY_RETRY_S)
    assert asset["identity"]["status"] == "verified"


def test_a_missing_signature_is_not_a_source_failure_and_does_not_block_momentum(db):
    feed = EstablishedFeed()  # PumpSwap pool from GeckoTerminal; the RPC has no history for it
    hub = SimpleNamespace(client=feed.client, registry=load_registry())
    first = sim.tick(hub, db, EventBus(db))
    state = sim.load(db)[0]
    assert state["assets"][MINT]["identity"]["status"] == "unknown"
    assert not any(isinstance(e, dict) and "wallets" in e for e in first["errors"] + state["errors"])  # no "'signature'" failure
    feed.pair.update(volume={"m5": 2000}, txns={"m5": {"buys": 12, "sells": 3}})
    second = sim.tick(hub, db, EventBus(db))
    assert {f["strategy"] for f in second["activity"]} == {"momentum"}


# -- D5 : sniper on an established pool --------------------------------------------------------------------------------
def observation(**changes):
    point = {"price": 1.05, "liquidity_usd": 50_000.0, "volume_usd_per_min_m5": 400.0, "buy_sell_count_ratio_m5": 3.0,
             "ts": NOW, "pool_age_minutes": 180.0, "volume_h1_usd": 12_000.0}
    return {**point, **changes}


def sniper_asset(**previous):
    last = {"price": 1.0, "liquidity_usd": 50_000.0, "volume_usd_per_min_m5": 100.0, "buy_sell_count_ratio_m5": 1.5, "ts": NOW - 60}
    return {"symbol": MINT, "pool": POOL, "dex_id": "raydium", "last": {**last, **previous}, "peak": 1.05, "peak_liquidity": 50_000.0}


def test_sniper_fires_on_a_verifiable_acceleration_of_an_established_pool():
    check = ms.sniper_check(sniper_asset(), observation())
    assert check["signal"] and check["reason"] == "accélération vérifiée"
    assert check["volume_jump"] == pytest.approx(4.0) and check["hourly_pace"] == pytest.approx(2.0)
    assert check["price_move"] == pytest.approx(0.05) and check["gap_s"] == 60
    assert (ms.SNIPER_MIN_AGE_MINUTES, ms.SNIPER_MIN_LIQUIDITY_USD) == (est.MIN_AGE_MINUTES, est.MIN_LIQUIDITY_USD)  # M58 rules
    feed = EstablishedFeed()
    point = observation(liquidity_usd=50_000.0)
    signals, guard = evaluate(feed.client, sniper_asset(), point, False)
    assert guard["allowed"] and "sniper" in signals
    feed.rug["risks"] = [{"level": "danger", "name": "LP Unlocked"}]
    vetoed, guard = evaluate(feed.client, sniper_asset(), point, True)
    assert vetoed == [] and not guard["allowed"]  # the guardian veto comes first, whatever the acceleration


@pytest.mark.parametrize("asset_changes,point_changes,reason", [
    (None, {}, "aucune observation précédente"),
    ({"ts": NOW - 601}, {}, "périmée"),
    ({"ts": NOW}, {}, "même observation"),
    ({"price": 1.05, "volume_usd_per_min_m5": 400.0, "buy_sell_count_ratio_m5": 3.0}, {}, "inchangée"),
    ({}, {"volume_h1_usd": None}, "volume sur 1 h inconnu"),
    ({}, {"buy_sell_count_ratio_m5": None}, "incomplète"),
    ({"price": float("nan")}, {}, "incomplète"),
    ({}, {"liquidity_usd": -1.0}, "incohérente"),
    ({}, {"pool_age_minutes": 30.0}, "âge"),
    ({}, {"liquidity_usd": 15_000.0}, "liquidité sous 20 000 $"),
    ({}, {"volume_usd_per_min_m5": 250.0}, "saut de volume"),
    ({}, {"volume_usd_per_min_m5": 90.0}, "trop faible"),
    ({}, {"volume_h1_usd": 30_000.0}, "moyenne horaire"),
    ({}, {"buy_sell_count_ratio_m5": 1.8}, "pression acheteuse"),
    ({"buy_sell_count_ratio_m5": 3.5}, {}, "pression acheteuse"),
    ({}, {"price": 0.99}, "variation de prix"),
    ({}, {"price": 1.40}, "variation de prix"),
    ({}, {"liquidity_usd": 45_000.0}, "liquidité en retrait"),
])
def test_sniper_refuses_missing_stale_inconsistent_or_flat_data(asset_changes, point_changes, reason):
    asset = sniper_asset(**(asset_changes or {}))
    if asset_changes is None:
        del asset["last"]
    check = ms.sniper_check(asset, observation(**point_changes))
    assert not check["signal"] and reason in check["reason"]


@pytest.mark.parametrize("gap,expected", [(60, {"momentum", "sniper"}), (ms.SNIPER_MAX_GAP_S + 100, {"momentum"})])
def test_sniper_enters_through_the_paper_tick_only_on_a_recent_verified_acceleration(db, gap, expected):
    feed = EstablishedFeed()  # established PumpSwap pool, three hours old, 50,000 USD deep
    feed.pair.update(volume={"m5": 500, "h1": 12_000}, txns={"m5": {"buys": 6, "sells": 3}})
    hub = SimpleNamespace(client=feed.client, registry=load_registry())
    sim.tick(hub, db, EventBus(db))  # first observation: nothing to compare with
    feed.client.clock = lambda: NOW + gap
    feed.pair.update(priceUsd="1.05", volume={"m5": 2000, "h1": 12_000}, txns={"m5": {"buys": 12, "sells": 3}})
    state = sim.tick(hub, db, EventBus(db))
    assert {f["strategy"] for f in state["activity"]} == expected
    held = sim.load(db)[0]["positions"]
    entry = next(iter(held.values()))["entry"]
    assert entry["confidence_model"] == "m58-continuous-v1" and entry["signals"] == sorted(expected)
    assert 0.45 <= entry["confidence"] <= 0.95 and entry["round_trip_loss"] == pytest.approx(0.02, abs=1e-3)


def test_sniper_never_fires_twice_on_the_same_observation():
    asset, point = sniper_asset(), observation()
    assert ms.sniper_check(asset, point)["signal"]
    asset["last"] = dict(point)  # the simulator keeps the observation once processed
    assert not ms.sniper_check(asset, dict(point))["signal"]


# -- D6 : continuous, bounded and explainable confidence ----------------------------------------------------------------
def score(signals=("momentum",), liquidity=100_000.0, volume=50_000.0, loss=0.02, guard=None):
    return ms.confidence_breakdown(list(signals), liquidity_usd=liquidity, volume_h1_usd=volume, round_trip_loss=loss, guard=guard)


def test_same_signal_different_markets_get_different_scores():
    deep_cheap = score(liquidity=400_000.0, volume=200_000.0, loss=0.005)
    thin_costly = score(liquidity=25_000.0, volume=6_000.0, loss=0.08)
    assert deep_cheap["score"] > thin_costly["score"] and deep_cheap["model"] == "m58-continuous-v1"
    c = score()
    expected = {"liquidity": math.log(5) / math.log(50), "turnover": 0.5, "round_trip": 0.8, "agreement": 0.0}
    assert c["components"] == pytest.approx(expected) and c["missing"] == []
    assert c["score"] == pytest.approx(0.45 + 0.5 * (0.3 * expected["liquidity"] + 0.2 * 0.5 + 0.3 * 0.8))


def test_each_component_moves_the_score_progressively_and_monotonically():
    by_liquidity = [score(liquidity=x, volume=x / 2)["score"] for x in (20_000, 50_000, 200_000, 800_000, 1_000_000, 5_000_000)]
    by_turnover = [score(volume=v)["score"] for v in (0, 10_000, 50_000, 100_000, 400_000)]
    by_cost = [score(loss=x)["score"] for x in (0.10, 0.08, 0.05, 0.02, 0.0)]
    by_agreement = [score(signals=s)["score"] for s in (["momentum"], ["momentum", "sniper"], ["momentum", "sniper", "dip_hunter"], list(NAMES))]
    for series in (by_liquidity, by_turnover, by_cost, by_agreement):
        assert series == sorted(series)
    assert len(set(by_liquidity[:4])) == 4 and len(set(by_cost)) == 5  # progressive, not a step
    assert by_liquidity[-1] == by_liquidity[-2] and by_turnover[-1] == by_turnover[-2]  # each component is capped


def test_confidence_bounds_and_missing_data_never_help():
    worst = score(liquidity=20_000.0, volume=0.0, loss=0.10)["score"]
    best = score(signals=NAMES, liquidity=2_000_000.0, volume=9_000_000.0, loss=0.0)["score"]
    assert (worst, best) == (pytest.approx(0.45), pytest.approx(0.95))
    extreme = score(signals=NAMES, liquidity=1e12, volume=1e15, loss=-0.5)["score"]
    assert 0.45 <= extreme <= 0.95
    full = score()["score"]
    for missing in ({"loss": None}, {"volume": None}, {"liquidity": None}, {"loss": float("nan")}, {"volume": -5.0}, {"liquidity": 0.0}):
        degraded = score(**missing)
        assert degraded["score"] < full and degraded["missing"]
    assert score(loss=None)["score"] == pytest.approx(score(loss=0.10)["score"])  # unknown cost = the worst admissible cost


def test_a_veto_overrides_any_confidence():
    vetoed = score(signals=NAMES, liquidity=5_000_000.0, volume=5_000_000.0, loss=0.0, guard={"allowed": False})
    assert vetoed["score"] == 0.0 and vetoed["vetoed"] is True
    assert ms.confidence_breakdown([], liquidity_usd=1e6, volume_h1_usd=1e6, round_trip_loss=0.0)["score"] == 0.0


def test_legacy_confidence_is_unchanged_and_m58_entries_record_the_continuous_score(db):
    assert ms.confidence(["momentum"]) == 0.55 and ms.confidence(["whale_tail"]) == pytest.approx(0.70)
    assert ms.confidence(["momentum", "sniper"]) == pytest.approx(0.70) and ms.confidence(["momentum"], {"allowed": False}) == 0.0
    point = {"price": 1.0, "liquidity_usd": 100_000.0, "volume_h1_usd": 50_000.0, "ts": NOW, "pool_age_minutes": 120.0}
    legacy = ms.entry_confidence(["momentum"], point, {"symbol": "L"})
    assert legacy == {"model": "legacy-agreement", "score": 0.55}  # a candidate that did not come through M58
    state, _ = sim.load(db)
    state["assets"]["L"] = {"symbol": "L"}
    state["assets"]["M"] = {"symbol": "M", "dex_id": "pumpswap", "round_trip_loss": 0.02}
    sim.process(state, "L", dict(point), ["momentum"], 0.001)
    sim.process(state, "M", dict(point), ["momentum"], 0.001)
    old, new = state["positions"]["momentum/L"]["entry"], state["positions"]["momentum/M"]["entry"]
    assert old == {"liquidity_usd": 100_000.0, "volume_h1_usd": 50_000.0, "confidence": 0.55, "signals": ["momentum"]}
    assert new["confidence"] == pytest.approx(score()["score"]) and new["confidence_model"] == "m58-continuous-v1"
    assert new["confidence_components"] == pytest.approx(score()["components"]) and new["round_trip_loss"] == 0.02


# -- invariants ----------------------------------------------------------------------------------------------------------
def test_four_strategies_memecoin_domain_and_no_new_execution_path(db):
    assert NAMES == ("sniper", "momentum", "dip_hunter", "whale_tail") and ms.ASSET_DOMAIN == sw.DOMAIN == sim.DOMAIN == "memecoin"
    state, _ = sim.load(db)
    state["assets"]["M"] = {"symbol": "M", "dex_id": "pumpswap", "round_trip_loss": 0.02}
    sim.process(state, "M", {"price": 1.0, "liquidity_usd": 100_000.0, "volume_h1_usd": 50_000.0, "ts": NOW}, list(NAMES), 0.001)
    assert {f["strategy"] for f in state["fills"]} == set(NAMES)
    assert all(f["asset_domain"] == "memecoin" and f["simulated"] is True for f in state["fills"])
    root = Path(sim.__file__).parent
    for name in ("smart_wallets.py", "memecoin_strategies.py", "memecoin_simulator.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        texts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not {t for t in texts if any(w in t for w in ("sendTransaction", "signTransaction", "sendRawTransaction"))}, name
        external = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        external |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
        relative = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level > 0}
        assert not any(m.split(".")[0] in ("solders", "solana", "nacl", "web3", "eth_account", "ccxt") for m in external), name
        assert not any("execution" in m or m.endswith("live") for m in relative), name  # no live broker
    wallet_rpc = {n.args[2].value for n in ast.walk(ast.parse((root / "smart_wallets.py").read_text(encoding="utf-8")))
                  if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_rpc" and isinstance(n.args[2], ast.Constant)}
    assert wallet_rpc == {"getSignaturesForAddress", "getTransaction"}  # read-only calls only


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
