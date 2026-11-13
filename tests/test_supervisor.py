"""Integrated continuous scheduler, accelerated clocks and real crash/restart; no network."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sentinelle.services.supervisor import (
    State,
    Supervisor,
    prepare,
    load_plan,
    approve,
    authorize,
    read_status,
    instance_lock,
    CONFIRM,
)
from sentinelle.storage.db import Database
from tools.validation.archive import STEP_MS, encoded, digest
from tools import supervise
from tests import test_observation_e2e as fixture

ROOT = Path(__file__).resolve().parents[1]
START = fixture.NOW


def plan():
    return dict(
        start=START - 2 * 86400,
        end=START + 86400 * 90,
        poll_seconds=15,
        capture_budget_seconds=110,
        retry_seconds=30,
        max_attempts_per_bar=4,
        disk_limit_bytes=3_000_000_000,
    )


class FakeBackend:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def validate(self):
        self.calls.append("validate")

    def collect(self, path):
        self.calls.append("collect")
        return {"end_ms": int(self.clock() * 1000) // STEP_MS * STEP_MS}

    def observe(self, path):
        self.calls.append("observe")
        return {"steps_complete": True, "ready": True, "origin": "synthetic_fixture"}

    def protect(self):
        self.calls.append("protect")
        return {"maintenance_only": True}

    def macro(self):
        self.calls.append("macro")


@pytest.fixture
def rig(tmp_path):
    clock = [START]
    state = State(tmp_path / "data/supervisor")
    state.set("plan", plan())
    backend = FakeBackend(lambda: clock[0])
    observer = Supervisor(plan(), state, backend, clock=lambda: clock[0])
    yield clock, state, backend, observer
    state.close()


def test_accelerated_day_exactly_six_closes_and_quote_maintenance(rig):
    clock, state, backend, observer = rig
    for i in range(6):
        clock[0] = START + i * STEP_MS / 1000
        assert observer.tick()
        assert observer.tick()  # restart/repeated poll does not generate another forecast
        clock[0] += 60
        observer.tick()
    assert backend.calls.count("observe") == 6
    assert backend.calls.count("collect") == 6
    assert backend.calls.count("protect") == 18
    status = read_status(state.folder.parent, now=clock[0])
    assert status["observed"] == 6 and not status["coverage_complete"] and status["missing_slots"] > 0
    assert not status["launch_validated"] and status["origin"] == "synthetic_fixture"
    assert len(list((state.folder / "daily").glob("*.json"))) == 2  # the six 4h closes span two UTC calendar dates


def test_exact_close_waits_before_collecting_then_observes(rig):
    clock, _, backend, observer = rig
    clock[0] -= 10
    observer.tick()
    assert "collect" not in backend.calls
    clock[0] += 3
    observer.tick()
    assert backend.calls.count("observe") == 1


def test_delayed_close_is_archived_and_never_predicted(rig):
    clock, state, backend, observer = rig
    clock[0] += 181
    observer.tick()
    observer.tick()
    assert "observe" not in backend.calls and backend.calls.count("collect") == 1
    assert read_status(state.folder.parent, now=clock[0])["history_only"] == 1
    assert state.get("operation_capability") == "history_only_no_late_prediction"


def test_missing_quotes_never_claims_healthy_and_macro_is_optional(rig):
    _, state, backend, observer = rig
    backend.protect = lambda: None
    observer.tick()
    assert state.get("status") == "degraded" and state.get("last_quote_ok") is None
    backend.protect = lambda: {"maintenance_only": True}
    backend.macro = lambda: (_ for _ in ()).throw(RuntimeError("optional official feed down"))
    observer.tick()
    assert state.get("status") == "monitoring" and state.get("last_quote_ok") == START
    assert state.db.execute("SELECT count(*) FROM events WHERE kind='optional_macro_error'").fetchone()[0] == 1


def test_error_retry_is_bounded_and_utc_backwards_is_rejected(rig):
    clock, state, backend, observer = rig

    def failed(_):
        backend.calls.append("failed")
        raise RuntimeError("429 fixture")

    backend.collect = failed
    for _ in range(5):
        observer.tick()
        observer.tick()
        clock[0] += 30
    assert backend.calls.count("failed") == 4
    assert state.db.execute("SELECT status FROM cycles").fetchone()[0] == "exhausted"
    clock[0] -= 60
    with pytest.raises(ValueError, match="backwards"):
        observer.tick()


def test_stop_resume_end_window_and_disk_guard_never_delete(rig):
    clock, state, backend, observer = rig
    state.set("stop_requested", True)
    assert observer.run(max_ticks=1) == 0 and "collect" not in backend.calls
    state.set("stop_requested", False)
    assert observer.run(max_ticks=1) == 1
    sentinel = state.folder / "retained.raw"
    sentinel.write_bytes(b"keep")
    observer.p = {**plan(), "disk_limit_bytes": 1}
    with pytest.raises(ValueError, match="no automatic deletion"):
        observer.tick()
    assert sentinel.read_bytes() == b"keep"
    observer.p = plan()
    clock[0] = plan()["end"]
    assert observer.tick() is False and state.get("status") == "period_ended"


def test_no_period_shift_before_start_or_at_expiry(rig):
    clock, state, backend, observer = rig
    observer.p = {**plan(), "start": START + 1}
    observer.tick()
    assert state.get("status") == "waiting_for_period" and not backend.calls
    clock[0] = float("nan")
    with pytest.raises(ValueError, match="clock"):
        observer.tick()


def snapshot(folder):
    return {str(f.relative_to(folder)): f.read_bytes() for f in folder.rglob("*") if f.is_file()}


def test_plan_binds_original_frozen_period_model_code_and_installation(tmp_path, monkeypatch):
    protocol = fixture.setup(tmp_path, real_model=True)
    before = snapshot(tmp_path / "app")
    frozen = snapshot(protocol)
    target = tmp_path / "launch.json"
    p = prepare(tmp_path / "app", protocol, target, now=START)
    assert load_plan(target) == p and p["paper_only"] and p["initial_eur"] == 30 and not p["promotion_allowed"]
    assert before == snapshot(tmp_path / "app") and frozen == snapshot(protocol)
    with pytest.raises((ValueError, FileExistsError)):
        prepare(tmp_path / "app", protocol, target, now=START + 1)
    monkeypatch.setattr("sentinelle.services.supervisor.code_hashes", lambda: {"changed": "bad"})
    with pytest.raises(ValueError, match="implementation changed"):
        load_plan(target)


@pytest.mark.parametrize("key,value", [("start", START), ("initial_eur", 31), ("poll_seconds", 0), ("capture_budget_seconds", 999)])
def test_rehashed_unsafe_launch_configuration_still_refused(tmp_path, key, value):
    protocol = fixture.setup(tmp_path, real_model=True)
    target = tmp_path / "launch.json"
    p = prepare(tmp_path / "app", protocol, target)
    p[key] = value
    target.write_bytes(encoded(p))
    Path(str(target) + ".sha256").write_text(digest(encoded(p)))
    with pytest.raises(ValueError):
        load_plan(target)


def test_approval_requires_real_pc_matching_report_and_distinct_operator_confirmation(tmp_path, monkeypatch):
    protocol = fixture.setup(tmp_path, real_model=True)
    target = tmp_path / "launch.json"
    p = prepare(tmp_path / "app", protocol, target)
    report = tmp_path / "windows.json"
    approval = tmp_path / "approval.json"
    report.write_bytes(
        encoded(
            {
                "windows_actually_tested": True,
                "installation": {"ok": True, "not_verified": []},
                "supervisor_code": p["code"],
                "data_dir": p["data"],
            }
        )
    )
    monkeypatch.setattr("tools.diagnose_windows.system_info", lambda: {"windows_actually_tested": False})
    with pytest.raises(ValueError, match="Real Windows"):
        approve(target, report, approval, CONFIRM)
    assert not approval.exists()
    # Fake OS ONLY inside this unit test; never a claim of Windows validation.
    monkeypatch.setattr("tools.diagnose_windows.system_info", lambda: {"windows_actually_tested": True})
    with pytest.raises(ValueError, match="confirmation"):
        approve(target, report, approval, "yes")
    approve(target, report, approval, CONFIRM)
    assert authorize(target, approval) == p
    report.write_bytes(report.read_bytes() + b" ")
    with pytest.raises(ValueError, match="report changed"):
        authorize(target, approval)


def test_cli_missing_approval_creates_no_state_and_status_is_read_only(tmp_path):
    protocol = fixture.setup(tmp_path, real_model=True)
    target = tmp_path / "launch.json"
    prepare(tmp_path / "app", protocol, target)
    before = snapshot(tmp_path / "app")
    with pytest.raises(SystemExit):
        supervise.main(["run", "--plan", str(target)])
    assert read_status(tmp_path / "app")["state"] == "service_continu_non_validé"
    assert before == snapshot(tmp_path / "app")


class RealFixtureBackend(FakeBackend):
    """Actual archives -> features -> frozen fixture model -> paper ledger -> macro journal."""

    def __init__(self, root, clock, crash=None):
        super().__init__(clock)
        self.root = Path(root)
        self.crash = crash

    def collect(self, path):
        from tools.public_capture import PublicCapture

        with patch.object(fixture, "NOW", self.clock()), patch.object(fixture, "END", int(self.clock() * 1000) // STEP_MS * STEP_MS):
            with fixture.public_client() as client:
                c = PublicCapture(path, client, clock=self.clock)
                try:
                    manifest = c.collect(origin="synthetic_fixture")
                finally:
                    c.close()
        self.capture = path
        if self.crash == "collected":
            os._exit(77)
        return manifest

    def observe(self, path):
        from tools.observe import step
        from sentinelle.news.shadow.service import MacroShadow
        from sentinelle.news.shadow.parsers import rss
        from sentinelle.news.shadow.sources import SOURCES

        macro = MacroShadow(self.root / "app/supervisor/macro")
        now = self.clock()
        try:
            # Dated feed is available before the decision; a later revision cannot leak backwards.
            raw = b"<rss><channel><item><title>Fixture release</title><guid>fixture</guid><pubDate>Sun, 15 Nov 2026 03:55:00 GMT</pubDate></item></channel></rss>"
            macro.store.append("fed", rss(raw, SOURCES["fed"]), digest(raw), now - 1, stored_at=now - 1)
            with (
                patch.object(fixture, "NOW", now),
                patch("tools.prospective.datetime", fixture.FrozenDate),
                patch("tools.macro_shadow.time.time", return_value=now),
            ):
                result = step(
                    self.root / "app",
                    self.root / "fixture-protocol",
                    path,
                    execute=True,
                    clock=self.clock,
                    macro=macro,
                    macro_protocol=ROOT / "research/macro-shadow-v3/protocol.json",
                )
            if self.crash == "observed":
                os._exit(77)
            return result
        finally:
            macro.stop()

    def protect(self):
        from tools.public_capture import load_capture
        from tools.observe import protect

        m, _ = load_capture(self.capture)
        for b in m["books"].values():
            b["timestamp"] = self.clock() * 1000
            b["received_at"] = self.clock()
        return protect(self.root / "app", self.root / "fixture-protocol", m, clock=self.clock)


def test_integrated_full_day_paper_costs_journal_macro_and_read_only_preparation(tmp_path):
    from sentinelle.preflight import facts_from_data_dir, evaluate

    protocol = fixture.setup(tmp_path)
    frozen = snapshot(protocol)
    now = [START]
    state = State(tmp_path / "app/supervisor")
    state.set("plan", plan())
    backend = RealFixtureBackend(tmp_path, lambda: now[0])
    observer = Supervisor(plan(), state, backend, clock=lambda: now[0])
    try:
        for i in range(6):
            now[0] = START + i * STEP_MS / 1000
            observer.tick()
            observer.tick()
        assert state.db.execute("SELECT count(*) FROM cycles WHERE status='observed'").fetchone()[0] == 6
        last = state.get("last_observation")["report"]
        assert last["performance"]["capital"]["initial"] == 30 and last["journal"]["rows"]
        assert last["performance"]["result"]["check_gap"] == pytest.approx(0, abs=1e-8)
        db = Database(tmp_path / "app/sentinelle.db")
        assert db.scalar("SELECT count(*) FROM fills") > 0
        assert db.scalar("SELECT sum(fee) FROM fills") > 0
        assert db.scalar('SELECT count(*) FROM candidates WHERE decision="préparé"') == 0
        assert db.scalar("SELECT count(*) FROM decisions") > 0
        assert db.scalar("SELECT count(*) FROM event_outbox WHERE dispatched_at IS NOT NULL") == 0
        db.close()
        shadow = __import__("sqlite3").connect(tmp_path / "app/supervisor/macro/macro-shadow.sqlite")
        assert shadow.execute("SELECT count(*) FROM decision_contexts").fetchone()[0] > 0
        shadow.close()
        before = snapshot(tmp_path / "app")
        facts = facts_from_data_dir(tmp_path / "app")
        rep = evaluate(facts, now=now[0], diagnostic=None)
        assert snapshot(tmp_path / "app") == before and snapshot(protocol) == frozen
        assert rep["verdict"] == "BLOCKED" and rep["states"]["windows"] == "Windows_NON_TESTÉ"
        assert not next(c for c in rep["checks"] if c["key"] == "forex_factory")["blocking"]
        assert rep["supervisor"]["origin"] == "synthetic_fixture"  # never reclassified as public
    finally:
        state.close()


@pytest.mark.parametrize("phase", ["collected", "observed", "state_committed"])
def test_process_crash_restarts_one_cycle_without_duplicate_paper_fills(tmp_path, phase):
    fixture.setup(tmp_path)
    p = subprocess.run([sys.executable, __file__, str(tmp_path), phase], capture_output=True, text=True, timeout=45)
    assert p.returncode == 77, p.stderr
    db = Database(tmp_path / "app/sentinelle.db")
    first = db.query("SELECT * FROM fills")
    db.close()
    state = State(tmp_path / "app/supervisor")
    backend = RealFixtureBackend(tmp_path, lambda: START)
    observer = Supervisor(plan(), state, backend, clock=lambda: START)
    try:
        observer.run(max_ticks=1)
        db = Database(tmp_path / "app/sentinelle.db")
        after = db.query("SELECT * FROM fills")
        db.close()
        assert after
        if first:
            assert first == after
        observer.run(max_ticks=1)
        db = Database(tmp_path / "app/sentinelle.db")
        assert db.query("SELECT * FROM fills") == after
        assert db.scalar('SELECT count(*) FROM candidates WHERE decision="préparé"') == 0
        db.close()
        with instance_lock(state.folder):
            pass  # the dead child did not leave a stale lock
    finally:
        state.close()


def test_concurrent_process_cannot_acquire_installation_lock(tmp_path):
    with instance_lock(tmp_path):
        p = subprocess.run(
            [
                sys.executable,
                "-c",
                "from sentinelle.services.supervisor import instance_lock; import sys\nwith instance_lock(sys.argv[1]): pass",
                str(tmp_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert p.returncode != 0 and "already owns" in p.stderr
    with instance_lock(tmp_path):
        pass


def test_stale_prepared_forecast_is_finalized_as_refusal_without_late_order(tmp_path):
    from tools.observe import expire_intents
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource

    protocol = fixture.setup(tmp_path)
    fixture.capture(tmp_path)
    # #32 crash injection commits T1, then exits inside the fill transaction (T2 rolls back).
    p = subprocess.run(
        [sys.executable, str(ROOT / "tests/test_observation_e2e.py"), str(tmp_path), "fill"], capture_output=True, text=True, timeout=45
    )
    assert p.returncode == 77, p.stderr
    cfg = json.loads((protocol / "settings.json").read_bytes())
    app = App(tmp_path / "app", start=False, source=SyntheticSource(fixture.SYMBOLS, days=2), settings_override=cfg)
    try:
        forecasts = app.db.query('SELECT id,p_win,model_version FROM candidates WHERE decision="préparé"')
        assert forecasts
        expire_intents(app, START + 300)
        expire_intents(app, START + 300)
        assert app.db.scalar("SELECT count(*) FROM fills") == 0
        for f in forecasts:
            row = app.db.one("SELECT * FROM candidates WHERE id=?", (f["id"],))
            assert row["decision"] == "refusé" and row["p_win"] == f["p_win"] and row["model_version"] == f["model_version"]
        assert app.db.scalar("SELECT count(*) FROM decisions") == len(forecasts)
    finally:
        app.close()


def child(root, phase):
    state = State(root / "app/supervisor")
    state.set("plan", plan())
    backend = RealFixtureBackend(root, lambda: START, crash=phase)
    if phase == "state_committed":
        original = state.event

        def crash(*a, **kw):
            original(*a, **kw)
            os._exit(77)

        state.event = crash
    Supervisor(plan(), state, backend, clock=lambda: START).run(max_ticks=1)


if __name__ == "__main__":
    child(Path(sys.argv[1]), sys.argv[2])


def test_public_backend_collect_observe_protect_and_optional_official_poll_use_mock_http(tmp_path, monkeypatch):
    from tools.public_capture import PublicCapture
    from tools.observe import protect

    protocol = fixture.setup(tmp_path, real_model=True)
    target = tmp_path / "launch.json"
    prepare(tmp_path / "app", protocol, target)
    state = State(tmp_path / "app/supervisor")
    backend = supervise.PublicBackend(target, state)
    backend.client.close()
    backend.client = fixture.public_client()
    # This entire input is artificial. Public production adapter is exercised against MockTransport.
    monkeypatch.setattr(
        "tools.public_capture.PublicCapture", lambda path, client, **kw: PublicCapture(path, client, clock=lambda: START, **kw)
    )
    monkeypatch.setattr("tools.observe.protect", lambda data, root, quotes: protect(data, root, quotes, clock=lambda: START))
    monkeypatch.setattr(supervise.time, "time", lambda: START)
    calls = []
    monkeypatch.setattr("sentinelle.news.shadow.sources.Collector.fetch_once", lambda self, source: calls.append(source))
    try:
        manifest = backend.collect(state.folder / "fixture-capture")
        assert manifest["end_ms"] == fixture.END
        with patch("tools.prospective.datetime", fixture.FrozenDate):
            report = backend.observe(state.folder / "fixture-capture")
        assert report["steps_complete"] and report["performance"]["capital"]["initial"] == 30
        assert backend.protect()["maintenance_only"]
        assert state.get("last_quotes")["sha"]
        for _ in range(5):
            backend.macro()
        assert calls == ["fed", "bls", "bea", "ecb", "eurostat"] and "forex_factory" not in calls
    finally:
        backend.close()
        state.close()


def test_protective_429_cooldown_survives_restart_without_repeated_requests(tmp_path, monkeypatch):
    from tools.public_capture import PublicCapture
    from tools.observe import protect
    import httpx

    protocol = fixture.setup(tmp_path, real_model=True)
    fixture.capture(tmp_path)
    target = tmp_path / "launch.json"
    prepare(tmp_path / "app", protocol, target)
    state = State(tmp_path / "app/supervisor")
    state.set("latest_capture", str(tmp_path / "capture"))
    backend = supervise.PublicBackend(target, state)
    backend.client.close()
    calls = []

    def rate_limited(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={"Retry-After": "300"}, json={"fixture": "rate limited"})

    backend.client = httpx.Client(transport=httpx.MockTransport(rate_limited))
    monkeypatch.setattr(
        "tools.public_capture.PublicCapture", lambda path, client, **kw: PublicCapture(path, client, clock=lambda: START, **kw)
    )
    monkeypatch.setattr("tools.observe.protect", lambda data, root, quotes: protect(data, root, quotes, clock=lambda: START))
    monkeypatch.setattr(supervise.time, "time", lambda: START)
    try:
        with pytest.raises(RuntimeError, match="429"):
            backend.protect()
        assert state.get("quote_next_at") == START + 300
        state.close()
        state = State(tmp_path / "app/supervisor")
        backend.state = state
        with pytest.raises(ValueError, match="backoff"):
            backend.protect()
        assert len(calls) == 1 and state.get("last_quotes") is None
    finally:
        backend.close()
        state.close()


def test_preflight_api_with_supervisor_state_is_authenticated_read_only_and_never_fetches(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sentinelle.api.server import create_api
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource
    import httpx

    fixture.setup(tmp_path, real_model=True)
    state = State(tmp_path / "app/supervisor")
    state.set("plan", plan())
    state.set("status", "stopped")
    cfg = json.loads((ROOT / "research/prospective-v2/settings.json").read_bytes())
    app = App(tmp_path / "app", start=False, source=SyntheticSource(fixture.SYMBOLS, days=2), settings_override=cfg)

    def forbidden(*a, **kw):
        raise AssertionError("no fetch in preflight")

    monkeypatch.setattr(httpx.Client, "stream", forbidden)

    async def direct_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(app, "acall", direct_call)
    try:
        with TestClient(create_api(app)) as client:
            assert client.get("/api/observation/preflight").status_code == 401
            assert (
                client.post(
                    "/api/auth/setup", json={"password": "supervisor-api-fixture-secret"}, headers={"X-Sentinelle": "1"}
                ).status_code
                == 200
            )
            # Older sessions are checked without sliding-expiration writes on this read-only route.
            app.db.execute("UPDATE sessions SET last_seen=last_seen-120")
            before = snapshot(tmp_path / "app")
            response = client.get("/api/observation/preflight")
            assert response.status_code == 200
            report = response.json()
            assert report["verdict"] == "BLOCKED" and snapshot(tmp_path / "app") == before
            assert report["states"]["continuous"] == "service_continu_non_validé"
            assert report["states"]["windows"] == "Windows_NON_TESTÉ"
    finally:
        app.close()
        state.close()


def test_continuous_plan_cannot_substitute_an_experimental_fixture_model(tmp_path):
    protocol = fixture.setup(tmp_path)
    with pytest.raises(ValueError, match="unchanged registered v2"):
        prepare(tmp_path / "app", protocol, tmp_path / "forbidden.json")
    assert not (tmp_path / "forbidden.json").exists()


def test_changed_launch_plan_is_rejected_between_ticks(tmp_path, monkeypatch):
    protocol = fixture.setup(tmp_path, real_model=True)
    path = tmp_path / "plan.json"
    p = prepare(tmp_path / "app", protocol, path)
    state = State(tmp_path / "app/supervisor")
    backend = supervise.PublicBackend(path, state)
    try:
        monkeypatch.setattr(supervise, "load_plan", lambda _: dict(p, poll_seconds=30))
        with pytest.raises(ValueError, match="plan changed"):
            backend.validate()
    finally:
        backend.close()
        state.close()
