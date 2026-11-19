"""COST OPTIMIZER — moteur : preuves, frais, minimums et reliquats, ruban, exécution market/limit, contrat."""
import ast
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.lab import costs as labcosts
from sentinelle.costs import audit, benefit, book, contract, inventory, markets, stats, venues
from sentinelle.costs.evidence import EvidenceError, Value, hypothesis, weakest
from sentinelle.costs.execution import Execution, evaluate_sample, limit_price, market, passive
from sentinelle.costs.tape import Tape, from_binance, quote_at

ROOT = Path(__file__).resolve().parent.parent
SHA = "ab" * 32
T0 = 1_700_000_000_000


def tape(rows, symbol="TESTUSDT"):
    """rows : (décalage ms, prix, quantité, initié par un acheteur ?)."""
    rows = sorted(rows)
    return Tape(symbol, np.array([T0 + r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows], float),
                np.array([r[2] for r in rows], float), np.array([r[3] for r in rows], bool), SHA)


def book_tape(bid=99.99, ask=100.01, *, n=60, step_ms=1000, start=-60_000, qty=1.0):
    """Un marché calme : alternance d'achats au prix vendeur et de ventes au prix acheteur."""
    rows = []
    for k in range(n):
        rows.append((start + k * step_ms, ask, qty, True))
        rows.append((start + k * step_ms + 500, bid, qty, False))
    return rows


# ---------------------------------------------------------------------------
# Preuves
# ---------------------------------------------------------------------------


def test_values_must_carry_their_evidence():
    assert Value(1.0, "bps", "OBSERVED", "x", as_of="2026-11-16", sha256=SHA).level == "OBSERVED"
    with pytest.raises(EvidenceError):
        Value(1.0, "bps", "OBSERVED", "x", as_of="2026-11-16")  # une observation sans empreinte n'en est pas une
    with pytest.raises(EvidenceError):
        Value(1.0, "bps", "ESTIMATED", "x", method="moyenne")  # sans nombre d'observations
    with pytest.raises(EvidenceError):
        Value(1.0, "bps", "DECLARED", "x")  # sans date
    with pytest.raises(EvidenceError):
        Value(1.0, "bps", "HYPOTHETICAL", "x")  # sans justification
    with pytest.raises(EvidenceError):
        Value(float("nan"), "bps", "HYPOTHETICAL", "x", note="n")
    with pytest.raises(EvidenceError):
        Value(1.0, "bps", "MEASURED", "x")
    with pytest.raises(EvidenceError):
        Value(1.0, "bps", "HYPOTHETICAL", "x", interval=(2.0, 1.0), note="n")
    h = hypothesis(5.0, "provisoire", low=1.0, high=9.0)
    assert Value.from_dict(h.to_dict()) == h and weakest(["OBSERVED", "HYPOTHETICAL", "ESTIMATED"]) == "HYPOTHETICAL"
    with pytest.raises(EvidenceError):
        Value.from_dict({**h.to_dict(), "bonus": 1})


def test_fee_schedule_is_hypothetical_unless_the_page_was_archived():
    f = venues.binance_spot_vip0()
    assert f.taker.level == "HYPOTHETICAL" and f.rate(maker=False) == 0.001 and f.rate(maker=True, discounted=True) == 0.00075
    assert "non vérifiée" in f.taker.note
    g = venues.binance_spot_vip0(page_sha256=SHA, as_of="2026-11-16")
    assert g.taker.level == "DECLARED" and g.taker.sha256 == SHA


def test_market_filters_are_read_from_the_public_catalogue():
    raw = json.dumps({"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"}, {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
            {"filterType": "NOTIONAL", "minNotional": "5.0", "applyMinToMarket": True}]},
        {"symbol": "BAD", "baseAsset": "B", "quoteAsset": "USDT", "status": "BREAK", "filters": []}]}).encode()
    f = venues.parse_exchange_info(raw, as_of="2026-11-16")
    assert set(f) == {"BTCUSDT"} and f["BTCUSDT"].step == Decimal("0.00001") and f["BTCUSDT"].min_notional == Decimal("5.0")
    assert f["BTCUSDT"].market_step == Decimal("0.00001") and f["BTCUSDT"].evidence.level == "OBSERVED"


# ---------------------------------------------------------------------------
# Minimums, pas de quantité, reliquats
# ---------------------------------------------------------------------------


