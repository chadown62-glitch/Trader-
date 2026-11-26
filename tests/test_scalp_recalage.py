"""C082 — recalage logistique (Platt) des probabilités de l'IA principale : définitions et causalité."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import calibration, recalage
from sentinelle.scalp.data import month_bounds


def test_platt_tempers_overconfident_scores_and_improves_brier():
    rng = np.random.default_rng(80)
    truth = rng.uniform(0.2, 0.4, 20000)
    y = (rng.random(20000) < truth).astype(int)
    overconfident = 1 / (1 + np.exp(-6 * (truth - 0.3)))  # même classement, probabilités poussées aux extrêmes
    ab = recalage.platt_fit(overconfident[:10000], y[:10000])
    p = recalage.platt_apply(overconfident[10000:], ab)
    assert calibration.brier(p, y[10000:]) < calibration.brier(overconfident[10000:], y[10000:])
    assert 0.15 < p.min() and p.max() < 0.5  # ramenées dans la plage des fréquences réelles
    assert recalage.platt_fit([0.3, 0.4], [1, 1])[1] == 0.0  # une seule classe : constante, sans pente


def test_logit_is_clipped_and_platt_inverts_it():
    assert np.isfinite(recalage.logit([0.0, 1.0])).all()
    assert recalage.platt_apply([0.25, 0.7], (0.0, 1.0)) == pytest.approx([0.25, 0.7])


def test_the_calibration_slice_is_the_most_recent_fifth_of_the_training_set():
    train = pd.DataFrame({"decided_at": [5, 1, 4, 2, 3, 9, 8, 7, 6, 10], "r": range(10)})
    assert recalage.calibration_slice(train)["decided_at"].tolist() == [9, 10]


def test_walk_forward_recalibrates_each_month_on_its_past_only():
    from sentinelle.scalp.features import FEATURES
    rng = np.random.default_rng(81)
    rows = []
    for m in ("2025-01", "2025-02", "2025-03"):
        start, end = month_bounds(m)
        for k in range(400):
            t = int(start + (k + 0.5) * (end - start) / 400)
            rows.append({"decided_at": t, "exit_ms": t + 3_600_000, "setup": "pullback", "thesis": "essai", "r": float(rng.normal(-0.1, 1.0)),
                         "cost_r": 0.1, "risk_pct": 0.01, **{f: float(rng.normal()) for f in FEATURES}})
    cands = pd.DataFrame(rows)
    frame = recalage.walk_forward(cands, (), ["2025-01", "2025-02", "2025-03"])
    assert sorted(frame["month"].unique()) == ["2025-02", "2025-03"]  # janvier : aucun passé
    assert frame[["p_iso", "p_platt", "p_melange"]].stack().between(0, 1).all()
    flipped = cands.copy()
    in_march = flipped["decided_at"] >= month_bounds("2025-03")[0]
    flipped.loc[in_march, "r"] = -flipped.loc[in_march, "r"]  # l'issue des alertes jugées ne change aucune probabilité
    again = recalage.walk_forward(flipped, (), ["2025-03"])
    np.testing.assert_allclose(again["p_platt"].to_numpy(), frame.loc[frame["month"] == "2025-03", "p_platt"].to_numpy())
