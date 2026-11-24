"""Étude des familles de signaux : causalité, simulation sans rééquilibrage fictif, avantage, stabilité, abstention."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import moteur, signaux

D0 = moteur._day("2021-01-04")  # un lundi


def synthetic_panel(days=400, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(D0, D0 + days)
    cols = ["BTCUSDT", "ETHUSDT", "XUSDT"]
    c = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, (days, 3)), axis=0)), index=idx, columns=cols)
    o = c.shift(1).fillna(c.iloc[0])
    h, lo = np.maximum(o, c) * 1.01, np.minimum(o, c) * 0.99
    v = pd.DataFrame(rng.uniform(1e3, 2e3, (days, 3)), index=idx, columns=cols)
    tb = v * rng.uniform(0.4, 0.6, (days, 3))
    return {"o": o, "h": h, "l": lo, "c": c, "v": v, "taker_buy": tb}


def test_signals_never_look_ahead():
    p = synthetic_panel()
    base = signaux.families(p)
    t = D0 + 300
    future = {k: v.copy() for k, v in p.items()}
    for k in future:
        future[k].loc[t + 1:] = future[k].loc[t + 1:] * 3.0  # l'avenir change du tout au tout
    moved = signaux.families(future)
    for name in signaux.FAMILIES:
        pd.testing.assert_frame_equal(base[name].loc[:t], moved[name].loc[:t], check_names=False)


def test_position_is_sized_at_entry_and_not_rebalanced_daily():
    idx = pd.RangeIndex(0, 5)
    on = pd.DataFrame({"A": [True, True, True, False, False]}, index=idx)
    size = pd.DataFrame(0.1, index=idx, columns=["A"])
    r = pd.DataFrame({"A": [0.10, 0.10, 0.0, 0.0, 0.0]}, index=idx)
    out, W = signaux.simulate(on, size, r, cost=0.01)
    assert out[0] == pytest.approx(0.1 * 0.10 - 0.01 * 0.1)  # entrée : frais sur la mise
    held = 0.1 * 1.1 / (1 + out[0] + 0.01 * 0.1)
    assert W[1, 0] == pytest.approx(held)  # le lendemain : la position a dérivé, aucun achat de complément
    assert out[1] == pytest.approx(held * 0.10)
    assert out[3] < 0 and W[3, 0] == 0.0  # sortie : frais sur la valeur vendue


def test_entries_never_exceed_available_cash():
    idx = pd.RangeIndex(0, 1)
    on = pd.DataFrame(True, index=idx, columns=[f"C{i}" for i in range(12)])
    size = pd.DataFrame(0.1, index=idx, columns=on.columns)
    _, W = signaux.simulate(on, size, pd.DataFrame(0.0, index=idx, columns=on.columns), cost=0.0)
    assert W.sum() == pytest.approx(1.0)


def test_edge_interval_and_reproducibility():
    idx = pd.RangeIndex(0, 200)
    r = pd.DataFrame({"A": 0.002, "B": 0.0}, index=idx)
    signal = pd.DataFrame({"A": True, "B": False}, index=idx)
    member = pd.DataFrame(True, index=idx, columns=["A", "B"])
    e = signaux.edge(signal, member, r)
    assert e["mean_daily_pct"] == pytest.approx(0.1)  # moyenne des actifs en signal (0,2 %) − univers (0,1 %)
    assert e["ci95_daily_pct"][0] > 0
    assert signaux.edge(signal, member, r) == e


def test_stability_definition():
    days = pd.RangeIndex(D0, D0 + 7 * 60)
    r = pd.Series(0.0, index=days)
    week = (days.to_numpy() - D0) // 7
    r[(days.to_numpy() - D0) % 7 == 0] = np.where(week % 3 == 2, -0.01, 0.02)  # deux semaines à +2 %, une à −1 %
    s = signaux.stability(r)
    assert s["weeks"] == 60 and s["negative_weeks"] == 20 and s["positive_weeks"] == 40
    assert s["longest_losing_streak_weeks"] == 1 and s["worst_week_pct"] == pytest.approx(-1.0)
    assert s["geometric_week_pct"] == pytest.approx(100 * ((1.02 ** 40 * 0.99 ** 20) ** (1 / 60) - 1))
    # chaque fenêtre de 52 semaines fait environ +1,0 % par semaine : toutes positives, aucune à +1,30 %
    assert s["rolling52"]["windows"] == 8 and s["rolling52"]["share_positive"] == 1.0
    assert s["rolling52"]["share_at_reference"] == 0.0 and 0.9 < s["rolling52"]["median"] < 1.1


def test_bear_market_days_use_the_all_time_peak():
    days = pd.RangeIndex(D0, D0 + 10)
    btc = pd.Series([100, 100, 60, 60, 60, 100, 100, 100, 100, 100], index=days, dtype=float)
    r = pd.Series(0.01, index=days)
    s = signaux.stability(r, btc)
    assert s["bear_days"] == 3 and s["bear_return_pct"] == pytest.approx(100 * (1.01 ** 3 - 1))


def test_breakout_needs_two_closes_and_exits_on_ten_day_low():
    c = np.array([10, 10, 10, 11, 12, 13, 12, 9, 9, 9], dtype=float)[:, None]
    level = np.full_like(c, 10.5)
    exit_level = np.full_like(c, 9.5)
    on = signaux._breakout_state(c, level, exit_level, np.ones_like(c, dtype=bool))[:, 0]
    assert on.tolist() == [False, False, False, False, True, True, True, False, False, False]


def test_abstention_reasons_and_trade_list():
    idx = pd.RangeIndex(D0, D0 + 5)
    member = pd.DataFrame({"AUSDT": [False, True, True, True, True]}, index=idx)
    signal = pd.DataFrame({"AUSDT": [True, True, False, False, False]}, index=idx)
    regime = pd.DataFrame({"AUSDT": [True, True, False, True, True]}, index=idx)
    p = {"o": pd.DataFrame({"AUSDT": 1.0}, index=idx)}
    counts = signaux.abstention(signal, member, p, regime)["2021"]  # chaque jour juge les données de la veille
    assert counts == {"univers vide": 1, "en position": 2, "régime BTC défavorable": 1, "aucun signal": 1}
    W = np.array([[0.0], [0.1], [0.11], [0.0], [0.0]])
    assert signaux.trades(W, idx, ["AUSDT"]) == [["2021-01-05", "A", "entrée", 0.1], ["2021-01-07", "A", "sortie", 0.11]]


def test_equal_weight_universe_rebalances_on_the_first_of_the_month_only():
    idx = pd.RangeIndex(moteur._day("2021-01-30"), moteur._day("2021-02-02") + 1)
    member = pd.DataFrame(True, index=idx, columns=["A", "B"])
    r = pd.DataFrame({"A": [0.1, 0.0, 0.0, 0.0], "B": [0.0, 0.0, 0.0, 0.0]}, index=idx)
    out = signaux.equal_weight_monthly(member, r, cost=0.01)
    assert out.iloc[0] == pytest.approx(0.05 - 0.01)  # entrée le premier jour
    assert out.iloc[1] == pytest.approx(0.0)  # pas de rééquilibrage le 31 janvier
    assert out.iloc[2] < 0  # 1er février : rééquilibrage payant


def test_timing_edge_measures_a_market_filter_over_time():
    idx = pd.RangeIndex(0, 400)
    on = pd.Series(np.arange(400) % 40 < 20, index=idx)  # filtre vrai 20 jours sur 40
    r = pd.DataFrame({"A": np.where(on.shift(1, fill_value=False), 0.01, -0.01), "B": 0.0}, index=idx)
    member = pd.DataFrame(True, index=idx, columns=["A", "B"])
    e = signaux.timing_edge(on, member, r)
    assert e["diff_daily_pct"] == pytest.approx(1.0)  # moyenne de l'univers : +0,5 % les jours vrais, −0,5 % sinon
    assert e["ci95_daily_pct"][0] > 0


def test_positions_follow_the_previous_close_signal_and_only_quoted_members():
    idx = pd.RangeIndex(500, 530)
    cols = ["AUSDT", "BUSDT"]
    signal = pd.DataFrame(False, index=idx, columns=cols)
    signal.loc[505, "AUSDT"] = True
    signal.loc[510:, "BUSDT"] = True
    member = pd.DataFrame(True, index=idx, columns=cols)
    member.loc[516:, "BUSDT"] = False
    c = pd.DataFrame(100.0, index=idx, columns=cols)
    o = c.copy()
    o.loc[513, "BUSDT"] = np.nan  # aucune cotation ce jour-là : aucune exécution
    on, size = signaux.positions(signal, member, {"o": o, "c": c})
    assert on.index[on["AUSDT"]].tolist() == [506]  # signal à la clôture de 505 → position à l'ouverture de 506
    assert on.index[on["BUSDT"]].tolist() == [511, 512, 514, 515]  # hors cotation (513), puis hors univers (516 →)
    assert (size == 1 / signaux.TOP_N).all().all()


def test_volatility_target_sizing_only_uses_closes_known_the_day_before():
    rng = np.random.default_rng(7)
    idx = pd.RangeIndex(600, 700)
    c = pd.DataFrame({"AUSDT": 100 * np.exp(np.cumsum(rng.normal(0, 0.05, len(idx))))}, index=idx)
    p = {"o": c.copy(), "c": c}
    signal, member = pd.DataFrame(True, index=idx, columns=["AUSDT"]), pd.DataFrame(True, index=idx, columns=["AUSDT"])
    _, size = signaux.positions(signal, member, p, vol_target=0.30)
    shocked = c.copy()
    shocked.loc[650, "AUSDT"] *= 3.0  # choc à la clôture de 650
    _, after = signaux.positions(signal, member, {"o": shocked.copy(), "c": shocked}, vol_target=0.30)
    pd.testing.assert_series_equal(size.loc[:650, "AUSDT"], after.loc[:650, "AUSDT"])  # aucune information future
    assert after.at[651, "AUSDT"] < size.at[651, "AUSDT"] and (size["AUSDT"] <= 1 / signaux.TOP_N).all()


def test_members_need_volume_known_before_the_month_and_sixty_days_of_history():
    days = pd.RangeIndex(moteur._day("2021-01-01"), moteur._day("2021-06-30") + 1)
    cols = [f"S{i}USDT" for i in range(12)]
    v = pd.DataFrame({s: float(100 - i) for i, s in enumerate(cols)}, index=days)
    v.loc[moteur._day("2021-04-01"):, "S11USDT"] = 10_000.0  # volume record à partir du 1er avril seulement
    p = {"v": v, "c": pd.DataFrame(1.0, index=days, columns=cols)}
    m = signaux.members(p)
    assert not m.loc[moteur._day("2021-02-15")].any()  # moins de 60 jours d'historique
    assert m.columns[m.loc[moteur._day("2021-03-15")]].tolist() == cols[:10]  # les 10 plus gros volumes
    assert not m.at[moteur._day("2021-04-15"), "S11USDT"]  # avril : volume d'avril encore inconnu au 1er avril
    may = m.loc[moteur._day("2021-05-15")]
    assert may["S11USDT"] and not may["S9USDT"] and int(may.sum()) == 10
