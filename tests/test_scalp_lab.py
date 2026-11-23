"""Laboratoire court terme (SPEC.md, chantier Moteur court terme) : règles d'exécution sans regard vers le futur,
coûts, période réservée verrouillée et données publiques vérifiées. Hors ligne."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import data
from sentinelle.scalp.barriers import INVALID, STOP, TARGET, TIME, Barrier, Costs, baseline, breakeven_win_rate, simulate

ROOT = Path(__file__).resolve().parents[1]
FREE = Costs(0.0, 0.0)


def bars(rows):
    o, h, l, c = (np.array(x, dtype=float) for x in zip(*rows))
    return o, h, l, c


def test_target_is_reached_after_entering_at_the_next_open():
    o, h, l, c = bars([(100, 100, 100, 100), (100, 101.2, 99.9, 101), (101, 101, 101, 101)])
    r = simulate(o, h, l, c, [0], Barrier(1.0, 1.0, 2), Costs())
    assert r["outcome"][0] == TARGET and r["exit"][0] == 1
    assert r["gross"][0] == pytest.approx(0.01) and r["net"][0] == pytest.approx(0.01 - 0.0022)


def test_the_decision_bar_never_counts():
    # La bougie de décision monte au-delà de l'objectif : sans regard vers le futur, ce gain est inaccessible.
    o, h, l, c = bars([(100, 105, 100, 104), (104, 104.1, 103.9, 104), (104, 104, 104, 104)])
    r = simulate(o, h, l, c, [0], Barrier(1.0, 1.0, 2), FREE)
    assert r["outcome"][0] == TIME and r["gross"][0] == pytest.approx(0.0)


def test_stop_wins_when_stop_and_target_share_a_bar():
    o, h, l, c = bars([(100, 100, 100, 100), (100, 102, 98, 100), (100, 100, 100, 100)])
    r = simulate(o, h, l, c, [0], Barrier(1.0, 1.0, 2), FREE)
    assert r["outcome"][0] == STOP and r["gross"][0] == pytest.approx(-0.01)


def test_an_open_below_the_stop_fills_at_the_open():
    o, h, l, c = bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (97, 97.5, 96, 97), (97, 97, 97, 97)])
    r = simulate(o, h, l, c, [0], Barrier(2.0, 1.0, 3), FREE)
    assert r["outcome"][0] == STOP and r["gross"][0] == pytest.approx(-0.03)


def test_time_exit_uses_the_close_of_the_last_bar():
    o, h, l, c = bars([(100, 100, 100, 100), (100, 100.4, 99.6, 100.2), (100.2, 100.5, 100, 100.3), (1, 1, 1, 1)])
    r = simulate(o, h, l, c, [0], Barrier(1.0, 1.0, 2), FREE)
    assert r["outcome"][0] == TIME and r["exit"][0] == 2 and r["gross"][0] == pytest.approx(0.003)


def test_a_missing_minute_invalidates_the_trade_instead_of_guessing():
    o, h, l, c = bars([(100, 100, 100, 100), (100, 100.2, 99.9, 100), (np.nan,) * 4, (100, 102, 100, 101)])
    r = simulate(o, h, l, c, [0], Barrier(1.0, 1.0, 3), FREE)
    assert r["outcome"][0] == INVALID


def test_breakeven_matches_the_costs():
    assert breakeven_win_rate(Barrier(0.3, 0.3, 60), Costs()) == pytest.approx((0.003 + 0.0022) / 0.006)
    assert breakeven_win_rate(Barrier(2.0, 1.0, 60), Costs()) == pytest.approx(0.0122 / 0.03)
    assert Costs(0.00075, 0.0001).round_trip == pytest.approx(0.0017)


def test_random_walk_loses_the_costs_on_average():
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 20_000)))
    o = np.r_[close[0], close[:-1]]
    frame = pd.DataFrame({"t": np.arange(len(close)) * 60_000, "o": o, "h": np.maximum(o, close) * 1.0003,
                          "l": np.minimum(o, close) * 0.9997, "c": close})
    report = baseline(frame, [Barrier(0.5, 0.5, 120)], Costs())
    row = report["grid"][0]
    assert row["trades"] > 3000 and row["mean_net"] < 0
    lo, hi = row["mean_net_ci95_daily_blocks"]
    assert lo <= row["mean_net"] <= hi


def test_reserved_period_is_locked_outside_the_single_evaluation(tmp_path):
    with pytest.raises(data.ReservedPeriodError):
        data.download_month("BTCUSDC", "2026-09", tmp_path)
    with pytest.raises(data.ReservedPeriodError):
        data.guard(*data.month_bounds("2026-11"))
    data.guard(*data.month_bounds("2026-08"))
    data.guard(*data.month_bounds("2026-12"))
    data.guard(*data.month_bounds("2026-10"), evaluation=True)


def test_reserved_period_matches_the_preregistered_protocol():
    protocol = json.loads((ROOT / "research" / "scalp-v1" / "protocol.json").read_text(encoding="utf-8"))
    reserved = protocol["periods"]["reserved"]
    assert (reserved["from_utc"], reserved["to_utc"]) == (data.RESERVED_FROM, data.RESERVED_TO)
    assert protocol["mode"] == "paper_only" and protocol["can_trade_real"] is False


def _archive(rows):
    text = "\n".join(",".join(str(v) for v in r) for r in rows).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X-1m.csv", text)
    return buf.getvalue()


def _row(t_us, price):
    return [t_us, price, price + 1, price - 1, price, 2.0, t_us + 59_999_999, 0, 10, 1.0, 0, 0]


def test_download_checks_the_published_checksum_and_normalises_microseconds(tmp_path):
    start = data.month_bounds("2026-08")[0] * 1000  # archives récentes : microsecondes
    body = _archive([_row(start, 100), _row(start + 60_000_000, 101), _row(start + 180_000_000, 102)])
    good = hashlib.sha256(body).hexdigest()

    def handler(request):
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{good}  BTCUSDC-1m-2026-08.zip\n")
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame, report = data.load_bars("BTCUSDC", "2026-08", "2026-08", tmp_path, client=client)
    assert frame["t"].iloc[0] == start // 1000
    assert report["gaps"] == 1 and report["missing_minutes"] == 1 and report["bars"] == 3
    assert np.isnan(frame["o"].iloc[2]) and frame["taker_buy"].iloc[0] == 1.0
    assert report["files"][0]["sha256"] == good


def test_a_corrupted_archive_is_refused_and_not_cached(tmp_path):
    body = _archive([_row(data.month_bounds("2026-07")[0] * 1000, 100)])

    def handler(request):
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text="0" * 64 + "  BTCUSDC-1m-2026-07.zip\n")
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(data.DataIntegrityError):
        data.download_month("BTCUSDC", "2026-07", tmp_path, client=client)
    assert not any(tmp_path.rglob("*.zip"))


def test_a_cached_archive_modified_after_verification_is_refused(tmp_path):
    body = _archive([_row(data.month_bounds("2026-06")[0] * 1000, 100)])
    good = hashlib.sha256(body).hexdigest()

    def handler(request):
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{good}  BTCUSDC-1m-2026-06.zip\n")
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        path = data.download_month("BTCUSDC", "2026-06", tmp_path, client=client)
    assert data.download_month("BTCUSDC", "2026-06", tmp_path) == path  # cache vérifié, sans réseau
    path.write_bytes(body + b"x")
    with pytest.raises(data.DataIntegrityError):
        data.download_month("BTCUSDC", "2026-06", tmp_path)


def test_daily_archives_complete_a_month_not_yet_archived_and_stay_locked(tmp_path):
    with pytest.raises(data.ReservedPeriodError):
        data.download_day("BTCUSDC", "2026-11-03", tmp_path)
    july = data.month_bounds("2026-07")[0] * 1000
    aug1 = data.month_bounds("2026-08")[0] * 1000
    bodies = {"BTCUSDC-1m-2026-07.zip": _archive([_row(july, 100)]),
              "BTCUSDC-1m-2026-08-01.zip": _archive([_row(aug1, 101), _row(aug1 + 60_000_000, 102)]),
              "BTCUSDC-1m-2026-08-02.zip": _archive([_row(aug1 + 86_400_000_000, 103)])}

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if name.endswith(".CHECKSUM"):
            zip_name = name[:-len(".CHECKSUM")]
            return httpx.Response(200, text=f"{hashlib.sha256(bodies[zip_name]).hexdigest()}  {zip_name}\n")
        return httpx.Response(200, content=bodies[name])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame, report = data.load_bars("BTCUSDC", "2026-07", "2026-08", tmp_path, client=client, last_day="2026-08-02")
    assert [f["file"] for f in report["files"]] == list(bodies)
    assert frame["t"].iloc[-1] == (aug1 + 86_400_000_000) // 1000 and frame["o"].iloc[-1] == 103
    with pytest.raises(ValueError):
        data.load_bars("BTCUSDC", "2026-07", "2026-08", tmp_path, last_day="2026-09-01")
