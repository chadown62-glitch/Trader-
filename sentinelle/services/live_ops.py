"""Portefeuille réel : connexion à l'exchange, suivi des ordres, réconciliation."""

from __future__ import annotations

import time

from ..execution.base import OrderError
from ..execution.live import LiveBroker, map_ccxt_error
from ..execution.positions import PositionManager
from ..market.sources import make_ccxt_exchange, market_from_ccxt
from ..risk.manager import RiskManager
from .common import AccountRuntime, LivePrices


class LiveMixin:
    """Méthodes de l'application (portefeuille réel : connexion à l'exchange, suivi des ordres, réconciliation)."""

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
