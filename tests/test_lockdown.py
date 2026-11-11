"""Verrou « simulation uniquement » : aucun ordre réel ne peut être envoyé, modifié ou annulé.

Tout est faux ici : fausses clés, faux exchange, base temporaire, marché simulé.
Aucun accès réseau n'est nécessaire, et toute tentative de connexion fait échouer le test.
"""

import ast
import inspect
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import sentinelle
from sentinelle import safety
from sentinelle.agent.agent import MODES, TradingAgent
from sentinelle.api.server import create_api
from sentinelle.execution.live import LiveBroker
from sentinelle.execution.paper import PaperBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.market import sources
from sentinelle.market.sources import PublicExchange, SyntheticSource, make_ccxt_exchange
from sentinelle.notify.telegram import TelegramService
from sentinelle.risk.manager import RiskManager
from sentinelle.safety import RealTradingForbidden, forbid
from sentinelle.services import core as core_module
from sentinelle.services.common import AGENTS, RETIRED_AGENTS, UserError
from sentinelle.services.core import App
from sentinelle.services.secrets import SecretStore

from .conftest import market_fn

H = {"X-Sentinelle": "1"}
PKG = Path(sentinelle.__file__).resolve().parent
FAKE_KEY = "FAUSSE-CLE-API-0123456789"
FAKE_SECRET = "FAUX-SECRET-API-9876543210"
PRIVATE_CALLS = ("create_order", "create_orders", "edit_order", "cancel_order", "cancel_all_orders", "fetch_balance",
                 "fetch_order", "fetch_orders", "fetch_open_orders", "fetch_my_trades", "withdraw", "transfer",
                 "set_sandbox_mode", "fetch_deposit_address")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Accès réseau interdit dans les tests du verrou")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def fake_exchange():
    ex = Mock(name="faux_exchange")
    for name in ("apiKey", "secret", "password", "uid", "login", "privateKey", "walletAddress", "token", "twofa"):
        setattr(ex, name, None)
    return ex


def assert_untouched(ex):
    for name in PRIVATE_CALLS:
        getattr(ex, name).assert_not_called()


# ---------------------------------------------------------------------------
# 1. Il n'existe aucun interrupteur
# ---------------------------------------------------------------------------
def test_lock_has_no_switch(monkeypatch):
    assert safety.PAPER_ONLY is True
    src = (PKG / "safety.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"environ", "getenv", "settings", "argv"}, "le verrou ne doit lire ni réglage, ni variable d'environnement"
    for var in ("SENTINELLE_LIVE", "SENTINELLE_REAL", "LIVE", "PAPER_ONLY", "SENTINELLE_PAPER_ONLY", "TESTNET"):
        monkeypatch.setenv(var, "1")
    monkeypatch.setenv("SENTINELLE_PAPER_ONLY", "0")
    monkeypatch.setattr(safety, "PAPER_ONLY", False)  # même en modifiant la constante, rien ne s'ouvre
    with pytest.raises(RealTradingForbidden):
        LiveBroker(None, 1, "USDC", fake_exchange(), market_fn, lambda _: 1.0)
    with pytest.raises(RealTradingForbidden):
        forbid("essai")


def test_forbid_always_raises_and_never_logs_secrets(db):
    with pytest.raises(RealTradingForbidden) as err:
        forbid("essai d'ordre", db=db, compte=3, api_key=FAKE_KEY, secret=FAKE_SECRET, exchange_password="x", jeton_token="y", paire="BTC/USDC")
    assert err.value.code == "paper_only" and "essai d'ordre" in err.value.message
    row = db.one("SELECT * FROM logs WHERE category = 'sécurité' ORDER BY id DESC")
    assert row is not None and "essai d'ordre" in row["message"]
    dumped = json.dumps(row, default=str)
    assert FAKE_KEY not in dumped and FAKE_SECRET not in dumped
    assert "BTC/USDC" in dumped and '"compte"' in dumped.replace("\\", "")

    broken = Mock()
    broken.log.side_effect = RuntimeError("journal en panne")
    with pytest.raises(RealTradingForbidden):  # un journal en panne n'empêche jamais le refus
        forbid("essai", db=broken, compte=1)


# ---------------------------------------------------------------------------
# 2. Le code source ne contient plus d'appel privé à un exchange
# ---------------------------------------------------------------------------
def _trees():
    for path in sorted(PKG.rglob("*.py")):
        yield path.relative_to(PKG).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def test_source_has_no_private_exchange_call():
    forbidden = {"create_order", "create_orders", "create_market_order", "create_limit_order", "edit_order", "cancel_all_orders",
                 "fetch_balance", "fetch_my_trades", "fetch_open_orders", "fetch_closed_orders", "fetch_orders", "fetch_order",
                 "withdraw", "transfer", "set_sandbox_mode", "fetch_deposit_address", "fetch_positions", "set_leverage",
                 "set_margin_mode", "borrow_margin", "create_deposit_address"}
    hits = []
    for rel, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (node.attr in forbidden or node.attr.startswith(("private", "sapi"))):
                hits.append(f"{rel}:{node.lineno} .{node.attr}")
    assert hits == [], "appels privés d'exchange trouvés : " + ", ".join(hits)


def test_ccxt_is_only_imported_by_the_public_market_module():
    importers = set()
    for rel, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "ccxt" for a in node.names):
                importers.add(rel)
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "ccxt":
                importers.add(rel)
    assert importers == {"market/sources.py"}


