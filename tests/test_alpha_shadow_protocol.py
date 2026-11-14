"""ALPHA SHADOW — candidat figé, verdicts, puissance, manifeste immuable, isolation (mission #41)."""
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.execution import run_portfolio
from sentinelle.alpha.learners import TakeAll, climate
from sentinelle.alpha.markets import synthetic_universe
from sentinelle.alpha.shadow import analysis, protocol, r1
from sentinelle.alpha.shadow.analysis import DEFAULT_PLAN, build_report, primary_test
from sentinelle.alpha.shadow.contract import ContractError, sha256, validate
from sentinelle.alpha.shadow.engine import Limits, market_sha
from sentinelle.alpha.shadow.ledger import ANCHOR_CONTRACT, line_sha
from sentinelle.alpha.velocity import intents_for, window_indices
from tests.shadow_fixtures import COST, RULES, TF, bars_at, book_at, config, kinds, last_index, market, run, start_engine, warm_index
from tools import alpha_shadow

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / protocol.DIRECTORY
FAST = {"bootstrap_reps": 400}
M = market(7)
P = market(22, "hidden_rule")  # témoin positif : une vraie règle cachée existe dans ce marché


def anchors_for(eng, *, dated=True):
    lines = eng.ledger.lines
    out = []
    for ln in lines:
        if ln["kind"] == "commit":
            a = {"contract": ANCHOR_CONTRACT, "ledger_id": lines[0]["sha"], "seq": ln["seq"], "head_sha256": ln["sha"], "created_at_ms": ln["at"]}
            if dated:
                a["published_at_ms"] = ln["at"] + 60_000  # date donnée par le support externe
            out.append(a)
    return out


def report(eng, m, *, anchors=None, plan=None, now=None, **kw):
    rep = build_report(eng.ledger.items, manifest_sha256="0" * 64, model_sha256=m["model"].sha256, plan={**FAST, **(plan or {})},
                       now_ms=eng.now if now is None else now, anchors=anchors, **kw)
    return validate(rep)


@pytest.fixture(scope="module")
def prospective():
    eng = run(P, config(P, mode="prospective"), books=True)
    return eng, anchors_for(eng)


# ---------------------------------------------------------------------------
# Candidat figé
# ---------------------------------------------------------------------------


def test_frozen_model_decides_exactly_like_the_router_it_was_frozen_from():
    d, model = M["d"], M["model"]
    s = d["situations"]
    enter, _ = d["router"].policy(s)
    c = climate(s)
    mine = np.array([model.takes(s.teacher[k], int(c[k]), bool(s.vetoed[k])) for k in range(len(s))])
    assert len(s) > 1000 and np.array_equal(mine, enter)
    scores = d["router"].score(s)
    assert all(model.score(s.teacher[k], int(c[k])) == scores[k] for k in range(0, len(s), 7))


def test_online_climate_matches_the_development_definition():
    from sentinelle.alpha.bars import indicators
    u, s = M["u"], M["d"]["situations"]
    inds = {sym: indicators(u.bars[sym], COST.default_spread_bps) for sym in u.symbols}
    c = climate(s)
    for k in range(0, len(s), 5):
        assert r1.climate_of(inds[s.symbol[k]], int(s.index[k])) == int(c[k])


def test_a_modified_artifact_is_refused(monkeypatch):
    art = M["d"]["artifact"]
    good = sha256(art)
    assert r1.load_model(art, good).sha256 == good
    tampered = json.loads(json.dumps(art))
    tampered["cells"][0]["sum_net_r"] += 50.0
    with pytest.raises(ContractError, match="MODEL_SHA_MISMATCH"):
        r1.load_model(tampered, good)
    for change in (lambda a: a["hyperparameters"].update(prior=1.0), lambda a: a.update(margin_r="-inf") or a["hyperparameters"].update(min_taken=1),
                   lambda a: a["rules"]["teachers"]["breakout"].update(stop_atr=0.5), lambda a: a.update(kind="autre")):
        other = json.loads(json.dumps(art))
        change(other)
        with pytest.raises(ContractError):  # même avec une empreinte recalculée, des règles changées sont refusées
            r1.load_model(other, sha256(other))
    for bad in ([{"teacher": "inconnu", "climate": 0, "sum_net_r": 1.0, "n": 3}], [{"teacher": "trend", "climate": 9, "sum_net_r": 1.0, "n": 3}],
                [{"teacher": "trend", "climate": 1, "sum_net_r": 1.0, "n": 3}] * 2, [{"teacher": "trend", "climate": 1, "sum_net_r": float("inf"), "n": 3}]):
        other = {**json.loads(json.dumps(art)), "cells": bad}
        with pytest.raises((ContractError, ValueError)):
            r1.load_model(other, sha256(other) if all(np.isfinite(c["sum_net_r"]) for c in bad) else "0" * 64)
    monkeypatch.setattr(r1, "ABSTENTION_SPREAD_BPS", 99.0)
    with pytest.raises(ContractError, match="TEACHER_RULES_CHANGED"):
        r1.load_model(art, good)  # le code des enseignants a changé depuis le gel


