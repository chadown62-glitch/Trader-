"""Adaptive learning of the memecoin paper session: rolling scores, bounded multipliers, threshold suggestions."""

import json
import math
import random
import sqlite3
import statistics
from contextlib import closing

import pytest

from sentinelle.dmh import memecoin_analytics as analytics, memecoin_learner as learner, memecoin_simulator as sim
from sentinelle.dmh.memecoin_strategies import MemecoinIsolatedLearner
from tests import test_configurable_capital as frozen


def closed(strategy, returns, **extra):
    return [dict(strategy=strategy, mint="M", **{"return": r}, **extra) for r in returns]


def save(path, state):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES (?,?)", (sim.KEY, json.dumps(state)))
        conn.commit()


# -- rolling efficiency score -----------------------------------------------------------------------------------------
def test_score_combines_average_profit_profit_factor_and_win_rate_exactly():
    s = learner.score([0.4, -0.2, 0.1])
    assert s["avg_profit_pct"] == pytest.approx(10) and s["profit_factor"] == pytest.approx(2.5)
    assert s["win_rate"] == pytest.approx(2 / 3) and s["window_trades"] == 3
    expected = {"profit": math.tanh(10 / 20), "profit_factor": 1.5 / 3.5, "win_rate": 1 / 3}
    assert s["components"] == pytest.approx(expected)
    raw = 0.5 * expected["profit"] + 0.3 * expected["profit_factor"] + 0.2 * expected["win_rate"]
    assert s["raw_score"] == pytest.approx(raw) and s["shrink"] == pytest.approx(3 / 11)
    assert s["score"] == pytest.approx(raw * 3 / 11)


def test_score_edge_cases_and_rolling_window():
    assert learner.score([])["score"] == 0 and learner.score([])["avg_profit_pct"] is None
    no_loss = learner.score([0.1, 0.2])
    assert no_loss["profit_factor"] is None and no_loss["components"]["profit_factor"] == 1
    flat = learner.score([0.0, 0.0])
    assert flat["components"] == {"profit": 0, "profit_factor": 0, "win_rate": -1}  # a flat lot is not a win
    # Only the last 20 lots count: an old losing streak is forgotten once 20 winners followed.
    recovered = learner.score([-0.5] * 30 + [0.3] * 20)
    assert recovered["window_trades"] == 20 and recovered["win_rate"] == 1 and recovered["score"] > 0


# -- bounded multipliers ----------------------------------------------------------------------------------------------
def test_empty_history_is_neutral_for_every_strategy():
    result = learner.multipliers([])
    assert set(result) == {"sniper", "momentum", "dip_hunter", "whale_tail"}
    for name, entry in result.items():
        assert entry["multiplier"] == 1.0 and entry["closed_trades"] == 0 and not entry["upside_unlocked"]
        assert entry["reason"] == f"{name} : aucune clôture, mise neutre 1,00x"


def test_small_samples_cannot_raise_a_stake_but_can_lower_it():
    result = learner.multipliers(closed("sniper", [2.0] * 7) + closed("momentum", [-0.2] * 7))
    assert result["sniper"]["multiplier"] == 1.0 and "hausse bloquée : 7/8" in result["sniper"]["reason"]
    assert 0.5 <= result["momentum"]["multiplier"] < 1.0
    unlocked = learner.multipliers(closed("sniper", [2.0] * 8))["sniper"]
    assert unlocked["upside_unlocked"] and 1.0 < unlocked["multiplier"] <= 1.5


def test_multiplier_saturates_at_the_bounds():
    result = learner.multipliers(closed("whale_tail", [0.5] * 20) + closed("dip_hunter", [-0.2] * 20))
    assert result["whale_tail"]["multiplier"] == 1.5  # 1 + 0.993 x 20/28 = 1.71, clamped
    assert result["dip_hunter"]["multiplier"] == 0.5  # 1 - 0.881 x 20/28 = 0.37, clamped
    assert "-> 1,50x" in result["whale_tail"]["reason"] and "-> 0,50x" in result["dip_hunter"]["reason"]