def test_fee_taken_in_the_received_asset_leaves_about_one_step_of_dust():
    rt = inventory.round_trip(Decimal("90000"), Decimal("10"), step=Decimal("0.00001"), min_notional=Decimal("5"), fee_rate=Decimal("0.001"))
    assert rt.buy_qty == Decimal("0.00011") and rt.buy_notional == Decimal("9.90000")  # arrondi au pas inférieur
    assert rt.sellable_qty == Decimal("0.00010") and rt.dust_qty == Decimal("0.00011") * Decimal("0.999") - Decimal("0.00010")
    assert 0.08 < rt.dust_share < 0.10  # près de 9 % de la position reste immobilisée en reliquat
    bnb = inventory.round_trip(Decimal("90000"), Decimal("10"), step=Decimal("0.00001"), min_notional=Decimal("5"), fee_rate=Decimal("0.001"),
                               fee_in="platform_token")
    assert bnb.dust_qty == 0 and bnb.sellable_qty == bnb.buy_qty  # frais payés en BNB : aucun reliquat
    assert inventory.step_value(Decimal("90000"), Decimal("0.00001")) == Decimal("0.90000")


def test_minimum_order_raises_or_refuses_small_orders():
    up = inventory.round_trip(Decimal("90000"), Decimal("3"), step=Decimal("0.00001"), min_notional=Decimal("5"), fee_rate=Decimal("0.001"))
    assert up.buy_qty == Decimal("0.00006") and up.buy_notional == Decimal("5.40000") and up.sizing_error == pytest.approx(0.8)
    refused = inventory.round_trip(Decimal("90000"), Decimal("3"), step=Decimal("0.00001"), min_notional=Decimal("5"), fee_rate=Decimal("0.001"),
                                   raise_to_minimum=False)
    assert refused.status == "below_minimum" and refused.buy_qty == 0
    stuck = inventory.round_trip(Decimal("1"), Decimal("5"), step=Decimal("1"), min_notional=Decimal("5"), fee_rate=Decimal("0.001"))
    assert stuck.sellable_qty == 0 and stuck.dust_share == pytest.approx(0.999)  # après frais, sous le minimum : rien n'est revendable
    with pytest.raises(ValueError):
        inventory.round_trip(Decimal("1"), Decimal("5"), step=Decimal("1"), min_notional=Decimal("5"), fee_rate=Decimal("0.001"), fee_in="gift")


# ---------------------------------------------------------------------------
# Ruban et estimation des meilleurs prix
# ---------------------------------------------------------------------------


def test_quote_estimate_recovers_a_known_spread_and_refuses_missing_sides():
    t = tape(book_tape())
    q = quote_at(t, T0, lookback_ms=60_000)
    assert q.bid == 99.99 and q.ask == 100.01 and q.mid == pytest.approx(100.0) and q.half_spread_bps == pytest.approx(1.0)
    only_buys = tape([(k * -1000, 100.0, 1.0, True) for k in range(1, 30)])
    assert quote_at(only_buys, T0) is None  # un seul côté : pas d'estimation, pas de supposition
    assert quote_at(t, T0 - 10**9) is None
    crossed = quote_at(tape([(-2000, 99.0, 1.0, True), (-1000, 100.0, 1.0, False)]), T0)
    assert crossed.crossed and crossed.half_spread_bps < 0 and crossed.mid == pytest.approx(99.5)  # gardée, signalée, sans biais en moyenne
    assert limit_price("join", "buy", crossed, 0.01) == (pytest.approx(98.99), False)  # jamais au-dessus du prix vendeur estimé


def test_quote_uses_only_trades_strictly_before_the_decision():
    rows = book_tape() + [(0, 150.0, 5.0, True), (0, 140.0, 5.0, False)]  # transactions AU moment de la décision : invisibles
    assert quote_at(tape(rows), T0).ask == 100.01


def test_binance_rows_are_deduplicated_and_ordered():
    rows = [{"a": 2, "p": "100.0", "q": "1", "T": T0 + 5, "m": False}, {"a": 1, "p": "99.9", "q": "2", "T": T0, "m": True},
            {"a": 2, "p": "100.0", "q": "1", "T": T0 + 5, "m": False}]
    t = from_binance(rows, "X")
    assert len(t) == 2 and t.buyer_initiated.tolist() == [False, True] and t.price.tolist() == [99.9, 100.0]
    with pytest.raises(ValueError):
        Tape("X", np.array([2, 1]), np.array([1.0, 1.0]), np.array([1.0, 1.0]), np.array([True, False]))


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------


