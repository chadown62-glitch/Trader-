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


def test_real_waits_for_last_receipt_of_a_week_and_uses_the_input_clock_separately(monkeypatch):
    import pandas as pd
    from sentinelle.scalp import hive, moteur

    start = moteur._day("2020-01-05")
    rows = []
    for week in range(-12, 2):
        d = start + 7 * week
        for n in (0, 1):
            rows.append({"day": d, "asset": str(n), "ret": 0.1 if n else -0.1, "y": n,
                         **{f: n / 2 for f in hive.FEATURES}, "features_received_day": d,
                         "label_received_day": d + hive.HORIZON + (4 if week == 0 and n == 1 else 0)})
    frame = pd.DataFrame(rows)
    # Contrôler uniquement les deux horloges, avec des détecteurs qui réagissent au premier échantillon.
    monkeypatch.setattr(pulse, "cusum", lambda x, *_a, **_kw: 0 if len(x) else None)
    monkeypatch.setattr(pulse, "page_hinkley", lambda x, *_a, **_kw: 0 if len(x) else None)
    monkeypatch.setattr(pulse, "psi_alarm", lambda _train, xs: 0 if xs else None)
    at_signal = pulse.real(frame, as_of_day=start)[2020]
    assert at_signal["alerte_entrees"] == "2020-01-05" and at_signal["alerte_biais"] is None
    at_maturity = pulse.real(frame, as_of_day=start + 7)[2020]
    assert at_maturity["alerte_erreurs"] is None and at_maturity["semaines_resultats"] == 0
    at_receipt = pulse.real(frame, as_of_day=start + 11)[2020]
    assert at_receipt["alerte_biais"] == at_receipt["alerte_erreurs"] == "2020-01-16"
    cutoff = frame.copy()
    cutoff.loc[cutoff.label_received_day > start + 7, "y"] = np.nan
    assert pulse.real(cutoff, as_of_day=start + 7)[2020] == at_maturity
    no_receipts = frame.drop(columns=["features_received_day", "label_received_day"])
    assert not pulse.real(no_receipts, as_of_day=start + 7)
    theoretical = pulse.real(no_receipts, as_of_day=start + 7, historical=True)[2020]
    assert theoretical["borne_cloture_biais"] == "2020-01-12" and "alerte_biais" not in theoretical
