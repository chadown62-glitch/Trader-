"""Trading manuel : aperçu et passage d'ordres, positions, calculatrice, avis de l'IA."""

from __future__ import annotations

import time

import pandas as pd

from ..ai.features import REGIME_LABELS, compute_features, warmup_bars
from ..ai.labeling import candidate_row
from ..ai.strategies import STRATEGIES, levels
from ..execution.base import OrderError
from ..execution.paper import PaperBroker
from .common import AccountRuntime, UserError


class TradingMixin:
    """Méthodes de l'application (trading manuel : aperçu et passage d'ordres, positions, calculatrice, avis de l'IA)."""

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
            raise UserError(f"Paire inconnue : {symbol}") from None
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
        if req["side"] == "buy" and req.get("type", "market") == "market" and qty > 0:
            danger = self.shield.check_entry(symbol, value)
            if danger:
                out["warnings"] = [*warnings, f"Bouclier temps réel : {danger}. Prudence."]
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
                raise UserError(exc.message + " (si la crypto est bloquée par le stop d'une position, fermez plutôt la position)") from None
            raise UserError(exc.message) from None
        self._refresh_watch()
        self.db.log("info", "trading", f"Ordre manuel {side} {prev['qty']:g} {symbol} sur « {rt.account['name']} »")
        result["warnings"] = prev["warnings"]
        return result

    def cancel_order(self, account_id: int, order_id: int) -> dict:
        rt = self.runtime(account_id)
        try:
            o = rt.broker.cancel_order(order_id, "Annulé par l'utilisateur")
        except OrderError as exc:
            raise UserError(exc.message) from None
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
            raise UserError(exc.message) from None

    def modify_position(self, position_id: int, stop=None, target=None, clear_target=False) -> dict:
        pos = self.db.one("SELECT * FROM positions WHERE id = ?", (position_id,))
        if pos is None:
            raise UserError("Position introuvable")
        rt = self.runtime(pos["account_id"])
        try:
            return rt.positions.modify(position_id, stop=stop, target=target, clear_target=clear_target)
        except OrderError as exc:
            raise UserError(exc.message) from None

    def size_helper(self, account_id: int, entry: float, stop: float, risk_pct: float | None) -> dict:
        self.runtime(account_id)  # vérifie que le compte existe
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