def test_no_code_passes_credentials_to_an_exchange():
    """Aucun fichier, hors la liste de contrôle du client public, ne manipule les champs d'identification CCXT."""
    hits = []
    for rel, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in ("apiKey", "privateKey", "walletAddress") and rel != "market/sources.py":
                hits.append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr in ("apiKey", "privateKey", "walletAddress"):
                hits.append(f"{rel}:{node.lineno}")
    assert hits == []


# ---------------------------------------------------------------------------
# 3. Client de marché : public uniquement, sans identifiants
# ---------------------------------------------------------------------------
def test_market_client_factory_accepts_no_credentials():
    params = set(inspect.signature(make_ccxt_exchange).parameters)
    assert params == {"exchange_id", "public_mirror", "timeout_ms"}
    for kwargs in ({"api_key": FAKE_KEY}, {"secret": FAKE_SECRET}, {"password": "x"}, {"testnet": True}, {"apiKey": FAKE_KEY}):
        with pytest.raises(TypeError):
            make_ccxt_exchange("binance", **kwargs)


def test_public_exchange_refuses_everything_private():
    ex = make_ccxt_exchange("binance", public_mirror=True)  # aucune connexion : simple création de l'objet
    assert isinstance(ex, PublicExchange)
    assert callable(ex.fetch_ohlcv) and callable(ex.fetch_ticker) and callable(ex.fetch_order_book)
    for name in PRIVATE_CALLS + ("apiKey", "secret", "sign", "request", "fetch", "privatePostOrder", "sapiGetCapitalConfigGetall", "urls", "options"):
        with pytest.raises(RealTradingForbidden):
            getattr(ex, name)
    with pytest.raises(RealTradingForbidden):
        ex.apiKey = FAKE_KEY
    with pytest.raises(RealTradingForbidden):
        ex.secret = FAKE_SECRET
    with pytest.raises(RealTradingForbidden):
        del ex.apiKey
    inner = object.__getattribute__(ex, "_ex")
    assert not inner.apiKey and not inner.secret


def test_public_exchange_fails_closed_if_credentials_appear():
    inner = fake_exchange()
    ex = PublicExchange(inner)
    assert ex.fetch_ticker is inner.fetch_ticker
    inner.apiKey = FAKE_KEY  # quelqu'un contourne la façade et glisse une clé dans le client
    for name in ("fetch_ticker", "load_markets", "fetch_ohlcv"):
        with pytest.raises(RealTradingForbidden):
            getattr(ex, name)
    for field in sources.CREDENTIAL_FIELDS:
        loaded = fake_exchange()
        setattr(loaded, field, "valeur")
        with pytest.raises(RealTradingForbidden):
            PublicExchange(loaded)
    assert_untouched(inner)


