"""Routes appelées par des boutons du cockpit et jusqu'ici sans test (docs/interface/inventaire-actions.md).

Chaque test vérifie l'effet attendu sur le système, pas seulement le statut HTTP : une réponse 200 ne prouve pas qu'un
bouton fonctionne. Base SQLite jetable, marché synthétique, aucun accès réseau (transports simulés). Les services qui
dépendent d'un compte extérieur (Telegram, canaux de signaux) doivent refuser clairement, jamais simuler un succès.
"""

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.market.context import ContextService
from sentinelle.market.sources import SyntheticSource
from sentinelle.news.service import NewsService
from sentinelle.news.signals import SignalWatcher
from sentinelle.notify.telegram import TelegramService
from sentinelle.services.core import App

H = {"X-Sentinelle": "1"}
PASSWORD = "un-mot-de-passe-solide"
NEW_PASSWORD = "un-autre-mot-de-passe-solide"
WATCHLIST = ["BTC/USDC", "ETH/USDC", "SOL/USDC"]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    src = SyntheticSource(WATCHLIST, days=40)
    a = App(tmp_path_factory.mktemp("data"), source=src,
            settings_override={"market": {"source": "synthetic", "poll_seconds": 2, "history_days": 120},
                               "general": {"watchlist": WATCHLIST}, "ai": {"timeframe": "1h"}})
    assert a.engine.ready.wait(30)
    offline = httpx.MockTransport(lambda request: httpx.Response(503))  # aucun accès réseau
    # Comme python -m sentinelle : les services d'actualités, Telegram, contexte et signaux, avec leurs routes.
    a.extensions += [NewsService(a, transport=offline), TelegramService(a, transport=offline), ContextService(a, transport=offline),
                     SignalWatcher(a)]
    for e in a.extensions:
        e.start()
    yield a
    a.close()


@pytest.fixture(scope="module")
def api(app):
    return create_api(app)


@pytest.fixture(scope="module")
def client(api):
    with TestClient(api) as c:
        assert c.post("/api/auth/setup", json={"password": PASSWORD}, headers=H).status_code == 200
        yield c


def ext(app, name):
    return next(e for e in app.extensions if getattr(e, "name", None) == name)


def wait_job(client, job_id, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.5)
    raise AssertionError(f"tâche {job_id} toujours en cours après {timeout} s")


def test_candles_are_validated_and_returned_in_time_order(client):
    assert client.get("/api/candles", params={"symbol": "BTC/USDC", "tf": "2h"}).status_code == 400
    assert client.get("/api/candles", params={"symbol": "INCONNU/USDC"}).status_code == 404
    r = client.get("/api/candles", params={"symbol": "BTC/USDC", "tf": "1h", "limit": 50}).json()
    assert r["symbol"] == "BTC/USDC" and r["tf"] == "1h"
    ts = [c[0] for c in r["candles"]]
    assert 0 < len(ts) <= 50 and ts == sorted(set(ts))
    assert len(client.get("/api/candles", params={"symbol": "BTC/USDC", "limit": 1}).json()["candles"]) == 10  # au moins 10


def test_training_runs_once_at_a_time_and_reports_its_verdict_without_inventing_a_model(client):
    before = client.get("/api/ai/models").json()
    job = client.post("/api/ai/train", json={"days": 180}, headers=H).json()
    assert job["kind"] == "train" and job["status"] == "running"
    again = client.post("/api/ai/train", json={"days": 180}, headers=H)
    assert again.status_code == 400 and "déjà en cours" in again.json()["detail"]
    done = wait_job(client, job["id"])
    assert done["status"] == "done", done.get("error")
    # 40 jours de marché synthétique : trop peu pour entraîner. Le verdict le dit et aucun modèle n'est ajouté.
    assert done["result"]["verdict"] == "données insuffisantes"
    assert client.get("/api/ai/models").json() == before


def test_knowledge_refresh_fills_one_sheet_per_watched_crypto(client):
    job = client.post("/api/knowledge/refresh", headers=H).json()
    assert job["kind"] == "knowledge"
    done = wait_job(client, job["id"])
    assert done["status"] == "done", done.get("error")
    assert done["result"]["updated"] >= len(WATCHLIST)
    items = client.get("/api/knowledge").json()["items"]
    assert [i["symbol"] for i in items] == WATCHLIST and not any(i.get("missing") for i in items)