def test_multiplier_stays_within_bounds_on_random_and_degenerate_histories():
    rng = random.Random(58)
    for _ in range(400):
        n = rng.randrange(0, 45)
        returns = [rng.choice((-1.0, -0.2, 0.0, 0.05, 3.0, 50.0, rng.uniform(-1, 10))) for _ in range(n)]
        rows = closed("momentum", returns + [float("nan"), float("inf")])  # non-finite returns are ignored
        entry = learner.multipliers(rows)["momentum"]
        assert 0.5 <= entry["multiplier"] <= 1.5 and entry["closed_trades"] == n
        if n < learner.MIN_TRADES:
            assert entry["multiplier"] <= 1.0


def test_other_domains_are_ignored():
    rows = closed("sniper", [0.5] * 20, asset_domain="majors")
    assert learner.multipliers(rows)["sniper"]["multiplier"] == 1.0


# -- entry-threshold recommendations ----------------------------------------------------------------------------------
def entry_history():
    """10 losers on thin pools, 2 losers and 10 winners on deeper pools."""
    rows = []
    for liquidity in range(21_000, 31_000, 1_000):
        rows.append({"strategy": "momentum", "mint": "L", "return": -0.2,
                     "entry": {"liquidity_usd": liquidity, "volume_h1_usd": 8_000, "confidence": 0.55}})
    for liquidity in (80_000, 90_000):
        rows.append({"strategy": "momentum", "mint": "L", "return": -0.1,
                     "entry": {"liquidity_usd": liquidity, "volume_h1_usd": 8_000, "confidence": 0.55}})
    for liquidity in range(35_000, 85_000, 5_000):
        rows.append({"strategy": "momentum", "mint": "W", "return": 0.3,
                     "entry": {"liquidity_usd": liquidity, "volume_h1_usd": 8_000, "confidence": 0.55}})
    return rows


def test_recommendations_compare_winners_and_losers_and_are_never_applied():
    report = learner.learn_from_state({"completed": entry_history()})
    liquidity, volume, conf = report["recommendations"]
    assert [r["feature"] for r in report["recommendations"]] == ["liquidity_usd", "volume_h1_usd", "confidence"]
    assert liquidity["status"] == "suggested" and liquidity["suggested_threshold"] == 35_000
    assert liquidity["current_threshold"] == 20_000 and liquidity["applied"] is False
    assert (liquidity["samples"], liquidity["winners"], liquidity["losers"]) == (22, 10, 12)
    assert (liquidity["winners_median"], liquidity["losers_median"]) == (57_500, 26_500)
    assert (liquidity["kept_trades"], liquidity["excluded_losers"], liquidity["excluded_winners"]) == (12, 10, 0)
    assert liquidity["p_value"] <= 0.01 and [f["confirmed"] for f in liquidity["cross_validation"]] == [True, True]
    assert [f["train_threshold"] for f in liquidity["cross_validation"]] == [35_000, 40_000]  # each half, fitted alone
    assert liquidity["avg_profit_pct_before"] == pytest.approx(100 * 0.8 / 22)
    assert liquidity["avg_profit_pct_after"] == pytest.approx(100 * 2.8 / 12)
    assert "35000,00" in liquidity["explanation"] and "avant toute adoption humaine" in liquidity["explanation"]
    # Identical volumes and confidences: no threshold above the current rule would have helped.
    assert volume["status"] == conf["status"] == "no_improvement"
    assert volume["suggested_threshold"] is conf["suggested_threshold"] is None
    assert all(r["applied"] is False for r in report["recommendations"])


def test_recommendations_wait_for_a_sufficient_sample():
    rows = entry_history()[:9] + entry_history()[-6:]  # 15 lots only
    rec = learner.learn_from_state({"completed": rows})["recommendations"][0]
    assert rec["status"] == "insufficient_sample" and rec["suggested_threshold"] is None
    assert "il en faut 20" in rec["explanation"]
    legacy = closed("sniper", [0.2] * 30)  # lots closed before entry conditions were recorded
    assert learner.learn_from_state({"completed": legacy})["recommendations"][0]["samples"] == 0


