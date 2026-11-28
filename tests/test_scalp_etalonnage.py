"""OMEGA D — étalonnage de PULSE : seuils choisis sur l'étalonnage seul, jugés sur des scénarios indépendants."""

import numpy as np

from sentinelle.scalp import etalonnage, pulse


def test_the_generators_differ_and_the_first_matches_174():
    g1, g2 = etalonnage.G1, etalonnage.G2
    assert (g1.k, g1.per_week, g1.break_at) == (pulse.K, pulse.PER_WEEK, pulse.BREAK_AT)
    assert (g2.k, g2.per_week, g2.break_at) != (g1.k, g1.per_week, g1.break_at) and len(g2.beta) == g2.k
    np.testing.assert_allclose(etalonnage.beta_at(g1, "nette", g1.break_at), -pulse.BETA)
    np.testing.assert_allclose(etalonnage.beta_at(g2, "faible", g2.break_at)[0], g2.beta[0] / 3)


def test_series_are_weekly_and_scaled_on_training_only():
    s = etalonnage.series(etalonnage.G1, "aucune", np.random.default_rng(90))
    assert len(s["biais"]) == len(s["erreurs"]) == pulse.MONITOR_WEEKS
    assert s["sigma_biais"] > 0 and s["sigma_perte"] > 0 and 0.4 < s["reference_perte"] < 0.8


def test_calibration_picks_the_smallest_threshold_meeting_the_target():
    cal = etalonnage.calibrate(reps=40, seed=91)
    for d in ("biais", "erreurs"):
        rates = cal["fausses_alertes_par_seuil"][d]
        assert all(rates[f"{a:g}"] >= rates[f"{b:g}"] for a, b in zip(etalonnage.GRID, etalonnage.GRID[1:]))  # monotone
        chosen = cal["seuils"][d]
        ok = [t for t in etalonnage.GRID if rates[f"{t:g}"] <= etalonnage.TARGET_FALSE_ALARMS]
        assert chosen == (ok[0] if ok else etalonnage.GRID[-1])


def test_higher_thresholds_trade_false_alarms_for_delay():
    out = etalonnage.evaluate(etalonnage.G1, 92, {"bas": (5.0, 5.0), "haut": (20.0, 20.0)}, reps=25)
    assert out["aucune"]["haut"]["erreurs"]["fausses_alertes"] <= out["aucune"]["bas"]["erreurs"]["fausses_alertes"]
    assert out["nette"]["bas"]["erreurs"]["detections"] >= 0.5  # rupture nette vue par les erreurs
    assert etalonnage.summarize([None, 80, 10], True, 76)["fausses_alertes"] == 1 / 3


def test_calibrated_real_uses_received_cohorts_with_the_frozen_thresholds(monkeypatch):
    calls = []
    def monitor(frame, **kwargs):
        calls.append((frame, kwargs))
        return {2020: {"alerte_entrees": "2020-01-05", "alerte_biais": None,
                       "alerte_erreurs": "2020-01-20", "semaines_resultats": 1}}

    monkeypatch.setattr(pulse, "real", monitor)
    source = object()
    assert etalonnage.real(source, 7, 8, as_of_day=18281) == {
        2020: {"alerte_biais": None, "alerte_erreurs": "2020-01-20", "semaines_resultats": 1}}
    assert calls == [(source, {"as_of_day": 18281, "historical": False, "h": 7, "lam": 8})]
