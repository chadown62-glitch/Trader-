"""Préparation de l'observation : contrôles en lecture seule, verdict READY / BLOCKED, API authentifiée."""

import json
import re
import shutil
import socket
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinelle import preflight
from sentinelle.api.server import create_api
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App

ROOT = Path(__file__).resolve().parent.parent
H = {"X-Sentinelle": "1"}
PASSWORD = "mot-de-passe-de-test-pre-vol"
FROZEN = json.loads((ROOT / "research/prospective-v2/settings.json").read_bytes())
V2 = preflight.verify_prospective(ROOT / "research/prospective-v2")
START, END = preflight._iso_ts(V2["start"]), preflight._iso_ts(V2["end"])
INSIDE = START + 86400


def fingerprint(folder: Path) -> dict:
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(folder.rglob("*")) if p.is_file()}


def good_facts(**override):
    """Faits d'une installation prête en tout point (sauf ce que le test change)."""
    facts = {
        "context": "test", "settings": json.loads(json.dumps(FROZEN)),
        "account": {"id": 1, "kind": "paper", "quote": "EUR", "initial_balance": 30.0},
        "prices": {s: True for s in FROZEN["general"]["watchlist"]}, "prices_measured": True,
        "agents_running": [], "warmup_bars": 1500, "timeframe": "4h", "policy_locks": [],
        "history": {s: {"bars": 2000, "last_ms": int(INSIDE * 1000) - 14_400_000, "error": None} for s in FROZEN["general"]["watchlist"]},
    }
    facts.update(override)
    return facts


WINDOWS_OK = {"file": "diagnostic-x.json", "generated_at": INSIDE, "platform": "Windows-11", "windows_actually_tested": True,
              "installation_ok": True, "observation_verdict": "BLOCKED"}


# ---------------------------------------------------------------------------
# Protocoles gelés : mêmes règles que les outils existants, rien n'est modifié
# ---------------------------------------------------------------------------


def test_frozen_v2_matches_existing_check_and_v1_is_rejected():
    from tools.prospective import check

    assert V2["ok"], V2["error"]
    reference = check(ROOT / "research/prospective-v2")
    assert V2["model_sha256"] == reference["model_sha256"] and V2["settings_sha256"] == reference["settings_sha256"]
    assert V2["protocol_sha256"] == (ROOT / "research/prospective-v2/protocol.sha256").read_text().strip()
    assert V2["launch_status"] == "PREPARED_NOT_STARTED" and V2["promotion_allowed"] is False
    v1 = preflight.verify_prospective(ROOT / "research/prospective-v1")
    assert v1["invalid"] and not v1["ok"]


def test_tampered_snapshot_is_refused_without_touching_the_original(tmp_path):
    copy = tmp_path / "prospective-v2"
    shutil.copytree(ROOT / "research/prospective-v2", copy)
    before = fingerprint(ROOT / "research/prospective-v2")
    settings = json.loads((copy / "settings.json").read_bytes())
    settings["portfolio"]["initial"] = 1000
    (copy / "settings.json").write_text(json.dumps(settings))
    out = preflight.verify_prospective(copy)
    assert not out["ok"] and "réglages gelés" in out["error"]
    (copy / "protocol.json").write_bytes((copy / "protocol.json").read_bytes() + b" ")
    assert "protocol.sha256" in preflight.verify_prospective(copy)["error"]
    (copy / "model.joblib").unlink()
    assert not preflight.verify_prospective(copy)["ok"]
    assert fingerprint(ROOT / "research/prospective-v2") == before


