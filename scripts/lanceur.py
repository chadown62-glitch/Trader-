"""Lanceur de Sentinelle (Windows, Mac, Linux).

1. vérifie la version de Python ;
2. crée l'environnement Python du projet (.venv) au premier lancement ;
3. installe ou met à jour les dépendances si besoin ;
4. démarre Sentinelle et ouvre le panel dans le navigateur.

Usage : lancer.bat (Windows) ou ./lancer.sh (Mac/Linux), options :
  --demo          marché simulé hors-ligne
  --diagnostic    vérifie l'installation sans démarrer
  --remote        accès depuis le téléphone (réseau privé type Tailscale)
  --port 8765
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
REQ = ROOT / "requirements.txt"
MIN_PY = (3, 11)


def say(msg: str) -> None:
    print(f"[Sentinelle] {msg}", flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def req_hash() -> str:
    return hashlib.sha256(REQ.read_bytes()).hexdigest()[:16]


def ensure_env() -> Path:
    if sys.version_info < MIN_PY:
        say(f"Python {sys.version.split()[0]} est trop ancien : installez Python 3.12 (https://www.python.org/downloads/).")
        sys.exit(1)
    py = venv_python()
    if not py.exists():
        say("Premier lancement : création de l'environnement Python (une seule fois)…")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    marker = VENV / ".dependances"
    if not marker.exists() or marker.read_text().strip() != req_hash():
        say("Installation des composants nécessaires (quelques minutes la première fois)…")
        subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], check=False)
        r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)])
        if r.returncode != 0:
            say("L'installation a échoué. Vérifiez votre connexion internet puis relancez.")
            sys.exit(1)
        marker.write_text(req_hash())
        say("Composants installés.")
    return py


def main(argv: list[str]) -> int:
    py = ensure_env()
    os.chdir(ROOT)
    if "--diagnostic" in argv:
        return subprocess.run([str(py), "-m", "sentinelle.diagnostic"]).returncode
    say("Démarrage… le panel va s'ouvrir dans votre navigateur (http://127.0.0.1:8765).")
    say("Pour arrêter Sentinelle : fermez cette fenêtre ou appuyez sur Ctrl+C.")
    try:
        return subprocess.run([str(py), "-m", "sentinelle", *argv]).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
