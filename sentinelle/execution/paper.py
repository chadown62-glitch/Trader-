"""Broker simulé (argent fictif) aussi réaliste que possible.

- prix réels (ou rejoués), frais de l'exchange, glissement de prix pessimiste ;
- pas de quantité, minimum par ordre, soldes réservés par les ordres ouverts ;
- stops déclenchés au plus haut/plus bas réellement atteint, avec gestion des
  « trous » de cotation (exécution au prix d'ouverture s'il est pire) ;
- ordres OCO : quand le stop ou l'objectif d'une position est exécuté, l'autre
  est annulé ;
- prudence : si un stop et un objectif sont touchés dans la même bougie, on
  considère que le stop est passé en premier ;
- aucun levier, aucune vente à découvert : un solde ne peut jamais devenir négatif ;
- grand livre : chaque variation d'un solde est enregistrée avec le solde obtenu ;
- portefeuille en euros : les liquidités sont en euros alors que les paires sont
  cotées en USDC/USDT. Chaque exécution est convertie au taux du moment, et le
  taux, sa source et son horodatage sont enregistrés avec elle. Sans taux assez
  récent, l'ordre est refusé.
"""

from __future__ import annotations

import math
import time
import uuid
from decimal import Decimal
from functools import wraps
from collections import defaultdict
from typing import Callable

from ..market.fx import FxQuote, identity
from ..market.markets import MarketInfo
from ..storage.db import Database, atomic
from .fx_ledger import PaperFXLedger
from .book_execution import match_snapshot
from .liquidity import LiquidityError
from ..market.fx_book import FXError
from .base import OPEN_STATUSES, FillListener, OrderError
from .contract import limit_fill, limit_hit, market_fill, stop_fill, stop_hit, valid_bar

EPS = 1e-9
def exceeds(amount, balance):
    """Allow binary representation error, never a fixed cash overdraft."""
    return amount > balance and amount - balance > 4 * max(math.ulp(amount), math.ulp(balance))