def test_macro_v3_integrity_matches_the_shadow_tool(monkeypatch, tmp_path):
    import tools.macro_shadow as shadow_tool

    path = ROOT / "research/macro-shadow-v3/protocol.json"
    v3 = preflight.verify_macro_shadow(path)
    assert v3["ok"], v3["error"]
    protocol = json.loads(path.read_bytes())
    monkeypatch.setattr(shadow_tool.time, "time", lambda: protocol["observation_start"] + 1)
    assert shadow_tool.load_protocol(path)["name"] == "macro-shadow-v3"  # même verdict que l'outil gelé
    impl = tmp_path / "shadow"
    shutil.copytree(ROOT / "sentinelle/news/shadow", impl, ignore=shutil.ignore_patterns("__pycache__"))
    (impl / "store.py").write_text((impl / "store.py").read_text() + "\n# modifié\n")
    altered = preflight.verify_macro_shadow(path, implementation=impl)
    assert not altered["ok"] and "store.py" in altered["error"]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def test_ready_only_when_every_real_prerequisite_is_verified(monkeypatch):
    monkeypatch.setattr(preflight, "RUNNER_AVAILABLE", True)
    rep = preflight.evaluate(good_facts(), now=INSIDE, diagnostic=WINDOWS_OK)
    assert rep["verdict"] == "READY", rep["reasons"]
    assert rep["reasons"] == [] and rep["capital_eur"] == 30


def test_this_version_is_blocked_because_the_runner_does_not_exist_yet():
    assert preflight.RUNNER_AVAILABLE is False
    rep = preflight.evaluate(good_facts(), now=INSIDE, diagnostic=WINDOWS_OK)
    assert rep["verdict"] == "BLOCKED"
    assert [c["key"] for c in rep["checks"] if c["blocking"] and c["status"] in ("blocked", "untested")] == ["runner"]
    assert any("mission #30" in r for r in rep["reasons"])


DEFECTS = {
    "window": dict(now=START - 60),
    "window_end": dict(now=END + 60),
    "settings": dict(facts=dict(settings={**FROZEN, "general": {"quote": "USDC", "watchlist": ["BTC/USDC"]}})),
    "market_source": dict(facts=dict(settings={**FROZEN, "market": {**FROZEN["market"], "source": "synthetic"}}),
                          keys=["settings", "market_source"]),
    "wallet": dict(facts=dict(account={"id": 1, "kind": "paper", "quote": "USDC", "initial_balance": 10000.0})),
    "wallet_missing": dict(facts=dict(account=None)),
    "prices": dict(facts=dict(prices={"BTC/EUR": True, "ETH/EUR": False, "SOL/EUR": True})),
    "prices_offline": dict(facts=dict(prices={}, prices_measured=False)),
    "history": dict(facts=dict(history={"BTC/EUR": {"bars": 10, "last_ms": None, "error": None}})),
    "history_unreadable": dict(facts=dict(history={s: {"bars": 0, "last_ms": None, "error": "Historique illisible"} for s in FROZEN["general"]["watchlist"]})),
    "agents": dict(facts=dict(agents_running=["ia_entrainement"])),
    "windows": dict(diagnostic=None),
    "windows_failed": dict(diagnostic={**WINDOWS_OK, "installation_ok": False}),
    "windows_not_windows": dict(diagnostic={**WINDOWS_OK, "windows_actually_tested": False}),
}


@pytest.mark.parametrize("name", sorted(DEFECTS))
def test_each_missing_prerequisite_blocks_with_its_reason(monkeypatch, name):
    monkeypatch.setattr(preflight, "RUNNER_AVAILABLE", True)
    case = DEFECTS[name]
    rep = preflight.evaluate(good_facts(**case.get("facts", {})), now=case.get("now", INSIDE),
                             diagnostic=case.get("diagnostic", WINDOWS_OK))
    keys = case.get("keys", [name.split("_")[0]])
    assert rep["verdict"] == "BLOCKED"
    blockers = [c for c in rep["checks"] if c["blocking"] and c["status"] in ("blocked", "untested")]
    assert [c["key"] for c in blockers] == keys, blockers
    assert len(rep["reasons"]) == len(keys) and all(c["label"] in r for c, r in zip(blockers, rep["reasons"]))


