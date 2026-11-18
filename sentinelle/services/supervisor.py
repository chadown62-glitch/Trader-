"""Continuous paper supervisor. No process starts on import; production requires a bound approval."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import uuid

from tools.validation.archive import digest, encoded, immutable, STEP_MS

PROJECT = Path(__file__).resolve().parents[2]
CODE = (
    "requirements.txt",
    "tools/supervise.py",
    "tools/observe.py",
    "tools/public_capture.py",
    "tools/prospective.py",
    "tools/diagnose_windows.py",
    "tools/collect_public.py",
    "tools/macro_shadow.py",
)
CONFIRM = "AUTORISER OBSERVATION PAPIER CONTINUE"


def code_hashes():
    paths = set(CODE)
    for folder, pattern in (("sentinelle", "*.py"), ("tools/validation", "*.py"), ("web", "*.js"), ("web", "*.css")):
        paths.update(p.relative_to(PROJECT).as_posix() for p in (PROJECT / folder).rglob(pattern))
    return {p: digest((PROJECT / p).read_bytes()) for p in sorted(paths)}


def prepare(data, root, output, *, now=None):
    from tools.prospective import check
    from tools.observe import existing_installation
    from sentinelle.preflight import verify_macro_shadow

    data, root = Path(data).resolve(), Path(root).resolve()
    p = check(root)
    if digest((root / "protocol.json").read_bytes()) != digest((PROJECT / "research/prospective-v2/protocol.json").read_bytes()):
        raise ValueError("Continuous launch supports only the unchanged registered v2 protocol")
    settings = json.loads((root / "settings.json").read_bytes())
    existing_installation(data, settings)
    macro = verify_macro_shadow(PROJECT / "research/macro-shadow-v3/protocol.json")
    if not macro["ok"]:
        raise ValueError("Frozen v3 integrity failure")
    plan = {
        "schema": 1,
        "kind": "continuous-paper-supervisor",
        "registered_at": time.time() if now is None else now,
        "data": str(data),
        "root": str(root),
        "v2_sha": digest((root / "protocol.json").read_bytes()),
        "v3_sha": macro["protocol_sha256"],
        "model_sha": p["model_sha256"],
        "settings_sha": p["settings_sha256"],
        "start": datetime.fromisoformat(p["start"]).timestamp(),
        "end": datetime.fromisoformat(p["end"]).timestamp(),
        "code": code_hashes(),
        "paper_only": True,
        "initial_eur": 30,
        "promotion_allowed": False,
        "poll_seconds": 15,
        "capture_budget_seconds": 110,
        "retry_seconds": 30,
        "max_attempts_per_bar": 4,
        "disk_limit_bytes": 3_000_000_000,
        "macro_optional": True,
        "launch_status": "PREPARED_NOT_STARTED",
        "windows_required": True,
    }
    immutable(Path(output), encoded(plan))
    immutable(Path(str(output) + ".sha256"), (digest(encoded(plan)) + "\n").encode())
    return plan


def load_plan(path):
    raw = Path(path).read_bytes()
    p = json.loads(raw)
    if digest(raw) != Path(str(path) + ".sha256").read_text().strip():
        raise ValueError("Supervisor plan checksum mismatch")
    if (
        p["schema"] != 1
        or p["kind"] != "continuous-paper-supervisor"
        or not p["paper_only"]
        or p["initial_eur"] != 30
        or p["promotion_allowed"]
        or not p["windows_required"]
    ):
        raise ValueError("Unsafe supervisor plan")
    if p["code"] != code_hashes():
        raise ValueError("Supervisor implementation changed; review a new launch plan")
    for key, low, high in (
        ("poll_seconds", 5, 60),
        ("capture_budget_seconds", 5, 120),
        ("retry_seconds", 15, 3600),
        ("max_attempts_per_bar", 1, 4),
        ("disk_limit_bytes", 1_000_000, 3_000_000_000),
    ):
        if not isinstance(p.get(key), (int, float)) or not low <= p[key] <= high:
            raise ValueError("Unsafe resource limit: " + key)
    from tools.prospective import check

    frozen = check(p["root"])
    if p["v2_sha"] != digest((PROJECT / "research/prospective-v2/protocol.json").read_bytes()):
        raise ValueError("Continuous launch supports only the unchanged registered v2 protocol")
    if (
        digest((Path(p["root"]) / "protocol.json").read_bytes()) != p["v2_sha"]
        or frozen["model_sha256"] != p["model_sha"]
        or frozen["settings_sha256"] != p["settings_sha"]
        or p["start"] != datetime.fromisoformat(frozen["start"]).timestamp()
        or p["end"] != datetime.fromisoformat(frozen["end"]).timestamp()
    ):
        raise ValueError("Frozen v2 replaced")
    from sentinelle.preflight import verify_macro_shadow

    macro = verify_macro_shadow(PROJECT / "research/macro-shadow-v3/protocol.json")
    if not macro["ok"] or macro["protocol_sha256"] != p["v3_sha"]:
        raise ValueError("Frozen v3 replaced")
    from tools.observe import existing_installation

    existing_installation(p["data"], json.loads((Path(p["root"]) / "settings.json").read_bytes()))
    return p


def approve(plan_path, diagnostic_path, output, confirmation):
    p = load_plan(plan_path)
    from tools.diagnose_windows import system_info

    d = json.loads(Path(diagnostic_path).read_bytes())
    if confirmation != CONFIRM:
        raise ValueError("Explicit operator confirmation required")
    if not system_info()["windows_actually_tested"]:
        raise ValueError("Real Windows 11 required for launch approval")
    if (
        not d.get("windows_actually_tested")
        or not d.get("installation", {}).get("ok")
        or d.get("installation", {}).get("not_verified")
        or d.get("supervisor_code") != p["code"]
        or Path(d.get("data_dir", "")).resolve() != Path(p["data"]).resolve()
    ):
        raise ValueError("Windows report does not validate this code and installation")
    receipt = {
        "schema": 1,
        "plan_sha": digest(Path(plan_path).read_bytes()),
        "windows_report_sha": digest(Path(diagnostic_path).read_bytes()),
        "diagnostic": str(Path(diagnostic_path).resolve()),
        "approved_at": time.time(),
        "confirmation": CONFIRM,
    }
    immutable(Path(output), encoded(receipt))
    return receipt


def authorize(plan_path, approval_path):
    p = load_plan(plan_path)
    receipt = json.loads(Path(approval_path).read_bytes())
    if receipt.get("plan_sha") != digest(Path(plan_path).read_bytes()) or receipt.get("confirmation") != CONFIRM:
        raise ValueError("Approval absent or bound to a different plan")
    from tools.diagnose_windows import system_info

    if not system_info()["windows_actually_tested"]:
        raise ValueError("Continuous launch requires Windows 11 validation")
    report = Path(receipt["diagnostic"])
    if digest(report.read_bytes()) != receipt["windows_report_sha"]:
        raise ValueError("Approved report changed")
    d = json.loads(report.read_bytes())
    if (
        not d.get("windows_actually_tested")
        or not d.get("installation", {}).get("ok")
        or d.get("installation", {}).get("not_verified")
        or d.get("supervisor_code") != p["code"]
        or Path(d.get("data_dir", "")).resolve() != Path(p["data"]).resolve()
    ):
        raise ValueError("Launch report no longer applicable")
    return p


class State:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "state.sqlite"
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS cycles(bucket INTEGER PRIMARY KEY,status TEXT,capture TEXT,attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,error TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts REAL,kind TEXT,payload TEXT);
        """)

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value, allow_nan=False)))

    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def event(self, at, kind, payload):
        self.db.execute("INSERT INTO events(ts,kind,payload) VALUES(?,?,?)", (at, kind, json.dumps(payload, allow_nan=False)))

    def close(self):
        self.db.close()