def test_market_order_pays_the_ask_and_walks_up_when_needed():
    rows = book_tape(n=60) + [(1500, 100.02, 0.3, True), (1600, 100.05, 1.0, True)]
    ex = market(tape(rows), "buy", 1.0, T0 + 1000)
    assert ex.status == "filled" and ex.fills[0][1] == 100.02 and ex.avg_price == pytest.approx(0.3 * 100.02 + 0.7 * 100.05)
    q = quote_at(tape(rows), T0)
    assert ex.shortfall_bps(q.mid, 0.001, 0.001) == pytest.approx(((0.3 * 100.02 + 0.7 * 100.05) * 1.001 - 100.0) / 100.0 * 1e4)
    none = market(tape(book_tape(n=60, start=-60_000, step_ms=500)), "buy", 1.0, T0 + 1000)  # plus aucune transaction après la décision
    assert none.status == "no_evidence" and none.shortfall_bps(100.0, 0.001, 0.001) is None


def test_passive_conservative_needs_a_trade_through_while_optimistic_accepts_a_touch():
    pre = book_tape(n=60)
    touches = [(2000 + k * 1000, 99.99, 0.4, False) for k in range(10)]  # des ventes AU prix acheteur, jamais en dessous
    t = tape(pre + touches)
    q = quote_at(t, T0)
    cons = passive(t, "buy", 1.0, q, T0 + 1000, 60_000, policy="join", tick=0.01, rule="conservative", fallback="cancel")
    opt = passive(t, "buy", 1.0, q, T0 + 1000, 60_000, policy="join", tick=0.01, rule="optimistic", fallback="cancel")
    assert cons.status == "unfilled" and cons.filled_qty == 0  # la file d'attente devant nous a pu tout absorber
    assert opt.status == "filled" and opt.avg_price == 99.99 and all(f[3] for f in opt.fills)
    through = tape(pre + [(5000, 99.98, 0.01, False)])  # une vente plus bas : notre niveau a été entièrement consommé
    full = passive(through, "buy", 1.0, q, T0 + 1000, 60_000, policy="join", tick=0.01, rule="conservative", fallback="cancel")
    assert full.status == "filled" and full.filled_qty == 1.0 and full.avg_price == 99.99


def test_improving_puts_the_order_alone_at_the_front_of_the_queue():
    wide = book_tape(bid=99.90, ask=100.10)
    t = tape(wide + [(3000, 99.91, 0.6, False), (4000, 99.91, 0.6, False)])
    q = quote_at(t, T0)
    assert limit_price("improve", "buy", q, 0.01) == (pytest.approx(99.91), True)
    ex = passive(t, "buy", 1.0, q, T0 + 1000, 60_000, policy="improve", tick=0.01, rule="conservative", fallback="cancel")
    assert ex.status == "filled" and [f[2] for f in ex.fills] == pytest.approx([0.6, 0.4])  # exécutions partielles successives
    tight = quote_at(tape(book_tape(bid=99.99, ask=100.00)), T0)
    assert limit_price("improve", "buy", tight, 0.01) == (99.99, False)  # écart d'un pas : impossible d'améliorer, on rejoint la file


def test_an_unexecuted_limit_order_saves_nothing_and_the_fallback_pays_the_later_price():
    pre = book_tape(n=60)
    later = [(65_000 + k * 1000, 100.31, 1.0, True) for k in range(30)]  # le prix est parti à la hausse pendant l'attente
    t = tape(pre + [(1500, 100.01, 2.0, True)] + later)
    q = quote_at(t, T0)
    cancel = passive(t, "buy", 1.0, q, T0 + 1000, 60_000, policy="join", tick=0.01, rule="optimistic", fallback="cancel")
    assert cancel.status == "unfilled" and cancel.shortfall_bps(q.mid, 0.001, 0.001) is None  # aucun « gain » compté
    fb = passive(t, "buy", 1.0, q, T0 + 1000, 60_000, policy="join", tick=0.01, rule="optimistic", fallback="market")
    mk = market(t, "buy", 1.0, T0 + 1000)
    assert fb.status == "filled" and fb.fallback_qty == 1.0 and fb.avg_price == 100.31
    assert fb.shortfall_bps(q.mid, 0.001, 0.001) > mk.shortfall_bps(q.mid, 0.001, 0.001)  # attendre a coûté plus cher que le marché


