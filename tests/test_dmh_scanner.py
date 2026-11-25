"""Finalized RPC and HTTP fixtures. All outbound requests are intercepted at the transport."""
import json
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sentinelle.ai.features import FEATURE_COLUMNS
from sentinelle.ai.features_memecoin import extract_features
from sentinelle.dmh.dex import SPL_TOKEN
from sentinelle.dmh.net import PublicClient
from sentinelle.dmh.solana_scanner import AMM4, CPMM, scan_once
from sentinelle.dmh.tokens import B58
from sentinelle.services.bus import EventBus
from tests import test_configurable_capital as frozen
from tests import test_dmh_api

api_setup = test_dmh_api.setup


def b58(raw):
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, digit = divmod(n, 58)
        out = B58[digit] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


MINT = b58(bytes(range(32)))
POOL = b58(bytes(range(32, 64)))
WSOL = "So11111111111111111111111111111111111111112"
SIG = b58(bytes(range(64)))
NOW = 1_800_000_000


class Feed:
    def __init__(self, program=CPMM):
        self.program = program
        self.block_time = NOW - 60
        self.failed = None
        self.offline = False
        self.info = {"mintAuthority": None, "freezeAuthority": None, "supply": "1000", "decimals": 6, "isInitialized": True}
        self.holders = [{"address": POOL, "amount": "120"}]
        self.rug = {"risks": []}
        self.pair = {"pairAddress": POOL, "dexId": "raydium", "chainId": "solana", "baseToken": {"address": MINT},
                     "quoteToken": {"address": WSOL}, "liquidity": {"usd": 50_000}, "marketCap": 100_000,
                     "volume": {"m5": 500}, "txns": {"m5": {"buys": 6, "sells": 3}}}
        self.ix = {"programId": program}
        if program == CPMM:
            self.ix.update(data=b58(bytes.fromhex("afaf6d1f0d989bed") + bytes(24)), accounts=[POOL]*4 + [WSOL, MINT])
        else:
            self.ix.update(data=b58(bytes([1]) + bytes(25)), accounts=[POOL]*8 + [MINT, WSOL])
        self.calls = []
        self.client = PublicClient(transport=self.transport, clock=lambda: NOW, sleep=lambda _: None, retries=0)

    def transport(self, method, url, headers, body, timeout):
        host = urlsplit(url).hostname
        if method == "POST":
            rpc = json.loads(body)
            name, args = rpc["method"], rpc["params"]
            self.calls.append(name)
            if name == "getSignaturesForAddress":
                result = [{"signature": SIG, "blockTime": self.block_time, "err": None}] if args[0] == self.program else []
            elif name == "getTransaction":
                result = {"meta": {"err": self.failed}, "blockTime": self.block_time, "slot": 50, "transaction": {
                    "signatures": [SIG], "message": {"instructions": [self.ix, self.ix]}}}
            elif name == "getAccountInfo":
                result = {"value": {"owner": SPL_TOKEN, "data": {"parsed": {"type": "mint", "info": self.info}}}}
            elif name == "getTokenLargestAccounts":
                result = {"value": self.holders}
            else:
                pytest.fail(f"Unexpected RPC {name}")
            data = {"jsonrpc": "2.0", "result": result}
        elif host == "api.dexscreener.com":
            data = [self.pair]
        elif host == "api.rugcheck.xyz":
            if self.offline:
                return 503, {}, b"offline"
            data = self.rug
        else:
            pytest.fail(f"Unexpected HTTP request {method} {host}")
        return 200, {}, json.dumps(data).encode("utf-8")


@pytest.mark.parametrize("program", [CPMM, AMM4])
def test_detect_once_isolated_from_orders_and_major_features(db, program):
    feed = Feed(program)
    bus = EventBus(db)
    received = []
    bus.add_listener(lambda topic, event: received.append((topic, event["data"])))
    result = scan_once(feed.client, db, bus)
    assert not result["errors"]
    event, = result["decisions"]
    fields = event["features"]
    assert fields["asset_domain"] == event["asset_domain"] == "memecoin"
    assert not set(fields).intersection(FEATURE_COLUMNS)
    assert [fields[k] for k in ("pool_age_minutes", "liquidity_to_market_cap", "volume_usd_per_min_m5",
                                "buy_sell_count_ratio_m5")] == [1, .5, 100, 2]
    assert fields["xyk_impact_fraction"] == pytest.approx(10 / 25010)
    assert event["kind"] == "observation_accepted"
    assert received == [("decision", {**event, "decision_id": db.scalar("SELECT id FROM decisions")})]
    assert json.loads(db.scalar("SELECT data FROM decisions")) == event
    assert scan_once(feed.client, db, EventBus(db))["decisions"] == []
    assert db.scalar("SELECT COUNT(*) FROM orders") == db.scalar("SELECT COUNT(*) FROM fills") == 0
    assert feed.calls.count("getAccountInfo") == 1