def test_windows_untested_is_reported_as_untested_not_as_ok():
    rep = preflight.evaluate(good_facts(), now=INSIDE, diagnostic={**WINDOWS_OK, "windows_actually_tested": False})
    win = next(c for c in rep["checks"] if c["key"] == "windows")
    assert win["status"] == "untested" and win["detail"].startswith("NON TESTÉ")


def test_informative_items_never_block_and_limits_are_explicit(monkeypatch):
    monkeypatch.setattr(preflight, "RUNNER_AVAILABLE", True)
    rep = preflight.evaluate(good_facts(agents_running=None), now=INSIDE, diagnostic=WINDOWS_OK)
    assert rep["verdict"] == "READY"
    info = {c["key"]: c for c in rep["checks"] if not c["blocking"]}
    assert {"browser", "forex_factory", "service", "protocol_v1", "agents"} <= set(info)
    assert any("Forex Factory" in x for x in rep["limitations"])


def test_settings_differences_lists_dotted_paths():
    current = json.loads(json.dumps(FROZEN))
    current["general"]["watchlist"] = ["BTC/USDC"]
    current["ai"]["learning"] = True
    current["extra"] = {"x": 1}
    assert preflight.settings_differences(current, FROZEN) == ["ai.learning", "extra", "general.watchlist"]
    assert preflight.settings_differences(FROZEN, json.loads(json.dumps(FROZEN))) == []


def test_evaluation_is_offline(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("aucune connexion réseau attendue")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert preflight.evaluate(good_facts(), now=INSIDE)["verdict"] == "BLOCKED"


# ---------------------------------------------------------------------------
# Lecture d'une installation : rien n'est créé ni modifié
# ---------------------------------------------------------------------------


def test_reading_a_missing_installation_creates_nothing(tmp_path):
    target = tmp_path / "absent"
    facts = preflight.facts_from_data_dir(target)
    assert not target.exists()
    assert facts["settings"] is None and facts["account"] is None
    rep = preflight.evaluate(facts, now=INSIDE)
    assert next(c for c in rep["checks"] if c["key"] == "wallet")["status"] == "blocked"
    assert next(c for c in rep["checks"] if c["key"] == "prices")["status"] == "untested"


def test_reading_an_installation_does_not_modify_any_file_even_while_it_is_open(tmp_path):
    app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2),
              settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}})
    try:
        app.db.execute("SELECT 1")
        before = fingerprint(tmp_path)
        facts = preflight.facts_from_data_dir(tmp_path)
        assert fingerprint(tmp_path) == before  # y compris -wal et -shm de la base ouverte
    finally:
        app.db.close()
    assert facts["account"]["initial_balance"] == 30.0 and facts["account"]["quote"] == "EUR"
    assert facts["settings"]["general"]["watchlist"] == ["BTC/EUR"]
    assert set(facts["history"]) == set(FROZEN["general"]["watchlist"])


def test_history_file_reading_counts_bars(tmp_path):
    import gzip

    path = tmp_path / "h.csv.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("ts,open,high,low,close,volume\n" + "".join(f"{1000 * i},1,1,1,1,1\n" for i in range(5)))
    assert preflight._history_file_info(path) == {"bars": 5, "last_ms": 4000, "error": None}
    (tmp_path / "bad.csv.gz").write_bytes(b"pas du gzip")
    assert preflight._history_file_info(tmp_path / "bad.csv.gz")["error"]


def test_latest_diagnostic_reads_only_sentinelle_reports(tmp_path):
    assert preflight.latest_diagnostic(tmp_path) is None
    (tmp_path / "diagnostic-1.json").write_text("{pas du json")
    (tmp_path / "diagnostic-2.json").write_text(json.dumps({"kind": "autre"}))
    assert preflight.latest_diagnostic(tmp_path) is None
    (tmp_path / "diagnostic-3.json").write_text(json.dumps({"kind": "sentinelle-diagnostic", "generated_at": 1.0, "platform": "Windows-11",
                                                           "windows_actually_tested": True, "installation": {"ok": True},
                                                           "observation": {"verdict": "BLOCKED"}}))
    got = preflight.latest_diagnostic(tmp_path)
    assert got["file"] == "diagnostic-3.json" and got["windows_actually_tested"] and got["installation_ok"]


