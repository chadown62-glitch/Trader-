"""Diagnostic de l'installation : `python -m sentinelle.diagnostic` (ou `lancer.bat --diagnostic`).

Vérifie, sans rien modifier : Python, composants, dossier de données, port du
panel, accès aux données de marché, Ollama (actualités), carte graphique.
"""

from __future__ import annotations

import importlib
import platform
import shutil
import socket
import subprocess
import sys
import time

from . import __version__
from .config import default_data_dir

OK, KO, INFO = "✅", "❌", "ℹ️"


def line(status: str, label: str, detail: str = "") -> None:
    print(f"{status} {label}" + (f" — {detail}" if detail else ""))


def check() -> int:
    problems = 0
    print(f"Diagnostic de Sentinelle {__version__}\n")
    v = sys.version_info
    if v >= (3, 11):
        line(OK, "Python", f"{v.major}.{v.minor}.{v.micro} ({platform.system()} {platform.release()})")
    else:
        problems += 1
        line(KO, "Python", f"{v.major}.{v.minor} : version 3.11 ou plus récente requise")
    for mod in ("fastapi", "uvicorn", "ccxt", "pandas", "numpy", "sklearn", "httpx", "feedparser", "joblib"):
        try:
            m = importlib.import_module(mod)
            line(OK, f"Composant {mod}", getattr(m, "__version__", ""))
        except Exception as exc:
            problems += 1
            line(KO, f"Composant {mod}", f"absent ({exc}). Relancez lancer.bat pour l'installer.")
    data = default_data_dir()
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".test-ecriture"
        probe.write_text("ok")
        probe.unlink()
        free = shutil.disk_usage(data).free / 1e9
        line(OK, "Dossier de données", f"{data} ({free:.1f} Go libres)")
    except Exception as exc:
        problems += 1
        line(KO, "Dossier de données", str(exc))
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8765))
        line(OK, "Port du panel (8765)", "libre")
    except OSError:
        line(INFO, "Port du panel (8765)", "déjà utilisé : Sentinelle est peut-être déjà lancé (ou utilisez --port 8770)")
    finally:
        s.close()
    try:
        from .market.sources import CcxtSource

        t = time.time()
        src = CcxtSource("binance", public_mirror=True)
        rows = src.fetch_ohlcv("BTC/USDC", "1m", limit=1)
        line(OK, "Données de marché Binance", f"BTC/USDC = {rows[-1][4]:,.2f} ({(time.time() - t) * 1000:.0f} ms)".replace(",", " "))
    except Exception as exc:
        problems += 1
        line(KO, "Données de marché Binance", f"injoignables ({str(exc)[:120]}). Vérifiez la connexion internet / le pare-feu.")
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        line(OK, "Ollama (IA locale pour les actualités)", "modèles : " + (", ".join(models) or "aucun (ollama pull qwen2.5:7b)"))
    except Exception:
        line(INFO, "Ollama (IA locale pour les actualités)", "non détecté : facultatif, voir docs/GUIDE.md")
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
            line(OK, "Carte graphique", out.stdout.strip() or "détectée")
        except Exception:
            line(INFO, "Carte graphique", "nvidia-smi présent mais illisible")
    else:
        line(INFO, "Carte graphique NVIDIA", "non détectée (utile seulement pour Ollama)")
    print()
    if problems:
        print(f"{problems} problème(s) à corriger avant d'utiliser Sentinelle.")
    else:
        print("Tout est prêt. Lancez Sentinelle avec lancer.bat.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(check())