# -- read-only learning and analytics exposure --------------------------------------------------------------------------
def test_learn_reads_read_only_and_falls_back_on_absent_or_empty_bases(tmp_path, monkeypatch):
    path = tmp_path / "session.sqlite"
    neutral = learner.learn(path)
    assert neutral["status"] == "empty" and not path.exists()  # never created
    assert all(s["multiplier"] == 1.0 for s in neutral["strategies"].values())
    path.touch()
    assert learner.learn(path)["status"] == "empty"
    path.unlink()
    save(path, {"completed": closed("sniper", [0.5] * 20)})
    connect = sqlite3.connect

    def checked(database, **kwargs):
        conn = connect(database, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM meta")
        return conn

    monkeypatch.setattr(sqlite3, "connect", checked)
    report = learner.learn(path)
    assert report["status"] == "learning" and report["strategies"]["sniper"]["multiplier"] == 1.5
    assert report["bounds"] == {"min": 0.5, "max": 1.5} and report["paper_only"] and report["asset_domain"] == "memecoin"


def test_analytics_report_exposes_learning_and_keeps_the_cockpit_fields(tmp_path):
    rows = entry_history()
    fills = []
    for row in rows:
        for side, pnl in (("buy", 0), ("sell", row["return"])):
            fills.append(dict(strategy=row["strategy"], mint=row["mint"], side=side, pnl=pnl, asset_domain="memecoin", simulated=True))
    fills.append(dict(strategy="sniper", mint="O", side="buy", pnl=0, ts=5, quantity=10, dex_fee_usd=0.1, network_fee_usd=0.1,
                      slippage_usd=0.3, asset_domain="memecoin", simulated=True))
    state = dict(initial=30, cash=25, revision=3, curve=[{"equity": 30}], errors=[], completed=rows, fills=fills,
                 positions={"sniper/O": dict(asset_domain="memecoin", strategy="sniper", mint="O", qty=10, initial=5, basis=5,
                                             mark=5.5, pnl=0.0, stage=1, high=1.3, exit_policy=sim.CALIBRATED_EXITS)},
                 assets={"O": {"symbol": "O", "last": {"price": 0.6, "ts": 6}}})
    path = tmp_path / "paper.sqlite"
    save(path, state)
    report = analytics.build_analytics_report(path)
    assert report["learning"] == learner.learn(path)
    assert report["learning"]["strategies"]["momentum"]["closed_trades"] == 22
    assert report["avg_profit_pct"] == pytest.approx(100 * 0.8 / 22)
    [pos] = report["open_positions"]
    assert (pos["entry_price_usd"], pos["tier_reached"], pos["next_tier"]) == (pytest.approx(0.45), 1.25, 3.0)
    empty = analytics.build_analytics_report(tmp_path / "absent.sqlite")
    assert empty["learning"]["status"] == "empty" and empty["open_positions"] == []


# -- the simulator applies the bounded multiplier and records why ---------------------------------------------------------
POINT = {"price": 1.0, "liquidity_usd": 200_000.0, "volume_h1_usd": 12_000.0, "ts": 100}


def paper_state(completed, positions=None):
    return {"revision": 0, "cash": 100.0, "initial": 100.0, "assets": {"T": {"symbol": "T"}}, "positions": positions or {},
            "fills": [], "completed": completed, "curve": [], "errors": [], "exit_policy": sim.CALIBRATED_EXITS}


def test_simulator_scales_the_kelly_target_and_logs_the_sizing_reason():
    history = closed("momentum", [0.3, -0.2] * 4)  # 8 lots: upside unlocked, mean +5 %
    state = paper_state(history)
    sim.process(state, "T", dict(POINT), ["momentum"], 0.001)
    pos = state["positions"]["momentum/T"]
    learned = learner.multipliers(history)["momentum"]
    kelly = MemecoinIsolatedLearner(history)
    target = 100.0 * kelly.fractions["momentum"] * kelly.weights["momentum"]
    assert learned["multiplier"] == pytest.approx(1 + learned["score"]) and 1.0 < learned["multiplier"] < 1.5
    assert pos["initial"] == pytest.approx(target * learned["multiplier"])
    assert pos["sizing"] == {"learner": "adaptive-v1", "scoring": "lots-v2", "multiplier": learned["multiplier"],
                             "score": learned["score"], "closed_trades": 8, "open_lots": 0,
                             "kelly_target_usd": pytest.approx(target),
                             "exploration_floor_usd": pytest.approx(max(0.05, 0.005 * 100.0 * learned["multiplier"])),
                             "size_usd": pos["initial"], "binding": "kelly_target", "reason": learned["reason"]}
    assert pos["opened_at"] == 100
    assert pos["entry"] == {"liquidity_usd": 200_000.0, "volume_h1_usd": 12_000.0, "confidence": 0.55, "signals": ["momentum"]}
    # A full exit carries the entry conditions and the sizing into the closed lot the learner reads next.
    sim.process(state, "T", dict(POINT, price=0.5, ts=200), [], 0.001)
    assert "momentum/T" not in state["positions"]
    assert state["completed"][-1]["entry"] == pos["entry"] and state["completed"][-1]["sizing"]["multiplier"] == learned["multiplier"]
    assert (state["completed"][-1]["opened_at"], state["completed"][-1]["closed_at"], state["completed"][-1]["exit"]) == (100, 200, "stop_loss")


def test_hard_limits_bind_whatever_the_multiplier():
    history = closed("whale_tail", [0.5] * 20)
    assert learner.multipliers(history)["whale_tail"]["multiplier"] == 1.5
    held = {"sniper/T": {"mint": "T", "strategy": "sniper", "qty": 9.0, "basis": 9.0, "initial": 9.0, "mark": 9.0, "high": 1.0,
                         "stage": 0, "pnl": 0.0, "exit_policy": sim.CALIBRATED_EXITS, "asset_domain": "memecoin"}}
    state = paper_state(history, held)
    state["cash"] = 91.0
    sim.process(state, "T", dict(POINT), ["whale_tail"], 0.001)
    pos = state["positions"]["whale_tail/T"]
    marked = state["positions"]["sniper/T"]["mark"]  # re-marked at this point before the new stake is sized
    assert pos["sizing"]["binding"] == "token_cap"  # 10 % of equity per token, minus the 9 USD already held
    assert pos["initial"] == pytest.approx(0.10 * (91.0 + marked) - 9.0)
    assert pos["initial"] < pos["sizing"]["kelly_target_usd"] * 1.5  # the boosted target would have been larger


@pytest.mark.parametrize("h1,expected", [(1234.5, 1234.5), (None, None), (float("nan"), None), (-1, None), ("9", None)])
def test_quote_records_the_one_hour_volume_when_it_is_valid(h1, expected):
    mint, pool, quote_mint = "T" * 32, "P" * 32, "Q" * 32
    pair = {"chainId": "solana", "dexId": "pumpswap", "pairAddress": pool, "baseToken": {"address": mint},
            "quoteToken": {"address": quote_mint}, "priceUsd": "0.5", "liquidity": {"usd": 50_000}, "marketCap": 1e6,
            "volume": {"m5": 100, **({} if h1 is None else {"h1": h1})}, "txns": {"m5": {"buys": 3, "sells": 2}}}

    class Client:
        def get(self, host, path):
            return type("Response", (), {"json": lambda self: [pair]})()

    asset = {"symbol": mint, "pool": pool, "quote_mint": quote_mint, "dex_id": "pumpswap", "creation": {"block_time": 1}}
    point = sim.quote(Client(), asset, 1_000)
    assert point["volume_h1_usd"] == expected and point["price"] == 0.5


def test_real_paper_session_records_entries_and_learns_from_the_closed_lot(tmp_path, monkeypatch):
    from tests.test_memecoin_cloud_runner import ThreePools, runner

    feed = ThreePools()
    monkeypatch.setattr(runner, "PublicClient", lambda **_: feed.client)
    real_tick, ticks = sim.tick, []

    def tick(hub, db, bus):
        if len(ticks) == 1:  # volume and buying pressure accelerate: momentum entry
            feed.pair.update(volume={"m5": 2000, "h1": 30000}, txns={"m5": {"buys": 12, "sells": 3}})
        if len(ticks) >= 2:  # price halves: the stop closes the lot
            feed.pair["priceUsd"] = str(float(feed.pair["priceUsd"]) * 0.5)
        ticks.append(1)
        return real_tick(hub, db, bus)

    monkeypatch.setattr(sim, "tick", tick)
    monkeypatch.setattr(runner, "build_analytics_report", None)
    runner.run(4, tmp_path / "session.sqlite", tmp_path / "report.json", initial_capital=30)
    with closing(sqlite3.connect(tmp_path / "session.sqlite")) as conn:
        state = json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (sim.KEY,)).fetchone()[0])
    [lot] = state["completed"]
    assert lot["strategy"] == "momentum" and lot["return"] < 0
    assert lot["entry"]["volume_h1_usd"] == 30000 and lot["entry"]["liquidity_usd"] > 20_000
    assert lot["sizing"]["multiplier"] == 1.0 and "aucune clôture" in lot["sizing"]["reason"]  # first lot: neutral
    report = learner.learn(tmp_path / "session.sqlite")
    momentum = report["strategies"]["momentum"]
    assert report["status"] == "learning" and momentum["closed_trades"] == 1 and 0.5 <= momentum["multiplier"] < 1.0
    assert report["recommendations"][0]["status"] == "insufficient_sample"