# ---------------------------------------------------------------------------
# 4. Courtier réel : inconstructible, inutilisable même s'il existe déjà
# ---------------------------------------------------------------------------
def test_live_broker_cannot_be_built(db):
    ex = fake_exchange()
    for args in ((), (db, 42, "USDC", ex, market_fn, lambda _: 50_000.0)):
        with pytest.raises(RealTradingForbidden):
            LiveBroker(*args)
    with pytest.raises(RealTradingForbidden):
        LiveBroker(db=db, account_id=42, quote="USDC", exchange=ex, market_fn=market_fn, price_fn=lambda _: 1.0)

    class Derived(LiveBroker):
        pass

    with pytest.raises(RealTradingForbidden):
        Derived(db, 42, "USDC", ex, market_fn, lambda _: 1.0)
    assert_untouched(ex)


def test_every_method_of_a_retained_live_broker_refuses(db):
    ex = fake_exchange()
    broker = object.__new__(LiveBroker)  # objet créé sans constructeur, ou conservé par une ancienne version
    broker.db, broker.account_id, broker.quote, broker.ex = db, 42, "USDC", ex
    broker.market_fn, broker.price_fn, broker.listeners = market_fn, (lambda _: 50_000.0), []
    methods = [name for name, value in vars(LiveBroker).items() if callable(value) and name != "__init__"]
    assert {"place_order", "cancel_order", "fetch_balance", "sync", "reconcile", "process_bar", "balances", "equity"} <= set(methods)
    for name in methods:
        with pytest.raises(RealTradingForbidden):
            getattr(broker, name)("BTC/USDC", "buy", "market", 0.001)
    assert ex.mock_calls == []


# ---------------------------------------------------------------------------
# 5. Positions et agents : uniquement sur le courtier simulé
# ---------------------------------------------------------------------------
def test_position_manager_only_accepts_the_paper_broker(db, paper, clock):
    stranger = Mock(name="courtier_inconnu")
    stranger.account_id, stranger.quote, stranger.listeners, stranger.kind = 1, "USDC", [], "paper"

    class Lookalike(PaperBroker):  # une sous-classe pourrait rediriger les ordres : refusée
        pass

    pm = PositionManager(db, paper, clock=clock)
    pos = pm.open("BTC/USDC", 0.01, origin="manual", stop=49_000.0, target=52_000.0)
    assert pos["status"] == "open"
    orders_before = db.query("SELECT id, status FROM orders ORDER BY id")

    # Lié à un courtier non simulé, un gestionnaire est inerte : il ne s'abonne pas, ignore les exécutions, refuse tout.
    lookalike = Lookalike(db, paper.account_id, "USDC", market_fn, paper.price_fn)
    spec = Mock(spec=PaperBroker)
    spec.listeners = []
    for other in (stranger, lookalike, spec):
        inert = PositionManager(db, other, clock=clock)
        assert other.listeners == []
        for name, args, kwargs in (("open", ("BTC/USDC", 0.01), {"origin": "manual"}), ("close", (pos["id"],), {}),
                                   ("modify", (pos["id"],), {"stop": 48_000.0}), ("_place_protections", (pos,), {}),
                                   ("_resize_protections", (pos,), {})):
            with pytest.raises(RealTradingForbidden):
                getattr(inert, name)(*args, **kwargs)
        inert.on_fill({"purpose": "exit", "position_id": pos["id"], "side": "sell", "status": "filled"},
                      {"amount": 0.01, "price": 1.0, "fee": 0.0, "fee_asset": "USDC", "ts": clock()})
        assert pm.get(pos["id"])["status"] == "open"  # fausse exécution ignorée
    assert db.query("SELECT id, status FROM orders ORDER BY id") == orders_before
    assert db.scalar("SELECT COUNT(*) FROM logs WHERE category = 'sécurité' AND message LIKE '%inerte%'") == 3
    stranger.place_order.assert_not_called()
    stranger.cancel_order.assert_not_called()
    pm.broker = stranger  # le courtier est remplacé après coup
    for action in (lambda: pm.open("BTC/USDC", 0.01, origin="manual"), lambda: pm.close(pos["id"]),
                   lambda: pm.modify(pos["id"], stop=48_000.0), lambda: pm._place_protections(pos), lambda: pm._resize_protections(pos)):
        with pytest.raises(RealTradingForbidden):
            action()
    stranger.place_order.assert_not_called()
    stranger.cancel_order.assert_not_called()


