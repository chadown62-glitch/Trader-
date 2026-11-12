"""Préparation de l'observation prospective : contrôles en lecture seule.

Ce module répond à une seule question : « TRADER AI peut-il commencer
l'observation prospective gelée (protocole v2, 30 € fictifs) ? ». Il produit un
verdict READY ou BLOCKED avec la raison de chaque blocage.

Garanties (vérifiées par les tests) :
- aucune connexion réseau ;
- aucune écriture : ni base, ni fichier, ni réglage ; la base d'un PC est ouverte
  en lecture seule (`mode=ro`) ;
- aucun démarrage de service, d'agent, de collecte ou de téléchargement ;
- aucun secret lu.

Il est utilisé par le panel (page « Préparation de l'observation ») et par
`tools/diagnose_windows.py`. Il ne remplace pas le lanceur d'observation, qui
reste à construire (mission #30) : tant qu'il n'existe pas, le verdict reste
BLOCKED, par construction.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESEARCH = PROJECT_ROOT / "research"
REPORTS = PROJECT_ROOT / "rapports"
FROZEN_V2 = "prospective-v2"
INVALID_V1 = "prospective-v1"
MACRO_V3 = "macro-shadow-v3"
SHADOW_IMPLEMENTATION = PROJECT_ROOT / "sentinelle" / "news" / "shadow"

# Le lanceur d'observation continue (collecte fiable, reprise après panne,
# exécution contrefactuelle) relève de la mission #30. Tant qu'il n'est pas
# livré et testé, l'observation ne peut pas être déclarée prête.
RUNNER_AVAILABLE = False

OK, BLOCKED, UNTESTED, WARNING, INFO = "ok", "blocked", "untested", "warning", "info"
READY, NOT_READY = "READY", "BLOCKED"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso_ts(value: str) -> float:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("Date sans fuseau horaire")
    return stamp.timestamp()


def _human(value: str) -> str:
    """« 2026-11-13T00:00:00+00:00 » → « 13/11/2026 00:00 UTC »."""
    try:
        from datetime import timezone

        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    except (TypeError, ValueError):
        return str(value)


def check(key: str, label: str, status: str, detail: str, *, blocking: bool = True, data: Any = None) -> dict:
    return {"key": key, "label": label, "status": status, "blocking": bool(blocking), "detail": detail, "data": data}


# ---------------------------------------------------------------------------
# Protocoles gelés (mêmes règles que tools/prospective.py et tools/macro_shadow.py)
# ---------------------------------------------------------------------------


def verify_prospective(root: Path) -> dict:
    """Vérifie un instantané prospectif sans le modifier. Mêmes règles que `tools.prospective.check`."""
    root = Path(root)
    out: dict[str, Any] = {"name": root.name, "ok": False, "invalid": (root / "INVALID.txt").exists(), "error": None}
    try:
        if out["invalid"]:
            raise ValueError("Préparation marquée invalide (INVALID.txt) : utiliser l'instantané corrigé prospective-v2")
        raw = (root / "protocol.json").read_bytes()
        out["protocol_sha256"] = sha256(raw)
        if out["protocol_sha256"] != (root / "protocol.sha256").read_text().strip():
            raise ValueError("Empreinte du protocole différente de protocol.sha256")
        protocol = json.loads(raw)
        model_sha = sha256((root / "model.joblib").read_bytes())
        settings_sha = sha256((root / "settings.json").read_bytes())
        if model_sha != protocol["model_sha256"]:
            raise ValueError("Le modèle gelé ne correspond plus à son empreinte")
        if settings_sha != protocol["settings_sha256"]:
            raise ValueError("Les réglages gelés ne correspondent plus à leur empreinte")
        out.update(ok=True, model_sha256=model_sha, settings_sha256=settings_sha,
                   start=protocol["start"], end=protocol["end"], launch_status=protocol.get("launch_status"),
                   execution=protocol.get("execution"), initial_eur=protocol.get("initial_eur"),
                   promotion_allowed=protocol.get("promotion_allowed"), supervised_only=protocol.get("supervised_only"),
                   criteria=protocol.get("criteria"), registered_at=protocol.get("registered_at"))
    except FileNotFoundError as exc:
        out["error"] = f"Fichier manquant : {Path(exc.filename).name if exc.filename else exc}"
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        out["error"] = str(exc)
    return out


def verify_macro_shadow(path: Path, implementation: Path = SHADOW_IMPLEMENTATION) -> dict:
    """Intégrité du protocole macro v3 (hors fenêtre de temps, rapportée à part)."""
    path = Path(path)
    out: dict[str, Any] = {"name": path.parent.name, "ok": False, "error": None}
    try:
        raw = path.read_bytes()
        out["protocol_sha256"] = sha256(raw)
        if out["protocol_sha256"] != Path(str(path) + ".sha256").read_text().strip():
            raise ValueError("Empreinte du protocole différente du fichier .sha256")
        protocol = json.loads(raw)
        if protocol.get("mode") != "shadow_only" or protocol.get("can_trade"):
            raise ValueError("Le protocole n'est plus en observation parallèle sans ordre")
        from .news.shadow.sources import SOURCES  # catalogue figé : simple lecture

        if protocol.get("source_catalogue") != SOURCES:
            raise ValueError("Catalogue de sources modifié après l'enregistrement du protocole")
        changed = [name for name, expected in protocol.get("implementation_sha256", {}).items()
                   if sha256((Path(implementation) / name).read_bytes()) != expected]
        if changed:
            raise ValueError("Code de l'observation macro modifié après enregistrement : " + ", ".join(sorted(changed)))
        out.update(ok=True, observation_start=protocol["observation_start"], observation_end=protocol["observation_end"],
                   baseline_protocol_sha256=protocol.get("baseline_protocol_sha256"), arms=protocol.get("arms"),
                   assets=protocol.get("assets"), capital_eur=protocol.get("capital_eur"))
    except FileNotFoundError as exc:
        out["error"] = f"Fichier manquant : {Path(exc.filename).name if exc.filename else exc}"
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        out["error"] = str(exc)
    return out


def settings_differences(current: dict, frozen: dict, prefix: str = "") -> list[str]:
    """Chemins (« general.watchlist ») dont la valeur diffère entre deux jeux de réglages."""
    diffs: list[str] = []
    for key in sorted(set(current) | set(frozen)):
        path = f"{prefix}{key}"
        a, b = current.get(key, None), frozen.get(key, None)
        if isinstance(a, dict) and isinstance(b, dict):
            diffs.extend(settings_differences(a, b, path + "."))
        elif a != b or (key in current) != (key in frozen):
            diffs.append(path)
    return diffs


# ---------------------------------------------------------------------------
# Faits observés (depuis l'application en marche, ou depuis un dossier en lecture seule)
# ---------------------------------------------------------------------------


def facts_from_app(app) -> dict:
    """Relevé en mémoire, sans appel réseau : l'application a déjà ses prix et sa base."""
    from .ai.features import warmup_bars

    settings = copy.deepcopy(app.settings)
    account = app.main_account()
    frozen = _frozen_settings()
    symbols = (frozen or settings)["general"]["watchlist"]
    tf = (frozen or settings)["ai"]["timeframe"]
    prices = {}
    for s in symbols:
        known = s in getattr(app.hub, "markets", {}) or s in getattr(app.hub, "prices", {})
        prices[s] = app.hub.is_fresh(s, 180) if known else False
    history = {}
    for s in symbols:
        try:
            info = app.history.info(s, tf)
        except Exception as exc:  # cache illisible : signalé, jamais réparé ici
            info = {"bars": 0, "error": str(exc)[:200]}
        history[s] = {"bars": info.get("bars", 0), "last_ms": info.get("last"), "error": info.get("error")}
    return {
        "context": "panel",
        "settings": settings,
        "account": dict(account) if account else None,
        "prices": prices,
        "prices_measured": True,
        "agents_running": sorted(n for n, a in app.agents.items() if getattr(a, "running", False)),
        "history": history,
        "warmup_bars": warmup_bars(tf),
        "timeframe": tf,
        "policy_locks": [dict(r) for r in app.models.policy_locks()],
        "market_ok": bool(app.hub.status.get("ok")),
    }


