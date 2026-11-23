"""Moteur rapide (SPEC.md, chantier Moteur court terme) : bougies d'une heure, signaux sans regard vers le futur,
marchés ouverts seulement aux meneuses en tendance, comptes à la semaine. Hors ligne."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import core, rapide

MONDAY = 1_756_684_800_000  # lundi 1er septembre 2025, 00:00 UTC


def minutes(n=60 * 24 * 20, seed=2, start=MONDAY, drift=0.00002):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.0012, n)))
    o = np.r_[c[0], c[:-1]]
    v = rng.uniform(5, 15, n)
    return pd.DataFrame({"t": start + np.arange(n) * 60_000, "o": o, "h": np.maximum(o, c) * 1.0006,
                         "l": np.minimum(o, c) * 0.9994, "c": c, "v": v, "taker_buy": v / 2})


def test_hourly_bars_enter_at_the_next_hour_and_reject_incomplete_hours():
    m = minutes(180)
    m.loc[70, ["o", "h", "l", "c", "v"]] = np.nan
    b = rapide.hourly_bars(m)
    assert list(b["m_next"]) == [60, 120, 180]
    assert b["h"].iloc[0] == m["h"].iloc[:60].max() and b["c"].iloc[0] == m["c"].iloc[59]
    assert not b["complete"].iloc[1] and np.isnan(b["c"].iloc[1])


def test_setups_never_use_future_hours():
    m = minutes()
    full = rapide.setups(rapide.hourly_bars(m))
    cut_t = int(m["t"].iloc[len(m) // 2])
    part = rapide.setups(rapide.hourly_bars(m[m["t"] < cut_t]))
    known = full[full["decided_at"] <= cut_t]
    assert len(known) > 0
    pd.testing.assert_frame_equal(known.reset_index(drop=True)[["t", "setup", "stop"]],
                                  part[part["decided_at"] <= cut_t].reset_index(drop=True)[["t", "setup", "stop"]])


def test_alerts_respect_the_risk_bounds_and_carry_a_plan():
    a = rapide.alerts("TESTUSDC", minutes(drift=0.00006))
    assert not a.empty
    assert a["risk_pct"].between(rapide.Rules().min_risk, rapide.Rules().max_risk).all()
    assert (a["cost_r"] <= 0.2 + 1e-9).all() and set(a["setup"]) <= {"repli", "cassure"}
    assert (a["exit_ms"] > a["decided_at"]).all()


def test_only_leaders_in_an_uptrend_are_open_and_the_day_is_known_in_advance():
    days = np.arange(20_000, 20_100)
    up = pd.Series(100 * 1.01 ** np.arange(100), index=days)
    closes = pd.DataFrame({"BTCUSDC": up, "AUSDC": up * 1.0, "BUSDC": 100 * 0.99 ** np.arange(100)}, index=days)
    opened = rapide.open_markets(closes)
    assert opened["AUSDC"].iloc[-1] and not opened["BUSDC"].iloc[-1]
    d = int(days[-1])
    cands = pd.DataFrame({"decided_at": [d * core.DAY_MS + 3_600_000, d * core.DAY_MS + 3_600_000],
                          "symbol": ["AUSDC", "BUSDC"]})
    assert list(rapide.gate(cands, opened)) == [True, False]
    # Un signal de la dernière heure du jour d − 1 (décidé à 00:00 du jour d) relève encore du jour d − 1.
    late = pd.DataFrame({"decided_at": [d * core.DAY_MS], "symbol": ["AUSDC"]})
    assert rapide.gate(late, opened)[0] == bool(opened.at[d - 1, "AUSDC"])


def test_weekly_report_counts_positive_negative_and_flat_weeks():
    start = MONDAY
    curve = [(start, 1000.0), (start + 2 * core.DAY_MS, 1010.0), (start + 8 * core.DAY_MS, 1005.0)]
    trades = [{"exit_ms": start + 2 * core.DAY_MS}, {"exit_ms": start + 8 * core.DAY_MS}]
    rep = rapide.weekly_report({"curve": curve, "capital": 1000.0, "trades": trades}, start, start + 3 * rapide.WEEK_MS)
    assert rep["weeks"] == 3 and rep["positive_weeks"] == 1 and rep["negative_weeks"] == 1 and rep["flat_weeks"] == 1
    assert [w["trades"] for w in rep["week_rows"]] == [1, 1, 0]
    assert rep["week_rows"][0]["pct"] == pytest.approx(1.0)


def test_trailing_stop_follows_the_high_and_never_goes_down():
    from sentinelle.scalp.trade import trail
    path = [(100, 100.5, 99.8, 100.4), (100.4, 103, 100.3, 102.8), (102.8, 103.2, 102.1, 102.5), (102.5, 102.6, 101.9, 102)]
    o, h, l, c = (np.array(x, dtype=float) for x in zip(*path))
    r = trail(o, h, l, c, 0, 99.0, 1.0, 4, round_trip=0.0)
    # Minute 3 : plus haut des minutes précédentes 103,2, stop 102,2 ; le plus bas 101,9 le touche, vente à 102,2.
    assert r["outcome"] == "suiveur" and r["exit"] == pytest.approx(102.2)
    r = trail(o, h, l, c, 0, 99.0, 1.0, 5, round_trip=0.0)
    assert r["outcome"] == "invalide"  # issue encore inconnue : historique trop court


def test_the_trailing_variant_keeps_breakouts_only():
    m = minutes(n=60 * 24 * 40, drift=0.00006)
    hour = (m["t"] // rapide.HOUR_MS) % 29
    m.loc[hour == 0, "v"] *= 6  # pics de volume réguliers : des cassures possibles
    a = rapide.alerts("TESTUSDC", m, rapide.SUIVEUSE)
    assert len(a) > 0 and set(a["setup"]) == {"cassure"} and a["reasoning"].str.contains("suiveur").all()
    flat = minutes(n=60 * 24 * 5)
    flat[["o", "h", "l", "c"]] = 100.0
    assert rapide.alerts("FLATUSDC", flat, rapide.SUIVEUSE).empty  # aucun signal : résultat vide, sans erreur
