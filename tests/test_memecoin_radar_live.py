"""Recorded-shape HTTP and RPC fixtures; no market traffic."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from sentinelle.dmh import memecoin_simulator as sim
from sentinelle.dmh.solana_scanner import LiveRadar, RadarClient
from tests import test_configurable_capital as frozen, test_dmh_api
from tests.test_dmh_scanner import Feed, MINT, NOW, WSOL

api_setup = test_dmh_api.setup


def test_public_rows_and_ttl():
    feed = Feed()
    feed.pair.update(priceChange={"m5": -1.5}, volume={"m5": 23, "h1": 165}, priceUsd="0.25", pairCreatedAt=(NOW - 60) * 1000)
    feed.pair["baseToken"]["symbol"] = "TEST"
    feed.rug.update(score_normalised=6)
    requests = []
    clock = [0]

    def transport(method, url, headers, body, timeout):
        requests.append(url)
        if url.endswith("/token-boosts/latest/v1"):
            rows = [{"chainId": "solana", "tokenAddress": x} for x in (MINT, WSOL, MINT)]
            return 200, {}, json.dumps(rows).encode("utf-8")
        return feed.transport(method, url, headers, body, timeout)

    radar = LiveRadar(lambda: RadarClient(transport, retries=0, clock=lambda: NOW, sleep=lambda _: None), lambda: clock[0])
    result = radar.get_live_radar_snapshot()
    (row,) = result["tokens"]
    assert result["asset_domain"] == "memecoin" and row["source"] == "live"
    assert (row["symbol"], row["liquidity_usd"], row["volume_5m"], row["volume_1h"], row["change_5m_pct"]) == ("TEST", 50000, 23, 165, -1.5)
    assert row["rug_score"] == 6 and row["eligible_strategies"] == ["sniper"]
    calls = len(requests)
    clock[0] += 59
    assert radar.get_live_radar_snapshot() == result and len(requests) == calls
    feed.offline = True
    clock[0] += 1
    (unknown,) = radar.get_live_radar_snapshot()["tokens"]
    assert len(requests) > calls and unknown["risk"] == "unknown" and not unknown["eligible_strategies"]


@pytest.mark.parametrize("status", [429, 503])
def test_unavailable_returns_only_explicit_display_fallback(status):
    radar = LiveRadar(lambda: RadarClient(lambda *_: (status, {}, b"offline"), retries=0))
    snapshot = radar.get_live_radar_snapshot()
    (row,) = snapshot["tokens"]
    assert row["source"] == "fallback" and row["mint"] is None
    assert row["eligible_strategies"] == ["momentum"]
    assert row["rug_score"] is None and snapshot["errors"]


def test_authenticated_domain_and_wallet_recency(api_setup, monkeypatch):
    app, client, _ = api_setup
    url = "/api/dmh/memecoin/radar"
    assert TestClient(client.app).get(url).status_code == 401
    wallet = {
        "qualified": True,
        "asset_domain": "memecoin",
        "cycles": [{"early": True, "ts": int(time.time()) - 3600, "mint": MINT, "pnl_sol": 1.0}] * 2,
    }
    wallets = {"observed": wallet, "excluded": {**wallet, "dumper": True}, "foreign": {**wallet, "asset_domain": "majors"}}
    monkeypatch.setattr(sim, "load", lambda _: ({"assets": {MINT: {"winner": True}}}, wallets))
    monkeypatch.setattr(
        LiveRadar, "get_live_radar_snapshot", lambda *_: {"asset_domain": "memecoin", "observed_at": NOW, "tokens": [], "errors": []}
    )
    result = client.get(url).json()
    assert result["asset_domain"] == "memecoin"
    (row,) = result["smart_wallets"]
    assert row["address"] == "observed" and row["win_rate"] == 1 and 0 < row["score_72h_sol"] < 2
    assert row["anti_dump"] == "no_flag_observed"


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_scientific_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
