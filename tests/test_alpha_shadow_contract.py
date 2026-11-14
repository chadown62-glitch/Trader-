"""ALPHA SHADOW — contrat ``alpha_shadow_contract_v1`` et registre ancré (mission #41)."""
import json

import pytest

from sentinelle.alpha.ledger import ShadowLedger, _digest
from sentinelle.alpha.shadow import CONTRACT, contract
from sentinelle.alpha.shadow.contract import ContractError, bar_from_kline, canonical, make, migrate, sha256, validate
from sentinelle.alpha.shadow.ledger import (ChainLedger, LedgerError, chain_lines, line_sha, parse, precedence, verify_anchors,
                                            verify_chain)

TF = 14_400_000
T0 = 1_794_614_400_000  # 2026-11-14 00:00 UTC
SHA = "ab" * 32


def bar(**over):
    base = dict(symbol="BTC/EUR", timeframe_ms=TF, open_at_ms=T0, close_at_ms=T0 + TF, open=100.0, high=101.0, low=99.0, close=100.5,
                volume=12.0, source="test", published_at_ms=T0 + TF, received_at_ms=T0 + TF + 1000, payload_sha256=None)
    return {"contract": CONTRACT, "kind": "bar", **{**base, **over}}


def decision(**over):
    plan = {"stop_price": 95.0, "max_bars": 120, "exit_rule": "close_below_low10", "trail_atr": None, "target_price": None}
    cand = {"teacher": "breakout", "vetoed": False, "veto_reasons": [], "climate": 3, "score_r": 0.2, "taken": True, "plan": plan}
    base = dict(protocol_id="p", manifest_sha256=SHA, model_sha256=SHA, arm="r1", symbol="BTC/EUR", as_of_ms=T0 + TF, bar_sha256=SHA,
                history={"bars": 400, "first_open_at_ms": T0 - 400 * TF, "tape_sha256": SHA},
                observed={"published_at_ms": T0 + TF, "received_at_ms": T0 + TF + 1000}, recorded_at_ms=T0 + TF + 2000,
                action="ENTER", reason="SCORE_ABOVE_MARGIN", candidates=[cand], chosen="breakout", threshold_r=0.0,
                theoretical={"equity_eur": 30.0, "risk_eur": 0.15, "notional_eur": 5.0})
    return {"contract": CONTRACT, "kind": "decision", **{**base, **over}}


def outcome(**over):
    entry = {"at_ms": T0 + TF, "price": 100.6, "evidence": "book", "book_sha256": SHA, "notional_eur": 5.0, "qty": 0.0497,
             "fee_eur": 0.005, "partial": False, "bar_received_late": False}
    exit_ = {"at_ms": T0 + 3 * TF, "price": 102.0, "reason": "règle de sortie", "evidence": "bar_model", "fee_eur": 0.005}
    base = dict(protocol_id="p", arm="r1", symbol="BTC/EUR", teacher="breakout", decision_as_of_ms=T0 + TF, decision_sha256=SHA,
                status="filled", refusal=None, entry=entry, exit=exit_, gross_eur=0.07, costs_eur=0.012, net_eur=0.058, net_r=0.21,
                bars_held=3, available_at_ms=T0 + 4 * TF)
    return {"contract": CONTRACT, "kind": "outcome", **{**base, **over}}


def code_of(rec):
    with pytest.raises(ContractError) as err:
        validate(rec)
    return err.value.code


# ---------------------------------------------------------------------------
# Contrat : validation stricte
# ---------------------------------------------------------------------------


def test_valid_records_pass_and_hash_is_independent_of_key_order():
    assert validate(bar())["symbol"] == "BTC/EUR" and validate(decision()) and validate(outcome())
    shuffled = dict(reversed(list(bar().items())))
    assert sha256(shuffled) == sha256(bar()) and canonical(shuffled) == canonical(bar())


