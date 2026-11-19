"""Registre shadow en ajout seul, validé par pas, avec ancres externes.

Pourquoi une chaîne d'empreintes ne suffit pas (audit du ``ShadowLedger`` de la PR #40) : celui
qui peut écrire le fichier peut le réécrire EN ENTIER et recalculer toutes les empreintes ; la
chaîne reste cohérente et ``verify`` ne voit rien. Elle détecte une retouche partielle, pas une
réécriture complète.

Réponse, sans secret ni service :
- **ancre** : ``anchor()`` rend une ligne courte (numéro et empreinte de la dernière ligne). Elle
  est publiée par l'opérateur sur un support qu'il ne peut pas réécrire seul et qui date les
  dépôts (commentaire ou commit poussé sur GitHub). ``verify_anchors`` compare ensuite le
  registre aux ancres publiées : réécriture, troncature ou bifurcation avant une ancre sont
  détectées. Ce qui suit la dernière ancre n'est pas encore prouvé, et le rapport le dit ;
- **encadrement temporel** : une décision contient l'empreinte de la bougie qui l'a déclenchée
  (elle n'a pas pu être écrite avant sa clôture) et elle est couverte par une ancre datée (elle
  n'a pas pu être écrite après). ``precedence`` mesure la part des décisions prouvées antérieures
  à leur résultat ;
- **pas atomiques** : toutes les lignes d'un pas sont écrites d'un seul bloc et closes par une
  ligne ``commit``. Après un arrêt brutal, les lignes sans ``commit`` sont déclarées nulles par une
  ligne ``recovery`` (elles restent visibles) : aucune décision n'est reconstituée après coup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .contract import canonical, sha256

GENESIS = "0" * 64
ANCHOR_CONTRACT = "alpha_shadow_anchor_v1"


class LedgerError(ValueError):
    pass


def line_sha(line: dict) -> str:
    return sha256({k: v for k, v in line.items() if k != "sha"})


def parse(raw: bytes) -> tuple[list[dict], bytes]:
    """Éléments du fichier dans l'ordre (lignes, ou marqueur ``torn`` pour un fragment illisible)
    et fragment final sans saut de ligne (écriture interrompue), s'il existe."""
    items: list[dict] = []
    chunks = raw.split(b"\n")
    tail = chunks.pop()  # vide si le fichier se termine par un saut de ligne
    for chunk in chunks:
        if not chunk:
            continue
        try:
            obj = json.loads(chunk.decode("utf-8"))
            if not isinstance(obj, dict) or "seq" not in obj:
                raise ValueError
            items.append(obj)
        except (ValueError, UnicodeError):
            items.append({"kind": "torn", "raw_sha256": sha256(chunk)})
    return items, tail


def verify_chain(items: list[dict]) -> dict:
    """Vérifie numérotation, chaînage, empreintes, horloge, commits et reprises.

    Renvoie les lignes VALIDES (pas clos, hors lignes annulées), les lignes d'un pas non clos et les
    fragments illisibles pas encore déclarés. Un fragment illisible suivi d'une ligne ordinaire
    (donc jamais déclaré par une reprise) est une corruption : erreur.
    """
    prev, clock, k = GENESIS, 0, 0
    valid: list[dict] = []
    pending: list[dict] = []
    void: list[int] = []
    open_torn: list[str] = []
    for ln in items:
        if ln.get("kind") == "torn":
            open_torn.append(ln["raw_sha256"])
            continue
        if ln.get("seq") != k or ln.get("prev") != prev or line_sha(ln) != ln.get("sha"):
            raise LedgerError(f"Chaîne rompue à la ligne {k}")
        if not isinstance(ln.get("at"), int) or ln["at"] < clock or not isinstance(ln.get("step"), int):
            raise LedgerError(f"Horloge qui recule à la ligne {k}")
        prev, clock, k = ln["sha"], ln["at"], k + 1
        if ln["kind"] == "recovery":
            body = ln["body"]
            if body["void_from_seq"] != (pending[0]["seq"] if pending else None) or body["void_lines"] != len(pending) \
                    or body["torn_fragments"] != open_torn:
                raise LedgerError(f"Reprise incohérente à la ligne {ln['seq']}")
            void += [p["seq"] for p in pending]
            pending, open_torn = [], []
            valid.append(ln)
            continue
        if open_torn:
            raise LedgerError(f"Fragment illisible non déclaré avant la ligne {ln['seq']}")
        if ln["kind"] == "commit":
            if ln["body"]["lines"] != len(pending) or any(p["step"] != ln["step"] for p in pending):
                raise LedgerError(f"Commit incohérent à la ligne {ln['seq']}")
            valid += pending + [ln]
            pending = []
        else:
            if pending and pending[0]["step"] != ln["step"]:
                raise LedgerError(f"Pas non clos à la ligne {ln['seq']}")
            pending.append(ln)
    return {"valid": valid, "uncommitted": pending, "void": void, "undeclared_torn": open_torn, "head": prev, "lines": k,
            "clock": clock}


