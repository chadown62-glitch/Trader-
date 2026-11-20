"""MEMECOIN ALPHA ENGINE (mission 56) — veto, causalité, coûts, contrôle en avant, stress (données SYNTHÉTIQUES, aucun réseau)."""
import re
from pathlib import Path

import numpy as np
import pytest

from sentinelle import safety
from sentinelle.dmh.net import PublicClient, RequestNotAllowed
from sentinelle.memecoin import CAN_TRADE, PAPER_ONLY, collect as C, guardian as G, lab as L

ROOT = Path(__file__).resolve().parent.parent
MINT = "ErpWP9KFTMYHNfS6gdKVPucvAZrPUQYwgFiB33GYpump"
POOL = "6PFin3yL7a3LcaZSTBwPc138fFRJpx69mTaN9NJq4kzf"
T0 = 1_794_900_000_000


def pool(**kw):
    p = {"kind": "pool", "pool": POOL, "mint": MINT, "dex": "pumpswap", "observed_ms": T0, "created_ms": T0 - 3 * 3600_000,
         "reserve_usd": 80_000.0, "price_usd": 1e-4, "price_change_pct": {"m5": 2.0},
         "tx": {"h1": {"buys": 40, "sells": 35, "buyers": 30, "sellers": 25}}}
    return p | kw


def sec(**kw):
    s = {"kind": "security", "mint": MINT, "observed_ms": T0 + 60_000,
         "mint_info": {"exists": True, "mint_authority": None, "freeze_authority": None, "transfer_fee": False, "transfer_hook": False,
                       "permanent_delegate": False},
         "rugcheck": {"rugged": False, "risks": [], "top1_share": 0.05, "top10_share": 0.20, "creator_share": 0.0, "insider_networks": 0,
                      "launchpad": "pump_fun", "lp_locked_pct_max": 100.0, "transfer_fee_pct": 0.0},
         "round_trip": {"loss": 0.03, "sell_unavailable": False}, "dexscreener": {"liquidity_usd": 82_000.0}}
    return s | kw


# -- verrou -------------------------------------------------------------------------------------------------------------
def test_the_engine_is_paper_only_and_has_no_way_to_sign_or_send():
    assert CAN_TRADE is False and PAPER_ONLY is True
    for path in (ROOT / "sentinelle" / "memecoin").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sendTransaction|signTransaction|Keypair|private_?key|seed_?phrase|/swap/v2/execute|taker", text), path


def test_geckoterminal_is_read_only_and_jupiter_execution_stays_forbidden():
    PublicClient.check("api.geckoterminal.com", "/api/v2/networks/solana/new_pools")
    PublicClient.check("api.geckoterminal.com", f"/api/v2/networks/solana/pools/{POOL}/ohlcv/minute")
    with pytest.raises(RequestNotAllowed):
        PublicClient.check("api.geckoterminal.com", "/api/v2/networks/eth/new_pools")
    with pytest.raises(safety.RealTradingForbidden):
        PublicClient.check("api.jup.ag", "/swap/v2/execute")
    with pytest.raises(safety.RealTradingForbidden):
        PublicClient.check("api.jup.ag", "/swap/v2/order", {"taker": "x"})


# -- module A -----------------------------------------------------------------------------------------------------------
def test_new_pool_parsing_keeps_identity_timing_and_activity():
    item = {"attributes": {"address": POOL, "name": "T / SOL", "pool_created_at": "2026-11-17T08:54:46Z", "reserve_in_usd": "1234.5",
                           "base_token_price_usd": "0.001", "base_token_price_native_currency": "0.00001", "volume_usd": {"h1": "10"},
                           "price_change_percentage": {"m5": "-3"}, "transactions": {"h1": {"buys": 3, "sells": 1, "buyers": 2, "sellers": 1}}},
            "relationships": {"base_token": {"data": {"id": f"solana_{MINT}"}}, "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}},
                              "dex": {"data": {"id": "pump-fun"}}}}
    p = C.parse_pool(item, T0)
    assert p["mint"] == MINT and p["dex"] == "pump-fun" and p["reserve_usd"] == 1234.5 and p["tx"]["h1"]["buys"] == 3
    assert p["created_ms"] == 1794905686000 and p["price_native"] == 1e-5
    assert C.parse_pool({"attributes": {"address": POOL}, "relationships": {"base_token": {"data": {"id": "eth_0x1"}}}}, T0) is None


