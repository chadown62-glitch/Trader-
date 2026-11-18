"""Registre du hub : UN SEUL portefeuille fictif partagé, réservations atomiques, opérations sans doublon.

- Les euros du hub SONT ceux du portefeuille principal de 30 € fictifs (`MainAccountCash`) : le hub ne crée
  aucun argent. Ses réservations sont aussi comptées par le broker fictif existant, et inversement : personne
  ne peut engager deux fois les mêmes euros (transactions SQLite « BEGIN IMMEDIATE », sérialisées).
- Chaque opération a un identifiant de demande unique : la rejouer renvoie son état, sans rien ré-exécuter.
- Tout changement de solde passe par une transaction unique (réservation, ou règlement complet) : une panne
  au milieu laisse l'état précédent intact, jamais une demi-opération ni un doublon.
- Le journal est chaîné (empreinte de l'entrée précédente) et ne peut être ni modifié ni effacé.
- La réconciliation vérifie soldes, réservations, positions et journal ; une incohérence BLOQUE toute nouvelle
  opération jusqu'à une réconciliation réussie.
- `StandaloneCash` ne sert qu'aux tests et aux outils hors application.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from typing import Callable, Protocol

from ..execution.base import OrderError
from ..storage.db import Database

EPS = 1e-9
STATES = ("RESERVED", "OPEN", "SUBMITTED", "FILLED", "PARTIAL", "NOT_FILLED", "FAILED", "REJECTED", "EXPIRED", "ABANDONED")
TERMINAL = frozenset({"FILLED", "PARTIAL", "NOT_FILLED", "FAILED", "REJECTED", "EXPIRED", "ABANDONED"})
TRANSITIONS = {
    None: {"RESERVED", "REJECTED"},
    "RESERVED": {"SUBMITTED", "OPEN", "FILLED", "PARTIAL", "NOT_FILLED", "FAILED", "REJECTED", "ABANDONED"},
    "OPEN": {"FILLED", "PARTIAL", "EXPIRED", "ABANDONED"},
    "SUBMITTED": {"FILLED", "PARTIAL", "FAILED", "ABANDONED"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dmh_state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dmh_cash(account_id INTEGER PRIMARY KEY, eur REAL NOT NULL CHECK(eur >= 0));
CREATE TABLE IF NOT EXISTS dmh_operations(
    id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
    origin TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('buy','sell')), asset TEXT NOT NULL, state TEXT NOT NULL,
    intent TEXT NOT NULL, route TEXT, verdict TEXT, config_hash TEXT NOT NULL, result TEXT, evidence_mode TEXT NOT NULL,
    expires_ms INTEGER
);
CREATE TABLE IF NOT EXISTS dmh_reservations(
    op_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL, asset TEXT NOT NULL, location TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0), active INTEGER NOT NULL CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS dmh_holdings(
    account_id INTEGER NOT NULL, asset TEXT NOT NULL, location TEXT NOT NULL, qty REAL NOT NULL CHECK(qty >= 0),
    cost_eur REAL NOT NULL CHECK(cost_eur >= 0), final_ms INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account_id, asset, location)
);
CREATE TABLE IF NOT EXISTS dmh_fills(
    id INTEGER PRIMARY KEY AUTOINCREMENT, op_id TEXT NOT NULL, leg INTEGER NOT NULL, ts_ms INTEGER NOT NULL, venue TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy','sell','fee')), asset TEXT NOT NULL, location TEXT NOT NULL,
    qty REAL NOT NULL CHECK(qty >= 0), counter_asset TEXT NOT NULL, counter_location TEXT NOT NULL,
    counter_qty REAL NOT NULL CHECK(counter_qty >= 0), eur REAL NOT NULL, costs TEXT NOT NULL, evidence TEXT NOT NULL,
    mode TEXT NOT NULL, UNIQUE(op_id, leg)
);
CREATE TABLE IF NOT EXISTS dmh_sim_tx(
    tx_id TEXT PRIMARY KEY, op_id TEXT NOT NULL UNIQUE, network TEXT NOT NULL, status TEXT NOT NULL,
    submitted_ms INTEGER NOT NULL, final_ms INTEGER NOT NULL, fee_eur REAL NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dmh_approvals(
    account_id INTEGER NOT NULL, network TEXT NOT NULL, token TEXT NOT NULL, spender TEXT NOT NULL, ts_ms INTEGER NOT NULL,
    PRIMARY KEY(account_id, network, token, spender)
);
CREATE TABLE IF NOT EXISTS dmh_marks(
    account_id INTEGER NOT NULL, asset TEXT NOT NULL, location TEXT NOT NULL, value_eur REAL NOT NULL, ts_ms INTEGER NOT NULL,
    method TEXT NOT NULL, PRIMARY KEY(account_id, asset, location)
);
CREATE TABLE IF NOT EXISTS dmh_previews(id TEXT PRIMARY KEY, created_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dmh_journal(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts_ms INTEGER NOT NULL, op_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL,
    prev TEXT NOT NULL, hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS dmh_journal_no_update BEFORE UPDATE ON dmh_journal BEGIN SELECT RAISE(ABORT,'journal immuable'); END;
CREATE TRIGGER IF NOT EXISTS dmh_journal_no_delete BEFORE DELETE ON dmh_journal BEGIN SELECT RAISE(ABORT,'journal immuable'); END;
CREATE TRIGGER IF NOT EXISTS dmh_fills_no_update BEFORE UPDATE ON dmh_fills BEGIN SELECT RAISE(ABORT,'exécution immuable'); END;
CREATE TRIGGER IF NOT EXISTS dmh_fills_no_delete BEFORE DELETE ON dmh_fills BEGIN SELECT RAISE(ABORT,'exécution immuable'); END;
"""


