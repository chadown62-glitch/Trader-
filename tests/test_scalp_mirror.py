"""OMEGA TRUTH / MIRROR : monde simulé, positions causales, sorties, tests et règles de validation."""

import math

import numpy as np
import pytest

from sentinelle.scalp import mirror


def test_the_null_world_has_no_drift_and_the_positive_world_rewards_20_day_momentum():
    rng = np.random.default_rng(1)
    r = mirror.simulate(rng, n_assets=2, days=20_000)
    assert abs(r.mean(axis=0)).max() < 3 * r.std(axis=0).max() / math.sqrt(20_000)
    pos = mirror.base_signals(np.cumsum(r, axis=0))["momentum_20"].astype(float)
    null_edge = (pos[:-1, 0] * r[1:, 0]).mean()
    pr = mirror.simulate(np.random.default_rng(2), n_assets=2, days=20_000, effect_sharpe=1.5)
    pp = mirror.base_signals(np.cumsum(pr, axis=0))["momentum_20"].astype(float)
    edge = pp[:-1, 0] * pr[1:, 0]
    sharpe = edge.mean() / edge.std() * math.sqrt(mirror.YEAR)
    assert sharpe > 0.8 and abs(null_edge) < edge.mean()  # l'avantage injecté est retrouvé ; le second actif reste nul
    assert abs((pp[:-1, 1] * pr[1:, 1]).mean()) < edge.mean()


def test_positions_never_use_the_future():
    rng = np.random.default_rng(3)
    r = mirror.simulate(rng, n_assets=2, days=600)
    _, pos = mirror.positions(r)
    changed = r.copy()
    changed[400:] = -changed[400:] * 3  # tout l'avenir après le jour 400 est bouleversé
    _, pos2 = mirror.positions(changed)
    np.testing.assert_array_equal(pos[:400], pos2[:400])
    net = mirror.net_returns(r, pos)
    assert net.shape == pos.shape and np.all(net[0] == 0)  # aucune position ne rapporte le jour de sa décision


def test_stops_exit_and_wait_for_the_signal_to_reset():
    logp = np.log(np.array([[100], [100], [95], [89], [92], [93], [93], [94]], dtype=float))
    signal = np.array([[0], [1], [1], [1], [1], [0], [1], [1]], dtype=bool)
    fixed = mirror.with_exit(signal, logp, "stop_fixe")[:, 0].tolist()
    assert fixed == [False, True, True, False, False, False, True, True]  # −11 % au jour 3 : sortie, puis attente du nouveau signal
    trail = mirror.with_exit(signal, np.log(np.array([[100], [100], [120], [101], [110], [110], [111], [112]], dtype=float)), "stop_suiveur")
    assert trail[:, 0].tolist() == [False, True, True, False, False, False, True, True]  # −15,8 % depuis le plus haut


def test_newey_west_and_costs_behave():
    rng = np.random.default_rng(4)
    assert mirror.newey_west_p(rng.normal(0.01, 0.01, 500)) < 1e-6
    assert 0.05 < mirror.newey_west_p(rng.normal(0, 0.01, 500)) < 0.95
    r = np.zeros((5, 1))
    pos = np.array([[[0.0]], [[1.0]], [[1.0]], [[0.0]], [[0.0]]]).reshape(5, 1, 1)
    net = mirror.net_returns(r, pos)
    assert net[2, 0, 0] == pytest.approx(-mirror.COST["crypto"]) and net[4, 0, 0] == pytest.approx(-mirror.COST["crypto"])


def test_validation_rules_return_counts_per_asset_and_find_a_strong_edge():
    rng = np.random.default_rng(5)
    out = mirror.validate(mirror.simulate(rng, effect_sharpe=3.0))
    assert out["variantes"] == 45 and set(out["validations"]) == set(mirror.RULES)
    v = out["validations"]
    assert all(len(v[k]) == len(mirror.ASSETS) for k in mirror.RULES)
    assert all(0 <= x <= mirror.TOP for k in ("V2", "V3", "V4", "V5", "V6") for x in v[k])
    assert v["V4"][0] >= 1 and v["V6"][0] <= v["V4"][0] <= v["V3"][0]  # un avantage fort survit ; les règles sont emboîtées
    s = mirror.summarize([out, out], effect=True)
    assert s["V4"]["puissance_actif_avec_avantage"] == 1.0


def test_crypto_correlation_and_common_effects_are_simulated_as_declared():
    r = mirror.simulate(np.random.default_rng(6), days=20_000, crypto_corr=0.6)
    c = np.corrcoef(r.T)
    assert 0.45 < c[0, 1] < 0.75 and abs(c[0, 5]) < 0.05  # cryptos corrélées entre elles, pas avec l'argent
    rc = mirror.simulate(np.random.default_rng(7), n_assets=3, days=20_000, effect_sharpe=1.5, effect_assets="tous")
    for a in range(3):
        pos = mirror.base_signals(np.cumsum(rc, axis=0))["momentum_20"][:, a].astype(float)
        assert (pos[:-1] * rc[1:, a]).mean() > 0


def test_pooled_validation_counts_one_strategy_per_asset():
    out = mirror.validate_pooled(mirror.simulate(np.random.default_rng(8), effect_sharpe=3.0, effect_assets="tous"))
    assert out["mutualise_variantes"] >= 1 and out["mutualise_strategies_actif"] == out["mutualise_variantes"] * len(mirror.ASSETS)
    assert len(out["par_actif_V4"]) == len(mirror.ASSETS)


def test_the_martingale_world_gives_long_positions_no_arithmetic_drift():
    lr = mirror.simulate(np.random.default_rng(9), n_assets=3, days=50_000)  # v1 : espérance nulle en logarithme
    mr = mirror.simulate(np.random.default_rng(9), n_assets=3, days=50_000, martingale=True)
    drift_v1, drift_v3 = (np.exp(lr) - 1).mean(axis=0), (np.exp(mr) - 1).mean(axis=0)
    assert (drift_v1 > 0).all()  # la dérive σ²/2 que v3 corrige
    se = (np.exp(mr) - 1).std(axis=0) / np.sqrt(50_000)
    assert (np.abs(drift_v3) < 4 * se).all() and (mr > np.log1p(mirror.FLOOR) - 1e-12).all()
