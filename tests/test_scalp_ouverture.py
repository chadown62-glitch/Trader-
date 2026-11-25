"""Écart entre l'exécution simulée à l'ouverture et la cotation accessible (C099) : sens de l'écart, tirage, lecture."""

import zipfile

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import data, moteur, ouverture


def minutes(prices, volumes):
    p, v = np.asarray(prices, float), np.asarray(volumes, float)
    return pd.DataFrame({"t": np.arange(len(p)) * 60_000, "o": p, "v": v, "qv": p * v})


def test_the_gap_is_adverse_for_a_buyer_paying_more_and_a_seller_receiving_less():
    m = minutes([101.0, 102.0, 99.0], [1.0, 1.0, 2.0])
    assert ouverture.adverse_gap(m, 100.0, +1, 1) == pytest.approx(0.01)  # achat à 101 au lieu de 100
    assert ouverture.adverse_gap(m, 100.0, -1, 1) == pytest.approx(-0.01)  # vente à 101 : meilleure que simulée
    assert ouverture.adverse_gap(m, 100.0, +1, 3) == pytest.approx((101 + 102 + 198) / 4 / 100 - 1)  # moyenne pondérée
    assert ouverture.adverse_gap(minutes([100.0], [0.0]), 100.0, +1, 1) is None  # aucun échange : non mesurable
    assert ouverture.adverse_gap(m, float("nan"), +1, 1) is None


def test_entries_and_exits_are_read_from_the_held_weights():
    W = np.array([[0.0, 0.1], [0.1, 0.1], [0.1, 0.0], [0.0, 0.0]])
    assert ouverture.executions(W, [10, 11, 12, 13], ["AUSDT", "BUSDT"]) == [(10, "BUSDT", 1), (11, "AUSDT", 1), (12, "BUSDT", -1),
                                                                              (13, "AUSDT", -1)]


def test_the_draw_is_fixed_and_skips_the_reserved_period_and_missing_opens():
    reserved = data.RESERVED_MS[0] // moteur.DAY_MS
    days = list(range(reserved - 50, reserved + 5))
    opens = pd.DataFrame({"AUSDT": 1.0}, index=days)
    opens.loc[days[0], "AUSDT"] = np.nan
    trades = [(d, "AUSDT", 1) for d in days]
    a, b = ouverture.draw(trades, opens, 10, 0), ouverture.draw(trades, opens, 10, 0)
    assert a == b and len(a) == 10  # même graine, même tirage
    assert all(days[0] < d < reserved for d, _, _ in ouverture.draw(trades, opens, 100, 0))  # 49 éligibles au plus


def test_minute_archives_in_microseconds_are_read_in_milliseconds(tmp_path):
    rows = "1735689600000000,1.0,1.1,0.9,1.05,10,1735689659999999,10.5,3,5,5.2,0\n"
    path = tmp_path / "X-1m-2025-01-01.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("X-1m-2025-01-01.csv", "open_time,o,h,l,c,v,ct,qv,n,tb,tq,i\n" + rows)
    m = ouverture.read_minutes(path)
    assert m.loc[0, "t"] == 1735689600000 and m.loc[0, "qv"] == 10.5 and list(m.columns) == ["t", "o", "v", "qv"]


def test_the_summary_counts_missing_minutes_and_the_share_above_the_assumed_slippage():
    rows = [{"absente": True}] + [{"ouverture_identique": True, "volume_premiere_minute_usdt": 1000.0,
                                   **{f"ecart_{k}min": g for k in ouverture.WINDOWS}} for g in (0.0, 0.0002, 0.001)]
    s = ouverture.summarize(rows)
    assert s["executions"] == 4 and s["minute_absente"] == 1 and s["premiere_minute_egale_a_l_ouverture"] == 1.0
    assert s["1 min"]["n"] == 3 and s["1 min"]["part_au_dela_du_glissement_suppose"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["1 min"]["mediane_pb"] == 2.0
