"""Position de fond (double élan journalier, SPEC.md chantier Moteur court terme) : décisions prises sur les seules
clôtures de la veille, capital en USDC quand rien ne monte, meneuses retenues, frais déduits. Hors ligne."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import campaign, core


def table(paths, start_day=20_000):
    days = np.arange(start_day, start_day + len(next(iter(paths.values()))))
    closes = pd.DataFrame(paths, index=days, dtype=float)
    opens = closes.shift(1).fillna(closes.iloc[0])
    return opens, closes


def test_decisions_use_only_closes_known_the_day_before():
    rng = np.random.default_rng(0)
    paths = {s: 100 * np.exp(np.cumsum(rng.normal(0.002, 0.03, 120))) for s in "ABCDE"}
    _, closes = table(paths)
    want = core.targets(closes)
    d = closes.index[70]
    altered = closes.copy()
    altered.loc[d:] *= rng.uniform(0.3, 3.0, size=altered.loc[d:].shape)
    assert want.loc[:d].equals(core.targets(altered).loc[:d])


def test_capital_stays_in_usdc_when_nothing_trends_up():
    down = {s: np.linspace(100, 40 + i, 90) for i, s in enumerate("ABCD")}
    opens, closes = table(down)
    r = core.run(opens, closes, int(closes.index[30]) * core.DAY_MS, int(closes.index[-1]) * core.DAY_MS, 1000.0)
    assert r["trades"] == [] and r["final_equity"] == pytest.approx(1000.0)


def test_holds_the_three_strongest_eligible_leaders():
    n = 60
    paths = {"A": 100 * 1.004 ** np.arange(n), "B": 100 * 1.010 ** np.arange(n), "C": 100 * 1.007 ** np.arange(n),
             "D": 100 * 1.002 ** np.arange(n), "E": 100 * 0.99 ** np.arange(n)}
    _, closes = table(paths)
    held = core.targets(closes).iloc[-1]
    assert set(held[held].index) == {"A", "B", "C"}


def test_round_trip_costs_are_paid_once_per_side():
    n = 40
    flat = {"A": np.r_[100 * 1.01 ** np.arange(30), np.full(n - 30, 100 * 1.01 ** 29)]}
    opens, closes = table(flat)
    closes.iloc[-8:] = closes.iloc[-8:] * 0.5  # la tendance casse : vente
    opens = closes.shift(1).fillna(closes.iloc[0])
    r = core.run(opens, closes, int(closes.index[25]) * core.DAY_MS, int(closes.index[-1] + 1) * core.DAY_MS, 1000.0)
    sides = [t["side"] for t in r["trades"]]
    assert sides[0] == "achat" and "vente" in sides
    buy, sell = r["trades"][0], next(t for t in r["trades"] if t["side"] == "vente")
    side = core.CoreRules().fee_per_side + core.CoreRules().slippage_per_side
    p_buy = opens.at[buy["day"], "A"]
    p_sell = opens.at[sell["day"], "A"]
    # Une seule crypto éligible : elle reçoit un tiers du capital (une place sur trois), le reste reste en USDC.
    assert buy["value"] == pytest.approx(1000.0 / 3)
    assert sell["value"] == pytest.approx(1000.0 / 3 * (1 - side) ** 2 * p_sell / p_buy)


def test_system_equity_combines_the_core_and_short_term_sleeves():
    fund = {"capital": 700.0, "final_equity": 770.0, "trades": [], "curve": [(2 * core.DAY_MS, 735.0), (3 * core.DAY_MS, 770.0)]}
    tactical = {"capital": 300.0, "final_equity": 303.0, "trades": [{}], "curve": [(0, 300.0), (core.DAY_MS + 5, 303.0)]}
    out = campaign._combine(fund, tactical, 1000.0, core.DAY_MS, 3 * core.DAY_MS)
    assert out["return_pct"] == pytest.approx(7.3) and out["max_drawdown_pct"] == pytest.approx(0.0)


def test_trailing_stop_sells_intraday_and_waits_three_days_before_buying_back():
    n = 60
    up = 100 * 1.01 ** np.arange(n)
    opens, closes = table({"A": up})
    lows = closes * 0.995
    d = closes.index[40]
    peak_close = closes.at[d - 1, "A"]
    lows.at[d, "A"] = 0.85 * peak_close  # chute dans la journée, sous le stop de 10 %
    rules = core.CoreRules(trailing_stop=0.10)
    r = core.run(opens, closes, int(closes.index[30]) * core.DAY_MS, int(closes.index[-1] + 1) * core.DAY_MS, 1000.0,
                 rules, lows=lows)
    stop = next(t for t in r["trades"] if t["side"] == "stop")
    assert stop["day"] == d
    rebuy = [t["day"] for t in r["trades"] if t["side"] == "achat" and t["day"] > d]
    assert rebuy and rebuy[0] == d + 4  # bloquée les jours d + 1 à d + 3
    units_value = stop["value"] / (1 - rules.fee_per_side - rules.slippage_per_side)
    first_buy = next(t for t in r["trades"] if t["side"] == "achat")
    units = first_buy["value"] * (1 - rules.fee_per_side - rules.slippage_per_side) / opens.at[first_buy["day"], "A"]
    assert units_value == pytest.approx(units * 0.9 * peak_close)  # exécution au stop, pas au plus bas


def test_market_filter_keeps_everything_in_usdc_when_btc_trends_down():
    n = 80
    paths = {"BTCUSDC": 100 * 0.99 ** np.arange(n), "A": 100 * 1.01 ** np.arange(n), "B": 100 * 1.02 ** np.arange(n)}
    _, closes = table(paths)
    assert core.targets(closes).iloc[-1][["A", "B"]].all()
    assert not core.targets(closes, core.CoreRules(market_filter="BTCUSDC")).iloc[-1].any()


def test_year_runs_the_core_continuously_without_monthly_reset():
    from sentinelle.scalp.data import month_bounds, months
    rng = np.random.default_rng(5)
    first_day = month_bounds("2025-07")[0] // core.DAY_MS
    n = 430
    paths = {s: 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n))) for s in ("BTCUSDC", "A", "B", "C")}
    opens, closes = table(paths, start_day=first_day)
    lows = np.minimum(opens, closes) * 0.99
    monthly = {s: {} for s in paths}
    for m in months("2025-07", "2026-08"):
        last_day = month_bounds(m)[1] // core.DAY_MS
        for s in paths:
            monthly[s][m] = float(closes.at[last_day, s])
    monthly["_daily"] = (opens, closes, lows)
    variant = campaign.VARIANTS["v7"]
    year = campaign.year_test(pd.DataFrame(columns=["decided_at", "exit_ms"]), monthly, "2026-08", variant=variant)
    start, end = month_bounds("2025-09")[0], month_bounds("2026-08")[1] + 1
    direct = core.run(opens, closes, start, end, 1000.0, variant.core_rules, lows=lows)
    assert year["system"]["return_pct"] == pytest.approx(100 * (direct["final_equity"] / 1000.0 - 1))
    assert len(year["monthly"]) == 12 and year["months"][0] == "2025-09"
    total = np.prod([1 + r["system_pct"] / 100 for r in year["monthly"]])
    assert total == pytest.approx(direct["final_equity"] / 1000.0, rel=1e-9)