# -- D1 : open lots are evaluated, as one entry cohort ---------------------------------------------------------------------
def lot(strategy, mint, *, opened, initial=1.0, basis=1.0, mark=1.0, pnl=0.0, stage=0):
    return {"asset_domain": "memecoin", "strategy": strategy, "mint": mint, "opened_at": opened, "initial": initial,
            "basis": basis, "mark": mark, "pnl": pnl, "stage": stage, "qty": 1.0}


def session(positions, marks, completed=(), fills=(), stale=()):
    return {"positions": positions, "assets": {m: {"symbol": m, "last": {"price": 1.0, "ts": ts}} for m, ts in marks.items()},
            "fills": list(fills), "completed": list(completed), "stale_marks": list(stale)}


def test_open_lots_count_partial_sales_and_the_liquidation_mark_once_mature():
    positions = {
        "whale_tail/A": lot("whale_tail", "A", opened=0, initial=2.0, basis=1.6, mark=4.8, pnl=0.5, stage=1),  # young, one slice sold
        "momentum/B": lot("momentum", "B", opened=0, mark=0.9),  # held 3 h, nothing sold
        "dip_hunter/C": lot("dip_hunter", "C", opened=0, mark=1.3),  # 30 min, nothing sold: too young
        "sniper/D": lot("sniper", "D", opened=0, mark=2.0),  # stale quote: value unknown
        "sniper/E": {**lot("sniper", "E", opened=0, mark=3.0), "asset_domain": "majors"},  # another domain
    }
    rows = learner.open_lots(session(positions, {"A": 600, "B": 3 * 3600, "C": 1800, "D": 7200, "E": 7200}, stale=["D"]))
    assert [(r["strategy"], r["mint"]) for r in rows] == [("whale_tail", "A"), ("momentum", "B")]
    assert rows[0]["return"] == pytest.approx((0.5 + 4.8 - 1.6) / 2.0) and rows[0]["held_s"] == 600
    assert rows[1]["return"] == pytest.approx(-0.1) and rows[1]["held_s"] == 3 * 3600
    legacy = {"momentum/F": {**lot("momentum", "F", opened=None, mark=1.2)}}  # opened before opening times were recorded
    [row] = learner.open_lots(session(legacy, {"F": 100 + 3600}, fills=[{"strategy": "momentum", "mint": "F", "side": "buy", "ts": 100}]))
    assert row["held_s"] == 3600 and row["return"] == pytest.approx(0.2)


