"""COST OPTIMIZER — chaîne complète sur un marché synthétique à écart connu : mesure, comparaison, contrat."""
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.lab import costs as labcosts
from sentinelle.costs import contract, measure, venues
from tools import cost_fetch

DAY = 86_400_000
D0 = 19_000  # 2022-01-08
SPREADS = {"BTCUSDT": 0.6, "ETHUSDT": 1.0, "SOLUSDT": 4.0, "DOGEUSDT": 6.0, "BTCEUR": 3.0, "EURUSDT": 0.8}  # demi-écarts vrais (pb)
CLASSES = {"BTCUSDT": "majors_usdt", "ETHUSDT": "majors_usdt", "SOLUSDT": "others_usdt", "DOGEUSDT": "memecoin_usdt", "BTCEUR": "eur_direct",
           "EURUSDT": "fx_eurusdt"}
PRICES = {"BTCUSDT": 40_000.0, "ETHUSDT": 3_000.0, "SOLUSDT": 100.0, "DOGEUSDT": 0.15, "BTCEUR": 37_000.0, "EURUSDT": 1.08}


def synthetic_rows(symbol, start, end, seed):
    """Transactions alternées au prix vendeur et au prix acheteur autour d'un milieu qui varie au hasard."""
    rng = np.random.default_rng(seed)
    half = SPREADS[symbol] / 1e4
    t, mid, rows, agg = start, PRICES[symbol], [], seed * 10**6
    while t < end:
        mid *= 1 + rng.normal(0, 0.00005)
        buy = bool(rng.random() < 0.5)
        price = mid * (1 + half) if buy else mid * (1 - half)
        rows.append([int(t), f"{price:.8f}", f"{rng.exponential(2.0) * 50 / PRICES[symbol]:.8f}", not buy, agg])
        agg += 1
        t += int(rng.integers(200, 1500))
    return rows


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("cost")
    samples = []
    for k in range(24):
        day = D0 + k * 9
        group = f"route-{k}"
        for sym in SPREADS:
            for kind, off in (("open", 60_000), ("intraday", 40_000_000 + k * 1000)):
                t = day * DAY + off
                samples.append({"id": f"{sym}-{k}-{kind}", "class": CLASSES[sym], "symbol": sym, "day": f"2022-{1 + k // 3:02d}-{1 + (k % 3) * 9:02d}",
                                "kind": kind, "t_ms": t, "start_ms": t - 60_000, "end_ms": t + 420_000,
                                "split": "estimation" if k < 12 else "validation", "sizes_quote": [5.0, 10.0, 30.0], "eur_to_quote": 1.0,
                                "file_group": group})
    plan = {"plan_id": "synthetic", "samples": samples, "sizes_eur": [5.0, 10.0, 30.0], "sides": ["buy", "sell"],
            "latencies_ms": [1000, 5000, 30000], "timeouts_ms": [30000, 60000, 300000],
            "rules": {"execution_confirmatory": "r", "conversion_alternative": "a"}}
    (root / "samples").mkdir()
    for i, s in enumerate(samples):
        body = {"sample": s, "source": "synthetic", "zip_sha256": "cd" * 32, "official_sha256": "cd" * 32, "checksum_ok": True,
                "rows": synthetic_rows(s["symbol"], s["start_ms"], s["end_ms"], i + 1)}
        (root / "samples" / f"{s['id']}.json.gz").write_bytes(gzip.compress(json.dumps(body).encode()))
    bad = dict(samples[0], id="corrupt")
    plan["samples"].append(bad)
    (root / "samples" / "corrupt.json.gz").write_bytes(gzip.compress(json.dumps({"sample": bad, "checksum_ok": False, "rows": []}).encode()))
    fees = venues.binance_spot_vip0()
    tapes, status = measure.load_samples(root, plan)
    rows = measure.evaluate(plan, tapes, {}, fees)
    summary = measure.summarize(plan, rows, fees)
    return plan, rows, summary, status, fees


