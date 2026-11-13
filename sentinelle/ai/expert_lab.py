"""Offline expert-decision evidence gate. No orders, model promotion or network.

Expert decisions remain research *candidates*, never certified profitable traders.
Every admitted feature was available at the decision; every attributed outcome
requires a documented fill and a label mature at the requested cut-off.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from typing import Iterable

ACTIONS = frozenset({"enter", "skip", "exit"})
LABEL_KINDS = frozenset({"documented_fill", "paper_fill", "hypothetical"})
TIMELY_RECORD_DELAY_SECONDS = 120


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


@dataclass(frozen=True)
class Admission:
    sample_id: str
    accepted: bool
    reason: str


@dataclass(frozen=True)
class Dataset:
    eligible: tuple[ExpertDecision, ...]
    excluded: tuple[Admission, ...]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for entry in self.excluded:
            counts[entry.reason] = counts.get(entry.reason, 0) + 1
        return {
            "eligible": len(self.eligible),
            "excluded": len(self.excluded),
            "exclusion_reasons": dict(sorted(counts.items())),
            "claim": "research_only_no_profit_evidence",
            "can_trade": False,
        }


def _valid_time(value: object) -> bool:
    return type(value) is int and 0 < value < 10**13


def _valid_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith("https://") and len(value) > 8


def admission(decision: ExpertDecision, cutoff: int) -> Admission:
    """Gate for inclusion in a *documented* supervised teaching dataset.

    A data provider's self-asserted 'verified' field is not sufficient proof;
    evidence references and independent source/rights reviews are both required.
    The caller must archive evidence and inspect authenticity outside this gate.
    """
    identifier = decision.sample_id if isinstance(decision.sample_id, str) else "<invalid>"

    def deny(reason: str) -> Admission:
        return Admission(identifier, False, reason)

    if not all(isinstance(v, str) and v.strip() for v in (decision.sample_id, decision.source_id, decision.symbol)):
        return deny("missing_identity")
    if decision.action not in ACTIONS:
        return deny("unsupported_action")
    if not _valid_time(cutoff) or not all(_valid_time(t) for t in
            (decision.decision_at, decision.features_available_at, decision.recorded_at)):
        return deny("invalid_timestamp")
    if not (decision.features_available_at <= decision.decision_at
            <= decision.recorded_at <= decision.decision_at + TIMELY_RECORD_DELAY_SECONDS):
        return deny("lookahead_or_retrospective_record")
    if decision.recorded_at > cutoff:
        return deny("decision_not_yet_recorded")
    if not all(_valid_reference(s) for s in
            (decision.source_uri, decision.rights_evidence_uri, decision.expert_evidence_uri)):
        return deny("missing_provenance")
    if not decision.rights_verified:
        return deny("rights_unverified")
    if not decision.source_verified:
        return deny("expert_claim_unverified")
    if decision.label_kind is None:
        if decision.label_available_at is not None or decision.net_r is not None or decision.fill_evidence_uri:
            return deny("incomplete_label")
        return Admission(identifier, True, "unlabeled_decision")
    if decision.label_kind not in LABEL_KINDS:
        return deny("unknown_label_kind")
    if not _valid_time(decision.label_available_at) or decision.label_available_at <= decision.decision_at:
        return deny("invalid_label_time")
    if decision.label_available_at > cutoff:
        return deny("label_not_yet_available")
    if decision.net_r is not None and (isinstance(decision.net_r, bool) or not isinstance(decision.net_r, (int, float))
                                       or not isfinite(decision.net_r)):
        return deny("invalid_return")
    if decision.label_kind == "hypothetical":
        if decision.net_r is not None or decision.fill_evidence_uri:
            return deny("hypothetical_is_not_realized_pnl")
        return Admission(identifier, True, "hypothetical_unscored")
    if decision.action == "skip":
        return deny("skipped_trade_has_no_realized_fill")
    if decision.net_r is None or not _valid_reference(decision.fill_evidence_uri):
        return deny("realized_return_without_fill_proof")
    return Admission(identifier, True, "documented_research_sample")


def build_dataset(rows: Iterable[ExpertDecision], *, cutoff: int) -> Dataset:
    """Deterministic audit of uniqueness and eligible records. Never fetches data."""
    accepted: list[ExpertDecision] = []
    excluded: list[Admission] = []
    seen: set[str] = set()
    for row in rows:
        if row.sample_id in seen:
            excluded.append(Admission(row.sample_id, False, "duplicate_sample_id"))
            continue
        seen.add(row.sample_id)
        verdict = admission(row, cutoff)
        if verdict.accepted:
            accepted.append(row)
        else:
            excluded.append(verdict)
    accepted.sort(key=lambda d: (d.decision_at, d.sample_id))
    return Dataset(tuple(accepted), tuple(excluded))


def visible_snapshot(dataset: Dataset, as_of: int) -> tuple[ExpertDecision, ...]:
    """Past facts only. A previously admitted label cannot appear prematurely."""
    if not _valid_time(as_of):
        raise ValueError("invalid as_of")
    output = []
    for record in dataset.eligible:
        if record.recorded_at > as_of:
            continue
        if record.label_available_at is not None and record.label_available_at > as_of:
            output.append(replace(record, label_kind=None, label_available_at=None,
                                  net_r=None, fill_evidence_uri=None))
        else:
            output.append(record)
    return tuple(output)


def chronological_partitions(dataset: Dataset, *, train_end: int, calibration_end: int,
                             evaluation_end: int, embargo_seconds: int) -> dict[str, tuple[ExpertDecision, ...]]:
    """Disjoint time windows; training/calibration labels must mature by window end.

    Evaluation is protected from being reused as training. Skips and hypothetical
    outcomes are retained for decision auditing but not counted as realized PnL.
    """
    if (not all(_valid_time(x) for x in (train_end, calibration_end, evaluation_end))
            or not train_end < calibration_end < evaluation_end
            or type(embargo_seconds) is not int or embargo_seconds < 0):
        raise ValueError("invalid chronological boundaries")
    parts: dict[str, list[ExpertDecision]] = {"train": [], "calibration": [], "evaluation": []}
    for d in dataset.eligible:
        if d.decision_at <= train_end:
            name, end = "train", train_end
        elif d.decision_at > train_end + embargo_seconds and d.decision_at <= calibration_end:
            name, end = "calibration", calibration_end
        elif d.decision_at > calibration_end + embargo_seconds and d.decision_at <= evaluation_end:
            name, end = "evaluation", evaluation_end
        else:
            continue
        if d.recorded_at > end or (d.label_available_at is not None and d.label_available_at > end):
            continue
        parts[name].append(d)
    return {k: tuple(v) for k, v in parts.items()}
