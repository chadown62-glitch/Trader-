"""Broker simulé (argent fictif) aussi réaliste que possible.

- prix réels (ou rejoués), frais de l'exchange, glissement de prix pessimiste ;
- pas de quantité, minimum par ordre, soldes réservés par les ordres ouverts ;
- stops déclenchés au plus haut/plus bas réellement atteint, avec gestion des
  « trous » de cotation (exécution au prix d'ouverture s'il est pire) ;
- ordres OCO : quand le stop ou l'objectif d'une position est exécuté, l'autre
  est annulé ;
- prudence : si un stop et un objectif sont touchés dans la même bougie, on
  considère que le stop est passé en premier.
"""

from __future__ import annotations

import time
import uuid
import math
from collections import defaultdict
from typing import Callable

from ..market.markets import MarketInfo
from ..storage.db import Database
from .base import OPEN_STATUSES, FillListener, OrderError

EPS = 1e-9


class PaperBroker:
    kind = "paper"

    def __init__(
        self,
        db: Database,
        account_id: int,
        quote: str,
        market_fn: Callable[[str], MarketInfo],
        price_fn: Callable[[str], float | None],
        fee_rate: float = 0.001,
        slippage_bps: float = 5.0,
        clock: Callable[[], float] = time.time,
    ):
        self.db = db
        self.account_id = account_id
        self.quote = quote
        self.market_fn = market_fn
        self.price_fn = price_fn
        self.fee_rate = fee_rate
        self.slip = slippage_bps / 10_000.0
        self.clock = clock
        self.listeners: list[FillListener] = []

    # -- soldes ---------------------------------------------------------------
    def balances(self) -> dict[str, float]:
        rows = self.db.query("SELECT asset, total FROM balances WHERE account_id = ?", (self.account_id,))
        return {r["asset"]: r["total"] for r in rows if abs(r["total"]) > EPS}

    def _total(self, asset: str) -> float:
        return float(
            self.db.scalar(
                "SELECT total FROM balances WHERE account_id = ? AND asset = ?",
                (self.account_id, asset),
                0.0,
            )
        )

    def _adjust(self, asset: str, delta: float) -> None:
        self.db.execute(
            "INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id, asset) DO UPDATE SET total = total + excluded.total",
            (self.account_id, asset, delta),
        )
        if self._total(asset) < -1e-7:
            raise OrderError(f"Solde {asset} négatif : incohérence comptable", "internal")

    def set_balance(self, asset: str, total: float) -> None:
        self.db.execute(
            "INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id, asset) DO UPDATE SET total = excluded.total",
            (self.account_id, asset, total),
        )

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        sql = f"SELECT * FROM orders WHERE account_id = ? AND status IN {OPEN_STATUSES}"
        params: list = [self.account_id]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        return self.db.query(sql + " ORDER BY id", params)

    def reserved(self) -> dict[str, float]:
        """Montants bloqués par les ordres ouverts (un groupe OCO ne réserve qu'une fois)."""
        res: dict[str, float] = defaultdict(float)
        groups: dict[tuple[str, str], float] = {}
        for o in self.open_orders():
            base, quote = o["symbol"].split("/")
            left = o["amount"] - o["filled"]
            if o["side"] == "sell":
                if o["oco_group"]:
                    key = (o["oco_group"], base)
                    groups[key] = max(groups.get(key, 0.0), left)
                else:
                    res[base] += left
            else:
                px = o["price"] or o["stop_price"] or self.price_fn(o["symbol"]) or 0.0
                res[quote] += left * px * (1 + self.fee_rate + self.slip)
        for (_, asset), amt in groups.items():
            res[asset] += amt
        return dict(res)

    def available(self, asset: str) -> float:
        return max(0.0, self._total(asset) - self.reserved().get(asset, 0.0))

    def equity(self) -> tuple[float, float]:
        """(valeur totale, liquidités) en monnaie de cotation."""
        bal = self.balances()
        cash = bal.get(self.quote, 0.0)
        total = cash
        for asset, qty in bal.items():
            if asset == self.quote or abs(qty) < EPS:
                continue
            px = self.price_fn(f"{asset}/{self.quote}")
            if px is None or not math.isfinite(px) or px <= 0:
                raise OrderError(f"Valorisation impossible : cours {asset}/{self.quote} indisponible.", "no_price")
            total += qty * px
        return total, cash

    # -- ordres ---------------------------------------------------------------
    def place_order(
        self, symbol, side, type, amount, price=None, stop_price=None, *, origin="manual", agent=None,
        purpose="manual", position_id=None, oco_group=None, reason=None,
    ) -> dict:
        # Reservation checks, insertions and immediate fills must be serialized
        # together, including across broker instances sharing the database.
        with self.db.transaction():
            return self._place_order_locked(symbol, side, type, amount, price, stop_price,
                                            origin=origin, agent=agent, purpose=purpose,
                                            position_id=position_id, oco_group=oco_group, reason=reason)

    def _place_order_locked(
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
        if not isinstance(symbol, str) or symbol.count("/") != 1 or symbol.split("/")[1] != self.quote:
            raise OrderError(f"Paire incompatible avec le portefeuille {self.quote} : conversion indisponible.", "quote_mismatch")
        if not math.isfinite(float(amount)) or float(amount) <= 0:
            raise OrderError("Quantité invalide", "invalid")
        if any(value is not None and (not math.isfinite(float(value)) or float(value) <= 0) for value in (price, stop_price)):
            raise OrderError("Prix d'ordre invalide", "invalid")
        if side not in ("buy", "sell"):
            raise OrderError("Sens d'ordre invalide")
        if type not in ("market", "limit", "stop"):
            raise OrderError("Type d'ordre invalide")
        try:
            m = self.market_fn(symbol)
        except KeyError:
            raise OrderError(f"Paire inconnue : {symbol}") from None
        last = self.price_fn(symbol)
        if last is not None and (not math.isfinite(last) or last <= 0):
            raise OrderError("Cours invalide ou indisponible", "no_price")
        amount = m.floor_amount(float(amount))
        if type == "limit":
            if not price or price <= 0:
                raise OrderError("Prix limite manquant")
            price = m.round_price(price)
        if type == "stop":
            if not stop_price or stop_price <= 0:
                raise OrderError("Prix de déclenchement manquant")
            stop_price = m.round_price(stop_price)
        if type == "market" and not last:
            raise OrderError("Prix indisponible pour le moment, réessayez", "no_price")
        ref = price if type == "limit" else stop_price if type == "stop" else last
        err = m.validate(amount, ref or 0.0)
        if err:
            raise OrderError(err, "min_size")
        if type == "stop" and last:
            if side == "sell" and last <= stop_price:
                raise OrderError("Le prix est déjà sous le stop : il se déclencherait immédiatement", "would_trigger")
            if side == "buy" and last >= stop_price:
                raise OrderError("Le prix est déjà au-dessus du déclenchement", "would_trigger")

        base, quote = symbol.split("/")
        if side == "buy":
            px = price if type == "limit" else stop_price if type == "stop" else last * (1 + self.slip)
            need = amount * px * (1 + self.fee_rate)
            if need > self.available(quote) + 1e-6:
                raise OrderError(
                    f"Fonds insuffisants : {need:,.2f} {quote} nécessaires, {self.available(quote):,.2f} disponibles",
                    "insufficient_funds",
                )
        else:
            extra = amount
            if oco_group:
                current = max(
                    [o["amount"] - o["filled"] for o in self.open_orders(symbol) if o["oco_group"] == oco_group and o["side"] == "sell"]
                    or [0.0]
                )
                extra = max(0.0, amount - current)
            if extra > self.available(base) + 1e-12:
                raise OrderError(
                    f"Quantité insuffisante : {amount:g} {base} demandés, {self.available(base):g} disponibles",
                    "insufficient_funds",
                )

        now = self.clock()
        order_id = self.db.insert(
            "orders",
            {
                "account_id": self.account_id,
                "client_id": f"p{self.account_id}-{uuid.uuid4().hex[:16]}",
                "symbol": symbol,
                "side": side,
                "type": type,
                "amount": amount,
                "price": price,
                "stop_price": stop_price,
                "status": "open",
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
        order = self.get_order(order_id)
        # Exécution immédiate : ordre au marché ou ordre limite « traversant »
        if type == "market":
            fill_px = last * (1 + self.slip) if side == "buy" else last * (1 - self.slip)
            return self._fill(order, fill_px, maker=False)
        if type == "limit" and last:
            if side == "buy" and price >= last:
                return self._fill(order, min(price, last * (1 + self.slip)), maker=False)
            if side == "sell" and price <= last:
                return self._fill(order, max(price, last * (1 - self.slip)), maker=False)
        return order

    def get_order(self, order_id: int) -> dict:
        o = self.db.one("SELECT * FROM orders WHERE id = ? AND account_id = ?", (order_id, self.account_id))
        if o is None:
            raise OrderError("Ordre introuvable", "not_found")
        return o

    def cancel_order(self, order_id: int, reason: str | None = None) -> dict:
        with self.db.transaction():
            o = self.get_order(order_id)
            if o["status"] not in OPEN_STATUSES:
                return o
            self.db.update(
                "orders",
                order_id,
                {"status": "cancelled", "updated_at": self.clock(), "error": reason},
            )
            return self.get_order(order_id)

    def _fill(self, order: dict, price: float, maker: bool) -> dict:
        fee_rate = self.fee_rate  # hypothèse prudente : même taux maker/taker
        now = self.clock()
        with self.db.transaction():
            # Another market poll or cancellation may have completed this order
            # since the caller read it. Recheck under the same write lock as the
            # balance update so a stale snapshot can never be filled twice.
            order = self.get_order(order["id"])
            if order["status"] not in OPEN_STATUSES:
                raise OrderError("Ordre déjà exécuté ou annulé", "not_open")
            base, quote = order["symbol"].split("/")
            amount = order["amount"] - order["filled"]
            if amount <= 0 or not math.isfinite(amount) or not math.isfinite(price) or price <= 0:
                raise OrderError("Exécution invalide", "invalid")
            if order["side"] == "buy":
                cost = amount * price
                fee = cost * fee_rate
                if self._total(quote) + 1e-6 < cost + fee:
                    raise OrderError("Fonds insuffisants à l'exécution", "insufficient_funds")
                self._adjust(quote, -(cost + fee))
                self._adjust(base, amount)
            else:
                if self._total(base) + 1e-12 < amount:
                    raise OrderError("Quantité insuffisante à l'exécution", "insufficient_funds")
                proceeds = amount * price
                fee = proceeds * fee_rate
                self._adjust(base, -amount)
                self._adjust(quote, proceeds - fee)
            fill_id = self.db.insert(
                "fills",
                {
                    "order_id": order["id"],
                    "account_id": self.account_id,
                    "symbol": order["symbol"],
                    "side": order["side"],
                    "amount": amount,
                    "price": price,
                    "fee": fee,
                    "fee_asset": quote,
                    "ts": now,
                },
            )
            self.db.update(
                "orders",
                order["id"],
                {
                    "status": "filled",
                    "filled": order["filled"] + amount,
                    "avg_price": price,
                    "fee": (order["fee"] or 0) + fee,
                    "fee_asset": quote,
                    "updated_at": now,
                },
            )
            if order["oco_group"]:
                for sib in self.open_orders(order["symbol"]):
                    if sib["oco_group"] == order["oco_group"] and sib["id"] != order["id"]:
                        self.cancel_order(sib["id"], "OCO : l'autre ordre a été exécuté")
            # Ledger, order and position updates form one transaction. A failed
            # position listener must not leave a debit without a tracked asset.
            filled = self.get_order(order["id"])
            fill = self.db.one("SELECT * FROM fills WHERE id = ?", (fill_id,))
            for fn in list(self.listeners):
                fn(filled, fill)
            return self.get_order(order["id"])

    # -- passage du temps -----------------------------------------------------
    def process_bar(self, symbol: str, o: float, h: float, l: float, c: float, start_ms: int, end_ms: int | None = None) -> int:
        """Déclenche les ordres en attente touchés pendant la période. Renvoie le nombre d'exécutions."""
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)) or not (l <= min(o, c) <= max(o, c) <= h):
            raise OrderError("Bougie incohérente", "invalid")
        orders = [x for x in self.open_orders(symbol) if x["created_at"] * 1000 <= start_ms + 1e-6]
        if not orders:
            return 0
        count = 0
        stops = [x for x in orders if x["type"] == "stop"]
        limits = [x for x in orders if x["type"] == "limit"]
        for od in stops + limits:  # les stops d'abord (hypothèse pessimiste)
            current = self.get_order(od["id"])
            if current["status"] not in OPEN_STATUSES:
                continue
            px = None
            if od["type"] == "stop":
                sp = od["stop_price"]
                if od["side"] == "sell" and l <= sp:
                    px = min(sp, o) * (1 - self.slip)
                elif od["side"] == "buy" and h >= sp:
                    px = max(sp, o) * (1 + self.slip)
            else:
                lp = od["price"]
                if od["side"] == "sell" and h >= lp:
                    px = max(lp, o) if o > lp else lp
                elif od["side"] == "buy" and l <= lp:
                    px = min(lp, o) if o < lp else lp
            if px is None:
                continue
            try:
                self._fill(current, px, maker=od["type"] == "limit")
                count += 1
            except OrderError as exc:
                # The failed fill transaction was rolled back. Persist the
                # rejection separately so a gapped order does not stay open
                # forever, but never overwrite a concurrent fill/cancellation.
                if exc.code == "insufficient_funds":
                    with self.db.transaction():
                        if self.get_order(od["id"])["status"] in OPEN_STATUSES:
                            self.db.update("orders", od["id"], {"status": "rejected", "error": exc.message, "updated_at": self.clock()})
                continue
        return count

    # -- réinitialisation -----------------------------------------------------
    def reset(self, initial_balance: float) -> None:
        with self.db.transaction():
            for o in self.open_orders():
                self.cancel_order(o["id"], "Compte réinitialisé")
            self.db.execute("DELETE FROM balances WHERE account_id = ?", (self.account_id,))
            self.set_balance(self.quote, initial_balance)
            self.db.execute(
                "UPDATE positions SET status = 'cancelled', reason_close = 'Compte réinitialisé' "
                "WHERE account_id = ? AND status IN ('pending', 'open')",
                (self.account_id,),
            )
