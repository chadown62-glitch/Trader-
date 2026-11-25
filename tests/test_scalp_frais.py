"""Frais datés (C061) : barème officiel par date et par paire, positions rejouées identiques au simulateur."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import frais, moteur, signaux


def days(*dates):
    return [moteur._day(d) for d in dates]


def test_the_dated_schedule_follows_the_official_announcements():
    idx = days("2018-07-20", "2018-07-21", "2022-07-08", "2022-07-09", "2023-03-21", "2023-03-22", "2019-08-01", "2020-08-01")
    t = frais.fee_table(idx, ["BTCUSDT", "ETHUSDT"], "annonces")
    assert t["ETHUSDT"].tolist() == pytest.approx([0.0005, 0.00075, 0.00075, 0.00075, 0.00075, 0.00075, 0.00075, 0.00075])
    assert t["BTCUSDT"].tolist() == pytest.approx([0.0005, 0.00075, 0.00075, 0.0, 0.0, 0.00075, 0.00075, 0.00075])  # BTC sans frais
    white = frais.fee_table(idx, ["ETHUSDT"], "livre blanc")["ETHUSDT"]
    assert white.iloc[-2:].tolist() == pytest.approx([0.000875, 0.0009325])  # remise décroissante, non attestée
    assert frais.fee_table(idx, ["ETHUSDT"], "sans BNB")["ETHUSDT"].tolist() == pytest.approx([0.001] * len(idx))
    assert frais.fee_table(idx, ["ETHUSDT"], "constant")["ETHUSDT"].tolist() == pytest.approx([0.00075] * len(idx))
    with pytest.raises(ValueError):
        frais.fee_table(idx, ["ETHUSDT"], "inconnue")


def test_replayed_positions_with_a_constant_cost_match_the_simulator_exactly():
    rng = np.random.default_rng(3)
    idx = pd.RangeIndex(1000, 1300)
    cols = ["AUSDT", "BUSDT", "CUSDT"]
    r = pd.DataFrame(rng.normal(0, 0.03, (len(idx), 3)), index=idx, columns=cols)
    on = pd.DataFrame(rng.random((len(idx), 3)) > 0.5, index=idx, columns=cols)
    size = pd.DataFrame(0.3, index=idx, columns=cols)
    ret, W = signaux.simulate(on, size, r, signaux.COST)
    again = frais.replay(W, r, pd.DataFrame(signaux.COST, index=idx, columns=cols))
    np.testing.assert_allclose(again.to_numpy(), ret.to_numpy(), rtol=0, atol=1e-15)
    dearer = frais.replay(W, r, pd.DataFrame(signaux.COST, index=idx, columns=cols).assign(AUSDT=0.01))
    traded = np.abs(np.diff(np.vstack([np.zeros(3), W]), axis=0))[:, 0] > 0
    assert (dearer.to_numpy() <= again.to_numpy() + 1e-15).all() and (dearer.to_numpy() < again.to_numpy())[traded].any()
