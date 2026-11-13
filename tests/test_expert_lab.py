"""Offline, no-network causal/provenance tests for Expert Strategy Lab (v0 + v1 audit, mission #38)."""
import pytest

from sentinelle.ai.expert_lab import (ExpertDecision, ProvenanceAudit, admission, build_dataset,
                                     chronological_partitions, visible_snapshot)

T = 1_750_000_000
H = "a" * 64
AUDIT = ProvenanceAudit(source_id="licensed-ledger", auditor="operateur-test", audited_at=T - 10,
                        level="verified_fill", evidence_sha256=H, rights_sha256="b" * 64)
AUDITS = {"licensed-ledger": AUDIT}


def sample(**updates):
    base = dict(sample_id="expert-001", source_id="licensed-ledger", symbol="BTC/EUR",
                action="enter", decision_at=T, features_available_at=T-1,
                recorded_at=T+1, source_uri="https://example.org/ledger/entry-1",
                rights_evidence_uri="https://example.org/licenses/grant-1",
                expert_evidence_uri="https://example.org/audit/verified-1",
                rights_verified=True, source_verified=True,
                label_kind="documented_fill", label_available_at=T+50,
                net_r=-0.4, fill_evidence_uri="https://example.org/ledger/fill-1",
                fill_at=T+2, fill_price=100.0, exit_at=T+40, exit_price=99.0, costs_r=0.05)
    base.update(updates)
    return ExpertDecision(**base)


UNLABELED = dict(label_kind=None, label_available_at=None, net_r=None, fill_evidence_uri=None,
                 fill_at=None, fill_price=None, exit_at=None, exit_price=None, costs_r=None)


def test_documented_losses_are_eligible_not_only_winners():
    record = sample()
    assert admission(record, T+100, AUDIT).accepted
    assert build_dataset([record], cutoff=T+100, audits=AUDITS).eligible == (record,)


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
    assert admission(sample(**changes), T+1000, AUDIT).reason == expected


def test_late_label_is_not_mature_at_cutoff():
    record = sample(label_available_at=T+500)
    assert admission(record, T+100, AUDIT).reason == "label_not_yet_available"
    assert not build_dataset([record], cutoff=T+100, audits=AUDITS).eligible


def test_snapshot_does_not_reveal_future_result():
    record = sample(label_available_at=T+500)
    dataset = build_dataset([record], cutoff=T+600, audits=AUDITS)
    prior = visible_snapshot(dataset, T+100)[0]
    assert prior.net_r is None and prior.label_kind is None and prior.fill_evidence_uri is None
    assert prior.fill_price is None and prior.exit_price is None and prior.costs_r is None
    after = visible_snapshot(dataset, T+600)[0]
    assert after.net_r == -0.4


def test_not_yet_recorded_decisions_are_invisible():
    d = sample(recorded_at=T+50, label_available_at=T+60)
    dataset = build_dataset([d], cutoff=T+600, audits=AUDITS)
    assert visible_snapshot(dataset, T+5) == ()


def test_conflicting_duplicates_are_all_quarantined_whatever_the_order():
    first, second = sample(), sample(net_r=8)
    for rows in ([first, second], [second, first]):
        dataset = build_dataset(rows, cutoff=T+100, audits=AUDITS)
        assert dataset.eligible == ()
        assert dataset.summary()["exclusion_reasons"] == {"conflicting_duplicate_sample_id": 2}
        assert dataset.summary()["can_trade"] is False


def test_identical_copies_keep_one():
    dataset = build_dataset([sample(), sample()], cutoff=T+100, audits=AUDITS)
    assert len(dataset.eligible) == 1 and dataset.summary()["exclusion_reasons"] == {"duplicate_sample_id": 1}