def test_source_quality_reports_delay_completeness_and_cross_source_gaps():
    q = C.quality([pool(), pool(pool="P2", mint="M2", reserve_usd=None, created_ms=None), sec()])
    assert q["completeness"]["reserve_usd"] == 0.5 and q["detection_delay_minutes"]["p50"] == 180.0
    assert q["liquidity_gap_vs_dexscreener"]["n"] == 1 and q["liquidity_gap_vs_dexscreener"]["p50"] == pytest.approx(0.025)


# -- module B -----------------------------------------------------------------------------------------------------------
def test_a_clean_token_passes_but_passing_promises_nothing():
    v = G.assess(pool(), sec())
    assert v["status"] == G.PASS and "ne garantit ni" in v["note"]


@pytest.mark.parametrize("change, reason", [
    (dict(mint_info={"exists": True, "mint_authority": "X"}), "émission encore possible"),
    (dict(mint_info={"exists": True, "freeze_authority": "X"}), "gel des comptes"),
    (dict(mint_info={"exists": True, "transfer_hook": True}), "Token-2022"),
    (dict(rugcheck={"rugged": True, "risks": []}), "rugged"),
    (dict(rugcheck={"risks": [{"name": "Creator history of rugged tokens", "level": "danger"}]}), "Creator history"),
    (dict(rugcheck={"risks": [], "top10_share": 0.6}), "10 plus gros détenteurs"),
    (dict(rugcheck={"risks": [], "creator_share": 0.3}), "le créateur détient"),
    (dict(rugcheck={"risks": [], "launchpad": "raydium", "lp_locked_pct_max": 0.0}), "liquidité verrouillée"),
    (dict(round_trip={"sell_unavailable": True}), "aucune route de revente"),
    (dict(round_trip={"loss": 0.4, "sell_unavailable": False}), "aller-retour immédiat"),
])
def test_each_danger_signal_alone_vetoes(change, reason):
    v = G.assess(pool(), sec(**change))
    assert v["status"] == G.VETO and any(reason in r for r in v["veto"]), v


def test_thin_liquidity_and_buys_without_sells_veto():
    assert G.assess(pool(reserve_usd=900.0), sec())["status"] == G.VETO
    v = G.assess(pool(tx={"h1": {"buys": 50, "sells": 0, "buyers": 40, "sellers": 0}}), sec())
    assert v["status"] == G.VETO and any("aucune vente" in r for r in v["veto"])


@pytest.mark.parametrize("p, s, reason", [
    (pool(), None, "aucun contrôle"),
    (pool(), sec(mint_info=None), "lecture du mint"),
    (pool(), sec(rugcheck=None), "rapport de contrôle"),
    (pool(), sec(round_trip=None), "revendabilité non mesurable"),
    (pool(created_ms=T0 - 5 * 60_000), sec(), "trop récent"),
    (pool(price_change_pct={"m5": 80.0}), sec(), "en 5 minutes"),
    (pool(), sec(dexscreener={"liquidity_usd": 10_000.0}), "contradictoire"),
])
def test_missing_contradictory_or_weak_data_abstains_never_passes(p, s, reason):
    v = G.assess(p, s)
    assert v["status"] == G.ABSTAIN and any(reason in r for r in v["missing"] + v["weak"]), v


