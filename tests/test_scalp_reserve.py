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


def test_measured_costs_lower_each_trade_by_its_extra_slippage(tmp_path):
    import pandas as pd
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps({"rows": [{"symbol": "BTCUSDC", "buy_330": 0.0, "sell_330": 0.0},
                                          {"symbol": "BONKUSDC", "buy_330": 0.23, "sell_330": 0.20}]}), encoding="utf-8")
    slip = reserve.measured_slippage(costs)
    assert slip == {"BTCUSDC": 0.0001, "BONKUSDC": pytest.approx(0.0023)}  # jamais sous l'hypothèse de 0,01 %
    cands = pd.DataFrame({"symbol": ["BTCUSDC", "BONKUSDC"], "net_return": [0.02, 0.02], "risk_pct": [0.01, 0.01]})
    out = reserve.with_costs(cands, slip)
    assert out["net_return"].tolist() == pytest.approx([0.02, 0.02 - 2 * (0.0023 - 0.0001)])
    assert out["r"].tolist() == pytest.approx([2.0, (0.02 - 0.0044) / 0.01])
