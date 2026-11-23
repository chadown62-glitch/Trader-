"""Système complet (SPEC.md, chantier Moteur court terme) : règle de choix fixée avant calcul et partage du capital
entre la position de fond et le moteur rapide, sans jamais le dépasser. Hors ligne."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import core, systeme
from sentinelle.scalp.data import month_bounds


def test_admissible_requires_the_drawdown_cap_and_two_positive_years():
    good = {"max_drawdown_pct": 16.1, "years": {"a": 44.2, "b": 6.4}}
    assert systeme.admissible(good)
    assert not systeme.admissible({**good, "max_drawdown_pct": 21.0})
    assert not systeme.admissible({**good, "years": {"a": 35.6, "b": -2.0}})


def test_the_capital_is_shared_between_the_core_and_the_fast_engine():
    rng = np.random.default_rng(1)
    start, end = month_bounds("2025-09")[0], month_bounds("2025-12")[1] + 1
    days = np.arange(start // core.DAY_MS - 80, end // core.DAY_MS + 1)
    paths = {s: 100 * np.exp(np.cumsum(rng.normal(0.003, 0.03, len(days)))) for s in ("BTCUSDC", "AUSDC", "BUSDC")}
    closes = pd.DataFrame(paths, index=days)
    opens, lows = closes.shift(1).fillna(closes.iloc[0]), closes * 0.98
    t = start + 3 * core.DAY_MS
    fast = pd.DataFrame([{"decided_at": t, "exit_ms": t + 3_600_000, "symbol": "AUSDC", "setup": "cassure",
                          "risk_pct": 0.02, "net_return": 0.04, "r": 2.0, "outcome": "suiveur"}])
    only_core = systeme.evaluate((opens, closes, lows), fast, np.array([False]), systeme.BASE, 1.0, start, end)
    only_fast = systeme.evaluate((opens, closes, lows), fast, np.array([True]), systeme.BASE, 0.0, start, end)
    mixed = systeme.evaluate((opens, closes, lows), fast, np.array([True]), systeme.BASE, 0.7, start, end)
    assert only_fast["fast_trades"] == 1 and only_fast["core_orders"] == 0
    # 0,5 % de 1 000 risqué sur un stop à 2 % : 250 investis, +4 % net, soit +10 sur 1 000.
    assert only_fast["return_pct"] == pytest.approx(1.0)
    expected = 0.7 * (1 + only_core["return_pct"] / 100) + 0.3 * (1 + 0.04 * 0.005 / 0.02)
    assert 1 + mixed["return_pct"] / 100 == pytest.approx(expected, rel=1e-9)
