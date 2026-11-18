"""ALPHA FACTORY — cœur causal : indicateurs, enseignants, exécution, situations, catalogue (hors ligne)."""
import json

import numpy as np
import pytest

from sentinelle.ai.expert_lab import ExpertDecision, ProvenanceAudit, build_dataset
from sentinelle.alpha.audit import prefix_violations
from sentinelle.alpha.bars import FEATURES, Bars, feature_row, indicators
from sentinelle.alpha.catalogue import load_catalogue, parse_catalogue
from sentinelle.alpha.dataset import SPLITS, build_situations, split, time_windows
from sentinelle.alpha.execution import CostModel, Plan, PortfolioRules, run_portfolio, simulate_trade
from sentinelle.alpha.markets import leaky_feature, macro_view, synthetic_macro_calendar, synthetic_universe
from sentinelle.alpha.teachers import TEACHERS, abstention_mask

U = synthetic_universe(7, n_bars=1500)
B = U.bars["AAA/EUR"]
IND = indicators(B)
CHECK = list(range(205, 1500, 97))


# ---------------------------------------------------------------------------
# Causalité : famille « caractéristiques »
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [f for f in IND.__dataclass_fields__])
def test_every_indicator_is_prefix_invariant(name):
    assert prefix_violations(lambda b: getattr(indicators(b), name), B, CHECK) == []


@pytest.mark.parametrize("k", range(len(FEATURES)))
def test_every_model_feature_is_prefix_invariant(k):
    def fn(b):
        ind = indicators(b)
        return np.array([feature_row(b, ind, i)[k] for i in range(len(b))])
    assert prefix_violations(fn, B, CHECK[:6]) == []


def test_leaky_feature_is_caught_by_the_audit():
    def leaky(b):
        return leaky_feature(type(U)({b.symbol: b}), b.symbol)
    assert prefix_violations(leaky, B, CHECK) == CHECK  # chaque point de contrôle est signalé


@pytest.mark.parametrize("t", TEACHERS, ids=lambda t: t.name)
def test_teacher_decisions_never_change_when_the_future_is_removed(t):
    veto, why = abstention_mask(B, IND)
    full = [(d.index, d.action, round(d.plan.stop_price, 9)) for d in t.decisions(B, IND, veto, why)]
    for cut in (700, 1100):
        part = B.prefix(cut)
        pind = indicators(part)
        pv, pw = abstention_mask(part, pind)
        prefix = [(d.index, d.action, round(d.plan.stop_price, 9)) for d in t.decisions(part, pind, pv, pw)]
        assert prefix == [x for x in full if x[0] < cut]


def test_teacher_sequences_are_interpretable():
    veto, why = abstention_mask(B, IND)
    for t in TEACHERS:
        ds = t.decisions(B, IND, veto, why)
        assert ds, t.name
        for d in ds[:5]:
            assert d.action in ("enter", "skip") and d.reasons and d.invalidation and d.sources
            assert d.plan.stop_price < B.close[d.index] and d.plan.max_bars > 0
            assert d.entry_zone[0] < B.close[d.index] < d.entry_zone[1]
            assert d.decided_at == B.close_time(d.index)
            if d.action == "skip":
                assert any(r.startswith("ABSTENTION") for r in d.reasons)


# ---------------------------------------------------------------------------
# Exécution : contrat du projet
# ---------------------------------------------------------------------------


def toy(rows, spread=None):
    a = np.array(rows, dtype=float)
    ts = np.arange(len(a), dtype=np.int64) * 14_400_000 + 1_700_000_000_000
    return Bars("TOY/EUR", ts, a[:, 0], a[:, 1], a[:, 2], a[:, 3], np.ones(len(a)), None if spread is None else np.full(len(a), spread))


ZERO = CostModel(fee_rate=0.0, slippage_bps=0.0, default_spread_bps=0.0)


def test_entry_at_next_open_and_label_available_only_after_exit():
    b = toy([[100, 101, 99, 100], [100, 103, 99.5, 102], [102, 106, 101, 105], [105, 106, 104, 105]])
    out = simulate_trade(b, indicators(b), 0, Plan("t", 95.0, 10, target_price=104.0), ZERO)
    assert out.entry_price == 100 and out.exit_reason == "objectif" and out.exit_price == 104.0
    assert out.available_at == b.close_time(2) > out.decided_at == b.close_time(0)


