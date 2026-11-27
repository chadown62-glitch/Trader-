"""Programme multi-actifs, vague 1 : coûts par côté, mesures, poches collectives, jugement à 34 tests et arrêt au 31 août
2026, sur données construites (aucun réseau)."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import multi_vague1 as V


def test_each_change_pays_the_side_cost_at_execution():
    net = V.net_returns(np.zeros(5), np.array([[0.0], [1.0], [1.0], [0.0], [0.0]]), 0.001)
    assert net[:, 0].tolist() == pytest.approx([0, 0, -0.001, 0, -0.001])


def test_trade_stats_count_costs_inside_each_trade():
    pos = np.array([0, 1, 1, 0, 0, 1, 0, 0], dtype=float)
    net = np.array([0, 0, 0.02, 0.01, -0.001, 0, -0.03, -0.001])
    s = V.trade_stats(pos, net, 365)
    assert s["transactions"] == 2
    first, second = 1.02 * 1.01 * 0.999 - 1, 0.97 * 0.999 - 1
    assert s["esperance_par_transaction"] == pytest.approx((first + second) / 2)
    assert s["profit_factor"] == pytest.approx(first / -second)


def _returns(rng, days, effect_index, effect):
    """Rendements journaliers indépendants (1,5 % d'écart type) ; avantage de momentum 20 j sur une colonne."""
    sd = 0.015
    r = rng.normal(0, sd, (days, len(V.INSTRUMENTS)))
    if effect_index is not None:
        kappa = effect / (np.sqrt(0.5) * np.sqrt(252))
        for t in range(20, days):
            r[t, effect_index] += kappa * sd * np.sign(r[t - 20:t, effect_index].sum())
    return np.log1p(np.maximum(r, -0.9))


def _closes(rng, days, effect_index=None, effect=0.0):
    r = _returns(rng, days, effect_index, effect)
    out = {}
    for a, name in enumerate(V.INSTRUMENTS):
        freq = "D" if V.INSTRUMENTS[name][0] == "crypto" else "B"
        idx = pd.date_range("2015-01-01", periods=days + 1, freq=freq)
        out[name] = pd.Series(100 * np.exp(np.r_[0.0, np.cumsum(r[:, a])]), index=idx)
    return out


def test_collective_sleeves_hold_zero_on_days_without_quotes():
    prep = V.prepare(_closes(np.random.default_rng(1), 400))
    s = V.collective_series(prep, 0)
    assert s.index.is_monotonic_increasing and (s.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()
    assert np.isfinite(s).all()


def test_the_program_finds_a_strong_edge_only_where_it_was_put():
    prep = V.prepare(_closes(np.random.default_rng(2), 1500, effect_index=7, effect=3.0))
    verdict = V.judge(prep)
    assert len(verdict["tests"]) == 2 + 4 * len(V.INSTRUMENTS)
    kept = {n for _, n in verdict["retenues"]}
    assert "SP500" in kept and kept <= {"SP500"}
    assert set(V.describe(prep, verdict["retenues"])) == {f"{v}@{n}" for v, n in verdict["retenues"]}
    null = V.judge(V.prepare(_closes(np.random.default_rng(3), 1500)))
    assert null["retenues"] == [] and null["validations_collectives"] == []


def test_history_stops_before_the_reserved_period(monkeypatch, tmp_path):
    days = pd.date_range("2024-01-01", "2026-11-22", freq="D")
    long = pd.Series(np.linspace(100, 200, len(days)), index=days)
    bars = pd.DataFrame({"c": long.to_numpy()}, index=(days.asi8 // 10**6 // V.moteur.DAY_MS))
    monkeypatch.setattr(V.signaux, "load_bars", lambda *a, **k: (bars, {}))
    monkeypatch.setattr(V.actifs, "download", lambda *a, **k: long)
    closes = V.load_closes(tmp_path, client=None)
    assert all(s.index[-1] <= pd.Timestamp(V.LAST_DAY) for s in closes.values())
