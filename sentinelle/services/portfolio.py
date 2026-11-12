"""One active EUR paper wallet, with immutable legacy accounts and opening ledger entries."""
from __future__ import annotations

import math
import time

from ..market.fx import FxService, FxUnavailable, market_fetcher, synthetic_fetcher
from .common import UserError

MAIN_ROLE = "main"
MAIN_NAME = "Portefeuille principal"
LEDGER_LABELS = {
    "depot": "Dépôt (argent fictif)",
    "achat": "Achat",
    "vente": "Vente",
    "frais": "Frais de la plateforme",
    "conversion": "Coût de conversion",
    "reinitialisation": "Réinitialisation",
    "ajustement": "Ajustement",
}


class PortfolioMixin:
    """Méthodes de l'application : portefeuille principal en euros, migration explicite, taux de change, grand livre."""

    # ======================================================================
    # Taux de change
    # ======================================================================
    def _build_fx(self) -> FxService:
        base = self.settings["portfolio"]["currency"]
        if self.settings["market"]["source"] == "synthetic":
            books = market_fetcher(self.source)
            generated = synthetic_fetcher(self.market_clock)
            def simulated(base, quote):
                markets = self.source.load_markets()
                if f"{base}/{quote}" in markets or f"{quote}/{base}" in markets:
                    return books(base, quote)
                return generated(base, quote)
            fetchers = [simulated]
        else:
            fetchers = [market_fetcher(self.source, lambda: self.settings["portfolio"]["fx_max_age_minutes"] * 60)]
        return FxService(base, fetchers, clock=self.market_clock, db=self.db)

    def _historical_fx(self, currency, timestamp):
        from ..market.sources import SyntheticSource
        if isinstance(self.source, SyntheticSource) and self.settings["market"]["source"] == "synthetic":
            if currency == self.fx.base:
                return self.fx.historical(currency, timestamp)
            q = synthetic_fetcher(lambda: timestamp)(self.fx.base, currency)
            if q is None:
                raise FxUnavailable("Devise absente du générateur de démonstration")
            return q
        return self.fx.historical(currency, timestamp)

    def _fx_quote(self, currency: str, for_trading: bool = True):
        return self.fx.quote(currency, for_trading=for_trading)

    def _refresh_fx(self) -> None:
        """Relevé périodique du taux (hors des ordres) : un ordre n'a alors presque jamais à attendre le réseau."""
        quote = self.settings["general"]["quote"]
        if any(rt.account["quote"] != quote for _aid, rt in self.paper_runtimes()):
            self.fx.refresh(quote)

    def _half_spread(self, symbol: str) -> float | None:
        """Demi-écart achat/vente lu sur le carnet d'ordres public (None si indisponible : seul le glissement s'applique)."""
        if self.settings["market"]["source"] == "synthetic":
            return None
        try:
            ms = self.shield.microstructure(symbol, 1.0)
            return ms["spread_bps"] / 2 / 10_000 if ms else None
        except Exception:
            return None

    # ======================================================================
    # Mode et comptes
    # ======================================================================
    def portfolio_mode(self) -> str:
        """« main » : portefeuille principal actif. « legacy » : anciens comptes, migration proposée."""
        return self.db.get_meta("portfolio_mode") or "legacy"

    def _init_portfolio(self) -> None:
        with self.db.transaction():
            active = self.db.query("SELECT * FROM accounts WHERE role IN ('main','principal') AND archived=0")
            if len(active) > 1 or any(a["kind"] != "paper" or a["quote"] != "EUR" for a in active):
                raise UserError("Portefeuille principal incohérent : vérifier les comptes existants")
            if active and active[0]["role"] == "principal":
                self.db.update("accounts", active[0]["id"], {"role": MAIN_ROLE})
            if not active:
                self.db.execute("UPDATE agents SET enabled=0 WHERE enabled!=0")
            self._ensure_main_account()
            self.db.set_meta("portfolio_mode", "main")
            for row in self.db.query("SELECT b.* FROM balances b JOIN accounts a ON a.id=b.account_id "
                                     "WHERE a.kind='paper' AND NOT EXISTS (SELECT 1 FROM ledger l "
                                     "WHERE l.account_id=b.account_id AND l.asset=b.asset)"):
                self.db.insert("ledger", {"account_id": row["account_id"], "ts": time.time(), "kind": "reprise",
                               "asset": row["asset"], "amount": row["total"], "balance_after": row["total"],
                               "note": "Solde existant à l’intégration"})

    def _ensure_main_account(self) -> dict:
        acc = self.main_account()
        if acc is not None:
            return acc
        cfg = self.settings["portfolio"]
        now = self.market_clock()  # même horloge que les relevés de capital et le grand livre
        with self.db.transaction():
            aid = self.db.insert("accounts", {"name": MAIN_NAME, "kind": "paper", "role": MAIN_ROLE, "quote": cfg["currency"],
                                              "initial_balance": 30.0, "created_at": now, "data": "{}"})
            self.db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?)", (aid, cfg["currency"], 30.0))
            self.db.insert("ledger", {"account_id": aid, "ts": now, "kind": "depot", "asset": cfg["currency"], "amount": 30.0,
                                      "balance_after": 30.0, "note": "Capital de départ (argent fictif)"})
        self.db.log("info", "portefeuille", f"Portefeuille principal créé : {30.0:.2f} {cfg['currency']} fictifs")
        return self.main_account()

    def main_account(self) -> dict | None:
        return self.db.one("SELECT * FROM accounts WHERE role = ? AND kind = 'paper' AND archived = 0 ORDER BY id", (MAIN_ROLE,))

    def ai_account(self) -> dict | None:
        return self.main_account()

    def is_tradable(self, acc: dict) -> bool:
        return bool(acc and acc.get("role") == MAIN_ROLE and acc.get("kind") == "paper" and not acc.get("archived"))

    def tradable_accounts(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM accounts WHERE kind = 'paper' AND archived = 0 ORDER BY id")
        return [a for a in rows if self.is_tradable(a)]

    def legacy_paper_accounts(self) -> list[dict]:
        return self.db.query("SELECT * FROM accounts WHERE kind = 'paper' AND archived = 0 AND role != ? ORDER BY id", (MAIN_ROLE,))

    # ======================================================================
    # Migration explicite
    # ======================================================================
    def portfolio_status(self) -> dict:
        mode = self.portfolio_mode()
        cfg = self.settings["portfolio"]
        quote = self.settings["general"]["quote"]
        legacy = []
        for acc in self.legacy_paper_accounts():
            rt = self.runtimes.get(acc["id"])
            equity = None
            if rt is not None:
                try:
                    equity = rt.broker.equity()[0]
                except Exception:
                    equity = None
            if equity is None:
                last = self.db.one("SELECT equity FROM equity WHERE account_id = ? ORDER BY ts DESC LIMIT 1", (acc["id"],))
                equity = last["equity"] if last else acc["initial_balance"]
            legacy.append({
                "id": acc["id"], "name": acc["name"], "role": acc["role"], "quote": acc["quote"], "initial_balance": acc["initial_balance"],
                "equity": equity,
                "open_positions": int(self.db.scalar(
                    "SELECT COUNT(*) FROM positions WHERE account_id = ? AND status IN ('pending', 'open')", (acc["id"],), 0)),
                "trades": int(self.db.scalar("SELECT COUNT(*) FROM positions WHERE account_id = ? AND status = 'closed'", (acc["id"],), 0)),
            })
        main = self.main_account()
        return {
            "mode": mode,
            "currency": cfg["currency"],
            "initial": 30.0,
            "trade_quote": quote,
            "main_account_id": main["id"] if main else None,
            "migration_available": mode == "legacy",
            "legacy_accounts": legacy,
            "fx": self.fx.status([quote]),
            "fx_cost_bps": cfg["fx_cost_bps"],
        }

    def activate_main_portfolio(self, confirm: bool = False) -> dict:
        if confirm is not True:
            raise UserError("Confirmation requise")
        return {"main_account_id": self.main_account()["id"], "positions_closed": 0, "status": self.portfolio_status()}

    # ======================================================================
    # Grand livre
    # ======================================================================
    def ledger(self, account_id: int, limit: int = 200) -> dict:
        acc = self.db.one("SELECT id, quote FROM accounts WHERE id = ?", (account_id,))
        if acc is None:
            raise UserError("Compte introuvable")
        rows = self.db.query("SELECT * FROM ledger WHERE account_id = ? ORDER BY id DESC LIMIT ?", (account_id, max(1, min(int(limit), 2000))))
        for r in rows:
            r["label"] = LEDGER_LABELS.get(r["kind"], r["kind"])
        return {"currency": acc["quote"], "rows": rows, "check": self.ledger_check(account_id)}

    def ledger_check(self, account_id: int) -> dict:
        """Contrôle comptable : pour chaque actif, la somme des écritures doit égaler le solde, et aucun solde n'est négatif."""
        sums, invalid_entries = {}, []
        rows = self.db.query("SELECT id,asset,amount,balance_after FROM ledger WHERE account_id=? ORDER BY id", (account_id,))
        for row in rows:
            asset = row["asset"]
            sums[asset] = sums.get(asset, 0.) + row["amount"]
            if not math.isclose(sums[asset], row["balance_after"], rel_tol=1e-12, abs_tol=1e-9) or row["balance_after"] < 0:
                invalid_entries.append(row["id"])
        balances = {r["asset"]: r["total"] for r in self.db.query("SELECT asset, total FROM balances WHERE account_id = ?", (account_id,))}
        gaps = {}
        for asset in set(sums) | set(balances):
            gap = balances.get(asset, 0.0) - sums.get(asset, 0.0)
            if not math.isclose(sums.get(asset, 0.), balances.get(asset, 0.), rel_tol=1e-12, abs_tol=1e-9):
                gaps[asset] = gap
        negative = [a for a, v in balances.items() if v < 0]
        return {"ok": not gaps and not negative and not invalid_entries, "gaps": gaps, "negative": negative,
                "entries": len(rows), **({"invalid_entries": invalid_entries} if invalid_entries else {})}

    def evaluation_dashboard(self) -> dict:
        """Read only committed simulation observations and registered model comparisons."""
        from ..storage.db import loads
        account = self.main_account()
        aid = account["id"]
        with self.db.transaction():
            rows = self.db.query("SELECT ts, equity, unrealized FROM equity WHERE account_id=? ORDER BY ts", (aid,))
            day_marks = {}
            peak, drawdown = account["initial_balance"], 0.
            for point in rows:
                value = point["equity"]
                peak = max(peak, value)
                drawdown = max(drawdown, 1 - value / peak)
                day_marks[int(point["ts"] // 86400)] = value
            days = []
            previous_day = None
            for day, value in day_marks.items():
                measurable = previous_day is not None and day == previous_day + 1 and day_marks[previous_day] > 0
                days.append({"ts": day * 86400, "equity": value,
                             "net_return": value / day_marks[previous_day] - 1 if measurable else None})
                previous_day = day
            trades = self.db.query("SELECT id,symbol,exit_ts,realized_pnl,initial_qty,entry_price,entry_fx,entry_fees "
                                   "FROM positions WHERE account_id=? AND status='closed' ORDER BY exit_ts DESC LIMIT 30", (aid,))
            for trade in trades:
                rate = trade["entry_fx"]
                if rate is None and trade["symbol"].split("/")[1] == account["quote"]:
                    rate = 1.
                basis = ((trade["initial_qty"] or 0)*(trade["entry_price"] or 0)*rate + trade["entry_fees"]) if rate else 0.
                trade["net_return"] = trade["realized_pnl"]/basis if basis > 0 else None
            fees = -self.db.scalar("SELECT SUM(amount) FROM ledger WHERE account_id=? AND asset='EUR' "
                                   "AND kind IN ('frais','conversion')", (aid,), 0)
            comparisons = []
            for row in self.db.query("SELECT version,reference,start_ms,end_ms,evidence FROM evaluation_windows ORDER BY end_ms DESC LIMIT 10"):
                evidence = loads(row.pop("evidence"), {}) or {}
                comparisons.append({**row, "verdict": evidence.get("verdict","TESTS INCOMPLETS"),
                                    "interval": evidence.get("paired_daily_interval"), "days": evidence.get("days")})
            return {"currency": account["quote"], "as_of": rows[-1]["ts"] if rows else None,
                    "equity": rows[-1]["equity"] if rows else None, "unrealized": rows[-1]["unrealized"] if rows else None,
                    "realized": self.db.scalar("SELECT SUM(realized_pnl) FROM positions WHERE account_id=?", (aid,), 0),
                    "fees_eur": fees, "max_drawdown": drawdown if rows else None, "days": days[-30:], "trades": trades,
                    "rejected": self.db.scalar("SELECT COUNT(*) FROM paper_refusals WHERE account_id=?", (aid,), 0),
                    "models": [{"version":r["version"],"status":r["status"]} for r in self.models.list(10)],
                    "comparisons": comparisons, "verdict": comparisons[0]["verdict"] if comparisons else "NON TESTÉ"}

    def fx_or_error(self, symbol: str) -> dict:
        """Taux applicable à un ordre sur cette paire, pour un compte en euros (détail affiché avant l'ordre)."""
        quote = symbol.split("/")[1]
        try:
            return self.fx.quote(quote).to_dict(self.market_clock())
        except FxUnavailable as exc:
            raise UserError(exc.message) from None