# -- module C : causalité et coûts --------------------------------------------------------------------------------------
def grid(prices, volume=1000.0, t0=T0):
    prices = np.asarray(prices, float)
    t = t0 + 60000 * np.arange(len(prices))
    o = np.r_[prices[0], prices[:-1]]
    return np.column_stack([t, o, np.maximum(o, prices) * 1.002, np.minimum(o, prices) * 0.998, prices, np.full(len(prices), volume)])


@pytest.mark.parametrize("name", L.STRATEGIES)
def test_signals_are_causal(name):
    rng = np.random.default_rng(2)
    px = np.cumprod(1 + rng.normal(0.001, 0.03, 400))
    g = grid(px, volume=1.0)
    g[:, 5] = rng.lognormal(3, 1, len(px))
    full = L.signals(name, g)
    for k in (100, 250, 399):
        assert (L.signals(name, g[:k + 1]) == full[:k + 1]).all(), (name, k)


def test_dense_grid_fills_quiet_minutes_with_the_last_price_and_no_volume():
    g = L.dense([[T0, 1, 1, 1, 1, 5], [T0 + 180000, 2, 2, 2, 2, 7]])
    assert g.shape == (4, 6) and g[1, 4] == 1 and g[1, 5] == 0 and g[3, 4] == 2


def test_a_flat_market_loses_exactly_the_costs():
    p = pool()
    g = grid(np.r_[np.full(70, 1.0), np.full(10, 1.2), np.full(100, 1.2)])  # +20 % puis plat : une entrée momentum, sortie à la durée
    g[70:75, 5] = 5000.0  # le saut se fait avec du volume
    tr = L.simulate_pool("momentum_15", g, p, 72.0, failed_p=0.0, rng=np.random.default_rng(0))
    assert len(tr) == 1 and tr[0]["gross_return"] == pytest.approx(0.0, abs=1e-9)
    t = tr[0]
    assert t["net_usd"] < 0 and t["net_usd"] == pytest.approx(-(t["fees_usd"] + t["impact_usd"] + t["mev_usd"] + t["network_usd"]), rel=0.02)


def test_impact_and_reserve_model():
    assert L.impact(11, 22) == pytest.approx(0.5) and L.impact(11, None) == 1.0
    p = pool(reserve_usd=10_000.0, price_usd=1.0)
    assert L.reserve_at(4.0, p, "central") == pytest.approx(20_000.0) and L.reserve_at(4.0, p, "prudent") == 10_000.0
    assert L.reserve_at(100.0, p, "central") == 30_000.0  # jamais plus de 3 fois la réserve actuelle


def test_split_reserves_the_most_recent_pools_untouched():
    ps = [pool(pool=f"P{i}", created_ms=T0 + i) for i in range(10)]
    s = L.split(ps)
    assert [len(s[k]) for k in ("train", "dev", "reserved")] == [6, 2, 2] and s["reserved"] == ["P8", "P9"]


# -- contrôle en avant et stress ----------------------------------------------------------------------------------------
def test_forward_check_reads_outcomes_only_after_the_decision():
    p1, p2 = pool(pool="A", mint="A"), pool(pool="B", mint="B")
    verdicts = {"A": {"status": G.VETO}, "B": {"status": G.PASS}}
    before = [[T0 - 60000, 1, 1, 1, 1.0, 1]]
    follow = {"A": {"candles": before + [[T0 + 60000, 1, 1, 0.02, 0.03, 1]]}, "B": {"candles": before + [[T0 + 60000, 1, 1.1, 1, 1.05, 1]]}}
    fc = L.forward_check([p1, p2], verdicts, follow, 72.0)
    assert fc[G.VETO]["collapsed_share"] == 1.0 and fc[G.PASS]["collapsed_share"] == 0.0
    assert fc[G.PASS]["median_return"] == pytest.approx(0.05) and fc[G.PASS]["mean_net_after_costs"] < 0.05


