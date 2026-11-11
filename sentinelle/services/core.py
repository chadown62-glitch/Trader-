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
from pathlib import Path
from typing import Any, Callable


from .. import __version__
from ..agent.agent import TradingAgent
from ..ai.features import compute_features, warmup_bars
from ..config import DEFAULT_SETTINGS, SETTINGS_REV, apply_update, deep_merge, migrate_settings, validate_settings
from ..execution.base import OrderError
from ..execution.paper import PaperBroker
from ..execution.positions import PositionManager
from ..market.history import HistoryStore
from ..market.hub import MarketHub
from ..market.sources import build_source
from ..risk.manager import RiskManager
from ..safety import is_paper_runtime, require_paper_account, require_paper_runtime
from ..storage.db import Database, dumps, loads
from .agents import AgentsMixin
from .bus import EventBus
from .common import AGENTS, EMERGENCY_CONFIRM, RETIRED_AGENTS, AccountRuntime, UserError  # noqa: F401
from .jobs import JobManager
from .learning import LearningMixin
from .live_ops import LiveMixin
from .models import ModelStore
from .secrets import SecretStore
from .trading import TradingMixin
from .training import TrainingMixin

log = logging.getLogger("sentinelle.core")

class Engine(threading.Thread):
    def __init__(self, app: "App"):
        super().__init__(name="sentinelle-moteur", daemon=True)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
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
        self._stop_event.set()
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
        nxt = {"poll": 0.0, "equity": 0.0, "sweep": 0.0, "maint": time.time() + 600, "heartbeat": time.time() + 30, "liq": time.time() + 20,
               "learn": time.time() + 300, "markets": 0.0}
        while not self._stop_event.is_set():
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
                        app.hub.poll(between=lambda: self.drain(0.0))
                    nxt["poll"] = time.time() + app.settings["market"]["poll_seconds"]
                if now >= nxt["equity"]:
                    app._snapshot_equity()
                    nxt["equity"] = now + 60
                if now >= nxt["sweep"]:
                    app.paper_runtimes()  # sécurité : écarte tout compte actif qui ne serait pas simulé
                    nxt["sweep"] = now + 10
                if now >= nxt["maint"]:
                    app._maintenance()
                    nxt["maint"] = now + 3600
                if now >= nxt["liq"]:
                    app._poll_liquidations()
                    nxt["liq"] = now + 60
                if now >= nxt["heartbeat"]:
                    app._heartbeat()
                    nxt["heartbeat"] = now + 300
                if now >= nxt["learn"]:
                    app._maybe_retrain()
                    nxt["learn"] = now + 600
            except Exception:
                log.exception("Erreur dans la boucle du moteur")
                app.db.log("error", "moteur", "Erreur dans la boucle du moteur (voir journal technique)")
            self.drain(timeout=0.0)


