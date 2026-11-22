"""Cycle time, reception time, unknown publication time: the 600 s bounds hold on reception times too.

A quote is stamped with its cycle's start (``ts``, used for ``decided_at`` and the order's life) but received later in the
cycle (``received_ms``), by a delay that changes with the quote's place in the cycle and the network; DexScreener publishes
no time for its price and none is invented. Reproduced on main 96d26c7 (``tests/test_memecoin_cadence.py`` harness, whale
reads on 24 pools): revisits packed at 600 s of cycle time were received 604 s apart, and momentum fired on four such
pairs. The sniper's and momentum's upper bound now applies to both gaps, and an entry whose execution quote was received
more than ``ORDER_TTL_S`` after the decision's expires. Only refusals are added; nothing changes when reception equals the
cycle time, nor without a reception time. Replayed, same-instant and unprovenanced quotes are covered by
``tests/test_memecoin_engine.py`` (``test_a_replayed_ambiguous_or_expired_quote_never_fills``,
``test_execution_check_refuses_old_or_unprovenanced_quotes``). Offline and deterministic."""

import pytest

from sentinelle.dmh import memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT, NOW
from tests.test_memecoin_cadence import session
from tests.test_memecoin_engine import ACCELERATION, causal_session


def lagged(db):
    """A causal session whose clock moves while the pool's own quote is in flight: ``at(t, delay)`` runs the cycle that
    starts at ``t`` and receives the quote ``delay`` seconds later."""
    feed, clock, step = causal_session(db)
    lag = {"s": 0}
    real = feed.client.transport

    def transport(method, url, headers, body, timeout):
        if url.endswith("/token-pairs/v1/solana/" + MINT):
            clock["t"] += lag["s"]
        return real(method, url, headers, body, timeout)

    feed.client._transport = transport  # the client's own transport, wrapped

    def at(t, delay=0, **pair):
        clock["t"], lag["s"] = t, delay
        return step(0, **pair)

    return feed, at


def test_a_slow_request_keeps_its_cycle_time_and_its_reception_time_and_the_price_it_received(db):
    feed, at = lagged(db)
    at(NOW)
    at(NOW + 60, delay=7, priceUsd="1.03", **ACCELERATION)  # the price moved while the request was in flight
    row = sim.journal(db)[-1]
    quote, ev = row["quotes"][MINT], row["evaluations"][MINT]
    assert (quote["ts"], quote["received_ms"], quote["price"]) == (NOW + 60, (NOW + 67) * 1000, 1.03)
    assert (ev["gap_s"], ev["reception_gap_s"], ev["signals"]) == (60, 67, ["momentum"])
    [order] = sim.load(db)[0]["orders"].values()
    # the decision belongs to the cycle that started at NOW + 60 and rests on data received at NOW + 67, never earlier
    assert order["decided_at"] == NOW + 60 and order["decision"]["received_ms"] == (NOW + 67) * 1000
    assert order["decision"]["price"] == 1.03 and "published" not in str(order["decision"])  # no publication time invented


def test_quotes_of_one_cycle_share_the_cycle_time_and_are_received_one_after_the_other(db):
    feed, cycle = session(db, latency=1.0)
    cycle(15)
    row = sim.journal(db)[-1]
    received = [q["received_ms"] for q in row["quotes"].values()]
    assert len(received) == 4 and received == sorted(received) and len(set(received)) == 4
    assert {q["ts"] for q in row["quotes"].values()} == {row["ts"]} and min(received) > row["ts"] * 1000


@pytest.mark.parametrize(("delay", "fires"), [(0, True), (4, False)])
def test_momentum_at_the_600_s_bound_holds_on_reception_times(db, delay, fires):
    feed, at = lagged(db)
    at(NOW)  # baseline received at the cycle start
    at(NOW + 600, delay=delay, **ACCELERATION)  # 600 s of cycle time; received 600 or 604 s after the baseline
    ev = sim.journal(db)[-1]["evaluations"][MINT]
    assert (ev["gap_s"], ev["reception_gap_s"]) == (600, 600 + delay)
    assert ("momentum" in ev["signals"]) is fires
    audit = sim.audit_signals(sim.journal(db))  # the journal alone re-derives the decision, reception times included
    assert audit["mismatches"] == [] and audit["checked"] == audit["matched"] == 2


def test_the_sniper_bound_holds_on_reception_times_and_falls_back_to_the_cycle_time_without_one():
    previous = {"price": 1.0, "liquidity_usd": 50_000.0, "volume_usd_per_min_m5": 100.0, "buy_sell_count_ratio_m5": 2.0,
                "ts": NOW, "received_ms": NOW * 1000}
    point = {"price": 1.05, "liquidity_usd": 50_000.0, "volume_usd_per_min_m5": 400.0, "buy_sell_count_ratio_m5": 4.0,
             "ts": NOW + 600, "received_ms": (NOW + 604) * 1000, "pool_age_minutes": 180.0, "volume_h1_usd": 12_000.0}
    asset = {"dex_id": "pumpswap", "last": previous, "peak": 1.05, "peak_ts": NOW + 600, "peak_liquidity": 50_000.0}
    assert ms.sniper_check(asset, point)["reason"] == "observation précédente périmée (604 s > 600 s)"
    assert ms.sniper_check(asset, {**point, "received_ms": (NOW + 600) * 1000})["signal"] is True
    assert ms.sniper_check(asset, {**point, "received_ms": None})["signal"] is True  # no reception time: cycle time only
    assert ms.reception_gap(point, {"ts": NOW}) is None and ms.reception_gap(point, previous) == 604
    momentum = {**point, "price": 1.0}
    assert "momentum" not in ms.entry_signals(asset, momentum)
    assert "momentum" in ms.entry_signals({**asset, "last": {k: v for k, v in previous.items() if k != "received_ms"}}, momentum)


@pytest.mark.parametrize(("delay", "status"), [(0, "executed"), (30, "expired")])
def test_an_entry_received_more_than_10_minutes_after_its_decision_expires(db, delay, status):
    feed, at = lagged(db)
    at(NOW)
    at(NOW + 60, **ACCELERATION)  # momentum decided; its quote received at NOW + 60
    for t in range(NOW + 120, NOW + 660, 60):
        at(t)  # the pool does not move: the snapshot is identical, the order waits
    at(NOW + 660, delay=delay, priceUsd="1.02")  # 600 s of cycle time after the decision; received 600 or 630 s after
    state = sim.load(db)[0]
    [resolved] = [o for o in state["order_log"] if o["kind"] == "buy"]
    assert resolved["status"] == status and resolved["resolved_at"] == NOW + 660
    if status == "expired":
        assert resolved["reason"] == "cotation reçue 630 s après celle de la décision (plus de 10 minutes)"
        assert state["positions"] == {} and [f for f in state["fills"] if f["side"] == "buy"] == []
    else:
        assert list(state["positions"]) == ["momentum/" + MINT]


def test_a_protective_exit_is_never_refused_for_its_age(db):
    feed, at = lagged(db)
    at(NOW)
    at(NOW + 60, **ACCELERATION)
    at(NOW + 120, priceUsd="1.02")  # filled
    at(NOW + 180, priceUsd="0.7")  # the stop triggers: a sale is decided
    [sale] = [o for o in sim.load(db)[0]["orders"].values() if o["kind"] == "sell"]
    at(NOW + 900, delay=30, priceUsd="0.69")  # received 750 s after the sale's decision: a protective exit still fills
    state = sim.load(db)[0]
    assert state["positions"] == {} and [f["side"] for f in state["fills"]] == ["buy", "sell"]
    assert sale["decided_at"] == NOW + 180


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
