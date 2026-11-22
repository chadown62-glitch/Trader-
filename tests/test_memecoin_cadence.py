"""Observation cadence: the rotation keeps only the pools it can revisit within the sniper's and momentum's own bound.

Reproduced on main 418ffa6 with this harness (24 admitted pools, the real per-host rate limits and request budget of
``net.PublicClient`` on a virtual clock, no network): four pools are quoted per cycle among the 24 most recently
admitted, so each is revisited every six cycles. Once a cycle period exceeds 100 s (an interval of 90 s or more, many held
lots with network latency, whale reads), every rotated pool was revisited after more than 600 s: the sniper and momentum
could fire on none of them, and nothing said so. The watched set is now ``ROTATION`` x floor(600 s / measured period),
at most ``WATCH_LIMIT``: no quote is added, held lots and pending orders stay quoted every cycle, and the cadence is
journaled. Offline and deterministic."""

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from sentinelle.dmh import established_pools as est, memecoin_simulator as sim, memecoin_strategies as ms, net, service
from sentinelle.dmh.tokens import load_registry
from sentinelle.services.bus import EventBus
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import b58
from tests.test_memecoin_engine import ACCELERATION, NOW, WSOL, EstablishedFeed


def mint(i):
    return b58(bytes([i + 1]) * 32)


def pool(i):
    return b58(bytes([200 - i]) * 32)


