"""Tâches longues en arrière-plan (entraînement, backtest, préparation des données)."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from typing import Callable

log = logging.getLogger("sentinelle.jobs")


class JobManager:
    def __init__(self, bus=None, max_kept: int = 30):
        self.bus = bus
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.max_kept = max_kept

    def running(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            return [dict(j) for j in self.jobs.values() if j["status"] == "running" and (kind is None or j["kind"] == kind)]

    def submit(self, kind: str, label: str, fn: Callable[[Callable[[float, str], None]], dict]) -> dict:
        if kind in ("train", "backtest") and self.running(kind):
            raise RuntimeError("Une tâche de ce type est déjà en cours")
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "kind": kind, "label": label, "status": "running", "progress": 0.0, "message": "Démarrage…",
               "started_at": time.time(), "finished_at": None, "result": None, "error": None}
        with self._lock:
            self.jobs[jid] = job
            self._prune()

        def progress(x: float, msg: str) -> None:
            with self._lock:
                job["progress"] = round(float(max(0.0, min(1.0, x))), 3)
                job["message"] = msg
            self._publish(job)

        def run() -> None:
            try:
                res = fn(progress)
                with self._lock:
                    job.update(status="done", result=res, progress=1.0, message="Terminé", finished_at=time.time())
            except Exception as exc:
                log.exception("Échec de la tâche %s", label)
                with self._lock:
                    job.update(status="error", error=str(exc)[:500], message="Échec", finished_at=time.time(),
                               trace=traceback.format_exc()[-2000:])
            self._publish(job)

        threading.Thread(target=run, name=f"job-{kind}-{jid}", daemon=True).start()
        self._publish(job)
        return dict(job)

    def get(self, jid: str) -> dict | None:
        with self._lock:
            j = self.jobs.get(jid)
            return dict(j) if j else None

    def list(self) -> list[dict]:
        with self._lock:
            return [{k: v for k, v in j.items() if k not in ("result", "trace")} for j in sorted(self.jobs.values(), key=lambda j: -j["started_at"])]

    def _prune(self) -> None:
        done = sorted((j for j in self.jobs.values() if j["status"] != "running"), key=lambda j: j["started_at"])
        while len(self.jobs) > self.max_kept and done:
            self.jobs.pop(done.pop(0)["id"], None)

    def _publish(self, job: dict) -> None:
        if self.bus:
            self.bus.publish("job", {k: v for k, v in job.items() if k not in ("result", "trace")})
