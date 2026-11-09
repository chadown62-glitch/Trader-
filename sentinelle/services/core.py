"""Cœur de l'application : comptes, agents, moteur temps réel, entraînement.

Un seul thread (« moteur ») modifie l'état du trading : relevés de prix,
brokers, agents, ordres manuels. Le panel et Telegram lui envoient des
commandes. Cela évite toute incohérence due à des accès simultanés.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .. import __version__
from ..agent.agent import TradingAgent
from ..ai.features import compute_features, warmup_bars
from ..ai.labeling import candidate_row
from ..ai.strategies import STRATEGIES, levels
from ..ai.trainer import build_dataset, train_walk_forward
from ..backtest.engine import run_backtest
from ..config import DEFAULT_SETTINGS, apply_update, deep_merge, timeframe_ms, validate_settings
from ..execution.base import OrderError
from ..execution.live import LiveBroker
from ..execution.paper import PaperBroker
from ..execution.positions import PositionManager
from ..market.history import HistoryStore
from ..market.hub import MarketHub
from ..market.sources import build_source, make_ccxt_exchange, market_from_ccxt
from ..risk.manager import RiskManager
from ..storage.db import Database, dumps, loads
from .bus import EventBus
from .jobs import JobManager
from .models import ModelStore
from .readiness import compute_readiness
from .secrets import SecretStore

log = logging.getLogger("sentinelle.core")

LIVE_CONFIRM = "JE COMPRENDS LES RISQUES"
EMERGENCY_CONFIRM = "URGENCE"

AGENTS = {
    "ia_entrainement": {"mode": "training", "role": "ai_training", "label": "IA — Entraînement (argent fictif)"},
    "ia_reel": {"mode": "live", "role": "live", "label": "IA — Portefeuille réel"},
}


class UserError(Exception):
    """Erreur à afficher telle quelle à l'utilisateur."""


@dataclass
class AccountRuntime:
    account: dict
    broker: Any
    positions: PositionManager
    risk: RiskManager


class LivePrices:
    """Prix de l'exchange réel (ou testnet) : peuvent différer de la source de données."""

    def __init__(self, exchange, ttl: float = 4.0):
        self.ex = exchange
        self.ttl = ttl
        self.cache: dict[str, tuple[float, float]] = {}

    def __call__(self, symbol: str) -> float | None:
        hit = self.cache.get(symbol)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        try:
            t = self.ex.fetch_ticker(symbol)
            px = float(t.get("last") or t.get("close") or 0) or None
        except Exception:
            return hit[1] if hit else None
        if px:
            self.cache[symbol] = (time.time(), px)
        return px


class Engine(threading.Thread):
    def __init__(self, app: "App"):
        super().__init__(name="sentinelle-moteur", daemon=True)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self.ready = threading.Event()
        self.heartbeat = 0.0

    def submit(self, fn: Callable, *args, **kwargs) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        if threading.current_thread() is self or not self.is_alive():
            try:
                fut.set_result(fn(*args, **kwargs))
            except BaseException as exc:
                fut.set_exception(exc)
            return fut
        self.q.put((fn, args, kwargs, fut))
        return fut

    def stop(self) -> None:
        self._stop.set()
        self.q.put(None)

    def drain(self, timeout: float = 0.0) -> None:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            try:
                item = self.q.get(timeout=max(0.0, remaining)) if remaining > 0 else self.q.get_nowait()
            except queue.Empty:
                return
            if item is None:
                return
            fn, args, kwargs, fut = item
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn(*args, **kwargs))
                except BaseException as exc:
                    fut.set_exception(exc)
            if timeout == 0.0:
                continue

    def run(self) -> None:
        app = self.app
        app._startup()
        self.ready.set()
        nxt = {"poll": 0.0, "equity": 0.0, "sync": 0.0, "reconcile": time.time() + 60, "maint": time.time() + 600,
               "learn": time.time() + 300, "markets": 0.0}
        while not self._stop.is_set():
            self.heartbeat = time.time()
            now = time.time()
            due = min(nxt.values())
            if due > now:
                self.drain(timeout=min(1.0, due - now))
                continue
            try:
                if now >= nxt["markets"]:
                    if not app.hub.markets:
                        app._load_markets()
                        if app.hub.markets:
                            app._refresh_watch()
                    nxt["markets"] = now + (30 if not app.hub.markets else 3600)
                if now >= nxt["poll"]:
                    if app.hub.markets:
                        app.hub.poll()
                        app._poll_live()
                    nxt["poll"] = time.time() + app.settings["market"]["poll_seconds"]
                if now >= nxt["equity"]:
                    app._snapshot_equity()
                    nxt["equity"] = now + 60
                if now >= nxt["sync"]:
                    app._sync_live()
                    nxt["sync"] = now + 10
                if now >= nxt["reconcile"]:
                    app._reconcile_live()
                    nxt["reconcile"] = now + 300
                if now >= nxt["maint"]:
                    app._maintenance()
                    nxt["maint"] = now + 3600
                if now >= nxt["learn"]:
                    app._maybe_retrain()
                    nxt["learn"] = now + 600
            except Exception:
                log.exception("Erreur dans la boucle du moteur")
                app.db.log("error", "moteur", "Erreur dans la boucle du moteur (voir journal technique)")
            self.drain(timeout=0.0)


