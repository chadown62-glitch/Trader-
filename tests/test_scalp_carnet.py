"""C042 — carnets d'ordres publics : intégrité, causalité, couverture et mesures, sur données construites (aucun réseau)."""

import hashlib
import io
import zipfile

import httpx
import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import carnet, data

LEVELS_SIGNED = (-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0)


def _book_rows(stamp, mid, bid_scale=1.0, ask_scale=1.0):
    rows = []
    for p in LEVELS_SIGNED:
        depth = abs(p) * (bid_scale if p < 0 else ask_scale)
        price = mid * (1 + p / 200)  # prix moyen de la tranche, à mi-chemin du niveau
        rows.append({"timestamp": stamp, "percentage": p, "depth": depth, "notional": depth * price})
    return rows


def test_wide_computes_signed_imbalance_and_the_implied_price():
    book = carnet.parse_book(pd.DataFrame(_book_rows("2024-01-01 00:00:01", 100.0, bid_scale=3.0, ask_scale=1.0)))
    snap = carnet.wide(book)
    assert snap.index.tolist() == [1704067201]
    for k in carnet.LEVELS:
        assert snap[f"i{k:g}"].iloc[0] == pytest.approx(0.5)  # (3 − 1) / (3 + 1)
    assert snap["mid"].iloc[0] == pytest.approx(100.0, abs=1e-9) and snap["niveaux"].iloc[0] == 12
    old_format = carnet.parse_book(pd.DataFrame([r for r in _book_rows("2024-01-01 00:00:01", 100.0) if abs(r["percentage"]) >= 1]))
    snap10 = carnet.wide(old_format)  # format antérieur à fin 2025 : pas de niveau ±0,2 %
    assert np.isnan(snap10["i0.2"].iloc[0]) and snap10["mid"].iloc[0] == pytest.approx(100.0) and snap10["niveaux"].iloc[0] == 10
    assert carnet.quality(old_format, snap10)["instantanes_incomplets"] == 0 and carnet.quality(old_format, snap10)["niveaux_du_format"] == 10


def test_the_grid_never_uses_a_later_snapshot_and_marks_stale_ones():
    snaps = pd.DataFrame({"i1": [0.1, 0.2, 0.3], "niveaux": 12}, index=[10, 290, 400])
    g = carnet.on_grid(snaps, 0, length_s=900, step_s=300, stale_s=60)
    assert g.index.tolist() == [0, 300, 600]
    assert np.isnan(g["i1"].iloc[0])  # aucun instantané avant 0
    assert g["i1"].iloc[1] == 0.2  # 290 ≤ 300, jamais 400
    assert np.isnan(g["i1"].iloc[2])  # 400 a plus de 60 s à 600


def test_klines_are_indexed_by_the_end_of_the_candle_with_or_without_header():
    with_header = pd.DataFrame({"open_time": [1704067200000, 1704067260000], "open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [10.0, 11.0]})
    s = carnet.parse_klines(with_header)
    assert s.index.tolist() == [1704067260, 1704067320] and s.tolist() == [10.0, 11.0]
    raw = pd.DataFrame([[1704067200000000, 1, 1, 1, 10.0]], columns=["1704067140000000", "a", "b", "c", "9.0"])  # sans en-tête, µs
    s2 = carnet.parse_klines(raw)
    assert s2.index.tolist() == [1704067200, 1704067260]


def _zip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", text)
    return buf.getvalue()


def test_fetch_verifies_the_published_checksum_and_reports_missing_days():
    body = _zip("timestamp,percentage,depth,notional\n")
    good = hashlib.sha256(body).hexdigest()

    def handler(request):
        url = str(request.url)
        if "absent" in url:
            return httpx.Response(404)
        if url.endswith(".CHECKSUM"):
            return httpx.Response(200, text=("0" * 64 if "faux" in url else good) + "  x.zip\n")
        return httpx.Response(200, content=body, headers={"last-modified": "Sat, 11 May 2024 08:14:04 GMT"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        got, meta = carnet.fetch(client, "https://example.test/ok.zip")
        assert got == body and meta["sha256"] == good and meta["publication_utc"] == "2024-05-11T08:14:04Z"
        assert carnet.fetch(client, "https://example.test/absent.zip") == (None, {"publiee": False})
        with pytest.raises(carnet.IntegrityError):
            carnet.fetch(client, "https://example.test/faux.zip")


def test_the_reserved_period_stays_refused(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(data.ReservedPeriodError):
            carnet.book_day(client, "BTCUSDT", "2026-10-01", tmp_path)


def _synthetic(days, informative, seed):
    rng = np.random.default_rng(seed)
    start = carnet.day_ms("2024-01-01") // 1000
    t = np.arange(start, start + days * carnet.DAY_S, 300)
    imb = rng.uniform(-1, 1, len(t))
    steps = rng.normal(0, 1e-3, len(t)) + (2e-3 * np.r_[0.0, imb[:-1]] if informative else 0.0)
    price = 100 * np.exp(np.cumsum(steps))
    grid = pd.DataFrame({"i0.2": imb, "i1": imb, "i5": imb, "mid": price}, index=pd.Index(t, name="t"))
    minute = np.arange(start, start + days * carnet.DAY_S + 3600, 60)
    close = pd.Series(np.interp(minute, t, price), index=pd.Index(minute, name="t"))
    return grid, close


def test_intraday_daily_and_alignment_measures_see_what_was_built():
    grid, close = _synthetic(60, informative=True, seed=70)
    r = carnet.intraday_test(grid, close, 1.0, 5)
    assert r["jours"] >= 55 and r["rang_moyen"] > 0.3 and r["p_bilaterale"] < 1e-6
    assert r["rendement_moyen_pb"]["decile_superieur"] > r["rendement_moyen_pb"]["decile_inferieur"]
    flat, close0 = _synthetic(60, informative=False, seed=71)
    assert abs(carnet.intraday_test(flat, close0, 1.0, 5)["rang_moyen"]) < 0.05
    d = carnet.daily_test(flat, close0, 1.0, 200, np.random.default_rng(0))
    assert d["jours"] >= 58 and d["p_bilaterale"] > 0.01
    al = carnet.alignment(grid, close)
    assert al["+0 h"] == pytest.approx(0.0, abs=1e-6) and al["+1 h"] > al["+0 h"]


def test_coverage_counts_missing_days_and_publication_delays():
    q = {"instantanes": 2880, "plus_long_trou_s": 31, "doublons": 0, "instantanes_incomplets": 0, "niveaux_du_format": 10}
    metas = [{"jour": "2024-05-10", "publiee": True, "octets": 10, "publication_utc": "2024-05-11T08:00:00Z", "qualite": q},
             {"jour": "2024-05-11", "publiee": False}]
    c = carnet.coverage(metas)
    assert c["jours_publies"] == 1 and c["jours_absents"] == ["2024-05-11"] and c["delai_de_publication_h"]["mediane"] == pytest.approx(8.0)
