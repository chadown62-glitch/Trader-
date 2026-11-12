"""Portefeuille principal : une seule poche d'argent fictif, en euros, partagée entre le trading manuel et l'IA.

Deux situations :
- **nouvelle installation** : le portefeuille principal (30 € par défaut) est créé d'emblée ;
- **installation existante** (anciens comptes papier de 10 000 USDC) : rien n'est remplacé en
  silence. Sentinelle continue avec les anciens comptes tant que vous n'avez pas confirmé le
  passage au portefeuille principal (`activate_main_portfolio`). Après ce passage, les anciens
  comptes restent consultables (historique, courbes), mais on n'y trade plus.

Les backtests et les entraînements sur l'historique utilisent leur propre base de données en
mémoire (`backtest/engine.py`) : ils ne peuvent pas toucher à ce portefeuille.
"""

from __future__ import annotations

import time

from ..execution.base import OrderError
from ..market.fx import FxService, FxUnavailable, ecb_fetcher, market_fetcher, synthetic_fetcher
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
            fetchers = [synthetic_fetcher(self.market_clock)]
        else:
            fetchers = [market_fetcher(self.source, lambda: self.settings["portfolio"]["fx_max_age_minutes"] * 60), ecb_fetcher()]
        return FxService(base, fetchers, clock=self.market_clock, db=self.db)

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
        """Au premier démarrage de cette version : décide du mode sans rien remplacer."""
        if self.db.get_meta("portfolio_mode") is None:
            existing = self.db.scalar("SELECT COUNT(*) FROM accounts", (), 0)
            self.db.set_meta("portfolio_mode", "legacy" if existing else "main")
            if existing:
                self.db.log("info", "portefeuille", "Anciens comptes papier conservés. Le portefeuille principal de 30 € est proposé "
                                                    "dans le panel : rien n'est remplacé sans votre confirmation.")
        if self.portfolio_mode() == "main":
            self._ensure_main_account()
        else:
            self._ensure_legacy_accounts()

    def _ensure_main_account(self) -> dict:
        acc = self.main_account()
        if acc is not None:
            return acc
        cfg = self.settings["portfolio"]
        now = self.market_clock()  # même horloge que les relevés de capital et le grand livre
        with self.db.transaction():
            aid = self.db.insert("accounts", {"name": MAIN_NAME, "kind": "paper", "role": MAIN_ROLE, "quote": cfg["currency"],
                                              "initial_balance": cfg["initial"], "created_at": now, "data": "{}"})
            self.db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?)", (aid, cfg["currency"], cfg["initial"]))
            self.db.insert("ledger", {"account_id": aid, "ts": now, "kind": "depot", "asset": cfg["currency"], "amount": cfg["initial"],
                                      "balance_after": cfg["initial"], "note": "Capital de départ (argent fictif)"})
        self.db.log("info", "portefeuille", f"Portefeuille principal créé : {cfg['initial']:.2f} {cfg['currency']} fictifs")
        return self.main_account()

    def _ensure_legacy_accounts(self) -> None:
        quote = self.settings["general"]["quote"]
        initial = self.settings["paper"]["initial_balance"]
        for role, name in (("ai_training", "Entraînement IA"), ("manual", "Mon compte papier")):
            if not self.db.one("SELECT id FROM accounts WHERE role = ? AND kind = 'paper' AND archived = 0", (role,)):
                acc = self.db.insert("accounts", {"name": name, "kind": "paper", "role": role, "quote": quote,
                                                  "initial_balance": initial, "created_at": time.time(), "data": "{}"})
                self.db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, ?, ?)", (acc, quote, initial))

    def main_account(self) -> dict | None:
        return self.db.one("SELECT * FROM accounts WHERE role = ? AND kind = 'paper' AND archived = 0 ORDER BY id", (MAIN_ROLE,))

    def ai_account(self) -> dict | None:
        """Compte utilisé par l'IA : le portefeuille principal, ou l'ancien compte d'entraînement avant la migration."""
        if self.portfolio_mode() == "main":
            return self.main_account()
        return self.db.one("SELECT * FROM accounts WHERE role = 'ai_training' AND kind = 'paper' AND archived = 0 ORDER BY id")

    def is_tradable(self, acc: dict) -> bool:
        """Dans le mode « portefeuille principal », on ne trade que sur lui : les anciens comptes sont en lecture seule."""
        if acc.get("kind") != "paper" or acc.get("archived"):
            return False
        return acc.get("role") == MAIN_ROLE if self.portfolio_mode() == "main" else acc.get("role") != MAIN_ROLE

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
            "initial": cfg["initial"],
            "trade_quote": quote,
            "main_account_id": main["id"] if main else None,
            "migration_available": mode == "legacy",
            "legacy_accounts": legacy,
            "fx": self.fx.status([quote]),
            "fx_cost_bps": cfg["fx_cost_bps"],
        }

    def activate_main_portfolio(self, confirm: bool = False) -> dict:
        """Passage explicite au portefeuille principal. Les anciens comptes sont conservés, en lecture seule."""
        if self.portfolio_mode() == "main":
            raise UserError("Le portefeuille principal est déjà actif")
        if confirm is not True:
            raise UserError("Confirmation nécessaire pour passer au portefeuille principal")
        for name in list(self.agents):
            self.stop_agent(name)
        closed, errors = 0, []
        for acc in self.legacy_paper_accounts():
            rt = self.runtimes.get(acc["id"])
            if rt is None:
                continue
            for pos in rt.positions.open_positions():
                try:
                    rt.positions.close(pos["id"], "Passage au portefeuille principal")
                    closed += 1
                except OrderError as exc:
                    errors.append(f"{pos['symbol']} : {exc.message}")
            for order in rt.broker.open_orders():
                try:
                    rt.broker.cancel_order(order["id"], "Passage au portefeuille principal")
                except OrderError:
                    pass
        if errors:
            raise UserError("Passage annulé : certaines positions des anciens comptes n'ont pas pu être fermées (" + " ; ".join(errors) + ")")
        self._snapshot_equity()  # dernière valeur des anciens comptes, pour l'historique
        self.db.set_meta("portfolio_mode", "main")
        main = self._ensure_main_account()
        for acc in self.legacy_paper_accounts():  # ils ne tournent plus : plus aucun ordre ni relevé
            self.runtimes.pop(acc["id"], None)
        if self.hub.markets:
            self._make_paper_runtime(main)
        self._refresh_watch()
        self.db.log("info", "portefeuille", f"Passage au portefeuille principal confirmé : {closed} position(s) des anciens comptes fermée(s), "
                                            "anciens comptes conservés en lecture seule")
        self.bus.publish("settings", {})
        return {"main_account_id": main["id"], "positions_closed": closed, "status": self.portfolio_status()}

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
        sums = {r["asset"]: r["total"] for r in self.db.query(
            "SELECT asset, SUM(amount) AS total FROM ledger WHERE account_id = ? GROUP BY asset", (account_id,))}
        balances = {r["asset"]: r["total"] for r in self.db.query("SELECT asset, total FROM balances WHERE account_id = ?", (account_id,))}
        gaps = {}
        for asset in set(sums) | set(balances):
            gap = balances.get(asset, 0.0) - sums.get(asset, 0.0)
            if abs(gap) > 1e-6 * max(1.0, abs(balances.get(asset, 0.0))):
                gaps[asset] = gap
        negative = [a for a, v in balances.items() if v < 0]
        return {"ok": not gaps and not negative, "gaps": gaps, "negative": negative, "entries": int(self.db.scalar(
            "SELECT COUNT(*) FROM ledger WHERE account_id = ?", (account_id,), 0))}

    def fx_or_error(self, symbol: str) -> dict:
        """Taux applicable à un ordre sur cette paire, pour un compte en euros (détail affiché avant l'ordre)."""
        quote = symbol.split("/")[1]
        try:
            return self.fx.quote(quote).to_dict(self.market_clock())
        except FxUnavailable as exc:
            raise UserError(exc.message) from None