class LedgerError(OrderError):
    pass


class Blocked(LedgerError):
    def __init__(self, message: str):
        super().__init__(message, "dmh_blocked")


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


class CashBridge(Protocol):
    def total(self) -> float: ...

    def available(self) -> float: ...

    def adjust(self, delta: float, kind: str, note: str) -> None: ...


class StandaloneCash:
    """Euros fictifs du hub HORS application (tests, outils). Dans l'application : `MainAccountCash`."""

    def __init__(self, ledger: "Ledger", initial: float = 30.0):
        self.ledger = ledger
        with ledger.db.transaction():
            if ledger.db.one("SELECT 1 FROM dmh_cash WHERE account_id = ?", (ledger.account_id,)) is None:
                ledger.db.execute("INSERT INTO dmh_cash(account_id, eur) VALUES (?, ?)", (ledger.account_id, float(initial)))
                ledger.journal(None, "depot_fictif", {"eur": float(initial), "note": "capital de départ (argent fictif)"})

    def total(self) -> float:
        return float(self.ledger.db.scalar("SELECT eur FROM dmh_cash WHERE account_id = ?", (self.ledger.account_id,), 0.0))

    def available(self) -> float:
        return max(0.0, self.total() - self.ledger.reserved("EUR", "cash"))

    def adjust(self, delta: float, kind: str, note: str) -> None:
        after = self.total() + delta
        if not math.isfinite(after) or after < -EPS:
            raise LedgerError(f"solde en euros négatif refusé ({after:.6f})", "insufficient_funds")
        self.ledger.db.execute("UPDATE dmh_cash SET eur = ? WHERE account_id = ?", (max(0.0, after), self.ledger.account_id))


class MainAccountCash:
    """Les euros du portefeuille principal (compte fictif de l'application), via son broker fictif.

    Le broker compte déjà les réservations du hub (`external_reserved`), donc `available()` les exclut.
    """

    def __init__(self, broker_fn: Callable[[], object], currency: str = "EUR"):
        self.broker_fn = broker_fn
        self.currency = currency

    def _broker(self):
        from .. import safety
        b = self.broker_fn()
        safety.require_paper_broker(b, "utilisation des euros du portefeuille par le hub")
        return b

    def total(self) -> float:
        return float(self._broker()._total(self.currency))

    def available(self) -> float:
        return float(self._broker().available(self.currency))

    def adjust(self, delta: float, kind: str, note: str) -> None:
        self._broker().adjust_cash(delta, kind, note)


