"""Broker réel (ou testnet de l'exchange) via CCXT.

Règles de sécurité :
- le stop-loss est posé **chez l'exchange** : il protège même si le bot,
  le PC ou internet tombe en panne ;
- l'objectif de gain est surveillé par le bot (ordre « virtuel ») : en spot,
  un exchange bloque la quantité pour chaque ordre, on ne peut donc pas poser
  à la fois le stop et l'objectif sur la même quantité ;
- chaque ordre porte un identifiant unique : en cas de coupure réseau pendant
  l'envoi, on vérifie s'il a été reçu au lieu de le renvoyer (pas de doublon) ;
- réconciliation régulière : en cas de désaccord, l'exchange fait foi.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from ..market.markets import MarketInfo
from ..storage.db import Database
from .base import OPEN_STATUSES, FillListener, OrderError

log = logging.getLogger("sentinelle.live")

VIRTUAL = "virtual"

STATUS_MAP = {
    "open": "open",
    "closed": "filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "cancelled",
    "rejected": "rejected",
}


def map_ccxt_error(exc: Exception) -> OrderError:
    import ccxt

    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, ccxt.InsufficientFunds):
        return OrderError("Fonds insuffisants sur l'exchange", "insufficient_funds")
    if isinstance(exc, ccxt.OrderNotFound):
        return OrderError("Ordre introuvable sur l'exchange", "not_found")
    if isinstance(exc, (ccxt.AuthenticationError, ccxt.PermissionDenied, ccxt.AccountSuspended)):
        return OrderError("Clés API refusées par l'exchange (vérifiez les droits et l'adresse IP)", "auth")
    if isinstance(exc, ccxt.InvalidOrder):
        if "trigger immediately" in low or "-2010" in low and "stop" in low:
            return OrderError("Le stop se déclencherait immédiatement", "would_trigger")
        return OrderError(f"Ordre refusé par l'exchange : {msg[:200]}", "invalid")
    if isinstance(exc, (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.ExchangeNotAvailable)):
        return OrderError(f"Problème réseau avec l'exchange : {msg[:200]}", "network")
    if isinstance(exc, ccxt.BaseError):
        return OrderError(f"Erreur de l'exchange : {msg[:200]}", "exchange")
    return OrderError(f"Erreur inattendue : {msg[:200]}", "internal")


class LiveBroker:
    kind = "live"

    def __init__(
        self,
        db: Database,
        account_id: int,
        quote: str,
        exchange,
        market_fn: Callable[[str], MarketInfo],
        price_fn: Callable[[str], float | None],
        clock: Callable[[], float] = time.time,
        fee_rate: float = 0.001,
    ):
        self.db = db
        self.account_id = account_id
        self.quote = quote
        self.ex = exchange
        self.market_fn = market_fn
        self.price_fn = price_fn
        self.clock = clock
        self.fee_rate = fee_rate
        self.listeners: list[FillListener] = []
        self._balance: tuple[float, dict | None] = (0.0, None)
        self.last_error: str | None = None

    # -- appels exchange --------------------------------------------------------
    def _call(self, fn, *args, **kwargs):
        try:
            out = fn(*args, **kwargs)
            self.last_error = None
            return out
        except Exception as exc:  # traduit en erreur lisible
            err = map_ccxt_error(exc)
            self.last_error = err.message
            raise err from exc

    def fetch_balance(self, force: bool = False) -> dict:
        ts, cached = self._balance
        if not force and cached is not None and self.clock() - ts < 5:
            return cached
        bal = self._call(self.ex.fetch_balance)
        self._balance = (self.clock(), bal)
        return bal

    def _invalidate(self) -> None:
        self._balance = (0.0, None)

    def balances(self) -> dict[str, float]:
        total = self.fetch_balance().get("total") or {}
        return {k: float(v) for k, v in total.items() if v and abs(float(v)) > 1e-12}

    def available(self, asset: str) -> float:
        free = self.fetch_balance().get("free") or {}
        return float(free.get(asset) or 0.0)

    def equity(self) -> tuple[float, float]:
        bal = self.balances()
        cash = bal.get(self.quote, 0.0)
        total = cash
        for asset, qty in bal.items():
            if asset == self.quote:
                continue
            px = self.price_fn(f"{asset}/{self.quote}")
            if px:
                total += qty * px
        return total, cash

    # -- ordres -------------------------------------------------------------------
    def open_orders(self, symbol: str | None = None) -> list[dict]:
        sql = f"SELECT * FROM orders WHERE account_id = ? AND status IN {OPEN_STATUSES}"
        params: list = [self.account_id]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        return self.db.query(sql + " ORDER BY id", params)

    def get_order(self, order_id: int) -> dict:
        o = self.db.one("SELECT * FROM orders WHERE id = ? AND account_id = ?", (order_id, self.account_id))
        if o is None:
            raise OrderError("Ordre introuvable", "not_found")
        return o

    def place_order(
        self,
        symbol,
        side,
        type,
        amount,
        price=None,
        stop_price=None,
        *,
        origin="manual",
        agent=None,
        purpose="manual",
        position_id=None,
        oco_group=None,
        reason=None,
    ) -> dict:
        if side not in ("buy", "sell") or type not in ("market", "limit", "stop"):
            raise OrderError("Ordre invalide")
        try:
            m = self.market_fn(symbol)
        except KeyError:
            raise OrderError(f"Paire inconnue : {symbol}")
        last = self.price_fn(symbol)
        amount = m.floor_amount(float(amount))
        if type == "limit":
            if not price or price <= 0:
                raise OrderError("Prix limite manquant")
            price = m.round_price(price)
        if type == "stop":
            if not stop_price or stop_price <= 0:
                raise OrderError("Prix de déclenchement manquant")
            stop_price = m.round_price(stop_price)
            if last and ((side == "sell" and last <= stop_price) or (side == "buy" and last >= stop_price)):
                raise OrderError("Le stop se déclencherait immédiatement", "would_trigger")
        ref = price if type == "limit" else stop_price if type == "stop" else last
        if not ref:
            raise OrderError("Prix indisponible pour le moment", "no_price")
        err = m.validate(amount, ref)
        if err:
            raise OrderError(err, "min_size")

        # Objectif d'une position : surveillé par le bot (voir en-tête du module)
        virtual = type == "limit" and side == "sell" and purpose == "target"
        now = self.clock()
        client_id = f"stn{self.account_id}x{uuid.uuid4().hex[:20]}"
        order_id = self.db.insert(
            "orders",
            {
                "account_id": self.account_id,
                "client_id": client_id,
                "exchange_id": VIRTUAL if virtual else None,
                "symbol": symbol,
                "side": side,
                "type": type,
                "amount": amount,
                "price": price,
                "stop_price": stop_price,
                "status": "open" if virtual else "new",
                "origin": origin,
                "agent": agent,
                "position_id": position_id,
                "oco_group": oco_group,
                "purpose": purpose,
                "created_at": now,
                "updated_at": now,
                "reason": reason,
            },
        )
        if virtual:
            return self.get_order(order_id)

        params = {"clientOrderId": client_id}
        try:
            if type == "market":
                resp = self._call(self.ex.create_order, symbol, "market", side, amount, None, params)
            elif type == "limit":
                resp = self._call(self.ex.create_order, symbol, "limit", side, amount, price, params)
            else:
                resp = self._call(
                    self.ex.create_order, symbol, "market", side, amount, None, {**params, "stopLossPrice": stop_price}
                )
        except OrderError as err:
            if err.code == "network":
                # l'ordre a peut-être été reçu : on vérifie avant toute nouvelle tentative
                found = self._find_by_client_id(symbol, client_id)
                if found is not None:
                    self._apply(self.get_order(order_id), found)
                    self._invalidate()
                    return self.get_order(order_id)
            self.db.update("orders", order_id, {"status": "rejected", "error": err.message, "updated_at": self.clock()})
            self._invalidate()
            raise
        self._invalidate()
        if resp and resp.get("id") and (resp.get("status") is None or resp.get("filled") is None) and type == "market":
            try:  # certaines réponses sont incomplètes : on relit l'ordre
                resp = self._call(self.ex.fetch_order, resp["id"], symbol)
            except OrderError:
                pass
        self._apply(self.get_order(order_id), resp or {})
        return self.get_order(order_id)

    def _find_by_client_id(self, symbol: str, client_id: str) -> dict | None:
        for params in ({"origClientOrderId": client_id}, {"clientOrderId": client_id}):
            try:
                return self.ex.fetch_order(None, symbol, params)
            except Exception:
                continue
        return None

    def _apply(self, o: dict, ex_order: dict) -> None:
        """Met à jour un ordre local à partir de l'état renvoyé par l'exchange."""
        if not ex_order:
            return
        status = STATUS_MAP.get(ex_order.get("status") or "", o["status"])
        filled = float(ex_order.get("filled") or 0.0)
        avg = ex_order.get("average") or ex_order.get("price")
        avg = float(avg) if avg else None
        upd: dict = {"updated_at": self.clock()}
        if ex_order.get("id") and not o["exchange_id"]:
            upd["exchange_id"] = str(ex_order["id"])
        delta = filled - (o["filled"] or 0.0)
        fill_row = None
        if delta > 1e-12 and avg:
            prev_val = (o["avg_price"] or 0.0) * (o["filled"] or 0.0)
            px = (avg * filled - prev_val) / delta if o["filled"] else avg
            fee_total, fee_asset = self._fees(ex_order)
            fee_delta = max(0.0, fee_total - (o["fee"] or 0.0))
            fill_row = {
                "order_id": o["id"],
                "account_id": self.account_id,
                "symbol": o["symbol"],
                "side": o["side"],
                "amount": delta,
                "price": px,
                "fee": fee_delta,
                "fee_asset": fee_asset or self.quote,
                "ts": self.clock(),
            }
            upd.update(filled=filled, avg_price=avg, fee=fee_total, fee_asset=fee_asset or self.quote)
        if status == "open" and filled > 0:
            status = "partially_filled"
        upd["status"] = status
        with self.db.transaction():
            self.db.update("orders", o["id"], upd)
            fid = self.db.insert("fills", fill_row) if fill_row else None
        if fill_row:
            fill_row["id"] = fid
            updated = self.get_order(o["id"])
            if updated["status"] == "filled" and updated["oco_group"]:
                for sib in self.open_orders(updated["symbol"]):
                    if sib["oco_group"] == updated["oco_group"] and sib["id"] != updated["id"]:
                        try:
                            self.cancel_order(sib["id"], "OCO : l'autre ordre a été exécuté")
                        except OrderError:
                            log.warning("Annulation OCO impossible (ordre %s)", sib["id"])
            for fn in list(self.listeners):
                fn(self.get_order(o["id"]), fill_row)

    @staticmethod
    def _fees(ex_order: dict) -> tuple[float, str | None]:
        fees = ex_order.get("fees") or ([ex_order["fee"]] if ex_order.get("fee") else [])
        total, asset = 0.0, None
        for f in fees:
            if not f or f.get("cost") is None:
                continue
            if asset is None:
                asset = f.get("currency")
            if f.get("currency") == asset:
                total += float(f["cost"])
        return total, asset

    def cancel_order(self, order_id: int, reason: str | None = None) -> dict:
        o = self.get_order(order_id)
        if o["status"] not in OPEN_STATUSES:
            return o
        if o["exchange_id"] in (None, VIRTUAL):
            self.db.update("orders", order_id, {"status": "cancelled", "updated_at": self.clock(), "error": reason})
            return self.get_order(order_id)
        try:
            self._call(self.ex.cancel_order, o["exchange_id"], o["symbol"])
        except OrderError as err:
            if err.code != "not_found":
                raise
        # l'ordre a pu être exécuté juste avant l'annulation : on relit son état réel
        try:
            final = self._call(self.ex.fetch_order, o["exchange_id"], o["symbol"])
            self._apply(self.get_order(order_id), final)
        except OrderError:
            self.db.update("orders", order_id, {"status": "cancelled", "updated_at": self.clock(), "error": reason})
        cur = self.get_order(order_id)
        if cur["status"] in OPEN_STATUSES:
            self.db.update("orders", order_id, {"status": "cancelled", "updated_at": self.clock(), "error": reason})
        self._invalidate()
        return self.get_order(order_id)

    # -- suivi ------------------------------------------------------------------
    def sync(self) -> int:
        """Relit l'état des ordres ouverts chez l'exchange. Renvoie le nombre de mises à jour."""
        n = 0
        for o in self.open_orders():
            if o["exchange_id"] in (None, VIRTUAL):
                continue
            try:
                ex_order = self._call(self.ex.fetch_order, o["exchange_id"], o["symbol"])
            except OrderError as err:
                if err.code == "not_found":
                    self.db.update("orders", o["id"], {"status": "cancelled", "error": "Introuvable chez l'exchange", "updated_at": self.clock()})
                    n += 1
                continue
            before = (o["status"], o["filled"])
            self._apply(o, ex_order)
            after = self.get_order(o["id"])
            if (after["status"], after["filled"]) != before:
                n += 1
        if n:
            self._invalidate()
        return n

    def process_bar(self, symbol: str, o: float, h: float, l: float, c: float, start_ms: int, end_ms: int | None = None) -> int:
        """Déclenche les objectifs « virtuels » (le stop, lui, est chez l'exchange)."""
        count = 0
        for od in self.open_orders(symbol):
            if od["exchange_id"] != VIRTUAL or od["created_at"] * 1000 > start_ms + 1e-6:
                continue
            if od["side"] == "sell" and h >= od["price"]:
                self.db.update("orders", od["id"], {"status": "cancelled", "error": "Objectif atteint : vente au marché", "updated_at": self.clock()})
                for sib in self.open_orders(symbol):
                    if sib["oco_group"] and sib["oco_group"] == od["oco_group"]:
                        self.cancel_order(sib["id"], "Objectif atteint")
                remaining = od["amount"] - od["filled"]
                base = symbol.split("/")[0]
                qty = self.market_fn(symbol).floor_amount(min(remaining, self.available(base)))
                try:
                    self.place_order(
                        symbol, "sell", "market", qty, origin=od["origin"], agent=od["agent"],
                        purpose="target", position_id=od["position_id"], reason="Objectif atteint",
                    )
                    count += 1
                except OrderError as err:
                    log.error("Vente à l'objectif impossible (%s) : %s", symbol, err.message)
                    self.db.log("critical", "live", f"Vente à l'objectif impossible sur {symbol} : {err.message}")
        return count

    def reconcile(self, positions) -> list[str]:
        """Contrôle de cohérence au démarrage / périodique. Renvoie les anomalies constatées."""
        issues: list[str] = []
        self.sync()
        bal = self.balances()
        for pos in positions.open_positions():
            base = pos["symbol"].split("/")[0]
            held = bal.get(base, 0.0)
            m = self.market_fn(pos["symbol"])
            if pos["status"] == "open" and held + max(m.amount_step, 1e-12) < m.floor_amount(pos["qty"]) * 0.98:
                msg = f"{pos['symbol']} : la position du bot ({pos['qty']:g}) dépasse le solde réel ({held:g})"
                issues.append(msg)
            if pos["status"] == "open" and pos["stop_price"]:
                has_stop = any(o["purpose"] == "stop" for o in positions.protective_orders(pos["id"]))
                if not has_stop:
                    issues.append(f"{pos['symbol']} : stop absent, il est reposé")
                    try:
                        positions._place_protections(positions.get(pos["id"]))
                    except OrderError as err:
                        issues.append(f"{pos['symbol']} : impossible de reposer le stop ({err.message})")
        for msg in issues:
            self.db.log("warning", "reconcile", msg)
        return issues