class App(LiveMixin, TradingMixin, AgentsMixin, TrainingMixin, LearningMixin):
    """Application complète. Les méthodes sont réparties par thème dans les modules *Mixin."""

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
        # Ancien état du « portefeuille réel » : purement informatif, aucune décision n'en dépend.
        self.live_status: dict = {"connected": False, "locked": True, "error": None}
        self.started_at = time.time()
        self.extensions: list[Any] = []  # actualités, Telegram (démarrés par le serveur)
        self._feat_cache: dict[tuple, tuple[float, Any]] = {}
        self.monitoring_status: dict = {}
        from ..risk.shield import Shield

        book_fn = getattr(self.source, "fetch_order_book", None)
        self.shield = Shield(lambda: self.settings, book_fn=book_fn, db=self.db, bus=self.bus, clock=self.market_clock)
        self.hub.tick_handlers.append(self.shield.on_tick)
        self._liq_thread: threading.Thread | None = None
        self.crosscheck = None
        if self.settings["market"]["source"] != "synthetic":
            from ..market.crosscheck import CrossChecker

            self.crosscheck = CrossChecker(self.settings["market"]["source"])
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
        migrated = False
        if row is not None:
            stored, migrated = migrate_settings(stored, int(self.db.get_meta("settings_rev", "1") or 1))
        try:
            s = validate_settings(deep_merge(DEFAULT_SETTINGS, stored))
            if migrated:
                self.db.execute("UPDATE settings SET data = ?, updated_at = ? WHERE id = 1", (dumps(s), time.time()))
                self.db.log("info", "réglages", "Réglages mis à niveau (nouvelles sources officielles d'actualités)")
        except Exception:
            log.exception("Réglages enregistrés invalides : valeurs par défaut utilisées")
            s = copy.deepcopy(DEFAULT_SETTINGS)
        self.db.set_meta("settings_rev", str(SETTINGS_REV))
        if override:
            s = validate_settings(deep_merge(s, override))
        if row is None:
            self.db.execute("INSERT INTO settings(id, data, updated_at) VALUES (1, ?, ?)", (dumps(s), time.time()))
        return s

    def update_settings(self, patch: dict) -> dict:
        new = apply_update(self.settings, patch)
        if self.hub.markets and new["market"]["source"] == self.settings["market"]["source"]:
            unknown = [s for s in new["general"]["watchlist"] if s not in self.hub.markets]
            if unknown:
                raise UserError("Paire(s) inconnue(s) sur la source de données : " + ", ".join(unknown))
        old = self.settings
        self.db.execute("UPDATE settings SET data = ?, updated_at = ? WHERE id = 1", (dumps(new), time.time()))
        self.db.insert("settings_history", {"ts": time.time(), "data": dumps(patch)})
        self.settings = new
        self.db.log("info", "réglages", "Réglages modifiés", patch)
        if new["general"]["watchlist"] != old["general"]["watchlist"] or new["ai"]["timeframe"] != old["ai"]["timeframe"]:
            self._refresh_watch()
            for name in list(self.agents):
                self._restart_agent_later(name)
        for _aid, rt in self.paper_runtimes():  # écarte au passage tout compte actif non simulé
            rt.broker.fee_rate = new["paper"]["fee_rate"]
            rt.broker.slip = new["paper"]["slippage_bps"] / 10_000
        self.bus.publish("settings", {})
        return new

    # ======================================================================
    # Démarrage
    # ======================================================================
    def _ensure_default_accounts(self) -> None:
        # New shared principal; preserve all existing account rows and balances.
        # Atomic creation makes restarts idempotent and avoids a funded account
        # without its cash entry if the process is interrupted.
        with self.db.transaction():
            accounts = self.db.query("SELECT * FROM accounts WHERE role = 'principal' AND archived = 0")
            if len(accounts) > 1 or (accounts and (accounts[0]["kind"] != "paper" or accounts[0]["quote"] != "EUR")):
                raise UserError("Portefeuille principal incohérent : un seul compte papier EUR est autorisé")
            if not accounts:
                acc = self.db.insert("accounts", {"name": "Portefeuille principal", "kind": "paper", "role": "principal",
                                                  "quote": "EUR", "initial_balance": 30.0, "created_at": time.time(), "data": "{}"})
                self.db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?)", (acc, "EUR", 30.0))
                # Never silently redirect a previously enabled agent from a
                # historical account onto the new portfolio.
                self.db.execute("UPDATE agents SET enabled = 0 WHERE name = 'ia_entrainement' AND enabled = 1")
                self.db.log("warning", "comptes", "Portefeuille principal fictif créé à 30 EUR ; anciens comptes conservés en lecture seule et agent arrêté.")

    def _startup(self) -> None:
        self._load_markets()
        for acc in self.db.query("SELECT * FROM accounts WHERE archived = 0 AND kind = 'paper' AND role = 'principal'"):
            self._make_paper_runtime(acc)
        # Simulation uniquement : aucune connexion à un exchange, même si d'anciennes clés existent.
        self._connect_live()
        status = self.paper_only_status()
        if status["legacy_accounts"] or status["legacy_keys"]:
            self.db.log("warning", "sécurité",
                        "Simulation uniquement : anciens comptes réels et anciennes clés API conservés mais ignorés",
                        {"comptes": [a["id"] for a in status["legacy_accounts"]], "anciennes_cles": status["legacy_keys"]})
        self._refresh_watch()
        for row in self.db.query("SELECT * FROM agents WHERE enabled = 1"):
            if row["name"] in RETIRED_AGENTS:
                self.db.update("agents", row["name"], {"enabled": 0}, key="name")
                self.db.log("warning", "sécurité", f"{RETIRED_AGENTS[row['name']]} non relancée : simulation uniquement")
            elif row["name"] in AGENTS:
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
        extra = {p["symbol"] for p in self.db.query(
            "SELECT DISTINCT p.symbol FROM positions p JOIN accounts a ON a.id = p.account_id "
            "WHERE p.status IN ('open', 'pending') AND a.kind = 'paper' AND a.role = 'principal'")}
        btc = f"BTC/{quote}"
        syms = set(wl) | extra | ({btc} if not self.hub.markets or btc in self.hub.markets else set())
        self.hub.set_watch(syms)

    def _make_paper_runtime(self, acc: dict) -> AccountRuntime:
        require_paper_account(acc, "ouverture d'un compte non simulé", self.db)
        p = self.settings["paper"]
        clock = self.market_clock
        def fresh_price(symbol):
            return self.hub.price(symbol) if self.hub.is_fresh(symbol, 180) else None
        def fx_value(base, quote, quantity):
            from ..execution.fx_ledger import PaperFXLedger
            from ..market.fx import FXError
            from ..market.fx_provider import PublicFXProvider
            provider = PublicFXProvider(self.source, self.hub.markets, clock)
            # A missing or stale quote must never turn an asset into zero value.
            for via in (None, 'USD', 'USDC', 'USDT'):
                if via in (base, quote):
                    continue
                try:
                    book = provider.snapshot(base, quote, via=via)
                    out = book.convert(quantity, base, quote, now=clock(), max_age=60,
                                       via=via, require_depth=True)
                    PaperFXLedger(broker, clock).check_depth(out['legs'])
                    return float(out['amount'])
                except FXError:
                    continue
            raise OrderError(f"Valorisation impossible : taux {base}/{quote} indisponible ou périmé.", "no_price")
        broker = PaperBroker(self.db, acc["id"], acc["quote"], self.hub.market, fresh_price, p["fee_rate"], p["slippage_bps"], clock,
                             fx_value_fn=fx_value)
        rt = AccountRuntime(acc, broker, PositionManager(self.db, broker, self.bus, clock),
                            RiskManager(self.db, acc["id"], lambda: self.settings, clock))
        rt.positions.close_listeners.append(self._on_position_closed)
        self.runtimes[acc["id"]] = rt
        return rt

    # ======================================================================
    # Flux de marché
    # ======================================================================
    def _on_tick(self, symbol, o, h, l, c, start_ms, end_ms) -> None:
        for _aid, rt in self.paper_runtimes():
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
        for aid, rt in self.paper_runtimes():
            try:
                eq, cash = rt.broker.equity()
            except OrderError as exc:
                self.snapshots[aid] = {**self.snapshots.get(aid, {}), "error": exc.message}
                continue
            except Exception:
                continue
            self.db.execute("INSERT OR REPLACE INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (aid, now, eq, cash))
            self.snapshots[aid] = {"equity": eq, "cash": cash, "ts": now, "error": None}
            self.bus.publish("equity", {"account_id": aid, "equity": eq, "cash": cash, "ts": now})

    def _maintenance(self) -> None:
        week = time.time() - 7 * 86400
        self.db.execute("DELETE FROM equity WHERE ts < ? AND CAST(ts / 60 AS INTEGER) % 60 != 0", (week,))
        self.db.execute("DELETE FROM logs WHERE ts < ?", (time.time() - 60 * 86400,))
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        self.backup_if_due()

    # -- sauvegardes et surveillance ----------------------------------------------
    def backup_if_due(self, force: bool = False) -> Path | None:
        """Sauvegarde quotidienne de la base (data/backups), conservée N jours."""
        day = time.strftime("%Y%m%d")
        if not force and self.db.get_meta("last_backup_day") == day:
            return None
        folder = self.data_dir / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"sentinelle-{day}.db"
        try:
            self.db.backup(dest)
        except Exception as exc:
            self.db.log("error", "sauvegarde", f"Sauvegarde impossible : {exc}")
            return None
        keep = self.settings["monitoring"]["backup_keep_days"]
        for old_file in sorted(folder.glob("sentinelle-*.db"))[:-keep]:
            try:
                old_file.unlink()
            except OSError:
                pass
        self.db.set_meta("last_backup_day", day)
        self.db.log("info", "sauvegarde", f"Base sauvegardée : {dest.name}")
        return dest

    def _poll_liquidations(self) -> None:
        """Relevé des liquidations (OKX) dans un thread à part : ne bloque jamais le moteur."""
        cfg = self.settings["shield"]
        if self.settings["market"]["source"] == "synthetic" or not (cfg["enabled"] and cfg["liquidations"]):
            return
        if self._liq_thread is not None and self._liq_thread.is_alive():
            return
        self._liq_thread = threading.Thread(target=self.shield.poll_liquidations, name="sentinelle-liquidations", daemon=True)
        self._liq_thread.start()

    def _heartbeat(self) -> None:
        """Signal de vie vers un service de surveillance externe (alerte si le PC s'arrête)."""
        url = self.settings["monitoring"]["heartbeat_url"]
        if not url:
            return
        last_ok = self.hub.status.get("last_ok") or 0
        healthy = self.engine.is_alive() and (time.time() - last_ok < 600)
        target = url if healthy else url.rstrip("/") + "/fail"

        def ping():
            import httpx

            try:
                httpx.get(target, timeout=10, headers={"User-Agent": "Sentinelle"})
                self.monitoring_status = {"last_ping": time.time(), "healthy": healthy, "error": None}
            except Exception as exc:
                self.monitoring_status = {"last_ping": time.time(), "healthy": healthy, "error": str(exc)[:200]}

        threading.Thread(target=ping, daemon=True).start()

    def _on_position_closed(self, pos: dict) -> None:
        self.bus.publish("trade_closed", pos)

    # ======================================================================
    # Comptes
    # ======================================================================
    def runtime(self, account_id: int) -> AccountRuntime:
        """Compte actif, TOUJOURS simulé. Tout autre cas est refusé (et journalisé)."""
        aid = int(account_id)
        rt = self.runtimes.get(aid)
        if rt is None:
            acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
            if acc is None:
                raise UserError("Compte introuvable")
            require_paper_account(acc, "utilisation d'un compte non simulé", self.db)
            if acc.get("role") != "principal":
                raise UserError("Ancien compte papier conservé en lecture seule ; utilisez le portefeuille principal.")
            if not self.hub.markets:
                raise UserError("Données de marché pas encore disponibles, patientez quelques secondes")
            rt = self._make_paper_runtime(acc)
        if not is_paper_runtime(rt):
            self._evict_runtime(aid, "compte actif non simulé écarté")
            require_paper_runtime(rt, "utilisation d'un compte non simulé", self.db)
        if rt.account.get("role") != "principal":
            self._evict_runtime(aid, "ancien compte papier conservé en lecture seule")
            raise UserError("Ancien compte papier conservé en lecture seule ; utilisez le portefeuille principal.")
        return rt

    def create_paper_account(self, name: str, initial: float) -> dict:
        raise UserError("Un seul portefeuille principal est disponible ; les backtests utilisent des comptes isolés.")

    def reset_paper_account(self, account_id: int, initial: float | None) -> None:
        rt = self.runtime(account_id)
        if any(agent.account_id == account_id for agent in self.agents.values()):
            raise UserError("Arrêtez d'abord l'IA qui utilise ce compte")
        if initial is not None and float(initial) != 30.0:
            raise UserError("Le portefeuille principal doit être réinitialisé à 30 EUR fictifs.")
        amount = 30.0
        rt.broker.reset(amount)
        self.db.execute("DELETE FROM equity WHERE account_id = ?", (account_id,))
        self.db.execute("UPDATE accounts SET initial_balance = ?, data = '{}' WHERE id = ?", (amount, account_id))
        self.db.log("info", "comptes", f"Compte {rt.account['name']} réinitialisé à {amount:,.2f}")

    def account_summary(self, account_id: int) -> dict:
        acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if acc is None:
            raise UserError("Compte introuvable")
        out = {k: acc[k] for k in ("id", "name", "kind", "role", "quote", "initial_balance", "created_at")}
        if acc["kind"] != "paper":
            # ancien compte réel : affiché pour mémoire, jamais connecté ni utilisé
            out.update(connected=False, legacy=True, error="Ancien compte réel : conservé pour mémoire, inutilisable (simulation uniquement)")
            return out
        if acc["role"] != "principal":
            out.update(connected=False, legacy=True, balances={r["asset"]: r["total"] for r in self.db.query(
                "SELECT asset, total FROM balances WHERE account_id = ?", (account_id,))},
                error="Ancien compte papier conservé en lecture seule ; ses soldes n'ont pas été déplacés.")
            return out
        rt = self.runtimes.get(account_id)
        if rt is not None and not is_paper_runtime(rt):
            self._evict_runtime(account_id, "compte actif non simulé écarté")
            rt = None
        snap = self.snapshots.get(account_id, {})
        out["connected"] = rt is not None
        if rt is None:
            return out
        eq, cash = rt.broker.equity()
        balances = rt.broker.balances()
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
    # Vue d'ensemble
    # ======================================================================
    def overview(self) -> dict:
        self.paper_runtimes()  # sécurité : écarte tout compte actif qui ne serait pas simulé
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
            "live": {"connected": False, "locked": True},
            "paper_only": self.paper_only_status(),
            "jobs": self.jobs.list()[:5],
            "settings_summary": {"watchlist": self.settings["general"]["watchlist"], "timeframe": self.settings["ai"]["timeframe"],
                                 "quote": self.settings["general"]["quote"], "source": self.settings["market"]["source"]},
            "extensions": {getattr(e, "name", "?"): e.status() for e in self.extensions if hasattr(e, "status")},
            "shield": self.shield.status(),
        }
