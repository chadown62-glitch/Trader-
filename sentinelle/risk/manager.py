"""Gestionnaire de risque : il a le dernier mot sur toutes les entrées de l'IA.

Ses règles sont fixes et bornées (voir config.BOUNDS). Ni l'IA, ni une
stratégie, ni un signal extérieur ne peuvent les contourner.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from ..market.markets import MarketInfo
from ..storage.db import Database, dumps, loads
from .macro import blackout

DAY = 86_400


@dataclass
class RiskDecision:
    approved: bool
    qty: float = 0.0
    risk_amount: float = 0.0  # perte si le stop est touché, dans la monnaie du compte
    reasons: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    notional: float = 0.0  # montant de l'achat, dans la monnaie du compte
    costs: float = 0.0  # frais, glissement et conversion estimés pour l'aller-retour, dans la monnaie du compte
    bumped: bool = False  # vrai si la taille a été portée au minimum de la plateforme

    def refuse(self, name: str, message: str) -> "RiskDecision":
        self.approved = False
        self.reasons.append(message)
        self.checks.append({"name": name, "ok": False, "detail": message})
        return self

    def ok(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": True, "detail": detail})


class RiskManager:
    def __init__(self, db: Database, account_id: int, settings_fn: Callable[[], dict], clock: Callable[[], float] = time.time):
        self.db = db
        self.account_id = account_id
        self.settings_fn = settings_fn
        self.clock = clock

    # -- état du compte ---------------------------------------------------------
    def _account_data(self) -> dict:
        row = self.db.one("SELECT data FROM accounts WHERE id = ?", (self.account_id,))
        return loads(row["data"] if row else None, {}) or {}

    def _save_account_data(self, data: dict) -> None:
        self.db.execute("UPDATE accounts SET data = ? WHERE id = ?", (dumps(data), self.account_id))

    def halted(self) -> tuple[bool, str | None]:
        d = self._account_data()
        return bool(d.get("halted")), d.get("halted_reason")

    def halt(self, reason: str) -> None:
        d = self._account_data()
        d.update(halted=True, halted_reason=reason, halted_at=self.clock())
        self._save_account_data(d)
        self.db.log("critical", "risk", f"Arrêt de sécurité du compte {self.account_id} : {reason}")

    def resume(self) -> None:
        """Reprise manuelle après un arrêt de sécurité : la référence de baisse repart de zéro."""
        d = self._account_data()
        d.update(halted=False, halted_reason=None, peak_reset_ts=self.clock(), pause_until=None)
        self._save_account_data(d)

    def equity_at(self, ts: float, max_age: float = 2 * DAY) -> float | None:
        """Capital relevé juste avant `ts` (ignoré s'il est trop ancien, ex. bot éteint longtemps)."""
        row = self.db.one(
            "SELECT equity FROM equity WHERE account_id = ? AND ts <= ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
            (self.account_id, ts, ts - max_age),
        )
        return row["equity"] if row else None

    def state(self, equity: float) -> dict:
        now = self.clock()
        day_start = now - now % DAY
        d = self._account_data()
        peak_from = d.get("peak_reset_ts") or 0
        start_day = self.db.one(
            "SELECT equity FROM equity WHERE account_id = ? AND ts >= ? ORDER BY ts LIMIT 1",
            (self.account_id, day_start),
        )
        day_eq = self.equity_at(day_start, 1.5 * DAY) or (start_day["equity"] if start_day else None) or equity
        week_eq = self.equity_at(now - 7 * DAY, 2 * DAY)
        if week_eq is None:
            first = self.db.one(
                "SELECT equity FROM equity WHERE account_id = ? AND ts >= ? ORDER BY ts LIMIT 1",
                (self.account_id, now - 7 * DAY),
            )
            week_eq = first["equity"] if first else equity
        peak = self.db.scalar(
            "SELECT MAX(equity) FROM equity WHERE account_id = ? AND ts >= ?", (self.account_id, peak_from), equity
        )
        peak = max(float(peak), equity)
        return {
            "equity": equity,
            "day_start_equity": day_eq,
            "week_start_equity": week_eq,
            "peak_equity": peak,
            "day_pnl_pct": (equity / day_eq - 1) if day_eq else 0.0,
            "week_pnl_pct": (equity / week_eq - 1) if week_eq else 0.0,
            "drawdown_pct": (1 - equity / peak) if peak else 0.0,
            "halted": bool(d.get("halted")),
            "halted_reason": d.get("halted_reason"),
            "pause_until": d.get("pause_until"),
        }

    def consecutive_losses(self, agent: str | None = None) -> tuple[int, float | None]:
        sql = "SELECT realized_pnl, exit_ts FROM positions WHERE account_id = ? AND status = 'closed' AND origin = 'ai'"
        params: list = [self.account_id]
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        rows = self.db.query(sql + " ORDER BY exit_ts DESC LIMIT 50", params)
        n = 0
        for r in rows:
            if r["realized_pnl"] < 0:
                n += 1
            else:
                break
        return n, (rows[0]["exit_ts"] if rows else None)

    # -- décision d'entrée ----------------------------------------------------------
    def evaluate_entry(
        self,
        symbol: str,
        entry: float,
        stop: float,
        target: float | None,
        *,
        equity: float,
        cash: float,
        market: MarketInfo,
        fee_rate: float,
        slip: float,
        agent: str | None = None,
        budget: float | None = None,
        risk_factor: float = 1.0,
        fx: float = 1.0,
        fx_cost: float = 0.0,
    ) -> RiskDecision:
        """`entry`, `stop`, `target` : prix de la paire. `equity`, `cash` : monnaie du compte.

        `fx` convertit la monnaie de cotation dans celle du compte (1.0 si ce sont les mêmes) ;
        `fx_cost` est le coût de conversion par opération (fraction)."""
        cfg = self.settings_fn()["risk"]
        now = self.clock()
        dec = RiskDecision(approved=True)
        st = self.state(equity)

        if st["halted"]:
            return dec.refuse("arret", f"Arrêt de sécurité actif ({st['halted_reason']}). Reprise manuelle nécessaire.")
        if st["drawdown_pct"] >= cfg["max_drawdown"]:
            reason = f"baisse de {st['drawdown_pct']*100:.1f} % depuis le plus haut (limite {cfg['max_drawdown']*100:.0f} %)"
            self.halt(reason)
            return dec.refuse("drawdown", f"Arrêt de sécurité : {reason}.")
        dec.ok("drawdown", f"baisse actuelle {st['drawdown_pct']*100:.1f} %")
        if st["day_pnl_pct"] <= -cfg["daily_loss_limit"]:
            return dec.refuse("perte_jour", f"Limite de perte du jour atteinte ({st['day_pnl_pct']*100:.1f} %). Reprise demain.")
        if st["week_pnl_pct"] <= -cfg["weekly_loss_limit"]:
            return dec.refuse("perte_semaine", f"Limite de perte sur 7 jours atteinte ({st['week_pnl_pct']*100:.1f} %).")
        dec.ok("pertes", "limites journalière et hebdomadaire respectées")

        mb = blackout(self.settings_fn(), now, loads(self.db.get_meta("calendar_events"), []))
        if mb:
            return dec.refuse("macro", f"Annonce macro « {mb[1]} » : pas de nouvelle position autour de l'annonce.")

        n_losses, last_exit = self.consecutive_losses(agent)
        if n_losses >= cfg["max_consecutive_losses"] and last_exit:
            until = last_exit + cfg["pause_hours_after_losses"] * 3600
            if now < until:
                return dec.refuse("serie_pertes", f"{n_losses} pertes d'affilée : pause jusqu'au {time.strftime('%d/%m %H:%M', time.localtime(until))}.")

        open_pos = self.db.query(
            "SELECT symbol, qty, entry_price, COALESCE(entry_fx, 1.0) AS entry_fx FROM positions "
            "WHERE account_id = ? AND status IN ('pending', 'open')",
            (self.account_id,),
        )
        if len(open_pos) >= cfg["max_positions"]:
            return dec.refuse("positions_max", f"Nombre maximal de positions atteint ({cfg['max_positions']}).")
        if any(p["symbol"] == symbol for p in open_pos):
            return dec.refuse("deja_position", f"Une position est déjà ouverte sur {symbol}.")
        cooldown = cfg["cooldown_minutes_after_loss"] * 60
        if cooldown:
            last_loss = self.db.one(
                "SELECT exit_ts FROM positions WHERE account_id = ? AND symbol = ? AND status = 'closed' "
                "AND realized_pnl < 0 ORDER BY exit_ts DESC LIMIT 1",
                (self.account_id, symbol),
            )
            if last_loss and last_loss["exit_ts"] and now - last_loss["exit_ts"] < cooldown:
                return dec.refuse("delai_apres_perte", "Délai de réflexion après une perte sur cette crypto (pas de rachat « pour se refaire »).")

        if not (stop < entry):
            return dec.refuse("stop", "Le stop doit être sous le prix d'entrée.")
        if target is not None and target <= entry:
            return dec.refuse("objectif", "L'objectif doit être au-dessus du prix d'entrée.")
        if not (fx > 0):
            return dec.refuse("change", "Taux de change indisponible : aucun ordre par prudence.")
        if target is None:
            costs = entry * (2 * fee_rate + 2 * slip + 2 * fx_cost)
            ratio = (entry - stop) / costs if costs > 0 else float("inf")
            if ratio < cfg["min_reward_to_fees"]:
                return dec.refuse("frais", f"Mouvement attendu trop faible face aux frais ({ratio:.1f}× les frais).")
            dec.ok("frais", f"risque = {ratio:.1f}× les frais (stop suiveur, pas d'objectif fixe)")
        if target is not None:
            costs = entry * (2 * fee_rate + 2 * slip + 2 * fx_cost)
            ratio = (target - entry) / costs if costs > 0 else float("inf")
            if ratio < cfg["min_reward_to_fees"]:
                return dec.refuse("frais", f"Gain visé trop faible face aux frais ({ratio:.1f}× les frais, minimum {cfg['min_reward_to_fees']:.0f}×).")
            dec.ok("frais", f"gain visé = {ratio:.1f}× les frais")

        base_capital = min(equity, budget) if budget else equity
        unit_risk = (entry - stop) * fx  # perte par unité achetée si le stop est touché, monnaie du compte
        unit_cost = entry * fx  # prix d'une unité, monnaie du compte
        risk_amount = base_capital * cfg["risk_per_trade"] * max(0.0, min(1.0, risk_factor))
        qty = risk_amount / unit_risk
        exposure = sum((p["qty"] or 0) * (p["entry_price"] or 0) * p["entry_fx"] for p in open_pos)
        caps = {
            "par crypto": base_capital * cfg["max_exposure_per_asset"] / unit_cost,
            "exposition totale": max(0.0, base_capital * cfg["max_exposure"] - exposure) / unit_cost,
            "liquidités": max(0.0, cash) / (unit_cost * (1 + fee_rate + slip + fx_cost)),
        }
        limiting = None
        for name, cap in caps.items():
            if cap < qty:
                qty, limiting = cap, name
        qty = market.floor_amount(qty)

        # Faisabilité : l'achat ET le stop doivent respecter les minimums de la plateforme.
        # Le stop est vérifié à son prix de déclenchement, là où le montant est le plus faible.
        if market.validate(qty, entry) or market.validate(qty, stop):
            need = self._minimum_qty(market, entry, stop)
            hard_cap = market.floor_amount(min(caps.values()))
            unit = market.quote
            if need is None or need > hard_cap:
                blocker = min(caps, key=caps.get)  # la règle qui empêche d'atteindre le minimum
                return dec.refuse("taille", (
                    f"Ordre impossible avec ce capital : la plateforme exige au moins {market.min_notional:g} {unit} par ordre "
                    f"(stop compris), et la règle « {blocker} » limite l'achat à {hard_cap * entry:.2f} {unit}."))
            bumped_risk = need * unit_risk
            ceiling = base_capital * cfg.get("min_order_max_risk", 0.01)
            if bumped_risk > ceiling:
                return dec.refuse("taille", (
                    f"Ordre trop petit pour la plateforme (minimum {market.min_notional:g} {unit}, stop compris). "
                    f"Au minimum, le risque serait de {bumped_risk / equity * 100:.2f} % du capital, "
                    f"au-dessus du plafond de {cfg.get('min_order_max_risk', 0.01) * 100:.2f} % : trade refusé."))
            qty, dec.bumped = need, True
        dec.qty = qty
        dec.risk_amount = qty * unit_risk
        dec.notional = qty * unit_cost
        dec.costs = dec.notional * (2 * fee_rate + 2 * slip + 2 * fx_cost)
        detail = f"risque {dec.risk_amount:,.2f} ({dec.risk_amount / equity * 100:.2f} % du capital)"
        if dec.bumped:
            detail += ", taille portée au minimum accepté par la plateforme"
        elif limiting:
            detail += f", taille limitée par la règle « {limiting} »"
        dec.ok("taille", detail)
        return dec

    @staticmethod
    def _minimum_qty(market: MarketInfo, entry: float, stop: float) -> float | None:
        """Plus petite quantité acceptée par la plateforme à l'achat ET pour le stop (marge de 2 %)."""
        if stop <= 0:
            return None
        step = market.amount_step or 1e-8
        need = max(market.min_amount or 0.0, (market.min_notional or 0.0) * 1.02 / min(entry, stop))
        steps = int(need / step)
        qty = market.floor_amount(steps * step)
        while qty + 1e-15 < need:  # arrondi au pas supérieur
            steps += 1
            qty = market.floor_amount(steps * step + step / 2)
        if market.validate(qty, entry) or market.validate(qty, stop):
            return None
        return qty

    # -- conseils pour le trading manuel ---------------------------------------------
    def manual_warnings(self, symbol: str, qty: float, price: float, stop: float | None, equity: float) -> list[str]:
        cfg = self.settings_fn()["risk"]
        out = []
        value = qty * price
        if stop is None:
            out.append("Sans stop-loss, la perte n'est pas limitée.")
        elif stop < price and equity > 0:
            risk_pct = (price - stop) * qty / equity
            if risk_pct > cfg["risk_per_trade"]:
                out.append(f"Ce trade risque {risk_pct*100:.2f} % du capital (conseillé : {cfg['risk_per_trade']*100:.2f} % maximum).")
        if equity > 0 and value / equity > cfg["max_exposure_per_asset"]:
            out.append(f"Position importante : {value/equity*100:.0f} % du capital sur une seule crypto.")
        return out