def test_unknown_cells_score_zero_and_a_model_that_takes_nothing_stays_in_cash():
    model = M["model"]
    assert model.score("trend", 5) == model.cells.get(("trend", 5), (0.0, 0))[0] / (model.cells.get(("trend", 5), (0.0, 0))[1] + 20.0)
    empty = r1.R1Model({}, 20.0, 0.0, "trend", "0" * 64)
    assert empty.score("breakout", 2) == 0.0 and not empty.takes("breakout", 2, False)
    nothing = market(21, "noise", 0.0)
    assert nothing["model"].takes_nothing
    eng = run(nothing)
    assert eng.arms["r1"].last_value == RULES.capital and not [ln for ln in kinds(eng, "outcome") if ln["body"]["arm"] == "r1"]
    assert {d["body"]["action"] for d in kinds(eng, "decision")} <= {"ABSTAIN", "NO_SIGNAL"}
    assert r1.describe(model)["margin"] == r1.margin_text(model.margin)


# ---------------------------------------------------------------------------
# Règle statistique : une seule comparaison, gain absolu ET relatif
# ---------------------------------------------------------------------------


def series(mean, n=200, seed=1, sd=0.004):
    rng = np.random.default_rng(seed)
    return dict(enumerate(rng.normal(mean, sd, n)))


def test_primary_rule_requires_absolute_and_relative_gain_together():
    plan = {**DEFAULT_PLAN, **FAST}
    good, flat, bad = series(0.004, seed=1), series(0.0, seed=2), series(-0.004, seed=3)
    assert primary_test(good, flat, plan)["outcome"] == "supported"
    cash = {d: 0.0 for d in bad}
    t = primary_test(cash, bad, plan)  # « ne rien faire » bat une référence perdante...
    assert t["relative"]["ci_low"] > 0 and t["outcome"] == "not_demonstrated"  # ...mais ne gagne rien : pas une amélioration
    assert primary_test(good, series(0.008, seed=4), plan)["outcome"] == "regression"  # gagne, mais moins que la référence
    assert primary_test(bad, flat, plan)["outcome"] == "regression"
    assert primary_test(series(0.0002, seed=5), flat, plan)["outcome"] == "not_demonstrated"
    short = primary_test(dict(list(good.items())[:20]), flat, plan)
    assert short["outcome"] == "undetermined" and short["absolute"]["ci_low"] is None


def test_p0_pure_noise_is_almost_never_called_an_improvement():
    plan = {**DEFAULT_PLAN, **FAST}
    rng = np.random.default_rng(99)
    hits = 0
    for _ in range(200):
        a, b = rng.normal(0, 0.004, 180), rng.normal(0, 0.006, 180)
        hits += primary_test(dict(enumerate(a)), dict(enumerate(b)), plan)["outcome"] == "supported"
    assert hits <= 10  # taux de faux positifs de la règle sous 5 % (attendu : nettement moins)


@pytest.mark.parametrize("seed", [21, 22, 23, 24])
def test_p0_no_noise_market_is_announced_profitable(seed):
    m = market(seed, "noise", 0.0)
    ev = m["d"]["control"]["evaluation"]["arms"]
    assert primary_test(ev["r1"]["daily"], ev["take_all"]["daily"], {**DEFAULT_PLAN, **FAST})["outcome"] != "supported"
    if seed == 22:  # R1 y prend des entrées : on le fait passer par toute la chaîne prospective
        eng = run(m, config(m, mode="prospective"), books=True)
        rep = report(eng, m, anchors=anchors_for(eng))
        assert rep["verdict"] in ("INCONCLUSIVE", "REJECTED") and rep["r1_status"] == "rejected"


@pytest.mark.parametrize("seed", [21, 23])
def test_a_market_whose_regime_changes_is_reported_honestly_never_promoted_by_default(seed):
    m = market(seed, "regime_shift")
    eng = run(m, config(m, mode="prospective"), books=True)
    rep = report(eng, m, anchors=anchors_for(eng))
    assert rep["verdict"] in ("INCONCLUSIVE", "REJECTED", "INSUFFICIENT_EVIDENCE", "PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY")
    if rep["verdict"] == "PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY":  # jamais sans les deux bornes positives
        assert rep["primary"]["absolute"]["ci_low"] > 0 and rep["primary"]["relative"]["ci_low"] > 0
    else:
        assert rep["r1_status"] in ("rejected", "not_evaluated")
    assert rep["coverage"]["missing"] == 0 and rep["arms"]["cash"]["net_pnl_eur"] == 0.0


