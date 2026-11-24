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


def bars_of(closes, start):
    c = pd.Series(closes, index=pd.RangeIndex(start, start + len(closes)), dtype=float)
    return pd.DataFrame({"o": c.shift(1).fillna(c.iloc[0]), "c": c, "l": c * 0.99, "q": 1.0})


def rename(last, first, ratio=1.0, announced_days_before=12):
    day = lambda d: str(pd.to_datetime(d * recherche.DAY_MS, unit="ms").date())  # noqa: E731
    return {"nouvelle": "NEWUSDT", "ratio": ratio, "annonce": day(last - announced_days_before), "arret_utc": day(last) + "T03:00:00Z",
            "ouverture_utc": day(first) + "T08:00:00Z", "sources": ["https://www.binance.com/en/support/announcement/x"]}


def test_a_renamed_pair_is_spliced_at_the_announced_ratio_and_dates():
    # MATIC → POL : dernier cours 0,3794 $, deux jours sans cotation, nouvelle paire ouverte à 0,3794 $.
    old, new = bars_of([0.40, 0.39, 0.3794], 100), bars_of([0.3794, 0.41, 0.40], 105)
    new.loc[105, "o"] = 0.3794
    joined, rep = recherche.splice(old, new, rename(102, 105))
    assert rep["statut"] == "relié" and rep["jours_sans_cotation"] == 2 and rep["ecart_prix"] == 0.0
    assert joined.index.tolist() == list(range(100, 108))
    assert joined.loc[103:104, "c"].isna().all() and joined.at[106, "c"] == 0.41
    # VEN → VET, 1 pour 100 : prix de l'ancienne divisés par 100, volumes en unités multipliés par 100.
    ven = bars_of([2.0, 1.8], 200).assign(v=5.0)
    vet = bars_of([0.018, 0.02], 202).assign(v=900.0)
    joined, rep = recherche.splice(ven, vet, rename(201, 202, ratio=100.0))
    assert rep["statut"] == "relié" and joined.at[200, "c"] == pytest.approx(0.02) and joined.at[201, "v"] == 500.0


def test_the_spliced_series_never_uses_the_new_pair_before_it_trades():
    old, new = bars_of([1.0, 1.1, 1.2, 1.25], 300), bars_of([1.25, 1.3, 1.4], 306)
    joined, _ = recherche.splice(old, new, rename(303, 306))
    pd.testing.assert_frame_equal(joined.loc[:303], old.loc[:303], check_freq=False)  # avant l'arrêt : l'ancienne seule
    assert joined.loc[304:305].isna().all().all()  # pendant l'arrêt : aucun cours inventé


def test_a_same_day_handover_merges_the_two_bars():
    # BCHABC → BCH le 28/11/2019 : arrêt à 02:00, ouverture à 10:00, même jour.
    old, new = bars_of([210.0, 220.08], 400).assign(q=4.0), bars_of([220.08, 224.0], 401).assign(q=11.0)
    old.loc[401, "o"], new.loc[401, "o"], new.loc[401, "c"] = 219.2, 220.08, 217.41
    joined, rep = recherche.splice(old, new, rename(401, 401))
    assert rep["statut"] == "relié" and rep["jours_sans_cotation"] == 0
    assert (joined.at[401, "o"], joined.at[401, "c"], joined.at[401, "q"]) == (219.2, 217.41, 15.0)


def test_a_splice_is_refused_on_a_price_jump_wrong_dates_late_announcement_or_long_halt():
    old = bars_of([1.0, 1.0, 1.0], 100)
    jump = bars_of([1.05, 1.05], 104)
    jump.loc[104, "o"] = 1.05  # +5 % : pas le même actif au ratio annoncé
    joined, rep = recherche.splice(old, jump, rename(102, 104))
    assert joined is None and rep["statut"] == "refusé" and "cours de reprise" in rep["raisons"][0]
    assert recherche.splice(old, bars_of([1.0, 1.0], 104), rename(102, 105))[1]["statut"] == "refusé"  # dates hors annonce
    assert recherche.splice(old, bars_of([1.0, 1.0], 104), rename(102, 104, announced_days_before=-1))[1]["raisons"] == ["annonce postérieure à l'arrêt"]
    assert recherche.splice(old, bars_of([1.0, 1.0], 101), rename(102, 101))[1]["statut"] == "refusé"  # chevauchement
    assert recherche.splice(old, bars_of([1.0, 1.0], 112), rename(102, 112))[1]["statut"] == "refusé"  # 9 jours sans cotation
    assert recherche.splice(old, pd.DataFrame(columns=old.columns), rename(102, 104)) == (None, {"statut": "absent"})
    stray = pd.concat([old, bars_of([0.0001], 190)])  # cotation résiduelle trois mois après l'arrêt annoncé
    new = bars_of([1.0, 1.0], 104)
    new.loc[104, "o"] = 1.0
    joined, rep = recherche.splice(stray, new, rename(102, 104))
    assert rep["statut"] == "relié" and rep["jours_cotes_apres_arret_ignores"] == 1 and joined.index[-1] == 105


def test_the_chain_stops_at_the_first_refused_link():
    saved = dict(recherche.RENAMES), dict(recherche.PREDECESSORS)
    try:
        recherche.RENAMES.clear()
        recherche.RENAMES.update({"AUSDT": {**rename(102, 104), "nouvelle": "BUSDT"}, "BUSDT": {**rename(105, 107), "nouvelle": "CUSDT"}})
        recherche.PREDECESSORS.clear()
        recherche.PREDECESSORS.update({"BUSDT": "AUSDT", "CUSDT": "BUSDT"})
        series = {"AUSDT": bars_of([1.0, 1.0, 1.0], 100), "CUSDT": bars_of([9.0, 9.0], 107)}
        series["CUSDT"].loc[107, "o"] = 9.0  # ×9 : refusé
        b = bars_of([1.0, 1.0], 104)
        b.loc[104, "o"] = 1.0
        bars, links = recherche.link_chain("BUSDT", b, series.get)
        assert [(x["sens"], x["statut"]) for x in links] == [("prédécesseur", "relié"), ("successeur", "refusé")]
        assert bars.index[0] == 100 and bars.index[-1] == 105
    finally:
        recherche.RENAMES.clear()
        recherche.RENAMES.update(saved[0])
        recherche.PREDECESSORS.clear()
        recherche.PREDECESSORS.update(saved[1])


def test_declared_renames_are_official_announced_before_the_halt_and_never_counted_twice():
    assert set(recherche.RENAMES) == {"VENUSDT", "BCHABCUSDT", "MATICUSDT", "EOSUSDT", "TONUSDT"}
    for r in recherche.RENAMES.values():
        assert all(u.startswith("https://www.binance.com/") for u in r["sources"]) and r["ratio"] > 0
        assert r["annonce"] < r["arret_utc"][:10] <= r["ouverture_utc"][:10]  # connue avant l'arrêt : aucune fuite
    linked = set(recherche.RENAMES) | {r["nouvelle"] for r in recherche.RENAMES.values()}
    assert len(linked & set(recherche.CANDIDATES)) == len(recherche.RENAMES)  # un seul membre de chaque lien est candidat
