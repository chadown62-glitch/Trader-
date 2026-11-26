from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import time

import pytest

from tests.test_cockpit_routes import H, api, app, client, wait_job


@pytest.mark.parametrize("days", [180, 2900])
def test_manual_call_joins_knowledge_job_from_training(client, app, monkeypatch, days):
    entered, release = Event(), Event()
    update = app.update_knowledge
    calls = []

    def pause_update(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(60)
        return update(*args, **kwargs)

    monkeypatch.setattr(app, "update_knowledge", pause_update)
    # Chaque paramétrage part de fiches absentes dans la base synthétique du module.
    app.db.execute("DELETE FROM knowledge")
    response = client.post("/api/ai/train", json={"days": days}, headers=H)
    assert response.status_code == 200, response.text
    train_id = response.json()["id"]
    try:
        assert entered.wait(60)
        [running] = app.jobs.running("knowledge")
        response = client.post("/api/knowledge/refresh", headers=H)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == running["id"]
        changed = client.put("/api/settings", headers=H, json={"ai": {"timeframe": "4h"}})
        assert changed.status_code == 200, changed.text
        try:
            conflict = client.post("/api/knowledge/refresh", headers=H)
            assert conflict.status_code == 400 and "réglages différents" in conflict.json()["detail"]
            assert [j["id"] for j in app.jobs.running("knowledge")] == [running["id"]]
        finally:
            assert client.put("/api/settings", headers=H, json={"ai": {"timeframe": "1h"}}).status_code == 200
    finally:
        release.set()
        assert wait_job(client, train_id)["status"] == "done"
        for job in app.jobs.running("knowledge"):
            wait_job(client, job["id"])
    completed = app.jobs.get(running["id"])
    assert completed["status"] == "done" and completed["result"]["updated"] > 0
    assert calls == [True]


def test_simultaneous_requests_start_one_worker_and_share_its_failure():
    from sentinelle.services.jobs import JobManager

    jobs = JobManager()
    aligned, release = Barrier(8), Event()
    calls = []

    def fail(_):
        calls.append(True)
        assert release.wait(10)
        raise ValueError("échec de la source synthétique")

    def request(_):
        aligned.wait(timeout=10)
        return jobs.submit("knowledge", "fiches", fail)["id"]

    try:
        with ThreadPoolExecutor(8) as pool:
            ids = list(pool.map(request, range(8)))
        assert len(set(ids)) == 1
    finally:
        release.set()
    # Le second appel après échec doit pouvoir démarrer un calcul neuf.
    deadline = time.monotonic() + 10
    while jobs.running("knowledge") and time.monotonic() < deadline:
        time.sleep(0.01)
    failed = jobs.get(ids[0])
    assert calls == [True] and failed["status"] == "error" and "source synthétique" in failed["error"]
    retry = jobs.submit("knowledge", "fiches", lambda _: {"updated": 1}, inline=True)
    assert retry["id"] != ids[0] and retry["status"] == "done"
