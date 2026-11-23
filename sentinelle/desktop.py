"""Application de bureau : le cockpit dans une fenêtre dédiée, le serveur local démarré et arrêté avec elle.

    python -m sentinelle --desktop        (Windows 11 : raccourci « Sentinelle » du Bureau, créé par bureau.bat)

- Le serveur n'écoute que sur 127.0.0.1, sur le port demandé s'il est libre, sinon sur un port libre. ``--remote`` est
  refusé : l'application de bureau n'expose jamais l'API sur le réseau.
- La fenêtre utilise pywebview (bibliothèque légère, moteur WebView2 de Windows) s'il est installé. À défaut, elle
  utilise Microsoft Edge en mode application (même moteur, intégré à Windows 11, profil dédié). En dernier recours, elle
  ouvre le navigateur par défaut.
- Fermer la fenêtre arrête proprement le serveur ; l'appelant ferme ensuite la base.

Aucune règle métier ici : la fenêtre affiche le cockpit servi par l'API locale.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

LOOPBACK = "127.0.0.1"
WINDOW = {"title": "Sentinelle", "width": 1440, "height": 900}


def pick_port(preferred: int = 8765) -> int:
    """Le port demandé s'il est libre sur 127.0.0.1, sinon un port libre choisi par le système."""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((LOOPBACK, port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("aucun port local libre")


def wait_ready(url: str, timeout: float = 60.0, alive=lambda: True) -> bool:
    """Vrai dès que ``/api/health`` répond 200 ; faux si le délai passe ou si le serveur s'arrête avant."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and alive():
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def edge_path() -> str | None:
    """Microsoft Edge (installé avec Windows 11), ou ``None``."""
    candidates = [Path(os.environ.get(var, default)) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                  for var, default in (("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), ("PROGRAMFILES", r"C:\Program Files"))]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("msedge")


def open_window(url: str, profile_dir: Path) -> str:
    """Ouvre le cockpit et ne rend la main qu'à la fermeture de la fenêtre. Renvoie le moyen utilisé."""
    try:
        import webview  # pywebview
    except ImportError:
        webview = None
    if webview is not None:
        webview.create_window(WINDOW["title"], url, width=WINDOW["width"], height=WINDOW["height"], min_size=(380, 600))
        webview.start()
        return "pywebview"
    edge = edge_path()
    if edge:
        profile_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([edge, f"--app={url}", f"--user-data-dir={profile_dir}", "--no-first-run", "--no-default-browser-check",
                        f"--window-size={WINDOW['width']},{WINDOW['height']}"], check=False)
        return "edge-app"
    import webbrowser

    webbrowser.open(url)
    print("Sentinelle est ouvert dans votre navigateur. Pour l'arrêter : fermez cette fenêtre ou appuyez sur Ctrl+C.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return "navigateur"


def smoke_window(url: str, profile_dir: Path) -> str:
    """Fenêtre de contrôle (tests, ``SENTINELLE_DESKTOP_SMOKE=1``) : vérifie que le cockpit et la santé répondent, puis se
    ferme aussitôt, comme si l'utilisateur fermait la fenêtre."""
    for path in ("", "api/health"):
        with urllib.request.urlopen(url + path, timeout=10) as r:
            if r.status != 200:
                raise RuntimeError(f"{url + path} : statut {r.status}")
    return "contrôle"


def serve_desktop(api, port: int, profile_dir: Path, opener=open_window, timeout: float = 60.0) -> str:
    """Sert ``api`` sur 127.0.0.1:``port`` le temps que ``opener`` garde la fenêtre ouverte, puis arrête le serveur."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(api, host=LOOPBACK, port=port, log_level="warning", ws="auto"))
    thread = threading.Thread(target=server.run, name="sentinelle-api", daemon=True)
    thread.start()
    url = f"http://{LOOPBACK}:{port}/"
    try:
        if not wait_ready(url, timeout, alive=thread.is_alive):
            raise RuntimeError(f"le serveur local n'a pas démarré sur {url}")
        return opener(url, profile_dir)
    finally:
        server.should_exit = True  # fin propre : requêtes en cours terminées, connexions fermées
        thread.join(timeout=15)
