"""Flux de stablecoins (C018) et effets calendaires (C029) : délai du relevé, causalité, calendrier, abstention."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import flux, moteur, regimes, signaux


def _day(text: str) -> int:
    return moteur._day(text)


def test_weekdays_and_month_turn_are_read_on_utc_day_numbers():
    days = [_day(t) for t in ("2026-11-21", "2026-11-23", "2026-11-30", "2026-12-01", "2026-12-03", "2026-12-04")]
    assert flux.weekday(days).tolist() == [5, 0, 0, 1, 3, 4]  # samedi, lundi, lundi, mardi, jeudi, vendredi
    assert flux.month_turn(days).tolist() == [False, False, True, True, True, False]


def test_a_snapshot_is_used_one_day_later_and_gaps_are_not_filled_beyond_a_day():
    start = _day("2024-01-01")
    payload = [{"date": (start + k) * 86400, "totalCirculatingUSD": {"peggedUSD": v}}
               for k, v in enumerate([0.0, 100.0, 101.0, 102.0])]
    payload.append({"date": (start + 6) * 86400, "totalCirculatingUSD": {"peggedUSD": 105.0}})
    supply = flux.parse_supply(payload)
    assert supply.index.tolist() == [start + 1, start + 2, start + 3, start + 6]  # valeur nulle : couverture absente
    known = flux.usable(supply, pd.RangeIndex(start, start + 8))
    assert np.isnan(known.iloc[:2]).all()
    assert known.loc[start + 2] == 100.0 and known.loc[start + 4] == 102.0  # relevé de la veille
    assert known.loc[start + 5] == 102.0 and np.isnan(known.loc[start + 6])  # un seul jour comblé
    assert known.loc[start + 7] == 105.0


def test_supply_rule_ignores_the_future_and_unknown_history():
    rng = np.random.default_rng(5)
    idx = pd.RangeIndex(0, 900)
    known = pd.Series(1e9 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 900))), index=idx)
    rule = flux.supply_rule(known)
    assert not rule.iloc[: flux.MIN_DAYS + flux.SUPPLY_WINDOW - 1].any()
    later = known.copy()
    later.iloc[600:] *= 0.5
    pd.testing.assert_series_equal(rule.iloc[:600], flux.supply_rule(later).iloc[:600])


def test_weekend_rule_keeps_cash_on_saturday_and_sunday_opens():
    grid = pd.RangeIndex(_day("2026-11-19"), _day("2026-11-24") + 1)  # jeudi → mardi
    mask = flux.weekend_rule(grid)
    assert mask.tolist() == [False, True, True, False, False, False]
    signal = pd.DataFrame(True, index=grid, columns=["AUSDT"])
    one = pd.DataFrame(1.0, index=grid, columns=["AUSDT"])
    on, _ = signaux.positions(regimes.abstain(signal, mask), pd.DataFrame(True, index=grid, columns=["AUSDT"]), {"o": one, "c": one})
    assert on["AUSDT"].tolist() == [False, True, False, False, True, True]  # pas de position samedi ni dimanche


def test_mean_gap_finds_a_planted_weekday_effect_and_nothing_elsewhere():
    rng = np.random.default_rng(6)
    days = pd.RangeIndex(_day("2019-01-01"), _day("2019-01-01") + 1400)
    r = pd.Series(rng.normal(0, 0.01, len(days)), index=days)
    r[flux.weekday(days) == 0] += 0.01  # lundi : +1 %
    monday = flux.mean_gap(r, flux.weekday(days) == 0)
    assert monday["ecart_moyen_pct"] == pytest.approx(100 * (r[flux.weekday(days) == 0].mean() - r[flux.weekday(days) != 0].mean()))
    assert monday["ecart_moyen_pct"] > 0.7 and monday["p_bilaterale"] < 0.01
    cal = flux.calendar(100 * np.exp(r.cumsum()))
    assert cal["lundi"]["p_holm"] < 0.05 and set(cal) == set(flux.WEEKDAYS) | {"tournant de mois"}


def test_supply_lead_sees_a_supply_change_that_leads_btc_and_ignores_one_that_lags_it():
    rng = np.random.default_rng(11)
    days = pd.RangeIndex(_day("2020-01-01"), _day("2020-01-01") + 900)
    btc = pd.Series(30000 * np.exp(np.cumsum(rng.normal(0, 0.03, len(days)))), index=days)
    ahead = np.log(btc).shift(-flux.LEAD_DAYS) - np.log(btc)  # rendement du BTC sur les 7 jours suivants
    noise = pd.Series(rng.normal(0, 0.002, len(days)), index=days)
    leading = pd.Series(1e11 * np.exp((ahead.fillna(0.0) + noise).cumsum() / flux.LEAD_DAYS), index=days)
    lead = flux.supply_lead(leading, btc)
    lagging = pd.Series(1e11 * np.exp(0.01 * np.log(btc / btc.iloc[0])), index=days)  # suit le passé du BTC
    lag = flux.supply_lead(lagging.shift(30), btc)
    assert lead["n"] > 800 and lead["spearman"] > 0.3 and lead["p_bilaterale"] < 0.01
    assert abs(lag["spearman"]) < 0.2