def test_daily_returns_match_the_development_portfolio():
    u, d = M["u"], M["d"]
    part = d["parts"]["evaluation"]
    win = window_indices(u, d["windows"].bounds["evaluation"])
    enter, variant = TakeAll().policy(part)
    res = run_portfolio(u, intents_for(part, enter, variant), start=win[0], end=win[1], rules=RULES, cost=COST)
    assert analysis.daily_returns(res.equity, RULES.capital) == res.daily_returns()


# ---------------------------------------------------------------------------
# Verdicts du rapport
# ---------------------------------------------------------------------------


def test_the_method_can_say_yes_when_a_real_edge_exists(prospective):
    eng, anchors = prospective
    rep = report(eng, P, anchors=anchors)
    assert rep["verdict"] == "PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY", rep["reasons"]
    assert rep["r1_status"] == "retained_as_candidate_for_a_new_paper_protocol"
    assert rep["primary"]["outcome"] == "supported" and rep["primary"]["trials"] == 1 and rep["primary"]["reference"] == "take_all"
    assert rep["primary"]["absolute"]["ci_low"] > 0 and rep["primary"]["relative"]["ci_low"] > 0
    assert rep["coverage"]["decision_coverage"] == 1.0 and rep["coverage"]["missing"] == 0 and rep["window"]["complete"]
    assert rep["integrity"]["precedence"]["share"] == 1.0 and rep["integrity"]["session_anchored_before_start"]
    assert rep["arms"]["r1"]["book_entry_share"] == 1.0 and rep["arms"]["cash"]["net_pnl_eur"] == 0.0
    assert "aucune promotion automatique" in rep["claim"] and any("décision humaine" in r for r in rep["reasons"])
    assert set(rep["secondary"]["descriptive_only"]) == {"teacher:" + P["model"].reference_teacher, "project_mixte", "cash", "buy_and_hold"}


def test_without_timestamped_books_the_result_is_an_approximation_not_a_confirmation():
    eng = run(P, config(P, mode="prospective"))
    rep = report(eng, P, anchors=anchors_for(eng))
    assert rep["verdict"] == "INSUFFICIENT_EVIDENCE" and rep["r1_status"] == "not_evaluated"
    assert any("approximation non confirmatoire" in r for r in rep["reasons"])
    assert rep["primary"]["outcome"] == "supported"  # le chiffre est favorable, le verdict ne l'est pas