@pytest.mark.parametrize(("case", "code"), [
    ("mintAuthority", "mint_authority"), ("freezeAuthority", "freeze_authority"), ("missing_authority", "mint_unavailable"),
    ("liquidity", "liquidity"), ("holders", "concentration"), ("empty_holders", "concentration_unavailable"),
    ("outage", "rugcheck_unavailable"), ("rug_warning", "rugcheck"), ("rug_unavailable", "rugcheck_unavailable"), ("wrong_pool", "liquidity"),
])
def test_guardian_refuses_and_records_reason(db, case, code):
    feed = Feed()
    if case in ("mintAuthority", "freezeAuthority"):
        feed.info[case] = POOL
    elif case == "missing_authority":
        del feed.info["freezeAuthority"]
    elif case == "holders":
        feed.holders[0]["amount"] = "501"
    elif case == "empty_holders":
        feed.holders = []
    elif case == "outage":
        feed.offline = True
    elif case == "rug_warning":
        feed.rug = {"risks": [{"level": "danger", "name": "LP risk"}]}
    elif case == "rug_unavailable":
        feed.rug = {}
    elif case == "wrong_pool":
        feed.pair["pairAddress"] = MINT
    event, = scan_once(feed.client, db, EventBus(db), min_liquidity_usd=50001 if case == "liquidity" else 25000)["decisions"]
    assert event["kind"] == "observation_refused"
    assert code in {r["code"] for r in event["guardian"]["vetoes"]}
    assert db.scalar("SELECT message FROM decisions") == event["message"]


@pytest.mark.parametrize("case", ["swap", "unrelated_program", "old", "failed"])
def test_only_recent_successful_initializations(db, case):
    feed = Feed()
    if case == "swap":
        feed.ix["data"] = b58(bytes(32))
    elif case == "old":
        feed.block_time -= 901
    elif case == "unrelated_program":
        feed.ix["programId"] = WSOL
    else:
        feed.failed = {"InstructionError": [0, "InvalidArgument"]}
    assert scan_once(feed.client, db, EventBus(db))["decisions"] == []
    assert db.scalar("SELECT COUNT(*) FROM decisions") == 0
    fields = extract_features({}, observed_ms=10, created_ms=20)
    assert fields["pool_age_minutes"] is fields["buy_sell_count_ratio_m5"] is fields["xyk_impact_fraction"] is None


def test_authenticated_scan_is_rate_limited(api_setup):
    app, client, _ = api_setup
    path = "/api/dmh/solana/scan"
    assert TestClient(client.app).post(path, headers={"X-Sentinelle": "1"}, json={"mode": "paper"}).status_code == 401
    assert client.post(path, json={"mode": "paper"}).status_code == 403
    original = app.dmh.client
    try:
        feed = Feed()
        app.dmh.client = feed.client
        for payload in ({"mode": "live"}, {"mode": "paper", "sign": True}):
            assert client.post(path, headers={"X-Sentinelle": "1"}, json=payload).status_code == 403
        assert feed.calls == []

        def scan():
            return client.post(path, headers={"X-Sentinelle": "1"}, json={"mode": "paper"})
        assert scan().json()["decisions"][0]["kind"] == "observation_accepted"
        assert scan().status_code == 429
    finally:
        app.dmh.client = original


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifest_sha256(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)


def test_goplus_evm_reads_restrictions_and_transfer_taxes():
    from types import SimpleNamespace

    from sentinelle.dmh.tokenrisk import _goplus_evm
    from sentinelle.dmh.tokens import TokenId

    token = TokenId("base", "0x" + "ab" * 20)
    seen = []

    def client(result):
        def get(host, path, params=None):
            seen.append((host, path, params))
            return SimpleNamespace(json=lambda: {"result": result}, sha256="e" * 64, received_ms=1)
        return SimpleNamespace(get=get)

    report = {token.address: {"is_honeypot": "0", "cannot_sell_all": "1", "buy_tax": "0.05", "sell_tax": "0.3", "is_mintable": "1",
                              "transfer_pausable": "1", "is_open_source": "0", "hidden_owner": "0"}}
    out = _goplus_evm(client(report), token)
    assert seen[0] == ("api.gopluslabs.io", "/api/v1/token_security/8453", {"contract_addresses": token.address})
    assert out["cannot_sell"] and not out["honeypot"] and out["buy_tax"] == 0.05 and out["sell_tax"] == 0.3
    assert out["flags"] == ["émission possible", "transferts suspendables", "code source non publié"]
    assert _goplus_evm(client({}), token) is None  # token inconnu de la source : aucune conclusion inventée
