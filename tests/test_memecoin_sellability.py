"""Resale checked again before a new entry: an hours-old Jupiter round trip no longer vouches for a route that is gone.

Reproduced on main c87da1a: a pool admitted with a 2 % round trip loses its resale route; an hour later momentum is
decided, the fleet judges the admission's measurement (2 %), no Jupiter request is made (the scanner revisits a pool only
after 6 hours) and the entry is filled: a fictitious position that no route could sell. Before a new entry, a round trip
older than ``ROUND_TRIP_MAX_AGE_S`` (600 s, the life of an order) is now measured again; a resale without route leaves
the loss unknown, which the Rug Pull Guardian already treats as an unconfirmed resale. No veto is loosened, a pool never
measured gains nothing, and the admission never replaces a later measurement. Offline and deterministic."""

import pytest

from sentinelle.dmh import established_pools as est, memecoin_simulator as sim
from sentinelle.dmh.net import SourceUnavailable
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT, NOW
from tests.test_memecoin_engine import ACCELERATION, causal_session


def jupiter(feed):
    return sum(1 for u in feed.urls if u.startswith("api.jup.ag"))


def an_hour_later(db, **change):
    """Admission (round trip measured), then a change of the resale route, a baseline an hour later and a momentum burst."""
    feed, clock, step = causal_session(db)
    step(0)
    admitted = jupiter(feed)
    for name, value in change.items():
        setattr(feed, name, value)
    step(3600)
    step(60, **ACCELERATION)
    return feed, step, admitted


def test_an_hour_old_round_trip_is_measured_again_and_a_lost_route_blocks_the_entry(db):
    feed, step, admitted = an_hour_later(db, sell_route=False)
    assert jupiter(feed) - admitted == 2  # buy then sell quoted again, at the decision
    row = sim.journal(db)[-1]
    judged = row["evaluations"][MINT]["round_trip"]
    assert (judged["loss"], judged["refreshed"], judged["measured_ms"]) == (None, True, (NOW + 3660) * 1000)
    assert "No routes found" in judged["error"]
    state = sim.load(db)[0]
    [advice] = state["fleet"]
    assert advice["decision"] == "ATTENDRE" and "revendabilité non confirmée" in advice["why"][0]
    step(60, priceUsd="1.02")
    state = sim.load(db)[0]
    assert state["positions"] == {} and state["orders"] == {} and state["fills"] == []


def test_with_the_route_kept_the_entry_is_decided_and_filled_as_before(db):
    feed, step, admitted = an_hour_later(db)
    judged = sim.journal(db)[-1]["evaluations"][MINT]["round_trip"]
    assert (judged["loss"], judged["refreshed"]) == (pytest.approx(0.02), True) and jupiter(feed) - admitted == 2
    filled = step(60, priceUsd="1.02")
    assert [f["strategy"] for f in filled["activity"]] == ["momentum"]


def test_a_fresh_measurement_costs_no_request(db):
    feed, clock, step = causal_session(db)
    step(0)
    admitted = jupiter(feed)
    step(60, **ACCELERATION)  # 60 s after the admission: the measurement is fresh
    judged = sim.journal(db)[-1]["evaluations"][MINT]["round_trip"]
    assert judged["refreshed"] is False and jupiter(feed) == admitted
    assert [o["key"] for o in sim.report(sim.load(db)[0], {})["pending_orders"]] == ["momentum/" + MINT]


def test_a_pool_never_measured_gains_nothing_and_is_not_quoted():
    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("aucune requête pour un pool jamais mesuré")

    asset = {"symbol": MINT}
    assert sim.refresh_round_trip(NoNetwork(), asset, NOW) is None and "round_trip_loss" not in asset


def test_a_costly_route_is_vetoed_and_an_outage_waits(db, monkeypatch):
    feed, step, admitted = an_hour_later(db, loss=0.15)  # the route now loses 15 %, above the 10 % limit
    assert sim.journal(db)[-1]["evaluations"][MINT]["round_trip"]["loss"] == pytest.approx(0.15)
    assert [f["decision"] for f in sim.load(db)[0]["fleet"]] == ["VETO"]
    asset = {"symbol": MINT, "round_trip_loss": 0.02, "round_trip_ms": 0}

    def down(client, mint):
        raise SourceUnavailable("api.jup.ag", "HTTP 429")

    monkeypatch.setattr(est, "round_trip", down)
    judged = sim.refresh_round_trip(None, asset, NOW)
    assert (judged["loss"], asset["round_trip_loss"], asset["round_trip_ms"]) == (None, None, NOW * 1000)
    assert "429" in judged["error"]  # unknown: never an entry, and never a 0 % loss


def test_the_admission_never_replaces_a_later_measurement_and_a_route_back_is_seen_again(db):
    feed, step, admitted = an_hour_later(db, sell_route=False)
    step(60)  # next cycle: the admission's 2 % must not come back
    asset = sim.load(db)[0]["assets"][MINT]
    assert (asset["round_trip_loss"], asset["round_trip_ms"]) == (None, (NOW + 3660) * 1000)
    feed.sell_route = True
    step(600, volume={"m5": 500}, txns={"m5": {"buys": 6, "sells": 3}})  # back to the baseline pace
    step(60, **ACCELERATION)  # more than 600 s after the last measurement: a new burst measures again
    judged = sim.journal(db)[-1]["evaluations"][MINT]["round_trip"]
    assert judged["refreshed"] is True and judged["loss"] == pytest.approx(0.02)
    assert [f["decision"] for f in sim.load(db)[0]["fleet"]][-1] == "PROPOSITION_SIMULÉE"


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
