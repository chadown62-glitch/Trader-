"""Accounting invariants of the portfolio sized like the paper session (brain v2 loop, iteration 10).

Instrumented on every lot of several seeds, mixes, Kelly multipliers and both exploration rules: the final equity minus
the initial capital is exactly the sum of the realized P&L; every stake respects the free cash, the reserve, the token
cap and the strategy budget it was sized with; partial proceeds counted as cash never exceed the lot's final proceeds
and never decrease; no lot is realized twice or before its closing step. Synthetic, offline, paper only."""

import math

import numpy as np
import pytest

from sentinelle.dmh import memecoin_calibrator as C, memecoin_strategies as ms
from tests import test_configurable_capital as frozen

CASES = [(seed, mixes, m, mode) for seed, mixes in ((11, C.MIXES), (12, C.RUG_STRESS), (101, C.NO_EXPLOSIVE))
         for m in (0.1, 0.5) for mode in C.EXPLORATIONS]


@pytest.mark.parametrize("seed,mixes,multiplier,exploration", CASES)
def test_no_fictitious_money_is_created_or_lost(monkeypatch, seed, mixes, multiplier, exploration):
    p = C.generate(seed, mixes=mixes)
    r, _, steps, lots = C.lot_paths(p, ms.CALIBRATED)
    stakes, sized, pops = {}, [], []
    push, pop, stake = C.heapq.heappush, C.heapq.heappop, C.session_stake

    def recording_push(heap, item):
        stakes[item[1]] = sized[-1]
        return push(heap, item)

    def recording_pop(heap):
        item = pop(heap)
        pops.append(item)
        return item

    def recording_stake(**kw):
        size = stake(**kw)
        sized.append((size, kw))
        return size

    monkeypatch.setattr(C.heapq, "heappush", recording_push)
    monkeypatch.setattr(C.heapq, "heappop", recording_pop)
    monkeypatch.setattr(C, "session_stake", recording_stake)
    run = C._portfolio_session(p, r, multiplier=multiplier, close_steps=steps, arrival_gap=C.ARRIVAL_GAP_MIN, lots=lots,
                               exploration=exploration)
    # 1. conservation: equity change = realized P&L of the lots actually opened
    assert run["final_equity"] - C.INITIAL == pytest.approx(sum(s * r[i] for i, (s, _) in stakes.items()), abs=1e-9)
    assert run["trades"] == len(stakes) == len(pops)
    # 2. every stake within the limits it was sized with, never more than the free cash
    profile = ms.PROFILES[ms.DEFAULT_PROFILE]
    for size, kw in sized:
        if size <= 0:
            continue
        reserve = kw["cash"] - profile.cash_reserve * max(kw["equity"], kw["known"] or kw["equity"])
        assert size <= reserve + 1e-12 and size <= profile.token_cap * kw["equity"] + 1e-12
        assert size <= kw["equity"] * kw["weight"] - kw["strategy_used"] + 1e-12 and kw["cash"] - size >= 0
    # 3. partial proceeds never exceed the final ones and never decrease; 4. each lot realized once, at its close
    for i, (s, _) in stakes.items():
        path = lots.realized[i, : int(steps[i]) + 1]
        assert (np.diff(path) >= -1e-12).all() and path.max() <= 1 + r[i] + 1e-9
    assert len({j for _, j in pops}) == len(pops)
    arrivals = {i: i * C.ARRIVAL_GAP_MIN for i in stakes}
    assert all(math.isclose(t, arrivals[j] + float(steps[j])) for t, j in pops)


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)