def test_maker_fees_apply_only_to_passive_fills():
    ex = Execution("join", "buy", 2.0, fills=[(T0, 100.0, 1.0, True), (T0, 101.0, 1.0, False)])
    assert ex.fees(0.0, 0.001) == pytest.approx(0.101) and ex.fees(0.001, 0.001) == pytest.approx(0.201)
    sell = Execution("market", "sell", 1.0, fills=[(T0, 99.0, 1.0, False)])
    assert sell.shortfall_bps(100.0, 0.001, 0.001) == pytest.approx((100.0 - 99.0 * 0.999) / 100.0 * 1e4)


def test_evaluate_sample_runs_every_variant_on_the_same_tape():
    rows = book_tape(n=60) + [(2000 + k * 900, 100.01, 1.0, True) for k in range(400)] + [(2400 + k * 900, 99.99, 1.0, False) for k in range(400)]
    r = evaluate_sample(tape(rows), T0, "buy", 10.0, tick=0.01, maker_rate=0.001, taker_rate=0.001, latencies_ms=(1000, 5000, 30_000),
                        timeouts_ms=(30_000, 60_000, 300_000))
    assert len(r.results) == 3 + 3 * 2 * 2 * 2 and r.qty == pytest.approx(0.1)
    assert r.results["market@1000ms"]["shortfall_bps"] == pytest.approx((100.01 * 1.001 - 100.0) / 100.0 * 1e4)
    assert r.results["join/60s/conservative/cancel"]["status"] == "unfilled" and r.results["join/60s/optimistic/cancel"]["status"] == "filled"
    empty = evaluate_sample(tape([(1000, 100.0, 1.0, True)]), T0, "buy", 10.0, tick=0.01, maker_rate=0.001, taker_rate=0.001,
                            latencies_ms=(1000,), timeouts_ms=(30_000,))
    assert empty.quote is None and empty.results == {}


# ---------------------------------------------------------------------------
# Carnets observés (réutilise le calculateur existant du projet)
# ---------------------------------------------------------------------------


def test_book_measure_reuses_the_existing_l2_calculator():
    raw = json.dumps({"bids": [["99.99", "0.5"], ["99.98", "1"]], "asks": [["100.01", "0.05"], ["100.05", "1"]]}).encode()
    m = book.measure(raw, "X", T0, (5.0, 10.0, 1000.0), step="0.0001")
    assert m.half_spread_bps == pytest.approx(1.0) and m.depth_quote_top == pytest.approx(5.0005)
    assert m.impact_bps["5.0"] == pytest.approx(0.0, abs=1e-9) and m.impact_bps["10.0"] > 0 and m.impact_bps["1000.0"] is None
    t = tape(book_tape(bid=99.98, ask=100.02))
    err = book.estimator_error(m, Tape("X", t.ts - T0 + T0, t.price, t.qty, t.buyer_initiated, SHA))
    assert err["error_bps"] == pytest.approx(1.0) and err["observed_half_spread_bps"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Modèles par marché
# ---------------------------------------------------------------------------


def test_market_models_refuse_to_invent_numbers():
    assert markets.model_for("crypto") == "crypto_cex" and markets.model_for("memecoin") == "crypto_cex"
    assert markets.model_for("memecoin", venue="dex") == "memecoin_dex_hypothetical"
    for family in ("equity", "etf", "forex", "commodity", "unknown"):
        with pytest.raises(markets.CostModelUnavailable):
            markets.model_for(family)
    assert all(v.level == "HYPOTHETICAL" for v in markets.dex_memecoin_hypotheses().values())
    assert markets.classify("BTC", "USDT") == "majors_usdt" and markets.classify("DOGE", "USDT") == "memecoin_usdt"
    assert markets.classify("SOL", "USDT") == "others_usdt" and markets.classify("SOL", "EUR") == "eur_direct"
    assert markets.classify("EUR", "USDT") == "fx_eurusdt"
    with pytest.raises(markets.CostModelUnavailable):
        markets.classify("BTC", "TRY")


# ---------------------------------------------------------------------------
# Statistiques et bénéfice net
# ---------------------------------------------------------------------------


def test_cluster_bootstrap_resamples_whole_days():
    x = np.array([1.0, 1.0, 1.0, 10.0, 10.0, 10.0, 5.0, 5.0])
    g = np.array(["a", "a", "a", "b", "b", "b", "c", "c"])
    r = stats.cluster_bootstrap(x, g, reps=500)
    assert r["clusters"] == 3 and r["n"] == 8 and r["ci_low"] <= r["estimate"] <= r["ci_high"]
    assert stats.cluster_bootstrap(np.array([1.0, np.nan]), np.array(["a", "b"]))["ci_low"] is None
    d = stats.paired_difference(np.array([2.0, 3.0, np.nan, 4.0, 5.0, 6.0]), np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0]), np.arange(6))
    assert d["n"] == 5 and d["estimate"] == pytest.approx(3.0) and d["share_a_better"] == 0.0
    assert stats.describe([None, float("nan")]) == {"n": 0}


