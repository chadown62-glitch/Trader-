"""Registre des modèles d'IA : champion, challenger, historique des promotions, retour en arrière.

Vocabulaire :
- **champion** : le modèle approuvé pour le trading en argent fictif. Version immuable (empreinte du fichier
  vérifiée à chaque chargement), paramètres et mesures enregistrés, date de promotion connue ;
- **challenger** : un nouveau modèle, validé seul sur des données jamais vues, mais pas encore prouvé meilleur
  que le champion. Il ne trade pas : ses prédictions sont enregistrées « à blanc », à côté de celles du champion ;
- **à l'essai** : prometteur sans être prouvé. Utilisé en argent fictif, à taille réduite, seulement s'il n'y a
  pas de champion ;
- **retiré** : ancien champion (conservé : on peut y revenir) ou modèle écarté ; **rejeté** : non validé.

Règles fixées d'avance (voir docs/APPRENTISSAGE.md) :
1. un modèle non validé ne devient jamais champion ;
2. s'il n'y a pas de champion, le premier modèle validé le devient ;
3. sinon, un modèle validé ne remplace le champion que s'il fait **mieux que lui sur les mêmes situations**,
   qu'aucun des deux n'a vues, avec une différence qui n'est très probablement pas due au hasard ;
4. s'il fait moins bien, il est rejeté ; si l'on ne peut pas conclure, il attend comme challenger ;
5. après une promotion, l'ancien champion reste suivi « à blanc » : si le nouveau fait nettement moins bien que
   lui, retour automatique à l'ancien.
Rien n'est jamais effacé : chaque étape est écrite dans l'historique (`model_events`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from ..ai.model import MetaModel
from ..storage.db import Database, dumps, loads

log = logging.getLogger("sentinelle.models")

DAY = 86_400
CHALLENGER_MAX_DAYS = 60  # au-delà, un challenger qui n'a pas pu être départagé cède sa place au suivant
MONITOR_DAYS = 90  # durée pendant laquelle l'ancien champion reste suivi après une promotion

EVENT_LABELS = {
    "enregistré": "Modèle enregistré",
    "promu": "Devient le champion",
    "promu manuellement": "Devient le champion (décision de l'utilisateur)",
    "à l'épreuve": "Devient le challenger (comparé au champion sur les situations à venir)",
    "à l'essai": "Mis à l'essai en argent fictif",
    "rejeté": "Rejeté",
    "non retenu": "Non retenu (un challenger est déjà à l'épreuve)",
    "remplacé": "Remplacé par un modèle plus récent",
    "rétrogradé": "N'est plus le champion",
    "rétabli": "Redevient le champion (retour en arrière)",
    "retiré": "Retiré",
    "empreinte invalide": "Fichier modifié : modèle refusé",
}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ModelStore:
    def __init__(self, db: Database, root: Path, bus=None):
        self.db = db
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self._cache: dict[str, MetaModel | None] = {}
        self._lock = threading.Lock()

    # -- chargement (version immuable) ---------------------------------------------------
    def _load(self, row: dict | None) -> MetaModel | None:
        if not row:
            return None
        try:
            path = Path(row["path"])
            if row.get("sha256") and file_hash(path) != row["sha256"]:
                self.event(row["version"], row["timeframe"], "empreinte invalide",
                           "Le fichier du modèle ne correspond plus à son empreinte d'origine : il n'est pas utilisé.")
                self.db.log("error", "models", f"Modèle {row['version']} : fichier modifié depuis son enregistrement, modèle refusé")
                return None
            return MetaModel.load(path)
        except Exception:
            log.exception("Modèle %s illisible", row["version"])
            self.db.log("error", "models", f"Modèle {row['version']} illisible : l'IA passe en observation")
            return None

    def _cached(self, key: str, row_fn) -> MetaModel | None:
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        model = self._load(row_fn())
        with self._lock:
            self._cache[key] = model
        return model

    def _row(self, status: str, timeframe: str) -> dict | None:
        return self.db.one("SELECT * FROM models WHERE status = ? AND timeframe = ? ORDER BY created_at DESC LIMIT 1", (status, timeframe))

    def champion(self, timeframe: str) -> MetaModel | None:
        return self._cached(timeframe, lambda: self._row("champion", timeframe))

    def trial(self, timeframe: str) -> MetaModel | None:
        """Modèle « à l'essai » : prometteur mais pas prouvé. Réservé à l'entraînement en argent fictif."""
        return self._cached("essai:" + timeframe, lambda: self._row("trial", timeframe))

    def challenger(self, timeframe: str) -> MetaModel | None:
        """Modèle validé seul, pas encore prouvé meilleur que le champion : il ne trade pas, il est suivi « à blanc »."""
        return self._cached("challenger:" + timeframe, lambda: self._row("challenger", timeframe))

    def monitoring(self, timeframe: str) -> dict | None:
        """Après une promotion : l'ancien champion encore suivi (pour un éventuel retour en arrière)."""
        champ = self._row("champion", timeframe)
        if champ is None:
            return None
        ev = self.db.one("SELECT * FROM model_events WHERE version = ? AND event IN ('promu', 'promu manuellement') ORDER BY id DESC LIMIT 1",
                         (champ["version"],))
        previous = (loads(ev["data"], {}) or {}).get("previous") if ev else None
        if not previous or time.time() - ev["ts"] > MONITOR_DAYS * DAY:
            return None
        row = self.db.one("SELECT * FROM models WHERE version = ?", (previous,))
        if row is None or row["status"] != "retired":
            return None
        return {"champion": champ["version"], "previous": previous, "since": ev["ts"], "until": ev["ts"] + MONITOR_DAYS * DAY}

    def previous(self, timeframe: str) -> MetaModel | None:
        mon = self.monitoring(timeframe)
        if mon is None:
            return None
        return self._cached("precedent:" + timeframe, lambda: self.db.one("SELECT * FROM models WHERE version = ?", (mon["previous"],)))

    def shadows(self, timeframe: str) -> list[MetaModel]:
        """Modèles dont les prédictions sont enregistrées à chaque situation : champion, challenger, ancien champion suivi."""
        out, seen = [], set()
        for model in (self.champion(timeframe), self.challenger(timeframe), self.previous(timeframe)):
            if model is not None and model.version not in seen:
                seen.add(model.version)
                out.append(model)
        return out

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    # -- historique --------------------------------------------------------------------------
    def event(self, version: str, timeframe: str | None, event: str, reason: str | None = None, data: dict | None = None) -> None:
        self.db.insert("model_events", {"ts": time.time(), "version": version, "timeframe": timeframe, "event": event,
                                        "reason": reason, "data": dumps(data) if data else None})

    def history(self, timeframe: str | None = None, limit: int = 100) -> list[dict]:
        if timeframe:
            rows = self.db.query("SELECT * FROM model_events WHERE timeframe = ? ORDER BY id DESC LIMIT ?", (timeframe, limit))
        else:
            rows = self.db.query("SELECT * FROM model_events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = loads(r["data"], None)
            r["label"] = EVENT_LABELS.get(r["event"], r["event"])
        return rows

    # -- enregistrement et décision -------------------------------------------------------------
    def register(self, result, report: dict, comparison: dict | None = None) -> dict:
        """Enregistre le résultat d'un entraînement et applique les règles de promotion.

        `comparison` : comparaison appariée avec le champion (ai/compare.py), si elle a pu être faite."""
        model = result.final_model
        version = model.version
        path = self.root / f"{version}.joblib"
        if path.exists() or self.get(version) is not None:
            raise ValueError(f"La version {version} existe déjà : une version enregistrée n'est jamais réécrite")
        model.save(path)
        if comparison is not None:
            report = {**report, "comparison": comparison}
        (self.root / f"{version}.json").write_text(json.dumps(report, default=str), encoding="utf-8")
        tf = model.timeframe
        notes = " ".join(result.reasons)
        self.db.insert(
            "models",
            {"version": version, "created_at": time.time(), "status": "rejected", "timeframe": tf, "metrics": dumps(model.metrics),
             "path": str(path), "notes": notes, "sha256": file_hash(path), "params": dumps(getattr(model, "meta", {}) or {})},
        )
        self.event(version, tf, "enregistré", notes[:500] or None, {"verdict": (model.metrics or {}).get("verdict")})
        # le dernier entraînement fait foi : un ancien modèle à l'essai est retiré dans tous les cas
        for old in self.db.query("SELECT version FROM models WHERE status = 'trial' AND timeframe = ?", (tf,)):
            self.db.update("models", old["version"], {"status": "retired"}, key="version")
            self.event(old["version"], tf, "remplacé", "Un entraînement plus récent a eu lieu.")
        champ = self._row("champion", tf)
        if result.ok and champ is None:
            self._promote(version, "promu", "Premier modèle validé sur des données jamais vues : aucun champion à qui le comparer.")
        elif result.ok:
            verdict = (comparison or {}).get("verdict", "insuffisant")
            explanation = (comparison or {}).get("explanation") or "Aucune comparaison avec le champion n'a pu être faite."
            if verdict == "meilleur":
                self._promote(version, "promu", explanation, {"comparison": comparison, "source": "historique"})
            elif verdict == "moins bon":
                self.event(version, tf, "rejeté", "Validé seul, mais moins bon que le champion. " + explanation, {"comparison": comparison})
            else:
                self._enter_challenger(version, tf, explanation, comparison)
        elif getattr(result, "trial", False):
            self.db.update("models", version, {"status": "trial"}, key="version")
            self.event(version, tf, "à l'essai", "Prometteur, pas encore prouvé : utilisé en argent fictif à taille réduite, s'il n'y a pas de champion.")
            self.db.log("info", "models", f"Modèle {version} mis à l'essai en argent fictif (prometteur, pas encore prouvé)")
        else:
            self.event(version, tf, "rejeté", "Non validé sur des données jamais vues.")
        self.invalidate()
        return self.get(version)

    def _enter_challenger(self, version: str, tf: str, explanation: str, comparison: dict | None) -> None:
        current = self._row("challenger", tf)
        if current is not None:
            age_days = (time.time() - current["created_at"]) / DAY
            if age_days < CHALLENGER_MAX_DAYS:
                self.db.update("models", version, {"status": "retired"}, key="version")
                self.event(version, tf, "non retenu", f"Le challenger {current['version']} est à l'épreuve depuis {age_days:.0f} jour(s) : "
                                                       "on lui laisse le temps d'être départagé avant d'en essayer un autre.")
                return
            self.db.update("models", current["version"], {"status": "retired"}, key="version")
            self.event(current["version"], tf, "remplacé", f"Pas départagé du champion en {CHALLENGER_MAX_DAYS} jours : il cède sa place.")
        self.db.update("models", version, {"status": "challenger"}, key="version")
        self.event(version, tf, "à l'épreuve", explanation, {"comparison": comparison} if comparison else None)
        self.db.log("info", "models", f"Modèle {version} : validé, mis à l'épreuve face au champion (il ne trade pas)")

    def _promote(self, version: str, event: str, reason: str, data: dict | None = None) -> None:
        row = self.get(version)
        tf = row["timeframe"]
        now = time.time()
        with self.db.transaction():
            old = self._row("champion", tf)
            if old is not None and old["version"] != version:
                self.db.update("models", old["version"], {"status": "retired"}, key="version")
                self.event(old["version"], tf, "rétrogradé", f"Remplacé par {version}.")
            self.db.update("models", version, {"status": "champion", "promoted_at": now}, key="version")
            self.event(version, tf, event, reason, {**(data or {}), "previous": old["version"] if old and old["version"] != version else None})
        self.invalidate()
        self.db.log("info", "models", f"Nouveau champion : {version}. {reason}"[:400])
        if self.bus:
            self.bus.publish("model", {"version": version, "status": "champion"})

    def promote_challenger(self, timeframe: str, comparison: dict) -> None:
        """Promotion du challenger après une comparaison concluante sur des situations vécues en direct."""
        row = self._row("challenger", timeframe)
        if row is None:
            raise ValueError("Aucun challenger")
        self._promote(row["version"], "promu", comparison.get("explanation") or "Meilleur que le champion sur les mêmes situations.",
                      {"comparison": comparison, "source": "direct"})

    def reject_challenger(self, timeframe: str, comparison: dict) -> None:
        row = self._row("challenger", timeframe)
        if row is None:
            return
        self.db.update("models", row["version"], {"status": "rejected"}, key="version")
        self.event(row["version"], timeframe, "rejeté", comparison.get("explanation") or "Moins bon que le champion.", {"comparison": comparison, "source": "direct"})
        self.invalidate()

    def promote(self, version: str) -> None:
        """Promotion décidée par l'utilisateur (hors comparaison automatique). Reste réservée à un modèle validé."""
        row = self.get(version)
        if row is None:
            raise ValueError("Modèle introuvable")
        if (row["metrics"] or {}).get("verdict") != "validé":
            raise ValueError("Seul un modèle validé sur des données jamais vues peut devenir le champion")
        if row["status"] == "champion":
            return
        self._promote(version, "promu manuellement", "Promotion décidée par l'utilisateur, sans attendre la comparaison avec le champion.")

    def rollback(self, timeframe: str, reason: str, data: dict | None = None) -> dict:
        """Retour au champion précédent. Le champion actuel est conservé (retiré), rien n'est effacé."""
        champ = self._row("champion", timeframe)
        if champ is None:
            raise ValueError("Aucun champion")
        ev = self.db.one("SELECT * FROM model_events WHERE version = ? AND event IN ('promu', 'promu manuellement', 'rétabli') ORDER BY id DESC LIMIT 1",
                         (champ["version"],))
        previous = (loads(ev["data"], {}) or {}).get("previous") if ev else None
        row = self.db.one("SELECT * FROM models WHERE version = ?", (previous,)) if previous else None
        if row is None or self._load(row) is None:
            raise ValueError("Aucun champion précédent utilisable : retour en arrière impossible")
        now = time.time()
        with self.db.transaction():
            self.db.update("models", champ["version"], {"status": "retired"}, key="version")
            self.event(champ["version"], timeframe, "rétrogradé", reason, data)
            self.db.update("models", previous, {"status": "champion", "promoted_at": now}, key="version")
            self.event(previous, timeframe, "rétabli", reason, {**(data or {}), "previous": None, "replaced": champ["version"]})
        self.invalidate()
        self.db.log("warning", "models", f"Retour en arrière : {previous} redevient le champion ({reason})"[:400])
        if self.bus:
            self.bus.publish("model", {"version": previous, "status": "champion"})
        return self.get(previous)

    def retire(self, version: str) -> None:
        row = self.get(version)
        if row is None:
            raise ValueError("Modèle introuvable")
        self.db.update("models", version, {"status": "retired"}, key="version")
        self.event(version, row["timeframe"], "retiré", "Retiré par l'utilisateur.")
        self.invalidate()

    # -- lecture ----------------------------------------------------------------------------------
    def get(self, version: str) -> dict | None:
        row = self.db.one("SELECT * FROM models WHERE version = ?", (version,))
        if row:
            row["metrics"] = loads(row["metrics"], {})
            row["params"] = loads(row.get("params"), {})
        return row

    def by_version(self, version: str) -> MetaModel | None:
        return self._load(self.db.one("SELECT * FROM models WHERE version = ?", (version,)))

    def list(self, limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT * FROM models ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["metrics"] = loads(r["metrics"], {})
            r["params"] = loads(r.get("params"), {})
            r.pop("path", None)
        return rows

    def report(self, version: str) -> dict | None:
        p = self.root / f"{version}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