def chain_lines(items: list[dict]) -> list[dict]:
    return [ln for ln in items if ln.get("kind") != "torn"]


class ChainLedger:
    """Écrit des pas atomiques ; ne relit jamais ses propres lignes pour décider."""

    def __init__(self, path: Path | None = None, *, fsync: bool = True):
        self.path = Path(path) if path else None
        self.fsync = fsync
        self.lines: list[dict] = []  # lignes de la chaîne
        self.items: list[dict] = []  # tout le fichier, dans l'ordre, fragments illisibles compris (pour la vérification)
        self._buffer: list[dict] | None = None
        self._step = -1
        self._at = 0
        self.recovered: dict | None = None
        if self.path and self.path.exists() and self.path.stat().st_size:
            self._load()

    # -- chargement et reprise --

    def _load(self) -> None:
        items, fragment = parse(self.path.read_bytes())
        self._needs_newline = bool(fragment)
        if fragment:  # dernière ligne sans saut de ligne : complète (acceptée) ou tronquée (fragment illisible)
            try:
                obj = json.loads(fragment.decode("utf-8"))
                verify_chain(items + [obj])
                items.append(obj)
            except (ValueError, UnicodeError, LedgerError, TypeError, KeyError):
                items.append({"kind": "torn", "raw_sha256": sha256(fragment)})
        report = verify_chain(items)  # lève si la chaîne est rompue
        self.items, self.lines = items, chain_lines(items)
        self._step = max((ln["step"] for ln in self.lines), default=-1)
        self._at = report["clock"]
        if report["uncommitted"] or report["undeclared_torn"]:
            self.recovered = {"void_from_seq": report["uncommitted"][0]["seq"] if report["uncommitted"] else None,
                              "void_lines": len(report["uncommitted"]), "torn_fragments": report["undeclared_torn"]}

    def recover(self, now_ms: int) -> dict | None:
        """Après un arrêt brutal : déclare nulles les lignes sans commit. À appeler avant tout nouveau pas."""
        if not self.recovered:
            return None
        body, self.recovered = self.recovered, None
        self._step += 1
        self._at = max(self._at, int(now_ms))
        line = self._seal("recovery", body, self._step, self._at)
        self._write([line])
        return line

    # -- écriture --

    @property
    def head(self) -> str:
        return self.lines[-1]["sha"] if self.lines else GENESIS

    @property
    def clock(self) -> int:
        return self._at

    def _seal(self, kind: str, body: dict, step: int, at: int) -> dict:
        prev = self._buffer[-1]["sha"] if self._buffer else self.head
        seq = len(self.lines) + (len(self._buffer) if self._buffer else 0)
        line = {"seq": seq, "prev": prev, "step": step, "at": at, "kind": kind, "body": body}
        line["sha"] = line_sha(line)
        return line

    def _write(self, lines: list[dict]) -> None:
        if self.path:
            data = b"".join(canonical(ln) + b"\n" for ln in lines)
            if getattr(self, "_needs_newline", False):  # referme une dernière ligne restée sans saut de ligne
                data, self._needs_newline = b"\n" + data, False
            with self.path.open("ab") as fh:
                fh.write(data)
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
        self.lines += lines
        self.items += lines

    def begin(self, at_ms: int) -> int:
        if self.recovered:
            raise LedgerError("Reprise non déclarée : appelez recover() avant d'écrire")
        if self._buffer is not None:
            raise LedgerError("Pas déjà ouvert")
        if at_ms < self._at:
            raise LedgerError("L'horloge du registre ne peut pas reculer")
        self._step += 1
        self._at = int(at_ms)
        self._buffer = []
        return self._step

    def add(self, kind: str, body: dict) -> dict:
        if self._buffer is None:
            raise LedgerError("Aucun pas ouvert")
        line = self._seal(kind, body, self._step, self._at)
        self._buffer.append(line)
        return line

    def commit(self, state_sha256: str) -> dict:
        if self._buffer is None:
            raise LedgerError("Aucun pas ouvert")
        commit = self._seal("commit", {"lines": len(self._buffer), "state_sha256": state_sha256}, self._step, self._at)
        lines, self._buffer = self._buffer + [commit], None
        self._write(lines)
        return commit

    def abort(self) -> None:
        """Abandonne le pas en cours sans rien écrire (erreur de l'appelant)."""
        if self._buffer is not None:
            self._buffer = None
            self._step -= 1

    # -- ancres --

    def anchor(self, now_ms: int) -> dict:
        """Ligne à publier hors de portée de celui qui tient le fichier (elle ne contient aucun secret)."""
        if not self.lines:
            raise LedgerError("Registre vide")
        return {"contract": ANCHOR_CONTRACT, "ledger_id": self.lines[0]["sha"], "seq": self.lines[-1]["seq"],
                "head_sha256": self.head, "created_at_ms": int(now_ms)}


