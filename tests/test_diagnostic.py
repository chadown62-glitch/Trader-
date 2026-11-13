"""Diagnostic d'installation (tools/diagnose_windows.py) : exécution finie, dossier temporaire, lecture seule des données."""

import json
import socket
from pathlib import Path

import pytest

from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App
from tools import diagnose_windows as diag

ROOT = Path(__file__).resolve().parent.parent


def _sockets_allowed() -> bool:
    try:
        s = socket.socket()
    except OSError:
        return False
    s.close()
    return True


def _fingerprint(folder: Path) -> dict:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def user_install(tmp_path):
    """Installation d'utilisateur fermée (base, portefeuille 30 €), à ne surtout pas modifier."""
    data = tmp_path / "data"
    app = App(data, start=False, source=SyntheticSource(["BTC/EUR"], days=2),
              settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}})
    app.db.close()
    return data


def test_offline_diagnostic_is_finite_and_never_touches_user_data(user_install, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("aucune donnée de marché publique sans --reseau")

    monkeypatch.setattr(diag, "network_prices", no_network)
    before = _fingerprint(user_install)
    result = diag.diagnose(data_dir=user_install, network=False, panel=False)
    assert _fingerprint(user_install) == before
    steps = {s["key"]: s for s in result["installation"]["steps"]}
    for key in ("python", "dependencies", "sqlite", "paper_only", "frozen", "user_data"):
        assert steps[key]["status"] == "ok", steps[key]
    assert steps["panel"]["status"] == "skip"
    assert result["installation"]["ok"] and result["kind"] == "sentinelle-diagnostic"
    assert result["windows_actually_tested"] is (diag.system_info()["windows_actually_tested"])
    obs = result["observation"]
    assert obs["verdict"] == "BLOCKED" and obs["reasons"]
    checks = {c["key"]: c for c in obs["checks"]}
    assert checks["prices"]["status"] == "untested"  # hors ligne : jamais présenté comme vérifié
    assert checks["runner"]["status"] == "blocked"
    assert checks["wallet"]["status"] == "ok"
    json.dumps(result)


def test_report_text_is_explicit_about_windows_and_blocking_reasons(user_install):
    result = diag.diagnose(data_dir=user_install, network=False, panel=False)
    text = "\n".join(diag.render(result))
    assert "Observation prospective : BLOCKED" in text and "Raisons du blocage" in text
    if not result["windows_actually_tested"]:
        assert "ce rapport ne valide pas Windows" in text


def test_cli_writes_a_report_the_panel_can_read_and_strict_mode_fails_when_blocked(user_install, tmp_path, capsys):
    from sentinelle.preflight import latest_diagnostic

    report = tmp_path / "rapports" / "diagnostic-test.json"
    assert diag.main(["--donnees", str(user_install), "--sans-panel", "--rapport", str(report)]) == 0
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["kind"] == "sentinelle-diagnostic" and data["observation"]["verdict"] == "BLOCKED"
    seen = latest_diagnostic(report.parent)
    assert seen["file"] == "diagnostic-test.json" and seen["observation_verdict"] == "BLOCKED"
    assert diag.main(["--donnees", str(user_install), "--sans-panel", "--strict"]) == 3
    assert "Rapport enregistré" in capsys.readouterr().out


def test_network_check_uses_public_reads_only(monkeypatch):
    calls = []

    class FakeSource:
        def __init__(self, exchange_id, public_mirror=False):
            calls.append(("init", exchange_id, public_mirror))

        def fetch_ohlcv(self, symbol, tf, limit):
            calls.append(("ohlcv", symbol, tf))
            if symbol == "SOL/EUR":
                raise RuntimeError("paire indisponible")
            import time
            return [[int(time.time() * 1000) - 30_000, 1, 1, 1, 1, 1]]

    import sentinelle.market.sources as sources

    monkeypatch.setattr(sources, "CcxtSource", FakeSource)
    out = diag.network_prices(["BTC/EUR", "SOL/EUR"])
    assert out["prices"] == {"BTC/EUR": True, "SOL/EUR": False} and "SOL/EUR" in out["errors"]
    assert calls[0] == ("init", "binance", True) and {c[0] for c in calls} == {"init", "ohlcv"}


def test_port_check_reports_forbidden_network_as_not_verified(monkeypatch):
    def denied(*a, **k):
        raise PermissionError("réseau interdit")

    monkeypatch.setattr(diag.socket, "socket", denied)
    assert diag.check_port()["status"] == "skip"
    assert diag.check_panel(Path("."))[0]["status"] == "skip"


@pytest.mark.skipif(not _sockets_allowed(), reason="sockets locales interdites (exécution sous filtre réseau)")
def test_real_panel_starts_answers_stops_and_restarts_in_a_temporary_folder(tmp_path):
    steps = {s["key"]: s for s in diag.check_panel(tmp_path)}
    assert steps["panel_api"]["status"] == "ok", steps["panel_api"]
    assert all(steps["panel_api"]["data"]["probes"].values())
    assert steps["panel_stop"]["status"] == "ok" and steps["panel_restart"]["status"] == "ok", steps


def test_windows_script_is_finite_and_never_targets_user_data():
    raw = (ROOT / "tools/diagnostic_windows.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # PowerShell 5.1 : accents lisibles
    script = raw.decode("utf-8-sig")
    assert "/XD data" in script and "--donnees" in script and "$env:TEMP" in script
    assert "Remove-Item -Recurse -Force $Travail" in script
    assert "Remove-Item" not in script.replace("Remove-Item -Recurse -Force $Travail", "")
    assert "Start-Process" not in script and "Register-ScheduledTask" not in script and "New-Service" not in script
    assert "--junitxml=$Junit tests" in script and "--seal-junit $Junit" in script
    assert "$CodeTests -eq 0 -and $Resultat.diagnostic -eq 0" in script


def test_launcher_diagnostic_runs_the_full_report():
    launcher = (ROOT / "scripts/lanceur.py").read_text(encoding="utf-8")
    assert '"tools.diagnose_windows", "--reseau", "--rapport"' in launcher
    assert (ROOT / "diagnostic.bat").read_text(encoding="utf-8", errors="replace").strip().endswith("call lancer.bat --diagnostic")