def test_without_gross_edge_net_is_always_negative_whatever_the_execution():
    for costs in ([30.0] * 50, [5.0] * 50, list(np.linspace(1.0, 60.0, 50))):
        r = benefit.net_edge(0.0, 0.0, costs, trades=100)
        assert r["net_mean_bps"] == pytest.approx(-np.mean(costs), rel=0.02) and r["p_net_positive"] == 0.0 and r["net_ci_high"] < 0
    edge = benefit.net_edge(50.0, 0.0, [30.0] * 20, trades=50)
    assert edge["net_mean_bps"] == pytest.approx(20.0) and edge["p_net_positive"] == 1.0 and edge["break_even_gross_bps"] == 30.0
    noisy = benefit.net_edge(35.0, 500.0, [30.0] * 20, trades=20, gross_mean_se_bps=20.0)
    assert 0.3 < noisy["p_net_positive"] < 0.8  # un petit avantage incertain ne garantit rien
    assert benefit.annual_cost_eur(40.0, 52, 10.0) == pytest.approx(2.08)
    rows = benefit.grid([40.0] * 30, gross_edges_bps=(0.0, 100.0), trades_per_year=(12,))
    assert rows[0]["annual_net_eur"] < 0 < rows[1]["annual_net_eur"]
    with pytest.raises(ValueError):
        benefit.net_edge(1.0, 0.0, [], trades=1)


# ---------------------------------------------------------------------------
# Audit du moteur existant
# ---------------------------------------------------------------------------


def test_audit_pins_the_existing_assumptions():
    f = {x["component"]: x for x in audit.findings()}
    assert f["commission"]["value"] == {"fee_bps": 10.0} and f["actif des frais"]["value"] == {"fee_asset": "quote"}
    assert f["glissement"]["value"]["slippage_bps"] == 5.0 and f["glissement (protocole gelé v2)"]["value"]["slippage_bps"] == 10.0
    assert f["ordre à cours limité (simulation sur bougies)"]["value"]["touch_fills"] is True
    assert f["écart achat/vente"]["value"]["half_spread_bps_without_book"] == 0.0 and f["conversion de devise"]["value"]["fx_cost_bps"] == 5.0
    assert f["pas de quantité par défaut"]["value"] == {"amount_step": 1e-08, "min_notional": 5.0}
    assert all(x["level"] == "HYPOTHETICAL" and x["measured_by"] for x in f.values())


# ---------------------------------------------------------------------------
# Contrat et traduction pour ALPHA FACTORY v2
# ---------------------------------------------------------------------------


def fake_summary(half=2.0, slip=0.5, fx_half=1.0):
    def cls(h, s):
        return {"half_spread_open": {"estimate": h, "ci_low": h * 0.8, "ci_high": h * 1.2, "n": 120},
                "half_spread_all": {"n": 240, "p90": h * 3}, "slippage_10eur_1s": {"estimate": s, "ci_low": -s, "ci_high": s * 2, "n": 240},
                "slippage_30eur_5s": {"n": 240, "p90": s * 6}, "no_quote_share": 0.01}
    return {"classes": {"majors_usdt": cls(half, slip), "others_usdt": cls(half * 3, slip * 2), "memecoin_usdt": cls(half * 4, slip),
                        "eur_direct": cls(half * 2, slip), "fx_eurusdt": cls(fx_half, 0.2)}, "confirmatory": {"demonstrated": False}}


def build_fake(summary=None, probes=None):
    plan = {"plan_id": "p", "latencies_ms": [1000], "rules": {"execution_confirmatory": "r", "conversion_alternative": "a"}}
    return contract.build(plan=plan, plan_sha256=SHA, summary=summary or fake_summary(), probes=probes or {}, routing={}, stress={}, inventory={},
                          bar_touch={}, fees=venues.binance_spot_vip0(), filters_sha256=SHA, filters_as_of="2026-11-16", coverage={},
                          created_at="2026-11-16T00:00:00Z")