def test_collection_status_counts_checksum_failures_and_never_uses_them(world):
    _, rows, _, status, _ = world
    assert status["checksum_failed"] == 1 and status["archived"] == 24 * 6 * 2 and status["missing"] == 0
    assert "corrupt" not in {r["id"] for r in rows}


def test_measured_half_spreads_recover_the_true_ones(world):
    _, _, summary, _, _ = world
    for cls, expected in (("majors_usdt", (0.6, 1.0)), ("others_usdt", (4.0, 4.0)), ("memecoin_usdt", (6.0, 6.0)), ("eur_direct", (3.0, 3.0)),
                          ("fx_eurusdt", (0.8, 0.8))):
        hs = summary["classes"][cls]["half_spread_open"]
        truth = sum(expected) / 2
        assert hs["ci_low"] - 0.3 <= truth <= hs["ci_high"] + 0.3, cls  # l'intervalle couvre l'écart vrai (à 0,3 pb près)
        assert abs(hs["estimate"] - truth) < 0.25 * truth + 0.3, cls
        assert summary["classes"][cls]["no_quote_share"] == 0.0 and summary["classes"][cls]["crossed_share"] < 0.5


def test_market_cost_is_half_spread_plus_fee_and_slippage_is_small(world):
    _, _, summary, _, _ = world
    m = summary["classes"]["majors_usdt"]
    assert 10.0 + 0.4 < m["market_shortfall_10eur_1s"]["estimate"] < 10.0 + 1.5  # commission 10 pb + demi-écart vrai (0,6 à 1)
    assert abs(m["slippage_10eur_1s"]["estimate"]) < 1.0 and m["market_no_evidence_share"] == 0.0


def test_one_preregistered_confirmatory_comparison_chosen_on_estimation_and_tested_on_validation(world):
    _, _, summary, _, _ = world
    c = summary["confirmatory"]
    assert c["chosen"] in c["candidates"] and all(v.endswith("/conservative/market") for v in c["candidates"])
    assert set(c["estimation_mean_shortfall"]) == set(c["candidates"]) and c["reference"] == "market@1000ms"
    assert isinstance(c["demonstrated"], bool) and c["validation_market_minus_chosen"]["n"] > 0
    assert c["validation_optimistic_bound"]["estimate"] >= c["validation_market_minus_chosen"]["estimate"] - 1e-9  # l'optimiste borne au-dessus


def test_policy_table_reports_fills_and_never_counts_unfilled_orders(world):
    _, _, summary, _, _ = world
    pol = summary["policies"]
    cancel = pol["join/30s/conservative/cancel"]
    assert 0 <= cancel["passive_ratio"] <= cancel["fill_ratio"] <= 1
    assert pol["join/30s/optimistic/cancel"]["passive_ratio"] >= cancel["passive_ratio"]  # l'optimiste remplit au moins autant
    assert pol["market@1000ms"]["fill_ratio"] == pytest.approx(1.0)


def test_routing_compares_direct_euro_pairs_with_two_orders_via_usdt(world):
    plan, rows, _, _, _ = world
    r = measure.routing(plan, rows)
    btc = r["BTC"]
    assert btc["pairs"] == 24 and btc["via_minus_direct"]["estimate"] > 0  # deux commissions de 10 pb contre une : le détour coûte plus
    assert btc["via_usdt"]["mean"] > btc["direct"]["mean"]


def test_inventory_effects_use_observed_filters(world):
    _, rows, _, _, fees = world
    from decimal import Decimal
    f = {"BTCUSDT": venues.MarketFilters("BTCUSDT", "BTC", "USDT", "TRADING", Decimal("0.01"), Decimal("0.00001"), Decimal("0.00001"),
                                         Decimal("0.00001"), Decimal("5"), True, venues.binance_spot_vip0().taker)}
    inv = measure.inventory_effects(rows, f, fees)
    assert set(inv) == {"majors_usdt"} and inv["majors_usdt"]["n"] == 24
    assert 0.03 < inv["majors_usdt"]["dust_share"]["median"] < 0.06  # 1 pas = 0,40 USDT à 40 000 : environ 4 % d'un ordre de 10


