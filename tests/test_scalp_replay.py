"""OMEGA — REPLAY : les trois expériences synthétiques du registre V3 sont reproduites et restent reproductibles."""

import numpy as np
import pytest

from sentinelle.scalp import replay


def test_cortex_echo_reproduces_the_published_table_and_the_theory():
    out = replay.cortex_echo()
    for (rho, b), published in replay.V3_CORTEX.items():
        row = out[f"rho={rho},b={b}"]
        assert [row[f"eqm_{k}"] for k in (1, 2, 4, 8)] == pytest.approx(published, abs=1e-6)  # code et graine du registre
        for k in (1, 2, 4, 8):
            assert row[f"eqm_{k}"] == pytest.approx(replay.cortex_theory(rho, b, k), abs=0.01)
    assert replay.cortex_theory(0.9, 0.5, 8) == pytest.approx(0.25 + 0.9 + 0.1 / 8)  # erreur commune et biais persistent


def test_pulse_quarantine_reproduces_the_published_alert_rates_and_delays():
    out = replay.pulse_quarantine()
    for name, (inp, res, uni, pre, delay) in replay.V3_PULSE.items():
        r = out[name]
        assert (r["input"]["post64_alert_pct"], r["residual"]["post64_alert_pct"], r["union"]["post64_alert_pct"]) == (inp, res, uni)
        assert (r["union"]["pre_alert_pct"], r["union"]["median_delay"]) == (pre, delay)
    # une alerte sur les entrées n'est pas une dégradation : la rupture d'erreur cachée n'est vue que par les erreurs
    assert out["input_only"]["input"]["post64_alert_pct"] == 100.0 and out["input_only"]["residual"]["post64_alert_pct"] <= 1.0
    assert out["hidden_error"]["input"]["post64_alert_pct"] <= 1.0 and out["hidden_error"]["residual"]["post64_alert_pct"] > 99


def test_hive_border_agrees_with_the_register_within_monte_carlo_error():
    out = replay.hive_border()
    cmp = replay.compare(out, replay.cortex_echo(), replay.pulse_quarantine())
    assert cmp["hive_dans_3_erreurs_types"] and cmp["cortex_identique"] and cmp["pulse_identique"]
    # premier scénario : l'ordre de tirage fixé a priori retrouve les valeurs publiées à l'identique
    assert [out["stable"][k]["eqm"] for k in ("individuel", "collectif", "hybride")] == pytest.approx(replay.V3_HIVE["stable"], abs=1e-6)
    # le mécanisme décrit par le registre : mutualiser n'est pas toujours bénéfique
    weak = out["familles_peu_coherentes"]
    assert weak["individuel"]["eqm"] < weak["hybride"]["eqm"] < weak["collectif"]["eqm"]
    broken = out["rupture_individuelle"]
    assert broken["collectif"]["eqm"] < broken["hybride"]["eqm"] < broken["individuel"]["eqm"]


def test_hive_theory_matches_a_large_simulation_and_the_closed_forms():
    theory = replay.hive_theory()
    assert theory["stable"]["individuel"] == pytest.approx(1 / 8)
    assert theory["stable"]["collectif"] == pytest.approx(0.16 * (1 - 1 / 50) + 0.125 / 50)
    assert theory["rupture_collective"]["individuel"] == pytest.approx(1 / 8 + 4 * 0.49)
    sim = replay.hive_border(reps=400, seed=7)
    for name, row in theory.items():
        for label, value in row.items():
            r = sim[name][label]
            assert abs(r["eqm"] - value) <= 4 * r["erreur_type"] + 1e-9, (name, label)


def test_the_hive_estimators_follow_their_definitions():
    rng_out = replay.hive_border(reps=3, families=2, assets=4, seed=1)
    assert set(rng_out) == set(replay.HIVE_SCENARIOS)
    for row in rng_out.values():
        assert set(row) == {"individuel", "collectif", "hybride"}
        assert all(np.isfinite(v["eqm"]) and v["eqm"] >= 0 for v in row.values())
