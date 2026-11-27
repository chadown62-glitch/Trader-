"""Préparation de la consolidation : douze agents rangés en quatre rôles, sans rien changer aux décisions ni aux protections."""

import pytest

from sentinelle.fleet import agents as A, coordinator as K, roles as R

T0 = 1_800_000_000_000
CLEAN = {"allowed": True, "vetoes": []}


def candidate(**kw):
    data = {"asset_domain": "memecoin", "observed_ms": T0, "price": 1.0, "signals": ["momentum"], "guardian": CLEAN,
            "round_trip_loss": 0.02, "liquidity_usd": 50_000.0, "notional_usd": 3.0, "impact": 3.0 / 25_003.0,
            "round_trip_cost": 0.008, "open_positions": 0, "equity_usd": 30.0, "initial_usd": 30.0} | kw
    return A.Event("memecoin_candidate", "MINT", T0 + 1_000, data)


def test_every_agent_has_exactly_one_of_four_roles_and_protections_stay_essential():
    assert R.check(A.fleet()) == []
    assert len(R.ROLES) == 4 and sum(len(v) for v in R.ROLES.values()) == len(A.fleet()) == 12
    assert {R.ROLE_OF[n] for n in ("Rug Pull Guardian", "Risk Commander", "Exit Guardian")} == {"garde"}
    assert set(R.PROTECTIONS) <= K.ESSENTIAL  # le coordinateur ne saute ni ne désactive jamais une protection
    broken = A.fleet()[:-1]
    assert R.check(broken) == ["nom inconnu dans les rôles : Challenger Lab"]


@pytest.mark.parametrize(("change", "decision", "role"), [
    ({}, K.PROPOSAL, None),
    ({"guardian": {"allowed": False, "vetoes": ["Mutable metadata"]}}, K.VETOED, "garde"),
    ({"open_positions": 12}, K.REFUSED, "garde"),
    ({"signals": []}, K.WAIT, None),  # sans signal, attendre ne dépend d'aucun constat isolé : aucun rôle décisif
    ({"impact": 0.05}, K.WAIT, "execution"),
    ({"observed_ms": T0 - 3_600_000}, K.ABSTENTION, "veille"),
])
def test_the_view_by_role_never_changes_the_decision_and_names_the_decisive_role(change, decision, role):
    k = K.Coordinator()
    record = k.decide(candidate(**change), {})
    view = R.by_role(record)
    assert view["decision"] == record["decision"] == decision
    if role:
        assert role in view["roles_decisifs"]
    assert "signal" in view["par_role"] and "garde" in view["par_role"]
    assert sum(len(v) for v in view["par_role"].values()) == len(record["findings"])
    report = R.role_report(k.stats)
    assert set(report) == set(R.ROLES) and report["garde"]["executions"] >= 2
