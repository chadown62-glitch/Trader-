"""Poche fonds indiciels et portefeuille global (SPEC.md, chantier Moteur court terme) : règles publiées appliquées
sans regard vers le futur, exécution le jour de bourse suivant, frais, liquidités, combinaison des poches. Hors ligne."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import actifs, portefeuille


def prices(n_days=800, seed=0, drifts=None, noise=0.01):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2020-01-01", periods=n_days)
    drifts = drifts or {t: 0.0003 for t in actifs.UNIVERSE}
    return pd.DataFrame({t: 100 * np.exp(np.cumsum(d + rng.normal(0, noise, n_days))) for t, d in drifts.items()}, index=days)


def test_parse_reads_adjusted_closes_and_drops_duplicate_days():
    payload = {"chart": {"result": [{"timestamp": [1704205800, 1704292200, 1704292300],
                                     "indicators": {"adjclose": [{"adjclose": [10.0, 11.0, 11.5]}]}}]}}
    s = actifs.parse(payload)
    assert list(s.values) == [10.0, 11.5] and s.index[0] == pd.Timestamp("2024-01-02")


def test_g1_holds_only_funds_above_their_ten_month_average():
    px = prices(drifts={**{t: 0.001 for t in actifs.UNIVERSE[:4]}, **{t: -0.001 for t in actifs.UNIVERSE[4:]}}, noise=0.002)
    w = actifs.month_end_signals(px, "G1")
    last = w.iloc[-1]
    assert (last[actifs.UNIVERSE[:4]] == 1 / 8).all() and (last[actifs.UNIVERSE[4:]] == 0).all()
    assert w.index[0] >= px.resample("ME").last().index[9]  # pas de décision avant 10 mois d'historique


def test_g2_keeps_the_three_strongest_trending_funds():
    drifts = {t: 0.0002 * (i + 1) for i, t in enumerate(actifs.UNIVERSE)}
    px = prices(drifts=drifts, seed=3, noise=0.002)
    w = actifs.month_end_signals(px, "G2").iloc[-1]
    m = px.resample("ME").last()
    score = (sum(m / m.shift(k) - 1 for k in (1, 3, 6, 12)) / 4).iloc[-1]
    assert set(w[w > 0].index) == set(score.nlargest(3).index) and w.sum() == pytest.approx(1.0)
    assert "SPY" not in set(w[w > 0].index)  # la plus faible tendance n'est jamais retenue


def test_decisions_execute_the_next_trading_day_and_ignore_the_future():
    px = prices(seed=5)
    base = actifs.run(px, "G1", "2021-06-01", "2022-12-30")
    future = px.copy()
    future.loc["2022-07-01":] *= 0.5
    cut = actifs.run(future, "G1", "2021-06-01", "2022-12-30")
    before = [v for d, v in base["curve"] if d < pd.Timestamp("2022-07-01")]
    assert before == [v for d, v in cut["curve"] if d < pd.Timestamp("2022-07-01")]


def test_cash_when_every_fund_is_below_its_average_and_costs_are_paid():
    down = actifs.run(prices(drifts={t: -0.002 for t in actifs.UNIVERSE}, noise=0.002), "G1", "2021-06-01", "2022-12-30")
    assert down["final"] == pytest.approx(1000.0)  # tout en liquidités, rien échangé
    bh = actifs.benchmark(prices(), {"SPY": 1.0}, "2021-06-01", "2022-12-30")
    px = prices().ffill()
    ratio = px["SPY"].loc["2021-06-01":"2022-12-30"]
    assert 1 + bh["return_pct"] / 100 == pytest.approx((1 - actifs.SIDE_COST) * ratio.iloc[-1] / ratio.iloc[0], rel=1e-3)


def test_pockets_are_marked_daily_and_combined_by_fixed_shares():
    day = 86_400_000
    a = portefeuille.on_days([(2 * day, 1100.0), (5 * day, 1210.0)], 0, 6 * day, 1000.0)
    assert list(a.round(4)) == [1.0, 1.0, 1.1, 1.1, 1.1, 1.21, 1.21]  # le week-end garde la dernière valeur
    b = portefeuille.on_days([(3 * day, 900.0)], 0, 6 * day, 1000.0)
    mix = 0.35 * a + 0.65 * b
    assert mix.iloc[-1] == pytest.approx(0.35 * 1.21 + 0.65 * 0.9)

