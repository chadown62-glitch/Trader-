"""Market regime and recent highs (SPEC.md, Sprint 5 extension: market regimes, fewer false signals).

- No new entry is decided or filled while SOL has lost 5 % or more over the last hour; exits are never blocked; an
  unknown regime blocks nothing and is recorded.
- Dip hunter measures a pullback against the high of the last 24 h only.
Parameters fixed before any result. Offline and deterministic."""

import pytest

from sentinelle.dmh import memecoin_simulator as sim
from sentinelle.dmh import memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import Feed, MINT, NOW, WSOL
from tests.test_memecoin_engine import ACCELERATION, asset, causal_session, entered

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
KEY = "momentum/" + MINT


class Client:
    def __init__(self, pairs):
        self.pairs = pairs

    def get(self, host, path):
        assert host == "api.dexscreener.com" and path.endswith(WSOL)
        return type("Fetched", (), {"json": lambda _self: self.pairs})()


def sol(change=None, liquidity=1e6):
    pair = {"chainId": "solana", "baseToken": {"address": WSOL}, "quoteToken": {"address": USDC}, "liquidity": {"usd": liquidity},
            "priceUsd": "120"}
    if change is not None:
        pair["priceChange"] = {"h1": change}
    return pair


@pytest.mark.parametrize("pairs,status", [
    ([sol(-2.0)], "normal"), ([sol(-4.99)], "normal"), ([sol(-5.0)], "risk_off"), ([sol(-7.5)], "risk_off"),
    ([sol(-9.0, liquidity=10.0), sol(1.0, liquidity=1e7)], "normal"),  # the deepest pool is the reference
    ([sol()], "unknown"), ([], "unknown"),
])
def test_the_regime_follows_the_deepest_sol_pool(pairs, status):
    regime = sim.market_regime(Client(pairs))
    assert regime["status"] == status and sim.REGIME_DROP_H1 == 0.05
    if status == "unknown":
        assert regime["reason"]


RISK_OFF = {"status": "risk_off", "sol_change_h1_pct": -6.0}


def test_no_entry_is_decided_or_filled_while_sol_falls(db, monkeypatch):
    feed, clock, step = causal_session(db)
    step(0)
    step(60, **ACCELERATION)  # decided in a normal regime (unknown here: the feed publishes no 1 h change)
    [(oid, order)] = sim.load(db)[0]["orders"].items()
    monkeypatch.setattr(sim, "market_regime", lambda client: RISK_OFF)
    state = step(60, priceUsd="1.02", volume={"m5": 6000}, txns={"m5": {"buys": 40, "sells": 3}})
    assert state["positions"] == {} and oid in sim.load(db)[0]["orders"]  # neither filled nor replaced by a new decision
    assert state["entry_gate"] == {"open": False, "reason": "régime de marché : SOL -6.0 % sur 1 h (seuil −5 %)", "blocking": []}
    assert sim.load(db)[0]["orders"][oid]["last_refusal"] == "entrée suspendue : " + state["entry_gate"]["reason"]
    assert sim.journal(db)[-1]["market_regime"] == RISK_OFF


def test_exits_are_never_blocked_by_the_regime(db, monkeypatch):
    feed, clock, step, order = entered(db)
    step(60, priceUsd="0.7")  # stop decided
    monkeypatch.setattr(sim, "market_regime", lambda client: RISK_OFF)
    step(60, priceUsd="0.66")
    assert KEY not in sim.load(db)[0]["positions"] and sim.load(db)[0]["completed"][0]["exit"] == "stop_loss"


def test_an_unavailable_regime_blocks_nothing_and_is_recorded(db, monkeypatch):
    feed, clock, step = causal_session(db)

    def outage(client):
        raise ValueError("cotation SOL indisponible")

    monkeypatch.setattr(sim, "market_regime", outage)
    step(0)
    state = step(60, **ACCELERATION)
    assert [o["key"] for o in state["pending_orders"]] == [KEY] and state["entry_gate"]["open"]
    assert sim.load(db)[0]["market_regime"] == {"status": "unknown", "reason": "cotation SOL indisponible"}


def dip_on(peak_age):
    feed = Feed()
    feed.pair["priceUsd"] = "0.7"
    a = asset()
    point = sim.quote(feed.client, a, NOW)
    point.update(volume_usd_per_min_m5=10.0)
    a.update(peak=1.0, peak_liquidity=point["liquidity_usd"], last={**point, "ts": NOW - 60})
    if peak_age is not None:
        a["peak_ts"] = NOW - peak_age
    signals, guard = ms.evaluate(feed.client, a, point, False)
    assert guard["allowed"]
    return "dip_hunter" in signals


@pytest.mark.parametrize("age,fires", [(3600, True), (ms.DIP_PEAK_MAX_AGE_S, True), (ms.DIP_PEAK_MAX_AGE_S + 1, False), (None, True)])
def test_dip_hunter_only_measures_a_pullback_against_a_recent_high(age, fires):
    assert ms.DIP_PEAK_MAX_AGE_S == 24 * 3600 == sim.MAX_HOLD_S
    assert dip_on(age) is fires  # None: a high recorded before its time was kept (former sessions)


def test_an_old_high_is_restarted_from_the_current_observation():
    a = {"peak": 2.0, "peak_ts": NOW - ms.DIP_PEAK_MAX_AGE_S - 10, "peak_liquidity": 90_000.0}
    sim._observe(a, {"price": 1.0, "liquidity_usd": 40_000.0, "ts": NOW})
    assert (a["peak"], a["peak_ts"], a["peak_liquidity"]) == (1.0, NOW, 40_000.0)
    sim._observe(a, {"price": 0.9, "liquidity_usd": 50_000.0, "ts": NOW + 60})  # lower: the recent high is kept
    assert (a["peak"], a["peak_ts"], a["peak_liquidity"]) == (1.0, NOW, 50_000.0)
    legacy = {"peak": 2.0, "peak_liquidity": 90_000.0}  # recorded before the high had a time: its window starts now
    sim._observe(legacy, {"price": 1.0, "liquidity_usd": 40_000.0, "ts": NOW})
    assert (legacy["peak"], legacy["peak_ts"]) == (2.0, NOW)


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
