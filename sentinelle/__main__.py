"""Lancement de Sentinelle : `python -m sentinelle`.

Options :
  --demo          marché simulé hors-ligne (aucune connexion internet nécessaire)
  --port 8765     port du panel
  --remote        accepte les connexions depuis le réseau (téléphone via Tailscale, etc.)
  --data DOSSIER  dossier des données
  --no-browser    ne pas ouvrir le navigateur automatiquement
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .config import default_data_dir


def setup_logging(data_dir: Path) -> None:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(logs / "sentinelle.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [file_handler, console]
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinelle", description="Sentinelle : bot de trading crypto prudent")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--remote", action="store_true", help="accepter les connexions depuis le réseau")
    parser.add_argument("--demo", action="store_true", help="marché simulé (hors-ligne)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    data_dir = (args.data or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(data_dir)
    log = logging.getLogger("sentinelle")
    log.info("Sentinelle %s — données : %s", __version__, data_dir)

    import uvicorn

    from .api.server import create_api
    from .news.service import NewsService
    from .notify.telegram import TelegramService
    from .services.core import App

    override = {"market": {"source": "synthetic"}} if args.demo else None
    app = App(data_dir, settings_override=override)
    app.extensions.append(NewsService(app))
    app.extensions.append(TelegramService(app))
    for ext in app.extensions:
        ext.start()
    api = create_api(app, remote_access=args.remote)
    host = "0.0.0.0" if args.remote else "127.0.0.1"
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(2.0), webbrowser.open(url)), daemon=True).start()
    log.info("Panel disponible sur %s", url)
    try:
        uvicorn.run(api, host=host, port=args.port, log_level="warning", ws="auto")
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
