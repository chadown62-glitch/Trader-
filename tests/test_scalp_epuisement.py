"""Amplitude des ranges (C036) et épuisement de mouvement (C039) : causalité, rangs transversaux, capitulation, tenue."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import epuisement, signaux


def _panel(n=120, cols=("AUSDT", "BUSDT"), seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(0, n)
    c = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n, len(cols))), axis=0)), index=idx, columns=list(cols))
    return {"c": c, "h": c * 1.01, "l": c * 0.99, "o": c.shift(1).fillna(c), "v": pd.DataFrame(1000.0, index=idx, columns=list(cols))}


def test_predictors_only_use_the_past_and_the_target_only_the_future():
    p = _panel()
    amp, vol, ahead = epuisement.predictors(p)
    later = {k: v.copy() for k, v in p.items()}
    for k in ("c", "h", "l"):
        later[k].iloc[80:] *= 2.0  # l'avenir change...
    amp2, vol2, ahead2 = epuisement.predictors(later)
    pd.testing.assert_frame_equal(amp.iloc[:80], amp2.iloc[:80])  # ...pas les prédicteurs passés
    pd.testing.assert_frame_equal(vol.iloc[:80], vol2.iloc[:80])
    assert not np.allclose(ahead.iloc[75:80].to_numpy(), ahead2.iloc[75:80].to_numpy())  # la cible regarde devant
    assert ahead.iloc[-epuisement.AHEAD:].isna().all().all()


def test_daily_rank_ic_needs_enough_members_and_reads_a_perfect_ordering():
    idx = pd.RangeIndex(0, 3)
    cols = [f"S{k}" for k in range(12)]
    x = pd.DataFrame(np.tile(np.arange(12.0), (3, 1)), index=idx, columns=cols)
    member = pd.DataFrame(True, index=idx, columns=cols)
    member.iloc[2, 3:] = False  # troisième jour : 3 membres seulement
    ic = epuisement.daily_rank_ic(x, 2 * x + 1, member)
    assert ic.index.tolist() == [0, 1] and ic.to_numpy() == pytest.approx([1.0, 1.0])
    assert epuisement.daily_rank_ic(x, -x, member).to_numpy() == pytest.approx([-1.0, -1.0])


def test_capitulation_needs_a_large_drop_measured_against_prior_volatility_and_a_volume_spike():
    idx = pd.RangeIndex(0, 60)
    calm = 100 * np.exp(np.cumsum(np.r_[0.0, np.tile([0.01, -0.01], 29), 0.0]))
    c = pd.Series(calm, index=idx)
    c.iloc[45:50] = c.iloc[44] * np.exp(-0.06 * np.arange(1, 6))  # −30 % en 5 jours
    c.iloc[50:] = c.iloc[49]
    v = pd.Series(1000.0, index=idx)
    p = {"c": c.to_frame("AUSDT"), "v": v.to_frame("AUSDT")}
    assert not epuisement.capitulation(p)["AUSDT"].any()  # pas de pic de volume : pas de capitulation
    v.iloc[49] = 2500.0
    p["v"] = v.to_frame("AUSDT")
    event = epuisement.capitulation(p)["AUSDT"]
    assert event[event].index.tolist() == [49]


def test_a_capitulation_is_held_five_days_from_the_next_open():
    idx = pd.RangeIndex(0, 12)
    event = pd.DataFrame(False, index=idx, columns=["AUSDT"])
    event.iloc[3] = True
    sig = epuisement.hold(event)
    one = pd.DataFrame(1.0, index=idx, columns=["AUSDT"])
    on, _ = signaux.positions(sig, pd.DataFrame(True, index=idx, columns=["AUSDT"]), {"o": one, "c": one})
    assert on["AUSDT"][on["AUSDT"]].index.tolist() == [4, 5, 6, 7, 8]
    shifted = epuisement.shifted_frame(sig, 2)
    assert shifted["AUSDT"].sum() == sig["AUSDT"].sum() and shifted["AUSDT"][shifted["AUSDT"]].index.tolist() == [5, 6, 7, 8, 9]


def test_two_sided_p_value_separates_a_signal_from_noise():
    rng = np.random.default_rng(8)
    assert epuisement.two_sided(rng.normal(0.5, 1.0, 600)) < 0.01
    assert epuisement.two_sided(rng.normal(0.0, 1.0, 600)) > 0.05


def test_amplitude_study_compares_both_predictors_on_the_same_days():
    rng = np.random.default_rng(12)
    n, k = 400, 12
    idx = pd.RangeIndex(0, n)
    cols = [f"S{j}USDT" for j in range(k)]
    sigma = np.linspace(0.005, 0.06, k)  # volatilité propre à chaque actif : l'ampleur future se prévoit
    c = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 1, (n, k)) * sigma, axis=0)), index=idx, columns=cols)
    p = {"c": c, "h": c * (1 + sigma / 2), "l": c * (1 - sigma / 2)}
    member = pd.DataFrame(True, index=idx, columns=cols)
    out = epuisement.amplitude_study(p, member, first=0)
    assert out["jours"] == n - epuisement.VOL_DAYS - epuisement.AHEAD  # volatilité connue après 20 rendements
    assert out["correlation_volatilite"] > 0.5 and out["correlation_amplitude"] > 0.5
    assert 0.0 <= out["p_ecart"] <= 1.0 and out["ecart_amplitude_moins_volatilite"] == pytest.approx(
        out["correlation_amplitude"] - out["correlation_volatilite"])