def test_agent_has_no_live_mode(db, paper, pm, clock):
    assert "live" not in MODES and set(MODES) == {"training", "backtest"}
    risk = RiskManager(db, paper.account_id, lambda: {}, clock)
    for mode in ("live", "real", "reel", ""):
        with pytest.raises(RealTradingForbidden):
            TradingAgent("ia", mode, db, pm, risk, lambda: {}, lambda: None, timeframe="1h")
    pm.broker = Mock(name="courtier_inconnu")
    with pytest.raises(RealTradingForbidden):
        TradingAgent("ia", "training", db, pm, risk, lambda: {}, lambda: None, timeframe="1h")
    assert set(AGENTS) == {"ia_entrainement"} and "ia_reel" in RETIRED_AGENTS
    assert all(d["mode"] == "training" for d in AGENTS.values())


# ---------------------------------------------------------------------------
# 6. Application : anciens comptes, anciennes clés, anciens réglages
# ---------------------------------------------------------------------------
def make_app(tmp_path, monkeypatch, **overrides):
    import sentinelle.services.live_ops as live_ops

    factory = Mock(name="make_ccxt_exchange", return_value=fake_exchange())
    monkeypatch.setattr(live_ops, "make_ccxt_exchange", factory)
    monkeypatch.setattr(sources, "make_ccxt_exchange", factory)
    app = App(tmp_path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=5), start=False, settings_override={
        "market": {"source": "synthetic"}, "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]},
        "portfolio": {"initial": 10_000.0}, **overrides})
    return app, factory


def add_legacy(app):
    """Installe ce qu'une ancienne version aurait laissé : compte réel, position, stop chez l'exchange, clés, IA réelle activée."""
    aid = app.db.insert("accounts", {"name": "Portefeuille réel", "kind": "live", "role": "live", "quote": "USDC", "created_at": 1.0, "data": "{}"})
    pid = app.db.insert("positions", {"account_id": aid, "symbol": "BTC/USDC", "origin": "ai", "agent": "ia_reel", "status": "open",
                                      "qty": 0.01, "initial_qty": 0.01, "entry_price": 50_000.0, "entry_ts": 1.0, "stop_price": 48_000.0})
    oid = app.db.insert("orders", {"account_id": aid, "client_id": "ancien-1", "exchange_id": "EX-123", "symbol": "BTC/USDC", "side": "sell",
                                   "type": "stop", "amount": 0.01, "stop_price": 48_000.0, "status": "open", "origin": "ai",
                                   "position_id": pid, "purpose": "stop", "created_at": 1.0})
    app.db.execute("INSERT INTO agents(name, account_id, enabled, started_at) VALUES ('ia_reel', ?, 1, 1.0)", (aid,))
    app.secrets.set({"exchange_api_key": FAKE_KEY, "exchange_secret": FAKE_SECRET, "exchange_password": "ancien-mot-de-passe"})
    return aid, pid, oid


@pytest.fixture
def app(tmp_path, monkeypatch):
    instance, factory = make_app(tmp_path, monkeypatch)
    instance.factory = factory
    yield instance
    instance.db.close()


def boot(app):
    """Démarrage réel de l'application, puis un relevé de prix du marché simulé."""
    app._startup()
    app.hub.poll()


def test_startup_ignores_legacy_real_setup_without_erasing_it(app, tmp_path):
    aid, pid, oid = add_legacy(app)
    app.settings["live"]["testnet"] = False
    app._startup()
    app.factory.assert_not_called()
    assert aid not in app.runtimes and all(safety.is_paper_runtime(rt) for rt in app.runtimes.values())
    assert "ia_reel" not in app.agents
    assert app.db.one("SELECT enabled FROM agents WHERE name = 'ia_reel'")["enabled"] == 0
    # rien n'est effacé : compte, position, ordre et clés sont toujours là, intacts
    assert app.db.one("SELECT kind, archived FROM accounts WHERE id = ?", (aid,)) == {"kind": "live", "archived": 0}
    assert app.db.one("SELECT status, qty FROM positions WHERE id = ?", (pid,)) == {"status": "open", "qty": 0.01}
    assert app.db.one("SELECT status, exchange_id FROM orders WHERE id = ?", (oid,)) == {"status": "open", "exchange_id": "EX-123"}
    stored = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert stored["exchange_api_key"] == FAKE_KEY and stored["exchange_secret"] == FAKE_SECRET
    # ... mais les clés ne sortent jamais du fichier
    for name in ("exchange_api_key", "exchange_secret", "exchange_password"):
        assert app.secrets.get(name) is None
    status = app.paper_only_status()
    assert status["paper_only"] is True and status["legacy_keys"] is True
    assert status["legacy_accounts"][0]["id"] == aid and status["legacy_accounts"][0]["open_positions"] == 1
    assert status["legacy_accounts"][0]["open_orders"] == 1 and status["legacy_note"]
    ov = app.overview()
    assert ov["live"] == {"connected": False, "locked": True} and ov["paper_only"]["paper_only"] is True
    legacy = next(a for a in ov["accounts"] if a["id"] == aid)
    assert legacy["connected"] is False and legacy["legacy"] is True and "positions" not in legacy
    text = json.dumps(ov, default=str) + json.dumps(app.db.query("SELECT * FROM logs"), default=str)
    assert FAKE_KEY not in text and FAKE_SECRET not in text and "ancien-mot-de-passe" not in text
    assert app.db.scalar("SELECT COUNT(*) FROM logs WHERE category = 'sécurité'") >= 1


