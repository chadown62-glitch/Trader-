"""Moteur court terme (SPEC.md) : lecture du marché, veilleurs et gestion des trades.
Causalité (aucune information future) et règles de gestion prudentes. Hors ligne."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp.features import FEATURES, compute, five_minute_bars
from sentinelle.scalp.trade import BREAKEVEN, INVALID, STOP, TARGET, TIME, manage
from sentinelle.scalp.watchers import ROUND_TRIP, alerts


def minutes(n=6000, seed=1, start=1_756_684_800_000):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
    o = np.r_[c[0], c[:-1]]
    v = rng.uniform(5, 15, n)
    return pd.DataFrame({"t": start + np.arange(n) * 60_000, "o": o, "h": np.maximum(o, c) * 1.0008,
                         "l": np.minimum(o, c) * 0.9992, "c": c, "v": v, "taker_buy": v * 0.5})


def test_five_minute_bars_and_next_open():
    m = minutes(20)
    m.loc[7, ["o", "h", "l", "c", "v", "taker_buy"]] = np.nan
    b = five_minute_bars(m)
    assert len(b) == 4 and list(b["m_next"]) == [5, 10, 15, 20]
    assert b["h"].iloc[0] == m["h"].iloc[:5].max() and b["c"].iloc[0] == m["c"].iloc[4]
    assert not b["complete"].iloc[1] and np.isnan(b["c"].iloc[1])


def test_features_never_use_future_bars():
    m = minutes(6000)
    full = compute(five_minute_bars(m))
    cut = compute(five_minute_bars(m.iloc[:3000]))
    k = len(cut) - 1
    a, b = full.loc[k, FEATURES].astype(float), cut.loc[k, FEATURES].astype(float)
    assert np.allclose(a.fillna(-9), b.fillna(-9))


def test_alerts_decide_at_the_close_and_respect_cost_limits():
    a = alerts("TESTUSDC", compute(five_minute_bars(minutes(20000, seed=4))))
    assert not a.empty
    assert (a["decided_at"] == a["t"] + 300_000).all()
    assert (a["cost_r"] <= 0.25 + 1e-9).all() and (a["risk_pct"] <= 0.04).all()
    assert a["thesis"].str.contains("Invalidation").all()


def path(rows):
    return (np.array(x, dtype=float) for x in zip(*rows))


def test_target_after_one_r_and_breakeven_protection():
    o, h, l, c = path([(100, 100.5, 99.5, 100), (100, 101.1, 100, 101), (101, 102.1, 100.9, 102)])
    r = manage(o, h, l, c, 0, 99.0, 10)
    assert r["outcome"] == TARGET and r["exit"] == pytest.approx(102.0)
    o, h, l, c = path([(100, 101.1, 99.5, 101), (101, 101.2, 99.9, 100), (100, 100, 100, 100)])
    r = manage(o, h, l, c, 0, 99.0, 3)
    assert r["outcome"] == BREAKEVEN and r["exit"] == pytest.approx(100 * (1 + ROUND_TRIP))
    assert r["net_return"] == pytest.approx(0.0)


@pytest.mark.parametrize("round_trip", [0.0022, 0.0017, 0.002, 0.0013])
def test_breakeven_is_exactly_zero_never_a_win(round_trip):
    o, h, l, c = path([(100, 101.1, 99.5, 101), (101, 101.2, 99.9, 100), (100, 100, 100, 100)])
    r = manage(o, h, l, c, 0, 99.0, 3, round_trip=round_trip)
    assert r["outcome"] == BREAKEVEN and r["net_return"] == 0.0 and r["r"] == 0.0


def test_stop_rules_entry_below_stop_and_missing_minutes():
    o, h, l, c = path([(100, 101.5, 98.5, 100), (100, 100, 100, 100)])
    assert manage(o, h, l, c, 0, 99.0, 2)["outcome"] == STOP  # stop et +1 R dans la même minute : stop
    o, h, l, c = path([(100, 100.2, 99.8, 100), (97, 97.5, 96.5, 97)])
    r = manage(o, h, l, c, 0, 99.0, 2)
    assert r["outcome"] == STOP and r["exit"] == pytest.approx(97.0)
    o, h, l, c = path([(98, 99, 97, 98)])
    assert manage(o, h, l, c, 0, 99.0, 1)["outcome"] == INVALID
    o, h, l, c = path([(100, 100.2, 99.8, 100), (np.nan,) * 4, (100, 103, 100, 102)])
    assert manage(o, h, l, c, 0, 99.0, 3)["outcome"] == INVALID
    o, h, l, c = path([(100, 100.2, 99.8, 100), (100, 100.3, 99.9, 100.2)])
    r = manage(o, h, l, c, 0, 99.0, 2)
    assert r["outcome"] == TIME and r["exit"] == pytest.approx(100.2)
    assert manage(o, h, l, c, 0, 99.0, 5)["outcome"] == INVALID  # issue encore inconnue


