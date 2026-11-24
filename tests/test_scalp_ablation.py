"""Ablations et stress de coûts : conditions identiques exigées, intervalles, correction de Holm, monotonie des coûts."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import ablation, moteur

D0 = moteur._day("2021-01-04")  # un lundi


def series(values, start=D0):
    return pd.Series(values, index=pd.RangeIndex(start, start + len(values)), dtype=float)


def test_comparison_is_refused_unless_both_series_cover_the_same_days():
    a = series(np.zeros(70))
    with pytest.raises(ablation.AblationError):
        ablation.compare(a, series(np.zeros(70), start=D0 + 1), label="x", costs="0")
    with pytest.raises(ablation.AblationError):
        ablation.compare(a, series([np.nan] + [0.0] * 69), label="x", costs="0")
    with pytest.raises(ablation.AblationError):
        ablation.compare(series([]), series([]), label="x", costs="0")


def test_a_real_improvement_gets_a_positive_interval_and_noise_does_not():
    rng = np.random.default_rng(5)
    base = rng.normal(0.0, 0.01, 7 * 200)
    better = series(base + 0.001)  # +0,1 % par jour : environ +0,7 % par semaine
    out = ablation.compare(series(base), better, label="mieux", costs="0")
    assert out["ecart_hebdo_moyen_pct"] == pytest.approx(100 * ((1.001) ** 7 - 1), rel=0.05)
    assert out["ic95_pct"][0] > 0 and out["p_unilaterale"] < 0.01
    noise = series(base + rng.normal(0.0, 0.002, len(base)))
    out = ablation.compare(series(base), noise, label="bruit", costs="0")
    assert out["ic95_pct"][0] < 0 < out["ic95_pct"][1]


def test_holm_is_applied_across_a_batch_of_ablations():
    rows = [{"p_unilaterale": p} for p in (0.01, 0.04, 0.03, 0.005)]
    out = ablation.adjust(rows)
    assert [r["p_holm"] for r in out] == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert [r["significative_holm_5pct"] for r in out] == [True, False, False, True]


def test_stress_levels_cost_more_and_are_judged_against_cash_and_a_reference():
    gross = np.full(7 * 60, 0.002)
    turnover = np.zeros(len(gross))
    turnover[::7] = 1.0  # un aller simple de tout le capital chaque semaine

    def run(cost):
        return series(gross - cost * turnover)

    out = ablation.stress(run, base_cost=0.002, reference=series(np.full(len(gross), 0.001)))
    levels = list(out)
    assert levels == ["× 1", "× 2", "× 3", "× 3 + écart 0,10 %"]
    returns = [out[k]["geometric_week_pct"] for k in levels]
    assert returns == sorted(returns, reverse=True)  # plus de coûts, moins de rendement
    assert out["× 1"]["bat_cash"] and out["× 1"]["bat_reference"]
    assert not out["× 3 + écart 0,10 %"]["bat_reference"]
