"""ALPHA FACTORY v2 — laboratoire : données datées, causalité, simulateur long-only, coûts, statistiques."""
import gzip
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.lab import CAPITAL, costs, data, stats
from sentinelle.alpha.lab.simulate import Features, is_decision_day, simulate
from sentinelle.alpha.lab.strategies import BY_NAME, CANDIDATES, FAMILIES, REFERENCES, Context, Spec
from sentinelle.alpha.lab.synthetic import START_DAY, synthetic_panel
from tools import alpha_lab_fetch

ROOT = Path(__file__).resolve().parent.parent
HYP = costs.load(ROOT / "research" / "alpha-factory-v2" / "cost-hypotheses-v1.json")
PRIMARY, STRESS, GROSS = HYP.scenario(HYP.primary), HYP.scenario(HYP.stress), HYP.scenario(HYP.gross)
P = synthetic_panel(11, kind="noise", days=900)
F = Features(P)
START = 430


def ctx_at(panel, feats, t, **kw):
    return Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t], **kw)


def flat_panel(closes: dict, *, opens: dict | None = None, volume: float = 1e7, start: int = START_DAY):
    """Panneau minimal : clôtures données, ouverture = clôture de la veille (sauf indication)."""
    series = {}
    for name, c in closes.items():
        c = np.asarray(c, float)
        o = np.asarray(opens[name], float) if opens and name in opens else np.concatenate([[c[0]], c[:-1]])
        series[name] = {"days": start + np.arange(len(c)), "open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999,
                        "close": c, "qvol": np.full(len(c), volume)}
    return data.from_series(series)


# ---------------------------------------------------------------------------
# Données : séries continues, vue tronquée, exclusions
# ---------------------------------------------------------------------------