def _read_only(db_path: Path, scratch: Path) -> sqlite3.Connection:
    """Ouvre une COPIE de la base (avec son journal WAL) : même en `mode=ro`, SQLite
    réécrit le fichier `-shm` d'une base WAL ; la copie garantit que le dossier de
    l'utilisateur est seulement lu."""
    db_path = Path(db_path)
    copy_path = Path(scratch) / "lecture.db"
    shutil.copyfile(db_path, copy_path)
    wal = Path(str(db_path) + "-wal")
    if wal.exists():
        shutil.copyfile(wal, Path(str(copy_path) + "-wal"))
    uri = "file:" + quote(str(copy_path.resolve()), safe="/:") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def facts_from_data_dir(data_dir: Path) -> dict:
    """Relevé d'une installation sans la démarrer : base ouverte en lecture seule, aucun fichier créé."""
    from .ai.features import warmup_bars
    from .config import DEFAULT_SETTINGS, deep_merge, validate_settings

    data_dir = Path(data_dir)
    frozen = _frozen_settings()
    facts: dict[str, Any] = {"context": "dossier", "data_dir": str(data_dir), "settings": None, "account": None,
                             "prices": {}, "prices_measured": False, "agents_running": None, "history": {},
                             "policy_locks": [], "market_ok": None, "errors": []}
    db_path = data_dir / "sentinelle.db"
    if db_path.exists():
        try:
            scratch = tempfile.TemporaryDirectory(prefix="sentinelle-lecture-", ignore_cleanup_errors=True)
            conn = _read_only(db_path, Path(scratch.name))
            try:
                row = conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
                stored = json.loads(row["data"]) if row else {}
                facts["settings"] = validate_settings(deep_merge(DEFAULT_SETTINGS, stored))
                acc = conn.execute("SELECT * FROM accounts WHERE role = 'main' AND kind = 'paper' AND archived = 0 ORDER BY id").fetchone()
                facts["account"] = dict(acc) if acc else None
                try:
                    facts["policy_locks"] = [dict(r) for r in conn.execute("SELECT * FROM model_policy_locks WHERE end > ?", (time.time(),))]
                except sqlite3.OperationalError:
                    facts["policy_locks"] = []
            finally:
                conn.close()
                scratch.cleanup()
        except Exception as exc:
            facts["errors"].append(f"Base illisible en lecture seule : {str(exc)[:200]}")
    settings = facts["settings"] or copy.deepcopy(DEFAULT_SETTINGS)
    ref = frozen or settings
    tf = ref["ai"]["timeframe"]
    facts["timeframe"], facts["warmup_bars"] = tf, warmup_bars(tf)
    source = ref["market"]["source"]
    for s in ref["general"]["watchlist"]:
        facts["history"][s] = _history_file_info(data_dir / "history" / source / f"{s.replace('/', '-')}_{tf}.csv.gz")
    return facts


