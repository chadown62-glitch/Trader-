"""Risques extrêmes, plafond par groupe corrélé et stops (C079, C074, C067) : sélection, causalité, écarts de cotation."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import risques


def test_worst_weeks_do_not_overlap():
    close = pd.Series(100.0, index=pd.RangeIndex(0, 60))
    close.loc[10:] = 70.0  # chute au jour 10
    close.loc[40:] = 50.0  # seconde chute au jour 40
    w = risques.worst_windows(close, n=2, days=7)
    assert len(w) == 2 and all(b - a == 7 for a, b in w)
    assert not (w[0][0] <= w[1][1] and w[1][0] <= w[0][1])  # sans chevauchement
    assert {any(a < 10 <= b for a, b in w), any(a < 40 <= b for a, b in w)} == {True}
    assert risques.window_return(pd.Series([0.1, -0.5, 0.2], index=[1, 2, 3]), 1, 3) == pytest.approx(0.5 * 1.2 - 1)


def test_the_cluster_cap_keeps_three_correlated_assets_and_every_independent_one():
    rng = np.random.default_rng(4)
    idx = pd.RangeIndex(0, 120)
    common = rng.normal(0, 0.03, len(idx))
    cols = [f"C{i}USDT" for i in range(5)] + ["IND1USDT", "IND2USDT"]
    rets = {c: common + rng.normal(0, 0.003, len(idx)) for c in cols[:5]}
    rets.update({c: rng.normal(0, 0.03, len(idx)) for c in cols[5:]})
    c = pd.DataFrame({k: 100 * np.exp(np.cumsum(v)) for k, v in rets.items()}, index=idx)
    signal = pd.DataFrame(True, index=idx, columns=cols)
    member = pd.DataFrame(True, index=idx, columns=cols)
    score = pd.DataFrame(np.tile(np.arange(len(cols), 0, -1), (len(idx), 1)), index=idx, columns=cols, dtype=float)
    capped, k = risques.cluster_cap(signal, member, c, score)
    last = capped.iloc[-1]
    assert last[cols[:3]].all() and not last[cols[3:5]].any() and last[cols[5:]].all()  # 3 du groupe + les 2 indépendants
    assert k.iloc[-1] == 5 and capped.iloc[0].all()  # sans historique, aucune corrélation connue : rien n'est retiré


def test_the_cluster_cap_only_uses_returns_known_the_day_before():
    rng = np.random.default_rng(5)
    idx = pd.RangeIndex(0, 90)
    c = pd.DataFrame({f"A{i}USDT": 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(idx)))) for i in range(4)}, index=idx)
    signal = member = pd.DataFrame(True, index=idx, columns=c.columns)
    score = pd.DataFrame(1.0, index=idx, columns=c.columns)
    base, _ = risques.cluster_cap(signal, member, c, score)
    shocked = c.copy()
    shocked.loc[70:, :] *= np.exp(np.cumsum(np.full((20, 4), 0.2), axis=0))  # mouvement commun à partir du jour 70
    after, _ = risques.cluster_cap(signal, member, shocked, score)
    pd.testing.assert_frame_equal(base.loc[:70], after.loc[:70])  # le jour 70 ne voit pas sa propre clôture


def test_witness_keeps_the_same_number_of_positions_by_score_only():
    idx = pd.RangeIndex(0, 2)
    signal = member = pd.DataFrame(True, index=idx, columns=["A", "B", "C"])
    score = pd.DataFrame([[3.0, 2.0, 1.0], [1.0, 3.0, 2.0]], index=idx, columns=["A", "B", "C"])
    w = risques.top_k(signal, member, score, pd.Series([2, 1], index=idx))
    assert w.loc[0].tolist() == [True, True, False] and w.loc[1].tolist() == [False, True, False]


def test_stop_gaps_measure_the_extra_loss_beyond_the_planned_stop():
    alerts = pd.DataFrame({"outcome": ["stop", "stop", "objectif"], "entry": [100.0, 100.0, 100.0], "stop": [98.0, 98.0, 98.0],
                           "exit": [98.0, 97.0, 104.0], "r": [-1.1, -1.6, 1.9]})
    g = risques.stop_gaps(alerts)
    assert g["stops"] == 2 and g["part_avec_ecart"] == 0.5 and g["perte_supplementaire_moyenne_r"] == pytest.approx(0.5)