def test_unknown_or_missing_fields_are_refused_never_ignored():
    assert code_of({**bar(), "note": "x"}) == "UNKNOWN_FIELD"
    assert code_of({k: v for k, v in bar().items() if k != "volume"}) == "MISSING_FIELD"
    assert code_of({**bar(), "contract": "alpha_shadow_contract_v0"}) == "UNKNOWN_CONTRACT"
    assert code_of({**bar(), "kind": "candle"}) == "UNKNOWN_KIND"
    assert code_of([1, 2]) == "NOT_AN_OBJECT"


@pytest.mark.parametrize("field,value", [
    ("open_at_ms", T0 // 1000),  # secondes prises pour des millisecondes
    ("received_at_ms", (T0 + TF) * 1000),  # microsecondes
    ("received_at_ms", float(T0 + TF + 5)),  # instant non entier
    ("close", float("nan")), ("high", float("inf")), ("open", True), ("volume", -1.0), ("close", "100.5"),
    ("symbol", "btc/eur"), ("timeframe_ms", 3_600_000), ("payload_sha256", "xyz"), ("source", " "),
])
def test_wrong_units_types_and_non_finite_numbers_are_refused(field, value):
    assert code_of(bar(**{field: value})) == "INVALID_FIELD"


def test_bars_must_be_aligned_coherent_and_published_before_reception():
    assert code_of(bar(close_at_ms=T0 + TF + 1)) == "MISALIGNED_BAR"
    assert code_of(bar(open_at_ms=T0 + 60_000, close_at_ms=T0 + 60_000 + TF)) == "MISALIGNED_BAR"
    assert code_of(bar(high=99.5)) == "INCOHERENT_OHLC" and code_of(bar(low=100.2)) == "INCOHERENT_OHLC"
    assert code_of(bar(published_at_ms=T0 + TF + 5000)) == "PUBLISHED_AFTER_RECEIVED"


def test_books_must_be_sorted_uncrossed_and_honest_about_their_clock():
    base = dict(symbol="BTC/EUR", source="t", exchange_at_ms=None, received_at_ms=T0, time_quality="receipt_only",
                bids=[[99.0, 1.0], [98.0, 2.0]], asks=[[100.0, 1.0], [101.0, 2.0]], update_id=7)
    assert make("book", **base)["update_id"] == 7
    assert code_of({"contract": CONTRACT, "kind": "book", **{**base, "bids": [[100.5, 1.0]]}}) == "CROSSED_BOOK"
    assert code_of({"contract": CONTRACT, "kind": "book", **{**base, "asks": [[101.0, 1.0], [100.0, 1.0]]}}) == "UNSORTED_BOOK"
    assert code_of({"contract": CONTRACT, "kind": "book", **{**base, "time_quality": "exchange"}}) == "BOOK_TIME_QUALITY"
    assert code_of({"contract": CONTRACT, "kind": "book", **{**base, "asks": [[100.0, 0.0]]}}) == "INVALID_FIELD"
    assert code_of({"contract": CONTRACT, "kind": "book", **{**base, "exchange_at_ms": T0 + 1, "time_quality": "exchange"}}) == "PUBLISHED_AFTER_RECEIVED"


def test_a_decision_cannot_carry_any_result_field():
    for field in ("net_eur", "pnl", "outcome", "exit_price", "realized_return"):
        assert code_of({**decision(), field: 1.0}) == "UNKNOWN_FIELD"


def test_decisions_must_be_internally_consistent():
    d = decision()
    assert code_of({**d, "action": "ABSTAIN"}) == "INCONSISTENT_DECISION"
    assert code_of({**d, "chosen": "trend"}) == "INCONSISTENT_DECISION"
    vetoed = {**d["candidates"][0], "vetoed": True}
    assert code_of({**d, "candidates": [vetoed]}) == "VETOED_ENTRY"
    assert code_of({**d, "recorded_at_ms": T0}) == "RECORDED_BEFORE_AS_OF"
    assert code_of({**d, "action": "REFUSED", "reason": "LATE_RECEPTION", "chosen": None}) == "INCONSISTENT_DECISION"
    assert code_of({**d, "reason": "parce que"}) == "INVALID_FIELD"  # raison = code stable, pas une phrase
    refused = {**d, "action": "REFUSED", "reason": "LATE_RECEPTION", "candidates": [], "chosen": None, "theoretical": None}
    assert validate(refused)["action"] == "REFUSED"


def test_refused_or_abandoned_opportunities_never_get_a_result():
    void = dict(entry=None, exit=None, gross_eur=None, costs_eur=None, net_eur=None, net_r=None, bars_held=None)
    ok = outcome(status="refused_by_portfolio", refusal="trop_de_positions", **void)
    assert validate(ok)["net_eur"] is None
    for status in ("refused_by_portfolio", "abandoned"):
        assert code_of(outcome(status=status, refusal="x", **{**void, "net_eur": 0.4})) == "RESULT_WITHOUT_FILL"
        assert code_of(outcome(status=status, refusal="x", **{**void, "net_r": 1.5})) == "RESULT_WITHOUT_FILL"


def test_outcomes_cannot_precede_their_decision_or_miss_their_fill():
    assert code_of(outcome(available_at_ms=T0)) == "OUTCOME_BEFORE_DECISION"
    early = {**outcome()["entry"], "at_ms": T0}
    assert code_of(outcome(entry=early)) == "FILL_BEFORE_DECISION"
    assert code_of(outcome(exit=None)) == "INCOMPLETE_OUTCOME"
    assert validate(outcome(status="latent", exit=None, gross_eur=None, costs_eur=None, net_r=None))["status"] == "latent"


def test_a_report_cannot_recommend_promotion_outside_a_prospective_session():
    rep = dict(protocol_id="p", manifest_sha256=SHA, model_sha256=SHA, mode="replay_exploratory", generated_at_ms=T0,
               verdict="PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY", reasons=[], r1_status="not_evaluated",
               window={"start_ms": T0, "end_ms": T0 + TF, "complete": True, "days_observed": 1}, integrity={}, coverage={},
               primary={}, arms={}, secondary={}, claim="simulation fictive ; aucun gain futur promis ; aucune promotion automatique")
    assert code_of({"contract": CONTRACT, "kind": "report", **rep}) == "NON_PROSPECTIVE_PROMOTION"
    assert validate({"contract": CONTRACT, "kind": "report", **{**rep, "verdict": "EXPLORATORY"}})
    assert code_of({"contract": CONTRACT, "kind": "report", **{**rep, "verdict": "PROFITABLE"}}) == "INVALID_FIELD"
    assert code_of({"contract": CONTRACT, "kind": "report", **{**rep, "verdict": "EXPLORATORY", "claim": "rentable"}}) == "INVALID_FIELD"


def test_unknown_contract_versions_are_refused_and_migrations_are_explicit(monkeypatch):
    with pytest.raises(ContractError) as err:
        migrate({**bar(), "contract": "alpha_shadow_contract_v0"})
    assert err.value.code == "UNKNOWN_CONTRACT"

    def from_v0(rec):  # exemple : la v0 nommait l'instant de réception autrement
        rec["received_at_ms"] = rec.pop("rx_ms")
        rec["contract"] = CONTRACT
        return rec

    monkeypatch.setitem(contract.MIGRATIONS, "alpha_shadow_contract_v0", from_v0)
    old = {k: v for k, v in bar().items() if k != "received_at_ms"} | {"contract": "alpha_shadow_contract_v0", "rx_ms": T0 + TF + 1000}
    assert migrate(old) == bar()
    assert migrate(bar()) == bar()


def test_public_kline_adapter_keeps_exchange_decimals_and_refuses_garbage():
    row = [T0, "62445.04000000", "62672.54000000", "61951.67000000", "62623.46000000", "15.19881000", T0 + TF - 1]
    b = bar_from_kline("BTC/EUR", row, received_at_ms=T0 + TF + 900, source="binance-public")
    assert b["open"] == 62445.04 and b["close_at_ms"] == T0 + TF and b["published_at_ms"] is None
    for bad in ([T0 + 0.5, "1", "2", "0.5", "1", "1"], [T0, "abc", "2", "1", "1", "1"], [T0]):
        with pytest.raises(ContractError):
            bar_from_kline("BTC/EUR", bad, received_at_ms=T0 + TF + 900, source="x")
    assert set(contract.json_schema()["kinds"]) == {"bar", "book", "decision", "reference", "outcome", "report"}


# ---------------------------------------------------------------------------
# Registre : pas atomiques, reprise, ancres
# ---------------------------------------------------------------------------


def fill(ledger, steps=3, start=1000):
    for k in range(steps):
        ledger.begin(start + k)
        ledger.add("decision", {"n": k})
        ledger.add("outcome", {"decision_sha256": sha256({"n": k}), "available_at_ms": start + k + 50})
        ledger.commit("s" * 64)
    return ledger


def test_steps_are_atomic_chained_and_reload_identically(tmp_path):
    path = tmp_path / "l.jsonl"
    led = fill(ChainLedger(path))
    assert len(led.lines) == 9 and verify_chain(led.lines)["valid"] == led.lines
    again = ChainLedger(path)
    assert again.lines == led.lines and again.recovered is None and again.head == led.head
    again.begin(2000)
    again.add("decision", {"n": 99})
    again.commit("s" * 64)
    assert ChainLedger(path).lines[-1]["kind"] == "commit" and len(ChainLedger(path).lines) == 11


def test_partial_edits_deletions_and_reordering_break_the_chain(tmp_path):
    led = fill(ChainLedger(None))
    for mutate in (lambda ls: ls[3]["body"].update(n=5), lambda ls: ls.pop(4), lambda ls: ls.insert(2, ls.pop(5))):
        lines = json.loads(json.dumps(led.lines))
        mutate(lines)
        with pytest.raises(LedgerError):
            verify_chain(lines)


def rehash(lines):
    """Attaque : celui qui tient le fichier réécrit tout et recalcule chaque empreinte."""
    prev = "0" * 64
    for ln in lines:
        ln["prev"] = prev
        ln["sha"] = line_sha(ln)
        prev = ln["sha"]
    return lines


def test_the_pr40_ledger_accepts_a_complete_rewrite_which_is_why_anchors_exist(tmp_path):
    path = tmp_path / "v1.jsonl"
    old = ShadowLedger(path)
    h = old.record_decision(10, {"action": "abstain"})
    old.record_outcome(h, 20, {"net_r": -1.0})
    records = [json.loads(x) for x in path.read_text().splitlines()]
    records[0]["payload"] = {"action": "enter"}  # l'histoire est réécrite après coup...
    records[0]["hash"] = _digest(records[0])
    records[1]["prev"], records[1]["decision"] = records[0]["hash"], records[0]["hash"]
    records[1]["payload"] = {"net_r": 2.0}
    records[1]["hash"] = _digest(records[1])
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records) + "\n")
    assert ShadowLedger(path).verify() is True  # ...et la chaîne seule ne voit rien