def test_stop_has_priority_and_gaps_fill_at_the_worse_price():
    b = toy([[100, 101, 99, 100], [100, 110, 94, 101], [101, 102, 100, 101]])
    out = simulate_trade(b, indicators(b), 0, Plan("t", 95.0, 10, target_price=105.0), ZERO)
    assert out.exit_reason == "stop" and out.exit_price == 95.0  # stop et objectif dans la même bougie : stop
    g = toy([[100, 101, 99, 100], [100, 101, 99, 100], [90, 91, 88, 89]])
    gap = simulate_trade(g, indicators(g), 0, Plan("t", 95.0, 10), ZERO)
    assert gap.exit_price == 90.0 and gap.net_r < -1  # trou de cotation : pire que le stop


def test_entry_abandoned_when_first_price_is_below_stop():
    b = toy([[100, 101, 99, 100], [94, 95, 93, 94], [94, 95, 93, 94]])
    out = simulate_trade(b, indicators(b), 0, Plan("t", 95.0, 10), ZERO)
    assert out.status == "abandoned" and out.net_r == 0.0


def test_costs_are_separated_and_consistent():
    b = toy([[100, 101, 99, 100], [100, 103, 99.5, 102], [102, 106, 101, 105], [105, 106, 104, 105]], spread=10)
    cost = CostModel(fee_rate=0.001, slippage_bps=5)
    out = simulate_trade(b, indicators(b), 0, Plan("t", 95.0, 1), cost)
    assert out.exit_reason == "durée maximale"
    assert out.cost_r > 0 and abs(out.gross_r - out.cost_r - out.net_r) < 1e-12
    assert out.entry_price > 100  # demi-écart + glissement


def test_unresolved_when_the_data_ends():
    b = toy([[100, 101, 99, 100], [100, 101, 99.5, 100]])
    assert simulate_trade(b, indicators(b), 0, Plan("t", 95.0, 10), ZERO).status == "unresolved"
    assert simulate_trade(b, indicators(b), 1, Plan("t", 95.0, 10), ZERO).status == "unresolved"


def test_portfolio_of_30_eur_respects_minimum_cash_and_limits():
    s = build_situations(U)
    intents = [(s.symbol[k], int(s.index[k]), s.decisions[k].plan, s.outcomes[k]) for k in range(len(s)) if not s.vetoed[k]]
    res = run_portfolio(U, intents, start=200, end=U.length, rules=PortfolioRules())
    assert res.initial == 30.0 and res.fees > 0
    assert all(t["notional"] >= 5.0 - 1e-9 for t in res.trades)
    assert set(res.refused) <= {"position_déjà_ouverte", "trop_de_positions", "sous_le_minimum_risque_trop_grand", "liquidités_insuffisantes"}
    assert min(v for _, v in res.equity) > 0  # aucun levier, aucun solde négatif


# ---------------------------------------------------------------------------
# Situations : étiquettes, purge, embargo (famille « labels »)
# ---------------------------------------------------------------------------


def test_labels_become_available_strictly_after_the_decision():
    s = build_situations(U)
    ok = np.isfinite(s.available_at)
    assert (s.available_at[ok] > s.decided_at[ok]).all()


def test_splits_are_disjoint_purged_and_embargoed():
    s = build_situations(U)
    w = time_windows(U)
    masks, purged = split(s, w)
    total = sum(m.astype(int) for m in masks.values()) + purged.astype(int)
    assert (total == 1).all()
    for k, name in enumerate(SPLITS):
        lo, hi = w.bounds[name]
        m = masks[name]
        assert (s.available_at[m] <= hi).all()  # issue connue avant la fin de la fenêtre
        if k:
            assert (s.decided_at[m] > lo + w.embargo_ms - 1).all()


def test_situations_are_deterministic():
    a, b = build_situations(synthetic_universe(9, n_bars=900)), build_situations(synthetic_universe(9, n_bars=900))
    assert np.array_equal(a.decided_at, b.decided_at) and np.allclose(a.net_r["teacher"], b.net_r["teacher"], equal_nan=True)


def test_hidden_truth_is_not_part_of_the_learning_inputs():
    s = build_situations(U)
    assert s.X.shape[1] == len(FEATURES)
    assert U.prefix(100).truth == {}


# ---------------------------------------------------------------------------
# Actualités révisées (famille « news »)
# ---------------------------------------------------------------------------


def test_macro_revisions_are_invisible_before_their_publication():
    events = synthetic_macro_calendar(U, 7)
    first = next(e for e in events if e.version == 1)
    revision = next(e for e in events if e.event_id == first.event_id and e.version == 2)
    assert first.available_at < revision.available_at
    assert macro_view(events, first.available_at - 1)[first.event_id].actual is None  # calendrier seul
    assert macro_view(events, first.available_at)[first.event_id].actual == first.actual
    assert macro_view(events, revision.available_at - 1)[first.event_id].version == 1
    assert macro_view(events, revision.available_at)[first.event_id].actual == revision.actual