def _history_file_info(path: Path) -> dict:
    if not path.exists():
        return {"bars": 0, "last_ms": None, "error": None}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
            col = header.index("ts")
            bars, last = 0, None
            for line in fh:
                parts = line.split(",")
                if len(parts) > col and parts[col].strip():
                    bars += 1
                    last = int(float(parts[col]))
        return {"bars": bars, "last_ms": last, "error": None}
    except Exception as exc:
        return {"bars": 0, "last_ms": None, "error": f"Historique illisible : {str(exc)[:150]}"}


def _frozen_settings() -> dict | None:
    try:
        return json.loads((RESEARCH / FROZEN_V2 / "settings.json").read_bytes())
    except Exception:
        return None


def latest_diagnostic(reports: Path = REPORTS) -> dict | None:
    """Dernier rapport écrit par tools/diagnose_windows.py --rapport (s'il existe)."""
    try:
        files = sorted(Path(reports).glob("diagnostic-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:5]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("kind") == "sentinelle-diagnostic":
            return {"file": path.name, "generated_at": data.get("generated_at"), "platform": data.get("platform"),
                    "windows_actually_tested": bool(data.get("windows_actually_tested")),
                    "installation_ok": bool(data.get("installation", {}).get("ok")),
                    "observation_verdict": data.get("observation", {}).get("verdict")}
    return None


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def evaluate(facts: dict, *, now: float | None = None, research: Path = RESEARCH,
             diagnostic: dict | None = None, network: dict | None = None) -> dict:
    """Assemble les contrôles et le verdict READY / BLOCKED. Fonction pure (lecture de fichiers seulement)."""
    from .safety import PAPER_ONLY

    now = time.time() if now is None else now
    checks: list[dict] = []
    v2 = verify_prospective(Path(research) / FROZEN_V2)
    v1 = verify_prospective(Path(research) / INVALID_V1)
    v3 = verify_macro_shadow(Path(research) / MACRO_V3 / "protocol.json")
    frozen = _load_json(Path(research) / FROZEN_V2 / "settings.json") if v2["ok"] else None

    checks.append(check("paper_only", "Aucun ordre réel possible", OK if PAPER_ONLY else BLOCKED,
                        "Verrou « simulation uniquement » actif : le code de passage d'ordres réels est absent." if PAPER_ONLY
                        else "Verrou de simulation absent : arrêt immédiat requis."))

    if v2["ok"]:
        checks.append(check("protocol_v2", "Protocole prospectif v2 intact", OK,
                            "Protocole, modèle et réglages gelés correspondent à leurs empreintes SHA-256.",
                            data={k: v2[k] for k in ("protocol_sha256", "model_sha256", "settings_sha256")}))
        policy_ok = (v2.get("launch_status") == "PREPARED_NOT_STARTED" and v2.get("execution") == "paper_only"
                     and v2.get("promotion_allowed") is False and v2.get("supervised_only") is True and v2.get("initial_eur") == 30)
        checks.append(check("policy", "Politique gelée : argent fictif, 30 €, aucune promotion", OK if policy_ok else BLOCKED,
                            "Exécution « paper_only », capital 30 €, promotion interdite, observation supervisée, état « préparé, non démarré »."
                            if policy_ok else "La politique enregistrée ne correspond plus à la politique gelée attendue."))
        start, end = _iso_ts(v2["start"]), _iso_ts(v2["end"])
        if now < start:
            checks.append(check("window", "Période réservée", BLOCKED,
                                f"La période gelée commence le {_human(v2['start'])} : aucune observation ne peut compter avant cette date.",
                                data={"start": start, "end": end}))
        elif now >= end:
            checks.append(check("window", "Période réservée", BLOCKED,
                                f"La période gelée s'est terminée le {_human(v2['end'])} : un nouveau protocole serait nécessaire.",
                                data={"start": start, "end": end}))
        else:
            checks.append(check("window", "Période réservée", OK, f"Période en cours, jusqu'au {_human(v2['end'])}.", data={"start": start, "end": end}))
    else:
        checks.append(check("protocol_v2", "Protocole prospectif v2 intact", BLOCKED,
                            f"Contrôle d'intégrité refusé : {v2['error']}. Rien n'a été modifié ; ne pas régénérer le protocole sans revue."))

    checks.append(check("protocol_v1", "Ancienne préparation v1 écartée", OK if v1["invalid"] else WARNING,
                        "La préparation v1 est marquée invalide et ne peut pas être utilisée." if v1["invalid"]
                        else "La préparation v1 n'est pas marquée invalide : elle ne doit pas être utilisée.", blocking=False))

    if v3["ok"]:
        in_window = v3["observation_start"] <= now < v3["observation_end"]
        checks.append(check("protocol_v3", "Observation macro v3 intacte (parallèle, sans ordre)", OK,
                            "Protocole, catalogue de sources et code d'analyse identiques à l'enregistrement. "
                            + ("Fenêtre d'observation en cours." if in_window else "Fenêtre d'observation hors période pour l'instant."),
                            data={"protocol_sha256": v3["protocol_sha256"], "observation_start": v3["observation_start"],
                                  "observation_end": v3["observation_end"]}))
    else:
        checks.append(check("protocol_v3", "Observation macro v3 intacte (parallèle, sans ordre)", BLOCKED,
                            f"Contrôle d'intégrité refusé : {v3['error']}."))

    account = facts.get("account")
    if account is None:
        checks.append(check("wallet", "Portefeuille principal de 30 € fictifs", BLOCKED,
                            "Aucun portefeuille principal trouvé. Lancez TRADER AI une fois puis confirmez le passage aux 30 € fictifs (page Tableau de bord)."))
    else:
        wallet_ok = account.get("kind") == "paper" and account.get("quote") == "EUR" and float(account.get("initial_balance") or 0) == 30.0
        checks.append(check("wallet", "Portefeuille principal de 30 € fictifs", OK if wallet_ok else BLOCKED,
                            "Portefeuille en euros, 30 € de départ, argent fictif. Il ne sera jamais remis à zéro par l'observation."
                            if wallet_ok else f"Portefeuille principal en {account.get('quote')} avec {account.get('initial_balance')} au départ : "
                            "l'observation exige le portefeuille de 30 € fictifs en euros.", data={"id": account.get("id")}))

    settings = facts.get("settings")
    if frozen is None:
        checks.append(check("settings", "Réglages identiques aux réglages gelés", BLOCKED, "Réglages gelés illisibles ou non vérifiés."))
    elif settings is None:
        checks.append(check("settings", "Réglages identiques aux réglages gelés", UNTESTED,
                            "Aucune installation trouvée : impossible de comparer les réglages."))
    else:
        diffs = settings_differences(settings, frozen)
        checks.append(check("settings", "Réglages identiques aux réglages gelés", OK if not diffs else BLOCKED,
                            "Les réglages de l'application sont exactement ceux du protocole gelé." if not diffs else
                            f"{len(diffs)} réglage(s) diffèrent du protocole gelé (premiers : {', '.join(diffs[:6])}). "
                            "L'observation gelée refuse de démarrer tant qu'ils ne sont pas identiques ; ne les modifiez pas à la main "
                            "pendant une observation.", data={"differences": diffs[:40]}))
        source = settings["market"]["source"]
        checks.append(check("market_source", "Données de marché publiques réelles", OK if source != "synthetic" else BLOCKED,
                            f"Source : {source}, lecture publique sans compte ni clé." if source != "synthetic"
                            else "Marché simulé (démonstration) : aucune observation prospective possible avec des prix simulés."))

    ref_symbols = (frozen or settings or {}).get("general", {}).get("watchlist", [])
    prices = facts.get("prices") or {}
    if network is not None and network.get("measured"):
        prices = network.get("prices", {})
    if facts.get("prices_measured") or (network is not None and network.get("measured")):
        stale = [s for s in ref_symbols if not prices.get(s)]
        checks.append(check("prices", "Prix publics récents pour les cryptos observées", OK if not stale else BLOCKED,
                            "Prix publics reçus il y a moins de 3 minutes pour " + ", ".join(ref_symbols) + "." if not stale
                            else "Aucun prix public récent pour " + ", ".join(stale) + " (réseau coupé, paire non suivie ou source en maintenance).",
                            data={"fresh": {s: bool(prices.get(s)) for s in ref_symbols}}))
    else:
        checks.append(check("prices", "Prix publics récents pour les cryptos observées", UNTESTED,
                            "Non vérifié : ce contrôle a été fait hors ligne. Relancez le diagnostic avec --reseau, ou ouvrez cette page dans le panel."))

    warm = int(facts.get("warmup_bars") or 0)
    history = facts.get("history") or {}
    missing = [s for s in ref_symbols if (history.get(s) or {}).get("bars", 0) < warm]
    unreadable = [s for s in ref_symbols if (history.get(s) or {}).get("error")]
    tf_ms = _tf_ms(facts.get("timeframe") or "4h")
    old = [s for s in ref_symbols if s not in missing and (history.get(s) or {}).get("last_ms")
           and now * 1000 - history[s]["last_ms"] > 2 * 86400_000 + tf_ms]
    if unreadable:
        checks.append(check("history", "Historique suffisant pour calculer les indicateurs", BLOCKED,
                            "Historique illisible pour " + ", ".join(unreadable) + " : fichier conservé, restauration explicite nécessaire."))
    elif missing:
        checks.append(check("history", "Historique suffisant pour calculer les indicateurs", BLOCKED,
                            f"Moins de {warm} bougies {facts.get('timeframe')} pour " + ", ".join(missing)
                            + ". Le téléchargement public se fera au démarrage de l'observation (mission #30), jamais pendant ce contrôle.",
                            data={s: (history.get(s) or {}).get("bars", 0) for s in ref_symbols}))
    else:
        checks.append(check("history", "Historique suffisant pour calculer les indicateurs", OK if not old else WARNING,
                            f"Au moins {warm} bougies {facts.get('timeframe')} pour chaque crypto." + (
                                " Dernières bougies anciennes pour " + ", ".join(old) + " : elles seront complétées au démarrage." if old else ""),
                            blocking=False, data={s: (history.get(s) or {}).get("bars", 0) for s in ref_symbols}))

    running = facts.get("agents_running")
    if running is None:
        checks.append(check("agents", "Aucun agent autonome en marche", INFO,
                            "Vérifié par l'observation elle-même au démarrage (elle refuse de démarrer si un autre agent tourne).", blocking=False))
    else:
        checks.append(check("agents", "Aucun agent autonome en marche", OK if not running else BLOCKED,
                            "Aucun agent IA n'est en marche." if not running else "Agent(s) en marche : " + ", ".join(running)
                            + ". Arrêtez-les avant toute observation gelée."))

    checks.append(check("runner", "Lanceur d'observation continue", OK if RUNNER_AVAILABLE else BLOCKED,
                        "Lanceur livré et testé." if RUNNER_AVAILABLE else
                        "Pas encore livré : collecte continue fiable, reprise après panne et comparaison contrefactuelle relèvent de la mission #30. "
                        "Aucun service permanent n'est démarré par cette version."))

    win = diagnostic or {}
    if win.get("windows_actually_tested") and win.get("installation_ok"):
        checks.append(check("windows", "Installation validée sur Windows 11", OK,
                            f"Diagnostic Windows réussi ({win.get('platform')}, rapport {win.get('file')}).", data=win))
    elif win.get("windows_actually_tested"):
        checks.append(check("windows", "Installation validée sur Windows 11", BLOCKED,
                            f"Le dernier diagnostic Windows ({win.get('file')}) a trouvé des problèmes : consultez-le.", data=win))
    else:
        checks.append(check("windows", "Installation validée sur Windows 11", UNTESTED,
                            "NON TESTÉ : aucun rapport de diagnostic produit sur un vrai Windows 11. "
                            "Exécutez tools\\diagnostic_windows.ps1 sur le PC (voir docs/WINDOWS.md).", data=win or None))

    checks.append(check("browser", "Affichage du panel dans le navigateur du PC", INFO,
                        "Vérifié automatiquement sous Chromium (Linux) en 1440 px et 390 px ; non vérifié dans le navigateur de votre PC.",
                        blocking=False))
    checks.append(check("forex_factory", "Calendrier Forex Factory", INFO,
                        "Droits d'usage non confirmés : aucune collecte Forex Factory n'est faite ni forcée. "
                        "L'observation macro v3 fonctionne sans lui, avec les sources officielles.", blocking=False))
    checks.append(check("service", "Aucun service permanent démarré", OK,
                        "Ce contrôle et cette page ne démarrent ni collecte, ni agent, ni tâche planifiée.", blocking=False))

    blockers = [c for c in checks if c["blocking"] and c["status"] in (BLOCKED, UNTESTED)]
    return {
        "verdict": READY if not blockers else NOT_READY,
        "checked_at": now,
        "context": facts.get("context"),
        "reasons": [f"{c['label']} : {c['detail']}" for c in blockers],
        "checks": checks,
        "frozen": {"v2": _public(v2), "v1": {"invalid": v1["invalid"]}, "v3": _public(v3)},
        "capital_eur": 30,
        "watchlist": ref_symbols,
        "timeframe": facts.get("timeframe"),
        "policy_locks": facts.get("policy_locks", []),
        "diagnostic": diagnostic,
        "limitations": [
            "Le lanceur d'observation continue n'existe pas encore (mission #30) : rien n'observe en continu.",
            "Windows 11 n'a pas été validé tant qu'un rapport de diagnostic produit sur un vrai Windows 11 n'est pas présent.",
            "Le rendu du panel n'a été vérifié automatiquement que sous Chromium Linux.",
            "Forex Factory : droits non confirmés, aucune collecte.",
            "Aucun résultat financier prospectif n'existe encore : il n'y a rien à comparer.",
        ],
        "errors": facts.get("errors", []),
    }


def _public(v: dict) -> dict:
    keep = ("name", "ok", "error", "protocol_sha256", "model_sha256", "settings_sha256", "start", "end", "launch_status",
            "registered_at", "observation_start", "observation_end", "baseline_protocol_sha256", "arms", "assets", "criteria")
    return {k: v.get(k) for k in keep if k in v}


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_bytes())
    except Exception:
        return None


def _tf_ms(tf: str) -> int:
    from .config import timeframe_ms

    return timeframe_ms(tf)


def observation_preflight(app) -> dict:
    """Contrôle demandé par le panel (lecture seule)."""
    return evaluate(facts_from_app(app), diagnostic=latest_diagnostic())


def summarize(report: dict) -> Iterable[str]:
    """Rapport lisible (terminal) : verdict puis chaque contrôle."""
    marks = {OK: "OK ", BLOCKED: "BLOQUÉ", UNTESTED: "NON TESTÉ", WARNING: "ATTENTION", INFO: "info"}
    yield f"Observation prospective : {report['verdict']}"
    for c in report["checks"]:
        yield f"  [{marks.get(c['status'], c['status'])}] {c['label']} — {c['detail']}"
