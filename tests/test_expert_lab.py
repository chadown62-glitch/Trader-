"""Offline, no-network causal/provenance tests for Expert Strategy Lab v0."""
import pytest

from sentinelle.ai.expert_lab import (ExpertDecision, admission, build_dataset,
                                     chronological_partitions, visible_snapshot)

T = 1_750_000_000


def sample(**updates):
    base = dict(sample_id="expert-001", source_id="licensed-ledger", symbol="BTC/EUR",
                action="enter", decision_at=T, features_available_at=T-1,
                recorded_at=T+1, source_uri="https://example.org/ledger/entry-1",
                rights_evidence_uri="https://example.org/licenses/grant-1",
                expert_evidence_uri="https://example.org/audit/verified-1",
                rights_verified=True, source_verified=True,
                label_kind="documented_fill", label_available_at=T+50,
                net_r=-0.4, fill_evidence_uri="https://example.org/ledger/fill-1")
    base.update(updates)
    return ExpertDecision(**base)


def test_documented_losses_are_eligible_not_only_winners():
    record = sample()
    assert admission(record, T+100).accepted
    assert build_dataset([record], cutoff=T+100).eligible == (record,)


@pytest.mark.parametrize("changes,expected", [
    ({"features_available_at": T+10}, "lookahead_or_retrospective_record"),
    ({"recorded_at": T+900}, "lookahead_or_retrospective_record"),
    ({"rights_verified": False}, "rights_unverified"),
    ({"source_verified": False}, "expert_claim_unverified"),
    ({"rights_evidence_uri": ""}, "missing_provenance"),
    ({"expert_evidence_uri": ""}, "missing_provenance"),
    ({"label_kind": "documented_fill", "fill_evidence_uri": None}, "realized_return_without_fill_proof"),
    ({"label_available_at": T-100}, "invalid_label_time"),
    ({"action": "skip"}, "skipped_trade_has_no_realized_fill"),
    ({"net_r": float("nan")}, "invalid_return"),
    ({"label_kind": "hypothetical"}, "hypothetical_is_not_realized_pnl"),
])
def test_reject_noncausal_or_unverified_claims(changes, expected):
    assert admission(sample(**changes), T+1000).reason == expected


def test_late_label_is_not_mature_at_cutoff():
    record = sample(label_available_at=T+500)
    assert admission(record, T+100).reason == "label_not_yet_available"
    assert not build_dataset([record], cutoff=T+100).eligible


def test_snapshot_does_not_reveal_future_result():
    record = sample(label_available_at=T+500)
    dataset = build_dataset([record], cutoff=T+600)
    prior = visible_snapshot(dataset, T+100)[0]
    assert prior.net_r is None and prior.label_kind is None and prior.fill_evidence_uri is None
    after = visible_snapshot(dataset, T+600)[0]
    assert after.net_r == -0.4


def test_not_yet_recorded_decisions_are_invisible():
    d = sample(recorded_at=T+50)
    dataset = build_dataset([d], cutoff=T+600)
    assert visible_snapshot(dataset, T+5) == ()


def test_duplicate_identifier_is_rejected_without_overwriting():
    first, second = sample(), sample(net_r=8)
    dataset = build_dataset([first, second], cutoff=T+100)
    assert dataset.eligible == (first,)
    assert dataset.summary()["exclusion_reasons"] == {"duplicate_sample_id": 1}
    assert dataset.summary()["can_trade"] is False


def test_chronological_purge_at_label_boundary():
    rows = [sample(sample_id="train", decision_at=T-300, features_available_at=T-301,
                   recorded_at=T-299, label_available_at=T+2),
            sample(sample_id="cal", decision_at=T+110, features_available_at=T+109,
                   recorded_at=T+111, label_available_at=T+120),
            sample(sample_id="test", decision_at=T+225, features_available_at=T+224,
                   recorded_at=T+226, label_available_at=T+230),
            sample(sample_id="boundary", decision_at=T+15, features_available_at=T+14,
                   recorded_at=T+16, label_available_at=T+25)]
    ds = build_dataset(rows, cutoff=T+1000)
    parts = chronological_partitions(ds, train_end=T, calibration_end=T+200,
                                     evaluation_end=T+400, embargo_seconds=20)
    assert parts["train"] == ()  # train outcome matured only after train cutoff
    assert [d.sample_id for d in parts["calibration"]] == ["cal"]
    assert [d.sample_id for d in parts["evaluation"]] == ["test"]
    ids = [x.sample_id for part in parts.values() for x in part]
    assert len(ids) == len(set(ids))


def test_empty_or_unlabeled_abstention_retained_without_inventing_profit():
    record = sample(action="skip", label_kind=None, label_available_at=None,
                    net_r=None, fill_evidence_uri=None)
    ds = build_dataset([record], cutoff=T+100)
    assert len(ds.eligible) == 1 and ds.eligible[0].net_r is None
    assert ds.summary()["claim"] == "research_only_no_profit_evidence"


def test_invalid_boundaries_fail_closed():
    ds = build_dataset([sample()], cutoff=T+100)
    with pytest.raises(ValueError):
        chronological_partitions(ds, train_end=T+50, calibration_end=T+40,
                                 evaluation_end=T+100, embargo_seconds=1)