def bench_state():
    """The bench: whale_tail stops fast (14 lots at -20 %) while its winners stay open behind their trailing stop."""
    completed, positions, marks = [], {}, {}
    for i in range(20):
        if i % 3 == 2:  # 6 winners kept open: 20 % sold at 1.25x, the rest marked at x3
            positions[f"whale_tail/W{i}"] = lot("whale_tail", f"W{i}", opened=600 * i, basis=0.8, mark=2.4, pnl=0.05, stage=1)
            marks[f"W{i}"] = 600 * 20 + 2 * 3600
        else:
            completed.append({"strategy": "whale_tail", "mint": f"L{i}", "return": -0.2, "opened_at": 600 * i})
        completed.append({"strategy": "momentum", "mint": f"M{i}", "return": -0.2, "opened_at": 600 * i})
    return session(positions, marks, completed)


def test_bench_scenario_the_winner_kept_open_lifts_its_strategy_above_one():
    state = bench_state()
    assert learner.multipliers(state["completed"])["whale_tail"]["multiplier"] == 0.5  # closed-only: survivorship bias
    result = learner.multipliers_for_state(state)
    whale = result["whale_tail"]
    assert (whale["closed_trades"], whale["open_lots"], whale["evaluated_lots"]) == (14, 6, 20)
    assert whale["avg_profit_pct"] == pytest.approx(100 * (14 * -0.2 + 6 * (0.05 + 2.4 - 0.8)) / 20)
    assert whale["upside_unlocked"] and whale["multiplier"] > 1.0
    assert "whale_tail : 14 clôtures récentes et 6 positions ouvertes évaluées" in whale["reason"]
    assert result["momentum"]["multiplier"] == 0.5  # a strategy that only stops stays at the bottom of its range
    report = learner.learn_from_state(state)
    assert report["open_lots_evaluated"] == 6 and report["scoring"] == "lots-v2" and report["status"] == "learning"