# ---------------------------------------------------------------------------
# API et page du panel
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def running(tmp_path_factory):
    d = tmp_path_factory.mktemp("preflight")
    app = App(d, source=SyntheticSource(["BTC/EUR", "ETH/EUR"], days=3),
              settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR", "ETH/EUR"]}})
    assert app.engine.ready.wait(30)
    yield app
    app.close()


def test_preflight_endpoint_requires_login_is_get_only_and_writes_nothing(running):
    with TestClient(create_api(running)) as client:
        assert client.get("/api/observation/preflight").status_code == 401
        assert client.post("/api/auth/setup", json={"password": PASSWORD}, headers=H).status_code == 200
        settings = json.dumps(running.settings, sort_keys=True)
        r = client.get("/api/observation/preflight")
        assert r.status_code == 200
        rep = r.json()
        assert json.dumps(running.settings, sort_keys=True) == settings
        assert not any(a.running for a in running.agents.values())
        assert rep["verdict"] == "BLOCKED" and rep["context"] == "panel"
        keys = {c["key"]: c for c in rep["checks"]}
        assert keys["market_source"]["status"] == "blocked"  # démonstration : jamais prête
        assert keys["runner"]["status"] == "blocked" and keys["wallet"]["status"] == "ok"
        assert keys["protocol_v2"]["status"] == "ok" and keys["protocol_v3"]["status"] == "ok"
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)("/api/observation/preflight", headers=H).status_code == 405
    routes = [r for r in create_api(running).routes if getattr(r, "path", "").startswith("/api/observation")]
    assert routes and all(set(r.methods) <= {"GET", "HEAD"} for r in routes)


def test_preparation_page_has_no_start_or_write_action():
    source = (ROOT / "web/js/views/preparation.js").read_text(encoding="utf-8")
    assert "api.get('/api/observation/preflight')" in source
    assert not re.search(r"api\.(post|put|patch|del)\b", source)
    buttons = re.findall(r"<button[^>]*>([^<]*)</button>", source)
    assert buttons == ["Refaire le contrôle"]
    assert not re.search(r"[Dd]émarrer", " ".join(buttons))
    main = (ROOT / "web/js/main.js").read_text(encoding="utf-8")
    assert "id: 'preparation'" in main and "preparation: Preparation" in main


def test_preflight_is_fast_enough_for_a_page(running):
    t = time.monotonic()
    preflight.observation_preflight(running)
    assert time.monotonic() - t < 10


def test_preflight_performs_no_database_write(tmp_path):
    import sqlite3

    app = App(tmp_path, start=False, source=SyntheticSource(["BTC/EUR"], days=2),
              settings_override={"market": {"source": "synthetic"}, "general": {"quote": "EUR", "watchlist": ["BTC/EUR"]}})
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE,
              sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_INDEX}
    seen = []

    def authorizer(action, *args):
        if action in writes:
            seen.append((action, args))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        app.db.conn.set_authorizer(authorizer)
        # « -shm » est l'index en mémoire partagée de SQLite : toute LECTURE par la connexion de
        # l'application le met à jour. Il n'est donc pas comparé ici (il l'est, strictement, quand
        # une installation est lue de l'extérieur : test précédent).
        def stable():
            return {k: v for k, v in fingerprint(tmp_path).items() if not k.endswith("-shm")}

        before = stable()
        rep = preflight.observation_preflight(app)
        assert seen == [] and stable() == before
        assert rep["verdict"] == "BLOCKED"
    finally:
        app.db.conn.set_authorizer(None)
        app.db.close()
