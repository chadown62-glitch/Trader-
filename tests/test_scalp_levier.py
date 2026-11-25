"""Open interest des perpétuels BTC (C028) : relevé de fin de jour, causalité, désendettement, archives vérifiées."""

import hashlib
import io
import zipfile

import httpx
import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import data, levier, moteur

HEADER = "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n"


def _zip(rows: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("m.csv", HEADER + rows)
    return buf.getvalue()


def test_the_day_reading_is_the_last_one_before_midnight(tmp_path):
    path = tmp_path / "m.zip"
    path.write_bytes(_zip("2023-06-01 00:00:00,BTCUSDT,100,1,1,1,1,1\n2023-06-01 23:55:00,BTCUSDT,120,1,1,1,1,1\n"
                          "2023-06-02 00:00:00,BTCUSDT,999,1,1,1,1,1\n2023-06-01 12:00:00,BTCUSDT,110,1,1,1,1,1\n"))
    assert levier.last_reading(path, "2023-06-01") == (moteur._day("2023-06-01"), 120.0)  # le relevé du lendemain est ignoré
    path.write_bytes(_zip(""))
    assert levier.last_reading(path, "2023-06-01") is None


def test_missing_days_stay_empty_and_the_rule_ignores_the_future():
    d0 = moteur._day("2021-01-01")
    s = levier.series([(d0, 1.0), None, (d0 + 2, 3.0)])
    assert s.index.tolist() == [d0, d0 + 1, d0 + 2] and np.isnan(s.loc[d0 + 1])
    rng = np.random.default_rng(13)
    idx = pd.RangeIndex(d0, d0 + 900)
    oi = pd.Series(1e5 * np.exp(np.cumsum(rng.normal(0, 0.03, 900))), index=idx)
    btc = pd.Series(3e4 * np.exp(np.cumsum(rng.normal(0, 0.03, 900))), index=idx)
    rule = levier.leverage_rule(oi, btc)
    assert not rule.iloc[: levier.MIN_DAYS + levier.CHANGE_DAYS - 1].any()  # historique insuffisant : jamais d'abstention
    later_oi, later_btc = oi.copy(), btc.copy()
    later_oi.iloc[600:] *= 3.0
    later_btc.iloc[600:] *= 0.3
    pd.testing.assert_series_equal(rule.iloc[:600], levier.leverage_rule(later_oi, later_btc).iloc[:600])
    falling = btc.copy()
    falling[:] = 3e4 * np.exp(-0.001 * np.arange(900))  # BTC en baisse continue : la règle ne s'applique jamais
    assert not levier.leverage_rule(oi, falling).any()


def test_deleveraging_starts_at_a_ten_percent_drop():
    oi = pd.Series([100.0, 90.0, 81.1, 85.0], index=pd.RangeIndex(0, 4))  # −10 %, −9,9 %, +4,8 %
    assert levier.deleveraging(oi).tolist() == [False, True, False, False]


def test_after_deleveraging_finds_a_planted_rebound():
    rng = np.random.default_rng(14)
    idx = pd.RangeIndex(0, 1500)
    oi = pd.Series(1e5 * np.exp(np.cumsum(rng.normal(0, 0.01, 1500))), index=idx)
    drops = idx[100::25]
    oi.loc[drops] = oi.shift(1).loc[drops] * 0.85  # désendettement de 15 %
    r = pd.Series(rng.normal(0, 0.02, 1500), index=idx)
    r.loc[drops + 1] += 0.03  # rebond planté le lendemain
    btc = 3e4 * np.exp(r.cumsum())
    out = levier.after_deleveraging(oi, btc)
    assert set(out) == {"1_jour", "3_jours"}
    assert out["1_jour"]["ecart_moyen_pct"] > 2.0 and out["1_jour"]["p_holm"] < 0.05


def test_archives_are_verified_against_their_published_checksum(tmp_path):
    body = _zip("2023-06-01 23:55:00,BTCUSDT,120,1,1,1,1,1\n")

    def serve(request):
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{hashlib.sha256(b'autre').hexdigest()}  BTCUSDT-metrics-2023-06-01.zip")
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client, pytest.raises(data.DataIntegrityError):
        levier.archive("2023-06-01", tmp_path, client)
    assert not (tmp_path / "metrics" / "BTCUSDT" / "BTCUSDT-metrics-2023-06-01.zip").exists()


def test_a_zero_open_interest_is_a_gap_of_the_source_not_a_value(tmp_path):
    path = tmp_path / "m.zip"
    path.write_bytes(_zip('2022-03-07 15:25:00,BTCUSDT,94000.5,1,1,1,1,1\n2022-03-07 15:30:00,BTCUSDT,0E-8,0E-8,"","",1.77,""\n'
                          '2022-03-07 23:55:00,BTCUSDT,0E-8,0E-8,"","",2.14,""\n'))
    assert levier.last_reading(path, "2022-03-07") == (moteur._day("2022-03-07"), 94000.5)  # dernier relevé valide
    path.write_bytes(_zip('2022-03-07 23:55:00,BTCUSDT,0E-8,0E-8,"","",2.14,""\n'))
    assert levier.last_reading(path, "2022-03-07") is None  # aucun relevé valide : jour absent, pas de faux désendettement