def test_old_open_survivors_leave_the_window_as_newer_entries_arrive():
    positions = {"dip_hunter/OLD": lot("dip_hunter", "OLD", opened=0, basis=0.5, mark=5.0, pnl=0.4, stage=2)}
    completed = [{"strategy": "dip_hunter", "mint": f"N{i}", "return": -0.2, "opened_at": 3600 + 60 * i} for i in range(20)]
    d = learner.multipliers_for_state(session(positions, {"OLD": 10_000}, completed))["dip_hunter"]
    assert (d["evaluated_lots"], d["window_trades"], d["open_lots"]) == (21, 20, 1)  # the old winner is outside the window
    assert d["avg_profit_pct"] == pytest.approx(-20) and d["multiplier"] == 0.5
    assert d["reason"].startswith("dip_hunter : 20 clôtures récentes, profit moyen -20,0 %")


def test_latent_gains_alone_never_raise_a_stake():
    positions = {f"sniper/O{i}": lot("sniper", f"O{i}", opened=i, mark=3.0, stage=1) for i in range(8)}
    completed = [{"strategy": "sniper", "mint": f"C{i}", "return": 0.5} for i in range(2)]
    s = learner.multipliers_for_state(session(positions, {f"O{i}": 9000 for i in range(8)}, completed))["sniper"]
    assert (s["evaluated_lots"], s["closed_trades"], s["upside_unlocked"], s["multiplier"]) == (10, 2, False, 1.0)
    assert "(hausse bloquée : 10/8 positions évaluées, 2/4 clôtures)" in s["reason"]


# -- D2 : a run of stops never freezes a strategy ------------------------------------------------------------------------
def test_exploration_floor_is_small_and_never_under_the_minimum_stake():
    assert learner.exploration_floor(100.0, 1.0) == pytest.approx(0.5)
    assert learner.exploration_floor(100.0, 0.5) == pytest.approx(0.25)
    assert learner.exploration_floor(30.0, 0.5) == pytest.approx(0.075)
    assert learner.exploration_floor(5.0, 0.5) == learner.exploration_floor(-1.0, 1.0) == 0.05


def test_a_run_of_stops_never_freezes_a_strategy():
    history = closed("momentum", [-0.2] * 20)
    assert MemecoinIsolatedLearner(history).fractions["momentum"] == 0.0  # Kelly alone would stop trading for good
    state = paper_state(history)
    for k in range(5):
        mint = f"T{k}"
        state["assets"][mint] = {"symbol": mint}
        sim.process(state, mint, dict(POINT, ts=100 + k), ["momentum"], 0.001)
        sizing = state["positions"][f"momentum/{mint}"]["sizing"]
        assert sizing["binding"] == "exploration_floor" and sizing["kelly_target_usd"] == 0.0
        assert sizing["multiplier"] == 0.5 and state["positions"][f"momentum/{mint}"]["initial"] == pytest.approx(sizing["exploration_floor_usd"])
    assert sum(f["side"] == "buy" for f in state["fills"]) == 5  # five signals, five small stakes: the strategy keeps learning
    assert state["positions"]["momentum/T0"]["sizing"]["exploration_floor_usd"] == pytest.approx(0.005 * 100.0 * 0.5)


