"""Réactions aux publications FOMC et CPI (C007) : heure officielle en UTC, témoin apparié, référence avant l'annonce."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import evenements


def test_official_release_times_are_converted_to_utc_with_daylight_saving():
    assert evenements.release_utc("2024-07-31", "14:00") == pd.Timestamp("2024-07-31 18:00", tz="UTC")  # EDT
    assert evenements.release_utc("2021-12-15", "14:00") == pd.Timestamp("2021-12-15 19:00", tz="UTC")  # EST
    assert evenements.release_utc("2025-08-12", "08:30") == pd.Timestamp("2025-08-12 12:30", tz="UTC")


def test_the_control_is_a_week_earlier_and_never_another_event_day():
    t = evenements.release_utc("2024-07-31", "14:00")
    assert evenements.control_time(t, {"2024-07-31"}) == t - pd.Timedelta(days=7)
    assert evenements.control_time(t, {"2024-07-31", "2024-07-24"}) == t - pd.Timedelta(days=14)


def test_the_reaction_is_measured_from_the_last_price_known_before_the_release():
    t = 1_000 * 60_000
    idx = np.arange(t - 5 * 60_000, t + 61 * 60_000, 60_000)
    closes = pd.Series(100.0, index=idx)
    closes.loc[t:] = 101.0  # saut à la minute de l'annonce
    closes.loc[t + 9 * 60_000] = 103.0  # pic à la 10e minute
    r = evenements.reaction(closes, t)
    assert r["abs_5"] == pytest.approx(np.log(1.01)) and r["signe_60"] == pytest.approx(np.log(1.01))
    assert r["minute_du_pic"] == 10
    assert evenements.reaction(closes.drop(t - 60_000), t) is None  # pas de cours avant l'annonce : non mesurable
    assert evenements.reaction(closes.drop(t + 30 * 60_000), t) is None  # minute manquante dans l'heure


def test_paired_and_signed_tests():
    rng = np.random.default_rng(0)
    control = np.abs(rng.normal(0, 0.002, 60))
    strong = evenements.paired_test(control * 3 + 0.001, control)
    assert strong["p"] < 0.01 and strong["ic95"][0] > 0
    symmetric = evenements.signed_test(rng.normal(0, 0.01, 60))
    assert symmetric["p"] > 0.05 and symmetric["ic95"][0] < 0 < symmetric["ic95"][1]


def test_events_exclude_the_reserved_period_and_flag_dates_not_read_verbatim():
    evs = evenements.events()
    days = {(e["type"], e["jour"]) for e in evs}
    assert ("FOMC", "2026-09-16") not in days and ("CPI", "2026-10-14") not in days and ("CPI", "2026-11-10") not in days
    assert ("FOMC", "2026-07-29") in days and ("CPI", "2018-02-14") in days
    assert sum(not e["verbatim"] for e in evs) == len(evenements.FOMC_NOT_VERBATIM)
    assert all(e["temoin_utc"] < e["utc"] for e in evs)