def test_without_dated_anchors_precedence_is_not_proven(prospective):
    eng, anchors = prospective
    for weak in ([], anchors_for(eng, dated=False)):
        rep = report(eng, P, anchors=weak)
        assert rep["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert any("non ancrée" in r for r in rep["reasons"]) and any("antériorité" in r for r in rep["reasons"])
    late = [{**a, "published_at_ms": a["created_at_ms"] + 30 * 86_400_000} for a in anchors]  # ancres publiées un mois après
    rep = report(eng, P, anchors=late)
    assert rep["verdict"] == "INSUFFICIENT_EVIDENCE" and rep["integrity"]["precedence"]["share"] < 0.5


def test_explored_or_simulated_periods_are_only_ever_exploratory(prospective):
    eng, anchors = prospective
    assert report(eng, P, anchors=anchors, explored=True)["verdict"] == "EXPLORATORY"
    for mode in ("synthetic", "replay_exploratory"):
        replayed = run(P, config(P, mode=mode), books=True)
        rep = report(replayed, P, anchors=anchors_for(replayed))
        assert rep["verdict"] == "EXPLORATORY" and rep["r1_status"] == "not_evaluated"
        assert rep["primary"]["outcome"] == "supported"  # même avec un résultat flatteur, aucune valeur de preuve


def test_integrity_failures_override_everything(prospective):
    eng, anchors = prospective
    wrong = build_report(eng.ledger.items, manifest_sha256="1" * 64, model_sha256=P["model"].sha256, plan=FAST, now_ms=eng.now, anchors=anchors)
    assert wrong["verdict"] == "FAILED_INTEGRITY" and any("préenregistrés" in r for r in wrong["reasons"])
    other = build_report(eng.ledger.items, manifest_sha256="0" * 64, model_sha256=M["model"].sha256, plan=FAST, now_ms=eng.now, anchors=anchors)
    assert other["verdict"] == "FAILED_INTEGRITY"
    forged = json.loads(json.dumps(eng.ledger.lines))
    target = next(ln for ln in forged if ln["kind"] == "outcome" and ln["body"]["status"] == "filled" and ln["body"]["arm"] == "r1")
    target["body"]["net_eur"] += 5.0
    prev = "0" * 64
    for ln in forged:
        ln["prev"] = prev
        ln["sha"] = line_sha(ln)
        prev = ln["sha"]
    rep = build_report(forged, manifest_sha256="0" * 64, model_sha256=P["model"].sha256, plan=FAST, now_ms=eng.now, anchors=anchors)
    assert rep["verdict"] == "FAILED_INTEGRITY" and any("réécrite" in r or "autre registre" in r for r in rep["reasons"])
    broken = json.loads(json.dumps(eng.ledger.lines))
    broken[40]["body"]["tape_sha256"] = "f" * 64
    rep = build_report(broken, manifest_sha256="0" * 64, model_sha256=P["model"].sha256, plan=FAST, now_ms=eng.now, anchors=anchors)
    assert validate(rep)["verdict"] == "FAILED_INTEGRITY" and rep["integrity"] == {"chain": "broken"}


def test_results_stay_masked_until_the_window_is_complete():
    cfg = config(P, mode="prospective")
    eng = run(P, cfg, books=True, upto=warm_index(P["u"], cfg) + 200)
    rep = report(eng, P, anchors=anchors_for(eng))
    assert rep["verdict"] == "IN_PROGRESS" and rep["primary"]["masked"] is True and rep["secondary"] == {"masked": True}
    assert all("net_pnl_eur" not in row and "final_eur" not in row for row in rep["arms"].values())
    assert rep["coverage"]["decision_coverage"] == 1.0 and not rep["window"]["complete"]
    idle, _ = start_engine(P, cfg)
    assert report(idle, P)["verdict"] == "NOT_STARTED"


def test_the_drawdown_limit_rejects_the_candidate_before_the_end():
    cfg = config(M, mode="prospective", limits=asdict(replace(Limits(), max_drawdown=0.004)))
    stop = warm_index(M["u"], cfg) + 400
    eng = run(M, cfg, books=True, upto=stop)
    rep = report(eng, M, anchors=anchors_for(eng))
    assert rep["verdict"] == "REJECTED" and rep["r1_status"] == "rejected" and any("critère d'arrêt" in r for r in rep["reasons"])


def test_an_unproven_candidate_is_rejected_not_kept_alive():
    eng = run(M, config(M, mode="prospective"), books=True)
    rep = report(eng, M, anchors=anchors_for(eng))
    assert rep["verdict"] == "INCONCLUSIVE" and rep["r1_status"] == "rejected"
    assert any("non démontrée" in r for r in rep["reasons"])
    floor = report(eng, M, anchors=anchors_for(eng), plan={"min_r1_entries": 500})
    assert floor["verdict"] == "INSUFFICIENT_EVIDENCE" and any("activité de R1 insuffisante" in r for r in floor["reasons"])


def test_late_data_and_revised_bars_lower_coverage_and_block_any_conclusion():
    cfg = config(P, mode="prospective")
    u = P["u"]
    eng, warm = start_engine(P, cfg)
    last = last_index(u, cfg)
    for i in range(warm, last):
        late = (i - warm) % 10 == 3  # une bougie sur dix arrive 10 minutes trop tard
        books = [book_at(u, s, i - 1) for s in u.symbols] if i > warm else []
        eng.step(int(u.ts[i]) + TF + (600_000 if late else 2000), bars=bars_at(u, i, latency=590_000 if late else 1000), books=books)
    rep = report(eng, P, anchors=anchors_for(eng))
    cov = rep["coverage"]
    assert rep["verdict"] == "INSUFFICIENT_EVIDENCE" and 0.89 < cov["decision_coverage"] < 0.91
    assert cov["refused"]["LATE_RECEPTION"] == cov["expected_decisions"] - cov["decided_on_time"] and cov["missing"] == 0
    assert cov["settlement_coverage"] == 1.0  # les positions restent gérées : seule la décision est perdue
    eng2, anchors2 = run(P, cfg, books=True), None
    revised = [{**b, "close": b["close"] * 1.01, "high": b["high"] * 1.03} for b in bars_at(u, last - 5)]
    eng2.step(eng2.now + 1000, bars=revised)
    rep2 = report(eng2, P, anchors=anchors_for(eng2))
    assert rep2["verdict"] == "INSUFFICIENT_EVIDENCE" and rep2["coverage"]["rejected_observations"] == {"CONFLICTING_REVISION": 3}
    assert anchors2 is None and any("révisées" in r for r in rep2["reasons"])


def test_missing_bars_are_counted_never_invented():
    cfg = config(P, mode="prospective", end=config(P).start_ms + 60 * TF)
    u = P["u"]
    eng, warm = start_engine(P, cfg)
    for i in range(warm, warm + 30):
        eng.step(int(u.ts[i]) + TF + 2000, bars=[b for b in bars_at(u, i) if b["symbol"] != "CCC/EUR" or i < warm + 10])
    rep = report(eng, P, now=int(u.ts[warm + 29]) + TF + 2000)
    cov = rep["coverage"]
    assert cov["expected_decisions"] == 90 and cov["decided_on_time"] == 70 and cov["missing"] == 20
    assert cov["settled"] == 10 and cov["expected_settlements"] == 30  # sans l'actif manquant, rien n'est réglé « à peu près »
    assert eng.tapes["CCC/EUR"].n == warm + 10 and rep["verdict"] == "IN_PROGRESS"


def test_a_ledger_rewritten_with_false_prices_is_caught_by_public_history():
    u = M["u"]
    truth = {s: {int(b.ts[i]): market_sha(x) for i in range(len(b)) for x in bars_at(u, i, symbols=[s])} for s, b in u.bars.items()}
    honest = analysis.verify_against_history(run(M).ledger.items, truth)
    assert honest["consistent"] and honest["checked"] > 9000 and not honest["unknown"]
    fake = {**M, "u": type(u)({s: (b if s != "BBB/EUR" else type(b)(b.symbol, b.ts, b.open, b.high * 1.001, b.low, b.close, b.volume, None, b.tf_ms))
                                  for s, b in u.bars.items()})}
    forged = run(fake, config(M))  # registre parfaitement cohérent avec lui-même, bâti sur de faux prix
    check = analysis.verify_against_history(forged.ledger.items, truth)
    assert not check["consistent"] and len(check["mismatched"]) > 3000 and {m[0] for m in check["mismatched"]} == {"BBB/EUR"}


# ---------------------------------------------------------------------------
# Puissance
# ---------------------------------------------------------------------------


def test_power_table_is_deterministic_monotone_and_controls_false_positives():
    ev = P["d"]["control"]["evaluation"]["arms"]
    kw = dict(horizons=(60, 240), yearly_effects=(0.0, 0.05, 0.5, 3.0), plan=FAST, sims=60, inner_reps=200)
    a = analysis.power_table(ev["r1"]["daily"], ev["take_all"]["daily"], **kw)
    assert a == analysis.power_table(ev["r1"]["daily"], ev["take_all"]["daily"], **kw)
    for h in ("60", "240"):
        row = a["power"][h]
        assert row["0.00"] <= 0.1 and row["0.00"] <= row["0.05"] <= row["0.50"] <= row["3.00"]  # plus l'effet est grand, plus on le voit
    assert a["power"]["240"]["0.50"] >= a["power"]["60"]["0.50"] and a["power"]["240"]["3.00"] > 0.9  # plus c'est long, plus on voit
    days = sorted(ev["r1"]["daily"])
    floor = analysis.activity_floor(ev["r1"]["entries_by_day"], days, 180)
    assert 0 <= floor["p05"] <= floor["p50"] and floor["expected_entries"] == pytest.approx(floor["entries_per_day"] * 180)
    assert analysis.activity_floor({}, days, 180)["p05"] == 0


# ---------------------------------------------------------------------------
# Manifeste immuable et journal des essais
# ---------------------------------------------------------------------------

FROZEN_V2_V3 = {
    "research/macro-shadow-v3/protocol.json": "7becacbdc7fa4fd0157a04c0f30ba4b55d79830c56a31da6171ca0ace6d2fb0c",
    "research/macro-shadow-v3/protocol.json.sha256": "858d42907dfc20645384ff9080b61d4a86c2b80c191e960c05dffafbe85b5fd4",
    "research/prospective-v2/evaluation-plan.json": "1ba39d4b5911df7dde84420b0cddd2a17c9622d230cc3e3ce242c44e6f721743",
    "research/prospective-v2/model-card.json": "e38c68d0f0f87e12acf1cecd8d8bb918f84956770156ba527503ae69baba864e",
    "research/prospective-v2/model.joblib": "539cd42346456db9f36f3f9f46f64fcbef33da6e96922fa5a5d10d58094b741b",
    "research/prospective-v2/protocol.json": "efecc21d9344d64ec351f2c9e5062b3adc4ed510730e29884f7df9f7357324ac",
    "research/prospective-v2/protocol.sha256": "bff98e2b3c29d04afbf28dd34d14bbaff4d2c86703db9950296a34665196f564",
    "research/prospective-v2/settings.json": "98eb77c15f61875cacfd78a606a7db50663addfb33a0c35d0681a742327ec747",
}


def test_frozen_v2_and_v3_protocols_are_bit_identical():
    for name, digest in FROZEN_V2_V3.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    assert sorted(p.relative_to(ROOT).as_posix() for d in ("research/prospective-v2", "research/macro-shadow-v3")
                  for p in (ROOT / d).iterdir()) == sorted(FROZEN_V2_V3)


def test_registered_protocol_is_intact_single_candidate_single_trial_and_not_started():
    manifest, digest, problems = protocol.load_manifest(DIR, root=ROOT)
    assert problems == [], problems  # tout fichier de code qui décide ou simule est celui qui a été enregistré
    assert manifest["protocol_id"] == protocol.PROTOCOL_ID and manifest["status"] == "PREREGISTERED_NOT_STARTED"
    assert manifest["mode"] == "shadow_only" and manifest["can_trade"] is False and manifest["promotion_allowed"] is False
    assert manifest["candidate"]["count"] == 1 and manifest["trials"]["count"] == 1 and manifest["analysis"]["trials"] == 1
    assert manifest["arms"]["primary_reference"] == "take_all" and manifest["distinct_from"] == ["research/prospective-v2", "research/macro-shadow-v3"]
    assert manifest["window"]["not_before_ms"] > manifest["registered_at_ms"]  # la fenêtre ne peut pas précéder l'enregistrement
    assert manifest["window"]["horizon_days"] in alpha_shadow.HORIZONS and manifest["universe"]["symbols"] == list(alpha_shadow.SYMBOLS)
    assert manifest["execution"]["limits"]["max_drawdown"] == 0.10 and manifest["execution"]["rules"]["capital"] == 30.0
    assert set(manifest["implementation_sha256"]) == set(protocol.IMPLEMENTATION)
    artifact = json.loads((DIR / "r1-model.json").read_text(encoding="utf-8"))
    model = r1.load_model(artifact, manifest["candidate"]["model_sha256"])
    assert artifact["training"]["preselection"]["tries"] == 5 and model.reference_teacher in ("trend", "breakout", "pullback", "mean_reversion")
    journal = protocol.read_journal(DIR / "trials.jsonl")
    assert [ln["kind"] for ln in journal] == ["development_freeze", "protocol_registered"]
    assert journal[0]["body"]["model_sha256"] == model.sha256 == journal[1]["body"]["model_sha256"]
    assert journal[1]["body"]["manifest_sha256"] == digest and journal[0]["at_ms"] < journal[1]["at_ms"]
    dev = json.loads((DIR / "development-report.json").read_text(encoding="utf-8"))
    assert dev["status"].startswith("EXPLORATORY") and dev["model_sha256"] == model.sha256
    limit = max(v["end_ms"] for v in dev["data"].values())
    assert limit <= manifest["window"]["not_before_ms"]  # aucune bougie du développement dans la future fenêtre


def test_the_manifest_is_write_once_and_any_edit_is_detected(tmp_path):
    work = tmp_path / "protocol"
    shutil.copytree(DIR, work)
    manifest, _, problems = protocol.load_manifest(work)
    assert problems == []
    with pytest.raises(protocol.ProtocolError, match="immuable"):
        protocol.write_manifest(work, manifest)
    edits = {
        "protocol.json": lambda t: t.replace('"horizon_days": %d' % manifest["window"]["horizon_days"], '"horizon_days": 7'),
        "r1-model.json": lambda t: t.replace('"margin_r": 0.0', '"margin_r": 0.2'),
        "development-report.json": lambda t: t.replace("EXPLORATORY", "CONFIRMATORY", 1),
    }
    for name, edit in edits.items():
        original = (work / name).read_text(encoding="utf-8")
        assert edit(original) != original, name
        (work / name).write_text(edit(original), encoding="utf-8")
        assert protocol.load_manifest(work)[2], name
        (work / name).write_text(original, encoding="utf-8")
    ref = work / "reference-project-mixte.joblib"
    ref.write_bytes(ref.read_bytes() + b"x")
    assert any("modèle du projet" in p for p in protocol.load_manifest(work)[2])
    crlf = (work / "protocol.json").read_bytes().replace(b"\n", b"\r\n")
    (work / "protocol.json").write_bytes(crlf)  # extraction du dépôt sous Windows : mêmes empreintes
    ref.write_bytes(ref.read_bytes()[:-1])
    assert protocol.load_manifest(work)[2] == []


def test_code_drift_after_registration_is_reported(tmp_path):
    manifest, _, _ = protocol.load_manifest(DIR)
    root = tmp_path / "repo"
    for name in protocol.IMPLEMENTATION:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, root / name)
    assert protocol.load_manifest(DIR, root=root)[2] == []
    target = root / "sentinelle/alpha/shadow/engine.py"
    target.write_text(target.read_text(encoding="utf-8").replace("max_drawdown: float = 0.10", "max_drawdown: float = 0.50"), encoding="utf-8")
    problems = protocol.load_manifest(DIR, root=root)[2]
    assert problems == ["code modifié depuis l'enregistrement : sentinelle/alpha/shadow/engine.py"]


