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

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

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
        if result.ok:
            self.promote(version)
        else:
            self.db.update("models", version, {"status": "rejected"}, key="version")
        return self.get(version)

    def promote(self, version: str) -> None:
        row = self.get(version)
        if row is None:
            raise ValueError("Modèle introuvable")
        if (row["metrics"] or {}).get("verdict") != "validé":
            raise ValueError("Seul un modèle validé sur des données jamais vues peut devenir le modèle actif")
        with self.db.transaction():
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
