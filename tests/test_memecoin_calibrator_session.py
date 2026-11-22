"""The calibrator sizes its lots exactly like the paper session (brain v2 loop, iteration 6).

``session_stake`` is ``memecoin_simulator._buy`` for the MODERATE profile: Kelly target x budget weight x adaptive
multiplier (``memecoin_learner``), never under the exploration floor, within the token cap, the strategy budget and the
cash reserve; the sizing equity values open lots at their liquidation value, partial proceeds are cash when sold, and
the fleet stops new entries at 50 % session loss or 12 open lots. Under these stakes the session takes almost every
signal; in the rug-pull stress mix it runs into its 50 % loss stop on many seeds (14 of 20 at half Kelly), and the
exploration floor is what drives it. calibration-v5-session-sizing is recorded, not promoted. Synthetic regimes, offline, paper only."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_learner as ml, memecoin_simulator as sim, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

WINS = [{"strategy": "momentum", "return": 0.8, "asset_domain": "memecoin"} for _ in range(12)]
LOSSES = [{"strategy": "sniper", "return": -0.2, "asset_domain": "memecoin"} for _ in range(12)]


def session_state(cash, lots, completed, stale=()):
    positions = {f"{strategy}/M{k}": {"mint": f"M{k}", "strategy": strategy, "basis": basis, "initial": basis, "mark": mark,
                                      "qty": 1.0, "stage": 0, "pnl": 0.0, "high": 1.0, "asset_domain": "memecoin"}
                 for k, (strategy, basis, mark) in enumerate(lots)}
    return {"cash": cash, "positions": positions, "completed": completed, "fills": [], "stale_marks": list(stale), "assets": {}}


STATES = [session_state(30.0, [], []), session_state(30.0, [], WINS + LOSSES),
          session_state(10.0, [("momentum", 5.0, 6.0)] * 3, WINS), session_state(8.0, [("sniper", 2.0, 1.5)], LOSSES),
          session_state(30.0, [("momentum", 2.0, float("nan"))], WINS, stale=["M0"]), session_state(0.2, [], [])]


@pytest.mark.parametrize("k", range(len(STATES)))
@pytest.mark.parametrize("strategy", ms.NAMES)
def test_the_stake_is_exactly_the_one_of_the_paper_session(k, strategy):
    state = {**STATES[k], "positions": dict(STATES[k]["positions"]), "fills": []}
    learner, adaptive = ms.MemecoinIsolatedLearner(state["completed"]), ml.multipliers(state["completed"])
    used = sum(p["basis"] for p in state["positions"].values() if p["strategy"] == strategy)
    expected = C.session_stake(equity=sim.sizing_equity(state), known=sim.known_equity(state), cash=state["cash"],
                               strategy_used=used, exposed=0.0, fraction=learner.fractions[strategy],
                               weight=learner.weights[strategy], multiplier=adaptive[strategy]["multiplier"], network=0.002)
    sim._buy(state, "NEW", strategy, strategy + "/NEW", {"price": 1.0, "liquidity_usd": 1e6, "ts": 1000}, 0.002, [strategy],
             learner, adaptive, {}, entry={})
    opened = state["positions"].get(strategy + "/NEW")
    assert (opened["basis"] if opened else 0.0) == pytest.approx(expected, abs=1e-12)


def test_lot_paths_follow_the_scalar_replay_at_every_fill():
    p = C.generate(9, paths_per_strategy=15)
    r, reasons, steps, lots = C.lot_paths(p, ms.CALIBRATED)
    r2, _, steps2 = C.replay(p, ms.CALIBRATED, with_steps=True)
    assert np.array_equal(r, r2, equal_nan=True) and np.array_equal(steps, steps2)
    checked = 0
    for i in range(len(r)):
        ri, events = C.trade_return(p.price[i], p.liquidity[i], p.network[i], C.REFERENCE_SIZE)
        if math.isnan(ri):
            continue
        realized, remaining, stage = 0.0, 1.0, 0
        for e in events:
            sold = remaining * e[2]
            realized, remaining = realized + sold * e[3], remaining - sold
            stage += e[1] == "take_profit"
            remaining = 0.0 if e[2] >= 1.0 or remaining <= 1e-9 else remaining
            assert lots.realized[i, e[0]] == pytest.approx(realized, abs=1e-3)  # events round the fill ratio
            assert lots.remaining[i, e[0]] == pytest.approx(remaining, abs=1e-12) and lots.stage[i, e[0]] == stage
            checked += 1
    assert checked > 50


def flat(n, steps=6):
    paths = C.Paths(np.zeros(n, int), np.zeros(n, int), np.ones((n, steps)), np.full((n, steps), 1e6), np.full((n, steps), 0.002),
                    np.ones(n))
    lots = C.LotPaths(np.zeros((n, steps)), np.ones((n, steps)), np.zeros((n, steps), int), np.ones((n, steps)))
    return paths, lots


def test_no_entry_beyond_the_session_loss_stop_nor_with_twelve_open_lots(monkeypatch):
    monkeypatch.setattr(C, "session_stake", lambda **kw: 5.0)
    paths, lots = flat(10)
    run = C._portfolio_session(paths, np.full(10, -1.0), multiplier=0.5, close_steps=np.full(10, 2), arrival_gap=20.0, lots=lots)
    assert (run["trades"], run["refused_open_lots"]) == (3, 7)  # 30 -> 25 -> 20 -> 15: the 50 % stop holds every later signal
    monkeypatch.setattr(C, "session_stake", lambda **kw: 0.5)
    paths, lots = flat(14)
    run = C._portfolio_session(paths, np.full(14, 0.1), multiplier=0.5, close_steps=np.full(14, 5), arrival_gap=0.1, lots=lots)
    assert (run["trades"], run["refused_open_lots"]) == (12, 2)


def test_the_former_settings_stay_reproducible():
    assert C.calibration_version("causal", 0.10, 20.0) == C.calibration_version("causal", 0.10, 20.0, "learner") == "calibration-v4-overlap"
    assert C.calibration_version("causal", 0.10, 20.0, "session") == "calibration-v5-session-sizing"
    assert C.calibration_version("causal", 0.10, None, "session") == "calibration-v3-causal-drift"  # sequential: learner sizing
    assert C.MemecoinCalibrator().sizing == "session"
    with pytest.raises(ValueError):
        C.MemecoinCalibrator(sizing="flat")


@pytest.fixture(scope="module")
def v5():
    cal = C.MemecoinCalibrator()
    return cal, cal.calibrate()


def test_session_sizing_is_recorded_as_a_new_version_and_promotes_nothing(v5):
    _, result = v5
    rec, v1 = C.RECORDED_SELECTIONS["calibration-v5-session-sizing"], C.RECORDED_SELECTIONS["calibration-v1"]
    sel = result["selected"]
    assert (result["version"], result["sizing"]) == ("calibration-v5-session-sizing", "session")
    assert sel["policy"] == rec["policy"] != v1["policy"] and sel["kelly_multiplier"] == rec["kelly_multiplier"] == 0.25
    dev = rec["development"]
    assert sel["calibration"]["compound_return"]["median"] == pytest.approx(dev["compound_return_median"], rel=1e-4)
    assert sel["calibration"]["max_drawdown"]["max"] == pytest.approx(dev["max_drawdown_max"], abs=1e-4)
    assert sel["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(dev["stress_worst_equity_min"], abs=1e-4)
    [half] = [c for c in result["candidates"] if c["policy"] == v1["policy"] and c["kelly_multiplier"] == 0.5]
    assert not half["survives"] and half["rug_stress"]["worst_equity_ratio"]["min"] == pytest.approx(0.4849, abs=1e-3)
    assert ms.KELLY_MULTIPLIER == 0.5 and rec["in_session"] is False and sim.EXIT_POLICIES[sim.CALIBRATED_EXITS] is ms.CALIBRATED
    assert ml.EXPLORATION_FRACTION == 0.005  # the session's exploration floor is reported, not changed


def test_under_the_session_stakes_the_loss_stop_is_reached_often_and_the_exploration_floor_drives_it(v5, monkeypatch):
    cal, _ = v5
    profile = cal.survival_profile(ms.CALIBRATED)
    failing = {m: tuple(r["seed"] for r in v["seeds"] if r["worst_equity_ratio"] < C.MIN_STRESS_EQUITY) for m, v in profile.items()}
    assert failing == C.RECORDED_SURVIVAL["calibration-v5-session-sizing"]
    assert (profile[0.5]["failures"], profile[0.25]["failures"], profile[0.1]["failures"]) == (14, 9, 0)
    monkeypatch.setattr(ml, "exploration_floor", lambda equity, multiplier: 0.0)  # report-only decomposition
    without_floor = cal.survival_profile(ms.CALIBRATED)
    assert all(v["failures"] == 0 for v in without_floor.values())


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
