"""OMEGA C — RADAR, ATLAS : fiche de couverture, dépendances, séries, coûts publiés."""

import numpy as np
import pandas as pd

from sentinelle.scalp import atlas, moteur


def test_a_crypto_row_reports_coverage_gaps_delisting_and_renames():
    d0 = moteur._day("2024-01-01")
    idx = pd.RangeIndex(d0, d0 + 400)
    c = pd.Series(10.0, index=idx)
    c.iloc[100:110] = np.nan  # dix jours sans cotation
    c.iloc[300:] = np.nan  # retirée après 300 jours
    bars = pd.DataFrame({"c": c, "v": 1000.0})
    member = pd.Series([True] * 200 + [False] * 200, index=idx)
    row = atlas.crypto_row("MATICUSDT", bars, {"cut_at": None}, member, d0 + 399)
    assert row["premier_jour"] == "2024-01-01" and row["jours_cotes"] == 290 and row["jours_sans_cotation"] == 110
    assert row["retiree"] and row["volume_median_usdt_derniere_annee"] == 10000.0 and row["part_des_jours_dans_l_univers"] == 0.5
    assert row["renommage"] == "MATICUSDT → POLUSDT"
    assert atlas.crypto_row("XUSDT", pd.DataFrame(), {}, None, d0) == {"symbole": "XUSDT", "cotee": False}


def test_dependence_sees_a_dominant_common_factor():
    rng = np.random.default_rng(50)
    d0 = moteur._day("2021-01-01")
    idx = pd.RangeIndex(d0, d0 + 7 * 60)
    common = np.cumsum(rng.normal(0, 0.03, len(idx)))
    together = pd.DataFrame({f"S{k}": 100 * np.exp(common + np.cumsum(rng.normal(0, 0.005, len(idx)))) for k in range(5)}, index=idx)
    apart = pd.DataFrame({f"S{k}": 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(idx)))) for k in range(5)}, index=idx)
    member = pd.DataFrame(True, index=idx, columns=together.columns)
    hi, lo = atlas.dependence(together, member), atlas.dependence(apart, member)
    assert hi[2021]["part_premiere_composante"] > 0.8 and hi[2021]["correlation_moyenne"] > 0.7
    assert lo[2021]["part_premiere_composante"] < 0.5 and abs(lo[2021]["correlation_moyenne"]) < 0.3


def test_series_row_measures_history_and_the_longest_gap():
    s = pd.Series([1.0, 2.0, 3.0, np.nan, 4.0], index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-06", "2024-01-15"]))
    row = atlas.series_row("X", s)
    assert row == {"serie": "X", "premier_jour": "2024-01-01", "dernier_jour": "2024-01-15", "jours": 4, "plus_long_trou_jours": 10}


def test_costs_are_read_from_the_published_results():
    out = atlas.costs()
    assert "frais_dates" in out and "part_des_entrees_faisables" in out
    caps = next(iter(out["part_des_entrees_faisables"].values()))
    assert set(caps) == {"30 €", "100 €", "1000 €", "10000 €"} and all(0 <= v <= 1 for v in caps.values())