def test_a_missing_day_splits_the_series_and_nothing_is_filled():
    days = np.array([10, 11, 12, 15, 16])
    one = np.ones(5)
    p = data.from_series({"X": {"days": days, "open": one, "high": one, "low": one, "close": one * 2, "qvol": one},
                          "Y": {"days": np.arange(9, 18), "open": np.ones(9), "high": np.ones(9), "low": np.ones(9), "close": np.ones(9), "qvol": np.ones(9)}})
    assert p.assets == ("X", "X#2", "Y") and len(p) == 9 and p.base("X#2") == "X"
    assert np.isfinite(p.close[:, 0]).tolist() == [False, True, True, True, False, False, False, False, False]
    assert np.isfinite(p.close[:, 1]).tolist() == [False] * 6 + [True, True, False]  # la suite est une nouvelle cotation
    with pytest.raises(ValueError):
        data.from_series({"X": {"days": [3, 2], "open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "qvol": [1, 1]}})


def test_a_view_physically_holds_no_future_row():
    v = P.view(100)
    assert len(v) == 101 and v.close.shape[0] == 101 and v.days[-1] == P.days[100]
    assert P.index_of(P.date(100)) == 100 and P.date(0) == "2018-01-01"
    with pytest.raises(IndexError):
        P.view(len(P))
    with pytest.raises(KeyError):
        P.index_of("2031-01-01")


def test_pegged_leveraged_and_lookalike_names():
    assert data.excluded("USDC") == "pegged" and data.excluded("PAXG") == "pegged" and data.excluded("WBTC") == "pegged"
    assert data.excluded("BTCUP") == "leveraged" and data.excluded("ETHBEAR") == "leveraged" and data.excluded("BULL") == "leveraged"
    assert data.excluded("JUP") is None and data.excluded("SYRUP") is None and data.excluded("BTC") is None and data.excluded("SUPER") is None


# ---------------------------------------------------------------------------
# Archive : téléchargement borné (simulé ici), lecture seule, empreintes
# ---------------------------------------------------------------------------

END_MS = (START_DAY + 40) * data.DAY_MS


def fake_exchange(calls: list):
    info = {"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING", "permissionSets": [["SPOT"]]},
        {"symbol": "OLDUSDT", "baseAsset": "OLD", "quoteAsset": "USDT", "status": "BREAK", "permissionSets": [["SPOT"]]},
        {"symbol": "USDCUSDT", "baseAsset": "USDC", "quoteAsset": "USDT", "status": "TRADING", "permissionSets": [["SPOT"]]},
        {"symbol": "BTCUPUSDT", "baseAsset": "BTCUP", "quoteAsset": "USDT", "status": "BREAK", "permissionSets": [["SPOT"]]},
        {"symbol": "NVDABUSDT", "baseAsset": "NVDAB", "quoteAsset": "USDT", "status": "TRADING", "permissionSets": [["SPOT", data.TOKENIZED_GROUP]]},
        {"symbol": "BTCEUR", "baseAsset": "BTC", "quoteAsset": "EUR", "status": "TRADING", "permissionSets": [["SPOT"]]},
    ]}

    def get(path, params):
        calls.append((path, params.get("symbol")))
        if path == "exchangeInfo":
            return json.dumps(info).encode()
        last = START_DAY + (20 if params["symbol"] == "OLDUSDT" else 45)  # OLD est retiré de la cote ; 45 dépasse la date de fin
        rows = [[d * data.DAY_MS, "10", str(11 + (d - START_DAY) * 0.1), "9", str(10 + (d - START_DAY) * 0.1), "5", 0, "50", 7, "0", "0", "0"]
                for d in range(START_DAY, last) if d * data.DAY_MS >= params["startTime"]]
        if params["symbol"] == "BTCUSDT":
            rows[5][2] = "8"  # plus haut sous le plus bas : bougie incohérente
        return json.dumps(rows[:1000]).encode()
    return get


def test_archive_is_resumable_bounded_hashed_and_read_only(tmp_path):
    calls = []
    manifest = alpha_lab_fetch.run(tmp_path, END_MS, fake_exchange(calls), max_requests=50)
    assert manifest["complete"] and set(manifest["symbols"]) == {"BTCUSDT", "OLDUSDT", "USDCUSDT", "BTCUPUSDT", "NVDABUSDT"}
    assert manifest["symbols"]["BTCUSDT"]["bars"] == 40 and manifest["symbols"]["OLDUSDT"]["bars"] == 20  # rien au-delà de la date de fin
    before = {p.name: p.read_bytes() for p in (tmp_path / "klines").iterdir()}
    n = len(calls)
    alpha_lab_fetch.run(tmp_path, END_MS, fake_exchange(calls), max_requests=50)
    assert len(calls) == n and before == {p.name: p.read_bytes() for p in (tmp_path / "klines").iterdir()}  # reprise : aucun nouvel appel
    rows = [[START_DAY * data.DAY_MS, "1", "1", "1", "1", "1", "1", 3]]
    assert alpha_lab_fetch.encode_rows(rows) == alpha_lab_fetch.encode_rows(rows) and gzip.decompress(alpha_lab_fetch.encode_rows(rows)).startswith(b"ts,open")
    with pytest.raises(RuntimeError, match="Budget"):
        alpha_lab_fetch.run(tmp_path / "petit", END_MS, fake_exchange([]), max_requests=2)
    panel = data.load_archive(tmp_path)
    assert panel.provenance["excluded"] == {"leveraged": 1, "pegged": 1, "tokenized_security": 1}
    assert panel.assets == ("BTC", "BTC#2", "OLD") and panel.provenance["invalid_bars_dropped"] == 1  # la bougie incohérente coupe la série
    assert int(np.isfinite(panel.close[:, 2]).sum()) == 20 and not np.isfinite(panel.close[-1, 2])
    assert alpha_lab_fetch.main(["--root", str(tmp_path / "x"), "--end", "2099-01-01"]) == 2  # jamais de bougie non close


def test_a_tampered_or_incomplete_archive_is_refused(tmp_path):
    alpha_lab_fetch.run(tmp_path, END_MS, fake_exchange([]), max_requests=50)
    target = tmp_path / "klines" / "BTCUSDT.csv.gz"
    original = target.read_bytes()
    target.write_bytes(alpha_lab_fetch.encode_rows([[START_DAY * data.DAY_MS, "1", "2", "1", "2", "1", "1", 1]]))
    with pytest.raises(ValueError, match="Fichier altéré"):
        data.load_archive(tmp_path)
    target.write_bytes(original)
    info = tmp_path / "exchangeInfo.json"
    info.write_bytes(info.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Catalogue"):
        data.load_archive(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(manifest.read_text().replace('"complete": true', '"complete": false'))
    with pytest.raises(ValueError, match="Manifeste"):
        data.load_archive(tmp_path)


# ---------------------------------------------------------------------------
# Univers daté et grandeurs causales
# ---------------------------------------------------------------------------


def test_features_do_not_change_when_the_future_is_removed():
    for t in (95, 300, 611, 899):
        part = Features(P.view(t))
        for name in ("age", "medvol", "vol90"):
            assert np.array_equal(getattr(part, name), getattr(F, name)[:t + 1], equal_nan=True), (name, t)


def test_universe_is_point_in_time_liquid_and_free_of_pegged_assets():
    t = 500
    ctx = ctx_at(P, F, t)
    cols = ctx.universe(min_age=91, top=20)
    names = [P.assets[j] for j in cols]
    assert len(cols) == 20 and "STBL" not in names and names[:2] == ["BTC", "ETH"]  # la monnaie stable, pourtant la plus échangée, est écartée
    assert all(F.age[t][j] >= 91 for j in cols) and list(F.medvol[t][cols]) == sorted(F.medvol[t][cols], reverse=True)
    late = [j for j in range(len(P.assets)) if 0 < F.age[t][j] < 91]
    ended = [j for j in range(len(P.assets)) if F.age[t][j] == 0]
    assert all(j not in cols for j in late + ended)
    assert "ETH" not in [P.assets[j] for j in ctx_at(P, F, t, exclude=frozenset({"ETH"})).universe()]
    early = ctx_at(P, F, 60).universe()
    assert early == []  # trop tôt : personne n'a encore 91 jours de cotation
    assert ctx.weekday == (int(P.days[t]) + 3) % 7 and ctx.col("NOPE") is None and ctx.name(cols[0]) == "BTC"


def test_a_resumed_listing_starts_as_a_new_asset():
    halted = next(a for a in P.assets if a.endswith("#2"))
    j_old, j_new = P.col(halted.split("#")[0]), P.col(halted)
    first_new = int(np.flatnonzero(np.isfinite(P.close[:, j_new]))[0])
    assert F.age[first_new, j_old] == 0 and F.age[first_new, j_new] == 1
    ctx = ctx_at(P, F, first_new + 30)
    assert ctx.col(halted.split("#")[0]) == j_new and j_new not in ctx.universe()  # la reprise doit refaire son ancienneté


# ---------------------------------------------------------------------------
# Candidats : six familles, achat seulement, sans levier, sans futur
# ---------------------------------------------------------------------------


def test_candidates_cover_six_families_with_one_fixed_parameter_set_each():
    assert len(CANDIDATES) == 15 and len({s.name for s in CANDIDATES + REFERENCES}) == 19
    by_family = {f: [s for s in CANDIDATES if s.family == f] for f in FAMILIES}
    assert all(len(v) >= 2 for v in by_family.values()) and sum(len(v) for v in by_family.values()) == 15
    assert all(s.source and s.label and s.rebalance in ("daily", "weekly", "monthly") and s.benchmark in ("btc", "majors", "market") for s in CANDIDATES)
    assert all(s.family == "reference" for s in REFERENCES) and BY_NAME["cash"].fn(ctx_at(P, F, 500), {}) == {}
    assert {s.name for s in REFERENCES} == {"cash", "hold_btc", "hold_majors", "hold_basket_top5"}


@pytest.mark.parametrize("spec", CANDIDATES + REFERENCES, ids=lambda s: s.name)
def test_weights_are_long_only_unlevered_and_on_listed_assets(spec):
    for t in range(430, 900, 23):
        ctx = ctx_at(P, F, t, held={"A07": {"days": 3, "entry": 1.0, "value": 7.0}})
        w = spec.fn(ctx, spec.params)
        assert all(x >= 0 for x in w.values()) and sum(w.values()) <= 1 + 1e-9, (spec.name, t)
        assert all(a in P.assets and (np.isfinite(P.close[t, P.col(a)]) or a == "A07") for a in w), (spec.name, t)


def test_momentum_and_trend_rules_follow_their_definition():
    n = 260
    up, down = 100 * 1.01 ** np.arange(n), 100 * 0.99 ** np.arange(n)
    p = flat_panel({"BTC": up, "ETH": down})
    f = Features(p)
    ctx = ctx_at(p, f, n - 1)
    assert BY_NAME["mom_ts_28"].fn(ctx, {"lookback": 28}) == {"BTC": 0.5}
    assert BY_NAME["trend_sma_200"].fn(ctx, {"n": 200}) == {"BTC": 0.5}
    assert BY_NAME["trend_cross_50_200"].fn(ctx, {"fast": 50, "slow": 200}) == {"BTC": 0.5}
    assert BY_NAME["trend_sma_200"].fn(ctx_at(p, f, 150), {"n": 200}) == {}  # historique insuffisant : rien, jamais une supposition
    assert BY_NAME["hold_majors"].fn(ctx, {}) == {"BTC": 0.5, "ETH": 0.5}
    other = ctx_at(p, f, n - 1, majors=("ETH",))
    assert BY_NAME["mom_ts_28"].fn(other, {"lookback": 28}) == {}


def test_cross_sectional_rules_pick_the_right_assets():
    n = 520
    rates = {"A": 0.004, "B": 0.002, "C": 0.0, "D": -0.002, "E": -0.004, "BTC": 0.001}
    wiggle = (-1.0) ** np.arange(n)  # oscillation déterministe : de la volatilité sans changer les rendements sur un nombre pair de jours
    p = flat_panel({k: 50 * np.exp(np.cumsum(r + wiggle * 0.006 * (1 + i))) for i, (k, r) in enumerate(rates.items())})
    ctx = ctx_at(p, Features(p), n - 1)
    assert set(BY_NAME["mom_xs_28_top3"].fn(ctx, {"lookback": 28, "n": 2})) == {"A", "B"}
    assert set(BY_NAME["value_rev_365_top3"].fn(ctx, {"lookback": 365, "skip": 30, "n": 2})) == {"D", "E"}
    assert set(BY_NAME["mom_dual_90_top3"].fn(ctx, {"lookback": 90, "n": 5})) == {"A", "B", "BTC"}  # jamais un actif en baisse
    low = BY_NAME["crypto_lowvol_top3"].fn(ctx, {"window": 30, "n": 2})
    assert set(low) == {"A", "B"} and all(v == 0.5 for v in low.values())  # A et B ont le bruit le plus faible
    assert set(BY_NAME["meanrev_xs_7_bottom3"].fn(ctx, {"lookback": 7, "n": 1})) <= set(rates)
    gate_on = BY_NAME["crypto_btc_gate_top5"].fn(ctx, {"n": 100, "basket": 3})
    assert len(gate_on) == 3 and all(v == pytest.approx(1 / 3) for v in gate_on.values())
    bear = flat_panel({k: (v[::-1] if k == "BTC" else v) for k, v in {a: p.close[:, p.col(a)] for a in rates}.items()})
    assert BY_NAME["crypto_btc_gate_top5"].fn(ctx_at(bear, Features(bear), n - 1), {"n": 100, "basket": 3}) == {}  # BTC sous sa moyenne : liquidités


def test_breakout_enters_on_a_new_high_and_leaves_below_the_recent_low():
    base = np.concatenate([np.full(130, 100.0), [106.0], np.full(15, 107.0), [90.0]])
    p = flat_panel({"BTC": base, "ETH": np.full(len(base), 50.0)})
    f = Features(p)
    spec = BY_NAME["breakout_55_20_majors"]
    assert spec.fn(ctx_at(p, f, 129), spec.params) == {}
    assert spec.fn(ctx_at(p, f, 130), spec.params) == {"BTC": 0.5}  # clôture au-dessus du plus haut des 55 jours précédents
    held = {"BTC": {"days": 10, "entry": 106.0, "value": 15.0}}
    assert spec.fn(ctx_at(p, f, 140, held=held), spec.params) == {"BTC": 0.5}  # pas de signal de sortie : on garde
    assert spec.fn(ctx_at(p, f, len(base) - 1, held=held), spec.params) == {}  # clôture sous le plus bas des 20 jours : sortie
    dust = {f"A{k:02d}": {"days": 1, "entry": 1.0, "value": 0.5 + k} for k in range(3, 12)}
    crowded = BY_NAME["breakout_20_10_slots4"].fn(ctx_at(P, F, 500, held=dust), BY_NAME["breakout_20_10_slots4"].params)
    assert sum(crowded.values()) <= 1 + 1e-9 and len(crowded) <= 4  # des reliquats invendables ne créent jamais de levier


def test_weekday_and_volatility_rules():
    p = flat_panel({"BTC": 100 + np.arange(120.0)})
    f = Features(p)
    held_days = [t for t in range(40, 110) if BY_NAME["crypto_weekday_btc"].fn(ctx_at(p, f, t), {"day": 0})]
    assert held_days and all((int(p.days[t]) + 3) % 7 == 6 for t in held_days)  # décidé à la clôture du dimanche, pour le lundi
    calm = BY_NAME["crypto_volmanaged_btc"].fn(ctx_at(p, f, 100), {"window": 30, "target": 0.40, "step": 0.25})
    assert calm == {"BTC": 1.0}  # plafond à 100 % : aucun levier, même par temps calme
    rng = np.random.default_rng(1)
    wild = flat_panel({"BTC": 100 * np.exp(np.cumsum(rng.normal(0, 0.06, 200)))})
    w = BY_NAME["crypto_volmanaged_btc"].fn(ctx_at(wild, Features(wild), 199), {"window": 30, "target": 0.40, "step": 0.25})
    assert w["BTC"] in (0.25, 0.5) and BY_NAME["crypto_volmanaged_btc"].fn(ctx_at(p, f, 10), {"window": 30, "target": 0.40, "step": 0.25}) == {}


# ---------------------------------------------------------------------------
# Simulateur
# ---------------------------------------------------------------------------

END = len(P) - 1


def run(name, cost=PRIMARY, **kw):
    return simulate(P, BY_NAME[name], cost, start=START, end=END, features=F, **kw)


def test_decision_calendar():
    daily, weekly, monthly = BY_NAME["trend_sma_200"], BY_NAME["mom_ts_28"], BY_NAME["value_rev_365_top3"]
    days = [t for t in range(400, 470)]
    assert all(is_decision_day(daily, P, t) for t in days)
    assert [t for t in days if is_decision_day(weekly, P, t)] == [t for t in days if (int(P.days[t]) + 3) % 7 == 6]
    month_ends = [t for t in days if is_decision_day(monthly, P, t)]
    assert month_ends and all(P.date(t + 1).endswith("-01") for t in month_ends)


def test_simulation_is_identical_when_the_future_is_removed():
    cut = 700
    short = P.view(cut)
    for name in ("mom_xs_28_top3", "breakout_20_10_slots4", "meanrev_rsi2_slots4", "crypto_btc_gate_top5", "value_mom_combo_top3"):
        full = run(name)
        part = simulate(short, BY_NAME[name], PRIMARY, start=START, end=cut, features=Features(short))
        n = cut - START  # le dernier jour diffère seulement par la valorisation nette de sortie
        assert np.array_equal(part.returns[:n], full.returns[:n]) and np.array_equal(part.exposure[:n], full.exposure[:n]), name


@pytest.mark.parametrize("spec", CANDIDATES + REFERENCES, ids=lambda s: s.name)
def test_portfolio_never_borrows_never_shorts_and_its_ledger_adds_up(spec):
    res = simulate(P, spec, PRIMARY, start=START, end=END, features=F)
    assert res.exposure.min() >= 0 and res.exposure.max() <= 1 + 1e-9 and res.equity.min() > 0
    assert sum(res.by_asset.values()) == pytest.approx(res.net_pnl, abs=1e-9)  # somme des résultats par actif = résultat du portefeuille
    assert np.prod(1 + res.returns) * CAPITAL == pytest.approx(res.equity[-1], rel=1e-12)
    assert res.fees >= 0 and res.spread_slippage >= 0 and len(res.returns) == END - START + 1
    if spec.name == "cash":
        assert res.net_pnl == 0.0 and res.trades == 0 and res.fees == 0.0


def test_costs_reduce_the_result_when_the_minimum_order_does_not_interfere():
    for name in ("mom_xs_28_top3", "breakout_20_10_slots4", "trend_sma_200", "crypto_weekday_btc"):
        gross, net, stressed = (run(name, c, capital=3000.0) for c in (GROSS, PRIMARY, STRESS))  # capital large : aucun ordre bloqué
        assert gross.net_pnl > net.net_pnl > stressed.net_pnl, name
        assert gross.fees == 0 and gross.spread_slippage == 0 and stressed.spread_slippage > net.spread_slippage > 0
        assert not any("minimum" in k for k in net.blocked)


def test_with_thirty_units_a_losing_rule_is_stopped_by_the_minimum_order():
    """Constat utile pour lire les résultats : un petit portefeuille qui perd ne peut bientôt plus passer d'ordres."""
    gross, net = run("mom_xs_28_top3", GROSS), run("mom_xs_28_top3", PRIMARY)
    assert net.net_pnl < 0 and any("minimum" in k for k in net.blocked)
    assert net.holdings_days < gross.holdings_days  # avec les coûts, le portefeuille s'arrête plus tôt de trader


def test_orders_execute_at_the_next_open_with_costs_never_at_the_decision_close():
    closes = np.concatenate([np.full(40, 100.0), np.full(10, 100.0)])
    opens = closes.copy()
    opens[40] = 120.0  # le lendemain de la décision, le marché ouvre 20 % plus haut
    p = flat_panel({"BTC": closes}, opens={"BTC": opens})
    res = simulate(p, BY_NAME["hold_btc"], PRIMARY, start=40, end=49)
    paid = 120.0 * (1 + PRIMARY.worse("BTC"))
    units = (CAPITAL / (1 + PRIMARY.fee)) / paid
    assert res.trades == 1 and res.equity[0] == pytest.approx(units * 100.0, rel=1e-12)  # acheté à 120, valorisé à 100
    assert res.equity[-1] == pytest.approx(units * 100.0 * (1 - PRIMARY.worse("BTC")) * (1 - PRIMARY.fee), rel=1e-12)  # dernier jour : net de la sortie
    free = simulate(p, BY_NAME["hold_btc"], GROSS, start=40, end=49)
    assert free.equity[-1] == pytest.approx(CAPITAL * 100.0 / 120.0, rel=1e-12)


def test_minimum_order_blocks_small_trades_and_dust_stays_put():
    def tiny(ctx, p):
        return {"BTC": 0.10}  # 3 unités sur 30 : sous le minimum de 5

    spec = Spec("tiny", "test", "t", tiny, {}, "daily", "btc", "t", 1)
    p = flat_panel({"BTC": np.full(60, 100.0)})
    res = simulate(p, spec, PRIMARY, start=10, end=59)
    assert res.trades == 0 and res.net_pnl == 0 and res.blocked["achat_sous_le_minimum"] == 50

    def in_then_out(ctx, p):
        return {"BTC": 0.2} if ctx.t < 20 else {}

    crash = flat_panel({"BTC": np.concatenate([np.full(15, 100.0), np.full(45, 60.0)])})  # la position de 6 tombe à 3,6 : invendable
    res = simulate(crash, Spec("io", "test", "t", in_then_out, {}, "daily", "btc", "t", 1), PRIMARY, start=10, end=59)
    assert res.trades == 1 and res.blocked["reliquat_sous_le_minimum"] > 30 and res.exposure[-1] > 0


def test_a_delisted_asset_is_sold_at_its_last_close_and_counted():
    n = 80
    p = flat_panel({"BTC": np.full(n, 100.0), "GONE": np.concatenate([np.full(50, 10.0), [4.0]])})

    def both(ctx, prm):
        return {a: 0.5 for a in ("BTC", "GONE") if ctx.col(a) is not None}

    res = simulate(p, Spec("both", "test", "t", both, {}, "daily", "market", "t", 1), GROSS, start=10, end=n - 1)
    assert res.forced_exits == 1 and res.by_asset["GONE"] == pytest.approx(15.0 * 4.0 / 10.0 - 15.0)  # vendu à 4, la dernière clôture connue
    assert res.exposure[-1] == pytest.approx(15.0 / 21.0) and np.isfinite(res.equity).all()  # le produit de la vente reste en liquidités


def test_strategies_cannot_cheat_the_simulator():
    for bad in (lambda c, p: {"BTC": -0.2}, lambda c, p: {"BTC": 0.7, "ETH": 0.6}, lambda c, p: {"BTC": float("nan")}, lambda c, p: {"ZZZ": 0.5}):
        with pytest.raises(ValueError):
            simulate(P, Spec("bad", "test", "t", bad, {}, "daily", "btc", "t", 1), PRIMARY, start=START, end=START + 5, features=F)
    with pytest.raises(ValueError, match="Historique insuffisant"):
        simulate(P, BY_NAME["value_rev_365_top3"], PRIMARY, start=100, end=200, features=F)
    with pytest.raises(ValueError, match="Fenêtre"):
        simulate(P, BY_NAME["cash"], PRIMARY, start=10, end=len(P), features=F)


def test_generalization_switches_really_change_the_universe():
    base, without = run("mom_xs_28_top3"), run("mom_xs_28_top3", exclude=frozenset({"BTC", "ETH", "A03", "A04", "A05"}))
    assert not any(a.split("#")[0] in ("BTC", "ETH", "A03", "A04", "A05") for a in without.by_asset) and base.by_asset != without.by_asset
    pair = run("trend_sma_200", majors=("A07", "A08"))
    assert set(a.split("#")[0] for a in pair.by_asset) <= {"A07", "A08"}


# ---------------------------------------------------------------------------
# Coûts : un fichier d'hypothèses, appliqué tel quel
# ---------------------------------------------------------------------------


def test_cost_hypotheses_file_is_provisional_explicit_and_ordered():
    assert HYP.primary == "prudent" and HYP.stress == "tendu" and HYP.gross == "brut" and "PROVISOIRE" in HYP.status
    assert "COST OPTIMIZER" in HYP.source_of_truth and len(HYP.sha256) == 64
    assert PRIMARY.round_trip("BTC") == pytest.approx(0.0044) and PRIMARY.round_trip("SOL") == pytest.approx(0.0056)
    assert STRESS.round_trip("SOL") > PRIMARY.round_trip("SOL") > HYP.scenario("projet").round_trip("SOL") > GROSS.round_trip("SOL") == 0
    assert PRIMARY.min_notional == 5.0 and PRIMARY.majors == ("BTC", "ETH")
    with pytest.raises(costs.CostError):
        HYP.scenario("gratuit")


def test_malformed_cost_files_are_refused():
    raw = json.loads((ROOT / "research" / "alpha-factory-v2" / "cost-hypotheses-v1.json").read_text(encoding="utf-8"))

    def broken(change):
        other = json.loads(json.dumps(raw))
        change(other)
        with pytest.raises((costs.CostError, KeyError)):
            costs.parse(other)

    broken(lambda r: r.update(kind="autre"))
    broken(lambda r: r.update(rabais=5))  # champ inconnu
    broken(lambda r: r["scenarios"]["prudent"].update(fee_bps=-1))
    broken(lambda r: r["scenarios"]["prudent"].update(fee_bps=True))
    broken(lambda r: r["scenarios"]["prudent"].update(fee_bps=float("nan")))
    broken(lambda r: r["scenarios"]["prudent"].update(remise=1))
    broken(lambda r: r["scenarios"]["brut"].update(fee_bps=1))  # le scénario brut doit être sans coût
    broken(lambda r: r.update(primary="brut"))  # on ne conclut jamais sans coûts
    broken(lambda r: r.update(stress="projet"))  # la tension ne peut pas coûter moins que le principal
    broken(lambda r: r.update(min_notional=0))
    broken(lambda r: r.update(primary="inconnu"))
    assert costs.parse(raw).primary == "prudent"


# ---------------------------------------------------------------------------
# Statistiques
# ---------------------------------------------------------------------------


def test_metrics_and_drawdown_on_a_known_path():
    equity = np.array([33.0, 36.3, 18.15, 27.225])
    returns = np.array([0.10, 0.10, -0.50, 0.50])
    m = stats.metrics(returns, equity, 30.0)
    assert m["net_pnl"] == pytest.approx(-2.775) and m["max_drawdown"] == pytest.approx(0.5) and m["worst_day"] == -0.5
    assert stats.max_drawdown(np.array([31.0, 32.0]), 30.0) == 0.0 and stats.risk_scaled(m, 0.10)["scale"] == pytest.approx(0.2)
    assert stats.risk_scaled({"max_drawdown": 0.05, "cagr": 0.3}, 0.10) == {"limit": 0.10, "scale": 1.0, "cagr_at_scale": 0.3, "within_limit_at_full_size": True}


def test_multiple_testing_correction_controls_false_discoveries_on_noise():
    rng = np.random.default_rng(5)
    raw_hits = adjusted_hits = 0
    for k in range(60):
        x = rng.normal(0, 0.03, (500, 15))
        fw = stats.familywise(x, reps=300, seed=k)
        raw_hits += min(fw["p_raw"]) < 0.05
        adjusted_hits += min(fw["p_adjusted"]) < 0.05
        assert all(a >= r - 1e-12 for a, r in zip(fw["p_adjusted"], fw["p_raw"]))
    assert raw_hits >= 20  # sans correction, « le meilleur de 15 » paraît gagnant dans plus d'un tiers des essais
    assert adjusted_hits <= 8  # avec correction, le taux de fausses découvertes revient vers 5 %


def test_multiple_testing_correction_still_finds_a_real_edge():
    rng = np.random.default_rng(6)
    x = rng.normal(0, 0.02, (900, 15))
    x[:, 4] += 0.004
    x[:, 9] = 0.0  # ne rien faire
    fw = stats.familywise(x, reps=500)
    assert fw["p_adjusted"][4] < 0.01 and fw["p_adjusted"][9] == 1.0 and fw["t"][9] == 0.0 and fw["tests"] == 15
    assert sum(p < 0.05 for p in fw["p_adjusted"]) == 1 and fw == stats.familywise(x, reps=500)  # déterministe


def test_exposure_matched_reference_separates_timing_from_market_risk():
    rng = np.random.default_rng(7)
    market = rng.normal(0.001, 0.03, 800)
    half = 0.5 * market  # un « candidat » qui détient simplement la moitié du marché
    ref, e = stats.exposure_matched(np.full(800, 0.5), market)
    assert e == 0.5 and np.allclose(half - ref, 0.0)  # aucun mérite propre : écart nul
    ab = stats.alpha_beta(half, market)
    assert ab["beta"] == pytest.approx(0.5) and abs(ab["alpha_annual"]) < 1e-9 and ab["r2"] == pytest.approx(1.0)
    assert stats.alpha_beta(half[:10], market[:10])["beta"] is None


def test_periods_years_and_concentration():
    days = np.arange(START_DAY + 350, START_DAY + 380)  # à cheval sur 2018 et 2019
    r = np.full(30, 0.01)
    years = stats.by_year(days, r)
    assert set(years) == {"2018", "2019"} and years["2018"] == pytest.approx(1.01 ** 15 - 1)
    per = stats.by_period(days, r, [("P1", int(days[0]), int(days[9])), ("P2", int(days[10]), int(days[-1]))])
    assert per["P1"]["days"] == 10 and per["P2"]["return"] == pytest.approx(1.01 ** 20 - 1)
    c = stats.concentration({"A": 8.0, "A#2": 1.0, "B": 1.0, "C": -4.0})
    assert c["top_asset"] == "A" and c["top_share_of_gains"] == pytest.approx(0.9) and c["worst_asset"] == "C" and c["winners"] == 2
    assert stats.concentration({"C": -1.0})["top_share_of_gains"] is None
    assert stats.t_stat(np.zeros(10)) == 0.0 and stats.mean_interval(np.full(100, 0.01))["ci_low"] == pytest.approx(0.01)


def test_hold_reference_matches_a_closed_form():
    res = run("hold_btc", GROSS)
    j = P.col("BTC")
    assert res.equity[-1] == pytest.approx(CAPITAL * P.close[END, j] / P.open[START, j], rel=1e-9) and res.trades == 1
    costly = run("hold_btc", PRIMARY)
    drag = (1 - PRIMARY.worse("BTC")) * (1 - PRIMARY.fee) / ((1 + PRIMARY.worse("BTC")) * (1 + PRIMARY.fee))
    assert costly.equity[-1] == pytest.approx(res.equity[-1] * drag, rel=1e-9)
    assert replace(PRIMARY, fx_bps=0).fee < PRIMARY.fee