def test_session_rules_come_from_the_manifest_only():
    manifest, digest, _ = protocol.load_manifest(DIR)
    start = manifest["window"]["not_before_ms"]
    cfg = protocol.session_config(manifest, digest, start_ms=start, mode="prospective")
    assert cfg.end_ms - cfg.start_ms == manifest["window"]["horizon_days"] * 86_400_000 and cfg.not_before_ms == start
    assert cfg.cost == manifest["execution"]["cost"] and cfg.limits == manifest["execution"]["limits"] and cfg.manifest_sha256 == digest
    with pytest.raises(protocol.ProtocolError, match="fixée par le manifeste"):
        protocol.session_config(manifest, digest, start_ms=start, mode="prospective", horizon_days=30)
    with pytest.raises(protocol.ProtocolError, match="minuit UTC"):
        protocol.session_config(manifest, digest, start_ms=start + TF, mode="prospective")
    assert protocol.session_config(manifest, digest, start_ms=start + TF, mode="replay_exploratory", horizon_days=30).end_ms == start + TF + 30 * 86_400_000


def test_trial_journal_is_chained_and_allows_a_single_prospective_session(tmp_path):
    path = tmp_path / "trials.jsonl"
    protocol.journal_append(path, "protocol_registered", {"protocol_id": "p1"}, 1000)
    protocol.journal_append(path, "prospective_session_started", {"protocol_id": "p1", "ledger_id": "a" * 64}, 2000)
    with pytest.raises(protocol.ProtocolError, match="un seul essai"):
        protocol.journal_append(path, "prospective_session_started", {"protocol_id": "p1", "ledger_id": "b" * 64}, 3000)
    protocol.journal_append(path, "prospective_session_started", {"protocol_id": "p2"}, 4000)  # autre protocole, autre fenêtre
    assert [ln["seq"] for ln in protocol.read_journal(path)] == [0, 1, 2]
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0].replace("p1", "p9")] + lines[1:]) + "\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError, match="altéré"):
        protocol.read_journal(path)
    assert protocol.read_journal(tmp_path / "absent.jsonl") == []