def test_contract_from_measures_and_export_for_alpha_factory_v2(world, tmp_path):
    plan, rows, summary, _, fees = world
    c = contract.build(plan=plan, plan_sha256="ef" * 32, summary=summary, probes={}, routing=measure.routing(plan, rows), stress={}, inventory={},
                       bar_touch={}, fees=fees, filters_sha256="ef" * 32, filters_as_of="2026-11-16", coverage=measure.quote_coverage(rows),
                       created_at="2026-11-16T00:00:00Z")
    assert set(c["classes"]) == {"majors_usdt", "others_usdt", "memecoin_usdt", "eur_direct", "fx_eurusdt"} and not c["missing_classes"]
    assert c["classes"]["majors_usdt"]["half_spread"]["value"] >= summary["classes"]["majors_usdt"]["half_spread_open"]["estimate"]
    assert c["execution"]["passive_improvement_demonstrated"] == summary["confirmatory"]["demonstrated"]
    hyp = labcosts.parse(contract.to_alpha_lab(c), "x")
    assert hyp.scenario(hyp.primary).half_spread_other_bps == pytest.approx(c["classes"]["memecoin_usdt"]["half_spread"]["value"], abs=1e-3)
    assert hyp.scenario(hyp.stress).round_trip("X") >= hyp.scenario(hyp.primary).round_trip("X")


def test_bulk_extraction_keeps_only_the_windows_and_handles_microseconds():
    import io
    import zipfile

    lines = ["agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker,is_best_match"]
    for k in range(10):
        lines.append(f"{k},100.{k},1,{k},{k},{(1_700_000_000_000 + k * 1000) * 1000},{'True' if k % 2 else 'False'},True")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X.csv", "\n".join(lines) + "\n")
    out = cost_fetch.extract_windows(buf.getvalue(), [("a", 1_700_000_002_000, 1_700_000_005_000), ("b", 1_700_000_008_000, 1_700_000_020_000)])
    assert [r[0] for r in out["a"]] == [1_700_000_002_000, 1_700_000_003_000, 1_700_000_004_000] and len(out["b"]) == 2
    assert out["a"][1][3] is True and out["a"][0][1] == "100.2"
    assert cost_fetch.to_ms(1_700_000_000_000) == 1_700_000_000_000 and cost_fetch.to_ms(1_700_000_000_000_123) == 1_700_000_000_000


