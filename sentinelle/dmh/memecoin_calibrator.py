"""Deterministic stress-test and calibration bench for the four paper memecoin strategies (asset_domain="memecoin").

What it simulates (fictitious funds only, no network, no key, no order):
- Solana regimes drawn from fixed seeds: masked rug pulls (healthy pump, then the pool is drained in one step), slippage
  spikes (temporary liquidity collapse), false breakouts, healthy pullbacks (the dip_hunter case), smart-wallet
  confluences (some of them dumped on followers), explosive runs (x3 to x15) and slow bleeds;
- execution frictions that mirror the paper simulator: 0.3 % DEX fee per swap, constant-product price impact
  (at least 1 %), a network fee per swap that spikes x20 during congestion;
- each strategy meets its own mix of regimes (assumptions declared in ``MIXES``).

How it calibrates:
1. every exit policy of the grid is replayed on all calibration paths at once (vectorised), at a reference size;
2. the best exit policies (expected log-growth) then run in a compounding portfolio of 30 USD with capped fractional
   Kelly sizing, confidence and liquidity scaling, and budget reallocation toward the recently best strategies;
3. the selected setting maximises the median compound return across calibration batches, under survival constraints
   (maximum drawdown and worst equity in the intense rug-pull stress set);
4. it is then reported on HELD-OUT seeds (synthetic out-of-sample) against the legacy thresholds of the paper session.

Execution contract (``execution``): ``"causal"`` (default, the contract of the paper session since #91) decides on the
observation of step T and fills on the observation of step T+1: the entry signalled at step 0 is filled at step 1, a lot
is not judged on the observation that filled it, an exit decided on the net ratio of step T is sold at the net ratio of
step T+1, a take-profit tier advances when its slice is sold, and no new exit is decided while one is pending. The
entry is filled only on a finite quote of a pool with some depth and, as in the paper session (``MAX_ENTRY_DRIFT``),
only if its price moved by at most 10 % since the signal; otherwise there is no trade (NaN). A missing
(NaN) or worthless (ratio <= 0) next observation sells nothing: the order stays pending and no fictitious proceeds are
invented; what is still held at the end of the horizon is sold at the last observation, and counts for 0 when that
observation is missing or worthless (prudent floor, as ``floor_usd`` in the paper session): a pending sale is never
credited at its decision price. ``"immediate"`` is the former contract (decision and fill on the same observation,
optimistic), kept only to reproduce ``calibration-v1``.

These are synthetic regimes built from assumptions: a calibrated setting is a prudent default, not evidence of profit.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass

import numpy as np

from . import memecoin_strategies as ms
from .memecoin_simulator import MAX_ENTRY_DRIFT as SESSION_ENTRY_DRIFT  # the session's own guard, not a new parameter

ASSET_DOMAIN = "memecoin"
INITIAL = 30.0
DEX_FEE = 0.003  # same scenario assumption as the paper simulator
MIN_IMPACT = 0.01  # the simulator charges at least 1 % impact per swap
NETWORK_USD = 0.002  # ~5000 lamports + priority fee at the 75th percentile, SOL ~ 140 USD
CONGESTION_MULT = 20.0
STEPS = 240  # one step = one minute: four hours of life after entry
REFERENCE_SIZE = 1.5  # USD per trade for the vectorised exit grid (5 % of 30 USD)
REGIMES = ("masked_rug", "slippage_spike", "false_breakout", "healthy_dip", "smart_confluence", "explosive", "slow_bleed")
MIXES = {  # share of each regime met by each strategy at entry (declared assumptions)
    "sniper": {"masked_rug": 0.30, "slow_bleed": 0.25, "false_breakout": 0.15, "explosive": 0.10, "slippage_spike": 0.10,
               "healthy_dip": 0.05, "smart_confluence": 0.05},
    "momentum": {"false_breakout": 0.30, "masked_rug": 0.15, "slow_bleed": 0.15, "explosive": 0.15, "healthy_dip": 0.10,
                 "slippage_spike": 0.10, "smart_confluence": 0.05},
    "dip_hunter": {"healthy_dip": 0.35, "masked_rug": 0.20, "slow_bleed": 0.20, "explosive": 0.10, "false_breakout": 0.05,
                   "slippage_spike": 0.10},
    "whale_tail": {"smart_confluence": 0.35, "masked_rug": 0.15, "false_breakout": 0.15, "explosive": 0.15, "slow_bleed": 0.10,
                   "healthy_dip": 0.05, "slippage_spike": 0.05},
}
LIQUIDITY0 = {"sniper": 8_000.0, "momentum": 40_000.0, "dip_hunter": 60_000.0, "whale_tail": 50_000.0}
RUG_STRESS = {name: {"masked_rug": 0.60, "slippage_spike": 0.15, "false_breakout": 0.15, "explosive": 0.10} for name in ms.NAMES}
# Robustness: the same mixes WITHOUT any explosive run (their share goes to slow bleeds).
NO_EXPLOSIVE = {name: {**{k: v for k, v in mix.items() if k != "explosive"},
                       "slow_bleed": mix.get("slow_bleed", 0.0) + mix.get("explosive", 0.0)} for name, mix in MIXES.items()}
GRID = {  # bounded by design: stop 15-20 %, first slice as soon as costs are covered (net ratio 1.05-1.25), room for x3-x10+
    "stop_loss": (0.15, 0.175, 0.20),
    "cover_ratio": (1.05, 1.10, 1.15, 1.25),
    "cover_fraction": (0.20, 0.25, 0.33),
    "tiers": ((2.0, 3.0, 5.0), (2.0, 4.0, 8.0), (3.0, 6.0, 10.0)),
    "trail_base": (0.20, 0.25, 0.30, 0.35),
    "trail_slope": (0.05, 0.10, 0.15),
}
TIER_FRACTION = 0.25
KELLY_GRID = (0.10, 0.25, 0.50)
CALIBRATION_SEEDS = (11, 12, 13)
HOLDOUT_SEEDS = (101, 102, 103)
PATHS_PER_STRATEGY = 120
TOP_EXITS = 4
MAX_DRAWDOWN = 0.40  # survival constraints of the selection
MIN_STRESS_EQUITY = 0.50  # the worst equity in the rug-pull stress set must stay above 50 % of the initial capital
EXECUTIONS = ("causal", "immediate")  # see the module docstring; "causal" is the contract of the paper session (#91)
NO_ENTRY = 5  # reason code of a path whose entry could not be filled (no finite observation after the signal)
# Results are versioned, never rewritten: (execution contract, entry drift guard) -> version of the calibration results.
CALIBRATION_VERSIONS = {("immediate", None): "calibration-v1", ("causal", None): "calibration-v2-causal",
                        ("causal", SESSION_ENTRY_DRIFT): "calibration-v3-causal-drift"}


def calibration_version(execution: str, entry_drift: float | None) -> str:
    _check_execution(execution)
    key = (execution, None if execution == "immediate" else entry_drift)
    return CALIBRATION_VERSIONS.get(key, f"unrecorded-{execution}-drift-{entry_drift}")


# -- market paths ---------------------------------------------------------------------------------------------------
@dataclass
class Paths:
    strategy: np.ndarray  # (n,) strategy index into ms.NAMES
    regime: np.ndarray  # (n,) regime index into REGIMES
    price: np.ndarray  # (n, STEPS + 1) mid price relative to the entry mid price
    liquidity: np.ndarray  # (n, STEPS + 1) pool depth in USD
    network: np.ndarray  # (n, STEPS + 1) network fee per swap in USD
    confidence: np.ndarray  # (n,) confidence score of the entry signal


def _path(rng: np.random.Generator, regime: str, liq0: float):
    t = STEPS + 1
    r = rng.normal(0.0, 0.025, t)
    liq_mult = np.ones(t)
    if regime == "masked_rug":  # looks healthy, then drained in one step
        k = int(rng.integers(10, 180))
        r[:k] += rng.uniform(0.002, 0.006)
        r[k] = math.log(rng.uniform(0.01, 0.05))
        r[k + 1:] = rng.normal(0.0, 0.01, t - k - 1)
        liq_mult[k:] = rng.uniform(0.005, 0.03)
    elif regime == "slippage_spike":
        k, d = int(rng.integers(20, 160)), int(rng.integers(10, 40))
        r += rng.uniform(-0.0005, 0.0015)
        liq_mult[k:k + d] = rng.uniform(0.05, 0.2)
    elif regime == "false_breakout":
        k = int(rng.integers(5, 15))
        r[:k] += math.log(rng.uniform(1.3, 1.6)) / k
        r[k:] += math.log(rng.uniform(0.35, 0.75) / 1.4) / (t - k)
    elif regime == "healthy_dip":
        k = int(rng.integers(5, 25))
        r[:k] += math.log(rng.uniform(0.85, 0.95)) / k
        r[k:] += math.log(rng.uniform(1.6, 3.5)) / (t - k)
    elif regime == "smart_confluence":
        r += math.log(rng.uniform(1.1, 2.5)) / t
        if rng.random() < 0.3:  # a tracked wallet dumps on the followers right after the entry
            r[1:3] += math.log(0.75) / 2
    elif regime == "explosive":
        r = rng.normal(0.0, 0.045, t) + math.log(rng.uniform(3.0, 15.0)) / t
    elif regime == "slow_bleed":
        r += rng.uniform(-0.004, -0.001)
        liq_mult *= np.linspace(1.0, rng.uniform(0.3, 0.8), t)
    r[0] = 0.0
    price = np.exp(np.cumsum(r))
    liquidity = liq0 * rng.lognormal(0.0, 0.3) * np.sqrt(np.clip(price, 1e-6, None)) * liq_mult
    network = np.full(t, NETWORK_USD * rng.uniform(0.5, 2.0))
    if rng.random() < 0.10:  # congestion: priority fees x20 for a while
        k = int(rng.integers(0, STEPS))
        network[k:k + 30] *= CONGESTION_MULT
    return price, liquidity, network


def generate(seed: int, *, mixes: dict = MIXES, paths_per_strategy: int = PATHS_PER_STRATEGY) -> Paths:
    """Deterministic: the same seed always gives the same paths."""
    rng = np.random.default_rng(seed)
    rows = []
    for s_idx, name in enumerate(ms.NAMES):
        regimes, probs = zip(*sorted(mixes[name].items()))
        probs = np.array(probs) / sum(probs)
        for _ in range(paths_per_strategy):
            regime = regimes[int(rng.choice(len(regimes), p=probs))]
            price, liq, net = _path(rng, regime, LIQUIDITY0[name])
            conf = float(np.clip(rng.normal(0.75 if regime in ("explosive", "healthy_dip", "smart_confluence") else 0.65, 0.12), 0.3, 1.0))
            rows.append((s_idx, REGIMES.index(regime), price, liq, net, conf))
    order = rng.permutation(len(rows))  # interleave the strategies in time
    rows = [rows[i] for i in order]
    return Paths(np.array([x[0] for x in rows]), np.array([x[1] for x in rows]), np.stack([x[2] for x in rows]),
                 np.stack([x[3] for x in rows]), np.stack([x[4] for x in rows]), np.array([x[5] for x in rows]))


# -- execution ------------------------------------------------------------------------------------------------------
def entry(size, liquidity0, network0):
    """Mid value bought for ``size`` USD (mirrors memecoin_simulator.process): fee, then impact, then network fee."""
    gross = (size - network0) / (1 + DEX_FEE)
    impact = np.maximum(MIN_IMPACT, gross / (liquidity0 / 2))
    return gross / (1 + impact)


def net_ratio(mid_value, liquidity, network, size):
    """Net liquidation value / cost basis (mirrors memecoin_simulator.value)."""
    impact = np.maximum(MIN_IMPACT, mid_value / (mid_value + liquidity / 2))
    return np.maximum(0.0, mid_value * (1 - impact) * (1 - DEX_FEE) - network) / size


def _check_execution(execution):
    if execution not in EXECUTIONS:
        raise ValueError(f"contrat d'exécution inconnu : {execution}")


def ratios(paths: Paths, size=REFERENCE_SIZE, execution: str = "causal", entry_drift: float | None = SESSION_ENTRY_DRIFT) -> np.ndarray:
    """Net liquidation ratio of every path at every step (independent of the exit policy): computed once per batch.
    Causal: the entry is filled at step 1 (price, depth and network fee of step 1); the ratio is NaN before the fill and
    wherever an observation is missing. As in the paper session, an entry whose price moved by more than
    ``entry_drift`` between the signal (step 0) and the fill (step 1) is cancelled: no trade (``None`` disables the guard
    and reproduces calibration-v2-causal). Immediate: filled at step 0 (former contract), no drift by construction."""
    _check_execution(execution)
    n = len(paths.strategy)
    size = np.broadcast_to(np.asarray(size, float), (n,)).copy()
    k = 1 if execution == "causal" else 0
    with np.errstate(invalid="ignore", divide="ignore"):
        mid = entry(size, paths.liquidity[:, k], paths.network[:, k])
        out = net_ratio(mid[:, None] * paths.price / paths.price[:, k:k + 1], paths.liquidity, paths.network, size[:, None])
    out = np.where(np.isfinite(paths.price) & np.isfinite(paths.liquidity) & np.isfinite(paths.network), out, np.nan)
    out[:, :k] = np.nan
    if k:  # an entry is filled only on a finite quote of a pool with some depth: otherwise no trade at all
        out[~_fillable(paths.price[:, 1], paths.liquidity[:, 1], paths.network[:, 1], paths.price[:, 0], entry_drift)] = np.nan
    return out


def _fillable(price, liquidity, network, decided=math.nan, entry_drift=None):
    ok = np.isfinite(price) & (price > 0) & (liquidity > 0) & np.isfinite(network)
    if entry_drift is None:
        return ok
    with np.errstate(invalid="ignore", divide="ignore"):  # same rule as memecoin_simulator._execute_buy
        drifted = np.isfinite(decided) & (decided > 0) & (np.abs(price / decided - 1) > entry_drift)
    return ok & ~drifted


def _decide(ratio, high, stage, open_, policy, stop, legacy):
    """Vectorised ``exit_decision`` on the lots in ``open_``: (full exit, tier slice share, stop, trailing, floor)."""
    n = len(ratio)
    stop_hit = open_ & (ratio <= 1 - stop)
    if legacy:  # same order as memecoin_simulator.process: stop, then trailing, then take-profit
        levels = np.array([lvl for lvl, _ in ms.LEGACY["tiers"]] + [np.inf])
        trail_hit = open_ & ~stop_hit & (stage > 0) & (ratio <= high * (1 - ms.LEGACY["trailing"]))
        tier_hit = open_ & ~stop_hit & ~trail_hit & (ratio >= levels[np.minimum(stage, len(levels) - 1)])
        floor_hit = np.zeros(n, bool)
        tier_share = np.where(tier_hit, 0.5, 0.0)
    else:
        trail = np.minimum(policy.trail_max, policy.trail_base + policy.trail_slope * np.log(np.maximum(1.0, high)))
        levels = np.array([policy.cover_ratio, *policy.tiers, np.inf])
        idx = np.minimum(stage, len(levels) - 1)
        floor_hit = open_ & ~stop_hit & (stage > 0) & (ratio <= policy.floor)
        trail_hit = open_ & ~stop_hit & ~floor_hit & (stage > 0) & (ratio <= high * (1 - trail))
        tier_hit = open_ & ~stop_hit & ~floor_hit & ~trail_hit & (ratio >= levels[idx])
        tier_share = np.where(tier_hit, np.where(stage == 0, policy.cover_fraction, policy.tier_fraction), 0.0)
    return stop_hit | trail_hit | floor_hit, tier_hit, tier_share, stop_hit, trail_hit, floor_hit


def _replay_causal(R, policy, stop, legacy):
    """Decision on the ratio of step T, fill at the ratio of step T+1 (see the module docstring)."""
    n, steps = R.shape
    entered = np.isfinite(R[:, 1]) if steps > 1 else np.zeros(n, bool)
    remaining, realized, high = np.ones(n), np.zeros(n), np.ones(n)
    stage, reason = np.zeros(n, int), np.zeros(n, int)
    open_ = entered.copy()
    pending, p_full, p_tier, p_share, p_reason = (np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool), np.zeros(n),
                                                  np.zeros(n, int))
    for t in range(2, steps):
        ratio = R[:, t]
        seen = np.isfinite(ratio)
        fill = open_ & pending & seen & (ratio > 0)  # nothing realizable, or nothing observed: the order waits
        sold = np.where(fill, np.where(p_full, remaining, remaining * p_share), 0.0)
        realized += np.where(fill, sold * ratio, 0.0)
        remaining -= sold
        stage += (fill & p_tier).astype(int)
        reason = np.where(fill & p_full, p_reason, reason)
        closed = fill & (p_full | (remaining <= 1e-9))
        reason = np.where(closed & ~p_full, 4, reason)
        open_ &= ~closed
        pending &= ~fill
        judge = open_ & ~pending & seen  # a lot with a pending sale is not judged again
        high = np.where(judge, np.maximum(high, ratio), high)
        full, tier, share, stop_hit, trail_hit, floor_hit = _decide(np.where(judge, ratio, 1.0), high, stage, judge, policy, stop, legacy)
        new = full | tier
        pending |= new
        p_full = np.where(new, full, p_full)
        p_tier = np.where(new, tier & ~full, p_tier)
        p_share = np.where(new, share, p_share)
        p_reason = np.where(new, np.where(stop_hit, 1, np.where(trail_hit, 2, np.where(floor_hit, 3, 0))), p_reason)
    final = np.nan_to_num(R[:, -1], nan=0.0) if steps > 2 else np.zeros(n)
    realized += remaining * np.where(open_, np.maximum(final, 0.0), 0.0)  # sold at the last observation, 0 if missing
    out = realized - 1.0
    return np.where(entered, out, np.nan), np.where(entered, reason, NO_ENTRY)


def replay(paths: Paths, policy, size=REFERENCE_SIZE, *, legacy: bool = False, stop_override: float | None = None, matrix=None,
           execution: str = "causal", entry_drift: float | None = SESSION_ENTRY_DRIFT):
    """Vectorised replay of an exit policy on all paths. Returns per-trade return and exit reasons (codes): 0 end of
    path, 1 stop, 2 trailing, 3 break-even, 4 tiers exhausted, 5 entry never filled (return NaN, not a trade)."""
    _check_execution(execution)
    n = len(paths.strategy)
    R = ratios(paths, size, execution, entry_drift) if matrix is None else matrix
    stop = (stop_override if stop_override is not None else (ms.LEGACY["stop_loss"] if legacy else policy.stop_loss))
    if execution == "causal":
        return _replay_causal(R, policy, stop, legacy)
    remaining = np.ones(n)  # share of the position still held
    realized = np.zeros(n)  # proceeds as a multiple of the basis
    high = np.ones(n)
    stage = np.zeros(n, int)
    open_ = np.ones(n, bool)
    reason = np.zeros(n, int)  # 0 end, 1 stop, 2 trailing, 3 break-even, 4 tiers exhausted
    for t in range(1, R.shape[1]):
        ratio = R[:, t]
        high = np.where(open_, np.maximum(high, ratio), high)
        sell = np.zeros(n)
        stop_hit = open_ & (ratio <= 1 - stop)
        if legacy:  # same order as memecoin_simulator.process: stop, then trailing, then take-profit
            levels = np.array([lvl for lvl, _ in ms.LEGACY["tiers"]] + [np.inf])
            trail_hit = open_ & ~stop_hit & (stage > 0) & (ratio <= high * (1 - ms.LEGACY["trailing"]))
            tier_hit = open_ & ~stop_hit & ~trail_hit & (ratio >= levels[np.minimum(stage, len(levels) - 1)])
            floor_hit = np.zeros(n, bool)
            tier_share = np.where(tier_hit, 0.5, 0.0)
        else:
            trail = np.minimum(policy.trail_max, policy.trail_base + policy.trail_slope * np.log(np.maximum(1.0, high)))
            levels = np.array([policy.cover_ratio, *policy.tiers, np.inf])
            idx = np.minimum(stage, len(levels) - 1)
            floor_hit = open_ & ~stop_hit & (stage > 0) & (ratio <= policy.floor)
            trail_hit = open_ & ~stop_hit & ~floor_hit & (stage > 0) & (ratio <= high * (1 - trail))
            tier_hit = open_ & ~stop_hit & ~floor_hit & ~trail_hit & (ratio >= levels[idx])
            tier_share = np.where(tier_hit, np.where(stage == 0, policy.cover_fraction, policy.tier_fraction), 0.0)
        full = stop_hit | trail_hit | floor_hit
        sell = np.where(full, remaining, remaining * tier_share)
        realized += sell * ratio
        remaining -= sell
        stage += tier_hit.astype(int)
        reason = np.where(stop_hit, 1, np.where(trail_hit, 2, np.where(floor_hit, 3, reason)))
        closed = full | (remaining <= 1e-9)
        reason = np.where(closed & ~full & open_, 4, reason)
        open_ &= ~closed
        if not open_.any():
            break
    realized += remaining * R[:, -1]  # positions still open at the end are valued at their net liquidation
    return realized - 1.0, reason


def _trade_return_causal(price, liquidity, network, size, policy, entry_drift):
    price, liquidity, network = (np.asarray(x, float) for x in (price, liquidity, network))

    def ratio_at(t):
        if not (np.isfinite(price[t]) and np.isfinite(liquidity[t]) and np.isfinite(network[t])):
            return math.nan
        return float(net_ratio(np.array([mid1 * price[t] / price[1]]), np.array([liquidity[t]]), np.array([network[t]]),
                               np.array([size]))[0])

    if len(price) < 2 or not _fillable(price[1], liquidity[1], network[1], price[0], entry_drift):
        return math.nan, []  # the entry was never filled: no trade
    mid1 = float(entry(np.array([size]), np.array([liquidity[1]]), np.array([network[1]]))[0])
    pos, remaining, realized, events, pending = {"high": 1.0, "stage": 0}, 1.0, 0.0, [], None
    for t in range(2, len(price)):
        r = ratio_at(t)
        if pending is not None and math.isfinite(r) and r > 0:  # the decision of an earlier step is filled here
            why, share, decided_t, decided_r = pending
            sold = remaining * share
            realized += sold * r
            remaining -= sold
            events.append((t, why, round(share, 4), round(r, 4), decided_t, round(decided_r, 4)))
            pending = None
            if why == "take_profit":
                pos["stage"] += 1
            if share >= 1.0 or remaining <= 1e-9:
                remaining = 0.0
                break
        if pending is None and math.isfinite(r):
            d = ms.exit_decision(pos, r, policy)
            if d is not None:
                pending = (d[0], d[1], t, r)
    final = ratio_at(len(price) - 1) if len(price) > 2 else math.nan
    if remaining > 0 and math.isfinite(final):  # sold at the last observation of the horizon, 0 when it is missing
        realized += remaining * max(final, 0.0)
    return realized - 1.0, events


def trade_return(price, liquidity, network, size, policy=ms.CALIBRATED, execution: str = "causal",
                 entry_drift: float | None = SESSION_ENTRY_DRIFT) -> tuple[float, list]:
    """Scalar replay of one trade with ``memecoin_strategies.exit_decision`` (reference for the vectorised replay).
    Causal events are ``(fill step, reason, share, fill ratio, decision step, decision ratio)``; immediate events are
    ``(step, reason, share, ratio)``. A causal entry that could not be filled returns ``(nan, [])``."""
    _check_execution(execution)
    if execution == "causal":
        return _trade_return_causal(price, liquidity, network, size, policy, entry_drift)
    mid0 = float(entry(np.array([size]), np.array([liquidity[0]]), np.array([network[0]]))[0])
    pos, remaining, realized, events = {"high": 1.0, "stage": 0}, 1.0, 0.0, []
    for t in range(1, len(price)):
        ratio = float(net_ratio(np.array([mid0 * price[t]]), np.array([liquidity[t]]), np.array([network[t]]), np.array([size]))[0])
        d = ms.exit_decision(pos, ratio, policy)
        if d is None:
            continue
        why, share = d
        sold = remaining * share
        realized += sold * ratio
        remaining -= sold
        events.append((t, why, round(share, 4), round(ratio, 4)))
        if why == "take_profit":
            pos["stage"] += 1
        if share >= 1.0 or remaining <= 1e-9:
            remaining = 0.0
            break
    if remaining > 0:
        realized += remaining * float(net_ratio(np.array([mid0 * price[-1]]), np.array([liquidity[-1]]), np.array([network[-1]]),
                                                np.array([size]))[0])
    return realized - 1.0, events


def policies(grid: dict = GRID):
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        p = dict(zip(keys, values))
        if p["tiers"][0] <= p["cover_ratio"]:
            continue
        yield ms.ExitPolicy(tier_fraction=TIER_FRACTION, **p)


def log_growth(returns, fraction=REFERENCE_SIZE / INITIAL) -> float:
    returns = np.asarray(returns, float)
    returns = returns[np.isfinite(returns)]  # a causal entry that was never filled is not a trade
    return float(np.mean(np.log(np.maximum(1e-9, 1 + fraction * returns)))) if len(returns) else -math.inf


# -- portfolio ------------------------------------------------------------------------------------------------------
def legacy_fractions(completed: list) -> tuple[dict, dict]:
    """The paper session's learner BEFORE calibration (verbatim logic), for the before/after comparison."""
    scores, fractions = {}, {}
    for name in ms.NAMES:
        returns = [x["return"] for x in completed if x["strategy"] == name][-30:]
        wins = [r for r in returns if r > 0]
        losses = [-r for r in returns if r < 0]
        growth = sum(math.log(max(0.001, 1 + r)) for r in returns) / (4 + len(returns))
        scores[name] = math.exp(max(-3, min(3, 4 * growth)))
        p = (len(wins) + 1) / (len(returns) + 2)
        odds = ((sum(wins) + 0.2) / (len(wins) + 1)) / ((sum(losses) + 0.2) / (len(losses) + 1))
        fractions[name] = min(0.10, max(0.0, 0.25 * (p - (1 - p) / odds))) if returns else 0.04
    return fractions, {name: score / sum(scores.values()) for name, score in scores.items()}


