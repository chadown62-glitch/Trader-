"""Registre « shadow » en ajout seul, chaîné par empreintes SHA-256.

- une décision est écrite à son instant ``recorded_at`` (temps simulé), AVANT toute issue ;
- une issue ne peut référencer qu'une décision déjà écrite, et seulement à partir de
  ``revealed_at >= recorded_at`` de la décision ; les instants du registre ne reculent jamais ;
- chaque ligne contient l'empreinte de la précédente : modifier, supprimer ou réordonner une ligne
  casse la chaîne (``verify``).
Le registre ne contient que des objets inertes (dictionnaires JSON) : aucune méthode d'exécution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GENESIS = "0" * 64


def _digest(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class LedgerError(ValueError):
    pass


class ShadowLedger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.records: list[dict] = []
        self._decisions: dict[str, dict] = {}
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                self.records.append(json.loads(line))
            self.verify()
            self._decisions = {r["hash"]: r for r in self.records if r["kind"] == "decision"}

    @property
    def head(self) -> str:
        return self.records[-1]["hash"] if self.records else GENESIS

    @property
    def clock(self) -> int:
        return self.records[-1]["at"] if self.records else 0

    def _append(self, record: dict) -> dict:
        if record["at"] < self.clock:
            raise LedgerError("Le temps du registre ne peut pas reculer")
        record = {"seq": len(self.records), "prev": self.head, **record}
        record["hash"] = _digest(record)
        self.records.append(record)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return record

    def record_decision(self, recorded_at: int, payload: dict) -> str:
        if "outcome" in payload or "net_r" in payload or "pnl" in payload:
            raise LedgerError("Une décision ne peut pas contenir son issue")
        rec = self._append({"kind": "decision", "at": int(recorded_at), "payload": payload})
        self._decisions[rec["hash"]] = rec
        return rec["hash"]

    def record_outcome(self, decision_hash: str, revealed_at: int, payload: dict) -> str:
        d = self._decisions.get(decision_hash)
        if d is None:
            raise LedgerError("Issue sans décision préalable")
        if revealed_at < d["at"]:
            raise LedgerError("Issue révélée avant la décision")
        return self._append({"kind": "outcome", "at": int(revealed_at), "decision": decision_hash, "payload": payload})["hash"]

    def verify(self) -> bool:
        prev, clock, seen = GENESIS, 0, {}
        for k, r in enumerate(self.records):
            if r.get("seq") != k or r.get("prev") != prev or _digest(r) != r.get("hash"):
                raise LedgerError(f"Chaîne rompue à la ligne {k}")
            if r["at"] < clock:
                raise LedgerError(f"Temps qui recule à la ligne {k}")
            if r["kind"] == "outcome":
                d = seen.get(r["decision"])
                if d is None or r["at"] < d["at"]:
                    raise LedgerError(f"Issue sans décision antérieure à la ligne {k}")
            elif r["kind"] == "decision":
                seen[r["hash"]] = r
            else:
                raise LedgerError(f"Type inconnu à la ligne {k}")
            prev, clock = r["hash"], r["at"]
        return True
