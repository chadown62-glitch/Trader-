"""Correction pour essais multiples : Holm, Benjamini-Hochberg, p-valeur par blocs, ratio de Sharpe dégonflé."""

import math

import numpy as np
import pytest

from sentinelle.scalp import essais, moteur


def test_normal_quantile_and_cdf():
    assert essais.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert essais.norm_ppf(0.001) == pytest.approx(-3.090232, abs=1e-5)
    assert essais.norm_cdf(essais.norm_ppf(0.9)) == pytest.approx(0.9, abs=1e-7)


def test_holm_and_benjamini_hochberg_on_a_known_example():
    p = [0.01, 0.04, 0.03, 0.005]
    assert essais.holm(p) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert essais.benjamini_hochberg(p) == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_block_bootstrap_pvalue_separates_signal_from_noise():
    rng = np.random.default_rng(3)
    noise = rng.normal(0.0, 0.01, 1000)
    assert essais.bootstrap_pvalue(noise + 0.003) < 0.01
    assert 0.05 < essais.bootstrap_pvalue(noise - noise.mean()) < 0.95
    assert essais.bootstrap_pvalue(noise + 0.003) == essais.bootstrap_pvalue(noise + 0.003)  # graine fixe


def test_expected_maximum_grows_with_the_number_of_trials():
    assert essais.expected_max_sr(1.0, 1) == 0.0
    assert 3.2 < essais.expected_max_sr(1.0, 1000) < 3.3
    assert essais.expected_max_sr(0.01, 10) < essais.expected_max_sr(0.01, 100)


def test_deflated_sharpe_penalizes_many_trials():
    s = {"sr_week": 0.15, "sr_annual": 0.15 * math.sqrt(52), "skew": 0.0, "kurtosis": 3.0, "weeks": 400}
    alone = essais.deflated_sharpe(s, variance=0.004, n=1)
    many = essais.deflated_sharpe(s, variance=0.004, n=3888)
    assert alone["dsr"] > 0.99 and alone["probable"]
    assert many["dsr"] < alone["dsr"] and many["sr0_week"] > 0.2 and not many["probable"]


def test_weekly_returns_follow_monday_to_sunday_weeks():
    days = np.arange(moteur._day("2024-01-01"), moteur._day("2024-01-14") + 1)  # deux semaines entières
    r = np.zeros(len(days))
    r[0], r[7] = 0.10, -0.10
    w = essais.weekly(r, days)
    assert w.tolist() == pytest.approx([0.10, -0.10])
    s = essais.sharpe(np.array([0.01, 0.02, 0.03, 0.04]))
    assert s["sr_week"] == pytest.approx(0.025 / np.std([0.01, 0.02, 0.03, 0.04], ddof=1))
