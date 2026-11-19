"""Registre des modèles d'IA (versions, champion, historique des entraînements)."""

from __future__ import annotations

import json
import hashlib
import io
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from ..ai.model import MetaModel
from ..storage.db import Database, atomic, dumps, loads

log = logging.getLogger("sentinelle.models")


class ModelStore:
    def __init__(self, db: Database, root: Path, bus=None):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self._cache: dict[str, MetaModel | None] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _version(version):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", version):
            raise ValueError("Identifiant de modèle invalide")
        return version

    def event(self, version, timeframe, event, reason=None, data=None):
        self.db.insert("model_events", {"ts": time.time(), "version": version, "timeframe": timeframe,
                                      "event": event, "reason": reason, "data": dumps(data) if data else None})

    def history(self, timeframe=None, limit=100):
        clause, params = (" WHERE timeframe=?", [timeframe]) if timeframe else ("", [])
        rows = self.db.query("SELECT * FROM model_events" + clause + " ORDER BY id DESC LIMIT ?", [*params, min(500, max(1, limit))])
        for row in rows:
            row["data"] = loads(row["data"], {})
        return rows

    def lock_policy(self, protocol_sha, start, end, reason):
        if not re.fullmatch(r"[a-f0-9]{64}", protocol_sha) or not 0 < start < end < float("inf"):
            raise ValueError("Verrou de protocole invalide")
        with self.db.transaction():
            row = self.db.one("SELECT * FROM model_policy_locks WHERE protocol_sha=?", (protocol_sha,))
            if row and (row["start"], row["end"], row["reason"]) != (start, end, reason):
                raise ValueError("Verrou de protocole déjà figé")
            self.db.execute("INSERT OR IGNORE INTO model_policy_locks VALUES(?,?,?,?)", (protocol_sha, start, end, reason))

    def policy_locks(self):
        # Lock immediately upon registration, through the end of the reserved run.
        return self.db.query("SELECT * FROM model_policy_locks WHERE end>? ORDER BY start", (time.time(),))

    def _require_unlocked(self):
        if self.policy_locks():
            raise ValueError("Politique figée : promotion et retrait interdits pendant le protocole prospectif")

    def by_version(self, version):
        """Verify bytes even on cache hits; legacy artifacts remain explicitly unverified."""
        row = self.get(version)
        if not row:
            return None
        try:
            raw = Path(row["path"]).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if row.get("sha256") and sha != row["sha256"]:
                raise ValueError("Empreinte du modèle invalide")
            key = version + ":" + sha
            with self._lock:
                model = self._cache.get(key)
                if model is None:
                    # Deserialize the exact verified bytes, not a second path read (TOCTOU).
                    model = MetaModel.load(io.BytesIO(raw))
                    if model.version != version or model.timeframe != row["timeframe"]:
                        raise ValueError("Version ou horizon du fichier incohérent")
                    self._cache[key] = model
                return model
        except Exception as exc:
            self.invalidate()
            log.warning("Modèle %s refusé : %s", version, exc)
            self.db.log("error", "models", f"Modèle {version} illisible ou modifié : observation uniquement")
            return None

    def _row(self, status, timeframe):
        return self.db.one("SELECT * FROM models WHERE status=? AND timeframe=? ORDER BY created_at DESC LIMIT 1", (status, timeframe))

    def champion(self, timeframe: str) -> MetaModel | None:
        row = self._row("champion", timeframe)
        return self.by_version(row["version"]) if row else None

    def trial(self, timeframe: str) -> MetaModel | None:
        """Modèle « à l'essai » : prometteur mais pas prouvé. Réservé à l'entraînement en argent fictif."""
        row = self._row("trial", timeframe)
        return self.by_version(row["version"]) if row else None

    def challenger(self, timeframe):
        row = self._row("challenger", timeframe)
        return self.by_version(row["version"]) if row else None

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

    @atomic
    def register(self, result, report: dict, *, artifact_bytes=None) -> dict:
        """Enregistre le résultat d'un entraînement ; promeut le modèle s'il est validé."""
        model = result.final_model
        version = self._version(model.version)
        path = self.root / f"{version}.joblib"
        if path.exists() or self.get(version) or path.with_suffix(".json").exists():
            raise ValueError("Version déjà enregistrée : aucun fichier ne sera écrasé")
        # Exclusive destination survives concurrent registrars; interrupted artifacts are preserved.
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            if artifact_bytes is None:
                temp = Path(tmp) / "model.joblib"
                model.save(temp)
                raw = temp.read_bytes()
            else:
                raw = artifact_bytes
                imported = MetaModel.load(io.BytesIO(raw))
                if imported.version != version or imported.timeframe != model.timeframe:
                    raise ValueError("L'artefact ne correspond pas à la version")
            with path.open("xb") as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
        with path.with_suffix(".json").open("x", encoding="utf-8") as target:
            json.dump(report, target, default=str, ensure_ascii=False)
        params = {"recipe": model.recipe, "params": model.params, "half_life_days": model.half_life_days,
                  "threshold": model.threshold, "trained_until": model.trained_until, "calib": model.calib,
                  "feature_names": model.feature_names, "period": report.get("period"),
                  "split_counts": report.get("split_counts")}
        status = "challenger"
        notes = " ".join(result.reasons)
        self.db.insert(
            "models",
            {"version": version, "created_at": time.time(), "status": status, "timeframe": model.timeframe,
             "metrics": dumps(model.metrics), "path": str(path), "notes": notes,
             "sha256": hashlib.sha256(raw).hexdigest(), "params": dumps(params)},
        )
        self.event(version, model.timeframe, "enregistré", notes, {"sha256": hashlib.sha256(raw).hexdigest()})
        # le dernier entraînement fait foi : un ancien modèle à l'essai est retiré dans tous les cas
        self.db.execute("UPDATE models SET status = 'retired' WHERE status = 'trial' AND timeframe = ? AND version != ?", (model.timeframe, version))
        if result.ok and self._approval(version) and not self.policy_locks():
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

    def import_artifact(self, path, expected_sha):
        """Import trusted local bytes unchanged. Re-pickling changes some joblib SHA values."""
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("Empreinte de l'artefact importé invalide")
        model = MetaModel.load(io.BytesIO(raw))
        existing = self.get(model.version)
        if existing:
            if existing.get("sha256") != expected_sha or self.by_version(model.version) is None:
                raise ValueError("Conflit avec une version déjà enregistrée ; aucun remplacement")
            return existing
        result = SimpleNamespace(final_model=model, ok=False, trial=False,
                                 reasons=["Artefact local figé importé sans promotion ni réentraînement"])
        return self.register(result, {"origin": "trusted_frozen_artifact", "sha256": expected_sha}, artifact_bytes=raw)

    def promote(self, version: str) -> None:
        row = self.get(version)
        if row is None:
            raise ValueError("Modèle introuvable")
        if (row["metrics"] or {}).get("verdict") != "validé":
            raise ValueError("Seul un modèle validé sur des données jamais vues peut devenir le modèle actif")
        with self.db.transaction():
            self._require_unlocked()
            approval = self._approval(version)
            current = self.db.scalar("SELECT version FROM models WHERE timeframe=? AND status='champion'", (row["timeframe"],))
            if current == version:
                return  # idempotent; no duplicate promotion event
            if not approval or approval["reference"] != (current or "all_signals"):
                raise ValueError("Validation appariée indépendante absente ou champion de référence modifié")
            if not row.get("sha256") or self.by_version(version) is None:
                raise ValueError("Modèle non vérifiable ou illisible")
            self.db.execute(
                "UPDATE models SET status = 'retired' WHERE status = 'champion' AND timeframe = ?", (row["timeframe"],)
            )
            self.db.update("models", version, {"status": "champion", "promoted_at": time.time()}, key="version")
            self.event(version, row["timeframe"], "promu", "Preuve appariée enregistrée", {"previous": current})
        self.invalidate()
        self.db.log("info", "models", f"Nouveau modèle actif : {version}")
        if self.bus:
            self.bus.publish("model", {"version": version, "status": "champion"})

    def retire(self, version: str) -> None:
        with self.db.transaction():
            self._require_unlocked()
            row = self.get(version)
            if row is None:
                raise ValueError("Modèle introuvable")
            self.db.update("models", version, {"status": "retired"}, key="version")
            self.event(version, row["timeframe"], "retiré", "Retrait explicite")
        self.invalidate()

    def get(self, version: str) -> dict | None:
        row = self.db.one("SELECT * FROM models WHERE version = ?", (version,))
        if row:
            row["metrics"] = loads(row["metrics"], {})
            row["params"] = loads(row.get("params"), {})
        return row

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT * FROM models ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["metrics"] = loads(r["metrics"], {})
            r["params"] = loads(r.get("params"), {})
            r.pop("path", None)
        return rows

    def report(self, version: str) -> dict | None:
        self._version(version)
        p = self.root / f"{version}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