# ---------------------------------------------------------------------------
# Isolation : aucun ordre, aucun réseau, moteur léger
# ---------------------------------------------------------------------------

FORBIDDEN = ("sentinelle.execution.paper", "sentinelle.execution.positions", "sentinelle.execution.live", "sentinelle.execution.base",
             "sentinelle.execution.book_execution", "sentinelle.services", "sentinelle.api", "sentinelle.notify", "sentinelle.market",
             "sentinelle.storage", "sentinelle.news", "sentinelle.preflight", "tools.supervise", "tools.observe", "tools.public_capture",
             "tools.collect_public", "ccxt", "httpx", "requests", "socket", "urllib", "aiohttp", "subprocess", "telethon", "sqlite3",
             "asyncio", "threading", "multiprocessing")
SHADOW_FILES = sorted((ROOT / "sentinelle" / "alpha" / "shadow").glob("*.py")) + [ROOT / "sentinelle/alpha/fastlearner.py",
                                                                                 ROOT / "tools/alpha_shadow.py", ROOT / "tools/alpha_bench.py"]


@pytest.mark.parametrize("path", SHADOW_FILES, ids=lambda p: p.name)
def test_shadow_modules_cannot_reach_a_broker_the_network_or_the_application(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = {3: "sentinelle.", 2: "sentinelle.alpha.", 1: "sentinelle.alpha.shadow." if path.parent.name == "shadow" else "sentinelle.alpha."}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = (package[node.level] if node.level else "") + (node.module or "")
            names = [base] + [f"{base.rstrip('.')}.{a.name}" for a in node.names]
        for name in names:
            assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN), f"{path.name} importe {name}"
        if isinstance(node, (ast.Attribute, ast.Name)):
            ident = node.attr if isinstance(node, ast.Attribute) else node.id
            assert ident not in ("place_order", "create_order", "submit_order", "cancel_order", "fetch_balance", "withdraw", "sleep"), path.name


