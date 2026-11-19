"""DIRECT MARKET HUB — concurrence (fils, processus), partage avec le broker fictif existant, charge, sécurité statique."""
import ast
import logging
import multiprocessing as mp
import re
import time
import tracemalloc
from pathlib import Path

import pytest

from sentinelle.dmh.ledger import Ledger, LedgerError, MainAccountCash, StandaloneCash
from sentinelle.dmh.router import Intent, build_routes, choose
from sentinelle.execution.base import OrderError
from sentinelle.execution.paper import PaperBroker
from sentinelle.market.markets import generic_market
from sentinelle.storage.db import Database

from tests import dmh_support as S

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parent.parent
DMH = ROOT / "sentinelle" / "dmh"


def _proc(path, start, results):
    db = Database(path)
    led = Ledger(db, 3)
    led.cash = StandaloneCash(led)
    start.wait()
    ok = 0
    for i in range(5):
        try:
            led.open_operation(f"{mp.current_process().name}-{i}", origin="agent", side="buy", asset="ETH", intent={}, route={},
                               verdict={}, config_hash="h", evidence_mode="test", reserve=("EUR", "cash", 2.5))
            ok += 1
        except LedgerError:
            pass
        except Exception as exc:  # base occupée au-delà du délai : refus (jamais un double engagement)
            results.put(("error", type(exc).__name__))
    results.put(("ok", ok))
    db.close()


def test_separate_processes_never_commit_the_same_euros(tmp_path):
    path = tmp_path / "shared.db"
    led = Ledger(Database(path), 3)
    led.cash = StandaloneCash(led)
    ctx = mp.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=_proc, args=(str(path), start, results), name=f"agent{i}") for i in range(4)]
    [p.start() for p in procs]
    start.set()
    [p.join(60) for p in procs]
    got = [results.get(timeout=5) for _ in range(len(procs))]
    ok = sum(n for kind, n in got if kind == "ok")
    assert ok == 12  # 12 × 2,50 € = 30 € : exactement le capital, jamais plus
    fresh = Ledger(Database(path), 3)
    fresh.cash = StandaloneCash(fresh)
    assert fresh.reserved("EUR", "cash") == pytest.approx(30.0) and fresh.cash.available() == pytest.approx(0.0)


def test_hub_and_existing_paper_broker_share_one_portfolio(tmp_path):
    db = Database(tmp_path / "app.db")
    db.execute("INSERT INTO accounts(id, name, kind, role, quote, initial_balance, created_at, data) VALUES (1, 'P', 'paper', 'main', 'EUR', 30, 0, '{}')")
    db.execute("INSERT INTO balances(account_id, asset, total) VALUES (1, 'EUR', 30.0)")
    broker = PaperBroker(db, 1, "EUR", lambda s: generic_market(s), lambda s: 60_000.0, 0.001, 5.0)
    led = Ledger(db, 1)
    led.cash = MainAccountCash(lambda: broker)
    broker.external_reserved = led.reserved_by_asset
    broker.external_value = led.liquidation_value
    assert broker.reserved() == {} and broker.available("EUR") == pytest.approx(30.0)
    led.open_operation("hub-0001", origin="user", side="buy", asset="BTC", intent={}, route={}, verdict={}, config_hash="h",
                       evidence_mode="test", reserve=("EUR", "cash", 20.0))
    assert broker.available("EUR") == pytest.approx(10.0)  # le broker existant voit la réservation du hub
    with pytest.raises(OrderError):
        broker.place_order("BTC/EUR", "buy", "market", 0.0003)  # ~18 € : impossible, 20 € sont réservés par le hub
    with pytest.raises(LedgerError):
        led.open_operation("hub-0002", origin="user", side="buy", asset="BTC", intent={}, route={}, verdict={}, config_hash="h",
                           evidence_mode="test", reserve=("EUR", "cash", 15.0))
    led.settle("hub-0001", "FILLED", [{"leg": 0, "venue": "binance", "side": "buy", "asset": "BTC", "location": "cex:binance",
                                       "qty": 0.0003, "counter_asset": "EUR", "counter_location": "cash", "counter_qty": 18.0, "eur": 18.0}], {})
    assert broker.balances()["EUR"] == pytest.approx(12.0)
    assert db.query("SELECT kind FROM ledger WHERE account_id = 1 ORDER BY id DESC LIMIT 1")[0]["kind"] == "hub_achat"
    assert broker.equity()[0] == pytest.approx(30.0)  # 12 € + position du hub au coût (pas encore de valorisation)
    with pytest.raises(OrderError):
        broker.adjust_cash(-100.0, "hub_achat", "test")  # jamais de solde négatif
    db.close()


