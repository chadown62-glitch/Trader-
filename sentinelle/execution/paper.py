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

import time
import uuid
from collections import defaultdict
from typing import Callable

from ..market.fx import FxQuote, identity
from ..market.markets import MarketInfo
from ..storage.db import Database
from .base import OPEN_STATUSES, FillListener, OrderError

EPS = 1e-9
CASH_DUST = 1e-6  # tolérance d'arrondi sur les liquidités (un millionième d'unité monétaire)
ASSET_DUST = 1e-12  # tolérance d'arrondi sur une quantité de crypto


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
        self.spread_fn = spread_fn  # demi-écart achat/vente observé sur le carnet, en fraction du prix (facultatif)
        self.listeners: list[FillListener] = []

    # -- conversion ---------------------------------------------------------------
    def fx(self, currency: str, for_trading: bool = True) -> FxQuote:
        """Taux pour convertir 1 unité de `currency` dans la monnaie du compte. Refus si aucun taux utilisable."""
        if currency == self.quote:
            return identity(currency, self.clock())
        if self.fx_fn is None:
            raise OrderError(f"Ce compte est tenu en {self.quote} : il ne peut pas traiter une paire cotée en {currency}", "no_fx")
        return self.fx_fn(currency, for_trading)

    def fx_rate(self, symbol: str) -> float | None:
        """Taux courant pour la paire (1.0 si elle est cotée dans la monnaie du compte). None si inconnu."""
        try:
            return float(self.fx(symbol.split("/")[1], False).rate)
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
        if after < 0:
            if after < -(CASH_DUST if asset == self.quote else ASSET_DUST):
                raise OrderError(f"Opération refusée : le solde {asset} deviendrait négatif ({after:.8g})", "insufficient_funds")
            after = 0.0  # poussière d'arrondi
        self._write(asset, before, after, kind, ref)

    def set_balance(self, asset: str, total: float, kind: str = "ajustement", note: str | None = None) -> None:
        if total < 0:
            raise OrderError(f"Un solde ne peut pas être négatif ({asset})", "invalid")
        self._write(asset, self._total(asset), float(total), kind, {"note": note})

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
                rate = self.fx_rate(o["symbol"]) or 0.0
                res[self.quote] += left * px * (1 + self.fee_rate + self.slip) * rate * (1 + self.conversion_cost(o["symbol"]))
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
            symbol = f"{asset}/{self.trade_quote}"
            px = self.price_fn(symbol)
            rate = self.fx_rate(symbol) if px else None
            if px and rate:
                total += qty * px * rate
        return total, cash

    def breakdown(self) -> dict:
        """Liquidités disponibles, réservées par des ordres d'achat en attente, et valeur investie en cryptos."""
        total, cash = self.equity()
        reserved = min(cash, self.reserved().get(self.quote, 0.0))
        return {"equity": total, "cash": cash, "reserved": reserved, "available": max(0.0, cash - reserved),
                "invested": max(0.0, total - cash), "currency": self.quote}

    # -- ordres ---------------------------------------------------------------
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
        if side not in ("buy", "sell"):
            raise OrderError("Sens d'ordre invalide")
        if type not in ("market", "limit", "stop"):
            raise OrderError("Type d'ordre invalide")
        try:
            m = self.market_fn(symbol)
        except KeyError:
            raise OrderError(f"Paire inconnue : {symbol}") from None
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
            px = price if type == "limit" else stop_price if type == "stop" else self._market_price(symbol, "buy", last)
            rate = self.fx(quote).rate  # refus si le taux de change manque ou est trop ancien
            need = amount * px * (1 + self.fee_rate) * rate * (1 + self.conversion_cost(symbol))
            if need > self.available(self.quote) + 1e-6:
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

    def cancel_order(self, order_id: int, reason: str | None = None) -> dict:
        o = self.get_order(order_id)
        if o["status"] not in OPEN_STATUSES:
            return o
        self.db.update(
            "orders",
            order_id,
            {"status": "cancelled", "updated_at": self.clock(), "error": reason},
        )
        return self.get_order(order_id)

    def _market_price(self, symbol: str, side: str, last: float) -> float:
        """Prix d'exécution d'un ordre au marché : dernier prix, moins favorable du glissement et du demi-écart observé."""
        half_spread = 0.0
        if self.spread_fn is not None:
            try:
                half_spread = max(0.0, min(0.05, float(self.spread_fn(symbol) or 0.0)))
            except Exception:
                half_spread = 0.0
        worse = self.slip + half_spread
        return last * (1 + worse) if side == "buy" else last * (1 - worse)

    def _reject(self, order: dict, message: str, code: str = "insufficient_funds"):
        self.db.update("orders", order["id"], {"status": "rejected", "error": message, "updated_at": self.clock()})
        raise OrderError(message, code)

    def _fill(self, order: dict, price: float, maker: bool) -> dict:
        symbol = order["symbol"]
        base, quote = symbol.split("/")
        amount = order["amount"] - order["filled"]
        fee_rate = self.fee_rate  # hypothèse prudente : même taux maker/taker
        now = self.clock()
        try:
            fx = self.fx(quote)
        except OrderError as exc:
            self._reject(order, exc.message, exc.code)
        rate = float(fx.rate)
        cross = quote != self.quote
        gross = amount * price  # dans la monnaie de cotation
        fee = gross * fee_rate
        if order["side"] == "buy":
            conv = (gross + fee) * rate * self.fx_cost if cross else 0.0
            cash = -((gross + fee) * rate + conv)
            if self._total(self.quote) + CASH_DUST < -cash:
                self._reject(order, "Fonds insuffisants à l'exécution")
        else:
            conv = (gross - fee) * rate * self.fx_cost if cross else 0.0
            cash = (gross - fee) * rate - conv
            if self._total(base) + ASSET_DUST < amount:
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
        filled = self.get_order(order["id"])
        fill = self.db.one("SELECT * FROM fills WHERE id = ?", (fill_id,))
        for fn in list(self.listeners):
            fn(filled, fill)
        return self.get_order(order["id"])

    # -- passage du temps -----------------------------------------------------
    def process_bar(self, symbol: str, o: float, h: float, l: float, c: float, start_ms: int, end_ms: int | None = None) -> int:
        """Déclenche les ordres en attente touchés pendant la période. Renvoie le nombre d'exécutions."""
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
            except OrderError:
                continue
        return count

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
