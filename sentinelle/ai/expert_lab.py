"""Offline expert-decision evidence gate. No orders, model promotion or network.

Expert decisions remain research *candidates*, never certified profitable traders.
Every admitted feature was available (and received) before the decision; every
attributed outcome requires a timestamped fill (time, price, costs), a decision
recorded *before* its outcome existed, and a label mature at the requested cut-off.

v1 audit (mission #38) — defects of v0 found by executable probes and fixed here:

1. A decision recorded after its outcome was known was accepted (hindsight record).
2. Seconds and milliseconds could be mixed silently (a label in ms looked "mature").
3. The same decision under two identifiers was counted twice.
4. Conflicting records sharing one identifier: input order decided which survived.
5. A "realized" return passed with a fill URL only: no fill time, price or costs.
6. Self-declared ``source_verified`` / ``rights_verified`` flags were sufficient.
7. Late-received features and retrospective revisions were not modelled.
8. Records dropped at window boundaries were not reported (silent purge).

The booleans are still required, but never sufficient: an independent
``ProvenanceAudit`` (archived evidence hashes, auditor, date, evidence level) must
exist for the source before ``cutoff``. This gate cannot judge authenticity itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Iterable, Mapping

ACTIONS = frozenset({"enter", "skip", "exit"})
LABEL_KINDS = frozenset({"documented_fill", "paper_fill", "hypothetical"})
TIMELY_RECORD_DELAY_SECONDS = 120
# Timestamps are UNIX seconds. Milliseconds (>= 1e11) or tiny values are refused.
MIN_SECONDS, MAX_SECONDS = 10**9, 10**11

# Evidence ladder, weakest to strongest (see sentinelle/alpha/catalogue.py).
EVIDENCE_LEVELS = ("claimed_result", "public_heuristic", "documented_method",
                   "timestamped_decision", "verified_fill")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ExpertDecision:
    sample_id: str
    source_id: str
    symbol: str
    action: str
    decision_at: int
    features_available_at: int
    recorded_at: int
    source_uri: str
    rights_evidence_uri: str
    expert_evidence_uri: str
    source_verified: bool
    rights_verified: bool
    label_kind: str | None = None
    label_available_at: int | None = None
    net_r: float | None = None
    fill_evidence_uri: str | None = None
    # v1: timestamped execution evidence required for any realized result
    fill_at: int | None = None
    fill_price: float | None = None
    exit_at: int | None = None
    exit_price: float | None = None
    costs_r: float | None = None
    # v1: when the features actually reached the expert (late delivery), and revisions
    features_received_at: int | None = None
    revision_of: str | None = None


@dataclass(frozen=True)
class ProvenanceAudit:
    """Independent review of one source, performed and archived by an operator."""
    source_id: str
    auditor: str
    audited_at: int
    level: str
    evidence_sha256: str
    rights_sha256: str
    notes: str = ""


@dataclass(frozen=True)
class Admission:
    sample_id: str
    accepted: bool
    reason: str


@dataclass(frozen=True)
class Dataset:
    eligible: tuple[ExpertDecision, ...]
    excluded: tuple[Admission, ...]
    superseded: tuple[ExpertDecision, ...] = field(default=())

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for entry in self.excluded:
            counts[entry.reason] = counts.get(entry.reason, 0) + 1
        return {
            "eligible": len(self.eligible),
            "excluded": len(self.excluded),
            "superseded": len(self.superseded),
            "exclusion_reasons": dict(sorted(counts.items())),
            "claim": "research_only_no_profit_evidence",
            "can_trade": False,
        }


def _valid_time(value: object) -> bool:
    return type(value) is int and MIN_SECONDS <= value < MAX_SECONDS


def _valid_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith("https://") and len(value) > 8


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value)


def audit_problem(audit: ProvenanceAudit | None, *, needed: str, cutoff: int) -> str | None:
    """None if the audit supports the needed evidence level at ``cutoff``, else a reason."""
    if audit is None:
        return "provenance_audit_missing"
    if (not isinstance(audit.auditor, str) or not audit.auditor.strip() or not _valid_time(audit.audited_at)
            or not _SHA256.fullmatch(str(audit.evidence_sha256)) or not _SHA256.fullmatch(str(audit.rights_sha256))):
        return "provenance_audit_invalid"
    if audit.audited_at > cutoff:
        return "provenance_audit_after_cutoff"
    if audit.level not in EVIDENCE_LEVELS or EVIDENCE_LEVELS.index(audit.level) < EVIDENCE_LEVELS.index(needed):
        return "evidence_level_insufficient"
    return None


def admission(decision: ExpertDecision, cutoff: int, audit: ProvenanceAudit | None = None) -> Admission:
    """Gate for inclusion in a *documented* supervised teaching dataset.

    A data provider's self-asserted 'verified' field is not sufficient proof;
    evidence references, the provider flags AND an independent ``ProvenanceAudit``
    are all required. The caller archives the evidence; this gate checks consistency.
    """
    identifier = decision.sample_id if isinstance(decision.sample_id, str) else "<invalid>"

    def deny(reason: str) -> Admission:
        return Admission(identifier, False, reason)

    if not all(isinstance(v, str) and v.strip() for v in (decision.sample_id, decision.source_id, decision.symbol)):
        return deny("missing_identity")
    if decision.action not in ACTIONS:
        return deny("unsupported_action")
    optional_times = [t for t in (decision.label_available_at, decision.fill_at, decision.exit_at,
                                  decision.features_received_at) if t is not None]
    if not _valid_time(cutoff) or not all(_valid_time(t) for t in
            (decision.decision_at, decision.features_available_at, decision.recorded_at, *optional_times)):
        return deny("invalid_timestamp")
    if decision.revision_of is not None and decision.recorded_at > decision.decision_at + TIMELY_RECORD_DELAY_SECONDS:
        return deny("retrospective_revision")
    if not (decision.features_available_at <= decision.decision_at
            <= decision.recorded_at <= decision.decision_at + TIMELY_RECORD_DELAY_SECONDS):
        return deny("lookahead_or_retrospective_record")
    if decision.features_received_at is not None and not (
            decision.features_available_at <= decision.features_received_at <= decision.decision_at):
        return deny("late_feature_receipt")
    if decision.recorded_at > cutoff:
        return deny("decision_not_yet_recorded")
    if not all(_valid_reference(s) for s in
            (decision.source_uri, decision.rights_evidence_uri, decision.expert_evidence_uri)):
        return deny("missing_provenance")
    if not decision.rights_verified:
        return deny("rights_unverified")
    if not decision.source_verified:
        return deny("expert_claim_unverified")
    realized = decision.label_kind in ("documented_fill", "paper_fill")
    problem = audit_problem(audit if audit is None or audit.source_id == decision.source_id else None,
                            needed="verified_fill" if realized else "timestamped_decision", cutoff=cutoff)
    if problem:
        return deny(problem)
    if decision.label_kind is None:
        if (decision.label_available_at is not None or decision.net_r is not None or decision.fill_evidence_uri
                or any(x is not None for x in (decision.fill_at, decision.fill_price, decision.exit_at,
                                               decision.exit_price, decision.costs_r))):
            return deny("incomplete_label")
        return Admission(identifier, True, "unlabeled_decision")
    if decision.label_kind not in LABEL_KINDS:
        return deny("unknown_label_kind")
    if decision.label_available_at is None or decision.label_available_at <= decision.decision_at:
        return deny("invalid_label_time")
    if decision.recorded_at >= decision.label_available_at:
        return deny("recorded_after_outcome")
    if decision.label_available_at > cutoff:
        return deny("label_not_yet_available")
    if decision.net_r is not None and not _finite(decision.net_r):
        return deny("invalid_return")
    if decision.label_kind == "hypothetical":
        if decision.net_r is not None or decision.fill_evidence_uri or decision.fill_at is not None:
            return deny("hypothetical_is_not_realized_pnl")
        return Admission(identifier, True, "hypothetical_unscored")
    if decision.action == "skip":
        return deny("skipped_trade_has_no_realized_fill")
    if decision.net_r is None or not _valid_reference(decision.fill_evidence_uri):
        return deny("realized_return_without_fill_proof")
    if not (decision.fill_at is not None and decision.exit_at is not None
            and decision.decision_at <= decision.fill_at <= decision.exit_at <= decision.label_available_at
            and _finite(decision.fill_price) and decision.fill_price > 0
            and _finite(decision.exit_price) and decision.exit_price > 0
            and _finite(decision.costs_r) and decision.costs_r >= 0):
        return deny("realized_return_without_timestamped_fill_price_costs")
    return Admission(identifier, True, "documented_research_sample")


def _content_key(d: ExpertDecision) -> tuple:
    return (d.source_id, d.symbol, d.action, d.decision_at)


def build_dataset(rows: Iterable[ExpertDecision], *, cutoff: int,
                  audits: Mapping[str, ProvenanceAudit] | None = None) -> Dataset:
    """Deterministic, input-order-independent audit. Never fetches data.

    - identical copies of one sample_id: one kept, extras reported;
    - conflicting records sharing a sample_id: ALL quarantined (no silent winner);
    - one decision under several identifiers: all but the smallest identifier reported;
    - timely revisions (``revision_of``) supersede their original, retrospective ones are refused.
    """
    audits = audits or {}
    rows = list(rows)
    by_id: dict[str, list[ExpertDecision]] = {}
    for row in rows:
        by_id.setdefault(row.sample_id if isinstance(row.sample_id, str) else "<invalid>", []).append(row)
    excluded: list[Admission] = []
    unique: list[ExpertDecision] = []
    for sid in sorted(by_id):
        copies = by_id[sid]
        if len({repr(c) for c in copies}) > 1:
            excluded.extend(Admission(sid, False, "conflicting_duplicate_sample_id") for _ in copies)
            continue
        unique.append(copies[0])
        excluded.extend(Admission(sid, False, "duplicate_sample_id") for _ in copies[1:])
    admitted: list[ExpertDecision] = []
    for row in unique:
        verdict = admission(row, cutoff, audits.get(row.source_id))
        (admitted.append(row) if verdict.accepted else excluded.append(verdict))
    # Revisions: only timely ones (admission refuses late records) may supersede their root.
    ids = {d.sample_id: d for d in admitted}
    superseded: list[ExpertDecision] = []
    chains: dict[str, list[ExpertDecision]] = {}
    for d in sorted((x for x in admitted if x.revision_of is not None), key=lambda x: (x.recorded_at, x.sample_id)):
        root = ids.get(d.revision_of)
        if (root is None or root.revision_of is not None or root.source_id != d.source_id or root.symbol != d.symbol
                or root.decision_at != d.decision_at or d.recorded_at < root.recorded_at):
            excluded.append(Admission(d.sample_id, False, "invalid_revision"))
            ids.pop(d.sample_id, None)
            continue
        chains.setdefault(root.sample_id, []).append(d)
    for root_id, revisions in chains.items():
        for older in revisions[:-1]:
            superseded.append(ids.pop(older.sample_id))
            excluded.append(Admission(older.sample_id, False, "superseded_by_later_revision"))
        superseded.append(ids.pop(root_id))
        excluded.append(Admission(root_id, False, "superseded_by_timely_revision"))
    final = list(ids.values())
    # Same decision under several identifiers: keep the smallest identifier, deterministically.
    seen: dict[tuple, str] = {}
    kept: list[ExpertDecision] = []
    for d in sorted(final, key=lambda x: (x.sample_id,)):
        key = _content_key(d)
        if key in seen:
            excluded.append(Admission(d.sample_id, False, "duplicate_decision_content"))
            continue
        seen[key] = d.sample_id
        kept.append(d)
    kept.sort(key=lambda d: (d.decision_at, d.sample_id))
    excluded.sort(key=lambda a: (a.sample_id, a.reason))
    return Dataset(tuple(kept), tuple(excluded), tuple(sorted(superseded, key=lambda d: d.sample_id)))


def visible_snapshot(dataset: Dataset, as_of: int) -> tuple[ExpertDecision, ...]:
    """Past facts only. A label cannot appear prematurely; a revision only once recorded."""
    if not _valid_time(as_of):
        raise ValueError("invalid as_of")
    output = []
    for record in dataset.eligible:
        if record.recorded_at > as_of:
            if record.revision_of is None:
                continue
            # the version of this decision that was known at as_of (root or earlier revision)
            known = [d for d in dataset.superseded if d.recorded_at <= as_of
                     and (d.sample_id == record.revision_of or d.revision_of == record.revision_of)]
            if not known:
                continue
            record = max(known, key=lambda d: (d.recorded_at, d.sample_id))
        if record.label_available_at is not None and record.label_available_at > as_of:
            output.append(replace(record, label_kind=None, label_available_at=None, net_r=None,
                                  fill_evidence_uri=None, fill_at=None, fill_price=None, exit_at=None,
                                  exit_price=None, costs_r=None))
        else:
            output.append(record)
    return tuple(output)


def chronological_partitions(dataset: Dataset, *, train_end: int, calibration_end: int,
                             evaluation_end: int, embargo_seconds: int) -> dict[str, tuple[ExpertDecision, ...]]:
    """Disjoint time windows; labels must mature by window end; purged rows are reported.

    Evaluation is protected from being reused as training. Skips and hypothetical
    outcomes are retained for decision auditing but not counted as realized PnL.
    The ``purged`` part lists every eligible record left out (embargo, immature label,
    late record or outside the windows), so nothing disappears silently.
    """
    if (not all(_valid_time(x) for x in (train_end, calibration_end, evaluation_end))
            or not train_end < calibration_end < evaluation_end
            or type(embargo_seconds) is not int or embargo_seconds < 0):
        raise ValueError("invalid chronological boundaries")
    parts: dict[str, list[ExpertDecision]] = {"train": [], "calibration": [], "evaluation": [], "purged": []}
    for d in dataset.eligible:
        if d.decision_at <= train_end:
            name, end = "train", train_end
        elif train_end + embargo_seconds < d.decision_at <= calibration_end:
            name, end = "calibration", calibration_end
        elif calibration_end + embargo_seconds < d.decision_at <= evaluation_end:
            name, end = "evaluation", evaluation_end
        else:
            parts["purged"].append(d)
            continue
        if d.recorded_at > end or (d.label_available_at is not None and d.label_available_at > end):
            parts["purged"].append(d)
            continue
        parts[name].append(d)
    return {k: tuple(v) for k, v in parts.items()}
