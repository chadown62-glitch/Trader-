"""Calibration, abstention par incertitude et dérive (C082, C083, C084) : mesures et causalité de l'apprentissage."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import calibration
from sentinelle.scalp.data import month_bounds


def test_brier_and_calibration_error_on_known_cases():
    y = np.array([1, 0, 1, 0])
    assert calibration.brier([1, 0, 1, 0], y) == 0.0 and calibration.brier([0.5] * 4, y) == pytest.approx(0.25)
    e, table = calibration.ece([0.9] * 10, [1] * 9 + [0])
    assert e == pytest.approx(0.0) and table[0]["n"] == 10
    e, _ = calibration.ece([0.9] * 10, [0] * 10)  # sûr de lui et toujours faux
    assert e == pytest.approx(0.9)


def test_population_stability_is_near_zero_without_drift_and_large_with_a_shift():
    rng = np.random.default_rng(0)
    a = rng.normal(size=5000)
    assert calibration.psi(a, rng.normal(size=5000)) < 0.02
    assert calibration.psi(a, rng.normal(loc=1.5, size=5000)) > calibration.PSI_ALERT
    assert np.isnan(calibration.psi(a, np.array([np.nan])))


def test_the_month_is_judged_only_by_alerts_settled_a_day_before_it_starts():
    start, end = month_bounds("2025-03")
    day = 86_400_000
    cands = pd.DataFrame({"decided_at": [start - 10 * day, start - 2 * day, start - 2 * day, start + day, end + day],
                          "exit_ms": [start - 9 * day, start - day - 1, start - day + 5, start + 2 * day, end + 2 * day]})
    train, test = calibration.training_set(cands, "2025-03")
    assert train.index.tolist() == [0, 1]  # la 3e alerte se dénoue moins d'un jour avant le mois : exclue (embargo)
    assert test.index.tolist() == [3]


def test_uncertainty_abstention_and_skill_are_computed_on_taken_trades_and_against_the_learned_rate():
    f = pd.DataFrame({"month": ["m"] * 4, "take": [True, True, True, False], "p_trees": [0.6, 0.9, 0.5, 0.5],
                      "p_lin": [0.55, 0.3, 0.45, 0.5], "r": [1.0, -1.0, 0.5, 3.0], "y": [1, 0, 1, 1],
                      "p": [0.6, 0.6, 0.5, 0.5], "base_rate": [0.5] * 4})
    # gardés : trades 0 et 2 (désaccord ≤ 0,20) → R moyen 0,75 ; tous les trades pris : 0,1667
    assert calibration.abstention_gain(f) == pytest.approx(0.75 - (1.0 - 1.0 + 0.5) / 3)
    assert calibration.skill(f) == pytest.approx(1 - calibration.brier(f["p"], f["y"]) / 0.25)


def test_walk_forward_judges_each_month_with_a_model_trained_on_its_past_only():
    from sentinelle.scalp.features import FEATURES
    rng = np.random.default_rng(1)
    rows = []
    for m in ("2025-01", "2025-02", "2025-03"):
        start, end = month_bounds(m)
        for k in range(400):
            t = int(start + (k + 0.5) * (end - start) / 400)
            rows.append({"decided_at": t, "exit_ms": t + 3_600_000, "setup": "pullback", "thesis": "essai", "r": float(rng.normal(-0.1, 1.0)),
                         "cost_r": 0.1, "risk_pct": 0.01, **{f: float(rng.normal()) for f in FEATURES}})
    cands = pd.DataFrame(rows)
    frame, drift = calibration.walk_forward(cands, (), ["2025-01", "2025-02", "2025-03"])
    assert sorted(frame["month"].unique()) == ["2025-02", "2025-03"]  # janvier : aucun passé, aucun jugement
    assert (frame.loc[frame["month"] == "2025-02", "n_train"] < 400).all()  # février n'apprend que sur janvier (embargo d'un jour)
    assert frame["p"].between(0, 1).all() and [d["month"] for d in drift] == ["2025-02", "2025-03"]