# ---------------------------------------------------------------------------
# Catalogue et sources trompeuses (famille « source expert »)
# ---------------------------------------------------------------------------


def test_catalogue_is_versioned_and_every_teacher_has_documented_sources():
    cat = load_catalogue()
    assert cat.version == "1.0.0" and len(cat.sha256) == 64
    for t in TEACHERS:
        assert t.sources and all(cat.can_teach_rules(sid) for sid in t.sources), t.name
    assert not cat.can_teach_rules("turtle-performance-claims")
    assert not cat.can_teach_rules("marketing-clip-trader")


def test_no_catalogue_source_can_label_profits_without_verified_fills_and_audit():
    cat = load_catalogue()
    audit = ProvenanceAudit("tsmom-moskowitz-2012", "auditeur", 1_700_000_000, "verified_fill", "a" * 64, "b" * 64)
    for sid in cat.sources:
        ok, why = cat.can_label_profit(sid, audit, cutoff=1_800_000_000)
        assert not ok and why  # aucune source v1 n'a de fills vérifiés : AUCUNE étiquette de gain


@pytest.mark.parametrize("bad", [{"verified": True}, {"performance": "+400 %"}, {"win_rate": 0.9}])
def test_misleading_catalogue_entries_are_refused(bad):
    data = json.loads(load_catalogue.__globals__["CATALOGUE_PATH"].read_text(encoding="utf-8"))
    data["entries"][0].update(bad)
    with pytest.raises(ValueError):
        parse_catalogue(data)


def test_claimed_results_cannot_teach_even_if_listed_as_used():
    data = json.loads(load_catalogue.__globals__["CATALOGUE_PATH"].read_text(encoding="utf-8"))
    claim = next(e for e in data["entries"] if e["level"] == "claimed_result")
    claim["used_by"] = ["trend"]
    with pytest.raises(ValueError):
        parse_catalogue(data)


def test_survivorship_trap_winners_only_expert_feed_is_rejected():
    """Un flux qui ne montre que des gains, auto-déclaré « vérifié », sans audit ni fills : rien n'est admis."""
    s = build_situations(U)
    winners = [k for k in range(len(s)) if np.isfinite(s.net_r["teacher"][k]) and s.net_r["teacher"][k] > 0][:30]
    rows = []
    for k in winners:
        t = int(s.decided_at[k] // 1000)
        rows.append(ExpertDecision(f"clip-{k}", "marketing-clip-trader", s.symbol[k], "enter", t, t - 1, t + 1,
                                   "https://example.org/clip", "https://example.org/rights", "https://example.org/proof",
                                   True, True, "documented_fill", t + 3600, float(s.net_r["teacher"][k]), "https://example.org/fill"))
    ds = build_dataset(rows, cutoff=2_000_000_000)
    assert ds.eligible == () and set(ds.summary()["exclusion_reasons"]) <= {"provenance_audit_missing",
                                                                           "realized_return_without_timestamped_fill_price_costs"}
    all_r = s.net_r["teacher"][np.isfinite(s.net_r["teacher"])]
    # biais du survivant : le flux affiche 100 % de gagnants alors que le taux réel est bien plus bas
    assert all(r.net_r > 0 for r in rows) and np.mean(all_r > 0) < 0.6
    assert np.mean([r.net_r for r in rows]) > all_r.mean()


# ---------------------------------------------------------------------------
# Docteur des erreurs
# ---------------------------------------------------------------------------


def test_error_doctor_uses_only_matured_outcomes_and_counts_each_category():
    from sentinelle.alpha.doctor import diagnose

    s = build_situations(U)
    enter = ~s.vetoed
    full = diagnose(s, enter)
    cats = full["categories"]
    assert set(cats) == {"fausse_entrée", "refus_injustifié", "sortie_trop_précoce", "tué_par_les_coûts"}
    assert cats["fausse_entrée"]["n"] > 0 and cats["fausse_entrée"]["mean_net_r"] < 0
    if cats["refus_injustifié"]["n"]:
        assert cats["refus_injustifié"]["mean_net_r"] > 0
    early = diagnose(s, enter, as_of=int(s.decided_at[len(s) // 3]))
    assert early["observations"] < full["observations"]  # rien d'inconnu à l'instant n'est compté
