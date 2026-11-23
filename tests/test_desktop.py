"""Application de bureau (chantier Interface V1) : serveur sur 127.0.0.1, ouvert le temps de la fenêtre, arrêté avec elle.

Ces tests ne prouvent pas que la fenêtre s'affiche sous Windows 11 : seule une validation sur le poste du propriétaire le
peut (SPEC.md). Ils vérifient ce qui se contrôle partout, Windows compris en CI :
- le choix du port et l'écoute sur 127.0.0.1 seulement ;
- le démarrage, puis l'arrêt et la libération du port à la fermeture de la fenêtre ;
- un échec de démarrage signalé, pas masqué ;
- l'ordre des fenêtres (pywebview, Edge en mode application, navigateur) ;
- le refus de ``--remote`` ;
- un démarrage puis un arrêt complets de la vraie application, avec une base jetable.
"""

import os
import socket
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

import pytest
from fastapi import FastAPI

from sentinelle import desktop

ROOT = Path(__file__).resolve().parents[1]


def tiny_api():
    api = FastAPI()

    @api.get("/api/health")
    def health():
        return {"ok": True}

    @api.get("/")
    def index():
        return {"cockpit": True}

    return api


def stopped(port):
    """Nothing listens on the port any more (the closed connections may linger in TIME_WAIT: binding is no proof)."""
    with socket.socket() as s:
        s.settimeout(2)
        try:
            s.connect((desktop.LOOPBACK, port))
        except OSError:
            return True
        return False


def test_the_requested_port_is_used_when_free_and_another_one_otherwise():
    port = desktop.pick_port(0)
    assert desktop.pick_port(port) == port
    with socket.socket() as busy:
        busy.bind((desktop.LOOPBACK, port))
        busy.listen()
        other = desktop.pick_port(port)
    assert other != port and other > 0


def test_the_server_listens_on_loopback_while_the_window_is_open_and_stops_when_it_closes(tmp_path):
    port = desktop.pick_port(0)
    seen = {}

    def window(url, profile_dir):
        seen["url"], seen["profile"] = url, profile_dir
        with urllib.request.urlopen(url + "api/health", timeout=5) as r:
            seen["health"] = r.status
        return "test"

    assert desktop.serve_desktop(tiny_api(), port, tmp_path / "fenetre", opener=window) == "test"
    assert seen == {"url": f"http://127.0.0.1:{port}/", "profile": tmp_path / "fenetre", "health": 200}
    assert stopped(port)  # the window closed: the server stopped listening


def test_a_server_that_cannot_start_is_reported_and_no_window_opens(tmp_path):
    with socket.socket() as busy:
        busy.bind((desktop.LOOPBACK, 0))
        busy.listen()
        port = busy.getsockname()[1]
        opened = []
        with pytest.raises(RuntimeError, match="n'a pas démarré"):
            desktop.serve_desktop(tiny_api(), port, tmp_path, opener=lambda *a: opened.append(a), timeout=10)
    assert opened == []


def test_the_window_is_pywebview_then_edge_in_app_mode_then_the_browser(monkeypatch, tmp_path):
    calls = []
    fake = types.SimpleNamespace(create_window=lambda *a, **k: calls.append(("create", a, k)), start=lambda: calls.append(("start",)))
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert desktop.open_window("http://127.0.0.1:9/", tmp_path) == "pywebview"
    assert calls[0][1] == ("Sentinelle", "http://127.0.0.1:9/") and calls[-1] == ("start",)

    monkeypatch.setitem(sys.modules, "webview", None)  # not installed
    monkeypatch.setattr(desktop, "edge_path", lambda: "msedge.exe")
    ran = []
    monkeypatch.setattr(desktop.subprocess, "run", lambda args, **k: ran.append(args))
    assert desktop.open_window("http://127.0.0.1:9/", tmp_path / "profil") == "edge-app"
    assert ran[0][0] == "msedge.exe" and "--app=http://127.0.0.1:9/" in ran[0]
    assert f"--user-data-dir={tmp_path / 'profil'}" in ran[0]  # a dedicated profile: the window owns its process

    monkeypatch.setattr(desktop, "edge_path", lambda: None)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr(desktop.time, "sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt))
    assert desktop.open_window("http://127.0.0.1:9/", tmp_path) == "navigateur" and opened == ["http://127.0.0.1:9/"]


def test_the_desktop_never_exposes_the_api_on_the_network():
    r = subprocess.run([sys.executable, "-m", "sentinelle", "--desktop", "--remote"], cwd=ROOT, capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 2 and "--remote" in r.stderr


def test_the_real_application_starts_then_stops_with_its_window(tmp_path):
    env = {**os.environ, "SENTINELLE_DESKTOP_SMOKE": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("SENTINELLE_DATA", None)
    port = desktop.pick_port(0)
    r = subprocess.run([sys.executable, "-m", "sentinelle", "--desktop", "--demo", "--port", str(port), "--data", str(tmp_path / "data")],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    log = (tmp_path / "data" / "logs" / "sentinelle.log").read_text(encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr[-2000:]
    assert f"Application de bureau sur http://127.0.0.1:{port}/" in log
    assert "Fenêtre fermée (contrôle) : serveur local arrêté" in log
    assert stopped(port)
