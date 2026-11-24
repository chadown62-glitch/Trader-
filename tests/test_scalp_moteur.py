"""Moteur unique : portefeuille partagé entre poches, causalité, statistiques hebdomadaires, poches et vue du jour."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import actifs, moteur

D0 = 20_000  # numéro de jour UTC (2024-10-04, un vendredi)


def pocket(values, active, cost=0.001, first=D0):
    idx = pd.RangeIndex(first, first + len(values))
    return {"equity": pd.Series(values, index=idx, dtype=float), "active": pd.Series(active, index=idx, dtype=bool), "cost": cost}


def test_idle_pocket_gives_its_share_to_the_invested_one():
    n = 30
    actions = pocket(1000 * 1.01 ** np.arange(n), [True] * n)
    crypto = pocket([1000.0] * n, [False] * n, cost=0.002)
    out = moteur.unify({"crypto": crypto, "actions": actions}, moteur.SHARES, D0 + 1, D0 + n - 1)
    # Tout le capital suit la poche actions, une fois payé l'achat initial (0,10 %).
    expected = 1000 * (1 - 0.001) * 1.01 ** np.arange(1, n)
    assert np.allclose(out["equity"].to_numpy(), expected)
    assert out["transfers"] == 1


def test_shares_follow_the_base_split_when_both_pockets_are_invested():
    n = 20
    actions = pocket(1000 * 1.01 ** np.arange(n), [True] * n)
    crypto = pocket(1000 * 1.03 ** np.arange(n), [True] * n, cost=0.002)
    out = moteur.unify({"crypto": crypto, "actions": actions}, moteur.SHARES, D0 + 1, D0 + n - 1)
    start = 1000 - 1000 * (0.35 * 0.002 + 0.65 * 0.001)
    expected = start * (0.35 * 1.03 ** np.arange(1, n) + 0.65 * 1.01 ** np.arange(1, n))
    assert np.allclose(out["equity"].to_numpy(), expected)
    fixed = moteur.unify({"crypto": crypto, "actions": actions}, moteur.SHARES, D0 + 1, D0 + n - 1, transfers=False)
    assert np.allclose(fixed["equity"].to_numpy(), 1000 * (0.35 * 1.03 ** np.arange(1, n) + 0.65 * 1.01 ** np.arange(1, n)))


def test_no_pocket_invested_means_cash_and_a_new_entry_is_only_funded_the_next_day():
    n = 10
    # La poche entre en position pendant le jour D0 + 5 (détention à la fin de ce jour) : son gain de ce jour-là n'est
    # pas capté, faute de part au début du jour ; elle reçoit tout le capital à partir de D0 + 6.
    values = [1000.0] * 5 + [1100.0, 1210.0, 1210.0, 1210.0, 1210.0]
    crypto = pocket(values, [False] * 5 + [True] * 5, cost=0.0)
    out = moteur.unify({"crypto": crypto}, {"crypto": 1.0}, D0 + 1, D0 + n - 1)
    eq = out["equity"]
    assert (eq.loc[:D0 + 5] == 1000.0).all()
    assert eq.at[D0 + 6] == pytest.approx(1100.0)


def test_transfer_charges_the_seller_only_while_it_stays_invested():
    n = 6
    actions = pocket([1000.0] * n, [True] * n, cost=0.001)
    crypto = pocket([1000.0] * n, [False, False, True, True, True, True], cost=0.002)
    out = moteur.unify({"crypto": crypto, "actions": actions}, moteur.SHARES, D0 + 1, D0 + n - 1)
    first = 1000 * (1 - 0.001)
    second = first - first * 0.35 * (0.002 + 0.001)  # achat crypto + vente partielle de la poche actions restée investie
    assert out["equity"].at[D0 + 2] == pytest.approx(first)
    assert out["equity"].at[D0 + 3] == pytest.approx(second)
    assert out["transfer_costs"] == pytest.approx(1000 - second)


def test_weekly_statistics_run_monday_to_sunday():
    monday = D0 + 3  # D0 est un vendredi
    idx = pd.RangeIndex(monday, monday + 21)
    eq = pd.Series(1000.0, index=idx)
    eq.loc[monday + 7:] = 1020.0  # +2 % le lundi de la deuxième semaine
    s = moteur.stats(eq)
    assert s["weeks"] == 3 and s["positive_weeks"] == 1 and s["flat_weeks"] == 2
    assert s["weeks_at_reference"] == 1 and s["worst_week_pct"] == 0.0
    assert s["return_pct"] == pytest.approx(2.0)
    eq.loc[monday + 14:] = 1009.8
    s = moteur.stats(eq)
    assert s["negative_weeks"] == 1 and s["max_drawdown_pct"] == pytest.approx(1.0)


def test_window_rescales_to_the_previous_close():
    eq = pd.Series([1000.0, 1100.0, 1210.0], index=pd.RangeIndex(D0, D0 + 3))
    assert moteur.window(eq, D0 + 2, D0 + 2).tolist() == [1100.0]
    assert moteur.window(eq, D0, D0 + 1).tolist() == [1000.0, 1100.0]


def tables(days=260, trend=None):
    idx = pd.RangeIndex(D0 - days, D0)
    t = np.arange(days)
    trend = trend or {"BTCUSDT": 0.004, "DOGEUSDT": 0.008, "PEPEUSDT": 0.006, "ETHUSDT": -0.003}
    closes = pd.DataFrame({s: 100 * np.exp(g * t + 0.01 * np.sin(t / 3 + i)) for i, (s, g) in enumerate(trend.items())}, index=idx)
    opens = closes.shift(1).fillna(closes.iloc[0])
    return opens, closes, closes * 0.995


def test_memecoins_only_use_btc_as_a_filter_never_as_a_holding():
    tb = tables()
    res, _ = moteur._crypto_run(tb, ["DOGEUSDT", "PEPEUSDT"], D0 - 200, D0 - 1)
    held = {s for _, h in res["holdings"] for s in h}
    assert held and held <= {"DOGEUSDT", "PEPEUSDT"}
    falling = tables(trend={"BTCUSDT": -0.004, "DOGEUSDT": 0.008, "PEPEUSDT": 0.006})
    res, _ = moteur._crypto_run(falling, ["DOGEUSDT", "PEPEUSDT"], D0 - 200, D0 - 1)
    assert not any(h for _, h in res["holdings"])  # BTC en baisse : aucun memecoin acheté


def etf(trend):
    idx = pd.bdate_range("2023-01-02", "2024-10-03")
    t = np.arange(len(idx))
    return pd.DataFrame({k: 100 * np.exp(trend * t + 0.002 * np.sin(t / 5 + i)) for i, k in enumerate(actifs.UNIVERSE)}, index=idx)


def test_actions_pocket_is_out_when_every_fund_falls():
    s, e = D0 - 200, D0 - 1
    up = moteur.actions_pocket(etf(0.0008), s, e)
    down = moteur.actions_pocket(etf(-0.0008), s, e)
    assert up["active"].loc[s:e].any() and not down["active"].loc[s:e].any()
    assert down["equity"].loc[s:e].eq(1000.0).all()


def test_today_plan_splits_fresh_capital_and_flags_news_vetoes():
    tb = tables()
    plan = moteur.today_plan(("crypto", "memecoins", "actions"), tb, etf(0.0008), 1000.0, {"assets": {"DOGE": ["piratage"]}})
    lines = plan["lignes"]
    assert {x["poche"] for x in lines} == {"crypto", "actions"}
    assert all(x["actif"] != "ETH" for x in lines)  # en baisse : jamais acheté
    assert sum(x["part_pct"] for x in lines) + plan["liquidites_pct"] == pytest.approx(100.0)
    assert all(x["montant"] == pytest.approx(10 * x["part_pct"], abs=0.06) for x in lines)  # arrondis séparés
    doge = [x for x in lines if x["actif"] == "DOGE"]
    assert doge and doge[0]["entree_bloquee_par_actualite"]
    memes_only = moteur.today_plan(("memecoins",), tb, etf(0.0008), 1000.0, {})
    assert {x["actif"] for x in memes_only["lignes"]} <= {"DOGE", "PEPE"}


class FakeClient:
    def __init__(self, first):
        self.first = first

    def head(self, url):
        month = url.rsplit("-1d-", 1)[1][:7]
        return type("R", (), {"status_code": 200 if month >= self.first else 404})()


def test_listing_month_is_found_by_bisection():
    assert moteur.first_month("SUIUSDT", "2026-10", FakeClient("2023-05")) == "2023-05"
    assert moteur.first_month("BTCUSDT", "2026-10", FakeClient("2017-08")) == "2017-08"
    assert moteur.first_month("NEWUSDT", "2026-10", FakeClient("2027-01")) is None