def test_a_complete_rewrite_passes_the_chain_but_not_the_published_anchors():
    led = fill(ChainLedger(None))
    anchor = led.anchor(5000)
    forged = json.loads(json.dumps(led.lines))
    forged[3]["body"]["n"] = 42
    rehash(forged)
    assert verify_chain(forged)["lines"] == 9  # chaîne cohérente : la réécriture est invisible sans ancre
    assert verify_anchors(led.lines, [anchor]) == []
    assert any("réécrite" in p for p in verify_anchors(forged, [anchor]))


def test_anchors_detect_truncation_another_session_and_disorder():
    led = fill(ChainLedger(None), steps=4)
    early, late = {**led.anchor(1), "seq": 5, "head_sha256": led.lines[5]["sha"]}, led.anchor(2)
    assert verify_anchors(led.lines, [early, late]) == []
    assert any("tronqué" in p for p in verify_anchors(led.lines[:8], [late]))
    other = fill(ChainLedger(None), start=7000)
    assert any("autre registre" in p for p in verify_anchors(other.lines, [late]))
    assert any("non chronologiques" in p for p in verify_anchors(led.lines, [late, early]))
    assert any("format inconnu" in p for p in verify_anchors(led.lines, [{"seq": 1}]))
    forged = rehash(json.loads(json.dumps(led.lines)))  # réécriture qui change aussi la première ligne
    forged[0]["body"]["n"] = 1
    assert any("autre registre" in p for p in verify_anchors(rehash(forged), [late]))


