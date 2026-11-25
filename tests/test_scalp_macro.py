"""Contexte macroéconomique (C014, C015, C016, C017) : dates de séance, causalité, seuils, décalage d'un jour."""

import numpy as np
import pandas as pd

from sentinelle.scalp import macro, moteur


def _day(text: str) -> int:
    return moteur._day(text)


def test_session_timestamps_map_to_the_session_day_whatever_the_exchange_hour():
    # Horodatages réels de Yahoo : dollar à minuit de New York (04:00 ou 05:00 UTC), VIX à 07:00 ou 08:00 UTC,
    # fonds à l'ouverture (13:30 ou 14:30 UTC) : tous appartiennent au jour de la séance, jamais au lendemain.
    idx = pd.to_datetime(["2026-07-15 04:00", "2026-11-24 05:00", "2026-11-24 08:00", "2026-11-24 14:30"])
    assert macro.day_numbers(idx).tolist() == [_day("2026-07-15")] + [_day("2026-11-24")] * 3


def _sessions(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_rules_only_use_the_history_known_at_the_session():
    rng = np.random.default_rng(3)
    n = 700
    s = {"taux_10_ans": _sessions(2 + np.cumsum(rng.normal(0, 0.05, n))),
         "dollar": _sessions(100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))),
         "vix": _sessions(20 * np.exp(np.cumsum(rng.normal(0, 0.05, n)))),
         "nasdaq_100": _sessions(300 * np.exp(np.cumsum(rng.normal(0, 0.012, n))))}
    rules = macro.session_rules(s)
    later = {k: v.copy() for k, v in s.items()}
    for v in later.values():
        v.iloc[500:] *= 3.0  # l'avenir change...
    again = macro.session_rules(later)
    for name, (rule, _) in rules.items():
        cut = rule.index[rule.index < s["vix"].index[500]]
        pd.testing.assert_series_equal(rule.loc[cut], again[name][0].loc[cut])  # ...pas les règles passées
        if name.startswith("M5"):
            continue
        assert not rule.iloc[: macro.MIN_SESSIONS - 1].any()  # historique insuffisant : jamais d'abstention


def test_nasdaq_rule_triggers_from_a_two_percent_drop():
    q = _sessions([100.0, 98.0, 96.1, 97.0])  # −2,0 %, −1,94 %, +0,94 %
    rule, carry = macro.session_rules({"taux_10_ans": q, "dollar": q, "vix": q, "nasdaq_100": q})["M5 Nasdaq-100 en baisse d'au moins 2 % sur la séance"]
    assert rule.tolist() == [False, True, False, False] and carry is False


def test_sessions_inform_the_same_crypto_row_and_states_persist_over_weekends_only_when_asked():
    friday, monday = pd.Timestamp("2026-11-20"), pd.Timestamp("2026-11-23")
    rule = pd.Series([True, False], index=pd.DatetimeIndex([friday, monday]))
    grid = pd.RangeIndex(_day("2026-11-19"), _day("2026-11-24") + 1)
    carried = macro.to_crypto_days(rule, grid, carry=True)
    single = macro.to_crypto_days(rule, grid, carry=False)
    # jeudi (avant la première séance), vendredi, samedi, dimanche, lundi, mardi
    assert carried.tolist() == [False, True, True, True, False, False]
    assert single.tolist() == [False, True, False, False, False, False]


def test_lead_lag_sees_the_next_day_and_not_the_same_day():
    rng = np.random.default_rng(4)
    sessions = pd.bdate_range("2019-01-01", periods=600)
    leader_r = pd.Series(rng.normal(0, 0.01, len(sessions)), index=sessions)
    leader = 100 * (1 + leader_r).cumprod()
    days = pd.RangeIndex(int(macro.day_numbers(sessions[:1])[0]), int(macro.day_numbers(sessions[-1:])[0]) + 3)
    noise = pd.Series(rng.normal(0, 0.02, len(days)), index=days)
    leader_by_day = pd.Series(leader.pct_change().to_numpy(), index=macro.day_numbers(sessions)).reindex(days)
    follows = noise.copy()
    follows[leader_by_day.shift(1).notna()] = leader_by_day.shift(1).dropna()  # BTC du jour d + 1 = séance d
    same_day = noise.copy()
    same_day[leader_by_day.notna()] = leader_by_day.dropna()  # BTC du jour d = séance d (déjà connu : pas d'avance)
    a = macro.lead_lag(leader, 100 * np.exp(follows.cumsum()))
    b = macro.lead_lag(leader, 100 * np.exp(same_day.cumsum()))
    assert a["spearman"] > 0.9 and a["p_bilaterale"] < 0.01
    assert abs(b["spearman"]) < 0.15
    late = macro.lead_lag(leader, 100 * np.exp(follows.cumsum()), start="2020-01-01")
    assert late["n"] < a["n"]