class Ledger:
    def __init__(self, db: Database, account_id: int, *, clock: Callable[[], float] = time.time):
        self.db = db
        self.account_id = int(account_id)
        self.clock = clock
        self.cash: CashBridge | None = None
        for stmt in _statements(SCHEMA):  # instructions idempotentes, sans executescript (qui validerait une transaction en cours)
            db.execute(stmt)

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    # -- état global : arrêt d'urgence, blocage après désynchronisation, réglages -------------------------------
    def state(self, key: str, default=None):
        row = self.db.one("SELECT value FROM dmh_state WHERE key = ?", (key,))
        return default if row is None else json.loads(row["value"])

    def set_state(self, key: str, value) -> None:
        self.db.execute("INSERT INTO dmh_state(key, value, updated_ms) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_ms = excluded.updated_ms",
                        (key, dumps(value), self.now_ms()))

    def check_open(self) -> None:
        """Refus de toute NOUVELLE opération si le hub est arrêté ou désynchronisé (lecture en échec = refus)."""
        try:
            halted = self.state("halted")
            desync = self.state("desync")
        except Exception as exc:  # base illisible : refus
            raise Blocked(f"état du hub illisible : {exc.__class__.__name__}") from exc
        if halted:
            raise Blocked(f"nouvelles opérations fictives arrêtées : {halted.get('reason', 'arrêt manuel')}")
        if desync:
            raise Blocked(f"portefeuille désynchronisé ({desync.get('reason')}) : réconciliation requise")

    def halt(self, reason: str, by: str) -> dict:
        with self.db.transaction():
            v = {"reason": reason[:200], "by": by, "ts_ms": self.now_ms()}
            self.set_state("halted", v)
            self.journal(None, "arret", v)
        return v

    def resume(self, by: str) -> None:
        with self.db.transaction():
            self.set_state("halted", None)
            self.journal(None, "reprise", {"by": by})

    # -- journal chaîné ----------------------------------------------------------------------------------------
    def journal(self, op_id: str | None, kind: str, data: dict) -> str:
        prev = self.db.scalar("SELECT hash FROM dmh_journal ORDER BY seq DESC LIMIT 1", (), "0" * 64)
        ts = self.now_ms()
        body = dumps({"ts_ms": ts, "op_id": op_id, "kind": kind, "data": data, "prev": prev})
        h = hashlib.sha256(body.encode()).hexdigest()
        self.db.execute("INSERT INTO dmh_journal(ts_ms, op_id, kind, data, prev, hash) VALUES (?, ?, ?, ?, ?, ?)",
                        (ts, op_id, kind, dumps(data), prev, h))
        return h

    def journal_ok(self) -> tuple[bool, int]:
        prev = "0" * 64
        rows = self.db.query("SELECT * FROM dmh_journal ORDER BY seq")
        for r in rows:
            body = dumps({"ts_ms": r["ts_ms"], "op_id": r["op_id"], "kind": r["kind"], "data": json.loads(r["data"]), "prev": prev})
            if r["prev"] != prev or hashlib.sha256(body.encode()).hexdigest() != r["hash"]:
                return False, r["seq"]
            prev = r["hash"]
        return True, len(rows)

    # -- réservations -----------------------------------------------------------------------------------------
    def reserved(self, asset: str | None = None, location: str | None = None, exclude_op: str | None = None) -> float:
        sql, params = "SELECT COALESCE(SUM(amount), 0) FROM dmh_reservations WHERE account_id = ? AND active = 1", [self.account_id]
        if exclude_op is not None:
            sql += " AND op_id != ?"
            params.append(exclude_op)
        if asset is not None:
            sql += " AND asset = ?"
            params.append(asset)
        if location is not None:
            sql += " AND location = ?"
            params.append(location)
        return float(self.db.scalar(sql, params, 0.0))

    def reserved_by_asset(self) -> dict[str, float]:
        """Pour le broker fictif existant : euros réservés par le hub (les positions du hub ne sont pas dans ses soldes)."""
        x = self.reserved("EUR", "cash")
        return {"EUR": x} if x > 0 else {}

    def holding(self, asset: str, location: str) -> dict | None:
        return self.db.one("SELECT * FROM dmh_holdings WHERE account_id = ? AND asset = ? AND location = ?",
                           (self.account_id, asset, location))

    def available_holding(self, asset: str, location: str, exclude_op: str | None = None) -> float:
        """Quantité détenue moins les réservations des AUTRES opérations (une opération n'est pas bloquée par la sienne)."""
        h = self.holding(asset, location)
        return max(0.0, (h["qty"] if h else 0.0) - self.reserved(asset, location, exclude_op))

    def operation(self, op_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM dmh_operations WHERE id = ?", (op_id,))
        if row:
            for k in ("intent", "route", "verdict", "result"):
                row[k] = json.loads(row[k]) if row[k] else None
        return row

    def open_operation(self, op_id: str, *, origin: str, side: str, asset: str, intent: dict, route: dict, verdict: dict,
                       config_hash: str, evidence_mode: str, reserve: tuple[str, str, float], expires_ms: int | None = None) -> dict:
        """Crée l'opération ET sa réservation dans une seule transaction ; refus si les fonds ne suffisent pas.

        Si l'identifiant existe déjà, renvoie l'opération existante sans rien refaire (idempotence).
        """
        if self.cash is None:
            raise LedgerError("registre sans source de liquidités", "invalid")
        asset_r, location_r, amount = reserve
        if not (math.isfinite(amount) and amount > 0):
            raise LedgerError("montant à réserver invalide", "invalid")
        with self.db.transaction():
            existing = self.operation(op_id)
            if existing is not None:
                return existing | {"replayed": True}
            self.check_open()
            if asset_r == "EUR" and location_r == "cash":
                free = self.cash.available()
            else:
                free = self.available_holding(asset_r, location_r)
            if amount > free + EPS:
                raise LedgerError(f"fonds fictifs insuffisants : {amount:.6g} {asset_r} demandés, {free:.6g} disponibles "
                                  "(réservations des autres opérations déduites)", "insufficient_funds")
            now = self.now_ms()
            self.db.insert("dmh_operations", {"id": op_id, "account_id": self.account_id, "created_ms": now, "updated_ms": now,
                                              "origin": origin, "side": side, "asset": asset, "state": "RESERVED",
                                              "intent": dumps(intent), "route": dumps(route), "verdict": dumps(verdict),
                                              "config_hash": config_hash, "result": None, "evidence_mode": evidence_mode,
                                              "expires_ms": expires_ms})
            self.db.insert("dmh_reservations", {"op_id": op_id, "account_id": self.account_id, "asset": asset_r,
                                                "location": location_r, "amount": float(amount), "active": 1})
            self.journal(op_id, "reservation", {"asset": asset_r, "location": location_r, "amount": amount, "origin": origin})
        return self.operation(op_id)

    def reject(self, op_id: str, *, origin: str, side: str, asset: str, intent: dict, reasons: list, config_hash: str,
               evidence_mode: str) -> dict:
        """Trace un refus (aucune réservation, aucun mouvement)."""
        with self.db.transaction():
            existing = self.operation(op_id)
            if existing is not None:
                return existing | {"replayed": True}
            now = self.now_ms()
            self.db.insert("dmh_operations", {"id": op_id, "account_id": self.account_id, "created_ms": now, "updated_ms": now,
                                              "origin": origin, "side": side, "asset": asset, "state": "REJECTED",
                                              "intent": dumps(intent), "route": None, "verdict": dumps({"reasons": reasons}),
                                              "config_hash": config_hash, "result": dumps({"reasons": reasons}),
                                              "evidence_mode": evidence_mode, "expires_ms": None})
            self.journal(op_id, "refus", {"reasons": reasons})
        return self.operation(op_id)

    def _transition(self, op: dict, new: str, result: dict | None = None) -> None:
        if new not in TRANSITIONS.get(op["state"], set()):
            raise LedgerError(f"transition interdite {op['state']} → {new}", "invalid")
        self.db.execute("UPDATE dmh_operations SET state = ?, updated_ms = ?, result = COALESCE(?, result) WHERE id = ?",
                        (new, self.now_ms(), dumps(result) if result is not None else None, op["id"]))

    def mark(self, op_id: str, new: str, result: dict | None = None) -> dict:
        with self.db.transaction():
            op = self.operation(op_id)
            if op is None:
                raise LedgerError("opération inconnue", "invalid")
            self._transition(op, new, result)
            if new in TERMINAL:
                self.db.execute("UPDATE dmh_reservations SET active = 0 WHERE op_id = ?", (op_id,))
            self.journal(op_id, "etat", {"from": op["state"], "to": new})
        return self.operation(op_id)

    # -- règlement atomique ----------------------------------------------------------------------------------------
    def settle(self, op_id: str, final_state: str, fills: list[dict], result: dict, *, sim_tx: dict | None = None,
               approvals: list[tuple[str, str, str]] = (), fail_after: str | None = None) -> dict:
        """Applique TOUTES les exécutions d'une opération en une transaction, ou rien.

        Chaque exécution : {leg, venue, side, asset, location, qty, eur, costs, evidence, mode, final_ms?}.
        Achat : euros (ou actif source) débités, actif crédité. Vente : actif débité, euros crédités.
        `fail_after` sert uniquement aux tests de panne (interruption simulée au milieu de l'écriture).
        """
        if final_state not in TERMINAL:
            raise LedgerError("état final attendu", "invalid")
        with self.db.transaction():
            op = self.operation(op_id)
            if op is None:
                raise LedgerError("opération inconnue", "invalid")
            if op["state"] in TERMINAL:
                return op | {"replayed": True}  # déjà réglée : ne rien refaire
            self._transition(op, final_state, result)
            self.db.execute("UPDATE dmh_reservations SET active = 0 WHERE op_id = ?", (op_id,))
            if sim_tx is not None:
                self.db.execute("UPDATE dmh_sim_tx SET status = ? WHERE tx_id = ?", (sim_tx["status"], sim_tx["tx_id"]))
            for i, f in enumerate(fills):
                if fail_after == f"fill:{i}":
                    raise RuntimeError("panne simulée pendant la sauvegarde")
                self._apply_fill(op_id, f)
            for network, token, spender in approvals:
                self.db.execute("INSERT OR IGNORE INTO dmh_approvals VALUES (?, ?, ?, ?, ?)",
                                (self.account_id, network, token, spender, self.now_ms()))
            self.journal(op_id, "reglement", {"state": final_state, "fills": len(fills), "eur": round(sum(f["eur"] for f in fills), 8)})
        return self.operation(op_id)

    def _apply_fill(self, op_id: str, f: dict) -> None:
        """Une exécution a deux côtés : l'actif (acheté, vendu) et sa contrepartie (payée, reçue).

        f = {leg, venue, side: buy|sell|fee, asset, location, qty, counter_asset, counter_location, counter_qty, eur, ...}
        - buy : contrepartie débitée, actif crédité ; sell : actif débité, contrepartie créditée ;
        - fee : contrepartie débitée seule (frais payés sans exécution, ex. gaz d'une transaction échouée).
        """
        qty, cq, eur = float(f["qty"]), float(f["counter_qty"]), float(f["eur"])
        if not all(math.isfinite(x) for x in (qty, cq, eur)) or qty < 0 or cq < 0:
            raise LedgerError("exécution invalide (quantité ou montant)", "invalid")
        side, ca, cl = f["side"], f["counter_asset"], f["counter_location"]
        if side == "buy":
            self._debit(ca, cl, cq, f"paiement {f['asset']} ({op_id})")
            self._credit(f["asset"], f["location"], qty, eur, int(f.get("final_ms", 0)))
        elif side == "sell":
            self._debit(f["asset"], f["location"], qty, f"vente {f['asset']} ({op_id})")
            self._credit(ca, cl, cq, eur, int(f.get("final_ms", 0)))
        elif side == "fee":
            if qty:
                raise LedgerError("des frais ne créditent rien", "invalid")
            self._debit(ca, cl, cq, f"frais sans exécution ({op_id})")
        else:
            raise LedgerError("sens d'exécution inconnu", "invalid")
        self.db.insert("dmh_fills", {"op_id": op_id, "leg": int(f["leg"]), "ts_ms": int(f.get("ts_ms", self.now_ms())),
                                     "venue": f["venue"], "side": side, "asset": f["asset"], "location": f["location"], "qty": qty,
                                     "counter_asset": ca, "counter_location": cl, "counter_qty": cq, "eur": eur,
                                     "costs": dumps(f.get("costs", {})), "evidence": dumps(f.get("evidence", {})),
                                     "mode": f.get("mode", "prospective")})

    def _debit(self, asset: str, location: str, qty: float, note: str) -> None:
        if asset == "EUR" and location == "cash":
            self.cash.adjust(-qty, "hub_achat", note)
            return
        h = self.holding(asset, location)
        have = h["qty"] if h else 0.0
        if qty > have + EPS * max(1.0, have):
            raise LedgerError(f"vente à découvert refusée : {qty:.10g} {asset} demandés, {have:.10g} détenus ({location})",
                              "insufficient_funds")
        left = max(0.0, have - qty)
        cost = (h["cost_eur"] if h else 0.0) * (left / have if have > 0 else 0.0)
        self.db.execute("UPDATE dmh_holdings SET qty = ?, cost_eur = ? WHERE account_id = ? AND asset = ? AND location = ?",
                        (left, max(0.0, cost), self.account_id, asset, location))

    def _credit(self, asset: str, location: str, qty: float, cost_eur: float, final_ms: int) -> None:
        if asset == "EUR" and location == "cash":
            self.cash.adjust(qty, "hub_vente", "produit d'une vente fictive")
            return
        self.db.execute("INSERT INTO dmh_holdings(account_id, asset, location, qty, cost_eur, final_ms) VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(account_id, asset, location) DO UPDATE SET qty = qty + excluded.qty, "
                        "cost_eur = cost_eur + excluded.cost_eur, final_ms = MAX(final_ms, excluded.final_ms)",
                        (self.account_id, asset, location, qty, max(0.0, cost_eur), final_ms))

    # -- transactions simulées sur la chaîne (journal local, jamais diffusé) ---------------------------------------
    def record_sim_tx(self, op_id: str, network: str, final_ms: int, fee_eur: float, data: dict) -> dict:
        tx_id = "sim-" + uuid.uuid4().hex
        with self.db.transaction():
            op = self.operation(op_id)
            existing = self.db.one("SELECT * FROM dmh_sim_tx WHERE op_id = ?", (op_id,))
            if existing:
                return existing
            self._transition(op, "SUBMITTED")
            self.db.insert("dmh_sim_tx", {"tx_id": tx_id, "op_id": op_id, "network": network, "status": "PENDING",
                                          "submitted_ms": self.now_ms(), "final_ms": int(final_ms), "fee_eur": float(fee_eur),
                                          "data": dumps(data)})
            self.journal(op_id, "transaction_simulee", {"tx_id": tx_id, "network": network})
        return self.db.one("SELECT * FROM dmh_sim_tx WHERE tx_id = ?", (tx_id,))

    def sim_tx(self, op_id: str) -> dict | None:
        row = self.db.one("SELECT * FROM dmh_sim_tx WHERE op_id = ?", (op_id,))
        if row:
            row["data"] = json.loads(row["data"])
        return row

    def approved(self, network: str, token: str, spender: str) -> bool:
        return self.db.one("SELECT 1 FROM dmh_approvals WHERE account_id = ? AND network = ? AND token = ? AND spender = ?",
                           (self.account_id, network, token, spender)) is not None

    # -- prévisualisations -------------------------------------------------------------------------------------------
    def save_preview(self, data: dict, ttl_ms: int) -> str:
        pid = "pv-" + uuid.uuid4().hex
        now = self.now_ms()
        with self.db.transaction():
            self.db.execute("DELETE FROM dmh_previews WHERE expires_ms < ?", (now - 3_600_000,))
            self.db.insert("dmh_previews", {"id": pid, "created_ms": now, "expires_ms": now + ttl_ms, "data": dumps(data)})
        return pid

    def preview(self, pid: str) -> dict | None:
        row = self.db.one("SELECT * FROM dmh_previews WHERE id = ?", (pid,))
        if row is None:
            return None
        return json.loads(row["data"]) | {"id": row["id"], "created_ms": row["created_ms"], "expires_ms": row["expires_ms"]}

    # -- lectures ------------------------------------------------------------------------------------------------------
    def holdings(self) -> list[dict]:
        return self.db.query("SELECT * FROM dmh_holdings WHERE account_id = ? AND qty > 0 ORDER BY asset, location", (self.account_id,))

    def operations(self, limit: int = 200) -> list[dict]:
        rows = self.db.query("SELECT id FROM dmh_operations WHERE account_id = ? ORDER BY created_ms DESC LIMIT ?",
                             (self.account_id, int(limit)))
        return [self.operation(r["id"]) for r in rows]

    def fills(self, op_id: str | None = None) -> list[dict]:
        if op_id:
            rows = self.db.query("SELECT * FROM dmh_fills WHERE op_id = ? ORDER BY leg", (op_id,))
        else:
            rows = self.db.query("SELECT f.* FROM dmh_fills f JOIN dmh_operations o ON o.id = f.op_id WHERE o.account_id = ? "
                                 "ORDER BY f.id", (self.account_id,))
        for r in rows:
            r["costs"], r["evidence"] = json.loads(r["costs"]), json.loads(r["evidence"])
        return rows

    def set_mark(self, asset: str, location: str, value_eur: float, method: str) -> None:
        self.db.execute("INSERT INTO dmh_marks VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, asset, location) DO UPDATE SET "
                        "value_eur = excluded.value_eur, ts_ms = excluded.ts_ms, method = excluded.method",
                        (self.account_id, asset, location, float(value_eur), self.now_ms(), method))

    def liquidation_value(self, max_age_ms: int = 86_400_000) -> float:
        """Valeur des positions du hub pour le tableau de bord : dernière valorisation prudente, sinon coût d'achat."""
        total = 0.0
        now = self.now_ms()
        for h in self.holdings():
            m = self.db.one("SELECT * FROM dmh_marks WHERE account_id = ? AND asset = ? AND location = ?",
                            (self.account_id, h["asset"], h["location"]))
            total += m["value_eur"] if m and now - m["ts_ms"] <= max_age_ms else h["cost_eur"]
        return total

    # -- réconciliation ----------------------------------------------------------------------------------------------
    def reconcile(self, *, block_on_error: bool = True) -> dict:
        problems: list[str] = []
        ok, n = self.journal_ok()
        if not ok:
            problems.append(f"journal rompu à l'entrée {n}")
        for r in self.db.query("SELECT r.op_id, o.state FROM dmh_reservations r JOIN dmh_operations o ON o.id = r.op_id "
                               "WHERE r.active = 1 AND r.account_id = ?", (self.account_id,)):
            if r["state"] in TERMINAL:
                problems.append(f"réservation active d'une opération terminée ({r['op_id']})")
        orphans = self.db.scalar("SELECT COUNT(*) FROM dmh_reservations r LEFT JOIN dmh_operations o ON o.id = r.op_id "
                                 "WHERE o.id IS NULL", (), 0)
        if orphans:
            problems.append(f"{orphans} réservation(s) sans opération")
        # positions = somme des deux côtés de toutes les exécutions, par actif et lieu
        flows: dict[tuple[str, str], float] = {}

        def add(key, v):
            flows[key] = flows.get(key, 0.0) + v
        for f in self.fills():
            own, other = (f["asset"], f["location"]), (f["counter_asset"], f["counter_location"])
            if f["side"] == "buy":
                add(own, f["qty"]); add(other, -f["counter_qty"])
            elif f["side"] == "sell":
                add(own, -f["qty"]); add(other, f["counter_qty"])
            else:
                add(other, -f["counter_qty"])
        held = {(h["asset"], h["location"]): h["qty"] for h in self.db.query(
            "SELECT * FROM dmh_holdings WHERE account_id = ?", (self.account_id,))}
        for key in set(flows) | set(held):
            if key == ("EUR", "cash"):
                continue
            exp, got = flows.get(key, 0.0), held.get(key, 0.0)
            if abs(exp - got) > 1e-9 * max(1.0, abs(exp)):
                problems.append(f"position {key[0]}@{key[1]} : registre {got:.12g}, exécutions {exp:.12g}")
        if isinstance(self.cash, StandaloneCash):
            dep = sum(json.loads(r["data"]).get("eur", 0.0) for r in self.db.query("SELECT data FROM dmh_journal WHERE kind = 'depot_fictif'"))
            if abs(dep + flows.get(("EUR", "cash"), 0.0) - self.cash.total()) > 1e-7:
                problems.append(f"euros : registre {self.cash.total():.8f}, dépôt + exécutions {dep + flows.get(('EUR', 'cash'), 0.0):.8f}")
        if self.cash is not None and self.cash.total() < -EPS:
            problems.append("solde en euros négatif")
        res = {"ok": not problems, "problems": problems, "journal_entries": n, "ts_ms": self.now_ms()}
        with self.db.transaction():
            if problems and block_on_error:
                self.set_state("desync", {"reason": problems[0], "count": len(problems)})
                self.journal(None, "desynchronisation", {"problems": problems[:20]})
            elif not problems and self.state("desync"):
                self.set_state("desync", None)
                self.journal(None, "resynchronisation", {"entries": n})
        return res


def _statements(script: str) -> list[str]:
    out, buf = [], []
    for line in script.strip().splitlines():
        buf.append(line)
        if line.rstrip().endswith(";") and (not line.lstrip().startswith("CREATE TRIGGER") or line.rstrip().endswith("END;")):
            out.append("\n".join(buf))
            buf = []
    return out