def test_the_engine_loads_no_learning_library_broker_or_network_module():
    code = ("import sys, sentinelle.alpha.shadow.engine, sentinelle.alpha.shadow.analysis, sentinelle.alpha.shadow.protocol;"
            "bad=sorted(m for m in sys.modules if m.split('.')[0] in ('sklearn','pandas','joblib','ccxt','httpx','requests','sqlite3','fastapi')"
            " or m.startswith(('sentinelle.execution.paper','sentinelle.execution.positions','sentinelle.execution.live','sentinelle.services',"
            "'sentinelle.api','sentinelle.market','sentinelle.storage','sentinelle.ai.model','sentinelle.alpha.learners')));print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_shadow_package_declares_itself_shadow_only():
    import sentinelle.alpha.shadow as shadow
    assert shadow.CAN_TRADE is False and shadow.SHADOW_ONLY is True and shadow.CONTRACT == "alpha_shadow_contract_v1"


# ---------------------------------------------------------------------------
# Commande de recherche
# ---------------------------------------------------------------------------


def test_cli_prints_the_contract_and_verifies_the_registered_protocol(capsys):
    assert alpha_shadow.main(["contract"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["contract"] == "alpha_shadow_contract_v1" and schema["unknown_fields"] == "refused"
    assert alpha_shadow.main(["verify"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["intact"] is True and out["trials_in_journal"] == ["development_freeze", "protocol_registered"]


def test_cli_refuses_to_freeze_twice_or_to_register_a_window_in_the_past(tmp_path, capsys):
    assert alpha_shadow.main(["freeze", "--archive", str(tmp_path)]) == 2  # un artefact existe : le gel ne se refait pas
    work = tmp_path / "p"
    shutil.copytree(DIR, work)
    (work / "protocol.json").unlink()
    assert alpha_shadow.main(["register", "--dir", str(work), "--not-before", "2026-01-01"]) == 2
    assert "futur" in capsys.readouterr().err and not (work / "protocol.json").exists()


def test_replay_of_the_registered_candidate_is_exploratory_and_reproducible(tmp_path):
    manifest, digest, _, model = alpha_shadow.load_protocol(DIR, check_code=False)
    raw = synthetic_universe(5, kind="regimes", n_bars=1200, symbols=tuple(alpha_shadow.SYMBOLS))
    u = type(raw)({s: type(b)(b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume, None, b.tf_ms) for s, b in raw.bars.items()})
    start = int(u.ts[600])
    start -= start % 86_400_000
    path = tmp_path / "ledger.jsonl"
    eng = alpha_shadow.replay(u, manifest, digest, model, start_ms=start, days=60, ledger_path=path)
    again = alpha_shadow.replay(u, manifest, digest, model, start_ms=start, days=60, ledger_path=None)
    assert eng.ledger.head == again.ledger.head and len(kinds(eng, "decision")) == 361 * 3
    rep = build_report(alpha_shadow.load_lines(path), manifest_sha256=digest, model_sha256=model.sha256, plan=manifest["analysis"], now_ms=eng.now)
    assert validate(rep)["verdict"] == "EXPLORATORY" and rep["mode"] == "replay_exploratory" and rep["window"]["complete"]
    assert alpha_shadow.main(["anchor", "--ledger", str(path)]) == 0


def test_cli_ingests_a_contract_file_one_step_at_a_time_and_keeps_prospective_mode_closed(tmp_path, capsys):
    manifest, digest, _, model = alpha_shadow.load_protocol(DIR, check_code=False)
    raw = synthetic_universe(6, kind="regimes", n_bars=420, symbols=tuple(alpha_shadow.SYMBOLS))
    u = type(raw)({s: type(b)(b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume, None, b.tf_ms) for s, b in raw.bars.items()})
    start = int(u.ts[400]) + TF
    start += (-start) % 86_400_000
    first = int(np.searchsorted(u.ts + TF, start))
    ledger, obs = tmp_path / "ledger.jsonl", tmp_path / "export.jsonl"
    obs.write_text("\n".join(json.dumps(b) for i in range(first) for b in bars_at(u, i)) + "\n", encoding="utf-8")
    common = ["ingest", "--ledger", str(ledger), "--observations", str(obs)]
    assert alpha_shadow.main(common + ["--now-ms", str(start), "--mode", "prospective", "--start-ms", str(start)]) == 2
    assert "accord du propriétaire" in capsys.readouterr().err and not ledger.exists()
    now = int(u.ts[first - 1]) + TF + 2000
    assert alpha_shadow.main(common + ["--now-ms", str(now)]) == 2  # nouvelle session sans début : refus
    assert alpha_shadow.main(common + ["--now-ms", str(now), "--start-ms", str(start), "--days", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lines"]["admission"] == first * 3 and out["decisions"] == [] and out["anchor"]["contract"] == ANCHOR_CONTRACT
    book = book_at(u, alpha_shadow.SYMBOLS[0], first - 1)
    obs.write_text("\n".join(json.dumps(r) for r in bars_at(u, first) + [book, {"kind": "note"}]) + "\n", encoding="utf-8")
    assert alpha_shadow.main(common + ["--now-ms", str(int(u.ts[first]) + TF + 2000)]) == 0  # reprise de la session existante
    out = json.loads(capsys.readouterr().out)
    assert len(out["decisions"]) == 3 and {d["as_of_ms"] for d in out["decisions"]} == {start} and out["ignored_records"] == 1
    assert {d["action"] for d in out["decisions"]} <= {"ENTER", "ABSTAIN", "NO_SIGNAL"}
    lines = alpha_shadow.load_lines(ledger)
    assert lines[0]["body"]["config"]["mode"] == "replay_exploratory" and lines[-1]["kind"] == "commit"
