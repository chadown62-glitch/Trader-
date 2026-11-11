"""Gestion des positions (argent fictif uniquement).

Une position = un achat + ses protections (stop-loss et objectif, en OCO).
Le gestionnaire écoute les exécutions du broker et tient à jour quantité,
prix moyen, frais, résultat réalisé et résultat en R (multiple du risque).

Monnaies : les prix (entrée, stop, objectif, sortie) restent ceux de la paire,
dans sa monnaie de cotation. Les montants (frais, résultat, risque) sont tenus
dans la **monnaie du compte**. Quand elles diffèrent (portefeuille en euros,
paire en USDC), chaque exécution porte son taux de change : le résultat tient
donc compte du taux à l'achat ET du taux à la vente.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from ..safety import is_paper_broker, require_paper_broker
from ..storage.db import Database, dumps
from .base import OPEN_STATUSES, OrderError, fee_in_quote
from .contract import entry_possible

log = logging.getLogger("sentinelle.positions")

def _fx(fill: dict) -> float:
    """Taux de change appliqué à une exécution (1.0 quand le compte est tenu dans la monnaie de cotation)."""
    return float(fill.get("fx_rate") or 1.0)


CLOSE_REASONS = {
    "stop": "Stop-loss touché",
    "target": "Objectif atteint",
    "exit": "Sortie",
    "manual": "Vente manuelle",
}


class PositionManager:
    def __init__(self, db: Database, broker, bus=None, clock: Callable[[], float] = time.time):
        self.db = db
        self.broker = broker
        self.bus = bus
        self.clock = clock
        self.account_id = getattr(broker, "account_id", None)
        self.quote = getattr(broker, "quote", None)
        self.close_listeners: list[Callable[[dict], None]] = []
        if is_paper_broker(broker):
            broker.listeners.append(self.on_fill)
        else:
            # Sécurité : lié à un courtier non simulé, ce gestionnaire est INERTE. Il ne s'abonne pas à ce
            # courtier, ignore ses exécutions et refuse chaque action (ouverture, fermeture, stop, objectif).
            log.warning("Gestionnaire de positions créé sur un courtier non simulé (%s) : inerte", type(broker).__name__)
            try:
                db.log("warning", "sécurité", "Simulation uniquement : gestionnaire de positions sur un courtier non simulé, rendu inerte",
                       {"courtier": type(broker).__name__, "compte": self.account_id})
            except Exception:
                pass

    # -- lecture ------------------------------------------------------------
    def get(self, position_id: int) -> dict:
        p = self.db.one("SELECT * FROM positions WHERE id = ?", (position_id,))
        if p is None:
            raise OrderError("Position introuvable", "not_found")
        return p

    def open_positions(self, symbol: str | None = None, origin: str | None = None, agent: str | None = None) -> list[dict]:
        sql = "SELECT * FROM positions WHERE account_id = ? AND status IN ('pending', 'open')"
        params: list = [self.account_id]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if origin:
            sql += " AND origin = ?"
            params.append(origin)
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        return self.db.query(sql + " ORDER BY id", params)

    def protective_orders(self, position_id: int) -> list[dict]:
        return self.db.query(
            f"SELECT * FROM orders WHERE position_id = ? AND status IN {OPEN_STATUSES} AND purpose IN ('stop', 'target')",
            (position_id,),
        )

    # -- ouverture ------------------------------------------------------------
    def open(
        self,
        symbol: str,
        qty: float,
        *,
        origin: str,
        agent: str | None = None,
        stop: float | None = None,
        target: float | None = None,
        entry_type: str = "market",
        limit_price: float | None = None,
        strategy: str | None = None,
        risk_amount: float | None = None,
        p_win: float | None = None,
        expected_r: float | None = None,
        model_version: str | None = None,
        candidate_id: int | None = None,
        horizon_bars: int | None = None,
        timeframe: str | None = None,
        reason: str | None = None,
        data: dict | None = None,
    ) -> dict:
        self._guard("ouverture de position")
        if stop is not None and limit_price is None and entry_type == "market":
            last = self.broker.price_fn(symbol) if hasattr(self.broker, "price_fn") else None
            # contrat d'exécution : entrée abandonnée si le premier prix disponible est déjà au niveau du stop
            if last and not entry_possible(last, stop):
                raise OrderError("Le stop-loss doit être sous le prix actuel", "invalid")
        if target is not None and stop is not None and target <= stop:
            raise OrderError("L'objectif doit être au-dessus du stop-loss", "invalid")
        now = self.clock()
        pos_id = self.db.insert(
            "positions",
            {
                "account_id": self.account_id,
                "symbol": symbol,
                "origin": origin,
                "agent": agent,
                "status": "pending",
                "qty": 0.0,
                "initial_qty": 0.0,
                "stop_price": stop,
                "initial_stop": stop,
                "target_price": target,
                "strategy": strategy,
                "risk_amount": risk_amount,
                "p_win": p_win,
                "expected_r": expected_r,
                "model_version": model_version,
                "candidate_id": candidate_id,
                "horizon_bars": horizon_bars,
                "timeframe": timeframe,
                "reason_open": reason,
                "entry_ts": now,
                "currency": self.quote,
                "data": dumps(data or {}),
            },
        )
        try:
            self.broker.place_order(
                symbol,
                "buy",
                entry_type,
                qty,
                price=limit_price,
                origin=origin,
                agent=agent,
                purpose="entry",
                position_id=pos_id,
                reason=reason,
            )
        except OrderError as exc:
            self.db.update("positions", pos_id, {"status": "cancelled", "reason_close": exc.message})
            raise
        return self.get(pos_id)

    # -- exécutions -------------------------------------------------------------
    def on_fill(self, order: dict, fill: dict) -> None:
        if not is_paper_broker(self.broker):
            return  # sécurité : une exécution venue d'un courtier non simulé est ignorée
        purpose = order.get("purpose")
        pos_id = order.get("position_id")
        try:
            if pos_id and purpose == "entry":
                self._on_entry_fill(pos_id, order, fill)
            elif pos_id and purpose in ("stop", "target", "exit"):
                self._on_exit_fill(pos_id, order, fill)
            elif order["side"] == "sell" and purpose == "manual":
                self._on_manual_sell(order, fill)
        except Exception:
            log.exception("Erreur de suivi de position (ordre %s)", order.get("id"))
            self.db.log("error", "positions", f"Erreur de suivi de position (ordre {order.get('id')})")

    def _on_entry_fill(self, pos_id: int, order: dict, fill: dict) -> None:
        pos = self.get(pos_id)
        base, quote = pos["symbol"].split("/")
        amount = float(fill["amount"])
        fx = _fx(fill)
        fee_q = fee_in_quote(fill["fee"], fill["fee_asset"], base, quote, fill["price"]) * fx + float(fill.get("fx_cost") or 0.0)
        if fill.get("fee_asset") == base:
            amount -= float(fill["fee"])  # frais prélevés sur la crypto achetée
        new_qty = pos["qty"] + amount
        avg = ((pos["entry_price"] or 0) * pos["qty"] + fill["price"] * amount) / new_qty if new_qty > 0 else fill["price"]
        # taux moyen d'entrée, pondéré par les montants achetés : coût en monnaie du compte = prix moyen × quantité × taux moyen
        cost_before = (pos["entry_price"] or 0) * pos["qty"] * (pos.get("entry_fx") or 1.0)
        entry_fx = (cost_before + fill["price"] * amount * fx) / (avg * new_qty) if avg * new_qty > 0 else fx
        upd = {
            "qty": new_qty,
            "initial_qty": new_qty,
            "entry_price": avg,
            "entry_fx": entry_fx,
            "fees": pos["fees"] + fee_q,
            "entry_fees": pos["entry_fees"] + fee_q,
        }
        fully = order["status"] == "filled" or order["filled"] + 1e-12 >= order["amount"]
        if fully:
            upd["status"] = "open"
            upd["entry_ts"] = fill["ts"]
            if pos["stop_price"]:  # risque réellement pris, au prix et au taux d'exécution
                upd["risk_amount"] = max(1e-9, (avg - pos["stop_price"]) * new_qty * entry_fx)
        self.db.update("positions", pos_id, upd)
        if fully:
            self._publish("position_opened", self.get(pos_id))
            self._place_protections(self.get(pos_id))

    def _guard(self, operation: str) -> None:
        """Sécurité : toute action passe par un courtier simulé, vérifié à chaque appel."""
        require_paper_broker(self.broker, f"{operation} sur un courtier non simulé", self.db)

    def _place_protections(self, pos: dict) -> None:
        self._guard("pose d'un stop ou d'un objectif")
        m = self.broker.market_fn(pos["symbol"])
        qty = m.floor_amount(pos["qty"])
        if qty <= 0:
            return
        group = f"pos{pos['id']}"
        if pos["stop_price"]:
            try:
                self.broker.place_order(
                    pos["symbol"], "sell", "stop", qty, stop_price=pos["stop_price"],
                    origin=pos["origin"], agent=pos["agent"], purpose="stop",
                    position_id=pos["id"], oco_group=group, reason="Stop-loss de protection",
                )
            except OrderError as exc:
                if exc.code == "would_trigger":
                    # le prix est déjà passé sous le stop : on sort immédiatement
                    self.close(pos["id"], "Stop dépassé à l'ouverture : sortie immédiate")
                    return
                log.error("Impossible de poser le stop de la position %s : %s", pos["id"], exc.message)
                self.db.log("critical", "positions", f"Stop non posé (position {pos['id']}) : {exc.message}")
                self._publish("alert", {"level": "critical", "message": f"Stop non posé sur {pos['symbol']} : {exc.message}"})
        pos = self.get(pos["id"])
        if pos["status"] != "open":
            return
        if pos["target_price"]:
            try:
                self.broker.place_order(
                    pos["symbol"], "sell", "limit", qty, price=pos["target_price"],
                    origin=pos["origin"], agent=pos["agent"], purpose="target",
                    position_id=pos["id"], oco_group=group, reason="Objectif de gain",
                )
            except OrderError as exc:
                log.warning("Objectif non posé (position %s) : %s", pos["id"], exc.message)

    def _on_exit_fill(self, pos_id: int, order: dict, fill: dict) -> None:
        pos = self.get(pos_id)
        if pos["status"] not in ("open", "pending"):
            return
        base, quote = pos["symbol"].split("/")
        amount = float(fill["amount"])
        fx = _fx(fill)
        entry_fx = pos.get("entry_fx") or 1.0
        fee_q = fee_in_quote(fill["fee"], fill["fee_asset"], base, quote, fill["price"]) * fx + float(fill.get("fx_cost") or 0.0)
        init = pos["initial_qty"] or amount
        entry_fee_share = pos["entry_fees"] * min(1.0, amount / init)
        entry_price = pos["entry_price"] or fill["price"]
        # vente convertie au taux du jour de la vente, achat au taux du jour de l'achat
        pnl = fill["price"] * amount * fx - entry_price * amount * entry_fx - fee_q - entry_fee_share
        qty = max(0.0, pos["qty"] - amount)
        upd = {
            "qty": qty,
            "realized_pnl": pos["realized_pnl"] + pnl,
            "fees": pos["fees"] + fee_q,
            "exit_value": pos["exit_value"] + fill["price"] * amount,
        }
        m = self.broker.market_fn(pos["symbol"])
        dust = qty < max(m.min_amount, m.amount_step) or qty * fill["price"] < 0.5
        if dust:
            sold = init - qty
            upd.update(
                status="closed",
                exit_price=upd["exit_value"] / sold if sold > 0 else fill["price"],
                exit_ts=fill["ts"],
                reason_close=order.get("reason") if order["purpose"] == "exit" and order.get("reason") else CLOSE_REASONS.get(order["purpose"], "Sortie"),
            )
            if pos["risk_amount"]:
                upd["pnl_r"] = upd["realized_pnl"] / pos["risk_amount"]
        self.db.update("positions", pos_id, upd)
        if dust:
            for o in self.protective_orders(pos_id):
                self.broker.cancel_order(o["id"], "Position clôturée")
            closed = self.get(pos_id)
            self._publish("position_closed", closed)
            for fn in list(self.close_listeners):
                try:
                    fn(closed)
                except Exception:
                    log.exception("Erreur d'un abonné à la clôture de position")
        else:
            self._resize_protections(self.get(pos_id))

    def _on_manual_sell(self, order: dict, fill: dict) -> None:
        """Vente manuelle « libre » : réduit les positions manuelles ouvertes (plus anciennes d'abord)."""
        remaining = float(fill["amount"])
        for pos in self.open_positions(order["symbol"], origin="manual"):
            if remaining <= 1e-12:
                break
            take = min(remaining, pos["qty"])
            part = dict(fill, amount=take, fee=fill["fee"] * take / float(fill["amount"]))
            self._on_exit_fill(pos["id"], dict(order, purpose="manual"), part)
            remaining -= take

    def _resize_protections(self, pos: dict) -> None:
        self._guard("ajustement d'un stop ou d'un objectif")
        orders = self.protective_orders(pos["id"])
        if not orders:
            return
        m = self.broker.market_fn(pos["symbol"])
        qty = m.floor_amount(pos["qty"])
        if all(abs((o["amount"] - o["filled"]) - qty) < 1e-12 for o in orders):
            return
        for o in orders:
            self.broker.cancel_order(o["id"], "Ajustement de quantité")
        self._place_protections(pos)

    # -- actions ----------------------------------------------------------------
    def modify(self, position_id: int, stop: float | None = None, target: float | None = None, clear_target: bool = False) -> dict:
        self._guard("modification de position")
        pos = self.get(position_id)
        if pos["status"] != "open":
            raise OrderError("Seule une position ouverte peut être modifiée")
        last = self.broker.price_fn(pos["symbol"])
        new_stop = pos["stop_price"] if stop is None else stop
        new_target = None if clear_target else (pos["target_price"] if target is None else target)
        if new_stop is not None and last and new_stop >= last:
            raise OrderError("Le stop doit rester sous le prix actuel", "would_trigger")
        if new_target is not None and new_stop is not None and new_target <= new_stop:
            raise OrderError("L'objectif doit être au-dessus du stop")
        for o in self.protective_orders(position_id):
            self.broker.cancel_order(o["id"], "Modification des protections")
        upd = {"stop_price": new_stop, "target_price": new_target}
        if pos["initial_stop"] is None and new_stop is not None:
            upd["initial_stop"] = new_stop
            upd["risk_amount"] = max(1e-9, (pos["entry_price"] - new_stop) * pos["qty"] * (pos.get("entry_fx") or 1.0))
        self.db.update("positions", position_id, upd)
        self._place_protections(self.get(position_id))
        p = self.get(position_id)
        self._publish("position_updated", p)
        return p

    def close(self, position_id: int, reason: str = "Clôture manuelle") -> dict:
        self._guard("fermeture de position")
        pos = self.get(position_id)
        if pos["status"] == "pending":
            for o in self.db.query(
                f"SELECT * FROM orders WHERE position_id = ? AND status IN {OPEN_STATUSES}", (position_id,)
            ):
                self.broker.cancel_order(o["id"], reason)
            self.db.update("positions", position_id, {"status": "cancelled", "reason_close": reason})
            return self.get(position_id)
        if pos["status"] != "open":
            return pos
        for o in self.protective_orders(position_id):
            self.broker.cancel_order(o["id"], reason)
        m = self.broker.market_fn(pos["symbol"])
        qty = m.floor_amount(min(pos["qty"], self.broker.available(pos["symbol"].split("/")[0])))
        if qty <= 0 or qty * (self.broker.price_fn(pos["symbol"]) or 0) < m.min_notional:
            # reliquat trop petit pour être vendu : on clôture comptablement
            self.db.update(
                "positions",
                position_id,
                {"status": "closed", "exit_ts": self.clock(), "reason_close": f"{reason} (reliquat non vendable)",
                 "exit_price": self.broker.price_fn(pos["symbol"]),
                 "pnl_r": (pos["realized_pnl"] / pos["risk_amount"]) if pos["risk_amount"] else None},
            )
            closed = self.get(position_id)
            self._publish("position_closed", closed)
            for fn in list(self.close_listeners):
                fn(closed)
            return closed
        self.broker.place_order(
            pos["symbol"], "sell", "market", qty, origin=pos["origin"], agent=pos["agent"],
            purpose="exit", position_id=position_id, reason=reason,
        )
        return self.get(position_id)

    def unrealized(self, pos: dict) -> dict:
        last = self.broker.price_fn(pos["symbol"])
        if not last or not pos["entry_price"] or pos["status"] != "open":
            return {"price": last, "pnl": 0.0, "pnl_pct": 0.0, "r": None, "value": 0.0}
        entry_fx = pos.get("entry_fx") or 1.0
        fx = self.broker.fx_rate(pos["symbol"]) or entry_fx  # taux inconnu : on garde celui de l'entrée
        value = last * pos["qty"] * fx  # valeur actuelle dans la monnaie du compte
        gross = value - pos["entry_price"] * pos["qty"] * entry_fx
        est_exit_cost = value * (getattr(self.broker, "fee_rate", 0.001) + self.broker.conversion_cost(pos["symbol"]))
        pnl = gross - pos["entry_fees"] * (pos["qty"] / (pos["initial_qty"] or pos["qty"])) - est_exit_cost + pos["realized_pnl"]
        return {
            "price": last,
            "pnl": pnl,
            "pnl_pct": ((last * fx) / (pos["entry_price"] * entry_fx) - 1) * 100,
            "r": pnl / pos["risk_amount"] if pos["risk_amount"] else None,
            "value": value,
            "fx": fx,
        }

    def _publish(self, topic: str, payload: dict) -> None:
        if self.bus:
            self.bus.publish(topic, {"account_id": self.account_id, **payload})
