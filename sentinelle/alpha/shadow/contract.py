"""``alpha_shadow_contract_v1`` : formats stricts des observations, décisions, résultats mûrs,
références et rapports.

Règles communes :
- JSON pur ; tout champ inconnu est refusé (rien n'est ignoré en silence) ;
- instants en millisecondes UTC entières (``*_ms``), bornés à [2017, 2100] : un instant en
  secondes ou en microsecondes est refusé au lieu d'être « deviné » ;
- nombres finis uniquement ; un booléen n'est jamais accepté comme nombre ;
- une décision ne peut contenir aucun champ de résultat : la liste de ses champs est fermée.

Ce contrat est indépendant de celui de Mission Control (#39) : il décrit ce que le laboratoire
ACCEPTE en entrée (``bar``, ``book``) et ce qu'il PRODUIT (``decision``, ``reference``,
``outcome``, ``report``). Aucun module de Mission Control n'est importé ; l'appariement des deux
contrats fera l'objet d'une revue commune.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable

from . import CONTRACT

MS_MIN, MS_MAX = 1_483_228_800_000, 4_102_444_800_000  # 2017-01-01 .. 2100-01-01 UTC
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}/[A-Z]{3,5}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,47}$")

ACTIONS = ("ENTER", "ABSTAIN", "NO_SIGNAL", "REFUSED")
BAR_STATUS = ("DECIDABLE", "HISTORY", "PENDING")
OUTCOME_STATUS = ("filled", "abandoned", "refused_by_portfolio", "latent")
EVIDENCE = ("book", "bar_model")
VERDICTS = ("NOT_STARTED", "IN_PROGRESS", "EXPLORATORY", "FAILED_INTEGRITY", "INSUFFICIENT_EVIDENCE", "REJECTED",
            "INCONCLUSIVE", "PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY")
MODES = ("prospective", "replay_exploratory", "synthetic")


class ContractError(ValueError):
    """Enregistrement refusé ; ``code`` est une raison stable, lisible par une machine."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def canonical(value: Any) -> bytes:
    """Sérialisation canonique (clés triées, pas d'espace, NaN interdit) : base de toutes les empreintes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


# ---------------------------------------------------------------------------
# Validateurs élémentaires
# ---------------------------------------------------------------------------

Check = Callable[[Any], bool]


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def ms(v: Any) -> bool:
    return _int(v) and MS_MIN <= v <= MS_MAX


def positive(v: Any) -> bool:
    return _num(v) and v > 0


def non_negative(v: Any) -> bool:
    return _num(v) and v >= 0


def text(v: Any) -> bool:
    return isinstance(v, str) and 0 < len(v) <= 400 and v == v.strip()


def symbol(v: Any) -> bool:
    return isinstance(v, str) and bool(SYMBOL_RE.match(v))


def digest(v: Any) -> bool:
    return isinstance(v, str) and bool(SHA_RE.match(v))


def code(v: Any) -> bool:
    return isinstance(v, str) and bool(CODE_RE.match(v))


def boolean(v: Any) -> bool:
    return isinstance(v, bool)


def one_of(*values: Any) -> Check:
    return lambda v: isinstance(v, str) and v in values


def optional(check: Check) -> Check:
    return lambda v: v is None or check(v)


def list_of(check: Check, *, max_len: int = 1000) -> Check:
    return lambda v: isinstance(v, list) and len(v) <= max_len and all(check(x) for x in v)


def record(fields: dict[str, Check]) -> Check:
    """Objet à liste de champs FERMÉE : un champ manquant ou en trop le rend invalide."""
    def check(v: Any) -> bool:
        return isinstance(v, dict) and set(v) == set(fields) and all(f(v[k]) for k, f in fields.items())
    return check


def mapping(key: Check, value: Check, *, max_len: int = 64) -> Check:
    return lambda v: isinstance(v, dict) and len(v) <= max_len and all(key(k) and value(x) for k, x in v.items())


def _levels(v: Any) -> bool:
    return (isinstance(v, list) and 0 < len(v) <= 200
            and all(isinstance(x, list) and len(x) == 2 and positive(x[0]) and positive(x[1]) for x in v))


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------

PLAN = record({"stop_price": positive, "max_bars": lambda v: _int(v) and 0 < v <= 1000, "exit_rule": optional(text),
               "trail_atr": optional(positive), "target_price": optional(positive)})
CANDIDATE = record({"teacher": text, "vetoed": boolean, "veto_reasons": list_of(text, max_len=8), "climate": optional(_int),
                    "score_r": optional(_num), "taken": boolean, "plan": PLAN})
HISTORY = record({"bars": lambda v: _int(v) and v >= 0, "first_open_at_ms": optional(ms), "tape_sha256": digest})
THEORETICAL = record({"equity_eur": non_negative, "risk_eur": non_negative, "notional_eur": non_negative})
ENTRY = record({"at_ms": ms, "price": positive, "evidence": one_of(*EVIDENCE), "book_sha256": optional(digest),
                "notional_eur": positive, "qty": positive, "fee_eur": non_negative, "partial": boolean,
                "bar_received_late": boolean})
EXIT = record({"at_ms": ms, "price": positive, "reason": text, "evidence": one_of(*EVIDENCE), "fee_eur": non_negative})
INTERVAL = record({"days": lambda v: _int(v) and v >= 0, "mean": optional(_num), "ci_low": optional(_num),
                   "ci_high": optional(_num)})

SCHEMAS: dict[str, dict[str, Check]] = {
    # --- entrées (exportées par un collecteur indépendant) ---
    "bar": {
        "symbol": symbol, "timeframe_ms": lambda v: _int(v) and v in (14_400_000,), "open_at_ms": ms, "close_at_ms": ms,
        "open": positive, "high": positive, "low": positive, "close": positive, "volume": non_negative,
        "source": text, "published_at_ms": optional(ms), "received_at_ms": ms, "payload_sha256": optional(digest),
    },
    "book": {
        "symbol": symbol, "source": text, "exchange_at_ms": optional(ms), "received_at_ms": ms,
        "time_quality": one_of("exchange", "receipt_only"), "bids": _levels, "asks": _levels,
        "update_id": optional(lambda v: _int(v) and v >= 0),
    },
    # --- sorties ---
    "decision": {
        "protocol_id": text, "manifest_sha256": digest, "model_sha256": digest, "arm": one_of("r1"), "symbol": symbol,
        "as_of_ms": ms, "bar_sha256": optional(digest), "history": HISTORY,
        "observed": record({"published_at_ms": optional(ms), "received_at_ms": optional(ms)}),
        "recorded_at_ms": ms, "action": one_of(*ACTIONS), "reason": code, "candidates": list_of(CANDIDATE, max_len=8),
        "chosen": optional(text), "threshold_r": optional(_num), "theoretical": optional(THEORETICAL),
    },
    "reference": {
        "protocol_id": text, "symbol": symbol, "as_of_ms": ms, "bar_sha256": optional(digest), "recorded_at_ms": ms,
        "arms": mapping(text, list_of(text, max_len=8), max_len=16), "unavailable": list_of(text, max_len=16),
    },
    "outcome": {
        "protocol_id": text, "arm": text, "symbol": symbol, "teacher": text, "decision_as_of_ms": ms,
        "decision_sha256": digest, "status": one_of(*OUTCOME_STATUS), "refusal": optional(text),
        "entry": optional(ENTRY), "exit": optional(EXIT), "gross_eur": optional(_num), "costs_eur": optional(_num),
        "net_eur": optional(_num), "net_r": optional(_num), "bars_held": optional(lambda v: _int(v) and v >= 0),
        "available_at_ms": ms,
    },
    "report": {
        "protocol_id": text, "manifest_sha256": digest, "model_sha256": digest, "mode": one_of(*MODES),
        "generated_at_ms": ms, "verdict": one_of(*VERDICTS), "reasons": list_of(text, max_len=40),
        "r1_status": one_of("not_evaluated", "rejected", "retained_as_candidate_for_a_new_paper_protocol"),
        "window": record({"start_ms": ms, "end_ms": ms, "complete": boolean, "days_observed": lambda v: _int(v) and v >= 0}),
        "integrity": lambda v: isinstance(v, dict), "coverage": lambda v: isinstance(v, dict),
        "primary": lambda v: isinstance(v, dict), "arms": lambda v: isinstance(v, dict),
        "secondary": lambda v: isinstance(v, dict),
        "claim": one_of("simulation fictive ; aucun gain futur promis ; aucune promotion automatique"),
    },
}

def validate(rec: Any, kind: str | None = None) -> dict:
    """Renvoie l'enregistrement s'il est conforme, lève ``ContractError`` sinon."""
    if not isinstance(rec, dict):
        raise ContractError("NOT_AN_OBJECT")
    if rec.get("contract") != CONTRACT:
        raise ContractError("UNKNOWN_CONTRACT", str(rec.get("contract")))
    k = rec.get("kind")
    if k not in SCHEMAS or (kind is not None and k != kind):
        raise ContractError("UNKNOWN_KIND", str(k))
    schema = SCHEMAS[k]
    body = {key: v for key, v in rec.items() if key not in ("contract", "kind")}
    extra, missing = sorted(set(body) - set(schema)), sorted(set(schema) - set(body))
    if extra:
        raise ContractError("UNKNOWN_FIELD", ",".join(extra))
    if missing:
        raise ContractError("MISSING_FIELD", ",".join(missing))
    for key, check in schema.items():
        try:
            ok = check(body[key])
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise ContractError("INVALID_FIELD", key)
    _CROSS[k](rec)
    return rec


def _bar_rules(b: dict) -> None:
    if b["close_at_ms"] != b["open_at_ms"] + b["timeframe_ms"] or b["open_at_ms"] % b["timeframe_ms"]:
        raise ContractError("MISALIGNED_BAR")
    if b["high"] < max(b["open"], b["close"]) or b["low"] > min(b["open"], b["close"]) or b["low"] > b["high"]:
        raise ContractError("INCOHERENT_OHLC")
    if b["published_at_ms"] is not None and b["published_at_ms"] > b["received_at_ms"]:
        raise ContractError("PUBLISHED_AFTER_RECEIVED")


def _book_rules(b: dict) -> None:
    bids, asks = [x[0] for x in b["bids"]], [x[0] for x in b["asks"]]
    if any(bids[i] <= bids[i + 1] for i in range(len(bids) - 1)) or any(asks[i] >= asks[i + 1] for i in range(len(asks) - 1)):
        raise ContractError("UNSORTED_BOOK")
    if bids[0] >= asks[0]:
        raise ContractError("CROSSED_BOOK")
    if (b["exchange_at_ms"] is None) != (b["time_quality"] == "receipt_only"):
        raise ContractError("BOOK_TIME_QUALITY")
    if b["exchange_at_ms"] is not None and b["exchange_at_ms"] > b["received_at_ms"]:
        raise ContractError("PUBLISHED_AFTER_RECEIVED")


def _decision_rules(d: dict) -> None:
    taken = [c["teacher"] for c in d["candidates"] if c["taken"]]
    if (d["action"] == "ENTER") != bool(taken) or (d["chosen"] is not None) != bool(taken):
        raise ContractError("INCONSISTENT_DECISION")
    if taken and d["chosen"] != taken[0]:
        raise ContractError("INCONSISTENT_DECISION")
    if any(c["taken"] and c["vetoed"] for c in d["candidates"]):
        raise ContractError("VETOED_ENTRY")
    if d["recorded_at_ms"] < d["as_of_ms"]:
        raise ContractError("RECORDED_BEFORE_AS_OF")
    if d["action"] == "REFUSED" and d["candidates"]:
        raise ContractError("INCONSISTENT_DECISION")


def _reference_rules(r: dict) -> None:
    if r["recorded_at_ms"] < r["as_of_ms"]:
        raise ContractError("RECORDED_BEFORE_AS_OF")


def _outcome_rules(o: dict) -> None:
    if o["available_at_ms"] < o["decision_as_of_ms"]:
        raise ContractError("OUTCOME_BEFORE_DECISION")
    if o["status"] in ("filled", "latent"):
        if o["entry"] is None or o["net_eur"] is None or (o["status"] == "filled") != (o["exit"] is not None):
            raise ContractError("INCOMPLETE_OUTCOME")
        if o["entry"]["at_ms"] < o["decision_as_of_ms"]:
            raise ContractError("FILL_BEFORE_DECISION")
    else:  # ni l'abandon ni le refus du portefeuille n'ont de résultat : jamais de « gain évité »
        if any(o[k] is not None for k in ("entry", "exit", "gross_eur", "costs_eur", "net_eur", "net_r", "bars_held")):
            raise ContractError("RESULT_WITHOUT_FILL")


def _report_rules(r: dict) -> None:
    if r["window"]["end_ms"] <= r["window"]["start_ms"]:
        raise ContractError("INVALID_WINDOW")
    if r["verdict"] == "PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY" and r["mode"] != "prospective":
        raise ContractError("NON_PROSPECTIVE_PROMOTION")


_CROSS: dict[str, Callable[[dict], None]] = {"bar": _bar_rules, "book": _book_rules, "decision": _decision_rules,
                                             "reference": _reference_rules, "outcome": _outcome_rules, "report": _report_rules}


def make(kind: str, **fields: Any) -> dict:
    """Construit puis valide un enregistrement (les producteurs passent tous par ici)."""
    return validate({"contract": CONTRACT, "kind": kind, **fields}, kind)


# ---------------------------------------------------------------------------
# Migrations et adaptateurs
# ---------------------------------------------------------------------------

MIGRATIONS: dict[str, Callable[[dict], dict]] = {}
"""Versions antérieures du contrat -> fonction de conversion vers la version courante.
Vide en v1 : toute version inconnue est refusée, jamais « interprétée au mieux »."""


def migrate(rec: Any) -> dict:
    if isinstance(rec, dict) and rec.get("contract") == CONTRACT:
        return validate(rec)
    version = rec.get("contract") if isinstance(rec, dict) else None
    if version not in MIGRATIONS:
        raise ContractError("UNKNOWN_CONTRACT", str(version))
    return validate(MIGRATIONS[version](dict(rec)))


def bar_from_kline(sym: str, row: list, *, received_at_ms: int, source: str, published_at_ms: int | None = None,
                   payload_sha256: str | None = None, timeframe_ms: int = 14_400_000) -> dict:
    """Adaptateur pur : ligne de bougie publique ``[ouverture_ms, o, h, l, c, v, ...]`` -> ``bar`` du contrat.

    Les prix peuvent être des chaînes décimales (format des plateformes) ; ils sont convertis en nombres.
    """
    try:
        ts = int(row[0])
        o, h, lo, c, v = (float(x) for x in row[1:6])
    except (TypeError, ValueError, IndexError):
        raise ContractError("INVALID_FIELD", "kline") from None
    if ts != float(row[0]):
        raise ContractError("INVALID_FIELD", "open_at_ms")
    return make("bar", symbol=sym, timeframe_ms=timeframe_ms, open_at_ms=ts, close_at_ms=ts + timeframe_ms, open=o, high=h,
                low=lo, close=c, volume=v, source=source, published_at_ms=published_at_ms, received_at_ms=received_at_ms,
                payload_sha256=payload_sha256)


def json_schema() -> dict:
    """Description lisible des champs (documentation ; la validation de référence est ``validate``)."""
    return {"contract": CONTRACT, "time_unit": "milliseconds since epoch, UTC", "unknown_fields": "refused",
            "kinds": {k: sorted(v) for k, v in SCHEMAS.items()}, "actions": list(ACTIONS), "verdicts": list(VERDICTS),
            "modes": list(MODES), "bar_status": list(BAR_STATUS), "outcome_status": list(OUTCOME_STATUS),
            "execution_evidence": list(EVIDENCE)}
