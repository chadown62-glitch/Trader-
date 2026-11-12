"""Registre des modèles d'IA (versions, champion, historique des entraînements)."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from ..ai.model import MetaModel
from ..storage.db import Database, dumps, loads

log = logging.getLogger("sentinelle.models")


class ModelStore:
    def __init__(self, db: Database, root: Path, bus=None):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self._cache: dict[str, MetaModel | None] = {}
        self._lock = threading.Lock()

    def champion(self, timeframe: str) -> MetaModel | None:
        with self._lock:
            if timeframe in self._cache:
                return self._cache[timeframe]
        row = self.db.one(
            "SELECT * FROM models WHERE status = 'champion' AND timeframe = ? ORDER BY created_at DESC LIMIT 1", (timeframe,)
        )
        model = None
        if row:
            try:
                model = MetaModel.load(Path(row["path"]))
            except Exception:
                log.exception("Modèle %s illisible", row["version"])
                self.db.log("error", "models", f"Modèle {row['version']} illisible : l'IA passe en observation")
        with self._lock:
            self._cache[timeframe] = model
        return model

    def trial(self, timeframe: str) -> MetaModel | None:
        """Modèle « à l'essai » : prometteur mais pas prouvé. Réservé à l'entraînement en argent fictif."""
        key = "essai:" + timeframe
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        row = self.db.one("SELECT * FROM models WHERE status = 'trial' AND timeframe = ? ORDER BY created_at DESC LIMIT 1", (timeframe,))
        model = None
        if row:
            try:
                model = MetaModel.load(Path(row["path"]))
            except Exception:
                log.exception("Modèle %s illisible", row["version"])
        with self._lock:
            self._cache[key] = model
        return model

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def reserve_evaluation(self, version, timeframe, start_ms, end_ms, reference):
        """A terminal observation can contribute to only one promotion attempt per horizon."""
        with self.db.transaction():
            last = self.db.scalar("SELECT MAX(end_ms) FROM evaluation_windows WHERE timeframe=?", (timeframe,))
            start_ms = max(start_ms, last or start_ms)
            if start_ms >= end_ms:
                return None
            self.db.insert("evaluation_windows", {"version": version, "timeframe": timeframe, "start_ms": start_ms,
                                                 "end_ms": end_ms, "reference": reference})
            return start_ms

    def record_evaluation(self, version, evidence):
        changed = self.db.execute("UPDATE evaluation_windows SET evidence=? WHERE version=? AND evidence IS NULL",
                                  (dumps(evidence), version)).rowcount
        if changed != 1:
            raise ValueError("Fenêtre de validation absente ou déjà utilisée")

    def _approval(self, version):
        row = self.db.one("SELECT * FROM evaluation_windows WHERE version=?", (version,))
        if not row:
            return None
        evidence = loads(row["evidence"], {}) or {}
        return row if evidence.get("verdict") == "MODÈLE APPROUVÉ POUR PAPER TRADING" else None

    def register(self, result, report: dict) -> dict:
        """Enregistre le résultat d'un entraînement ; promeut le modèle s'il est validé."""
        model = result.final_model
        version = model.version
        path = self.root / f"{version}.joblib"
        model.save(path)
        (self.root / f"{version}.json").write_text(json.dumps(report, default=str), encoding="utf-8")
        status = "challenger"
        notes = " ".join(result.reasons)
        self.db.insert(
            "models",
            {"version": version, "created_at": time.time(), "status": status, "timeframe": model.timeframe,
             "metrics": dumps(model.metrics), "path": str(path), "notes": notes},
        )
        # le dernier entraînement fait foi : un ancien modèle à l'essai est retiré dans tous les cas
        self.db.execute("UPDATE models SET status = 'retired' WHERE status = 'trial' AND timeframe = ? AND version != ?", (model.timeframe, version))
        if result.ok and self._approval(version):
            self.promote(version)
        elif result.ok:
            self.db.log("info", "models", f"{version} reste challenger en attente de preuve appariée")
        elif getattr(result, "trial", False):
            self.db.update("models", version, {"status": "trial"}, key="version")
            self.db.log("info", "models", f"Modèle {version} mis à l'essai en argent fictif (prometteur, pas encore prouvé)")
        else:
            self.db.update("models", version, {"status": "rejected"}, key="version")
        self.invalidate()
        return self.get(version)

    def promote(self, version: str) -> None:
        row = self.get(version)
        if row is None:
            raise ValueError("Modèle introuvable")
        if (row["metrics"] or {}).get("verdict") != "validé":
            raise ValueError("Seul un modèle validé sur des données jamais vues peut devenir le modèle actif")
        with self.db.transaction():
            approval = self._approval(version)
            current = self.db.scalar("SELECT version FROM models WHERE timeframe=? AND status='champion'", (row["timeframe"],))
            # A previously approved champion may be explicitly restored after retirement.
            if not approval or (row["status"] != "retired" and approval["reference"] != (current or "all_signals")):
                raise ValueError("Validation appariée indépendante absente ou champion de référence modifié")
            self.db.execute(
                "UPDATE models SET status = 'retired' WHERE status = 'champion' AND timeframe = ?", (row["timeframe"],)
            )
            self.db.update("models", version, {"status": "champion"}, key="version")
        self.invalidate()
        self.db.log("info", "models", f"Nouveau modèle actif : {version}")
        if self.bus:
            self.bus.publish("model", {"version": version, "status": "champion"})

    def retire(self, version: str) -> None:
        self.db.update("models", version, {"status": "retired"}, key="version")
        self.invalidate()

    def get(self, version: str) -> dict | None:
        row = self.db.one("SELECT * FROM models WHERE version = ?", (version,))
        if row:
            row["metrics"] = loads(row["metrics"], {})
        return row

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT * FROM models ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["metrics"] = loads(r["metrics"], {})
            r.pop("path", None)
        return rows

    def report(self, version: str) -> dict | None:
        p = self.root / f"{version}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