def test_news_refresh_starts_a_collection_now(client, app):
    requests = []
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><title>Flux</title>'
           f'<item><title>Bitcoin : annonce de test</title><link>https://flux.test/a</link><pubDate>'
           f'{time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())}</pubDate></item></channel></rss>')

    def handler(request):
        requests.append(str(request.url))
        if request.url.host == "flux.test":
            return httpx.Response(200, text=rss, headers={"Content-Type": "application/rss+xml"})
        return httpx.Response(503)  # IA locale absente : l'analyse attend, rien n'est inventé

    ext(app, "news").transport = httpx.MockTransport(handler)
    r = client.put("/api/settings", json={"news": {"enabled": True, "feeds": ["https://flux.test/rss"], "poll_minutes": 60}}, headers=H)
    assert r.status_code == 200, r.text
    end = time.time() + 40  # premier cycle après activation (le service vérifie son réglage toutes les 30 s)
    while time.time() < end and not any("flux.test" in u for u in requests):
        assert client.post("/api/news/refresh", headers=H).json() == {"ok": True}
        time.sleep(0.5)
    assert any("flux.test" in u for u in requests), "aucune collecte"
    requests.clear()
    assert client.post("/api/news/refresh", headers=H).json() == {"ok": True}  # cycle suivant prévu dans 60 min
    end = time.time() + 10
    while time.time() < end and not any("flux.test" in u for u in requests):
        time.sleep(0.2)
    assert any("flux.test" in u for u in requests), "« Analyser maintenant » n'a pas relancé la collecte"
    client.put("/api/settings", json={"news": {"enabled": False}}, headers=H)


def test_context_refresh_reports_unavailable_sources_and_never_invents_values(client, app):
    service = ext(app, "context")
    service.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    data = client.post("/api/context/refresh", headers=H).json()
    assert data["fear_greed"] is None and data["indices"] == {} and data["funding"] == {}
    assert {"fear_greed", "^GSPC", "funding:BTC"} <= set(data["errors"]) and data["updated"] > 0

    fng = {"data": [{"value": "23", "value_classification": "Extreme Fear", "timestamp": "1763856000"},
                    *({"value": "40", "value_classification": "Fear", "timestamp": "1763251200"} for _ in range(7))]}
    service.transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=fng) if request.url.host == "api.alternative.me" else httpx.Response(503))
    data = client.post("/api/context/refresh", headers=H).json()
    assert data["fear_greed"]["value"] == 23 and data["fear_greed"]["week_ago"] == 40  # valeur reçue, pas inventée
    assert "fear_greed" not in data["errors"]


def test_signals_resolve_settles_due_signals_and_reports_unknown_pairs(client, app):
    now = time.time()
    app.db.insert("signals", {"channel": "@test", "msg_id": 1, "ts": now - 100 * 3600, "parsed": 1, "symbol": "BTC",
                              "side": "long", "entry": None, "stop": 0.01, "targets": json.dumps([1e12])})
    app.db.insert("signals", {"channel": "@test", "msg_id": 2, "ts": now - 2 * 3600, "parsed": 1, "symbol": "ZZZ",
                              "side": "long", "entry": None, "stop": 1.0, "targets": json.dumps([2.0])})
    assert client.post("/api/signals/resolve", headers=H).json() == {"resolved": 1}
    rows = {r["symbol"]: r for r in client.get("/api/signals").json()["signals"]}
    assert rows["BTC"]["outcome"] == "durée" and rows["BTC"]["outcome_r"] is not None  # 72 h écoulées, ni stop ni objectif
    assert rows["ZZZ"]["outcome"] == "paire indisponible"
    assert client.post("/api/signals/resolve", headers=H).json() == {"resolved": 0}  # déjà évalués : rien de plus


def test_services_without_an_account_refuse_with_a_reason(client):
    r = client.post("/api/telegram/test", headers=H)
    assert r.status_code == 400 and "Envoi impossible" in r.json()["detail"]
    r = client.post("/api/signals/login", headers=H)
    assert r.status_code == 400 and "Numéro de téléphone manquant" in r.json()["detail"]


def test_password_change_keeps_the_old_one_on_error_and_revokes_other_sessions(client, api):
    other = TestClient(api)
    assert other.post("/api/auth/login", json={"password": PASSWORD}, headers=H).status_code == 200
    wrong = client.post("/api/auth/password", json={"current": "pas-le-bon-mot-de-passe", "new": NEW_PASSWORD}, headers=H)
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Mot de passe actuel incorrect"
    assert other.get("/api/overview").status_code == 200  # rien n'a changé

    assert client.post("/api/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}, headers=H).status_code == 200
    assert client.get("/api/overview").status_code == 200  # cette session reçoit un nouveau jeton
    assert other.get("/api/overview").status_code == 401  # les autres sessions sont fermées
    assert other.post("/api/auth/login", json={"password": PASSWORD}, headers=H).status_code == 401
    assert other.post("/api/auth/login", json={"password": NEW_PASSWORD}, headers=H).status_code == 200