def test_chronological_purge_at_label_boundary():
    fill = dict(fill_at=None, exit_at=None)
    rows = [sample(sample_id="train", decision_at=T-300, features_available_at=T-301,
                   recorded_at=T-299, label_available_at=T+2, fill_at=T-299, exit_at=T),
            sample(sample_id="cal", decision_at=T+110, features_available_at=T+109,
                   recorded_at=T+111, label_available_at=T+120, fill_at=T+111, exit_at=T+119),
            sample(sample_id="test", decision_at=T+225, features_available_at=T+224,
                   recorded_at=T+226, label_available_at=T+230, fill_at=T+226, exit_at=T+229),
            sample(sample_id="boundary", decision_at=T+15, features_available_at=T+14,
                   recorded_at=T+16, label_available_at=T+25, fill_at=T+16, exit_at=T+24)]
    assert fill  # (les fills restent horodatés dans chaque ligne)
    ds = build_dataset(rows, cutoff=T+1000, audits=AUDITS)
    parts = chronological_partitions(ds, train_end=T, calibration_end=T+200,
                                     evaluation_end=T+400, embargo_seconds=20)
    assert parts["train"] == ()  # train outcome matured only after train cutoff
    assert [d.sample_id for d in parts["calibration"]] == ["cal"]
    assert [d.sample_id for d in parts["evaluation"]] == ["test"]
    assert sorted(d.sample_id for d in parts["purged"]) == ["boundary", "train"]  # rien ne disparaît en silence
    ids = [x.sample_id for part in parts.values() for x in part]
    assert len(ids) == len(set(ids)) == len(ds.eligible)


def test_empty_or_unlabeled_abstention_retained_without_inventing_profit():
    record = sample(action="skip", **UNLABELED)
    ds = build_dataset([record], cutoff=T+100, audits=AUDITS)
    assert len(ds.eligible) == 1 and ds.eligible[0].net_r is None
    assert ds.summary()["claim"] == "research_only_no_profit_evidence"


def test_invalid_boundaries_fail_closed():
    ds = build_dataset([sample()], cutoff=T+100, audits=AUDITS)
    with pytest.raises(ValueError):
        chronological_partitions(ds, train_end=T+50, calibration_end=T+40,
                                 evaluation_end=T+100, embargo_seconds=1)


# ---------------------------------------------------------------------------
# v1 : défauts trouvés à l'audit de #37 (chaque test échouait sur la v0)
# ---------------------------------------------------------------------------


def test_decision_recorded_after_its_outcome_is_refused():
    record = sample(recorded_at=T+100, label_available_at=T+50, fill_at=T+1, exit_at=T+40)
    assert admission(record, T+1000, AUDIT).reason == "recorded_after_outcome"


@pytest.mark.parametrize("field", ["label_available_at", "decision_at", "fill_at"])
def test_milliseconds_are_refused_not_mixed_with_seconds(field):
    record = sample(**{field: getattr(sample(), field) * 1000})
    assert admission(record, 10**11 - 1, AUDIT).reason == "invalid_timestamp"


def test_same_decision_under_two_identifiers_counts_once():
    ds = build_dataset([sample(sample_id="b"), sample(sample_id="a")], cutoff=T+100, audits=AUDITS)
    assert [d.sample_id for d in ds.eligible] == ["a"]
    assert ds.summary()["exclusion_reasons"] == {"duplicate_decision_content": 1}


@pytest.mark.parametrize("missing", ["fill_at", "fill_price", "exit_at", "exit_price", "costs_r"])
def test_profit_label_without_timestamped_fill_price_and_costs_is_refused(missing):
    assert admission(sample(**{missing: None}), T+100, AUDIT).reason == "realized_return_without_timestamped_fill_price_costs"


@pytest.mark.parametrize("changes", [dict(fill_at=T-5), dict(exit_at=T+60), dict(fill_price=0.0),
                                     dict(costs_r=-0.1), dict(costs_r=float("inf")), dict(exit_at=T+1)])
def test_incoherent_fill_chronology_or_values_are_refused(changes):
    assert not admission(sample(**changes), T+100, AUDIT).accepted


