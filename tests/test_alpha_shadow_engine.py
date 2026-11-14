"""ALPHA SHADOW — moteur en ligne : causalité, refus des anomalies, P&L causal par bras, reprise (mission #41)."""
import json
from dataclasses import asdict, replace

import numpy as np
import pytest

from sentinelle.alpha.bars import indicators
from sentinelle.alpha.execution import CostModel, run_portfolio
from sentinelle.alpha.learners import TakeAll
from sentinelle.alpha.shadow.contract import validate
from sentinelle.alpha.shadow.engine import Limits, ShadowEngine, ShadowError, book_fill, market_sha
from sentinelle.alpha.shadow.ledger import ChainLedger, LedgerError, line_sha, verify_chain
from sentinelle.alpha.shadow.online import _FIELDS, Tape
from sentinelle.alpha.teachers import exit_signals
from sentinelle.alpha.velocity import intents_for, window_indices
from tests.shadow_fixtures import (COST, RULES, TF, bars_at, book_at, config, kinds, last_index, market, run, start_engine,
                                   warm_index)

M = market(7)
BASE = run(M)


# ---------------------------------------------------------------------------
# Série en ligne : mêmes valeurs que le développement, jamais de futur
# ---------------------------------------------------------------------------


def test_online_tape_reproduces_development_indicators_exactly():
    for sym, b in M["u"].bars.items():
        tape = Tape(sym, TF, COST.default_spread_bps, capacity=64)  # petite capacité : force plusieurs agrandissements
        for i in range(1500):
            tape.append(int(b.ts[i]), float(b.open[i]), float(b.high[i]), float(b.low[i]), float(b.close[i]), float(b.volume[i]))
        part = b.prefix(1500)
        ind, online = indicators(part, COST.default_spread_bps), tape.indicators()
        for f in _FIELDS:
            assert np.array_equal(getattr(ind, f), getattr(online, f), equal_nan=True), f
        batch_exits = exit_signals(part, ind)
        assert all(np.array_equal(batch_exits[k], v) for k, v in tape.exits().items())


