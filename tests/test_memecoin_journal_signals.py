"""The causal journal holds every input of the entry rules, so each entry signal can be re-derived from it alone.

Reproduced on main 930e246 before this change: the journal recorded a ``momentum`` decision, but each journaled quote
kept only its provenance, price, depth and 1 h volume; the 5-minute volume pace and buy/sell ratio that momentum and the
sniper compare, the pool age and the dip hunter's recent high were missing, so no entry signal could be re-checked, nor
any other routing rule replayed on the same observations. Each quote now keeps the inputs of the entry rules, and each
evaluation the M58 admission, the recent high and the previous observation it was compared with. ``audit_signals``
re-derives the signals with ``entry_signals`` (the pure part of ``evaluate``, after the Guardian). No decision changes.
Offline and deterministic."""

import json

import pytest

from sentinelle.dmh import memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT
from tests.test_memecoin_engine import ACCELERATION, causal_session


def audited(db):
    return sim.audit_signals(sim.journal(db))


def test_a_momentum_decision_carries_its_inputs_and_is_re_derived_from_the_journal_alone(db):
    feed, clock, step = causal_session(db)
    step(0)
    step(60, **ACCELERATION)
    before, decision = sim.journal(db)[-2:]
    ev, quote = decision["evaluations"][MINT], decision["quotes"][MINT]
    assert ev["signals"] == ["momentum"]
    for key in ("volume_usd_per_min_m5", "buy_sell_count_ratio_m5", "pool_age_minutes", "ts"):
        assert key in quote  # the inputs the momentum and sniper rules compare
    previous = ev["inputs"]["previous"]
    assert previous["ts"] == before["ts"] and previous["volume_usd_per_min_m5"] == before["quotes"][MINT]["volume_usd_per_min_m5"]
    assert ev["inputs"]["m58"] is True and ev["inputs"]["peak"] is not None
    result = audited(db)
    assert result["checked"] == 2 and result["matched"] == 2 and result["mismatches"] == [] and result["unverifiable"] == 0


def test_a_burst_that_triggers_the_sniper_and_momentum_together_is_re_derived(db):
    feed, clock, step = causal_session(db)
    feed.pair.update(volume={"m5": 500, "h1": 12_000}, txns={"m5": {"buys": 6, "sells": 3}})
    step(0)
    step(60, priceUsd="1.05", volume={"m5": 2000, "h1": 12_000}, txns={"m5": {"buys": 12, "sells": 3}})
    decision = sim.journal(db)[-1]
    assert decision["evaluations"][MINT]["signals"] == ["momentum", "sniper"]  # one burst, two strategies (by design)
    result = audited(db)
    assert result["matched"] == result["checked"] == 2 and result["mismatches"] == []


def test_a_pullback_from_the_recent_high_is_re_derived_as_a_dip(db):
    feed, clock, step = causal_session(db)
    step(0)
    step(60, priceUsd="0.7")  # 70 % of the high, same depth, volume still flowing
    decision = sim.journal(db)[-1]
    ev = decision["evaluations"][MINT]
    assert "dip_hunter" in ev["signals"] and ev["inputs"]["peak"] == pytest.approx(1.0)
    result = audited(db)
    assert result["matched"] == result["checked"] and result["mismatches"] == []


def test_a_tampered_journal_is_reported_and_old_rows_are_unverifiable_not_matched(db):
    feed, clock, step = causal_session(db)
    step(0)
    step(60)  # nothing changed: no signal
    rows = sim.journal(db)
    assert rows[-1]["evaluations"][MINT]["signals"] == []
    tampered = json.loads(json.dumps(rows))
    tampered[-1]["evaluations"][MINT]["signals"] = ["momentum"]  # a signal the inputs do not support
    result = sim.audit_signals(tampered)
    assert result["mismatches"] == [{"revision": rows[-1]["revision"], "mint": MINT, "journaled": ["momentum"], "derived": []}]
    old = json.loads(json.dumps(rows))
    for row in old:  # as recorded before this change: no inputs, quotes without the 5-minute fields
        for ev in row["evaluations"].values():
            ev.pop("inputs")
        for q in row["quotes"].values():
            q.pop("volume_usd_per_min_m5")
    assert sim.audit_signals(old) == {"checked": 0, "matched": 0, "mismatches": [], "unverifiable": 2}


def test_evaluate_is_the_guardian_then_the_pure_entry_signals(monkeypatch):
    point = {"price": 0.7, "liquidity_usd": 50_000.0, "volume_usd_per_min_m5": 120.0, "buy_sell_count_ratio_m5": 2.0,
             "ts": 1_000_060, "pool_age_minutes": 180.0, "volume_h1_usd": 12_000.0}
    asset = {"symbol": MINT, "dex_id": "pumpswap", "peak": 1.0, "peak_ts": 1_000_000, "peak_liquidity": 50_000.0,
             "last": {**point, "price": 1.0, "ts": 1_000_000, "volume_usd_per_min_m5": 100.0, "buy_sell_count_ratio_m5": 1.5}}
    monkeypatch.setattr(ms, "assess_candidate", lambda *a, **k: {"allowed": True, "vetoes": []})
    assert ms.evaluate(None, asset, point, True) == (ms.entry_signals(asset, point, True), {"allowed": True, "vetoes": []})
    assert ms.entry_signals(asset, point) == ["dip_hunter"]
    monkeypatch.setattr(ms, "assess_candidate", lambda *a, **k: {"allowed": False, "vetoes": [{"code": "x"}]})
    assert ms.evaluate(None, asset, point, True)[0] == []  # the veto still comes first


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
