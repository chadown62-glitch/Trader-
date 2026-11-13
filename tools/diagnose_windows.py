"""Diagnostic de l'installation et pré-vol de l'observation (Windows 11, Mac, Linux).

Usage (depuis le dossier de TRADER AI) :
    python -m tools.diagnose_windows                  # hors ligne
    python -m tools.diagnose_windows --reseau         # + prix publics (lecture seule, sans compte)
    python -m tools.diagnose_windows --reseau --rapport   # + rapport JSON dans rapports/

Exécution finie : chaque étape a un délai maximal ; aucun service n'est laissé en
marche. Tout ce qui s'écrit va dans un dossier temporaire supprimé à la fin.
L'installation de l'utilisateur (dossier data) est seulement lue, en lecture
seule, et son empreinte est comparée avant/après pour prouver qu'elle n'a pas
changé. Aucune clé, aucun compte, aucun ordre réel.

Un diagnostic exécuté ailleurs que sur Windows 11 ne valide pas Windows : le
rapport l'indique (windows_actually_tested = false).

Codes de sortie : 0 installation fonctionnelle ; 1 problème d'installation ;
3 avec --strict quand l'observation est BLOCKED.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # permet aussi `python tools/diagnose_windows.py`
    sys.path.insert(0, str(ROOT))

DEPENDENCIES = ("fastapi", "uvicorn", "websockets", "httpx", "ccxt", "numpy", "pandas", "sklearn", "joblib", "feedparser")
PANEL_TIMEOUT = 90.0
STOP_TIMEOUT = 20.0
PASSWORD = "diagnostic-jetable-123"  # mot de passe du panel TEMPORAIRE, supprimé avec son dossier


def _step(key, label, status, detail, started=None, **data):
    out = {"key": key, "label": label, "status": status, "detail": detail}
    if started is not None:
        out["seconds"] = round(time.monotonic() - started, 2)
    if data:
        out["data"] = data
    return out


# ---------------------------------------------------------------------------
# Système
# ---------------------------------------------------------------------------


def system_info() -> dict:
    build = None
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            build = int(platform.version().split(".")[-1])
    return {"platform": platform.platform(), "system": platform.system(), "release": platform.release(),
            "windows_build": build, "python": sys.version.split()[0], "executable": sys.executable,
            "machine": platform.machine(), "sqlite": sqlite3.sqlite_version,
            # Windows 11 = Windows NT 10.0, build 22000 ou plus
            "windows_actually_tested": sys.platform == "win32" and build is not None and build >= 22000}


def check_python() -> dict:
    v = sys.version_info
    if v < (3, 11):
        return _step("python", "Python", "fail", f"Python {v.major}.{v.minor} : la version 3.11 ou plus récente est requise (3.12 recommandée).")
    note = "" if v[:2] == (3, 12) else " (fonctionne ; la version testée en continu est la 3.12)"
    return _step("python", "Python", "ok", f"Python {v.major}.{v.minor}.{v.micro}{note}")


def check_dependencies() -> dict:
    t = time.monotonic()
    found, missing = {}, []
    for name in DEPENDENCIES:
        try:
            mod = importlib.import_module(name)
            found[name] = getattr(mod, "__version__", "?")
        except Exception as exc:
            missing.append(f"{name} ({type(exc).__name__})")
    if missing:
        return _step("dependencies", "Composants Python", "fail",
                     "Absents ou cassés : " + ", ".join(missing) + ". Relancez lancer.bat pour les installer.", t, versions=found)
    return _step("dependencies", "Composants Python", "ok", ", ".join(f"{k} {v}" for k, v in found.items()), t, versions=found)


# ---------------------------------------------------------------------------
# Base SQLite et portefeuille, dans un dossier temporaire
# ---------------------------------------------------------------------------


def check_sqlite_wallet(work: Path) -> dict:
    t = time.monotonic()
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.core import App

    root = work / "sqlite"
    results = []
    try:
        for _ in range(2):  # 2e passage = redémarrage sur la même base
            app = App(root, start=False, source=SyntheticSource(["BTC/EUR"], days=2),
                      settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}})
            try:
                acc = app.main_account()
                results.append(bool(acc) and acc["initial_balance"] == 30.0 and acc["quote"] == "EUR"
                               and app.performance()["capital"]["initial"] == 30.0
                               and app.db.scalar("PRAGMA integrity_check") == "ok")
            finally:
                app.db.close()
    except Exception as exc:
        return _step("sqlite", "Base SQLite et portefeuille de 30 € (dossier temporaire)", "fail", f"Erreur : {str(exc)[:300]}", t)
    ok = all(results)
    return _step("sqlite", "Base SQLite et portefeuille de 30 € (dossier temporaire)", "ok" if ok else "fail",
                 "Création, contrôle d'intégrité et réouverture réussis ; portefeuille 30 € fictifs en euros." if ok
                 else "Le portefeuille ou la base ne sont pas conformes après réouverture.", t)


def check_paper_only() -> dict:
    from sentinelle.execution.live import LiveBroker
    from sentinelle.safety import PAPER_ONLY, RealTradingForbidden

    import logging

    quiet = logging.getLogger("sentinelle")
    level = quiet.level
    quiet.setLevel(logging.CRITICAL)  # le refus attendu est journalisé : inutile de l'afficher ici
    try:
        LiveBroker(None, None, None)
    except RealTradingForbidden:
        inert = True
    else:
        inert = False
    finally:
        quiet.setLevel(level)
    ok = inert and PAPER_ONLY
    return _step("paper_only", "Aucun ordre réel possible", "ok" if ok else "fail",
                 "Le courtier réel refuse toute création ; verrou « simulation uniquement » actif." if ok
                 else "Le verrou de simulation n'est pas actif : ne pas utiliser cette installation.")


def check_frozen() -> dict:
    from sentinelle.preflight import INVALID_V1, MACRO_V3, RESEARCH, FROZEN_V2, verify_macro_shadow, verify_prospective

    v2 = verify_prospective(RESEARCH / FROZEN_V2)
    v3 = verify_macro_shadow(RESEARCH / MACRO_V3 / "protocol.json")
    v1 = verify_prospective(RESEARCH / INVALID_V1)
    ok = v2["ok"] and v3["ok"] and v1["invalid"]
    detail = ("Protocole v2 (modèle, réglages) et protocole macro v3 identiques à leurs empreintes ; v1 écartée." if ok else
              "; ".join(x for x in (None if v2["ok"] else f"v2 : {v2['error']}", None if v3["ok"] else f"v3 : {v3['error']}",
                                    None if v1["invalid"] else "v1 non marquée invalide") if x))
    return _step("frozen", "Intégrité des modèles et protocoles gelés", "ok" if ok else "fail", detail,
                 v2=v2.get("protocol_sha256"), model=v2.get("model_sha256"), v3=v3.get("protocol_sha256"))


# ---------------------------------------------------------------------------
# Ports et panel (vrai lanceur, données de démonstration temporaires)
# ---------------------------------------------------------------------------


def _port_state(port: int) -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return "interdit"
    try:
        s.bind(("127.0.0.1", port))
        return "libre"
    except OSError:
        return "occupé"
    finally:
        s.close()


def check_port(port: int = 8765) -> dict:
    state = _port_state(port)
    if state == "interdit":
        return _step("port", f"Port du panel ({port})", "skip", "Réseau local interdit dans cet environnement : non vérifié.")
    if state == "libre":
        return _step("port", f"Port du panel ({port})", "ok", "Libre.")
    who = _http_json(f"http://127.0.0.1:{port}/api/health", timeout=3)
    if who and who.get("version"):
        return _step("port", f"Port du panel ({port})", "ok", f"Occupé par TRADER AI {who['version']} déjà lancé : normal s'il tourne.")
    return _step("port", f"Port du panel ({port})", "warn",
                 "Occupé par un autre programme : lancez TRADER AI avec --port 8770 (lancer.bat --port 8770).")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(url, *, method="GET", body=None, cookie=None, timeout=5.0, csrf=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if csrf:
        req.add_header("X-Sentinelle", "1")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # adresse locale 127.0.0.1 uniquement
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def _http_json(url, **kw):
    try:
        status, raw, _ = _http(url, **kw)
        return json.loads(raw) if status == 200 else None
    except Exception:
        return None


def _start_panel(port: int, data: Path, log_path: Path) -> subprocess.Popen:
    log = open(log_path, "ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.pop("SENTINELLE_DATA", None)
    try:
        return subprocess.Popen([sys.executable, "-m", "sentinelle", "--demo", "--no-browser", "--port", str(port), "--data", str(data)],
                                cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, creationflags=flags)
    finally:
        log.close()


def _wait_health(port: int, proc: subprocess.Popen, timeout: float) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return None
        health = _http_json(f"http://127.0.0.1:{port}/api/health", timeout=2)
        if health:
            return health
        time.sleep(0.5)
    return None


def _stop_panel(proc: subprocess.Popen) -> bool:
    if proc.poll() is not None:
        return True
    proc.terminate()  # Windows : arrêt immédiat ; Linux/Mac : arrêt propre (SIGTERM)
    try:
        proc.wait(timeout=STOP_TIMEOUT)
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        return False


def check_panel(work: Path) -> list[dict]:
    """Démarre le vrai panel (démo hors ligne, données temporaires), vérifie l'API locale, l'arrête, le redémarre."""
    t = time.monotonic()
    try:
        port = _free_port()
    except OSError:
        return [_step("panel", "Démarrage, arrêt et redémarrage du panel", "skip",
                      "Réseau local interdit dans cet environnement : non vérifié.")]
    data, log_path = work / "panel", work / "panel.log"
    steps = []
    proc = _start_panel(port, data, log_path)
    try:
        health = _wait_health(port, proc, PANEL_TIMEOUT)
        if not health:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:] if log_path.exists() else ""
            return [_step("panel", "Démarrage, arrêt et redémarrage du panel", "fail",
                          f"Le panel n'a pas répondu en {PANEL_TIMEOUT:.0f} s. Fin du journal : {tail}", t)]
        base = f"http://127.0.0.1:{port}"
        probes = {
            "page": _http(base + "/")[0] == 200,
            "auth_status": _http_json(base + "/api/auth/status") is not None,
            "protected_401": _http(base + "/api/overview")[0] == 401,
            "preflight_401": _http(base + "/api/observation/preflight")[0] == 401,
            "csrf_403": _http(base + "/api/auth/setup", method="POST", body={"password": PASSWORD}, csrf=False)[0] == 403,
        }
        status, _, headers = _http(base + "/api/auth/setup", method="POST", body={"password": PASSWORD})
        cookie = (headers.get("set-cookie") or "").split(";")[0] if status == 200 else None
        perf = _http_json(base + "/api/performance", cookie=cookie) if cookie else None
        probes["login"] = bool(cookie)
        probes["wallet_30"] = bool(perf) and perf["capital"]["initial"] == 30.0
        pre = _http_json(base + "/api/observation/preflight", cookie=cookie) if cookie else None
        probes["preflight"] = bool(pre) and pre.get("verdict") in ("READY", "BLOCKED")
        api_ok = all(probes.values())
        steps.append(_step("panel_api", "API locale du panel (127.0.0.1)", "ok" if api_ok else "fail",
                           "Page servie, API protégée par mot de passe, portefeuille 30 €, page de préparation lisible."
                           if api_ok else "Échecs : " + ", ".join(k for k, v in probes.items() if not v), t, probes=probes))
        clean = _stop_panel(proc)
        stopped = _http_json(base + "/api/health", timeout=2) is None
        steps.append(_step("panel_stop", "Arrêt du panel", "ok" if stopped else "fail",
                           ("Arrêté proprement." if clean else "Arrêté de force après délai.") if stopped else "Le panel répond encore après l'arrêt."))
        # Redémarrage sur les mêmes données temporaires : le mot de passe et le portefeuille doivent être conservés.
        t2 = time.monotonic()
        proc = _start_panel(port, data, log_path)
        health = _wait_health(port, proc, PANEL_TIMEOUT)
        ok = False
        if health:
            status, _, headers = _http(base + "/api/auth/login", method="POST", body={"password": PASSWORD})
            cookie = (headers.get("set-cookie") or "").split(";")[0] if status == 200 else None
            perf = _http_json(base + "/api/performance", cookie=cookie) if cookie else None
            ok = bool(perf) and perf["capital"]["initial"] == 30.0 and not health.get("setup_required")
        steps.append(_step("panel_restart", "Redémarrage du panel", "ok" if ok else "fail",
                           "Redémarré : mot de passe et portefeuille conservés." if ok else "Redémarrage incomplet (voir panel.log).", t2))
    finally:
        _stop_panel(proc)
    return steps


# ---------------------------------------------------------------------------
# Installation de l'utilisateur : lecture seule, empreinte avant/après
# ---------------------------------------------------------------------------


def fingerprint(folder: Path) -> dict:
    out = {}
    if not folder.exists():
        return out
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(folder))] = (st.st_size, st.st_mtime_ns)
    return out


def network_prices(symbols, timeout: float = 15.0) -> dict:
    """Lecture publique des derniers prix (aucun compte, aucune clé). Seulement avec --reseau."""
    from sentinelle.market.sources import CcxtSource

    out = {"measured": True, "prices": {}, "errors": {}}
    try:
        src = CcxtSource("binance", public_mirror=True)
    except Exception as exc:
        return {"measured": True, "prices": {}, "errors": {"*": str(exc)[:200]}}
    for s in symbols:
        try:
            rows = src.fetch_ohlcv(s, "1m", limit=2)
            age = time.time() - rows[-1][0] / 1000
            out["prices"][s] = bool(rows) and 0 <= age <= 180
        except Exception as exc:
            out["prices"][s] = False
            out["errors"][s] = str(exc)[:200]
    return out


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------


def diagnose(*, data_dir: Path | None = None, network: bool = False, panel: bool = True, report_name: str | None = None) -> dict:
    from sentinelle.config import default_data_dir
    from sentinelle.preflight import evaluate, facts_from_data_dir
    from sentinelle.services.supervisor import code_hashes

    started = time.monotonic()
    info = system_info()
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    before = fingerprint(data_dir)
    steps = [check_python(), check_dependencies()]
    with tempfile.TemporaryDirectory(prefix="sentinelle-diagnostic-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        steps += [check_sqlite_wallet(work), check_paper_only(), check_frozen(), check_port()]
        if panel:
            steps += check_panel(work)
        else:
            steps.append(_step("panel", "Démarrage, arrêt et redémarrage du panel", "skip", "Non demandé (--sans-panel)."))
    facts = facts_from_data_dir(data_dir)
    after = fingerprint(data_dir)
    untouched = before == after
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    steps.append(_step("user_data", "Installation de l'utilisateur non modifiée", "ok" if untouched else "warn",
                       f"Dossier {data_dir} lu en lecture seule ; {len(before)} fichier(s), aucun changement." if untouched
                       else f"{len(changed)} fichier(s) du dossier de données ont changé pendant le diagnostic ({', '.join(changed[:4])}) : "
                       "TRADER AI était probablement en marche et écrivait lui-même. Le diagnostic n'écrit jamais dans ce dossier ; "
                       "fermez TRADER AI et relancez pour une vérification nette.",
                       files=len(before), changed=changed[:20]))
    net = network_prices(facts.get("history", {}).keys()) if network else None
    if net is not None:
        bad = [s for s, v in net["prices"].items() if not v]
        steps.append(_step("public_market", "Données de marché publiques (Binance, lecture seule)", "ok" if not bad else "fail",
                           "Prix récents reçus pour " + ", ".join(net["prices"]) + "." if not bad
                           else "Prix absents ou anciens pour " + ", ".join(bad) + " : vérifiez la connexion internet ou le pare-feu.",
                           errors=net["errors"]))
    installation_ok = all(s["status"] in ("ok", "skip", "warn") for s in steps if s["key"] != "public_market")
    skipped = [s["label"] for s in steps if s["status"] == "skip"]
    stamp = time.time()
    own = {"file": report_name or "(non enregistré)", "generated_at": stamp, "platform": info["platform"],
           "windows_actually_tested": info["windows_actually_tested"], "installation_ok": installation_ok and not skipped,
           "supervisor_code": code_hashes(), "data_dir": str(data_dir)}
    observation = evaluate(facts, now=stamp, diagnostic=own, network=net)
    return {
        "kind": "sentinelle-diagnostic", "schema": 2,
        "supervisor_code": code_hashes(),
        "generated_at": stamp, "generated_at_iso": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
        "duration_s": round(time.monotonic() - started, 1),
        **info,
        "data_dir": str(data_dir),
        "installation": {"ok": installation_ok, "steps": steps, "not_verified": skipped},
        "observation": observation,
        "ok": installation_ok,
        "limits": ["Ne valide pas le rendu du navigateur du PC, Telegram ni un long entraînement.",
                   "Sans --reseau, les données de marché publiques ne sont pas vérifiées.",
                   "Hors Windows 11, ce rapport ne valide pas Windows."],
    }


def render(result: dict):
    marks = {"ok": "OK      ", "fail": "ÉCHEC   ", "warn": "ATTENTION", "skip": "NON VÉRIFIÉ"}
    yield f"Diagnostic TRADER AI — {result['generated_at_iso']}"
    yield f"Système : {result['platform']} · Python {result['python']} · SQLite {result['sqlite']}"
    yield ("Windows 11 réellement testé : OUI" if result["windows_actually_tested"]
           else "Windows 11 réellement testé : NON (ce rapport ne valide pas Windows)")
    yield ""
    yield "Installation : " + ("FONCTIONNELLE" if result["installation"]["ok"] else "PROBLÈMES À CORRIGER")
    for s in result["installation"]["steps"]:
        yield f"  [{marks.get(s['status'], s['status'])}] {s['label']} — {s['detail']}"
    from sentinelle.preflight import summarize

    yield ""
    yield from summarize(result["observation"])
    if result["observation"]["verdict"] != "READY":
        yield ""
        yield "Raisons du blocage :"
        for r in result["observation"]["reasons"]:
            yield f"  - {r}"
    yield ""
    yield f"Durée : {result['duration_s']} s. Aucun service laissé en marche ; dossier temporaire supprimé."


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    cli.add_argument("--seal-junit", type=Path, help="Seal an existing report with completed Windows test evidence")
    cli.add_argument("--reseau", action="store_true", help="vérifie aussi les prix publics (lecture seule, sans compte)")
    cli.add_argument("--rapport", nargs="?", const="auto", help="écrit le rapport JSON (par défaut dans rapports/)")
    cli.add_argument("--donnees", type=Path, help="dossier de données à examiner en lecture seule (par défaut : data)")
    cli.add_argument("--sans-panel", action="store_true", help="ne démarre pas le panel temporaire")
    cli.add_argument("--json", action="store_true", help="affiche le rapport JSON complet")
    cli.add_argument("--strict", action="store_true", help="code de sortie 3 si l'observation est BLOCKED")
    a = cli.parse_args(argv)
    path = None
    if a.rapport:
        path = (ROOT / "rapports" / f"diagnostic-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json") if a.rapport == "auto" else Path(a.rapport)
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows
    if a.seal_junit:
        if not path or not path.is_file():
            cli.error("Existing --rapport required for sealing")
        from sentinelle.services.platform_validation import seal
        seal(path, a.seal_junit)
        return 0
    result = diagnose(data_dir=a.donnees, network=a.reseau, panel=not a.sans_panel, report_name=path.name if path else None)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2) if a.json else "\n".join(render(result)))
    if path:
        print(f"\nRapport enregistré : {path}")
    if not result["installation"]["ok"]:
        return 1
    if a.strict and result["observation"]["verdict"] != "READY":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
