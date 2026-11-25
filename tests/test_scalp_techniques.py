"""Familles techniques et de régime (C022, C023, C025, C031, C034, C038) : définitions et absence de regard en avant."""

import numpy as np
import pandas as pd

from sentinelle.scalp import techniques


def panel(n=200, seed=0, cols=("BTCUSDT", "AUSDT", "BUSDT")):
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(1000, 1000 + n)
    c = pd.DataFrame({k: 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n))) for k in cols}, index=idx)
    spread = pd.DataFrame(np.abs(rng.normal(0, 0.01, c.shape)), index=idx, columns=c.columns)
    return {"o": c.shift(1).fillna(c.iloc[0]), "h": c * (1 + spread), "l": c * (1 - spread), "c": c}


def test_adx_is_high_in_a_steady_trend_and_low_in_a_round_trip():
    idx = pd.RangeIndex(0, 80)
    up = pd.DataFrame({"X": 100 * 1.01 ** np.arange(80)}, index=idx)
    zz = pd.DataFrame({"X": 100 + np.where(np.arange(80) % 2 == 0, 0.0, 2.0)}, index=idx)
    assert techniques.adx(up * 1.005, up * 0.995, up).iloc[-1, 0] > 50
    assert techniques.adx(zz + 1, zz - 1, zz).iloc[-1, 0] < 25


def test_rsi_bounds_and_breadth_share():
    idx = pd.RangeIndex(0, 40)
    up = pd.DataFrame({"X": np.arange(40, dtype=float) + 1}, index=idx)
    assert techniques.rsi(up).iloc[-1, 0] == 100.0
    c = pd.DataFrame({"A": np.linspace(1, 2, 40), "B": np.linspace(2, 1, 40), "C": np.linspace(1, 3, 40)}, index=idx)
    member = pd.DataFrame(True, index=idx, columns=c.columns)
    assert techniques.breadth(c, member).iloc[-1] == 2 / 3
    member["C"] = False
    assert techniques.breadth(c, member).iloc[-1] == 0.5


def test_a_pivot_high_is_only_known_k_days_after_it():
    idx = pd.RangeIndex(0, 30)
    h = pd.DataFrame({"X": 10.0 - 0.01 * np.arange(30)}, index=idx)  # série décroissante : aucun sommet ex aequo
    h.loc[10, "X"] = 15.0
    piv = techniques.confirmed_pivot_high(h)
    assert piv.loc[:12, "X"].isna().all() and piv.loc[13, "X"] == 15.0  # confirmé 3 jours plus tard


def test_a_retest_after_a_breakout_keeps_the_signal_for_ten_days():
    idx = pd.RangeIndex(0, 60)
    c = pd.DataFrame({"X": 100.0}, index=idx)
    c.loc[30:, "X"] = 105.0  # cassure au jour 30
    h, lo = c * 1.001, c * 0.999
    lo.loc[32, "X"] = 100.5  # retour à 0,5 % du niveau cassé, clôture au-dessus
    sig = techniques.retest(h, lo, c)["X"]
    assert not sig.loc[:31].any() and sig.loc[32:41].all() and not sig.loc[42:].any()


def test_every_family_ignores_data_after_the_day_it_is_computed():
    p = panel()
    member = pd.DataFrame(True, index=p["c"].index, columns=p["c"].columns)
    base = techniques.families(p, member)
    cut = 1150
    q = {k: v.copy() for k, v in p.items()}
    for k in q:
        q[k].loc[cut + 1:] *= 3.0  # l'avenir change brutalement
    after = techniques.families(q, member)
    for name in base:
        pd.testing.assert_frame_equal(base[name].loc[:cut], after[name].loc[:cut], obj=name)