def test_legacy_account_is_refused_everywhere(app):
    aid, pid, oid = add_legacy(app)
    app._startup()
    acc = app.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
    calls = [
        lambda: app.runtime(aid),
        lambda: app._make_paper_runtime(acc),
        lambda: app.preview_order({"account_id": aid, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001}),
        lambda: app.place_manual_order({"account_id": aid, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001, "confirm": True}),
        lambda: app.place_manual_order({"account_id": aid, "symbol": "BTC/USDC", "side": "sell", "amount": 0.001, "confirm": True}),
        lambda: app.cancel_order(aid, oid),
        lambda: app.close_position(pid),
        lambda: app.modify_position(pid, stop=47_000.0),
        lambda: app.reset_paper_account(aid, 100.0),
        lambda: app.resume_account(aid),
        lambda: app.size_helper(aid, 50_000.0, 49_000.0, 1.0),
        lambda: app.reconnect_live(),
        lambda: app.start_agent("ia_reel", confirm="JE COMPRENDS LES RISQUES"),
        lambda: app.start_agent("ia_reel", auto=True),
    ]
    for call in calls:
        with pytest.raises(RealTradingForbidden):
            call()
    app.factory.assert_not_called()
    assert app.db.one("SELECT status FROM orders WHERE id = ?", (oid,))["status"] == "open"
    assert app.db.one("SELECT status, stop_price FROM positions WHERE id = ?", (pid,)) == {"status": "open", "stop_price": 48_000.0}
    assert app.db.scalar("SELECT COUNT(*) FROM orders WHERE account_id = ?", (aid,)) == 1  # aucun nouvel ordre
    assert app.db.scalar("SELECT COUNT(*) FROM logs WHERE category = 'sécurité' AND message LIKE 'Tentative refusée%'") >= len(calls)


def test_real_agent_cannot_start_even_when_everything_looks_ready(app, monkeypatch):
    add_legacy(app)
    app._startup()
    monkeypatch.setattr(app.models, "champion", lambda _tf: SimpleNamespace(version="modele-valide", metrics={"verdict": "validé"}))
    monkeypatch.setattr(app, "readiness", lambda: {"unlocked": True, "criteria": []})
    app.live_status = {"connected": True, "locked": False}
    submit = Mock()
    monkeypatch.setattr(app.jobs, "submit", submit)
    for kwargs in ({"confirm": "JE COMPRENDS LES RISQUES"}, {"auto": True}, {"confirm": "JE COMPRENDS LES RISQUES", "auto": True}):
        with pytest.raises(RealTradingForbidden):
            app.start_agent("ia_reel", **kwargs)
    with pytest.raises(UserError):
        app.start_agent("ia_inconnue")
    submit.assert_not_called()
    assert "ia_reel" not in app.agent_state and "ia_reel" not in {a["name"] for a in app.agents_status()}