def portfolio(paths: Paths, returns: np.ndarray, *, multiplier: float, legacy: bool = False, dynamic: bool = True) -> dict:
    """Sequential compounding portfolio of 30 USD: one trade per path, sized with the learner of memecoin_strategies.

    ``returns`` are the per-trade net returns of the exit policy (``replay`` at the reference size: for these order sizes
    the 1 % minimum impact dominates, so the return barely depends on the size). ``legacy`` sizes like the paper session
    (Kelly fraction x weight); otherwise ``learner.size`` adds confidence, liquidity and the impact cap."""
    equity, peak, worst, max_dd = INITIAL, INITIAL, INITIAL, 0.0
    completed, pnl, curve = [], [], [INITIAL]
    for i in range(len(paths.strategy)):
        r = float(returns[i])
        if not math.isfinite(r):  # the entry was never filled: no trade, nothing spent
            continue
        name = ms.NAMES[int(paths.strategy[i])]
        if legacy:
            fractions, weights = legacy_fractions(completed if dynamic else [])
            size = equity * fractions[name] * weights[name]
        else:
            learner = ms.MemecoinIsolatedLearner(completed if dynamic else [], multiplier=multiplier)
            size = learner.size(name, equity, confidence_score=float(paths.confidence[i]), liquidity_usd=float(paths.liquidity[i, 0]))
        size = min(size, 0.10 * equity, equity - 0.25 * INITIAL if equity > 0.25 * INITIAL else 0.0)
        if size < 0.05 or size <= 2 * paths.network[i, 0]:
            continue
        equity += size * r
        pnl.append(size * r)
        completed.append({"strategy": name, "return": r, "asset_domain": ASSET_DOMAIN})
        peak = max(peak, equity)
        worst = min(worst, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        curve.append(equity)
    pnl = np.array(pnl)
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    by = {}
    for name in ms.NAMES:
        rows = [c["return"] for c in completed if c["strategy"] == name]
        by[name] = {"trades": len(rows), "mean_return": float(np.mean(rows)) if rows else None,
                    "win_rate": float(np.mean([x > 0 for x in rows])) if rows else None}
    return {"compound_return": equity / INITIAL - 1, "final_equity": equity, "profit_factor": float(wins / losses) if losses > 0 else None,
            "win_rate": float(np.mean(pnl > 0)) if len(pnl) else None, "max_drawdown": max_dd, "worst_equity_ratio": worst / INITIAL,
            "trades": len(pnl), "by_strategy": by}


def _summary(results: list[dict]) -> dict:
    keys = ("compound_return", "profit_factor", "win_rate", "max_drawdown", "worst_equity_ratio")
    out = {}
    for k in keys:
        vals = [r[k] for r in results if r[k] is not None]
        out[k] = {"median": float(np.median(vals)) if vals else None, "min": float(np.min(vals)) if vals else None,
                  "max": float(np.max(vals)) if vals else None}
    out["trades"] = int(sum(r["trades"] for r in results))
    return out


# -- calibration ----------------------------------------------------------------------------------------------------
@dataclass
class MemecoinCalibrator:
    grid: dict = None
    kelly_grid: tuple = KELLY_GRID
    calibration_seeds: tuple = CALIBRATION_SEEDS
    holdout_seeds: tuple = HOLDOUT_SEEDS
    paths_per_strategy: int = PATHS_PER_STRATEGY
    top_exits: int = TOP_EXITS
    asset_domain: str = ASSET_DOMAIN
    execution: str = "causal"  # "immediate" reproduces calibration-v1 (see the module docstring)
    entry_drift: float | None = SESSION_ENTRY_DRIFT  # None reproduces calibration-v2-causal

    def __post_init__(self):
        self.grid = self.grid or GRID
        _check_execution(self.execution)

    def _paths(self, seeds, mixes=MIXES):
        return [generate(s, mixes=mixes, paths_per_strategy=self.paths_per_strategy) for s in seeds]

    def calibrate(self) -> dict:
        cal = self._paths(self.calibration_seeds)
        stress = self._paths(self.calibration_seeds, RUG_STRESS)
        everything = Paths(*(np.concatenate([getattr(p, f) for p in cal]) for f in ("strategy", "regime", "price", "liquidity",
                                                                                     "network", "confidence")))
        ranked = []
        matrix = ratios(everything, execution=self.execution, entry_drift=self.entry_drift)
        for pol in policies(self.grid):
            r, _ = replay(everything, pol, matrix=matrix, execution=self.execution, entry_drift=self.entry_drift)
            ranked.append((log_growth(r), asdict(pol), pol))
        ranked.sort(key=lambda x: (-x[0], str(x[1])))
        candidates = []
        for g, _, pol in ranked[: self.top_exits]:
            cal_r = [replay(p, pol, execution=self.execution, entry_drift=self.entry_drift)[0] for p in cal]
            stress_r = [replay(p, pol, execution=self.execution, entry_drift=self.entry_drift)[0] for p in stress]
            for m in self.kelly_grid:
                runs = [portfolio(p, r, multiplier=m) for p, r in zip(cal, cal_r)]
                worst = [portfolio(p, r, multiplier=m) for p, r in zip(stress, stress_r)]
                s, st = _summary(runs), _summary(worst)
                ok = s["max_drawdown"]["max"] <= MAX_DRAWDOWN and st["worst_equity_ratio"]["min"] >= MIN_STRESS_EQUITY
                candidates.append({"policy": _policy_dict(pol), "kelly_multiplier": m, "exit_log_growth": g, "calibration": s,
                                   "rug_stress": st, "survives": ok, "_policy": pol})
        viable = [c for c in candidates if c["survives"]] or candidates
        best = max(viable, key=lambda c: (c["calibration"]["compound_return"]["median"], -c["calibration"]["max_drawdown"]["max"],
                                          str(c["policy"]), -c["kelly_multiplier"]))
        return {"asset_domain": ASSET_DOMAIN, "execution": self.execution, "entry_drift": self.entry_drift,
                "version": calibration_version(self.execution, self.entry_drift),
                "selected": {k: v for k, v in best.items() if k != "_policy"},
                "candidates": [{k: v for k, v in c.items() if k != "_policy"} for c in candidates],
                "grid_size": len(ranked), "_policy": best["_policy"]}

    def compare(self, policy, multiplier: float) -> dict:
        """Legacy session (thresholds AND learner) vs the given setting, on HELD-OUT seeds: normal mix, intense rug-pull
        stress and a mix without any explosive run. Also splits the gain between exits and sizing."""
        out = {}
        for label, mixes in (("holdout", MIXES), ("rug_stress", RUG_STRESS), ("no_explosive", NO_EXPLOSIVE)):
            sets = self._paths(self.holdout_seeds, mixes)
            old_r = [replay(p, None, legacy=True, execution=self.execution, entry_drift=self.entry_drift)[0] for p in sets]
            new_r = [replay(p, policy, execution=self.execution, entry_drift=self.entry_drift)[0] for p in sets]
            runs = {
                "before": [portfolio(p, r, multiplier=0.25, legacy=True) for p, r in zip(sets, old_r)],
                "exits_only": [portfolio(p, r, multiplier=0.25, legacy=True) for p, r in zip(sets, new_r)],
                "sizing_only": [portfolio(p, r, multiplier=multiplier) for p, r in zip(sets, old_r)],
                "after": [portfolio(p, r, multiplier=multiplier) for p, r in zip(sets, new_r)],
            }
            out[label] = {k: _summary(v) for k, v in runs.items()}
            out[label]["by_strategy_before"] = _by_strategy(runs["before"])
            out[label]["by_strategy_after"] = _by_strategy(runs["after"])
            out[label]["trade_returns"] = {"before_mean": float(np.nanmean(np.concatenate(old_r))),
                                           "after_mean": float(np.nanmean(np.concatenate(new_r))),
                                           "before_median": float(np.nanmedian(np.concatenate(old_r))),
                                           "after_median": float(np.nanmedian(np.concatenate(new_r)))}
        return out


def _by_strategy(results):
    out = {}
    for name in ms.NAMES:
        means = [r["by_strategy"][name]["mean_return"] for r in results if r["by_strategy"][name]["mean_return"] is not None]
        wins = [r["by_strategy"][name]["win_rate"] for r in results if r["by_strategy"][name]["win_rate"] is not None]
        out[name] = {"trades": int(sum(r["by_strategy"][name]["trades"] for r in results)),
                     "mean_return": float(np.mean(means)) if means else None, "win_rate": float(np.mean(wins)) if wins else None}
    return out


def _policy_dict(pol) -> dict:
    d = asdict(pol)
    d["tiers"] = list(d["tiers"])
    return d


# -- recorded selections: versioned results, never rewritten; none is promoted automatically -------------------------
# Metrics on the development seeds (CALIBRATION_SEEDS) only. calibration-v1 is the policy of the paper session
# (ms.CALIBRATED, ms.KELLY_MULTIPLIER). calibration-v2-causal is recorded, not used: the session keeps calibration-v1
# until a human validates a promotion, and its gap with v1 over three development seeds is small. calibration-v3-causal-drift
# replays the session's own contract (T+1 fills and the 10 % entry drift guard of #97): it selects the v1 policy again.
RECORDED_SELECTIONS = {
    "calibration-v1": {
        "execution": "immediate", "in_session": True, "kelly_multiplier": 0.5,
        "policy": _policy_dict(ms.ExitPolicy(0.20, 1.25, 0.20, (3.0, 6.0, 10.0), 0.25, 0.25, 0.15)),
        "development": {"compound_return_median": 38.4134, "max_drawdown_max": 0.3187, "stress_worst_equity_min": 0.6579},
    },
    "calibration-v2-causal": {
        "execution": "causal", "entry_drift": None, "in_session": False, "kelly_multiplier": 0.5,
        "policy": _policy_dict(ms.ExitPolicy(0.20, 1.25, 0.20, (3.0, 6.0, 10.0), 0.25, 0.35, 0.15)),
        "development": {"compound_return_median": 90.5451, "max_drawdown_max": 0.3333, "stress_worst_equity_min": 0.6290},
    },
    "calibration-v3-causal-drift": {
        "execution": "causal", "entry_drift": SESSION_ENTRY_DRIFT, "in_session": False, "same_policy_as": "calibration-v1",
        "kelly_multiplier": 0.5, "policy": _policy_dict(ms.ExitPolicy(0.20, 1.25, 0.20, (3.0, 6.0, 10.0), 0.25, 0.25, 0.15)),
        "development": {"compound_return_median": 91.9133, "max_drawdown_max": 0.3321, "stress_worst_equity_min": 0.6373},
    },
}
