"""Évaluation unique (SPEC.md, chantier Moteur court terme) : le code doit correspondre exactement au système figé
publié avant le test, et la période réservée ne se lit qu'en mode évaluation. Hors ligne."""

import json

import pytest

from sentinelle.scalp import data, reserve


def test_the_code_matches_the_frozen_system():
    frozen, digest = reserve.check_frozen()
    assert len(digest) == 64 and frozen["core"]["share"] + frozen["fast"]["share"] == pytest.approx(1.0)


def test_any_drift_from_the_frozen_system_is_refused(tmp_path):
    frozen = json.loads(reserve.FROZEN.read_text(encoding="utf-8"))
    frozen["core"]["rules"]["vol_target"] = 0.5  # réglage modifié après coup
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps(frozen), encoding="utf-8")
    with pytest.raises(AssertionError):
        reserve.check_frozen(path)


def test_the_reserved_period_is_only_read_in_evaluation_mode(tmp_path):
    with pytest.raises(data.ReservedPeriodError):
        data.load_bars("BTCUSDC", "2026-09", "2026-11", tmp_path, last_day="2026-11-22")