def injected_runtime(app, kind="live", account_kind=None):
    """Compte actif qu'une ancienne version (ou une erreur de code) aurait laissé en mémoire."""
    aid = app.db.insert("accounts", {"name": "hérité", "kind": account_kind or kind, "role": "live" if kind == "live" else "manual",
                                     "quote": "USDC", "created_at": 1.0, "data": "{}"})
    account = app.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
    broker, positions, risk = Mock(name="courtier_hérité"), Mock(name="positions_héritées"), Mock(name="risque")
    broker.account_id, broker.quote, broker.kind = aid, "USDC", kind
    positions.broker = broker
    positions.open_positions.return_value = [{"id": 7, "symbol": "BTC/USDC", "origin": "ai", "agent": "ia_reel"}]
    rt = SimpleNamespace(account=account, broker=broker, positions=positions, risk=risk)
    app.runtimes[aid] = rt
    app.snapshots[aid] = {"equity": 1_000.0}
    return aid, rt


def assert_runtime_untouched(rt):
    for name in ("place_order", "cancel_order", "process_bar", "sync", "reconcile", "equity", "balances", "fetch_balance", "available"):
        getattr(rt.broker, name).assert_not_called()
    for name in ("open", "close", "modify", "_place_protections"):
        getattr(rt.positions, name).assert_not_called()


@pytest.mark.parametrize("kind,account_kind", [("live", "live"), ("paper", "live"), ("live", "paper"), ("paper", "paper")])
def test_retained_non_paper_runtime_is_evicted_and_never_used(app, kind, account_kind):
    """Un compte actif n'est accepté que si le compte ET le courtier sont réellement simulés."""
    boot(app)
    aid, rt = injected_runtime(app, kind, account_kind)
    paper_id = app.db.one("SELECT id FROM accounts WHERE role = 'main' AND kind = 'paper'")["id"]
    bought = app.place_manual_order({"account_id": paper_id, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001})
    assert bought["position"]["status"] == "open"

    with pytest.raises(RealTradingForbidden):
        app.place_manual_order({"account_id": aid, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001, "confirm": True})
    assert aid not in app.runtimes and aid not in app.snapshots  # écarté dès la première tentative

    for task in (app._poll_live, app._sync_live, app._reconcile_live, app._snapshot_equity, app.paper_runtimes,
                 lambda: app._on_tick("BTC/USDC", 1.0, 2.0, 0.5, 1.5, 0, 1000),
                 lambda: app.update_settings({"live": {"testnet": False, "budget": 5000.0}}),
                 lambda: app.update_settings({"paper": {"fee_rate": 0.002}}),
                 lambda: app.emergency("pause"),
                 lambda: app.emergency("flatten", "URGENCE", "all"),
                 app.overview):
        app.runtimes[aid] = rt  # même s'il revenait en mémoire
        task()
        assert aid not in app.runtimes
    assert_runtime_untouched(rt)
    app.factory.assert_not_called()
    # l'urgence a bien fermé la position fictive, et seulement elle
    assert app.db.one("SELECT status FROM positions WHERE id = ?", (bought["position"]["id"],))["status"] == "closed"
    assert app.db.scalar("SELECT COUNT(*) FROM logs WHERE category = 'sécurité' AND message LIKE '%écarté%'") >= 1


def test_broken_guard_refuses_instead_of_allowing(app, monkeypatch):
    """Si un contrôle de sécurité tombe en panne, l'opération est refusée (jamais autorisée par défaut)."""
    boot(app)
    paper_id = app.db.one("SELECT id FROM accounts WHERE role = 'main' AND kind = 'paper'")["id"]
    order = {"account_id": paper_id, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001}
    before = app.db.scalar("SELECT COUNT(*) FROM orders")

    def boom(*args, **kwargs):
        raise RuntimeError("contrôle en panne")

    monkeypatch.setattr(core_module, "is_paper_runtime", boom)
    with pytest.raises(RuntimeError):
        app.place_manual_order(order)
    monkeypatch.undo()

    monkeypatch.setattr(safety, "is_paper_account", boom)  # panne à l'intérieur du contrôle
    with pytest.raises(RealTradingForbidden):
        app.place_manual_order(order)
    monkeypatch.undo()

    import sentinelle.execution.paper as paper_module

    monkeypatch.setattr(paper_module, "PaperBroker", None)  # la référence du courtier simulé disparaît
    with pytest.raises(RealTradingForbidden):
        app.place_manual_order(order)
    monkeypatch.undo()

    monkeypatch.setattr(app.db, "log", boom)  # journal en panne : le refus reste un refus
    aid, rt = injected_runtime(app)
    with pytest.raises(RealTradingForbidden):
        app.place_manual_order({**order, "account_id": aid})
    assert_runtime_untouched(rt)
    monkeypatch.undo()

    assert app.db.scalar("SELECT COUNT(*) FROM orders") == before
    assert app.place_manual_order(order)["position"]["status"] == "open"  # contrôles rétablis : le papier fonctionne


