"""OMEGA D — PULSE, QUARANTINE : détecteurs, scénarios de rupture, comptage des délais et fausses alertes."""

import numpy as np
import pytest

from sentinelle.scalp import pulse


def test_cusum_and_page_hinkley_stay_silent_without_change_and_react_to_a_step():
    flat = np.zeros(200)
    assert pulse.cusum(flat, 1.0) is None and pulse.page_hinkley(flat, 0.0, 1.0) is None
    step = np.r_[np.zeros(10), np.full(50, 2.0)]
    a, b = pulse.cusum(step, 1.0), pulse.page_hinkley(step, 0.0, 1.0)
    assert 10 <= a <= 14 and 10 <= b <= 14  # alerte peu après la rupture, jamais avant
    assert 10 <= pulse.cusum(-step, 1.0) <= 14  # bilatéral pour le biais
    assert pulse.page_hinkley(-step, 0.0, 1.0) is None  # les erreurs qui baissent ne sont pas une dégradation


def test_psi_alarm_needs_a_shift_in_the_inputs():
    rng = np.random.default_rng(40)
    train = rng.normal(0, 1, (2000, 2))
    same = [rng.normal(0, 1, (20, 2)) for _ in range(30)]
    assert pulse.psi_alarm(train, same) is None
    shifted = [rng.normal([2, 0], 1, (20, 2)) for _ in range(30)]
    assert pulse.psi_alarm(train, shifted) == pulse.PSI_WINDOW - 1


def test_break_scenarios_change_the_relation_as_declared():
    before = pulse.beta_at("nette", pulse.BREAK_AT - 1)
    np.testing.assert_allclose(before, pulse.BETA)
    np.testing.assert_allclose(pulse.beta_at("nette", pulse.BREAK_AT), -pulse.BETA)
    assert pulse.beta_at("faible", pulse.BREAK_AT)[0] == pytest.approx(0.2)
    np.testing.assert_allclose(pulse.beta_at("progressive", pulse.BREAK_AT + 25), pulse.BETA * (1 - 2 * 26 / 52))
    np.testing.assert_allclose(pulse.beta_at("entrees_seules", pulse.BREAK_AT + 10), pulse.BETA)  # la relation ne change pas


def test_summary_counts_false_alarms_detections_and_delays():
    b = pulse.BREAK_AT
    s = pulse.summarize([None, b + 4, 10, b + 8], degraded=True)
    assert s["fausses_alertes"] == 0.25 and s["detections"] == 0.5 and s["non_detections"] == 0.5 and s["delai_median_semaines"] == 6
    quiet = pulse.summarize([None, b + 4], degraded=False)
    assert quiet["fausses_alertes"] == 0.5 and quiet["detections"] is None  # sans dégradation, toute alerte est fausse


def test_simulated_breaks_are_seen_by_the_matching_detector():
    rng = np.random.default_rng(41)
    sharp = [pulse.simulate("nette", rng) for _ in range(15)]
    assert set(sharp[0]) == {"entrees", "biais", "erreurs"}
    assert pulse.summarize([r["erreurs"] for r in sharp], degraded=True)["detections"] >= 0.8  # rupture nette : erreurs en hausse
    assert pulse.summarize([r["entrees"] for r in sharp], degraded=True)["detections"] <= 0.2  # entrées inchangées : PSI muet
    inputs = [pulse.simulate("entrees_seules", rng) for _ in range(15)]
    assert np.mean([r["entrees"] is not None and r["entrees"] >= pulse.BREAK_AT for r in inputs]) >= 0.8  # le PSI voit la dérive
