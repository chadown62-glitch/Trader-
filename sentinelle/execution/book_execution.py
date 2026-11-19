"""Match paper orders against dated L2 observations inside the wallet transaction."""
from decimal import Decimal
import json

from .base import OPEN_STATUSES
from .liquidity import BookSnapshot, LiquidityError, simulate_taker


def read_public_book(source, symbol):
    raw = source.fetch_order_book(symbol, limit=20)
    stamp = raw.get("timestamp")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or stamp <= 0:
        raise LiquidityError("Horodatage du carnet indisponible")
    return BookSnapshot(raw["bids"], raw["asks"], stamp / 1000, source.name)


def match_snapshot(broker, symbol, book, now, max_age):
    """One immutable observation per source/symbol/time; event, depth and fills commit together."""
    if not isinstance(book, BookSnapshot) or not 0 <= now - book.timestamp <= max_age:
        raise LiquidityError("Carnet périmé, futur ou invalide")
    db = broker.db
    key = (symbol, book.source)
    original = json.dumps([[(str(p), str(q)) for p, q in levels] for levels in (book.bids, book.asks)])
    with db.transaction():
        row = db.one("SELECT * FROM paper_books WHERE symbol=? AND source=?", key)
        if row and book.timestamp < row["ts"]:
            raise LiquidityError("Carnet antérieur au dernier relevé")
        if row and book.timestamp == row["ts"]:
            if original != row["original"]:
                raise LiquidityError("Carnet modifié au même horodatage")
            used = json.loads(row["consumed"])
        else:
            used = {"buy": {}, "sell": {}}
            db.execute("INSERT OR REPLACE INTO paper_books(symbol, source, ts, original, consumed) VALUES(?,?,?,?,?)",
                       (*key, book.timestamp, original, json.dumps(used)))
        count = 0
        for order in sorted(broker.open_orders(symbol), key=lambda o: (o["type"] != "stop", o["id"])):
            order = broker.get_order(order["id"])
            if order["status"] not in OPEN_STATUSES:
                continue
            if order["expires_at"] is not None and now >= order["expires_at"]:
                broker.cancel_order(order["id"], "Ordre expiré", expired=True)
                continue
            event = (order["id"], book.source, book.timestamp)
            if db.one("SELECT 1 FROM paper_book_events WHERE order_id=? AND source=? AND ts=?", event):
                continue
            side = order["side"]
            if order["type"] == "stop" and not order["triggered"]:
                hit = book.bids[0][0] <= Decimal(str(order["stop_price"])) if side == "sell" else book.asks[0][0] >= Decimal(str(order["stop_price"]))
                if not hit:
                    continue
                db.update("orders", order["id"], {"triggered": 1})
            db.execute("INSERT INTO paper_book_events VALUES(?,?,?)", event)
            levels = book.asks if side == "buy" else book.bids
            available = [(p, q - Decimal(used[side].get(str(p), "0"))) for p, q in levels]
            available = tuple((p, q) for p, q in available if q > 0)
            if not available:
                continue
            view = BookSnapshot(book.bids if side == "buy" else available, available if side == "buy" else book.asks,
                                book.timestamp, book.source)
            base, quote = symbol.split("/")
            other = broker.reserved(exclude_order=order["id"], exclude_group=order["oco_group"])
            cash = max(0.0, broker._total(broker.quote) - other.get(broker.quote, 0))
            rate, _ = broker.fx(quote).cash_leg(1, side, now)
            budget = Decimal(str(cash)) / (rate * Decimal(str(1 + broker.conversion_cost(symbol))))
            remaining = Decimal(str(order["amount"])) - Decimal(str(order["filled"]))
            result = simulate_taker(view, side, remaining, now=now, max_age=max_age, fee_rate=broker.fee_rate,
                                    amount_step=broker.market_fn(symbol).amount_step, limit_price=order["price"],
                                    available_quote=budget, available_base=max(0, broker._total(base) - other.get(base, 0)))
            for level in result["fills"]:
                px, qty = level["price"], level["quantity"]
                used[side][str(px)] = str(Decimal(used[side].get(str(px), "0")) + qty)
                broker._fill(order, float(px), maker=False, quantity=qty, source=book.source, market_ts=book.timestamp)
                count += 1
            current = broker.get_order(order["id"])
            if side == "buy" and current["status"] in OPEN_STATUSES:
                if broker.reserved().get(broker.quote, 0) > broker._total(broker.quote) + 1e-8:
                    broker.cancel_order(order["id"], "Solde insuffisant pour le reliquat après glissement")
        db.execute("UPDATE paper_books SET consumed=? WHERE symbol=? AND source=?", (json.dumps(used), *key))
        return count