def logged_submission(submit):
    @wraps(submit)
    def wrapped(self, symbol, *args, **kwargs):
        try:
            return submit(self, symbol, *args, **kwargs)
        except OrderError as exc:
            self.record_refusal(symbol, exc.code, exc.message)
            raise
    return wrapped


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
        *,
        fx_fn: Callable[[str, bool], object] | None = None,
        fx_cost_bps: float = 0.0,
        trade_quote: str | None = None,
        defer_market: bool = False,
        book_fn=None,
        spread_fn: Callable[[str], float | None] | None = None,
    ):
        self.db = db
        self.account_id = account_id
        self.quote = quote  # monnaie du compte : c'est celle des liquidités
        self.trade_quote = trade_quote or quote  # monnaie de cotation des paires suivies (pour valoriser les cryptos)
        self.market_fn = market_fn
        self.price_fn = price_fn
        self.fee_rate = fee_rate
        self.slip = slippage_bps / 10_000.0
        self.clock = clock
        self.fx_fn = fx_fn  # (monnaie, pour_un_ordre) -> taux daté ; None = compte tenu dans la monnaie de cotation
        self.fx_cost = fx_cost_bps / 10_000.0
        self.defer_market = defer_market
        self.book_fn = book_fn
        self.spread_fn = spread_fn  # demi-écart achat/vente observé sur le carnet, en fraction du prix (facultatif)
        self.listeners: list[FillListener] = []

    # -- conversion ---------------------------------------------------------------
    def fx(self, currency: str, for_trading: bool = True) -> FxQuote:
        """Taux pour convertir 1 unité de `currency` dans la monnaie du compte. Refus si aucun taux utilisable."""
        if currency == self.quote:
            return identity(currency, self.clock())
        if self.fx_fn is None:
            raise OrderError(f"Ce compte est tenu en {self.quote} : il ne peut pas traiter une paire cotée en {currency}", "no_fx")
        view = self.fx_fn(currency, for_trading)
        if view.base != self.quote or view.quote != currency:
            raise OrderError("Devises de règlement incohérentes", "no_fx")
        if for_trading:
            view.executable(self.clock())
        return view

    def fx_rate(self, symbol: str) -> float | None:
        """Taux courant pour la paire (1.0 si elle est cotée dans la monnaie du compte). None si inconnu."""
        try:
            return float(self.fx(symbol.split("/")[1]).rate)
        except OrderError:
            return None

    def conversion_cost(self, symbol: str) -> float:
        return self.fx_cost if symbol.split("/")[1] != self.quote else 0.0

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

    def _write(self, asset: str, before: float, after: float, kind: str, ref: dict) -> None:
        self.db.execute(
            "INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?) "
            "ON CONFLICT(account_id, asset) DO UPDATE SET total = excluded.total",
            (self.account_id, asset, after),
        )
        self.db.insert("ledger", {"account_id": self.account_id, "ts": self.clock(), "kind": kind, "asset": asset,
                                  "amount": after - before, "balance_after": after, **ref})

    def _adjust(self, asset: str, delta: float, kind: str = "ajustement", **ref) -> None:
        """Modifie un solde et l'inscrit au grand livre. Un solde ne peut JAMAIS devenir négatif."""
        before = self._total(asset)
        after = before + delta
        if not math.isfinite(after):
            raise OrderError("Solde non fini", "invalid")
        if after < 0:
            if exceeds(-delta, before):
                raise OrderError(f"Opération refusée : le solde {asset} deviendrait négatif ({after:.8g})", "insufficient_funds")
            after = 0.0  # poussière d'arrondi
        self._write(asset, before, after, kind, ref)

    @atomic
    def set_balance(self, asset: str, total: float, kind: str = "ajustement", note: str | None = None) -> None:
        if not math.isfinite(total) or total < 0:
            raise OrderError(f"Un solde ne peut pas être négatif ({asset})", "invalid")
        self._write(asset, self._total(asset), float(total), kind, {"note": note})

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        sql = f"SELECT * FROM orders WHERE account_id = ? AND status IN {OPEN_STATUSES}"
        params: list = [self.account_id]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        return self.db.query(sql + " ORDER BY id", params)

    def reserved(self, exclude_order=None, exclude_group=None) -> dict[str, float]:
        """Montants bloqués par les ordres ouverts (un groupe OCO ne réserve qu'une fois)."""
        res: dict[str, float] = defaultdict(float)
        groups: dict[tuple[str, str], float] = {}
        for o in self.open_orders():
            if o["id"] == exclude_order or (exclude_group and o["oco_group"] == exclude_group):
                continue
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
                if o.get("cash_reserved") is not None:
                    res[self.quote] += o["cash_reserved"] * left / o["amount"]
                else:
                    rate, _ = self.fx(quote).cash_leg(left * px, "buy", self.clock())
                    res[self.quote] += left * px * (1 + self.fee_rate + self.slip) * float(rate) * (1 + self.conversion_cost(o["symbol"]))
        for (_, asset), amt in groups.items():
            res[asset] += amt
        return dict(res)

    def available(self, asset: str) -> float:
        return max(0.0, self._total(asset) - self.reserved().get(asset, 0.0))

    def equity(self) -> tuple[float, float]:
        """(valeur totale, liquidités) dans la monnaie du compte."""
        bal = self.balances()
        cash = bal.get(self.quote, 0.0)
        total = cash
        for asset, qty in bal.items():
            if asset == self.quote or abs(qty) < EPS:
                continue
            if asset in {"EUR", "USD", "USDC", "USDT"}:
                try:
                    rate, legs = self.fx(asset).cash_leg(qty, "sell", self.clock())
                    PaperFXLedger.consume_depth(self.db, legs, reserve=False)
                    total += qty * float(rate) * (1 - self.fee_rate)
                except (FXError, OrderError) as exc:
                    raise OrderError(f"Valorisation impossible pour {asset} : {exc}", "no_fx") from exc
                continue
            symbol = f"{asset}/{self.trade_quote}"
            px = self.price_fn(symbol)
            rate = self.fx_rate(symbol) if px else None
            if not px or not rate or not math.isfinite(px) or px <= 0:
                raise OrderError(f"Valorisation impossible pour {asset} : prix ou change indisponible", "no_price")
            total += qty * px * rate
        return total, cash

    def breakdown(self) -> dict:
        """Liquidités disponibles, réservées par des ordres d'achat en attente, et valeur investie en cryptos."""
        total, cash = self.equity()
        reserved = min(cash, self.reserved().get(self.quote, 0.0))
        return {"equity": total, "cash": cash, "reserved": reserved, "available": max(0.0, cash - reserved),
                "invested": max(0.0, total - cash), "currency": self.quote}

    # -- ordres ---------------------------------------------------------------
    def record_refusal(self, symbol, code, reason):
        self.db.insert("paper_refusals", {"account_id": self.account_id, "ts": self.clock(), "symbol": symbol,
                                          "code": code, "reason": reason})

    @logged_submission
    @atomic
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
        expires_at=None,
    ) -> dict:
        if expires_at is not None and (not math.isfinite(expires_at) or expires_at <= self.clock()):
            raise OrderError("Échéance de l’ordre invalide", "invalid")
        if side not in ("buy", "sell"):
            raise OrderError("Sens d'ordre invalide")
        if type not in ("market", "limit", "stop"):
            raise OrderError("Type d'ordre invalide")
        try:
            m = self.market_fn(symbol)
        except KeyError:
            raise OrderError(f"Paire inconnue : {symbol}") from None
        last = self.price_fn(symbol)
        if not math.isfinite(float(amount)) or float(amount) <= 0 or any(
            x is not None and (not math.isfinite(x) or x <= 0) for x in (price, stop_price)
        ):
            raise OrderError("Quantité ou prix invalide", "invalid")
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
        if base == self.quote or base in {"EUR", "USD", "USDC", "USDT"} and quote in {"EUR", "USD", "USDC", "USDT"}:
            raise OrderError("Utilisez la conversion de devises du portefeuille", "invalid")
        if side == "buy":
            px = price if type == "limit" else stop_price if type == "stop" else self._market_price(symbol, "buy", last)
            rate, _ = self.fx(quote).cash_leg(amount * px * (1 + self.fee_rate), "buy", self.clock())
            rate = float(rate)
            need = amount * px * (1 + self.fee_rate) * rate * (1 + self.conversion_cost(symbol))
            if type != "market":
                need = amount * px * (1 + self.fee_rate + self.slip) * rate * (1 + self.conversion_cost(symbol))
            if exceeds(need, self.available(self.quote)):
                raise OrderError(
                    f"Fonds insuffisants : {need:,.2f} {self.quote} nécessaires, {self.available(self.quote):,.2f} disponibles",
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
            if exceeds(extra, self.available(base)):
                raise OrderError(
                    f"Quantité insuffisante : {amount:g} {base} demandés, {self.available(base):g} disponibles",
                    "insufficient_funds",
                )

        now = self.clock()
        order_id = self.db.insert(
            "orders",
            {
                "account_id": self.account_id,
                "expires_at": expires_at,
                "client_id": f"p{self.account_id}-{uuid.uuid4().hex[:16]}",
                "symbol": symbol,
                "side": side,
                "type": type,
                "amount": amount,
                "cash_reserved": need if side == "buy" else None,
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
        if self.book_fn is not None:
            # A protective order born during a fill first participates on the next market event.
            if purpose not in ("stop", "target"):
                self.process_book(symbol)
            return self.get_order(order_id)
        # Exécution immédiate : ordre au marché ou ordre limite « traversant »
        if type == "market" and not self.defer_market:
            return self._fill(order, self._market_price(symbol, side, last), maker=False)
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

    @atomic
    def cancel_order(self, order_id: int, reason: str | None = None, *, expired=False) -> dict:
        o = self.get_order(order_id)
        if o["status"] not in OPEN_STATUSES:
            return o
        self.db.update(
            "orders",
            order_id,
            {"status": "expired" if expired else "cancelled", "updated_at": self.clock(), "error": reason},
        )
        if o["purpose"] == "entry" and not o["filled"] and o["position_id"]:
            self.db.execute("UPDATE positions SET status='cancelled', reason_close=? WHERE id=? AND status='pending'",
                            (reason, o["position_id"]))
        return self.get_order(order_id)

    def _market_price(self, symbol: str, side: str, last: float) -> float:
        """Prix d'exécution d'un ordre au marché : dernier prix, moins favorable du glissement et du demi-écart observé."""
        half_spread = 0.0
        if self.spread_fn is not None:
            try:
                half_spread = max(0.0, min(0.05, float(self.spread_fn(symbol) or 0.0)))
            except Exception:
                half_spread = 0.0
        return market_fill(last, side, self.slip, half_spread)

    def _reject(self, order: dict, message: str, code: str = "insufficient_funds"):
        self.db.update("orders", order["id"], {"status": "rejected", "error": message, "updated_at": self.clock()})
        raise OrderError(message, code)

    @atomic
    def _fill(self, order: dict, price: float, maker: bool, *, quantity=None, source=None, market_ts=None) -> dict:
        order = self.get_order(order["id"])
        if order["status"] not in OPEN_STATUSES:
            raise OrderError("Exécution déjà traitée", "already_filled")
        if not math.isfinite(price) or price <= 0:
            raise OrderError("Prix d’exécution invalide", "invalid")
        symbol = order["symbol"]
        base, quote = symbol.split("/")
        remaining = Decimal(str(order["amount"])) - Decimal(str(order["filled"]))
        part = remaining if quantity is None else Decimal(str(quantity))
        if not 0 < part <= remaining:
            raise OrderError("Quantité de fill incohérente", "invalid")
        amount = float(part)
        filled_qty = float(Decimal(str(order["filled"])) + part)
        complete = part == remaining
        fee_rate = self.fee_rate  # hypothèse prudente : même taux maker/taker
        now = self.clock()
        try:
            fx = self.fx(quote)
        except OrderError as exc:
            self._reject(order, exc.message, exc.code)
        cross = quote != self.quote
        gross = amount * price  # dans la monnaie de cotation
        fee = gross * fee_rate
        rate, legs = fx.cash_leg(gross + fee if order["side"] == "buy" else gross - fee, order["side"], now)
        rate = float(rate)
        try:
            PaperFXLedger.consume_depth(self.db, legs)
        except FXError as exc:
            raise OrderError(str(exc), "no_fx") from exc
        other = self.reserved(exclude_order=order["id"], exclude_group=order["oco_group"])
        if order["side"] == "buy":
            conv = (gross + fee) * rate * self.fx_cost if cross else 0.0
            cash = -((gross + fee) * rate + conv)
            if exceeds(-cash, self._total(self.quote) - other.get(self.quote, 0)):
                self._reject(order, "Fonds insuffisants à l'exécution")
        else:
            conv = (gross - fee) * rate * self.fx_cost if cross else 0.0
            cash = (gross - fee) * rate - conv
            if exceeds(amount, self._total(base) - other.get(base, 0)):
                self._reject(order, "Quantité insuffisante à l'exécution")
        ref = {"order_id": order["id"], "position_id": order["position_id"], "symbol": symbol, "price": price,
               "fx_rate": rate if cross else None, "fx_source": fx.source if cross else None, "fx_ts": fx.ts if cross else None}
        with self.db.transaction():
            fill_id = self.db.insert(
                "fills",
                {
                    "order_id": order["id"],
                    "account_id": self.account_id,
                    "symbol": symbol,
                    "side": order["side"],
                    "amount": amount,
                    "price": price,
                    "fee": fee,
                    "fee_asset": quote,
                    "price_source": source or "modèle OHLC / dernier prix",
                    "price_ts": now if market_ts is None else market_ts,
                    "ts": now,
                    "fx_rate": rate,
                    "fx_source": fx.source if cross else None,
                    "fx_ts": fx.ts if cross else None,
                    "fx_cost": conv,
                    "cash_amount": cash,
                    "cash_asset": self.quote,
                },
            )
            ref["fill_id"] = fill_id
            if order["side"] == "buy":
                self._adjust(self.quote, -gross * rate, "achat", **ref)
                self._adjust(self.quote, -fee * rate, "frais", **ref)
                if conv:
                    self._adjust(self.quote, -conv, "conversion", **ref)
                self._adjust(base, amount, "achat", **ref)
            else:
                self._adjust(base, -amount, "vente", **ref)
                self._adjust(self.quote, gross * rate, "vente", **ref)
                self._adjust(self.quote, -fee * rate, "frais", **ref)
                if conv:
                    self._adjust(self.quote, -conv, "conversion", **ref)
            self.db.update(
                "orders",
                order["id"],
                {
                    "status": "filled" if complete else "partially_filled",
                    "filled": filled_qty,
                    "avg_price": ((order["avg_price"] or 0) * order["filled"] + price * amount) / filled_qty,
                    "fee": (order["fee"] or 0) + fee,
                    "fee_asset": quote,
                    "updated_at": now,
                },
            )
            if order["oco_group"] and complete:
                for sib in self.open_orders(order["symbol"]):
                    if sib["oco_group"] == order["oco_group"] and sib["id"] != order["id"]:
                        self.cancel_order(sib["id"], "OCO : l'autre ordre a été exécuté")
        filled = self.get_order(order["id"])
        fill = self.db.one("SELECT * FROM fills WHERE id = ?", (fill_id,))
        for fn in list(self.listeners):
            fn(filled, fill)
        return self.get_order(order["id"])

    # -- passage du temps -----------------------------------------------------
    def process_book(self, symbol, snapshot=None, *, max_age=60):
        now = self.clock()
        try:
            book = snapshot if snapshot is not None else self.book_fn(symbol)
            return match_snapshot(self, symbol, book, now, max_age)
        except (LiquidityError, KeyError, TypeError, ValueError, OSError) as exc:
            raise OrderError(f"Carnet inexécutable pour {symbol} : {exc}", "invalid_book") from exc

    def process_bar(self, symbol: str, o: float, h: float, l: float, c: float, start_ms: int, end_ms: int | None = None) -> int:
        """Déclenche les ordres en attente touchés pendant la période. Renvoie le nombre d'exécutions."""
        if self.book_fn is not None:
            if not self.open_orders(symbol):
                return 0
            try:
                return self.process_book(symbol)
            except OrderError as exc:
                self.db.log("warning", "simulation", exc.message, {"symbol": symbol})
                return 0
        for order in self.open_orders(symbol):
            if order["expires_at"] is not None and self.clock() >= order["expires_at"]:
                self.cancel_order(order["id"], "Ordre expiré", expired=True)
        if not valid_bar(o, h, l, c):
            return 0  # données incohérentes : aucune exécution
        orders = [x for x in self.open_orders(symbol) if x["created_at"] * 1000 <= start_ms + 1e-6]
        if not orders:
            return 0
        count = 0
        for od in [x for x in orders if x["type"] == "market"]:
            current = self.get_order(od["id"])
            if current["status"] not in OPEN_STATUSES:
                continue
            px = self._market_price(symbol, current["side"], o)
            if current["side"] == "buy" and current.get("position_id"):
                pos = self.db.one("SELECT * FROM positions WHERE id = ?", (current["position_id"],))
                if pos and pos["stop_price"] and o <= pos["stop_price"]:
                    with self.db.transaction():
                        self.cancel_order(current["id"], "Ouverture sous le stop : entrée impossible")
                        self.db.update("positions", pos["id"], {"status": "cancelled", "reason_close": "Ouverture sous le stop"})
                    continue
            try:
                self._fill(current, px, maker=False)
                count += 1
            except OrderError as exc:
                if exc.code == "insufficient_funds":
                    with self.db.transaction():
                        if self.get_order(current["id"])["status"] in OPEN_STATUSES:
                            self.db.update("orders", current["id"], {"status": "rejected", "error": exc.message, "updated_at": self.clock()})
                            if current.get("position_id") and current["side"] == "buy":
                                self.db.update("positions", current["position_id"], {"status": "cancelled", "reason_close": exc.message})
        # Include protective orders placed at this opening by an entry fill.
        orders = [x for x in self.open_orders(symbol) if x["created_at"] * 1000 <= start_ms + 1e-6]
        stops = [x for x in orders if x["type"] == "stop"]
        limits = [x for x in orders if x["type"] == "limit"]
        for od in stops + limits:  # les stops d'abord (hypothèse pessimiste)
            current = self.get_order(od["id"])
            if current["status"] not in OPEN_STATUSES:
                continue
            px = None
            if od["type"] == "stop":
                if stop_hit(od["stop_price"], h, l, od["side"]):
                    px = stop_fill(od["stop_price"], o, od["side"], self.slip)
            elif od["type"] == "limit":
                if limit_hit(od["price"], h, l, od["side"]):
                    px = limit_fill(od["price"], o, od["side"])
            if px is None:
                continue
            try:
                self._fill(current, px, maker=od["type"] == "limit")
                count += 1
            except OrderError as exc:
                self._record_rejection(current, exc)
        return count

    @atomic
    def _record_rejection(self, order, exc):
        current = self.get_order(order["id"])
        if exc.code == "already_filled":
            return
        if exc.code == "position_tracking":
            self.db.log("error", "simulation", exc.message, {"order_id": order["id"]})
            return
        if current["status"] in OPEN_STATUSES:
            self.db.update("orders", order["id"], {"status": "rejected", "error": exc.message})
            if order["purpose"] == "entry" and not current["filled"] and order["position_id"]:
                self.db.update("positions", order["position_id"], {"status": "cancelled", "reason_close": exc.message})
        self.record_refusal(order["symbol"], exc.code, exc.message)
        self.db.log("warning", "simulation", exc.message, {"order_id": order["id"]})

    # -- réinitialisation -----------------------------------------------------
    def reset(self, initial_balance: float) -> None:
        """Remet le compte à son capital de départ. Le grand livre garde la trace de la remise à zéro."""
        with self.db.transaction():
            for o in self.open_orders():
                self.cancel_order(o["id"], "Compte réinitialisé")
            for asset in list(self.balances()):
                if asset != self.quote:
                    self.set_balance(asset, 0.0, "reinitialisation", "Compte réinitialisé")
            self.set_balance(self.quote, initial_balance, "reinitialisation", "Compte réinitialisé : capital de départ")
            self.db.execute(
                "UPDATE positions SET status = 'cancelled', reason_close = 'Compte réinitialisé' "
                "WHERE account_id = ? AND status IN ('pending', 'open')",
                (self.account_id,),
            )
