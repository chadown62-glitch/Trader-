"""OMEGA B — CORTEX, ECHO : règle calibrée, ensemble à poids fixés, erreurs corrélées, information partagée, biais."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import cortex, hive, moteur


def test_momentum_rule_learns_one_rate_per_sign_of_the_28_day_return():
    r28 = np.array([0.1, 0.2, -0.1, -0.3, 0.05])
    y = np.array([1, 1, 0, 1, 0])
    m = cortex.Momentum().fit(r28, y)
    assert m.rates[True] == pytest.approx(2 / 3) and m.rates[False] == pytest.approx(0.5)
    assert m.proba(np.array([0.3, -0.2])).tolist() == pytest.approx([2 / 3, 0.5])


def test_the_ensemble_averages_log_odds_with_fixed_weights():
    p = cortex.ensemble(np.array([0.8]), np.array([0.2]), np.array([0.5]))
    assert p[0] == pytest.approx(0.5)  # logits opposés et nul
    q = cortex.ensemble(np.array([0.6]), np.array([0.6]), np.array([0.6]))
    assert q[0] == pytest.approx(0.6)


def test_echo_measures_separate_shared_errors_from_independent_ones():
    rng = np.random.default_rng(30)
    n = 3000
    y = rng.integers(0, 2, n)
    shared = rng.normal(0, 0.15, n)
    frame = pd.DataFrame({"day": np.repeat(np.arange(n // 10), 10) * 7 + moteur._day("2021-01-03"), "y": y,
                          "p_lr": np.clip(0.5 + shared, 0.01, 0.99), "p_gb": np.clip(0.5 + shared + rng.normal(0, 0.01, n), 0.01, 0.99),
                          "p_mom": np.clip(0.5 + rng.normal(0, 0.15, n), 0.01, 0.99), "p_base": 0.5})
    rc = cortex.residual_correlation(frame)
    assert rc["erreurs_brutes"]["lr–mom"] > 0.8  # l'issue commune domine les erreurs brutes, même entre modèles indépendants
    assert rc["ecarts_a_la_reference"]["lr–gb"] > 0.9 and abs(rc["ecarts_a_la_reference"]["lr–mom"]) < 0.2
    informative = frame.assign(p_lr=np.clip(0.5 + 0.2 * (frame["y"] - 0.5) + shared, 0.01, 0.99))
    mc = cortex.marginal_contribution(informative)
    assert mc["lr"]["correlation_avec_l_erreur_de_reference"] > 0.3 and abs(mc["mom"]["partielle_sachant_lr"]) < 0.1
    info = cortex.shared_information(frame)
    assert info["lr–gb"] > 0.9 and info["lr–mom"] < 0.05
    b = cortex.biases(frame)
    assert min(b["par_annee"]) == 2021 and len(b["par_annee"]) >= 2 and "lr–gb" in b["correlation_des_biais"]


def test_judge_year_never_learns_from_the_judged_year():
    rng = np.random.default_rng(31)
    rows = []
    for d in range(moteur._day("2019-01-06"), moteur._day("2021-12-31"), 7):
        for a in ("AUSDT", "BUSDT", "CUSDT"):
            rows.append({"day": d, "asset": a, **{f: rng.normal() for f in hive.FEATURES}, "ret": rng.normal(), "y": int(rng.random() < 0.5)})
    frame = pd.DataFrame(rows)
    train, test = hive.split(frame, 2021)
    out = cortex.judge_year(train, test)
    assert len(out) == len(test) and set(hive.year_of(out["day"])) == {2021}
    for m in cortex.MODELS:
        assert out[f"p_{m}"].between(0, 1).all()
    shuffled = train.assign(y=train["y"].sample(frac=1, random_state=0).to_numpy())
    again = cortex.judge_year(train, test.assign(y=1 - test["y"]))  # l'issue jugée ne change aucune probabilité
    pd.testing.assert_series_equal(out["p_ens"], again["p_ens"])
    assert not np.allclose(cortex.judge_year(shuffled, test)["p_lr"], out["p_lr"])  # l'apprentissage, lui, compte