@contextmanager
def instance_lock(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "instance.lock").open("a+b") as f:
        f.seek(0)
        if not f.read(1):
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("A supervisor already owns this installation") from None
        try:
            yield
        finally:
            f.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class Supervisor:
    """Injectable finite tick primitive. Production callbacks never replace the model or settings."""

    def __init__(self, plan, state, backend, *, clock=time.time):
        self.p, self.state, self.backend, self.clock = plan, state, backend, clock
        self.stop_event = threading.Event()

    def tick(self):
        p, s, now = self.p, self.state, self.clock()
        if not math.isfinite(now):
            raise ValueError("Invalid UTC clock")
        previous = s.get("last_clock")
        if previous is not None and now < previous:
            s.event(now, "clock_backwards", {"previous": previous})
            raise ValueError("UTC clock moved backwards")
        s.set("last_clock", now)
        s.set("heartbeat", now)
        if s.get("stop_requested", False) or self.stop_event.is_set():
            s.set("status", "stopped")
            return False
        if now >= p["end"]:
            s.set("status", "period_ended")
            return False
        if now < p["start"]:
            s.set("status", "waiting_for_period")
            return True
        self.backend.validate()
        if sum(f.stat().st_size for f in s.folder.rglob("*") if f.is_file()) > p["disk_limit_bytes"]:
            s.set("status", "disk_limit")
            raise ValueError("Archive disk limit reached; no automatic deletion")
        bucket = int(now * 1000) // STEP_MS * STEP_MS
        cycle = s.db.execute("SELECT * FROM cycles WHERE bucket=?", (bucket,)).fetchone()
        # No catch-up predictions. History may be collected, but an old close never executes.
        if now - bucket / 1000 >= 3 and (
            not cycle or (cycle["status"] not in ("observed", "history_only", "exhausted") and now >= cycle["next_retry"])
        ):
            attempts = cycle["attempts"] if cycle else 0
            if attempts >= p["max_attempts_per_bar"]:
                s.db.execute("UPDATE cycles SET status='exhausted' WHERE bucket=?", (bucket,))
            else:
                capture = cycle["capture"] if cycle else str(s.folder / "captures" / str(bucket) / uuid.uuid4().hex)
                s.db.execute(
                    "INSERT INTO cycles VALUES(?,?,?,?,?,?) ON CONFLICT(bucket) DO UPDATE SET attempts=excluded.attempts,status=excluded.status",
                    (bucket, "collecting", capture, attempts + 1, 0, None),
                )
                try:
                    manifest = self.backend.collect(Path(capture))
                    if manifest["end_ms"] != bucket:
                        raise ValueError("Capture belongs to another UTC close")
                    age = self.clock() - bucket / 1000
                    if 3 <= age <= 180:
                        report = self.backend.observe(Path(capture))
                        if not report.get("steps_complete"):
                            raise ValueError("Observation not executable: " + str(report.get("reasons")))
                        status = "observed"
                        s.set("last_observation", {"at": self.clock(), "capture": capture, "report": report, "bucket": bucket})
                        s.set("operation_capability", "fresh_step_completed")
                        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
                        immutable(s.folder / "daily" / f"{day}-{digest(encoded(report))}.json", encoded(report))
                    else:
                        status = "history_only"
                        s.set("operation_capability", "history_only_no_late_prediction")
                    s.db.execute("UPDATE cycles SET status=?,error=NULL WHERE bucket=?", (status, bucket))
                    s.event(self.clock(), status, {"bucket": bucket, "capture": capture})
                except Exception as exc:
                    s.db.execute(
                        "UPDATE cycles SET status='retry',next_retry=?,error=? WHERE bucket=?",
                        (self.clock() + p["retry_seconds"], str(exc)[:400], bucket),
                    )
                    s.event(self.clock(), "collection_error", {"bucket": bucket, "error": type(exc).__name__})
        try:
            # Fresh quotes continue protecting existing paper positions between 4h closes.
            protection = self.backend.protect()
            if not protection or not protection.get("maintenance_only"):
                raise ValueError("No verified protective quotes")
            s.set("status", "monitoring")
            s.set("last_quote_ok", self.clock())
        except Exception as exc:
            s.set("status", "degraded")
            s.event(self.clock(), "monitor_error", {"error": type(exc).__name__})
        try:
            self.backend.macro()
        except Exception as exc:
            s.event(self.clock(), "optional_macro_error", {"error": type(exc).__name__})
        return True

    def run(self, *, max_ticks=1):
        if max_ticks is not None and not 1 <= max_ticks <= 10000:
            raise ValueError("Invalid finite tick limit")
        count = 0
        with instance_lock(self.state.folder):
            self.state.set("status", "starting")
            try:
                while max_ticks is None or count < max_ticks:
                    if not self.tick():
                        break
                    count += 1
                    if max_ticks is None or count < max_ticks:
                        self.stop_event.wait(self.p["poll_seconds"])
            finally:
                self.state.set("status", "stopped")
                self.state.set("heartbeat", self.clock())
        return count


