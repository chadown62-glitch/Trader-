"""Tests du cerveau de l'IA : indicateurs, absence de fuite d'information, étiquetage."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.ai.exits import ExitState, end_of_bar, r_multiple, step
from sentinelle.ai.features import FEATURE_COLUMNS, compute_features, warmup_bars
from sentinelle.ai.indicators import atr, rsi
from sentinelle.ai.labeling import label_candidates, non_overlapping
from sentinelle.ai.strategies import STRATEGIES, signals

from .helpers import make_series


def test_rsi_extremes_and_bounds():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(up).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert rsi(down).iloc[-1] == pytest.approx(0.0)
    rnd = pd.Series(100 + np.cumsum(np.random.default_rng(1).standard_normal(500)))
    v = rsi(rnd).dropna()
    assert ((v >= 0) & (v <= 100)).all()


def test_atr_constant_range():
    df = pd.DataFrame({"open": [10.0] * 50, "high": [11.0] * 50, "low": [9.0] * 50, "close": [10.0] * 50})
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)


def test_features_have_no_lookahead():
    """Ajouter des bougies futures ne doit jamais changer les valeurs passées."""
    df = make_series(6000, seed=3)
    btc = make_series(6000, seed=4, start=30000)
    n = 5200
    full = compute_features(df, "1h", btc=btc)
    part = compute_features(df.iloc[:n].reset_index(drop=True), "1h", btc=btc.iloc[:n].reset_index(drop=True))
    cols = FEATURE_COLUMNS + ["atr", "ema20", "ema50", "don_high", "bb_lower", "macd_hist"]
    a = full.loc[: n - 1, cols].to_numpy(dtype=float)
    b = part[cols].to_numpy(dtype=float)
    assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-12)
    assert (full.loc[: n - 1, "regime"].to_numpy() == part["regime"].to_numpy()).all()


def test_live_window_matches_full_history():
    """En direct, l'IA recalcule sur une fenêtre récente : le résultat doit être identique."""
    df = make_series(9000, seed=5)
    full = compute_features(df, "1h")
    w = warmup_bars("1h")
    window = df.iloc[-w:].reset_index(drop=True)
    live = compute_features(window, "1h")
    last_full = full.iloc[-1][FEATURE_COLUMNS].to_numpy(dtype=float)
    last_live = live.iloc[-1][FEATURE_COLUMNS].to_numpy(dtype=float)
    assert np.allclose(last_full, last_live, rtol=1e-6, atol=1e-9, equal_nan=True)
    sig_full = {k: (len(full) - 1 in set(v)) for k, v in signals(full).items()}
    sig_live = {k: (len(live) - 1 in set(v)) for k, v in signals(live).items()}
    assert sig_full == sig_live


def test_exit_stop_first_when_both_hit():
    s = ExitState(entry=100, stop=98, target=104, initial_stop=98, horizon_bars=10, fee_rate=0.001, slip=0.0)
    px, reason = step(s, 100, 105, 97, 101)
    assert reason == "stop" and px == 98


def test_exit_gap_below_stop_fills_at_open():
    s = ExitState(entry=100, stop=98, target=104, initial_stop=98, horizon_bars=10, slip=0.0)
    px, reason = step(s, 95, 96, 94, 95)
    assert reason == "stop" and px == 95


def test_exit_time_and_breakeven():
    s = ExitState(entry=100, stop=98, target=110, initial_stop=98, horizon_bars=3, breakeven_r=1.0, fee_rate=0.001, slip=0.0)
    moved, timed = end_of_bar(s, h=102.5, c=102)  # +1.25 R atteint : stop au point mort
    assert moved and s.stop == pytest.approx(100 * 1.002)
    px, reason = step(s, 102, 102.4, 101, 101.5)
    assert px is None
    px, reason = step(s, 101.5, 101.8, 100.5, 101)
    assert reason == "time" and px == 101


def test_r_multiple_includes_fees():
    assert r_multiple(100, 102, 98, 0.0) == pytest.approx(1.0)
    assert r_multiple(100, 102, 98, 0.001) < 1.0
    assert r_multiple(100, 100, 98, 0.001) < 0  # sortie au prix d'entrée = perte (frais)


def test_labels_match_manual_simulation():
    df = make_series(7000, seed=11)
    feat = compute_features(df, "1h")
    cand = label_candidates(feat, "TEST/USDC", "1h", fee_rate=0.001, slip=0.0005)
    res = cand[cand["outcome"].notna()]
    assert len(res) > 50
    o, h, l, c = (feat[k].to_numpy() for k in ("open", "high", "low", "close"))
    for _, row in res.sample(25, random_state=0).iterrows():
        st = STRATEGIES[row["strategy"]]
        i = int(row["idx"])
        entry = o[i + 1] * 1.0005
        state = ExitState(entry, row["stop"], row["target"], row["stop"], st.horizon_bars, st.breakeven_r, 0.001, 0.0005)
        for j in range(i + 1, len(o)):
            px, reason = step(state, o[j], h[j], l[j], c[j])
            if px is not None:
                break
        assert reason == row["outcome"]
        assert r_multiple(entry, px, row["stop"], 0.001) == pytest.approx(row["outcome_r"])
        assert row["decision_ts"] < row["exit_ts"]


def test_non_overlapping_one_position_per_symbol():
    cand = pd.DataFrame({
        "symbol": ["A", "A", "A", "B"],
        "decision_ts": [0, 5, 20, 5],
        "exit_ts": [10, 15, 30, 8],
        "outcome": ["target"] * 4,
    })
    assert non_overlapping(cand).tolist() == [True, False, True, True]
