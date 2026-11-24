"""Recherche v1 : grille, mélange avec les fonds, frein de baisse et sélection hors échantillon sans regard vers l'avenir."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import moteur, recherche


def days_between(a, b):
    return np.arange(moteur._day(a), moteur._day(b) + 1)


def test_grid_has_324_rules_per_universe():
    rules = list(recherche.rules_grid())
    assert len(rules) == 324
    assert all(r.market_filter == "BTCUSDT" and r.keep_rank == 5 for r in rules)


def test_mix_is_rebalanced_on_january_first_with_costs():
    days = days_between("2019-12-30", "2020-01-02")
    starts = recherche.year_starts(pd.RangeIndex(days[0], days[-1] + 1))
    rc = np.array([0.0, 1.0, 0.0, 0.0])  # la crypto double le 31 décembre
    rg = np.zeros(4)
    r = recherche.mix_returns(rc, rg, 0.5, starts, 0.01, 0.01)
    assert r[1] == pytest.approx(0.5)
    # Au 1er janvier : parts 2/3 – 1/3 ramenées à 1/2 – 1/2, un sixième du capital déplacé à 2 % de frais.
    assert r[2] == pytest.approx(-(1.5 / 6) * 0.02 / 1.5)
    assert recherche.mix_returns(rc, rg, 1.0, starts, 0.01, 0.01).tolist() == rc.tolist()


def test_brake_halves_exposure_from_the_day_after_the_drawdown():
    r = np.array([0.0, -0.2, 0.1, 0.1, 0.1])
    out = recherche.brake_returns(r, 0.15, 0.0)
    assert out[1] == pytest.approx(-0.2)  # la baisse du jour n'est connue qu'à la clôture
    assert out[2] == pytest.approx(0.05)  # le lendemain : moitié exposée
    assert recherche.brake_returns(r, None, 0.0).tolist() == r.tolist()


def test_window_stats_counts_weeks_and_drawdown():
    days = days_between("2024-01-01", "2024-01-21")  # trois semaines, du lundi au dimanche
    r = np.zeros(len(days))
    r[7] = 0.02
    r[14] = -0.01
    s = recherche.window_stats(r, days)
    assert s["weeks"] == 3 and s["positive_weeks"] == 1 and s["negative_weeks"] == 1
    assert s["weeks_at_reference"] == 1
    assert s["max_drawdown_pct"] == pytest.approx(1.0)


def test_rules_r1_and_r2():
    rows = [{"mean_week_pct": 1.0, "max_drawdown_pct": 40.0, "annual_pct": 60.0},
            {"mean_week_pct": 0.5, "max_drawdown_pct": 20.0, "annual_pct": 25.0},
            {"mean_week_pct": 0.3, "max_drawdown_pct": 4.0, "annual_pct": 12.0}]
    assert recherche.choose(rows, "R1") == 1  # 40 % de baisse : écarté
    assert recherche.choose(rows, "R2") == 2  # 12 / 5 > 25 / 20 > 60 / 40


def test_walk_forward_only_uses_past_years():
    days = days_between("2018-01-01", "2026-11-22")
    years = pd.to_datetime(days * moteur.DAY_MS, unit="ms").year.to_numpy()
    good_early = np.where(years < 2020, 0.002, -0.001)
    good_late = np.where(years < 2020, -0.001, 0.002)
    R = np.vstack([good_early, good_late])
    res = recherche.walk_forward(R, days, "R1", "toutes", np.zeros(2))
    assert res["chosen"][2020]["index"] == 0  # seules 2018 et 2019 sont connues : le réglage d'alors est retenu
    first = res["returns"][: int((years == 2020).sum())]
    assert np.allclose(first, -0.001)  # et il perd en 2020 : la sélection ne voit pas l'avenir
    rolling = recherche.walk_forward(R, days, "R1", "deux dernières", np.zeros(2))
    assert rolling["chosen"][2022]["index"] == 1


def test_series_is_cut_when_the_asset_changes_under_the_same_name():
    idx = pd.RangeIndex(100, 106)
    c = pd.Series([1.0, 0.5, 0.0001, 0.0001, 6.0, 6.2], index=idx)  # effondrement puis nouvel actif recoté
    bars = pd.DataFrame({"o": c.shift(1).fillna(1.0), "c": c, "l": c, "q": 1.0})
    bars.loc[102, "o"] = 0.5  # l'effondrement se fait dans la journée : pas une rupture de cotation
    cut, at = recherche.cut_at_break(bars)
    assert at == 104 and cut.index[-1] == 103


def test_point_in_time_universe_uses_only_volumes_known_before_the_month():
    days = days_between("2021-01-01", "2021-03-31")
    q = pd.DataFrame({"A": 100.0, "B": 10.0, "C": 1.0}, index=days)
    feb = moteur._day("2021-02-01")
    q.loc[feb:, "C"] = 1000.0  # C devient la plus échangée à partir du 1er février
    m = recherche.point_in_time_members(q, n=2)
    assert m.loc[feb, ["A", "B"]].all() and not m.loc[feb, "C"]  # février : décidé sur janvier
    mar = moteur._day("2021-03-01")
    assert m.loc[mar, ["A", "C"]].all() and not m.loc[mar, "B"]


class Probe:
    def __init__(self, first, last):
        self.first, self.last = first, last

    def head(self, url):
        month = url.rsplit("-1d-", 1)[1][:7]
        return type("R", (), {"status_code": 200 if self.first <= month <= self.last else 404})()


def test_published_range_finds_delisted_pairs():
    assert recherche.published_range("LUNAUSDT", "2026-10", Probe("2020-08", "2022-05")) == ("2020-08", "2022-05")
    assert recherche.published_range("BTCUSDT", "2026-10", Probe("2017-08", "2026-10")) == ("2017-08", "2026-10")
    assert recherche.published_range("NONEUSDT", "2026-10", Probe("2030-01", "2030-02")) is None


def test_a_crypto_outside_the_universe_is_never_bought():
    from sentinelle.scalp import core
    idx = pd.RangeIndex(0, 200)
    t = np.arange(200)
    closes = pd.DataFrame({"BTCUSDT": 100 * np.exp(0.003 * t), "XUSDT": 100 * np.exp(0.01 * t)}, index=idx)
    members = pd.DataFrame({"BTCUSDT": True, "XUSDT": False}, index=idx)
    rules = core.CoreRules(market_filter="BTCUSDT", top=1)
    rank, _ = core.signals(closes, rules, members)
    assert rank["XUSDT"].isna().all() and (rank["BTCUSDT"].dropna() == 1).all()
    res = core.run(closes, closes, 0, 200 * core.DAY_MS, 1000.0, rules, members=members)
    assert all(h in ([], ["BTCUSDT"]) for _, h in res["holdings"])


def test_relisting_after_a_trading_halt_is_cut():
    # LUNA : dernière cotation le 13 mai 2022 à 0,00005 $, 17 jours sans cotation, puis nouvel actif à 8,87 $.
    idx = pd.RangeIndex(100, 106)
    c = pd.Series([1.0, 0.00005, np.nan, np.nan, 8.87, 6.5], index=idx)
    bars = pd.DataFrame({"o": [1.2, 1.0, np.nan, np.nan, 1.0, 8.87], "c": c, "l": c, "q": 1.0})
    cut, at = recherche.cut_at_break(bars)
    assert at == 104 and cut.index[-1] == 103