# -- D3 : thresholds must raise the mean out of sample and beat chance ------------------------------------------------------
def noise_history(seed, n=60):
    """Bench-like returns (fast stops, small and big winners) with liquidity and volume drawn independently of them."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        u = rng.random()
        ret = rng.uniform(-0.35, -0.18) if u < 0.65 else rng.uniform(-0.05, 0.4) if u < 0.9 else rng.uniform(1.0, 6.0)
        rows.append({"strategy": "momentum", "mint": f"M{i}", "return": ret, "opened_at": 60 * i,
                     "entry": {"liquidity_usd": rng.uniform(20_000, 300_000), "volume_h1_usd": rng.uniform(5_000, 120_000),
                               "confidence": 0.55}})
    return rows


def test_a_random_criterion_does_not_produce_false_thresholds():
    outcomes = []
    for seed in range(100):
        for rec in learner.learn_from_state({"completed": noise_history(seed)})["recommendations"]:
            outcomes.append((rec["status"], rec["rejection"]))
            assert rec["applied"] is False
    suggested = sum(status == "suggested" for status, _ in outcomes)
    # alpha = 1 % per criterion: 200 random criteria here give 3 (1.5 %); 1,000 noise histories measured 0.9-1.0 %.
    assert suggested <= 4
    assert {r for _, r in outcomes} >= {"cross_validation", "noise"}  # both guards are exercised


def test_a_threshold_that_lowers_the_mean_is_never_suggested():
    # The bench case: mostly stops whatever the depth, deeper pools slightly worse.
    rows = []
    for i in range(40):
        liquidity = 20_000 + 2_000 * i
        ret = (0.3 if i % 5 == 0 else -0.2) - (0.05 if liquidity > 60_000 else 0.0)
        rows.append({"strategy": "momentum", "mint": f"M{i}", "return": ret, "opened_at": i,
                     "entry": {"liquidity_usd": liquidity, "volume_h1_usd": 8_000, "confidence": 0.55}})
    rec = learner.learn_from_state({"completed": rows})["recommendations"][0]
    assert (rec["status"], rec["rejection"], rec["suggested_threshold"], rec["applied"]) == ("no_improvement", "no_threshold", None, False)
    deep_half = sorted(rows, key=lambda r: r["entry"]["liquidity_usd"])[20:]
    # The former criterion (sum of the kept returns) would have kept this half: its sum is higher, its mean lower.
    assert sum(r["return"] for r in deep_half) > sum(r["return"] for r in rows)
    assert statistics.fmean(r["return"] for r in deep_half) < statistics.fmean(r["return"] for r in rows)


def test_a_real_effect_is_confirmed_out_of_sample_and_beyond_chance():
    rng = random.Random(7)
    rows = []
    for i in range(60):
        liquidity = rng.uniform(20_000, 200_000)
        win = rng.random() < (0.6 if liquidity >= 80_000 else 0.15)
        ret = rng.uniform(0.2, 1.0) if win else rng.uniform(-0.3, -0.18)
        rows.append({"strategy": "dip_hunter", "mint": f"D{i}", "return": ret, "opened_at": i,
                     "entry": {"liquidity_usd": liquidity, "volume_h1_usd": rng.uniform(5_000, 120_000), "confidence": 0.55}})
    liquidity, volume, _ = learner.learn_from_state({"completed": rows})["recommendations"]
    assert liquidity["status"] == "suggested" and liquidity["p_value"] <= 0.01 and liquidity["applied"] is False
    assert all(f["confirmed"] and f["test_avg_profit_pct_after"] > f["test_avg_profit_pct_before"] for f in liquidity["cross_validation"])
    assert liquidity["avg_profit_pct_after"] > liquidity["avg_profit_pct_before"]
    assert 50_000 <= liquidity["suggested_threshold"] <= 110_000
    assert "confirmée hors échantillon" in liquidity["explanation"] and "avant toute adoption humaine" in liquidity["explanation"]
    assert volume["status"] == "no_improvement"  # drawn at random: rejected


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