class Pools(EstablishedFeed):
    """``n`` established pools behind the real rate-limited client, on a clock that requests and waits move forward."""

    def __init__(self, n, latency=0.0):
        super().__init__()
        self.n, self.latency, self.wall, self.mono, self.requests = n, latency, float(NOW), 0.0, 0
        self.client = net.PublicClient(transport=self.route, clock=lambda: self.wall, monotonic=lambda: self.mono,
                                       sleep=self.sleep, retries=0, budget=service.HUB_BUDGET,
                                       budget_window_s=service.HUB_BUDGET_WINDOW_S)

    def sleep(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def route(self, method, url, headers, body, timeout):
        self.sleep(self.latency)
        self.requests += 1
        parts = urlsplit(url)
        if parts.hostname == "api.geckoterminal.com":
            return 200, {}, b'{"data": []}'  # every pool is already admitted (6 h cooldown)
        if parts.hostname == "api.dexscreener.com" and not url.endswith("/" + WSOL):
            m = parts.path.rsplit("/", 1)[1]
            i = [mint(k) for k in range(self.n)].index(m)
            pair = {**self.pair, "pairAddress": pool(i), "baseToken": {"address": m}, "dexId": "pumpswap"}
            return 200, {}, json.dumps([pair]).encode("utf-8")
        return self.transport(method, url, headers, body, timeout)


def session(db, n=24, held=0, latency=0.0):
    feed, bus = Pools(n, latency), EventBus(db)
    for i in range(n):
        item = {"attributes": {**feed.pool, "address": pool(i)}, "relationships": {
            "base_token": {"data": {"id": "solana_" + mint(i)}}, "quote_token": {"data": {"id": "solana_" + WSOL}},
            "dex": {"data": {"id": "pumpswap"}}}}
        est._record(db, bus, est.parse(item), NOW - 60 + i, "observation_accepted", "accepted", "pool établi retenu",
                    round_trip={"loss": 0.02}, guardian={"allowed": True})
    hub = SimpleNamespace(client=feed.client, registry=load_registry())
    if held:  # lots held on the most recently admitted pools
        sim.tick(hub, db, bus)
        state, _ = sim.load(db)
        for i in range(n - held, n):
            state["positions"][f"dip_hunter/{mint(i)}"] = {
                "mint": mint(i), "strategy": "dip_hunter", "qty": 0.2, "basis": 0.2, "initial": 0.2, "mark": 0.2, "pnl": 0.0,
                "high": 1.0, "opened_at": NOW, "marked_at": NOW, "exit_policy": sim.CALIBRATED_EXITS, "sold_fraction": 0.0}
            state["fills"].append({"mint": mint(i), "strategy": "dip_hunter", "side": "buy", "pnl": 0.0, "ts": NOW,
                                   "asset_domain": "memecoin", "simulated": True})
        db.set_meta(sim.KEY, json.dumps(state))

    def cycle(interval):
        before = feed.requests
        result = sim.tick(hub, db, bus)
        feed.sleep(interval)  # the worker waits its interval after each cycle
        return feed.requests - before, result

    return feed, cycle


def gaps(db, skip=2, exclude=()):
    """Revisit gaps of the rotated pools, from the journal alone, once the period has been measured."""
    rows = sim.journal(db)
    seen = {}
    for row in rows[skip:]:
        for m, q in row["quotes"].items():
            if m not in exclude and "ts" in q:
                seen.setdefault(m, []).append(q["ts"])
    return [b - a for ts in seen.values() for a, b in zip(ts, ts[1:])]


@pytest.mark.parametrize(("period", "watch"), [
    (None, 24), (0, 24), (34, 24), (99, 24), (100, 24), (109, 20), (139, 16), (319, 4), (600, 4), (601, 24), (3600, 24)])
def test_the_watched_set_follows_the_measured_period(period, watch):
    assert sim.watch_size(period) == watch
    assert sim.REVISIT_MAX_S == ms.SNIPER_MAX_GAP_S == ms.MOMENTUM_MAX_GAP_S == 600  # the strategies' bounds, unchanged
    assert (sim.WATCH_LIMIT, sim.ROTATION) == (24, 4)


def test_beyond_100_s_every_rotated_pool_came_back_too_late_now_within_600_s(db, monkeypatch):
    feed, cycle = session(db)
    calls = [cycle(120)[0] for _ in range(12)]  # a cycle of 19 s and an interval of 120 s: a period of 139 s
    after = gaps(db)
    assert after and max(after) <= 600 and len(after) >= 16
    cadence = sim.journal(db)[-1]["cadence"]
    assert (cadence["period_s"], cadence["watch"], cadence["watched"], cadence["rotation"]) == (139, 16, 16, 4)
    assert cadence["revisit_s"] == 556 and cadence["flow_possible"] is True
    assert calls[-4:] == [20] * 4  # steady state: 4 fixed requests and 4 per quoted pool

    # the former behaviour, reproduced: the 24 most recent pools whatever the period
    monkeypatch.setattr(sim, "watch_size", lambda period: sim.WATCH_LIMIT)
    db2 = type(db)(":memory:")
    feed2, cycle2 = session(db2)
    calls2 = [cycle2(120)[0] for _ in range(12)]
    before = gaps(db2)
    assert before and min(before) > 600  # every revisit too late: no sniper or momentum comparison was possible
    assert calls2[-4:] == calls[-4:] and max(calls) <= max(calls2)  # no request added, ever


def test_held_lots_are_still_quoted_every_cycle_and_latency_is_absorbed(db):
    feed, cycle = session(db, n=36, held=12, latency=1.5)  # 12 lots and 1.5 s per request: a period of 125 s
    for _ in range(10):
        cycle(15)
    rows = sim.journal(db)
    held = {mint(i) for i in range(24, 36)}
    assert all(held <= set(r["quotes"]) for r in rows[1:])  # the priority of held lots is kept
    assert rows[-1]["cadence"]["watch"] == 16 and max(gaps(db, exclude=held)) <= 600


def test_a_burst_on_a_watched_pool_reaches_momentum_again(db, monkeypatch):
    def burst(database):
        feed, cycle = session(database)
        for _ in range(8):
            cycle(120)
        feed.pair.update(ACCELERATION)  # every pool accelerates from now on
        start = feed.wall
        for _ in range(4):
            cycle(120)
        return [ev for r in sim.journal(database) if r["ts"] >= start for ev in r["evaluations"].values()]

    after = burst(db)
    assert any(e["signals"] == ["momentum"] and e["gap_s"] <= 600 for e in after)
    monkeypatch.setattr(sim, "watch_size", lambda period: sim.WATCH_LIMIT)
    before = burst(type(db)(":memory:"))
    assert before and all(e["signals"] == [] and e["gap_s"] > 600 for e in before)  # the baseline was always too old


def test_an_impossible_cadence_is_recorded_and_left_as_before(db):
    feed, cycle = session(db)
    for _ in range(3):
        count, result = cycle(700)  # an interval longer than 600 s: no pool can be revisited in time
    assert result["cadence"]["watch"] == sim.WATCH_LIMIT and result["cadence"]["flow_possible"] is False
    assert sim.journal(db)[0]["cadence"]["period_s"] is None  # the first cycle has no measured period


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
