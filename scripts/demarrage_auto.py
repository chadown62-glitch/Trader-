"""Démarrage automatique de Sentinelle avec Windows (ouverture de session).

  py -3 scripts\\demarrage_auto.py --activer
  py -3 scripts\\demarrage_auto.py --desactiver

Crée (ou supprime) un petit fichier dans le dossier « Démarrage » de Windows.
Sentinelle se lance alors réduit, sans ouvrir le navigateur.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "Sentinelle.bat"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("Cette commande ne fonctionne que sous Windows.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def script_content(root: Path) -> str:
    return "\r\n".join([
        "@echo off",
        "rem Démarrage automatique de Sentinelle (créé par scripts\\demarrage_auto.py)",
        f'cd /d "{root}"',
        'start "Sentinelle" /min lancer.bat --no-browser',
        "",
    ])


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("--activer", "--desactiver"):
        print(__doc__)
        return 1
    target = startup_dir() / NAME
    if argv[0] == "--activer":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script_content(ROOT), encoding="utf-8")
        print(f"Démarrage automatique activé : {target}")
    else:
        if target.exists():
            target.unlink()
        print("Démarrage automatique désactivé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