def test_tape_refuses_gaps_and_exposes_read_only_views():
    b = M["u"].bars["AAA/EUR"]
    tape = Tape("AAA/EUR", TF, 4.0)
    tape.append(int(b.ts[0]), 1.0, 1.0, 1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        tape.append(int(b.ts[2]), 1.0, 1.0, 1.0, 1.0, 1.0)  # une bougie manquante n'est jamais comblée
    with pytest.raises(ValueError):
        tape.bars().close[0] = 2.0
    with pytest.raises(ValueError):
        tape.indicators().ema20[0] = 2.0
    assert tape.n == 1 and tape.index_of(int(b.ts[0])) == 0
    with pytest.raises(KeyError):
        tape.index_of(int(b.ts[5]))


# ---------------------------------------------------------------------------
# Équivalence avec le calcul de développement (PR #40), trade par trade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ["r1", "take_all"])
def test_online_engine_reproduces_the_batch_portfolio_trade_by_trade(arm):
    u, d = M["u"], M["d"]
    part = d["parts"]["evaluation"]
    win = window_indices(u, d["windows"].bounds["evaluation"])
    enter, variant = (d["router"] if arm == "r1" else TakeAll()).policy(part)
    batch = run_portfolio(u, intents_for(part, enter, variant), start=win[0], end=win[1], rules=RULES, cost=COST)
    expected = {(t["symbol"], t["decision_index"]): t["pnl"] for t in batch.trades}
    online, latent = {}, []
    for ln in kinds(BASE, "outcome"):
        b = ln["body"]
        key = (b["symbol"], int((b["decision_as_of_ms"] - TF - int(u.ts[0])) // TF))
        if b["arm"] == arm and b["status"] == "filled":
            online[key] = b["net_eur"]
        elif b["arm"] == arm and b["status"] == "latent":
            latent.append(key[1])
    assert len(online) >= 20
    first_latent = min(latent) if latent else 10**9  # au-delà, le calcul d'un bloc écarte les positions non closes
    for key, pnl in expected.items():
        if key[1] < first_latent:
            assert online[key] == pytest.approx(pnl, abs=1e-12), key
    assert set(online) <= set(expected)


def test_positions_still_open_at_the_end_are_marked_not_dropped():
    latent = [ln["body"] for ln in kinds(BASE, "outcome") if ln["body"]["status"] == "latent"]
    assert latent and all(o["exit"] is None and o["net_r"] is None and o["net_eur"] is not None for o in latent)
    last = kinds(BASE, "settlement")[-1]["body"]
    assert last["final"] is True and last["close_at_ms"] == M["hi"]
    for arm in ("r1", "take_all"):
        closed = sum(ln["body"]["net_eur"] for ln in kinds(BASE, "outcome") if ln["body"]["arm"] == arm and ln["body"]["status"] in ("filled", "latent"))
        assert closed == pytest.approx(last["arms"][arm]["equity_eur"] - RULES.capital, abs=1e-9)  # le grand livre se recoupe


# ---------------------------------------------------------------------------
# Causalité : rien ne change quand on ajoute ou retire le futur
# ---------------------------------------------------------------------------


def test_runs_are_deterministic_and_identical_when_the_future_is_removed():
    again = run(M)
    assert again.ledger.head == BASE.ledger.head
    cut = warm_index(M["u"], config(M)) + 300
    short = run(M, upto=cut)
    n = len(short.ledger.lines)
    assert n > 1000 and short.ledger.lines == BASE.ledger.lines[:n]  # décisions passées invariantes, empreintes comprises


def test_every_decision_is_recorded_before_the_next_price_is_admitted():
    seen_next = {}
    for ln in BASE.ledger.lines:
        b = ln["body"]
        if ln["kind"] == "admission":
            seen_next[(b["bar"]["symbol"], b["bar"]["open_at_ms"])] = ln["seq"]
        elif ln["kind"] == "decision":
            assert b["as_of_ms"] <= b["recorded_at_ms"] <= b["as_of_ms"] + Limits().max_decision_latency_ms
            assert (b["symbol"], b["as_of_ms"]) not in seen_next  # la bougie suivante n'était pas encore connue
            validate(b)
    assert len(kinds(BASE, "decision")) == len(kinds(BASE, "reference")) > 2000


def test_decisions_only_depend_on_public_bars_and_carry_their_fingerprint():
    u = M["u"]
    for ln in kinds(BASE, "decision")[:50]:
        b = ln["body"]
        i = int((b["as_of_ms"] - TF - int(u.ts[0])) // TF)
        expected = next(x for x in bars_at(u, i) if x["symbol"] == b["symbol"])
        assert b["bar_sha256"] == market_sha(expected) and b["history"]["bars"] == i + 1
    other = market_sha({**bars_at(u, 10)[0], "received_at_ms": 1, "source": "ailleurs", "published_at_ms": None})
    assert other == market_sha(bars_at(u, 10)[0])  # l'empreinte de marché ne dépend pas de qui a reçu la bougie, ni quand


# ---------------------------------------------------------------------------
# Anomalies : refus motivé, couverture honnête, jamais d'observation inventée
# ---------------------------------------------------------------------------


def fresh(**kw):
    cfg = config(M, **kw)
    eng, warm = start_engine(M, cfg)
    return eng, warm, cfg


def rejections(result):
    return [ln["body"]["code"] for ln in result["lines"] if ln["kind"] == "rejection"]


def test_malformed_future_unclosed_or_foreign_bars_are_refused_with_a_reason():
    eng, warm, _ = fresh()
    u = M["u"]
    good = bars_at(u, warm)[0]
    now = good["close_at_ms"] + 2000
    cases = {
        "INCOHERENT_OHLC": {**good, "high": good["low"] * 0.5},
        "INVALID_FIELD": {**good, "close": float("nan")},
        "UNKNOWN_FIELD": {**good, "future_close": 1.0},
        "RECEIVED_IN_FUTURE": {**good, "received_at_ms": now + 60_000},
        "BAR_NOT_CLOSED": {**good, "received_at_ms": good["close_at_ms"] - 5000, "published_at_ms": None},
        "UNKNOWN_MARKET": {**good, "symbol": "DOGE/EUR"},
        "MISALIGNED_BAR": {**good, "close_at_ms": good["close_at_ms"] + 60_000},
        "NOT_AN_OBJECT": "bougie",
    }
    for code, raw in cases.items():
        before = eng.tapes["AAA/EUR"].n
        assert rejections(eng.step(now, bars=[raw])) == [code], code
        assert eng.tapes["AAA/EUR"].n == before
    published_early = {**good, "published_at_ms": good["close_at_ms"] - 60_000}
    assert rejections(eng.step(now, bars=[published_early])) == ["BAR_NOT_CLOSED"]
    assert not kinds(eng, "decision")


def test_late_bars_extend_history_but_never_produce_a_decision():
    eng, warm, _ = fresh()
    u = M["u"]
    close = int(u.ts[warm]) + TF
    late_rx = eng.step(close + 400_000, bars=bars_at(u, warm, latency=390_000))  # reçue 6,5 min après la clôture
    assert {d["body"]["reason"] for d in late_rx["decisions"]} == {"LATE_RECEPTION"}
    close2 = close + TF
    late_dec = eng.step(close2 + 301_000, bars=bars_at(u, warm + 1))  # reçue à temps, traitée trop tard
    assert {d["body"]["reason"] for d in late_dec["decisions"]} == {"LATE_DECISION"}
    assert all(d["body"]["action"] == "REFUSED" and not d["body"]["candidates"] for d in late_rx["decisions"] + late_dec["decisions"])
    assert eng.tapes["AAA/EUR"].n == warm + 2  # la série reste continue : la bougie tardive est de l'histoire
    assert all(not arm.pending for arm in eng.arms.values())  # aucune entrée possible sur une décision refusée
    on_time = eng.step(close2 + TF + 2000, bars=bars_at(u, warm + 2))
    assert {d["body"]["action"] for d in on_time["decisions"]} <= {"ENTER", "ABSTAIN", "NO_SIGNAL"}


def test_a_gap_is_never_filled_and_blocks_decisions_until_the_missing_bar_arrives():
    eng, warm, _ = fresh()
    u = M["u"]
    skip = [b for b in bars_at(u, warm + 1) if b["symbol"] == "AAA/EUR"]
    eng.step(int(u.ts[warm]) + TF + 2000, bars=[b for b in bars_at(u, warm) if b["symbol"] != "AAA/EUR"])
    res = eng.step(int(u.ts[warm + 1]) + TF + 2000, bars=bars_at(u, warm + 1))
    aaa = [ln["body"] for ln in res["lines"] if ln["kind"] == "admission" and ln["body"]["bar"]["symbol"] == "AAA/EUR"]
    assert [a["status"] for a in aaa] == ["PENDING"] and eng.tapes["AAA/EUR"].n == warm  # mise en attente, pas d'invention
    assert all(d["body"]["symbol"] != "AAA/EUR" for d in res["decisions"])
    assert not [ln for ln in res["lines"] if ln["kind"] == "settlement"]  # pas de règlement tant qu'un actif manque
    healed = eng.step(int(u.ts[warm + 1]) + TF + 20_000, bars=[b for b in bars_at(u, warm, latency=TF + 15_000) if b["symbol"] == "AAA/EUR"])
    status = [(ln["body"]["bar"]["open_at_ms"], ln["body"]["status"], ln["body"]["from_pending"]) for ln in healed["lines"] if ln["kind"] == "admission"]
    assert status == [(int(u.ts[warm]), "HISTORY", False), (int(u.ts[warm + 1]), "DECIDABLE", True)]
    reasons = {d["body"]["as_of_ms"]: d["body"]["reason"] for d in healed["decisions"]}
    assert reasons[int(u.ts[warm]) + TF] == "LATE_RECEPTION" and len(healed["decisions"]) == 2
    assert len([ln for ln in healed["lines"] if ln["kind"] == "settlement"]) == 2 and eng.tapes["AAA/EUR"].n == warm + 2
    assert skip[0]["open_at_ms"] == int(u.ts[warm + 1])


def test_duplicates_are_ignored_and_revised_bars_are_refused():
    eng, warm, _ = fresh()
    u = M["u"]
    now = int(u.ts[warm]) + TF + 2000
    eng.step(now, bars=bars_at(u, warm))
    head, state = eng.ledger.head, eng.state_sha()
    same = eng.step(now + 10, bars=bars_at(u, warm))
    assert rejections(same) == [] and eng.counts["duplicates"] == 3 and eng.state_sha() == state and eng.ledger.head != head
    revised = [{**b, "close": b["close"] * 1.01, "high": b["high"] * 1.02} for b in bars_at(u, warm)]
    assert rejections(eng.step(now + 20, bars=revised)) == ["CONFLICTING_REVISION"] * 3
    assert eng.state_sha() == state  # la première version reste la seule connue
    old = [{**bars_at(u, 0)[0], "open_at_ms": int(u.ts[0]) - TF, "close_at_ms": int(u.ts[0]), "published_at_ms": int(u.ts[0])}]
    assert rejections(eng.step(now + 30, bars=old)) == ["BEFORE_HISTORY_START"]


def test_clock_regression_is_refused_and_leaves_the_ledger_untouched():
    eng, warm, _ = fresh()
    n = len(eng.ledger.lines)
    with pytest.raises(ShadowError):
        eng.step(eng.now - 1, bars=bars_at(M["u"], warm))
    with pytest.raises(ShadowError):
        eng.step(float(eng.now + 5))
    assert len(eng.ledger.lines) == n
    eng.step(eng.now + 5)
    assert eng.ledger.lines[-1]["kind"] == "commit"


def test_warmup_is_required_before_any_decision():
    cfg = config(M, start=int(M["u"].ts[100]) + TF, end=int(M["u"].ts[140]) + TF)
    eng = run(M, cfg)
    decisions = kinds(eng, "decision")
    assert len(decisions) == 41 * 3 and {d["body"]["reason"] for d in decisions} == {"WARMUP_INCOMPLETE"}
    assert not kinds(eng, "outcome")


def test_no_decision_before_the_window_or_after_its_end():
    cfg = config(M, end=config(M).start_ms + 50 * TF)
    eng = run(M, cfg)
    extra = bars_at(M["u"], last_index(M["u"], cfg))
    res = eng.step(extra[0]["close_at_ms"] + 2000, bars=extra)
    assert not res["decisions"] and not [ln for ln in res["lines"] if ln["kind"] == "settlement"]
    as_of = [d["body"]["as_of_ms"] for d in kinds(eng, "decision")]
    assert min(as_of) == cfg.start_ms and max(as_of) == cfg.end_ms
    assert sum(1 for ln in kinds(eng, "settlement") if ln["body"]["final"]) == 1


# ---------------------------------------------------------------------------
# P&L causal, indépendant par bras
# ---------------------------------------------------------------------------


def test_refusals_and_abstentions_never_receive_a_result_and_cash_stays_flat():
    by_arm = {}
    for ln in kinds(BASE, "outcome"):
        by_arm.setdefault(ln["body"]["arm"], []).append(ln["body"])
    assert "cash" not in by_arm and "buy_and_hold" not in by_arm
    assert {s["body"]["arms"]["cash"]["equity_eur"] for s in kinds(BASE, "settlement")} == {RULES.capital}
    entered = {sha for arm in by_arm.values() for sha in [o["decision_sha256"] for o in arm]}
    from sentinelle.alpha.shadow.contract import sha256
    abstained = [ln for ln in kinds(BASE, "decision") if ln["body"]["action"] in ("ABSTAIN", "NO_SIGNAL", "REFUSED")]
    assert abstained and all(sha256(ln["body"]) not in entered for ln in abstained)
    for arm, rows in by_arm.items():
        for o in rows:
            if o["status"] in ("abandoned", "refused_by_portfolio"):
                assert o["net_eur"] is None and o["entry"] is None, arm


def test_each_arm_has_its_own_portfolio():
    last = kinds(BASE, "settlement")[-1]["body"]["arms"]
    assert set(last) == {"r1", "take_all", "teacher:" + M["model"].reference_teacher, "project_mixte", "cash", "buy_and_hold"}
    assert len({round(v["equity_eur"], 9) for k, v in last.items() if k not in ("project_mixte",)}) >= 4
    assert last["project_mixte"]["equity_eur"] == RULES.capital  # référence indisponible : bras non simulé, et déclaré
    assert all(ln["body"]["unavailable"] == ["project_mixte"] for ln in kinds(BASE, "reference"))
    hold = BASE.arms["buy_and_hold"]
    assert hold.cash == 0.0 and set(hold.hold) == set(M["u"].symbols)


def test_higher_costs_reduce_the_simulated_result():
    stressed = run(M, config(M, cost=asdict(CostModel().stressed(4.0)) | {"fee_rate": 0.003}))
    for arm in ("take_all", "r1"):
        assert stressed.arms[arm].last_value < BASE.arms[arm].last_value
        assert stressed.arms[arm].fees > BASE.arms[arm].fees * 0.9
    assert stressed.arms["cash"].last_value == BASE.arms["cash"].last_value == RULES.capital


def test_book_fill_walks_the_asks_and_reports_what_was_really_served():
    asks = [[100.0, 0.02], [101.0, 0.05], [105.0, 1.0]]
    price, served = book_fill(asks, 2.0)
    assert price == 100.0 and served == 2.0
    price, served = book_fill(asks, 5.0)
    assert served == pytest.approx(5.0) and 100.0 < price < 101.0
    price, served = book_fill([[100.0, 0.01]], 5.0)
    assert served == pytest.approx(1.0) and price == 100.0  # carnet trop mince : exécution partielle


def test_entries_use_the_first_book_received_after_the_decision_else_a_flagged_approximation():
    with_books = run(M, books=True)
    entries = [ln["body"]["entry"] for ln in kinds(with_books, "outcome") if ln["body"]["entry"]]
    assert entries and {e["evidence"] for e in entries} == {"book"} and all(e["book_sha256"] for e in entries)
    assert {ln["body"]["entry"]["evidence"] for ln in kinds(BASE, "outcome") if ln["body"]["entry"]} == {"bar_model"}
    used = {ln["body"]["book_sha256"] for ln in kinds(with_books, "book")}
    assert {e["book_sha256"] for e in entries} <= used  # chaque carnet utilisé est conservé dans le registre
    exits = {ln["body"]["exit"]["evidence"] for ln in kinds(with_books, "outcome") if ln["body"]["exit"]}
    assert exits == {"bar_model"}  # les sorties (stops intrabougie) restent modélisées, et c'est dit


@pytest.mark.parametrize("kw,why", [
    ({"delay": 1500}, "reçu avant l'écriture de la décision"),
    ({"delay": 400_000}, "reçu après le délai admis"),
])
def test_books_outside_the_admissible_window_are_not_price_evidence(kw, why):
    eng = run(M, books=True, book_kw=kw, think=2000 if kw["delay"] < 10_000 else 2000)
    evidence = {ln["body"]["entry"]["evidence"] for ln in kinds(eng, "outcome") if ln["body"]["entry"]}
    assert evidence == {"bar_model"}, why
    assert not kinds(eng, "book")


def test_stale_malformed_and_future_books_are_refused():
    eng, warm, _ = fresh()
    u = M["u"]
    now = int(u.ts[warm]) + TF + 2000
    good = book_at(u, "AAA/EUR", warm)
    crossed = {**good, "bids": [[good["asks"][0][0] * 1.01, 1.0]]}
    future = {**good, "received_at_ms": now + 10_000_000}
    foreign = {**good, "symbol": "DOGE/EUR"}
    res = eng.step(now + 2000, bars=bars_at(u, warm), books=[crossed, future, foreign, {"x": 1}])
    assert rejections(res) == ["CROSSED_BOOK", "RECEIVED_IN_FUTURE", "UNKNOWN_MARKET", "UNKNOWN_CONTRACT"]
    assert all(not v for v in eng.books.values())
    stale = run(M, books=True, book_kw={"delay": 3000, "exchange": True})
    assert kinds(stale, "book")
    # carnet dont l'heure de plateforme précède la décision : pas une preuve de prix
    eng2, warm2, _ = fresh()
    old = {**book_at(u, "AAA/EUR", warm2), "exchange_at_ms": int(u.ts[warm2]) + TF - 60_000}
    eng2.step(int(u.ts[warm2]) + TF + 2000, bars=bars_at(u, warm2))
    eng2.step(int(u.ts[warm2 + 1]) + TF + 2000, bars=bars_at(u, warm2 + 1), books=[old])
    assert not kinds(eng2, "book")


def test_a_worse_real_price_lowers_the_result_and_thin_books_give_partial_or_no_fill():
    fair = run(M, books=True)
    worse = run(M, books=True, book_kw={"shift": 0.004})  # prix réellement payable 0,4 % au-dessus de l'ouverture
    assert worse.arms["take_all"].last_value < fair.arms["take_all"].last_value
    thin = run(M, books=True, book_kw={"qty": 1e-7})
    refusals = [ln["body"]["refusal"] for ln in kinds(thin, "outcome") if ln["body"]["status"] == "refused_by_portfolio"]
    assert "carnet_insuffisant" in refusals and thin.arms["take_all"].closed == 0
    partial = run(M, books=True, book_kw={"depth_eur": 5.2})  # le carnet n'offre que 5,20 EUR en tout
    entries = [ln["body"]["entry"] for ln in kinds(partial, "outcome") if ln["body"]["entry"]]
    assert any(e["partial"] for e in entries) and all(e["notional_eur"] <= 5.2 * 1.001 for e in entries)  # écart du carnet compris
    assert any(not e["partial"] for e in entries)  # les ordres au minimum de 5 EUR sont servis en entier


def test_drawdown_limit_stops_new_entries_of_the_candidate_only():
    cfg = config(M, limits=asdict(replace(Limits(), max_drawdown=0.004)))
    eng = run(M, cfg)
    aborted = eng.arms["r1"].aborted_at
    assert aborted is not None and eng.arms["take_all"].aborted_at is None
    after = [ln["body"] for ln in kinds(eng, "decision") if ln["body"]["as_of_ms"] > aborted]
    assert after and all(d["action"] != "ENTER" for d in after)
    assert any(d["reason"] == "ABORT_DRAWDOWN_LIMIT" for d in after)
    assert eng.arms["take_all"].closed == BASE.arms["take_all"].closed  # la référence continue à l'identique
    assert kinds(eng, "settlement")[-1]["body"]["r1_aborted_at_ms"] == aborted


# ---------------------------------------------------------------------------
# Sessions, reprise après arrêt brutal, altération
# ---------------------------------------------------------------------------


def test_a_prospective_session_cannot_start_in_the_past_or_before_its_preregistration():
    u, model = M["u"], M["model"]
    cfg = config(M, mode="prospective", not_before_ms=M["lo"])
    with pytest.raises(ShadowError, match="passé"):
        ShadowEngine.start(cfg, model, ChainLedger(None), now_ms=cfg.start_ms + 1)
    with pytest.raises(ShadowError, match="déjà explorée"):
        ShadowEngine.start(replace(cfg, not_before_ms=cfg.start_ms + TF), model, ChainLedger(None), now_ms=int(u.ts[0]))
    eng = ShadowEngine.start(cfg, model, ChainLedger(None), now_ms=cfg.start_ms)
    with pytest.raises(ShadowError, match="déjà une session"):
        ShadowEngine.start(cfg, model, eng.ledger, now_ms=cfg.start_ms)
    for bad in (replace(cfg, mode="live"), replace(cfg, start_ms=cfg.start_ms + 1), replace(cfg, end_ms=cfg.start_ms),
                replace(cfg, symbols=("BBB/EUR", "AAA/EUR"))):
        with pytest.raises(ShadowError):
            ShadowEngine.start(bad, model, ChainLedger(None), now_ms=0)
    session = eng.ledger.lines[0]["body"]
    assert session["can_trade"] is False and session["shadow_only"] is True and session["model_sha256"] == model.sha256


def test_resume_replays_the_ledger_and_continues_exactly_like_an_uninterrupted_run(tmp_path):
    cfg = config(M)
    u = M["u"]
    stop = warm_index(u, cfg) + 150
    path = tmp_path / "ledger.jsonl"
    run(M, cfg, ledger=ChainLedger(path, fsync=False), upto=stop)
    resumed = ShadowEngine.resume(cfg, M["model"], ChainLedger(path, fsync=False), now_ms=int(u.ts[stop]))
    for i in range(stop, last_index(u, cfg)):
        resumed.step(int(u.ts[i]) + TF + 2000, bars=bars_at(u, i))
    assert resumed.ledger.head == BASE.ledger.head and resumed.state_sha() == BASE.state_sha()
    assert not [ln for ln in resumed.ledger.lines if ln["kind"] == "recovery"]  # arrêt propre : rien à annuler


def test_a_crash_in_the_middle_of_a_step_voids_it_and_never_backdates_a_decision(tmp_path):
    cfg = config(M)
    u = M["u"]
    stop = warm_index(u, cfg) + 60
    path = tmp_path / "ledger.jsonl"
    run(M, cfg, ledger=ChainLedger(path, fsync=False), upto=stop)
    raw = path.read_bytes()
    lines = raw.rstrip(b"\n").split(b"\n")
    path.write_bytes(b"\n".join(lines[:-3]) + b"\n" + lines[-3][:57])  # commit perdu, ligne coupée
    with pytest.raises(LedgerError):
        ChainLedger(path).begin(10**13)
    back = int(u.ts[stop - 1]) + TF + 3 * 3_600_000  # redémarrage trois heures plus tard
    eng = ShadowEngine.resume(cfg, M["model"], ChainLedger(path, fsync=False), now_ms=back)
    recovery = [ln for ln in eng.ledger.lines if ln["kind"] == "recovery"]
    assert len(recovery) == 1 and recovery[0]["body"]["void_lines"] > 0 and len(recovery[0]["body"]["torn_fragments"]) == 1
    assert eng.tapes["AAA/EUR"].n == stop - 1  # le pas interrompu n'a laissé aucun état
    res = eng.step(back + 1000, bars=bars_at(u, stop - 1))  # la bougie est renvoyée, trop tard pour décider
    assert {d["body"]["reason"] for d in res["decisions"]} == {"LATE_DECISION"} and len(res["decisions"]) == 3
    voided = set(verify_chain(eng.ledger.items)["void"])
    lost = [ln for ln in eng.ledger.lines if ln["seq"] in voided and ln["kind"] == "decision"]
    kept = [ln["body"]["as_of_ms"] for ln in verify_chain(eng.ledger.items)["valid"] if ln["kind"] == "decision" and ln["body"]["action"] != "REFUSED"]
    assert all(ln["body"]["as_of_ms"] not in kept for ln in lost)  # une décision annulée n'est jamais « reconstituée »
    for i in range(stop, stop + 30):
        eng.step(int(u.ts[i]) + TF + 2000, bars=bars_at(u, i))
    assert ShadowEngine.resume(cfg, M["model"], ChainLedger(path, fsync=False), now_ms=eng.now).state_sha() == eng.state_sha()


def test_resume_detects_rewritten_decisions_even_when_every_hash_is_recomputed(tmp_path):
    cfg = config(M)
    path = tmp_path / "ledger.jsonl"
    eng = run(M, cfg, ledger=ChainLedger(path, fsync=False), upto=warm_index(M["u"], cfg) + 80)
    lines = json.loads(json.dumps(eng.ledger.lines))
    target = next(ln for ln in lines if ln["kind"] == "decision" and ln["body"]["action"] == "ENTER")
    target["body"].update(action="ABSTAIN", reason="SCORE_NOT_ABOVE_MARGIN", chosen=None, theoretical=None)
    for c in target["body"]["candidates"]:
        c["taken"] = False
    prev = "0" * 64
    for ln in lines:  # réécriture complète : toutes les empreintes sont recalculées
        ln["prev"], ln["sha"] = prev, None
        ln["sha"] = line_sha(ln)
        prev = ln["sha"]
    from sentinelle.alpha.shadow.contract import canonical
    path.write_bytes(b"".join(canonical(ln) + b"\n" for ln in lines))
    verify_chain(lines)  # la chaîne est cohérente...
    with pytest.raises(ShadowError, match="ne se recalcule pas"):
        ShadowEngine.resume(cfg, M["model"], ChainLedger(path, fsync=False), now_ms=eng.now)  # ...mais pas le rejeu


def test_resume_refuses_another_model_protocol_or_an_empty_ledger(tmp_path):
    cfg = config(M)
    path = tmp_path / "ledger.jsonl"
    eng = run(M, cfg, ledger=ChainLedger(path, fsync=False), upto=warm_index(M["u"], cfg) + 5)
    other = market(22, "hidden_rule")["model"]
    with pytest.raises(ShadowError, match="ne correspond pas"):
        ShadowEngine.resume(cfg, other, ChainLedger(path), now_ms=eng.now)
    with pytest.raises(ShadowError, match="ne correspond pas"):
        ShadowEngine.resume(replace(cfg, manifest_sha256="1" * 64), M["model"], ChainLedger(path), now_ms=eng.now)
    with pytest.raises(ShadowError, match="ne correspond pas"):
        ShadowEngine.resume(replace(cfg, end_ms=cfg.end_ms + TF), M["model"], ChainLedger(path), now_ms=eng.now)
    with pytest.raises(ShadowError, match="vide"):
        ShadowEngine.resume(cfg, M["model"], ChainLedger(tmp_path / "none.jsonl"), now_ms=1)


def test_an_exception_inside_a_step_writes_nothing(monkeypatch):
    eng, warm, _ = fresh()
    n = len(eng.ledger.lines)

    def boom(*a, **k):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(eng, "_decide", boom)
    with pytest.raises(RuntimeError):
        eng.step(int(M["u"].ts[warm]) + TF + 2000, bars=bars_at(M["u"], warm))
    assert len(eng.ledger.lines) == n and eng.ledger._buffer is None
