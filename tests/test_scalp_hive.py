"""OMEGA A — HIVE, GENOME, BORDER : causalité, découpage, mélange, actifs exclus, contrôle synthétique, statistiques."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import hive, moteur


def _panel(n=400, cols=("AUSDT", "BTCUSDT"), seed=20):
    rng = np.random.default_rng(seed)
    idx = pd.RangeIndex(moteur._day("2020-01-01"), moteur._day("2020-01-01") + n)
    c = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n, len(cols))), axis=0)), index=idx, columns=list(cols))
    v = pd.DataFrame(rng.uniform(900, 1100, (n, len(cols))), index=idx, columns=list(cols))
    return {"c": c, "h": c * 1.01, "v": v, "taker_buy": v * 0.5}


def test_features_only_use_the_past():
    p = _panel()
    f = hive.features(p)
    later = {k: v.copy() for k, v in p.items()}
    for k in later:
        later[k].iloc[300:] *= 1.7  # l'avenir change...
    g = hive.features(later)
    for name in hive.FEATURES:
        pd.testing.assert_frame_equal(f[name].iloc[:300], g[name].iloc[:300], check_names=False)  # ...pas le passé


def test_split_keeps_an_embargo_before_the_judged_year():
    days = np.arange(moteur._day("2021-11-01"), moteur._day("2022-03-01"), 7)
    frame = pd.DataFrame({"day": days, "asset": "AUSDT", **{f: 0.0 for f in hive.FEATURES}, "ret": 0.0, "y": 0})
    train, test = hive.split(frame, 2022)
    start = moteur._day("2022-01-01")
    assert (train["day"] + hive.HORIZON < start).all() and (train["day"] + hive.EMBARGO <= start - 1).all()
    assert set(hive.year_of(test["day"])) == {2022} and len(train) + len(test) < len(frame) + 1


def test_hybrid_weight_moves_from_the_collective_to_the_individual():
    pi, pp = np.array([0.8]), np.array([0.4])
    assert hive.hybrid(pi, pp, np.array([0]))[0] == pytest.approx(0.4)
    assert hive.hybrid(pi, pp, np.array([10**9]))[0] == pytest.approx(0.8, abs=1e-6)
    mid = hive.hybrid(pi, pp, np.array([hive.SHRINK]))[0]
    assert mid == pytest.approx(1 / (1 + np.exp(-(0.5 * hive.logit(0.8) + 0.5 * hive.logit(0.4)))))


def test_the_synthetic_control_finds_the_gain_and_the_negative_transfer():
    out = hive.synthetic(seed=1, assets=6, n_train=60, n_test=300)
    coh, div = out["coherente"], out["divergente"]
    assert coh["collectif"] < coh["individuel"]  # comportements cohérents : la mutualisation aide
    assert div["individuel"] < div["collectif"]  # comportements divergents : transfert négatif
    assert coh["hybride"] <= max(coh.values()) and div["hybride"] <= max(div.values())


def test_unseen_models_never_learn_from_the_judged_group(monkeypatch):
    rng = np.random.default_rng(21)
    assets = [f"S{k}USDT" for k in range(8)]
    rows = []
    for d in range(moteur._day("2019-01-06"), moteur._day("2021-01-01"), 7):
        for a in assets:
            rows.append({"day": d, "asset": a, **{f: rng.normal() for f in hive.FEATURES}, "ret": rng.normal(), "y": int(rng.random() < 0.5)})
    frame = pd.DataFrame(rows)
    seen = []
    real_fit = hive.Logit.fit

    def spy(self, X, y):
        seen.append(len(X))
        return real_fit(self, X, y)

    monkeypatch.setattr(hive.Logit, "fit", spy)
    folds = hive.folds_of(assets)
    train, test = hive.split(frame, 2020)
    out = hive.judge_year(train, test, folds)
    per_group = {g: int((train["asset"].map(folds) == g).sum()) for g in range(hive.FOLDS)}
    assert seen[0] == len(train)  # le collectif voit tout
    assert sorted(seen[-hive.FOLDS:]) == sorted(len(train) - n for n in per_group.values())  # chaque modèle exclut son groupe
    assert out["p_unseen"].notna().all() and not np.allclose(out["p_unseen"], out["p_pool"])


def test_week_bootstrap_and_negative_transfer_statistics():
    weeks = np.repeat(np.arange(100), 10)
    assert hive.week_bootstrap(np.zeros(1000), weeks)["p_bilaterale"] == 1.0
    rng = np.random.default_rng(22)
    strong = hive.week_bootstrap(rng.normal(-0.05, 0.1, 1000), weeks)
    assert strong["ecart"] < 0 and strong["p_bilaterale"] < 0.01 and strong["ic95"][1] < 0
    assets = [f"S{k}USDT" for k in range(12)]
    div = pd.Series(np.linspace(0, 1, 12), index=assets)
    rows = []
    for k, a in enumerate(assets):
        for _w in range(30):  # le collectif est d'autant plus mauvais que l'actif diverge
            rows.append({"asset": a, "y": 1, "p_ind": 0.6, "p_pool": 0.6 - 0.04 * k})
    out = hive.negative_transfer(pd.DataFrame(rows), div)
    assert out["spearman_divergence"] > 0.9 and out["p_bilaterale"] < 0.01 and out["actifs_en_transfert_negatif"] == 11