def verify_anchors(lines: list[dict], anchors: list[dict]) -> list[str]:
    """Problèmes entre le registre et les ancres publiées (liste vide = cohérent jusqu'à la dernière ancre)."""
    problems = []
    chain = chain_lines(lines)
    last = -1
    for k, a in enumerate(anchors):
        if a.get("contract") != ANCHOR_CONTRACT:
            problems.append(f"ancre {k} : format inconnu")
            continue
        if not chain or a["ledger_id"] != chain[0]["sha"]:
            problems.append(f"ancre {k} : autre registre (session différente ou réécrite)")
        elif a["seq"] >= len(chain):
            problems.append(f"ancre {k} : registre tronqué (ligne {a['seq']} absente)")
        elif chain[a["seq"]]["sha"] != a["head_sha256"]:
            problems.append(f"ancre {k} : ligne {a['seq']} réécrite depuis la publication")
        if a.get("seq", -1) < last:
            problems.append(f"ancre {k} : ancres non chronologiques")
        last = max(last, a.get("seq", -1))
    return problems


def precedence(lines: list[dict], anchors: list[dict]) -> dict:
    """Part des décisions d'entrée prouvées antérieures à leur résultat.

    Une ancre ne compte que si elle porte ``published_at_ms`` : l'instant donné par le support
    externe (pas par notre horloge). Une décision est « prouvée antérieure » si la première ancre
    qui la couvre a été publiée avant que le premier résultat de cette décision soit disponible.
    """
    dated = sorted((a for a in anchors if isinstance(a.get("published_at_ms"), int)), key=lambda a: a["seq"])
    first_outcome: dict[str, int] = {}
    for ln in lines:
        if ln.get("kind") == "outcome":
            key = ln["body"]["decision_sha256"]
            first_outcome[key] = min(first_outcome.get(key, ln["body"]["available_at_ms"]), ln["body"]["available_at_ms"])
    total = proven = 0
    for ln in lines:
        if ln.get("kind") not in ("decision", "reference") or sha256(ln["body"]) not in first_outcome:
            continue
        total += 1
        cover = next((a for a in dated if a["seq"] >= ln["seq"]), None)
        if cover is not None and cover["published_at_ms"] < first_outcome[sha256(ln["body"])]:
            proven += 1
    last = dated[-1]["seq"] if dated else -1
    return {"decisions_with_outcome": total, "proven_prior_to_outcome": proven,
            "share": (proven / total) if total else None, "last_anchored_seq": last,
            "lines_after_last_anchor": sum(1 for ln in lines if ln.get("seq", -1) > last)}
