"""Base de données SQLite : un seul fichier, aucune maintenance.

Tout est enregistré : ordres, exécutions, positions, courbe de capital,
candidats de l'IA (y compris ceux qu'elle a refusés), modèles, actualités,
journal et décisions expliquées.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS settings(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS settings_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    data TEXT
);
CREATE TABLE IF NOT EXISTS accounts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('paper', 'live')),
    role TEXT NOT NULL DEFAULT 'manual',
    quote TEXT NOT NULL,
    initial_balance REAL DEFAULT 0,
    created_at REAL,
    archived INTEGER DEFAULT 0,
    data TEXT
);
CREATE TABLE IF NOT EXISTS balances(
    account_id INTEGER NOT NULL,
    asset TEXT NOT NULL,
    total REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, asset)
);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    client_id TEXT UNIQUE,
    exchange_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    type TEXT NOT NULL CHECK (type IN ('market', 'limit', 'stop')),
    amount REAL NOT NULL,
    price REAL,
    stop_price REAL,
    status TEXT NOT NULL,
    filled REAL NOT NULL DEFAULT 0,
    avg_price REAL,
    fee REAL NOT NULL DEFAULT 0,
    fee_asset TEXT,
    origin TEXT NOT NULL DEFAULT 'manual',
    agent TEXT,
    position_id INTEGER,
    oco_group TEXT,
    purpose TEXT,
    created_at REAL,
    updated_at REAL,
    reason TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_account_status ON orders(account_id, status);
CREATE TABLE IF NOT EXISTS fills(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    fee_asset TEXT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_account ON fills(account_id, ts);
CREATE TABLE IF NOT EXISTS positions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    origin TEXT NOT NULL,
    agent TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'open', 'closed', 'cancelled')),
    qty REAL NOT NULL DEFAULT 0,
    initial_qty REAL NOT NULL DEFAULT 0,
    entry_price REAL,
    entry_ts REAL,
    stop_price REAL,
    initial_stop REAL,
    target_price REAL,
    exit_price REAL,
    exit_ts REAL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    pnl_r REAL,
    fees REAL NOT NULL DEFAULT 0,
    entry_fees REAL NOT NULL DEFAULT 0,
    risk_amount REAL,
    strategy TEXT,
    model_version TEXT,
    p_win REAL,
    expected_r REAL,
    candidate_id INTEGER,
    bars_held INTEGER NOT NULL DEFAULT 0,
    horizon_bars INTEGER,
    timeframe TEXT,
    breakeven_done INTEGER NOT NULL DEFAULT 0,
    exit_value REAL NOT NULL DEFAULT 0,
    reason_open TEXT,
    reason_close TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_account_status ON positions(account_id, status);
CREATE TABLE IF NOT EXISTS equity(
    account_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    PRIMARY KEY (account_id, ts)
);
CREATE TABLE IF NOT EXISTS candidates(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    source TEXT NOT NULL,
    entry REAL,
    stop REAL,
    target REAL,
    horizon_bars INTEGER,
    features TEXT,
    p_win REAL,
    expected_r REAL,
    model_version TEXT,
    decision TEXT,
    decision_reason TEXT,
    account_id INTEGER,
    outcome TEXT,
    outcome_r REAL,
    exit_ts INTEGER,
    resolved_at REAL,
    UNIQUE (symbol, timeframe, ts, strategy, source)
);
CREATE INDEX IF NOT EXISTS idx_candidates_unresolved ON candidates(outcome, symbol);
CREATE TABLE IF NOT EXISTS models(
    version TEXT PRIMARY KEY,
    created_at REAL,
    status TEXT,
    timeframe TEXT,
    metrics TEXT,
    path TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS news(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    source TEXT,
    title TEXT,
    url TEXT,
    published_ts REAL,
    summary TEXT,
    fetched_ts REAL,
    analysis TEXT,
    sentiment REAL,
    importance REAL,
    confidence REAL,
    assets TEXT,
    event_type TEXT,
    engine TEXT,
    analyzed_ts REAL,
    cluster INTEGER
);
CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    level TEXT,
    category TEXT,
    message TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE TABLE IF NOT EXISTS decisions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    account_id INTEGER,
    agent TEXT,
    symbol TEXT,
    kind TEXT,
    message TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE TABLE IF NOT EXISTS signals(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    text TEXT,
    parsed INTEGER NOT NULL DEFAULT 0,
    symbol TEXT,
    side TEXT,
    entry REAL,
    stop REAL,
    targets TEXT,
    outcome TEXT,
    outcome_r REAL,
    exit_ts REAL,
    resolved_at REAL,
    UNIQUE (channel, msg_id)
);
CREATE TABLE IF NOT EXISTS knowledge(
    symbol TEXT PRIMARY KEY,
    timeframe TEXT,
    updated_at REAL,
    data TEXT
);
CREATE TABLE IF NOT EXISTS sessions(
    token_hash TEXT PRIMARY KEY,
    created_at REAL,
    expires_at REAL,
    last_seen REAL
);
CREATE TABLE IF NOT EXISTS agents(
    name TEXT PRIMARY KEY,
    account_id INTEGER,
    enabled INTEGER NOT NULL DEFAULT 0,
    started_at REAL,
    data TEXT
);
-- Grand livre : chaque variation d'un solde, avec le solde obtenu (historique complet des opérations)
CREATE TABLE IF NOT EXISTS ledger(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    asset TEXT NOT NULL,
    amount REAL NOT NULL,
    balance_after REAL NOT NULL,
    order_id INTEGER,
    fill_id INTEGER,
    position_id INTEGER,
    symbol TEXT,
    price REAL,
    fx_rate REAL,
    fx_source TEXT,
    fx_ts REAL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger(account_id, id);
-- Vie des modèles : enregistrement, mise à l'épreuve, promotion, rejet, retour en arrière (jamais effacé)
CREATE TABLE IF NOT EXISTS model_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    version TEXT NOT NULL,
    timeframe TEXT,
    event TEXT NOT NULL,
    reason TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_events ON model_events(timeframe, id);
-- Prédictions enregistrées AVANT de connaître le résultat, pour chaque modèle suivi (champion, challenger, précédent)
CREATE TABLE IF NOT EXISTS shadow_predictions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    model_version TEXT NOT NULL,
    p_win REAL,
    expected_r REAL,
    take INTEGER NOT NULL DEFAULT 0,
    created_at REAL,
    UNIQUE (symbol, timeframe, ts, strategy, model_version)
);
CREATE INDEX IF NOT EXISTS idx_shadow_model ON shadow_predictions(model_version, ts);
-- Journal des expériences : chaque entraînement, banc d'essai ou simulation, avec ses paramètres et son résultat
CREATE TABLE IF NOT EXISTS experiments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    config_hash TEXT,
    params TEXT,
    result TEXT
);
CREATE INDEX IF NOT EXISTS idx_experiments ON experiments(kind, ts);
-- Taux de change utilisés (monnaie du portefeuille / monnaie de cotation), avec leur source
CREATE TABLE IF NOT EXISTS fx_rates(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    base TEXT NOT NULL,
    quote TEXT NOT NULL,
    rate REAL NOT NULL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_fx_rates ON fx_rates(quote, ts);
"""


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    """Accès SQLite partagé entre threads (verrou interne)."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(SCHEMA)
            self._ensure_column("news", "cluster", "INTEGER")
            # Portefeuille en euros : taux de change appliqué à chaque exécution et à chaque position
            for column, decl in (("fx_rate", "REAL"), ("fx_source", "TEXT"), ("fx_ts", "REAL"),
                                 ("fx_cost", "REAL NOT NULL DEFAULT 0"), ("cash_amount", "REAL"), ("cash_asset", "TEXT")):
                self._ensure_column("fills", column, decl)
            self._ensure_column("positions", "entry_fx", "REAL")
            self._ensure_column("positions", "currency", "TEXT")
            # registre des modèles : empreinte du fichier (version immuable), fiche d'identité, date de promotion
            for column, decl in (("sha256", "TEXT"), ("params", "TEXT"), ("promoted_at", "REAL")):
                self._ensure_column("models", column, decl)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        """Migration douce : ajoute une colonne apparue dans une version plus récente."""
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # -- accès bas niveau ---------------------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: tuple | list = (), default: Any = None) -> Any:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
            return row[0] if row and row[0] is not None else default

    def insert(self, table: str, data: dict) -> int:
        cols = ", ".join(data.keys())
        marks = ", ".join("?" for _ in data)
        with self._lock:
            cur = self.conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values())
            )
            return int(cur.lastrowid)

    def update(self, table: str, row_id: int, data: dict, key: str = "id") -> None:
        if not data:
            return
        sets = ", ".join(f"{k} = ?" for k in data)
        with self._lock:
            self.conn.execute(
                f"UPDATE {table} SET {sets} WHERE {key} = ?", (*data.values(), row_id)
            )

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Transaction atomique (réentrante)."""
        with self._lock:
            if self._depth == 0:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("COMMIT")

    def backup(self, dest: str | Path) -> None:
        """Copie cohérente de la base (même pendant son utilisation)."""
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self.conn.backup(target)
            finally:
                target.close()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -- journal ------------------------------------------------------------
    def log(self, level: str, category: str, message: str, data: Any = None) -> int:
        return self.insert(
            "logs",
            {
                "ts": time.time(),
                "level": level,
                "category": category,
                "message": message,
                "data": dumps(data) if data is not None else None,
            },
        )

    def decision(
        self,
        account_id: int | None,
        agent: str | None,
        symbol: str | None,
        kind: str,
        message: str,
        data: Any = None,
        ts: float | None = None,
    ) -> int:
        return self.insert(
            "decisions",
            {
                "ts": ts if ts is not None else time.time(),
                "account_id": account_id,
                "agent": agent,
                "symbol": symbol,
                "kind": kind,
                "message": message,
                "data": dumps(data) if data is not None else None,
            },
        )

    # -- méta ---------------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