def test_self_declared_verified_flags_never_replace_an_audit():
    record = sample(source_verified=True, rights_verified=True)
    assert admission(record, T+100, None).reason == "provenance_audit_missing"
    assert not build_dataset([record], cutoff=T+100).eligible  # aucun audit fourni
    weak = ProvenanceAudit(**{**AUDIT.__dict__, "level": "timestamped_decision"})
    assert admission(record, T+100, weak).reason == "evidence_level_insufficient"  # gain réalisé : fill vérifié exigé
    assert admission(sample(**UNLABELED), T+100, weak).accepted  # simple décision : niveau suffisant
    late = ProvenanceAudit(**{**AUDIT.__dict__, "audited_at": T+500})
    assert admission(record, T+100, late).reason == "provenance_audit_after_cutoff"
    forged = ProvenanceAudit(**{**AUDIT.__dict__, "evidence_sha256": "pas-une-empreinte"})
    assert admission(record, T+100, forged).reason == "provenance_audit_invalid"
    other = ProvenanceAudit(**{**AUDIT.__dict__, "source_id": "autre-source"})
    assert admission(record, T+100, other).reason == "provenance_audit_missing"


def test_late_feature_receipt_is_a_leak():
    record = sample(features_available_at=T-100, features_received_at=T+30, recorded_at=T+1)
    assert admission(record, T+100, AUDIT).reason == "late_feature_receipt"
    assert admission(sample(features_received_at=T-1), T+100, AUDIT).accepted


def test_retrospective_revision_is_refused_and_timely_revision_is_visible_only_once_recorded():
    root = sample(sample_id="root", **UNLABELED)
    late = sample(sample_id="late", revision_of="root", recorded_at=T+500, action="skip", **UNLABELED)
    assert admission(late, T+1000, AUDIT).reason == "retrospective_revision"
    timely = sample(sample_id="rev", revision_of="root", recorded_at=T+60, action="skip", **UNLABELED)
    ds = build_dataset([root, timely, late], cutoff=T+1000, audits=AUDITS)
    assert [d.sample_id for d in ds.eligible] == ["rev"] and [d.sample_id for d in ds.superseded] == ["root"]
    assert ds.summary()["exclusion_reasons"]["retrospective_revision"] == 1
    assert visible_snapshot(ds, T+10)[0].action == "enter"  # la révision n'existait pas encore
    assert visible_snapshot(ds, T+100)[0].action == "skip"


def test_revision_chains_keep_only_the_latest_timely_version():
    root = sample(sample_id="root", **UNLABELED)
    r1 = sample(sample_id="r1", revision_of="root", recorded_at=T+20, action="skip", **UNLABELED)
    r2 = sample(sample_id="r2", revision_of="root", recorded_at=T+40, action="enter", **UNLABELED)
    bad = sample(sample_id="bad", revision_of="r1", recorded_at=T+50, **UNLABELED)  # révision d'une révision
    ds = build_dataset([r2, bad, root, r1], cutoff=T+1000, audits=AUDITS)
    assert [d.sample_id for d in ds.eligible] == ["r2"]
    assert visible_snapshot(ds, T+30)[0].sample_id == "r1"
    assert ds.summary()["exclusion_reasons"]["invalid_revision"] == 1


def test_build_dataset_is_independent_of_input_order():
    rows = [sample(sample_id=f"s{i}", decision_at=T + 200 * i, features_available_at=T + 200 * i - 1,
                   recorded_at=T + 200 * i + 1, label_available_at=T + 200 * i + 50,
                   fill_at=T + 200 * i + 2, exit_at=T + 200 * i + 40) for i in range(6)]
    rows += [sample(sample_id="s2", net_r=9.0, decision_at=T + 400, features_available_at=T + 399, recorded_at=T + 401,
                    label_available_at=T + 450, fill_at=T + 402, exit_at=T + 440)]
    a = build_dataset(rows, cutoff=T + 5000, audits=AUDITS)
    b = build_dataset(list(reversed(rows)), cutoff=T + 5000, audits=AUDITS)
    assert a == b and "s2" not in [d.sample_id for d in a.eligible]