def test_contract_follows_the_preregistered_rules_and_keeps_evidence_levels():
    c = build_fake()
    m = c["classes"]["majors_usdt"]
    assert m["half_spread"]["value"] == pytest.approx(2.4) and m["half_spread"]["level"] == "ESTIMATED"  # borne haute, pas la moyenne
    assert m["slippage"]["value"] == pytest.approx(1.0) and m["half_spread_stress"]["value"] == pytest.approx(6.0)
    assert c["fees"]["taker"]["level"] == "HYPOTHETICAL" and c["execution"]["recommended"] == "market@1000ms"
    assert c["conversion"]["per_order"]["value"] == pytest.approx(1.2 + 0.4 + 10.0) and c["conversion"]["per_order"]["level"] == "HYPOTHETICAL"
    assert c["not_covered"] == ["equity", "etf", "forex", "commodity"] and c["can_trade"] is False
    negative = fake_summary()
    negative["classes"]["majors_usdt"]["slippage_10eur_1s"].update(ci_low=-5.0, ci_high=-3.0)
    assert build_fake(negative)["classes"]["majors_usdt"]["slippage"]["value"] == 0.0  # un glissement « favorable » n'est jamais compté
    corrected = build_fake(probes={"estimator_error": {"majors_usdt": {"mean_error": {"ci_low": -0.7, "ci_high": -0.2}}}})
    assert corrected["classes"]["majors_usdt"]["half_spread"]["value"] == pytest.approx(2.4 + 0.7)  # sous-estimation corrigée
    missing = fake_summary()
    del missing["classes"]["fx_eurusdt"]
    with pytest.raises(contract.ContractError):
        build_fake(missing)


def test_the_export_is_readable_by_alpha_factory_v2_and_never_cheaper_in_stress():
    export = contract.to_alpha_lab(build_fake())
    hyp = labcosts.parse(export, "x")  # le format exact attendu par le laboratoire ALPHA FACTORY v2
    p, s = hyp.scenario(hyp.primary), hyp.scenario(hyp.stress)
    assert p.half_spread_major_bps == pytest.approx(2.4) and p.half_spread_other_bps == pytest.approx(9.6)  # max(autres, memecoins)
    assert p.slippage_bps == pytest.approx(2.0) and p.fx_bps == pytest.approx(11.6) and p.fee_bps == 10.0
    assert s.round_trip("BTC") >= p.round_trip("BTC") and s.round_trip("X") >= p.round_trip("X")
    assert "PROPOSÉ" in hyp.status and hyp.gross == "brut"


def test_contract_is_write_once(tmp_path):
    c = build_fake()
    out = contract.write(tmp_path, c, contract.to_alpha_lab(c))
    assert set(out) == {"cost-contract-v1.json", "alpha-lab-cost-hypotheses-proposal.json"}
    with pytest.raises(contract.ContractError, match="immuable"):
        contract.write(tmp_path, c, contract.to_alpha_lab(c))


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

FORBIDDEN = ("sentinelle.execution.paper", "sentinelle.execution.positions", "sentinelle.execution.live", "sentinelle.execution.base",
             "sentinelle.execution.book_execution", "sentinelle.services", "sentinelle.api", "sentinelle.notify", "sentinelle.market.sources",
             "sentinelle.storage", "ccxt", "httpx", "requests", "socket", "urllib", "aiohttp", "subprocess", "sqlite3", "threading", "asyncio")


@pytest.mark.parametrize("path", sorted((ROOT / "sentinelle" / "costs").glob("*.py")), ids=lambda p: p.name)
def test_cost_modules_cannot_reach_a_broker_or_the_network(path):
    package = {2: "sentinelle.", 1: "sentinelle.costs."}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = (package.get(node.level, "") if node.level else "") + (node.module or "")
            names = [base] + [f"{base.rstrip('.')}.{a.name}" for a in node.names]
        for name in names:
            assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN), f"{path.name} importe {name}"
        if isinstance(node, (ast.Attribute, ast.Name)):
            ident = node.attr if isinstance(node, ast.Attribute) else node.id
            assert ident not in ("place_order", "create_order", "submit_order", "cancel_order", "fetch_balance", "withdraw"), path.name


def test_loading_the_cost_engine_loads_no_broker_or_network_module():
    code = ("import sys, sentinelle.costs.measure, sentinelle.costs.contract, sentinelle.costs.audit;"
            "bad=sorted(m for m in sys.modules if m.split('.')[0] in ('ccxt','httpx','requests','sqlite3','fastapi')"
            " or m.startswith(('sentinelle.execution.paper','sentinelle.execution.positions','sentinelle.execution.live','sentinelle.services',"
            "'sentinelle.api','sentinelle.storage','tools.cost_fetch')));print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