def test_load_many_route_decisions_stay_fast_and_bounded():
    v = S.market()
    v.set_quote("solana", lambda a, b, amt: amt)
    reg = S.registry()
    tracemalloc.start()
    t0 = time.perf_counter()
    n = 300
    for i in range(n):
        routes = build_routes(v, reg, Intent(("BTC", "ETH", "SOL")[i % 3], 5 + i % 10, 100.0, ("solana",), True))
        choose(routes, 100.0)
    elapsed = time.perf_counter() - t0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert elapsed / n < 0.05, f"{elapsed / n * 1000:.1f} ms par décision"
    assert peak < 50 * 1024 * 1024


def test_load_many_ledger_operations(tmp_path):
    db = Database(tmp_path / "load.db")
    led = Ledger(db, 1)
    led.cash = StandaloneCash(led, initial=30.0)
    t0 = time.perf_counter()
    for i in range(400):
        led.open_operation(f"load-{i:05d}", origin="t", side="buy", asset="BTC", intent={}, route={}, verdict={}, config_hash="h",
                           evidence_mode="test", reserve=("EUR", "cash", 0.05))
        led.settle(f"load-{i:05d}", "FILLED", [{"leg": 0, "venue": "x", "side": "buy", "asset": "BTC", "location": "cex:x", "qty": 1e-6,
                                                "counter_asset": "EUR", "counter_location": "cash", "counter_qty": 0.05, "eur": 0.05}], {})
    per = (time.perf_counter() - t0) / 400
    assert led.reconcile()["ok"] and led.cash.total() == pytest.approx(10.0)
    assert per < 0.05, f"{per * 1000:.1f} ms par opération"
    db.close()


# -- sécurité statique et dépendances -------------------------------------------------------------------------------------------
def _trees():
    for p in sorted(DMH.glob("*.py")):
        yield p.name, ast.parse(p.read_text(encoding="utf-8"))


def test_the_hub_never_imports_the_historical_live_broker_or_signing_libraries():
    banned = ("sentinelle.execution.live", "ccxt", "web3", "eth_account", "solders", "solana", "nacl", "ecdsa", "coincurve", "bip_utils",
              "mnemonic", "cryptography.hazmat.primitives.asymmetric")
    for name, tree in _trees():
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [("." * node.level) + (node.module or "")]
            for m in mods:
                absolute = m.replace("..", "sentinelle.", 1) if m.startswith("..") else m
                assert not any(absolute == b or absolute.startswith(b + ".") for b in banned), f"{name} importe {m}"
                assert "execution.live" not in m and m not in (".live",), f"{name} importe {m}"


def test_write_rpc_methods_only_appear_in_deny_lists():
    pat = re.compile(r"(sendRawTransaction|sendTransaction|signTransaction|eth_sign)")
    for name, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and pat.search(node.value):
                assert name in ("net.py",), f"{name}:{node.lineno} mentionne {node.value}"


def test_every_mutating_hub_route_inspects_the_raw_request_first():
    src = (DMH / "api.py").read_text(encoding="utf-8")
    posts = re.findall(r"@api\.(post|put)\(\"(/api/dmh/[^\"]+)\"\)\n    async def \w+\(request: Request\):\n        (\w+)", src)
    routes = {path for _, path, _ in posts}
    assert routes == {"/api/dmh/preview", "/api/dmh/orders", "/api/dmh/open-orders/refresh", "/api/dmh/halt", "/api/dmh/resume",
                      "/api/dmh/reconcile", "/api/dmh/limits", "/api/dmh/strategy", "/api/dmh/solana/scan", "/api/dmh/memecoin/session/tick",
                      "/api/dmh/memecoin/worker/start", "/api/dmh/memecoin/worker/stop"}
    reads = set(re.findall(r'@api\.get\("(/api/dmh/[^"]+)"\)', src))
    assert reads == {"/api/dmh/status", "/api/dmh/markets", "/api/dmh/positions", "/api/dmh/history",
                     "/api/dmh/observatory", "/api/dmh/limits", "/api/dmh/memecoin/session",
                     "/api/dmh/memecoin/worker/status"}
    assert all(first in ("data", "await") for _, _, first in posts) and src.count("await body(request,") == len(posts)


def test_no_new_dependency_and_no_signing_library_is_installed():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for lib in ("web3", "eth-account", "solders", "solana", "pynacl", "bip-utils", "mnemonic"):
        assert lib not in req
    import importlib.util
    for mod in ("web3", "eth_account", "solders", "nacl"):
        assert importlib.util.find_spec(mod) is None, f"bibliothèque de signature présente : {mod}"