def read_status(data, *, now=None):
    """Read a temporary copy including WAL. No sockets, fetch, mkdir, or writes to the installation."""
    from sentinelle.preflight import _read_only

    now = time.time() if now is None else now
    path = Path(data) / "supervisor/state.sqlite"
    if not path.is_file():
        return {"state": "service_continu_non_validé", "observed": 0, "active": False}
    try:
        with tempfile.TemporaryDirectory(prefix="sentinelle-supervisor-status-") as d:
            db = _read_only(path, Path(d))
            try:
                meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM meta")}
                event_count = db.execute("SELECT count(*) FROM events").fetchone()[0]
                cycles = [dict(r) for r in db.execute("SELECT * FROM cycles ORDER BY bucket")]
            finally:
                db.close()
        plan = meta.get("plan", {})
        first = math.ceil(plan.get("start", now) * 1000 / STEP_MS) * STEP_MS
        elapsed = max(0, int((min(now, plan.get("end", now) - 0.001) * 1000 - first) // STEP_MS) + 1)
        observed = sum(c["status"] == "observed" for c in cycles)
        validated = False
        if meta.get("plan_file") and meta.get("approval_file"):
            try:
                authorize(meta["plan_file"], meta["approval_file"])
                validated = True
            except (ValueError, OSError, KeyError):
                pass
        return {
            "state": "service_continu_validé_PC" if validated else "service_continu_non_validé",
            "launch_validated": validated,
            "status": meta.get("status"),
            "active": meta.get("status") in ("monitoring", "degraded") and 0 <= now - meta.get("heartbeat", 0) <= 45,
            "heartbeat": meta.get("heartbeat"),
            "observed": observed,
            "expected_closed_slots": elapsed,
            "operation_capability": meta.get("operation_capability", "no_fresh_step"),
            "history_only": sum(c["status"] == "history_only" for c in cycles),
            "cycle_attempts": sum(c["attempts"] for c in cycles),
            "journal_events": event_count,
            "missing_slots": max(0, elapsed - observed),
            "coverage_complete": elapsed > 0 and observed == elapsed,
            "last_observation": meta.get("last_observation"),
            "last_quote_ok": meta.get("last_quote_ok"),
            "origin": (meta.get("last_observation") or {}).get("report", {}).get("origin", "none"),
            "latest_capture": meta.get("latest_capture"),
            "last_quotes": meta.get("last_quotes"),
            "errors": sum(c["status"] in ("retry", "exhausted") for c in cycles),
        }
    except Exception as exc:
        return {"state": "service_continu_non_validé", "active": False, "observed": 0, "error": str(exc)[:250]}
