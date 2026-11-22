"""One command recomputes the calibrator's recorded results (brain v2 loop, iteration 19).

``python -m sentinelle.dmh.memecoin_calibrator verify [--quick]`` recomputes the recorded results, prints one line per
result (OK / ÉCART, recomputed and recorded values) and exits 0 only when every result matches. It writes nothing and
uses no network or key: a reproducible check for Windows 11, as the repository's definition of done asks."""

import pytest

from sentinelle.dmh import memecoin_calibrator as C
from tests import test_configurable_capital as frozen


def test_the_quick_verification_matches_every_recorded_result():
    rows = C.verify(quick=True)
    assert rows and all(ok for _, ok, _, _ in rows), [r for r in rows if not r[1]]
    assert len(rows) == 18


def test_a_tampered_recorded_value_is_reported(monkeypatch):
    monkeypatch.setitem(C.RECORDED_ADMISSION, "refused", {**C.RECORDED_ADMISSION["refused"], "sniper": (347, 348)})
    rows = []
    C.verify_admission(rows)
    bad = [r for r in rows if not r[1]]
    assert [r[0] for r in bad] == ["admission M58, signaux sniper refusés"] and bad[0][2:] == ((348, 348), (347, 348))


def test_the_exit_code_follows_the_rows(monkeypatch, capsys):
    monkeypatch.setattr(C, "verify", lambda quick: [("a", True, 1, 1), ("b", True, (2,), (2,))])
    assert C.main(["verify", "--quick"]) == 0 and "2 / 2 résultats concordants" in capsys.readouterr().out
    monkeypatch.setattr(C, "verify", lambda quick: [("a", True, 1, 1), ("b", False, 3, 2)])
    assert C.main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "ÉCART b : recalculé 3 ; enregistré 2" in out and "1 / 2 résultats concordants, 1 écart(s)" in out


def test_the_quick_mode_skips_only_the_full_calibrations(monkeypatch):
    calls = []
    quick = ("verify_survival", "verify_waves", "verify_admission", "verify_breaker")
    for name in quick + C.FULL_CHECKS:  # every check replaced: nothing slow runs here
        monkeypatch.setattr(C, name, lambda rows, name=name: calls.append(name))
    monkeypatch.setattr(C, "QUICK_CHECKS", tuple(getattr(C, n) for n in quick))
    C.verify(quick=True)
    assert calls == list(quick)
    calls.clear()
    C.verify(quick=False)
    assert calls == list(quick + C.FULL_CHECKS) and C.FULL_CHECKS[0] == "verify_calibrations"
    assert {"verify_bench", "verify_drawdown", "verify_wave_breaker", "verify_kelly", "verify_conditional"} <= set(C.FULL_CHECKS)


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
