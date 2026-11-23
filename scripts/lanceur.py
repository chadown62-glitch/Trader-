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
  --bureau        application de bureau (bureau.bat) : fenêtre dédiée, raccourci « Sentinelle » sur le Bureau
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
WINDOW_LIB = "pywebview==5.4"  # fenêtre WebView2 légère ; à défaut, Edge en mode application (aucune dépendance)


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
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != req_hash():
        say("Installation des composants nécessaires (quelques minutes la première fois)…")
        subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], check=False)
        r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ)])
        if r.returncode != 0:
            say("L'installation a échoué. Vérifiez votre connexion internet puis relancez.")
            sys.exit(1)
        marker.write_text(req_hash(), encoding="utf-8")
        say("Composants installés.")
    return py


def ensure_window(py: Path) -> None:
    """Installe la bibliothèque de fenêtre dans l'environnement du projet si elle manque. Facultatif : en cas d'échec,
    l'application utilise Edge en mode application."""
    if subprocess.run([str(py), "-c", "import webview"], capture_output=True).returncode == 0:
        return
    say("Installation de la fenêtre de bureau (une seule fois)…")
    if subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", WINDOW_LIB]).returncode != 0:
        say("Fenêtre de bureau non installée : Sentinelle s'ouvrira dans une fenêtre d'Edge.")


def ensure_shortcut() -> None:
    """Crée une fois le raccourci « Sentinelle » du Bureau Windows, qui lance bureau.bat avec sa console réduite."""
    if os.name != "nt":
        return
    target = ROOT / "bureau.bat"
    script = ("$d=[Environment]::GetFolderPath('Desktop');$p=Join-Path $d 'Sentinelle.lnk';if(-not(Test-Path $p)){"
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
              f"$s.TargetPath='{target}';$s.WorkingDirectory='{ROOT}';$s.WindowStyle=7;"
              "$s.IconLocation=\"$env:SystemRoot\\System32\\shell32.dll,47\";$s.Description='Sentinelle (simulation)';$s.Save();"
              "Write-Output 'cree'}")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True)
    if "cree" in (r.stdout or ""):
        say("Raccourci « Sentinelle » créé sur le Bureau.")


def main(argv: list[str]) -> int:
    py = ensure_env()
    os.chdir(ROOT)
    if "--diagnostic" in argv:
        # 1) contrôle rapide de l'installation et des sources en ligne ;
        # 2) diagnostic complet à exécution finie (dossier temporaire, panel démarré puis arrêté,
        #    modèles gelés, lecture seule de vos données) avec verdict READY / BLOCKED.
        quick = subprocess.run([str(py), "-m", "sentinelle.diagnostic"]).returncode
        print()
        full = subprocess.run([str(py), "-m", "tools.diagnose_windows", "--reseau", "--rapport"]).returncode
        return quick or full
    if "--bureau" in argv:
        ensure_window(py)
        ensure_shortcut()
        say("Démarrage de l'application… la fenêtre Sentinelle va s'ouvrir. La fermer arrête Sentinelle.")
        args = [a for a in argv if a != "--bureau"]
        try:
            return subprocess.run([str(py), "-m", "sentinelle", "--desktop", *args]).returncode
        except KeyboardInterrupt:
            return 0
    say("Démarrage… le panel va s'ouvrir dans votre navigateur (http://127.0.0.1:8765).")
    say("Pour arrêter Sentinelle : fermez cette fenêtre ou appuyez sur Ctrl+C.")
    try:
        return subprocess.run([str(py), "-m", "sentinelle", *argv]).returncode
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