def test_a_torn_write_is_declared_void_and_never_completed_after_the_fact(tmp_path):
    path = tmp_path / "l.jsonl"
    led = fill(ChainLedger(path), steps=2)
    led.begin(3000)
    led.add("decision", {"n": 7})
    led.add("outcome", {"decision_sha256": sha256({"n": 7}), "available_at_ms": 3100})
    led.commit("s" * 64)
    raw = path.read_bytes()
    cut = raw.rstrip(b"\n").rfind(b"\n")  # on perd le commit, et la ligne précédente est coupée en deux
    cut2 = raw[:cut].rfind(b"\n")
    path.write_bytes(raw[:cut2 + 1 + 40])
    crashed = ChainLedger(path)
    assert crashed.recovered == {"void_from_seq": 6, "void_lines": 1, "torn_fragments": [sha256(raw[cut2 + 1:cut2 + 41])]}
    with pytest.raises(LedgerError):
        crashed.begin(4000)  # impossible d'écrire avant d'avoir déclaré la reprise
    line = crashed.recover(4000)
    assert line["kind"] == "recovery" and crashed.recovered is None
    crashed.begin(4001)
    crashed.add("decision", {"n": 8})
    crashed.commit("s" * 64)
    report = verify_chain(parse(path.read_bytes())[0])
    assert report["void"] == [6] and [ln["body"].get("n") for ln in report["valid"] if ln["kind"] == "decision"] == [0, 1, 8]
    assert ChainLedger(path).recovered is None  # la reprise est durable


