"""Faisabilité avec un petit capital : montant minimal, pas de quantité, reliquat, règles inconnues."""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from sentinelle.costs.evidence import Value
from sentinelle.costs.venues import MarketFilters
from sentinelle.scalp import faisabilite


def rules(min_notional="5", step="0.001"):
    return MarketFilters("XUSDT", "X", "USDT", "TRADING", Decimal("0.01"), Decimal(step), Decimal(step), Decimal("0"),
                         Decimal(min_notional), True, Value(1.0, "filters", "OBSERVED", "test", as_of="2026-11-24T00:00:00Z", sha256="0" * 64))


def test_an_entry_below_the_exchange_minimum_is_impossible():
    assert faisabilite.check_entry(100.0, 3.0, rules())["statut"] == "sous le minimum"  # 3 USDT < 5 USDT
    ok = faisabilite.check_entry(100.0, 50.0, rules())
    assert ok["statut"] == "faisable" and ok["erreur_taille"] == pytest.approx(0.0)
    assert faisabilite.check_entry(100.0, 50.0, None)["statut"] == "règles inconnues"


def test_quantity_step_leaves_unsellable_dust():
    coarse = faisabilite.check_entry(40.0, 100.0, rules(step="1"))  # 2 unités achetées, commission prise dans l'actif
    assert coarse["statut"] == "faisable"
    assert coarse["erreur_taille"] == pytest.approx(0.2)  # 80 USDT achetés au lieu de 100
    assert coarse["part_reliquat"] > 0.4  # 1,998 unité détenue : une seule est revendable


def test_entries_are_taken_on_the_first_day_of_each_position():
    W = np.array([[0.0, 0.1], [0.1, 0.1], [0.1, 0.0], [0.0, 0.0], [0.05, 0.0]])
    assert faisabilite.entries(W, [10, 11, 12, 13, 14], ["AUSDT", "BUSDT"]) == [(10, "BUSDT", 0.1), (11, "AUSDT", 0.1), (14, "AUSDT", 0.05)]


def test_study_scales_with_capital_and_counts_each_outcome():
    days = pd.RangeIndex(10, 15)
    opens = pd.DataFrame({"XUSDT": 100.0, "YUSDT": 100.0}, index=days)
    eq = pd.Series([1.0, 1.0, 2.0, 2.0, 2.0], index=days)
    trades = [(11, "XUSDT", 0.1), (13, "XUSDT", 0.1), (13, "YUSDT", 0.1)]
    small = faisabilite.study(trades, eq, opens, {"XUSDT": rules()}, 30.0)  # 3 USDT, puis 6 USDT après doublement
    assert (small["sous_le_minimum"], small["faisables"], small["regles_inconnues"]) == (1, 1, 1)
    large = faisabilite.study(trades, eq, opens, {"XUSDT": rules()}, 1000.0)
    assert large["faisables"] == 2 and large["part_faisable"] == pytest.approx(2 / 3, abs=1e-4)  # arrondi à 4 décimales