def test_bulk_fetch_refuses_files_whose_official_checksum_does_not_match(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X.csv", "1,100.0,1,1,1,1700000001000,False,True\n")
    payload = buf.getvalue()

    def get(url, params):
        if url.endswith(".CHECKSUM"):
            return b"00" * 32 + b"  X.zip\n", 200  # somme publiée différente
        return payload, 200

    plan = {"samples": [{"id": "s1", "symbol": "XUSDT", "day": "2023-11-14", "start_ms": 1_700_000_000_000, "end_ms": 1_700_000_002_000}]}
    r = cost_fetch.fetch_bulk(tmp_path, plan, get, max_files=5, max_bytes=10**9)
    body = json.loads(gzip.decompress((tmp_path / "samples" / "s1.json.gz").read_bytes()))
    assert r["files_failed"] == 1 and body["checksum_ok"] is False and body["rows"] == []
    r2 = cost_fetch.fetch_bulk(tmp_path, plan, get, max_files=5, max_bytes=10**9)
    assert r2["files_done"] == 1  # déjà archivé (même en échec) : rien n'est retéléchargé


def test_client_is_bounded_and_restricted_to_public_read_paths():
    calls = []
    c = cost_fetch.Client(lambda url, params: (calls.append(url) or (b"[]", 200)), budget=2, pause=0)
    c.api("depth", {"symbol": "X"})
    with pytest.raises(ValueError):
        c.api("order", {"symbol": "X"})  # aucun chemin d'ordre ou de compte
    c.api("aggTrades", {"symbol": "X"})
    with pytest.raises(RuntimeError, match="Budget"):
        c.api("depth", {"symbol": "X"})
    assert len(calls) == 2 and all(u.startswith(cost_fetch.API) for u in calls)
    assert Path(cost_fetch.__file__).read_text(encoding="utf-8").count("apiKey") == 0


def test_duplicate_identifiers_are_used_once_and_overwritten_windows_are_never_misattributed(tmp_path):
    def sample(t):
        return {"id": "dup", "class": "majors_usdt", "symbol": "BTCUSDT", "day": "2022-01-08", "kind": "intraday", "t_ms": t, "start_ms": t - 60_000,
                "end_ms": t + 420_000, "split": "estimation", "sizes_quote": [5.0, 10.0, 30.0], "eur_to_quote": 1.0, "file_group": "g"}
    a, b = sample(D0 * DAY + 3_600_000), sample(D0 * DAY + 7_200_000)
    plan = {"samples": [a, b, dict(b)], "sizes_eur": [5.0, 10.0, 30.0], "sides": ["buy"], "latencies_ms": [1000, 5000, 30000],
            "timeouts_ms": [30000]}
    (tmp_path / "samples").mkdir()
    body = {"sample": b, "checksum_ok": True, "zip_sha256": "cd" * 32, "rows": synthetic_rows("BTCUSDT", b["start_ms"], b["end_ms"], 3)}
    (tmp_path / "samples" / "dup.json.gz").write_bytes(gzip.compress(json.dumps(body).encode()))  # seule la fenêtre de b a survécu
    tapes, status = measure.load_samples(tmp_path, plan)
    assert status["id_collision"] == 1 and status["duplicate_id"] == 1 and status["archived"] == 1
    rows = measure.evaluate(plan, tapes, {}, venues.binance_spot_vip0())
    assert {r["t"] for r in rows} == {b["t_ms"]} and len(rows) == 3  # b une seule fois (3 tailles), jamais a avec les données de b


def test_stress_states_split_market_costs_by_states_known_the_day_before(tmp_path):
    import hashlib

    from sentinelle.costs import plan as planmod

    closes = [100.0 * 1.001 ** k for k in range(40)]
    closes[38] = closes[37] * 0.88  # chute de 12 % la veille du dernier jour mesuré
    csv = "open_time,open,high,low,close\n" + "".join(f"{(D0 + k) * DAY},1,1,1,{c}\n" for k, c in enumerate(closes))
    raw = gzip.compress(csv.encode())
    (tmp_path / "klines").mkdir()
    (tmp_path / "klines" / "BTCUSDT.csv.gz").write_bytes(raw)
    manifest = {"symbols": {"BTCUSDT": {"sha256": hashlib.sha256(raw).hexdigest()}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    plan = {"latencies_ms": [0]}

    def row(day, shortfall, **kw):
        r = {"symbol": "BTCUSDT", "day": planmod._date(day), "size_index": measure.PRIMARY_SIZE, "side": "buy",
             "quote": {"half_spread_bps": 0.5}, "results": {"market@0ms": {"shortfall_bps": shortfall}}}
        return r | kw

    rows = [row(D0 + 31 + k, 1.0 + k) for k in range(9)]  # 31 clôtures connues avant chacun de ces jours
    rows += [row(D0 + 5, 50.0), row(D0 + 35, 50.0, side="sell"), row(D0 + 35, 50.0, quote=None), row(D0 + 35, 50.0, size_index=0)]
    out = measure.stress_states(plan, rows, tmp_path)
    assert out["after_10pct_day"]["samples"] == 1 and out["after_10pct_day"]["shortfall"]["mean"] == 9.0  # seul le lendemain du krach
    for state in ("vol30", "prior_day_move"):
        assert out[state]["high"]["n"] + out[state]["rest"]["n"] == 9  # historique insuffisant, vente, sans cotation, autre taille : exclus
        assert out[state]["high"]["max"] == 9.0
    manifest["symbols"]["BTCUSDT"]["sha256"] = "0" * 64  # archive altérée : aucun état n'est calculé
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert measure.stress_states(plan, rows, tmp_path) == {"after_10pct_day": {"samples": 0, "shortfall": {"n": 0}, "half_spread": {"n": 0}}}