class App:
    def __init__(self, data_dir: Path, *, source=None, start: bool = True, settings_override: dict | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "sentinelle.db")
        self.bus = EventBus()
        self.secrets = SecretStore(self.data_dir / "secrets.json")
        self.settings = self._load_settings(settings_override)
        self.source = source or build_source(self.settings)
        self.history = HistoryStore(self.data_dir / "history", self.source)
        self.hub = MarketHub(self.source, self.history, self.bus)
        self.hub.tick_handlers.append(self._on_tick)
        self.hub.candle_handlers.append(self._on_candle)
        self.models = ModelStore(self.db, self.data_dir / "models", self.bus)
        self.jobs = JobManager(self.bus)
        self.runtimes: dict[int, AccountRuntime] = {}
        self.agents: dict[str, TradingAgent] = {}
        self.agent_state: dict[str, dict] = {name: {"status": "arrêté", "message": None} for name in AGENTS}
        self.snapshots: dict[int, dict] = {}
        self.live_status: dict = {"connected": False, "error": None}
        self.live_prices: LivePrices | None = None
        self.started_at = time.time()
        self.extensions: list[Any] = []  # actualités, Telegram (démarrés par le serveur)
        self._feat_cache: dict[tuple, tuple[float, Any]] = {}
        self._ensure_default_accounts()
        self.engine = Engine(self)
        if start:
            self.engine.start()

    # ======================================================================
    # Outils
    # ======================================================================
    def market_clock(self) -> float:
        """Heure de la source de marché (identique à l'heure réelle, sauf en simulation de tests)."""
        return self.source.now_ms() / 1000

    def call(self, fn: Callable, *args, **kwargs) -> concurrent.futures.Future:
        return self.engine.submit(fn, *args, **kwargs)

    async def acall(self, fn: Callable, *args, **kwargs):
        return await asyncio.wrap_future(self.call(fn, *args, **kwargs))

    def close(self) -> None:
        for ext in self.extensions:
            try:
                ext.stop()
            except Exception:
                pass
        self.engine.stop()
        if self.engine.is_alive():
            self.engine.join(timeout=10)
        self.db.close()

    # ======================================================================
    # Réglages
    # ======================================================================
    def _load_settings(self, override: dict | None) -> dict:
        row = self.db.one("SELECT data FROM settings WHERE id = 1")
        stored = loads(row["data"], {}) if row else {}
        try:
            s = validate_settings(deep_merge(DEFAULT_SETTINGS, stored))
        except Exception:
            log.exception("Réglages enregistrés invalides : valeurs par défaut utilisées")
            s = copy.deepcopy(DEFAULT_SETTINGS)
        if override:
            s = validate_settings(deep_merge(s, override))
        if row is None:
            self.db.execute("INSERT INTO settings(id, data, updated_at) VALUES (1, ?, ?)", (dumps(s), time.time()))
        return s

    def update_settings(self, patch: dict) -> dict:
        new = apply_update(self.settings, patch)
        old = self.settings
        self.db.execute("UPDATE settings SET data = ?, updated_at = ? WHERE id = 1", (dumps(new), time.time()))
        self.db.insert("settings_history", {"ts": time.time(), "data": dumps(patch)})
        self.settings = new
        self.db.log("info", "réglages", "Réglages modifiés", patch)
        if new["general"]["watchlist"] != old["general"]["watchlist"] or new["ai"]["timeframe"] != old["ai"]["timeframe"]:
            self._refresh_watch()
            for name in list(self.agents):
                self._restart_agent_later(name)
        for rt in self.runtimes.values():
            if isinstance(rt.broker, PaperBroker):
                rt.broker.fee_rate = new["paper"]["fee_rate"]
                rt.broker.slip = new["paper"]["slippage_bps"] / 10_000
        self.bus.publish("settings", {})
        return new

    # ======================================================================
    # Démarrage
    # ======================================================================
    def _ensure_default_accounts(self) -> None:
        quote = self.settings["general"]["quote"]
        initial = self.settings["paper"]["initial_balance"]
        for role, name in (("ai_training", "Entraînement IA"), ("manual", "Mon compte papier")):
            if not self.db.one("SELECT id FROM accounts WHERE role = ? AND archived = 0", (role,)):
                acc = self.db.insert("accounts", {"name": name, "kind": "paper", "role": role, "quote": quote,
                                                  "initial_balance": initial, "created_at": time.time(), "data": "{}"})
                self.db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?)", (acc, quote, initial))

    def _startup(self) -> None:
        self._load_markets()
        for acc in self.db.query("SELECT * FROM accounts WHERE archived = 0 AND kind = 'paper'"):
            self._make_paper_runtime(acc)
        if self.secrets.get("exchange_api_key"):
            self._connect_live()
        self._refresh_watch()
        for row in self.db.query("SELECT * FROM agents WHERE enabled = 1"):
            if row["name"] in AGENTS:
                try:
                    self.start_agent(row["name"], auto=True)
                except UserError as exc:
                    self.db.update("agents", row["name"], {"enabled": 0}, key="name")
                    self.db.log("warning", "agents", f"{AGENTS[row['name']]['label']} non relancé : {exc}")

    def _load_markets(self) -> None:
        try:
            self.hub.load_markets()
            self.hub.status["markets_error"] = None
        except Exception as exc:
            self.hub.status["markets_error"] = str(exc)[:300]
            log.warning("Chargement des marchés impossible : %s", exc)

    def _refresh_watch(self) -> None:
        wl = list(self.settings["general"]["watchlist"])
        quote = self.settings["general"]["quote"]
        extra = {p["symbol"] for p in self.db.query("SELECT DISTINCT symbol FROM positions WHERE status IN ('open', 'pending')")}
        btc = f"BTC/{quote}"
        syms = set(wl) | extra | ({btc} if not self.hub.markets or btc in self.hub.markets else set())
        self.hub.set_watch(syms)

    def _make_paper_runtime(self, acc: dict) -> AccountRuntime:
        p = self.settings["paper"]
        clock = self.market_clock
        broker = PaperBroker(self.db, acc["id"], acc["quote"], self.hub.market, self.hub.price, p["fee_rate"], p["slippage_bps"], clock)
        rt = AccountRuntime(acc, broker, PositionManager(self.db, broker, self.bus, clock),
                            RiskManager(self.db, acc["id"], lambda: self.settings, clock))
        rt.positions.close_listeners.append(self._on_position_closed)
        self.runtimes[acc["id"]] = rt
        return rt

    # ======================================================================
    # Compte réel
    # ======================================================================
    def live_account(self) -> dict | None:
        return self.db.one("SELECT * FROM accounts WHERE role = 'live' AND archived = 0")

    def _connect_live(self) -> dict:
        cfg = self.settings["live"]
        key, secret = self.secrets.get("exchange_api_key"), self.secrets.get("exchange_secret")
        if not key or not secret:
            self.live_status = {"connected": False, "error": "Clés API non renseignées"}
            return self.live_status
        try:
            ex = make_ccxt_exchange(cfg["exchange"], api_key=key, secret=secret, password=self.secrets.get("exchange_password"), testnet=cfg["testnet"])
            raw = ex.load_markets()
            markets = {s: market_from_ccxt(m, ex.precisionMode) for s, m in raw.items() if m.get("spot")}
            bal = ex.fetch_balance()
        except Exception as exc:
            from ..execution.live import map_ccxt_error

            err = map_ccxt_error(exc)
            self.live_status = {"connected": False, "error": err.message, "exchange": cfg["exchange"], "testnet": cfg["testnet"]}
            self.db.log("warning", "réel", f"Connexion à l'exchange impossible : {err.message}")
            return self.live_status
        quote = self.settings["general"]["quote"]
        acc = self.live_account()
        if acc is None:
            aid = self.db.insert("accounts", {"name": "Portefeuille réel", "kind": "live", "role": "live", "quote": quote,
                                              "created_at": time.time(), "data": "{}"})
            acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
        prices = LivePrices(ex)
        self.live_prices = prices

        def market_fn(symbol: str):
            if symbol not in markets:
                raise KeyError(symbol)
            return markets[symbol]

        broker = LiveBroker(self.db, acc["id"], quote, ex, market_fn, prices)
        rt = AccountRuntime(acc, broker, PositionManager(self.db, broker, self.bus), RiskManager(self.db, acc["id"], lambda: self.settings))
        rt.positions.close_listeners.append(self._on_position_closed)
        self.runtimes[acc["id"]] = rt
        total = {k: v for k, v in (bal.get("total") or {}).items() if v}
        self.live_status = {"connected": True, "error": None, "exchange": cfg["exchange"], "testnet": cfg["testnet"],
                            "account_id": acc["id"], "assets": len(total), "connected_at": time.time()}
        self.db.log("info", "réel", f"Connecté à {cfg['exchange']}{' (testnet)' if cfg['testnet'] else ''}")
        issues = broker.reconcile(rt.positions)
        if issues:
            self.bus.publish("alert", {"level": "warning", "message": "Réconciliation : " + " ; ".join(issues)})
        return self.live_status

    def reconnect_live(self) -> dict:
        acc = self.live_account()
        if acc and acc["id"] in self.runtimes:
            if "ia_reel" in self.agents:
                self.stop_agent("ia_reel")
            del self.runtimes[acc["id"]]
        return self._connect_live()

    def _poll_live(self) -> None:
        acc = self.live_account()
        rt = self.runtimes.get(acc["id"]) if acc else None
        if rt is None or self.live_prices is None:
            return
        for sym in {o["symbol"] for o in rt.broker.open_orders() if o["exchange_id"] == "virtual"}:
            prev = self.live_prices.cache.get(sym, (0, None))[1]
            px = self.live_prices(sym)
            if px and prev:
                rt.broker.process_bar(sym, prev, max(prev, px), min(prev, px), px, int((time.time() - 1) * 1000))

    def _sync_live(self) -> None:
        acc = self.live_account()
        rt = self.runtimes.get(acc["id"]) if acc else None
        if rt is None:
            return
        try:
            rt.broker.sync()
            self.live_status["error"] = None
        except OrderError as exc:
            self.live_status["error"] = exc.message

    def _reconcile_live(self) -> None:
        acc = self.live_account()
        rt = self.runtimes.get(acc["id"]) if acc else None
        if rt is None:
            return
        try:
            issues = rt.broker.reconcile(rt.positions)
            if issues:
                self.bus.publish("alert", {"level": "warning", "message": "Réconciliation : " + " ; ".join(issues)})
        except OrderError as exc:
            self.live_status["error"] = exc.message

    # ======================================================================
    # Flux de marché
    # ======================================================================
    def _on_tick(self, symbol, o, h, l, c, start_ms, end_ms) -> None:
        for rt in list(self.runtimes.values()):
            if isinstance(rt.broker, PaperBroker):
                rt.broker.process_bar(symbol, o, h, l, c, start_ms, end_ms)

    def _features_for(self, symbol: str, tf: str):
        series = self.hub.series(symbol, tf)
        if len(series) < 300:
            return None
        w = warmup_bars(tf) + 200
        window = series.iloc[-w:].reset_index(drop=True)
        btc_sym = f"BTC/{self.settings['general']['quote']}"
        btc = None
        if symbol != btc_sym:
            bs = self.hub.series(btc_sym, tf)
            btc = bs.iloc[-w:].reset_index(drop=True) if len(bs) else None
        return compute_features(window, tf, btc=btc)

    def _on_candle(self, symbol: str, tf: str, series) -> None:
        agents = [a for a in self.agents.values() if a.running and a.timeframe == tf and symbol in self.settings["general"]["watchlist"]]
        if not agents:
            return
        feat = self._features_for(symbol, tf)
        if feat is None:
            return
        self._feat_cache[(symbol, tf)] = (time.time(), feat)
        for agent in agents:
            agent.on_bar(symbol, feat)

    def _snapshot_equity(self) -> None:
        now = self.market_clock()
        for aid, rt in list(self.runtimes.items()):
            try:
                eq, cash = rt.broker.equity()
            except OrderError as exc:
                self.snapshots[aid] = {**self.snapshots.get(aid, {}), "error": exc.message}
                continue
            except Exception:
                continue
            if eq <= 0 and not isinstance(rt.broker, PaperBroker):
                continue
            self.db.execute("INSERT OR REPLACE INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (aid, now, eq, cash))
            self.snapshots[aid] = {"equity": eq, "cash": cash, "ts": now, "error": None}
            self.bus.publish("equity", {"account_id": aid, "equity": eq, "cash": cash, "ts": now})

    def _maintenance(self) -> None:
        week = time.time() - 7 * 86400
        self.db.execute("DELETE FROM equity WHERE ts < ? AND CAST(ts / 60 AS INTEGER) % 60 != 0", (week,))
        self.db.execute("DELETE FROM logs WHERE ts < ?", (time.time() - 60 * 86400,))
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    def _on_position_closed(self, pos: dict) -> None:
        self.bus.publish("trade_closed", pos)

    # ======================================================================
    # Comptes
    # ======================================================================
    def runtime(self, account_id: int) -> AccountRuntime:
        rt = self.runtimes.get(int(account_id))
        if rt is None:
            acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
            if acc is None:
                raise UserError("Compte introuvable")
            if acc["kind"] == "live":
                raise UserError("Portefeuille réel non connecté : renseignez vos clés API dans les réglages")
            if not self.hub.markets:
                raise UserError("Données de marché pas encore disponibles, patientez quelques secondes")
            rt = self._make_paper_runtime(acc)
        return rt

    def create_paper_account(self, name: str, initial: float) -> dict:
        name = (name or "").strip()[:60] or "Compte papier"
        if not (10 <= initial <= 10_000_000):
            raise UserError("Capital de départ entre 10 et 10 000 000")
        quote = self.settings["general"]["quote"]
        aid = self.db.insert("accounts", {"name": name, "kind": "paper", "role": "manual", "quote": quote,
                                          "initial_balance": initial, "created_at": time.time(), "data": "{}"})
        self.db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?)", (aid, quote, initial))
        acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
        if self.hub.markets:
            self._make_paper_runtime(acc)
        return acc

    def reset_paper_account(self, account_id: int, initial: float | None) -> None:
        rt = self.runtime(account_id)
        if not isinstance(rt.broker, PaperBroker):
            raise UserError("Seul un compte papier peut être réinitialisé")
        for name, agent in list(self.agents.items()):
            if agent.account_id == account_id:
                raise UserError("Arrêtez d'abord l'IA qui utilise ce compte")
        amount = float(initial or rt.account["initial_balance"] or self.settings["paper"]["initial_balance"])
        rt.broker.reset(amount)
        self.db.execute("DELETE FROM equity WHERE account_id = ?", (account_id,))
        self.db.execute("UPDATE accounts SET initial_balance = ?, data = '{}' WHERE id = ?", (amount, account_id))
        self.db.log("info", "comptes", f"Compte {rt.account['name']} réinitialisé à {amount:,.2f}")

    def account_summary(self, account_id: int) -> dict:
        acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if acc is None:
            raise UserError("Compte introuvable")
        rt = self.runtimes.get(account_id)
        snap = self.snapshots.get(account_id, {})
        out = {k: acc[k] for k in ("id", "name", "kind", "role", "quote", "initial_balance", "created_at")}
        out["connected"] = rt is not None
        if rt is None:
            return out
        if isinstance(rt.broker, PaperBroker):
            eq, cash = rt.broker.equity()
            balances = rt.broker.balances()
        else:
            eq, cash = snap.get("equity"), snap.get("cash")
            balances = {}
            cached = rt.broker._balance[1]
            if cached:
                balances = {k: float(v) for k, v in (cached.get("total") or {}).items() if v}
        risk = rt.risk.state(eq) if eq else {}
        positions = []
        for p in rt.positions.open_positions():
            u = rt.positions.unrealized(p)
            positions.append({**{k: p[k] for k in ("id", "symbol", "origin", "agent", "status", "qty", "entry_price", "entry_ts",
                                                    "stop_price", "target_price", "strategy", "p_win", "expected_r", "risk_amount",
                                                    "reason_open", "bars_held", "horizon_bars")}, **u})
        orders = [{k: o[k] for k in ("id", "symbol", "side", "type", "amount", "filled", "price", "stop_price", "status", "purpose",
                                      "origin", "created_at", "exchange_id")} for o in rt.broker.open_orders()]
        day_pnl = (eq - risk.get("day_start_equity", eq)) if eq else None
        total_pnl = (eq - acc["initial_balance"]) if (eq and acc["initial_balance"] and acc["kind"] == "paper") else None
        out.update(equity=eq, cash=cash, balances=balances, positions=positions, orders=orders, risk=risk,
                   day_pnl=day_pnl, total_pnl=total_pnl, error=snap.get("error"))
        return out

    # ======================================================================
    # Trading manuel
    # ======================================================================
    def _order_qty(self, rt: AccountRuntime, symbol: str, side: str, price: float, amount, quote_amount) -> float:
        if amount:
            return float(amount)
        if quote_amount:
            return float(quote_amount) / price
        raise UserError("Indiquez une quantité ou un montant")

    def preview_order(self, req: dict) -> dict:
        rt = self.runtime(req["account_id"])
        symbol = req["symbol"]
        try:
            m = rt.broker.market_fn(symbol)
        except KeyError:
            raise UserError(f"Paire inconnue : {symbol}")
        last = rt.broker.price_fn(symbol)
        price = req.get("price") if req.get("type") == "limit" else last
        if not price:
            raise UserError("Prix indisponible pour le moment")
        qty = m.floor_amount(self._order_qty(rt, symbol, req["side"], price, req.get("amount"), req.get("quote_amount")))
        fee_rate = getattr(rt.broker, "fee_rate", 0.001)
        value = qty * price
        eq, _ = rt.broker.equity() if isinstance(rt.broker, PaperBroker) else (self.snapshots.get(rt.account["id"], {}).get("equity") or 0, 0)
        warnings = rt.risk.manual_warnings(symbol, qty, price, req.get("stop_loss"), eq or 0) if req["side"] == "buy" else []
        err = m.validate(qty, price)
        risk_amount = (price - req["stop_loss"]) * qty if req.get("stop_loss") and req["side"] == "buy" else None
        out = {
            "symbol": symbol, "qty": qty, "price": price, "value": value, "fee": value * fee_rate,
            "warnings": warnings, "error": err, "risk_amount": risk_amount,
            "risk_pct": (risk_amount / eq * 100) if risk_amount and eq else None,
            "live": rt.account["kind"] == "live",
        }
        if out["live"] and value > self.settings["live"]["manual_max_order_value"]:
            out["error"] = (f"Ordre supérieur au plafond de sécurité ({self.settings['live']['manual_max_order_value']:,.0f} "
                            f"{rt.account['quote']}) défini dans les réglages")
        return out

    def place_manual_order(self, req: dict) -> dict:
        prev = self.preview_order(req)
        if prev["error"]:
            raise UserError(prev["error"])
        rt = self.runtime(req["account_id"])
        if prev["live"] and req.get("confirm") is not True:
            raise UserError("Confirmation requise pour un ordre en argent réel")
        symbol, side, otype = req["symbol"], req["side"], req.get("type", "market")
        try:
            if side == "buy":
                if otype not in ("market", "limit"):
                    raise UserError("Achat : ordre au marché ou à cours limité uniquement")
                pos = rt.positions.open(
                    symbol, prev["qty"], origin="manual", stop=req.get("stop_loss"), target=req.get("take_profit"),
                    entry_type=otype, limit_price=req.get("price") if otype == "limit" else None,
                    reason=req.get("note") or "Trade manuel",
                )
                result = {"position": pos}
            else:
                order = rt.broker.place_order(symbol, "sell", otype, prev["qty"], price=req.get("price"),
                                              stop_price=req.get("stop_price"), origin="manual", purpose="manual",
                                              reason=req.get("note") or "Vente manuelle")
                result = {"order": order}
        except OrderError as exc:
            if exc.code == "insufficient_funds" and side == "sell":
                raise UserError(exc.message + " (si la crypto est bloquée par le stop d'une position, fermez plutôt la position)")
            raise UserError(exc.message)
        self._refresh_watch()
        self.db.log("info", "trading", f"Ordre manuel {side} {prev['qty']:g} {symbol} sur « {rt.account['name']} »")
        result["warnings"] = prev["warnings"]
        return result

    def cancel_order(self, account_id: int, order_id: int) -> dict:
        rt = self.runtime(account_id)
        try:
            o = rt.broker.cancel_order(order_id, "Annulé par l'utilisateur")
        except OrderError as exc:
            raise UserError(exc.message)
        if o.get("purpose") == "entry" and o.get("position_id"):
            pos = rt.positions.get(o["position_id"])
            if pos["status"] == "pending":
                self.db.update("positions", pos["id"], {"status": "cancelled", "reason_close": "Ordre d'achat annulé"})
        elif o.get("purpose") in ("stop", "target") and o.get("position_id"):
            field = "stop_price" if o["purpose"] == "stop" else "target_price"
            self.db.update("positions", o["position_id"], {field: None})
        return o

    def close_position(self, position_id: int, reason: str = "Clôture manuelle") -> dict:
        pos = self.db.one("SELECT * FROM positions WHERE id = ?", (position_id,))
        if pos is None:
            raise UserError("Position introuvable")
        rt = self.runtime(pos["account_id"])
        try:
            return rt.positions.close(position_id, reason)
        except OrderError as exc:
            raise UserError(exc.message)

    def modify_position(self, position_id: int, stop=None, target=None, clear_target=False) -> dict:
        pos = self.db.one("SELECT * FROM positions WHERE id = ?", (position_id,))
        if pos is None:
            raise UserError("Position introuvable")
        rt = self.runtime(pos["account_id"])
        try:
            return rt.positions.modify(position_id, stop=stop, target=target, clear_target=clear_target)
        except OrderError as exc:
            raise UserError(exc.message)

    def size_helper(self, account_id: int, entry: float, stop: float, risk_pct: float | None) -> dict:
        rt = self.runtime(account_id)
        eq = self.account_summary(account_id).get("equity") or 0
        pct = risk_pct if risk_pct is not None else self.settings["risk"]["risk_per_trade"] * 100
        if stop >= entry or entry <= 0:
            raise UserError("Le stop doit être sous le prix d'entrée")
        risk_amount = eq * pct / 100
        qty = risk_amount / (entry - stop)
        return {"qty": qty, "value": qty * entry, "risk_amount": risk_amount, "risk_pct": pct, "equity": eq}

    def coach(self, symbol: str) -> dict:
        """Avis de l'IA sur une crypto (aide au trading manuel)."""
        tf = self.settings["ai"]["timeframe"]
        cached = self._feat_cache.get((symbol, tf))
        feat = cached[1] if cached and time.time() - cached[0] < 900 else None
        if feat is None:
            series = self.hub.series(symbol, tf)
            if len(series) < 300:
                series = self.history.load(symbol, tf)
            if len(series) < 300:
                return {"available": False, "message": "Avis indisponible : lancez l'IA ou un entraînement pour charger l'historique."}
            btc_sym = f"BTC/{self.settings['general']['quote']}"
            btc = None if symbol == btc_sym else self.history.load(btc_sym, tf)
            w = warmup_bars(tf) + 200
            feat = compute_features(series.iloc[-w:].reset_index(drop=True), tf,
                                    btc=btc.iloc[-w:].reset_index(drop=True) if btc is not None and len(btc) else None)
            self._feat_cache[(symbol, tf)] = (time.time(), feat)
        row = feat.iloc[-1]
        model = self.models.champion(tf)
        signals_now = []
        for name, st in STRATEGIES.items():
            if bool(st.signal(feat).iloc[-1]):
                stop, target = levels(st, float(row["close"]), float(row["atr"]))
                item = {"strategy": name, "label": st.label, "stop": stop, "target": target}
                if model is not None:
                    p, er = model.predict(pd.DataFrame([candidate_row(row, name, stop, 0.001, 0.0005)]))
                    item.update(p_win=float(p[0]), expected_r=float(er[0]))
                signals_now.append(item)
        from ..ai.features import REGIME_LABELS

        return {
            "available": True,
            "symbol": symbol,
            "timeframe": tf,
            "bar_ts": int(row["ts"]),
            "regime": REGIME_LABELS.get(row["regime"], row["regime"]),
            "btc_regime": REGIME_LABELS.get(row.get("btc_regime", "unknown"), "inconnu"),
            "rsi": float(row["rsi14"]) if row["rsi14"] == row["rsi14"] else None,
            "atr_pct": float(row["atr_pct"] * 100) if row["atr_pct"] == row["atr_pct"] else None,
            "trend_200d": float(row["trend_200d"] * 100) if row["trend_200d"] == row["trend_200d"] else None,
            "signals": signals_now,
            "model": model.version if model else None,
        }

    # ======================================================================
    # Agents IA
    # ======================================================================
    def agents_status(self) -> list[dict]:
        out = []
        for name, d in AGENTS.items():
            agent = self.agents.get(name)
            st = {"name": name, "label": d["label"], "mode": d["mode"], **self.agent_state.get(name, {})}
            acc = self.db.one("SELECT id, name FROM accounts WHERE role = ? AND archived = 0", (d["role"],))
            st["account_id"] = acc["id"] if acc else None
            st["account_name"] = acc["name"] if acc else None
            if agent:
                st.update(agent.status())
            row = self.db.one("SELECT started_at FROM agents WHERE name = ?", (name,))
            st["started_at"] = row["started_at"] if row else None
            model = self.models.champion(self.settings["ai"]["timeframe"])
            st["model"] = model.version if model else None
            out.append(st)
        return out

    def readiness(self) -> dict:
        acc = self.db.one("SELECT id FROM accounts WHERE role = 'ai_training' AND archived = 0")
        return compute_readiness(self.db, self.settings, acc["id"] if acc else None, self.models.champion(self.settings["ai"]["timeframe"]))

    def start_agent(self, name: str, confirm: str | None = None, auto: bool = False) -> dict:
        if name not in AGENTS:
            raise UserError("Agent inconnu")
        if name in self.agents or self.agent_state[name]["status"] == "préparation":
            return self.agent_state[name]
        d = AGENTS[name]
        tf = self.settings["ai"]["timeframe"]
        if d["mode"] == "live":
            if not self.live_status.get("connected"):
                raise UserError("Portefeuille réel non connecté (clés API manquantes ou refusées)")
            if self.models.champion(tf) is None:
                raise UserError("Aucun modèle d'IA validé : l'IA ne peut pas trader en réel")
            rd = self.readiness()
            if not rd["unlocked"]:
                missing = [c["label"] for c in rd["criteria"] if not c["ok"]]
                raise UserError("Mode réel verrouillé. Conditions manquantes : " + " ; ".join(missing))
            if not auto and (confirm or "").strip().upper() != LIVE_CONFIRM:
                raise UserError(f"Pour confirmer, tapez exactement : {LIVE_CONFIRM}")
            acc = self.live_account()
        else:
            acc = self.db.one("SELECT * FROM accounts WHERE role = 'ai_training' AND archived = 0")
        if acc is None:
            raise UserError("Compte associé introuvable")
        self.agent_state[name] = {"status": "préparation", "message": "Téléchargement de l'historique des prix…"}
        self.bus.publish("agent", {"name": name, **self.agent_state[name]})
        symbols = self._agent_symbols()
        days = max(self.settings["market"]["history_days"], 260 if tf != "15m" else 220)

        def prepare(progress):
            for k, sym in enumerate(symbols):
                progress(k / max(1, len(symbols)), f"Historique {sym}")
                self.history.update(sym, tf, days)
            self.call(self._activate_agent, name, acc["id"], tf, symbols, days).result(timeout=600)
            return {"symbols": symbols}

        try:
            self.jobs.submit("prepare", f"Préparation : {d['label']}", prepare)
        except RuntimeError as exc:
            self.agent_state[name] = {"status": "arrêté", "message": str(exc)}
            raise UserError(str(exc))
        return self.agent_state[name]

    def _agent_symbols(self) -> list[str]:
        quote = self.settings["general"]["quote"]
        syms = list(self.settings["general"]["watchlist"])
        btc = f"BTC/{quote}"
        if btc not in syms and (not self.hub.markets or btc in self.hub.markets):
            syms.append(btc)  # contexte de marché
        return syms

    def _activate_agent(self, name: str, account_id: int, tf: str, symbols: list[str], days: int) -> None:
        try:
            for sym in symbols:
                self.hub.track(sym, tf, days)
            rt = self.runtime(account_id)
            d = AGENTS[name]
            budget_fn = (lambda: self.settings["live"]["budget"]) if d["mode"] == "live" else None
            news_fn = None
            for ext in self.extensions:
                if hasattr(ext, "score_for"):
                    news_fn = ext.score_for
            agent = TradingAgent(
                name, d["mode"], self.db, rt.positions, rt.risk, lambda: self.settings,
                lambda: self.models.champion(tf), timeframe=tf, news_fn=news_fn,
                fresh_fn=lambda s: self.hub.is_fresh(s, 180), budget_fn=budget_fn, bus=self.bus,
                clock=self.market_clock if d["mode"] != "live" else time.time,
            )
            agent.running = True
            for sym in symbols:  # ne pas rejouer la dernière bougie déjà clôturée
                s = self.hub.series(sym, tf)
                if len(s):
                    agent.last_bar[sym] = int(s["ts"].iloc[-1])
            self.agents[name] = agent
            now = time.time()
            self.db.execute(
                "INSERT INTO agents(name, account_id, enabled, started_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(name) DO UPDATE SET enabled = 1, account_id = excluded.account_id, started_at = excluded.started_at",
                (name, account_id, now),
            )
            self.agent_state[name] = {"status": "en marche", "message": None}
            self.db.log("info", "agents", f"{AGENTS[name]['label']} démarrée ({tf}, {', '.join(symbols)})")
        except Exception as exc:
            self.agent_state[name] = {"status": "arrêté", "message": f"Démarrage impossible : {exc}"}
            raise
        finally:
            self.bus.publish("agent", {"name": name, **self.agent_state[name]})

    def stop_agent(self, name: str, close_positions: bool = False) -> dict:
        agent = self.agents.pop(name, None)
        if agent:
            agent.running = False
            if close_positions:
                rt = self.runtimes.get(agent.account_id)
                if rt:
                    for p in rt.positions.open_positions(agent=name):
                        try:
                            rt.positions.close(p["id"], "Arrêt de l'IA")
                        except OrderError as exc:
                            self.db.log("error", "agents", f"Fermeture impossible ({p['symbol']}) : {exc.message}")
        self.db.execute("UPDATE agents SET enabled = 0 WHERE name = ?", (name,))
        self.agent_state[name] = {"status": "arrêté", "message": None}
        self.db.log("info", "agents", f"{AGENTS.get(name, {}).get('label', name)} arrêtée")
        self.bus.publish("agent", {"name": name, **self.agent_state[name]})
        return self.agent_state[name]

    def _restart_agent_later(self, name: str) -> None:
        self.stop_agent(name)
        try:
            self.start_agent(name, auto=True)
        except UserError as exc:
            self.db.log("warning", "agents", f"Redémarrage impossible : {exc}")

    # ======================================================================
    # Arrêt d'urgence
    # ======================================================================
    def emergency(self, action: str, confirm: str | None = None, scope: str = "ai") -> dict:
        stopped = [n for n in list(self.agents)]
        for n in stopped:
            self.stop_agent(n)
        closed, errors = 0, []
        if action == "flatten":
            if (confirm or "").strip().upper() != EMERGENCY_CONFIRM:
                raise UserError(f"Pour tout revendre, tapez exactement : {EMERGENCY_CONFIRM}")
            for rt in list(self.runtimes.values()):
                for p in rt.positions.open_positions():
                    if scope == "ai" and p["origin"] != "ai":
                        continue
                    try:
                        rt.positions.close(p["id"], "Arrêt d'urgence")
                        closed += 1
                    except OrderError as exc:
                        errors.append(f"{p['symbol']} : {exc.message}")
        self.db.log("critical" if action == "flatten" else "warning", "urgence",
                    f"Arrêt d'urgence ({action}) : {len(stopped)} IA arrêtée(s), {closed} position(s) fermée(s)", {"errors": errors})
        self.bus.publish("alert", {"level": "critical", "message": f"Arrêt d'urgence exécuté ({closed} position(s) fermée(s))"})
        return {"agents_stopped": stopped, "positions_closed": closed, "errors": errors}

    def resume_account(self, account_id: int) -> None:
        self.runtime(account_id).risk.resume()
        self.db.log("info", "risque", f"Reprise manuelle du compte {account_id} après arrêt de sécurité")

    # ======================================================================
    # Entraînement et backtests
    # ======================================================================
    def start_training(self, days: int | None = None, auto: bool = False) -> dict:
        tf = self.settings["ai"]["timeframe"]
        days = int(days or self.settings["market"]["history_days"])
        days = max(180, min(days, 3000))
        settings = copy.deepcopy(self.settings)

        def job(progress):
            return self._train(progress, settings, tf, days, auto)

        try:
            return self.jobs.submit("train", f"Entraînement de l'IA ({tf}, {days} jours)", job)
        except RuntimeError as exc:
            raise UserError(str(exc))

    def _histories(self, settings: dict, tf: str, days: int, progress) -> tuple[dict, Any]:
        quote = settings["general"]["quote"]
        wl = list(settings["general"]["watchlist"])
        btc_sym = f"BTC/{quote}"
        need = wl + ([btc_sym] if btc_sym not in wl else [])
        hist = {}
        for k, sym in enumerate(need):
            progress(0.25 * k / len(need), f"Téléchargement de l'historique {sym}")
            try:
                hist[sym] = self.history.update(sym, tf, days)
            except Exception as exc:
                if sym in wl:
                    raise UserError(f"Historique indisponible pour {sym} : {exc}")
        btc = hist.get(btc_sym) if btc_sym not in wl else None
        return {s: hist[s] for s in wl if s in hist}, btc

    def _train(self, progress, settings: dict, tf: str, days: int, auto: bool) -> dict:
        hist, btc = self._histories(settings, tf, days, progress)
        enabled = [n for n, on in settings["ai"]["strategies"].items() if on]
        fee, slip = settings["paper"]["fee_rate"], settings["paper"]["slippage_bps"] / 10_000
        progress(0.28, "Calcul des caractéristiques et des situations d'apprentissage")
        cand, _ = build_dataset(hist, tf, enabled, fee, slip, btc=btc)

        def sub(x, msg):
            progress(0.3 + 0.45 * x, msg)

        res = train_walk_forward(cand, tf, min_edge=settings["ai"]["min_edge_r"], progress=sub)
        report = dict(res.report)
        report.update(verdict=res.verdict, reasons=res.reasons, days=days, auto=auto, trained_at=time.time())
        if res.eval_model is not None and res.holdout_start:
            progress(0.8, "Simulation du portefeuille sur la période de contrôle (avec l'IA)")
            markets = {s: self.hub.markets[s] for s in hist if s in self.hub.markets}
            bt = run_backtest(hist, tf, settings, res.eval_model, start_ts=res.holdout_start, btc=btc, markets=markets)
            progress(0.9, "Simulation de référence (tous les signaux, sans l'IA)")
            base = run_backtest(hist, tf, settings, None, exploration=True, start_ts=res.holdout_start, btc=btc, markets=markets)
            report["backtest_model"] = {k: bt[k] for k in ("metrics", "equity_metrics", "equity", "buy_hold_pct", "final_equity", "initial", "fees_paid", "start", "end", "halted")}
            report["backtest_baseline"] = {k: base[k] for k in ("metrics", "equity_metrics", "equity", "buy_hold_pct", "final_equity", "initial", "fees_paid", "halted")}
        version = None
        if res.final_model is not None:
            row = self.models.register(res, report)
            version = row["version"]
        self.db.set_meta("last_train_ts", str(time.time()))
        self.db.set_meta("last_train_report", dumps({k: report.get(k) for k in ("verdict", "reasons", "summary", "n_resolved", "trained_at")}))
        level = "info" if res.ok else "warning"
        self.db.log(level, "ia", f"Entraînement terminé : modèle {res.verdict}" + (f" ({version})" if version else ""), {"reasons": res.reasons})
        self.bus.publish("training_done", {"verdict": res.verdict, "version": version, "reasons": res.reasons})
        return {"verdict": res.verdict, "ok": res.ok, "reasons": res.reasons, "version": version, "summary": report.get("summary"),
                "report": report}

    def _maybe_retrain(self) -> None:
        ai = self.settings["ai"]
        if not ai["learning"] or "ia_entrainement" not in self.agents or self.jobs.running("train"):
            return
        last = float(self.db.get_meta("last_train_ts", "0") or 0)
        if time.time() - last < ai["retrain_hours"] * 3600:
            return
        new_samples = self.db.scalar("SELECT COUNT(*) FROM candidates WHERE source = 'live' AND resolved_at > ?", (last,), 0)
        if last and new_samples < ai["min_new_samples"]:
            return
        self.db.log("info", "ia", "Ré-entraînement automatique avec les données les plus récentes")
        try:
            self.start_training(auto=True)
        except UserError:
            pass

    def start_backtest(self, days: int, use_model: bool) -> dict:
        tf = self.settings["ai"]["timeframe"]
        settings = copy.deepcopy(self.settings)
        days = max(30, min(int(days), 3000))

        def job(progress):
            warm = 210 if tf != "4h" else 230
            hist, btc = self._histories(settings, tf, days + warm, progress)
            model = self.models.champion(tf) if use_model else None
            if use_model and model is None:
                raise UserError("Aucun modèle validé : lancez la simulation « tous les signaux »")
            start = int((time.time() - days * 86400) * 1000)
            markets = {s: self.hub.markets[s] for s in hist if s in self.hub.markets}
            res = run_backtest(hist, tf, settings, model, start_ts=start, btc=btc, markets=markets, exploration=not use_model,
                               progress=lambda x, m: progress(0.3 + 0.7 * x, m))
            res["use_model"] = use_model
            res["model"] = model.version if model else None
            res["days"] = days
            return res

        try:
            return self.jobs.submit("backtest", f"Simulation sur {days} jours", job)
        except RuntimeError as exc:
            raise UserError(str(exc))

    # ======================================================================
    # Vue d'ensemble
    # ======================================================================
    def overview(self) -> dict:
        accounts = []
        for acc in self.db.query("SELECT * FROM accounts WHERE archived = 0 ORDER BY id"):
            try:
                s = self.account_summary(acc["id"])
            except Exception as exc:
                s = {"id": acc["id"], "name": acc["name"], "kind": acc["kind"], "role": acc["role"], "error": str(exc)}
            s.pop("orders", None)
            accounts.append(s)
        return {
            "version": __version__,
            "uptime_s": time.time() - self.started_at,
            "engine_alive": self.engine.is_alive() and time.time() - self.engine.heartbeat < 30,
            "market": self.hub.snapshot(),
            "accounts": accounts,
            "agents": self.agents_status(),
            "live": dict(self.live_status),
            "jobs": self.jobs.list()[:5],
            "settings_summary": {"watchlist": self.settings["general"]["watchlist"], "timeframe": self.settings["ai"]["timeframe"],
                                 "quote": self.settings["general"]["quote"], "source": self.settings["market"]["source"]},
            "extensions": {getattr(e, "name", "?"): e.status() for e in self.extensions if hasattr(e, "status")},
        }