def test_a_complete_last_line_without_newline_is_kept_and_garbage_in_the_middle_is_fatal(tmp_path):
    path = tmp_path / "l.jsonl"
    led = fill(ChainLedger(path), steps=2)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    again = ChainLedger(path)
    assert again.lines == led.lines and again.recovered is None
    again.begin(5000)
    again.add("decision", {"n": 3})
    again.commit("s" * 64)
    assert len(chain_lines(parse(path.read_bytes())[0])) == 8
    broken = path.read_bytes().split(b"\n")
    broken.insert(2, b'{"seq": 2, "tronq')
    path.write_bytes(b"\n".join(broken))
    with pytest.raises(LedgerError):
        ChainLedger(path)


def test_ledger_refuses_clock_regression_nested_steps_and_orphan_lines():
    led = fill(ChainLedger(None))
    with pytest.raises(LedgerError):
        led.begin(10)
    led.begin(9000)
    with pytest.raises(LedgerError):
        led.begin(9001)
    led.abort()
    with pytest.raises(LedgerError):
        led.add("decision", {})
    with pytest.raises(LedgerError):
        led.commit("s" * 64)
    with pytest.raises(LedgerError):
        ChainLedger(None).anchor(1)
    assert len(led.lines) == 9  # le pas abandonné n'a rien écrit


def test_precedence_requires_an_externally_dated_anchor_before_the_outcome():
    led = fill(ChainLedger(None))
    undated = led.anchor(1)
    assert precedence(led.lines, [undated])["proven_prior_to_outcome"] == 0  # notre propre horloge ne prouve rien
    per_step = [{**undated, "seq": 3 * k + 2, "head_sha256": led.lines[3 * k + 2]["sha"], "published_at_ms": 1000 + k + 10} for k in range(3)]
    p = precedence(led.lines, per_step)
    assert p["decisions_with_outcome"] == 3 and p["proven_prior_to_outcome"] == 3 and p["share"] == 1.0
    too_late = [{**undated, "published_at_ms": 9999}]
    assert precedence(led.lines, too_late)["proven_prior_to_outcome"] == 0
    assert precedence(led.lines, per_step[:1])["lines_after_last_anchor"] == 6
