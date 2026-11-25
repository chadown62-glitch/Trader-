"""Régimes de marché et abstention (C021, C024, C026, C030) : causalité, régimes inconnus, témoin décalé."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import regimes, signaux


def test_efficiency_ratio_separates_a_straight_move_from_a_round_trip():
    idx = pd.RangeIndex(0, 60)
    line = pd.Series(100.0 + np.arange(60), index=idx)
    zigzag = pd.Series(100.0 + np.where(np.arange(60) % 2 == 0, 0.0, 1.0), index=idx)
    assert regimes.efficiency_ratio(line).iloc[-1] == pytest.approx(1.0)
    assert regimes.efficiency_ratio(zigzag).iloc[-1] < 0.05
    assert regimes.efficiency_ratio(line).iloc[: regimes.ER_WINDOW].isna().all()  # pas de valeur avant 30 jours


def test_terciles_only_use_values_known_at_the_date():
    rng = np.random.default_rng(1)
    x = pd.Series(rng.normal(size=500))
    t = regimes.expanding_tercile(x, min_history=100)
    assert t.iloc[:99].isna().all() and t.iloc[99:].isin([0.0, 1.0, 2.0]).all()
    later = x.copy()
    later.iloc[300:] += 50.0  # l'avenir change...
    pd.testing.assert_series_equal(t.iloc[:300], regimes.expanding_tercile(later, min_history=100).iloc[:300])  # ...pas le passé


def test_correlation_with_btc_ignores_assets_outside_the_universe():
    rng = np.random.default_rng(2)
    idx = pd.RangeIndex(0, 80)
    btc = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 80))), index=idx)
    noise = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 80))), index=idx)
    c = pd.DataFrame({"BTCUSDT": btc, "TWINUSDT": btc * 2, "NOISEUSDT": noise})
    member = pd.DataFrame(True, index=idx, columns=c.columns)
    member["NOISEUSDT"] = False
    corr = regimes.btc_correlation(c, member)
    assert corr.iloc[: regimes.CORR_WINDOW].isna().all() and corr.iloc[-1] == pytest.approx(1.0)


def test_rules_never_abstain_on_an_unknown_regime_and_abstention_closes_the_next_open():
    idx = pd.RangeIndex(0, 5)
    g = pd.DataFrame({"efficacite": [np.nan, 0.1, 0.5, 0.5, 0.1], "tendance": [False, False, True, True, False],
                      "hausse": [False, True, False, True, False], "tercile_volatilite": [np.nan, 2, 1, 2, 0],
                      "tercile_correlation": [np.nan, 0, 2, 2, 2]}, index=idx)
    m = regimes.rules(g)
    assert m["A1 BTC en range"].tolist() == [False, True, False, False, True]
    assert m["A2 BTC en tendance baissière"].tolist() == [False, False, True, False, False]
    assert m["A3 volatilité dans le tercile haut"].tolist() == [False, True, False, True, False]
    assert m["A4 corrélation haute et BTC en baisse sur 30 jours"].tolist() == [False, False, True, False, True]
    signal = pd.DataFrame(True, index=idx, columns=["AUSDT"])
    gated = regimes.abstain(signal, m["A1 BTC en range"])
    on, _ = signaux.positions(gated, pd.DataFrame(True, index=idx, columns=["AUSDT"]), {"o": pd.DataFrame(1.0, index=idx, columns=["AUSDT"]),
                                                                                         "c": pd.DataFrame(1.0, index=idx, columns=["AUSDT"])})
    assert on["AUSDT"].tolist() == [False, True, False, True, True]  # régime de la clôture d → liquidités à l'ouverture d + 1


def test_the_control_keeps_the_share_and_lengths_of_abstention():
    mask = pd.Series([False] * 400 + [True] * 30 + [False] * 370)
    ctl = regimes.shifted(mask)
    assert ctl.sum() == mask.sum() and not ctl.equals(mask)
    runs = np.diff(np.flatnonzero(np.diff(np.r_[0, ctl.to_numpy().astype(int), 0])))[::2]
    assert runs.tolist() == [30]


def test_market_regimes_are_built_from_closes_known_at_each_date():
    rng = np.random.default_rng(3)
    idx = pd.RangeIndex(0, 500)
    c = pd.DataFrame({k: 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(idx)))) for k in ("BTCUSDT", "AUSDT", "BUSDT")}, index=idx)
    member = pd.DataFrame(True, index=idx, columns=c.columns)
    g = regimes.regimes(c, member)
    assert set(g.columns) == {"efficacite", "tendance", "hausse", "volatilite", "tercile_volatilite", "correlation", "tercile_correlation"}
    assert g["tercile_volatilite"].iloc[: regimes.MIN_HISTORY_DAYS].isna().all() and g["tercile_volatilite"].iloc[-1] in (0.0, 1.0, 2.0)
    later = c.copy()
    later.loc[400:] *= 2.0
    pd.testing.assert_frame_equal(g.loc[:399], regimes.regimes(later, member).loc[:399])  # aucune valeur future