def test_stress_a_rug_while_held_is_not_saved_by_the_stop_and_blind_sources_abstain():
    s = L.stress_scenarios(72.0)
    ref, rug = s["référence (tendance régulière)"], s["retrait de liquidité pendant la position"]
    assert ref["trades"] >= 1 and rug["trades"] >= 1 and rug["mean_net_return"] < -0.5  # le stop ne protège pas d'une chute en une minute
    assert s["liquidité ÷100 (600 $)"]["mean_net_return"] < ref["mean_net_return"]
    assert s["frais du réseau ×100 (congestion)"]["mean_net_return"] < ref["mean_net_return"]
    assert s["taxe de transfert activée (Token-2022)"]["guardian"] == G.VETO
    assert s["toutes les sources de sécurité en panne"]["guardian"] == G.ABSTAIN


def test_the_lab_runs_end_to_end_on_a_synthetic_capture_and_never_touches_the_reserved_pools():
    rng = np.random.default_rng(9)
    cap = [{"kind": "sol_price", "usd": 72.0, "observed_ms": T0}]
    for i in range(10):
        pid = f"{i:02d}" + POOL[2:]
        px = np.cumprod(1 + rng.normal(0.002, 0.04, 300)) * 1e-4
        cap.append(pool(pool=pid, mint=pid, created_ms=T0 - (20 - i) * 3600_000, price_usd=float(px[-1])))
        g = grid(px)
        cap.append({"kind": "ohlcv", "pool": pid, "candles": g.tolist(), "observed_ms": T0})
        cap.append(sec(mint=pid))
    cap.append({"kind": "collection", "sources": {}, "quality": {}})
    rep = L.run(cap)
    assert rep["data"]["split"] == {"train": 6, "dev": 2, "reserved": 2} and rep["can_trade"] is False
    assert rep["guardian"]["counts"][G.PASS] == 10 and isinstance(L.markdown(rep), str)
    assert rep["data"]["sol_usd"] == 72.0


def test_the_veto_filter_is_causal_only_trades_opened_after_the_decision_count():
    rng = np.random.default_rng(4)
    px = np.r_[np.full(70, 1.0), np.linspace(1.0, 1.3, 10), np.cumprod(1 + rng.normal(0.001, 0.02, 300)) * 1.3] * 1e-4
    g = grid(px)
    g[70:80, 5] = 5000.0  # un signal momentum vers la minute 75, puis d'autres possibles plus tard
    pid = POOL
    decided = int(g[200, 0])  # le veto décide à la minute 200 : les entrées d'avant ne comptent pas
    cap = [{"kind": "sol_price", "usd": 72.0, "observed_ms": decided}, pool(pool=pid, observed_ms=decided - 60_000, created_ms=int(g[0, 0]),
                                                                            price_usd=float(px[-1])),
           sec(mint=MINT, observed_ms=decided), {"kind": "collection", "sources": {}, "quality": {}}]
    follow = [{"kind": "ohlcv", "pool": pid, "candles": g.tolist(), "observed_ms": decided + 3_600_000}]
    for i in range(9):  # remplissage pour que le pool tombe dans la partie « train »
        cap.insert(1, pool(pool=f"{i:02d}" + POOL[2:], mint=f"{i:02d}" + POOL[2:], created_ms=int(g[0, 0]) + 10 ** 6 * (i + 1)))
    rep = L.run(cap, follow)
    every = L.simulate_pool("momentum_15", L.dense(g.tolist()), pool(pool=pid), 72.0, rng=np.random.default_rng(560))
    assert any(t["entry_ms"] < decided for t in every)  # sans la règle causale, des entrées antérieures seraient comptées
    for name in L.STRATEGIES:
        s = rep["strategies"][name]["train:sans_veto"]
        assert s.get("trades", 0) <= sum(1 for t in L.simulate_pool(name, L.dense(g.tolist()), pool(pool=pid), 72.0,
                                                                     rng=np.random.default_rng(560)) if t["entry_ms"] >= decided)