def test_secret_store_never_returns_exchange_keys(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.set({"exchange_api_key": FAKE_KEY, "exchange_secret": FAKE_SECRET, "telegram_token": "123456:ABCDEFGHIJKLMNOP"})
    assert store.get("exchange_api_key") is None and store.get("exchange_secret") is None and store.get("exchange_password") is None
    assert store.get("telegram_token") == "123456:ABCDEFGHIJKLMNOP"
    status = store.status()
    assert status["exchange_api_key"] == {"label": status["exchange_api_key"]["label"], "set": True, "hint": None, "ignored": True}
    assert status["telegram_token"]["ignored"] is False and status["telegram_token"]["hint"] == "…MNOP"
    assert store.has_any(["exchange_api_key"]) is True
    store.set({"exchange_api_key": None, "exchange_secret": None})  # l'utilisateur peut les supprimer
    assert store.has_any(["exchange_api_key", "exchange_secret", "exchange_password"]) is False


def test_stored_live_settings_are_inert(tmp_path, monkeypatch):
    app, factory = make_app(tmp_path, monkeypatch, live={"testnet": False, "budget": 100_000.0, "exchange": "kraken"})
    try:
        add_legacy(app)
        app._startup()
        assert app.settings["live"]["testnet"] is False  # ancien réglage conservé tel quel...
        factory.assert_not_called()  # ... et sans aucun effet
        assert app.overview()["live"] == {"connected": False, "locked": True}
        assert all(safety.is_paper_runtime(rt) for rt in app.runtimes.values())
    finally:
        app.db.close()


# ---------------------------------------------------------------------------
# 7. API et Telegram
# ---------------------------------------------------------------------------
@pytest.fixture
def client(app):
    boot(app)
    with TestClient(create_api(app)) as c:
        assert c.post("/api/auth/setup", json={"password": "faux-mot-de-passe-de-test"}, headers=H).status_code == 200
        yield c


def refused(response):
    """Refus de sécurité explicite : 403 + code « paper_only » (pas un simple « introuvable »)."""
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "paper_only" and "simulation" in body["detail"].lower()
    return body


def test_api_refuses_real_operations_with_an_explicit_security_error(app, client):
    aid, pid, oid = add_legacy(app)
    refused(client.post("/api/orders", json={"account_id": aid, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001, "confirm": True}, headers=H))
    refused(client.post("/api/orders/preview", json={"account_id": aid, "symbol": "BTC/USDC", "side": "buy", "amount": 0.001}, headers=H))
    refused(client.delete(f"/api/orders/{oid}", params={"account_id": aid}, headers=H))  # ordre réel EXISTANT : refus, pas « introuvable »
    refused(client.post(f"/api/positions/{pid}/close", headers=H))
    refused(client.patch(f"/api/positions/{pid}", json={"stop": 47_000}, headers=H))
    refused(client.post(f"/api/accounts/{aid}/reset", json={"initial_balance": 100}, headers=H))
    refused(client.post(f"/api/accounts/{aid}/resume", headers=H))
    refused(client.post("/api/risk/size", json={"account_id": aid, "entry": 50_000, "stop": 49_000}, headers=H))
    refused(client.post("/api/live/connect", headers=H))
    refused(client.post("/api/agents/ia_reel/start", json={"confirm": "JE COMPRENDS LES RISQUES"}, headers=H))
    assert client.post("/api/agents/ia_reel/stop", json={}, headers=H).status_code == 400
    app.factory.assert_not_called()
    assert app.db.one("SELECT status FROM orders WHERE id = ?", (oid,))["status"] == "open"
    assert app.db.one("SELECT status FROM positions WHERE id = ?", (pid,))["status"] == "open"

    # l'urgence fonctionne pour l'argent fictif et ne touche pas l'ancien compte
    r = client.post("/api/emergency", json={"action": "flatten", "scope": "all", "confirm": "URGENCE"}, headers=H)
    assert r.status_code == 200
    assert app.db.one("SELECT status FROM positions WHERE id = ?", (pid,))["status"] == "open"

    sec = client.get("/api/security").json()
    assert sec["paper_only"] is True and sec["legacy_keys"] is True and sec["legacy_accounts"][0]["id"] == aid
    summary = client.get(f"/api/accounts/{aid}").json()
    assert summary["legacy"] is True and summary["connected"] is False
    assert "confirm_phrase" not in client.get("/api/ai/readiness").json()


def test_api_refuses_new_exchange_keys_but_allows_deleting_old_ones(app, client, tmp_path):
    for values in ({"exchange_api_key": FAKE_KEY}, {"exchange_secret": FAKE_SECRET}, {"exchange_password": "x"},
                   {"telegram_token": "123456:ABCDEFGHIJKLMNOP", "exchange_api_key": FAKE_KEY}):
        refused(client.put("/api/secrets", json={"values": values}, headers=H))
    assert not (tmp_path / "secrets.json").exists() or "exchange_api_key" not in (tmp_path / "secrets.json").read_text(encoding="utf-8")
    assert app.secrets.get("telegram_token") is None  # la demande mixte a été refusée en entier
    app.secrets.set({"exchange_api_key": FAKE_KEY, "exchange_secret": FAKE_SECRET})
    listing = client.get("/api/secrets")
    assert FAKE_KEY[-4:] not in listing.text and listing.json()["exchange_api_key"]["ignored"] is True
    r = client.put("/api/secrets", json={"values": {"exchange_api_key": None, "exchange_secret": ""}}, headers=H)
    assert r.status_code == 200 and r.json()["exchange_api_key"]["set"] is False
    assert client.put("/api/secrets", json={"values": {"telegram_token": "123456:ABCDEFGHIJKLMNOP"}}, headers=H).status_code == 200
    logs = json.dumps(app.db.query("SELECT * FROM logs"), default=str)
    assert FAKE_KEY not in logs and FAKE_SECRET not in logs


def test_api_settings_cannot_enable_anything_real(app, client):
    r = client.put("/api/settings", json={"live": {"testnet": False, "budget": 9_999.0}}, headers=H)
    assert r.status_code == 200
    refused(client.post("/api/live/connect", headers=H))
    app.factory.assert_not_called()
    assert client.get("/api/overview").json()["live"] == {"connected": False, "locked": True}


def test_paper_trading_still_works_through_the_api(app, client):
    acc = next(a for a in client.get("/api/accounts").json() if a["role"] == "main")
    r = client.post("/api/orders", json={"account_id": acc["id"], "symbol": "BTC/USDC", "side": "buy", "quote_amount": 200,
                                         "stop_loss": round(app.hub.price("BTC/USDC") * 0.9, 2)}, headers=H)
    assert r.status_code == 200, r.text
    pos = r.json()["position"]
    assert pos["status"] == "open" and pos["qty"] > 0
    summary = client.get(f"/api/accounts/{acc['id']}").json()
    assert summary["cash"] < acc["initial_balance"] and summary["balances"]["BTC"] == pytest.approx(pos["qty"])
    assert client.post(f"/api/positions/{pos['id']}/close", headers=H).json()["status"] == "closed"


def test_telegram_cannot_reach_anything_real(app, monkeypatch):
    aid, pid, oid = add_legacy(app)
    app._startup()
    _, rt = injected_runtime(app)
    app.settings["telegram"]["chat_id"] = "42"
    telegram = TelegramService(app)
    sent = []
    monkeypatch.setattr(telegram, "_call", lambda *a, **kw: None)
    monkeypatch.setattr(telegram, "send", lambda text, *a, **kw: sent.append(text) or True)
    telegram.handle_update({"callback_query": {"id": "x", "data": "urgence_ok", "message": {"chat": {"id": 42}}}})
    assert_runtime_untouched(rt)
    assert app.db.one("SELECT status FROM positions WHERE id = ?", (pid,))["status"] == "open"
    state = telegram.command("/etat")
    assert "simulation uniquement" in state and "Portefeuille réel" not in state
    assert "ia_reel" not in telegram.command("/pause")
    app.factory.assert_not_called()
