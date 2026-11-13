"""Manual entry point. Default action is read-only; no daemon, task or startup hook is installed."""

import os

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"

import argparse
import json
from pathlib import Path
import signal
import time
import uuid

import httpx

from sentinelle.services.supervisor import State, Supervisor, prepare, load_plan, approve, authorize, read_status
from tools.validation.archive import encoded, immutable


class PublicBackend:
    def __init__(self, plan_path, state):
        self.plan_path, self.state = Path(plan_path), state
        self.plan = load_plan(plan_path)
        self.client = httpx.Client()
        self.macro_index = 0

    def close(self):
        self.client.close()

    def validate(self):
        load_plan(self.plan_path)

    def collect(self, path):
        from tools.public_capture import PublicCapture

        c = PublicCapture(path, self.client, deadline=time.monotonic() + self.plan["capture_budget_seconds"])
        try:
            m = c.collect()
            self.state.set("latest_capture", str(path))
            return m
        finally:
            c.close()

    def observe(self, path):
        from tools.observe import step

        # Optional macro writes are isolated in macro(), after the committed price step.
        # A locked/unavailable sidecar must never change the frozen price-only policy.
        return step(self.plan["data"], self.plan["root"], path, execute=True, clock=time.time)

    def protect(self):
        from tools.public_capture import PublicCapture, load_capture
        from tools.observe import protect

        capture = self.state.get("latest_capture")
        if not capture:
            return
        if time.time() < self.state.get("quote_next_at", 0):
            raise ValueError("Protective quote backoff active; no stale execution")
        m, _ = load_capture(capture)
        directory = self.state.folder / "quotes" / uuid.uuid4().hex
        c = PublicCapture(directory, self.client, deadline=time.monotonic() + 20)
        try:
            server, received = c.get("time")
            if abs(server["serverTime"] / 1000 - received) > 5:
                raise ValueError("UTC clock skew")
            books = {}
            for symbol in m["markets"]:
                book, stamp = c.get("depth", {"symbol": symbol.replace("/", ""), "limit": 20})
                books[symbol] = {**book, "timestamp": stamp * 1000, "received_at": stamp, "source_event_time": None}
            evidence = [dict(r) for r in c.db.execute("SELECT key,raw_path,sha,received,latency FROM requests WHERE sha IS NOT NULL")]
            if any(r["latency"] > 5 for r in evidence):
                raise ValueError("Quote latency >5 seconds")
            quotes = {
                "origin": "public_preflight",
                "created_at": time.time(),
                "books": books,
                "markets": m["markets"],
                "http_evidence": evidence,
            }
            immutable(directory / "quotes.json", encoded(quotes))
            result = protect(self.plan["data"], self.plan["root"], quotes)
            self.state.set(
                "last_quotes",
                {
                    "at": time.time(),
                    "directory": str(directory),
                    "sha": __import__("hashlib").sha256((directory / "quotes.json").read_bytes()).hexdigest(),
                },
            )
            self.state.set("quote_next_at", 0)
            return result
        except Exception:
            backoff = c.db.execute("SELECT MAX(next_at) FROM requests").fetchone()[0] or 0
            self.state.set("quote_next_at", max(time.time() + 30, backoff))
            raise
        finally:
            c.close()

    def macro(self):
        from sentinelle.news.shadow.service import MacroShadow
        from sentinelle.news.shadow.sources import Collector
        from tools.macro_shadow import load_protocol

        path = Path(__file__).resolve().parents[1] / "research/macro-shadow-v3/protocol.json"
        p = json.loads(path.read_bytes())
        if not p["observation_start"] <= time.time() < p["observation_end"]:
            return
        load_protocol(path)
        macro = MacroShadow(self.state.folder / "macro")
        try:
            collector = Collector(macro.store, self.client)  # FF rights false; never requested below
            sources = ("fed", "bls", "bea", "ecb", "eurostat")
            source = sources[self.macro_index % len(sources)]
            self.macro_index += 1
            collector.fetch_once(source)  # at most one (20s) HTTP call per tick; persistent leases/backoff
            macro.annotate(Path(self.plan["data"]) / "sentinelle.db", path)
        finally:
            macro.stop()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "status", "approve", "run", "stop", "resume"])
    p.add_argument("--data", type=Path)
    p.add_argument("--root", type=Path, default=Path("research/prospective-v2"))
    p.add_argument("--plan", type=Path)
    p.add_argument("--approval", type=Path)
    p.add_argument("--windows-report", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--confirm")
    p.add_argument("--ticks", type=int, default=1)
    p.add_argument("--continuous", action="store_true")
    a = p.parse_args(argv)
    if a.action == "prepare":
        if not a.data or not a.output:
            p.error("--data and --output required")
        result = prepare(a.data, a.root, a.output)
    elif a.action == "status":
        if not a.data:
            p.error("--data required")
        result = read_status(a.data)
    elif a.action == "approve":
        if not all((a.plan, a.windows_report, a.output)):
            p.error("Plan, Windows report and output required")
        result = approve(a.plan, a.windows_report, a.output, a.confirm)
    else:
        if not a.plan:
            p.error("--plan required")
        plan = load_plan(a.plan)
        if a.action == "run":
            if not a.approval:
                p.error("Explicit bound approval required; no service has been started")
            plan = authorize(a.plan, a.approval)
            state = State(Path(plan["data"]) / "supervisor")
            state.set("plan", plan)
            state.set("plan_file", str(a.plan.resolve()))
            state.set("approval_file", str(a.approval.resolve()))
            backend = PublicBackend(a.plan, state)
            supervisor = Supervisor(plan, state, backend)

            def stop(*_):
                supervisor.stop_event.set()

            signal.signal(signal.SIGINT, stop)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, stop)
            try:
                ticks = supervisor.run(max_ticks=None if a.continuous else a.ticks)
                result = {"ticks": ticks, "status": "stopped", "permanent_service_installed": False}
            finally:
                backend.close()
                state.close()
        else:
            state = State(Path(plan["data"]) / "supervisor")
            try:
                state.set("stop_requested", a.action == "stop")
                result = {"stop_requested": a.action == "stop", "resume_starts_nothing": True}
            finally:
                state.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
