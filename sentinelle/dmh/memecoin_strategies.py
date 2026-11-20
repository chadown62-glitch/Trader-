"""Local paper-only rules. Returns are learned exclusively from completed memecoin positions.

Asymmetric management (calibrated offline by ``memecoin_calibrator`` on deterministic synthetic Solana regimes):
- cut losses fast: the stop is a trigger at -15 % to -20 % of the net liquidation value (a rug can still gap through it);
- take profit in tiers: a first slice as soon as fees and slippage are covered, then slices at x2, x3, x5...;
- after the first slice, an adaptive trailing stop that widens with the multiple reached, so x3-x10+ runs can continue,
  and a floor at break-even so a covered winner never closes as a loss;
- size by capped fractional Kelly, scaled by a confidence score and the pool liquidity, and reallocate the budget
  toward the strategies that performed best on the recent window.

Nothing here places an order: the paper simulator alone consumes these rules, with fictitious funds.
"""

import math
from dataclasses import dataclass, field

from .tokenrisk import assess_candidate
from .tokens import TokenId

ASSET_DOMAIN = "memecoin"
NAMES = ("sniper", "momentum", "dip_hunter", "whale_tail")


def evaluate(client, asset, point, whale):
    guard = assess_candidate(client, TokenId("solana", asset["symbol"]), point["liquidity_usd"])
    if not guard["allowed"]:
        return [], guard
    previous = asset.get("last")
    signals = []
    if 0 <= point["pool_age_minutes"] <= 3:
        signals.append("sniper")
    if previous:
        if (
            previous["volume_usd_per_min_m5"]
            and previous["buy_sell_count_ratio_m5"]
            and (point["volume_usd_per_min_m5"] or 0) > 1.5 * previous["volume_usd_per_min_m5"]
            and (point["buy_sell_count_ratio_m5"] or 0) > max(1, previous["buy_sell_count_ratio_m5"])
        ):
            signals.append("momentum")
        if (
            0.65 <= point["price"] / asset["peak"] <= 0.80
            and point["liquidity_usd"] >= 0.95 * asset["peak_liquidity"]
            and (point["volume_usd_per_min_m5"] or 0) > 0
        ):
            signals.append("dip_hunter")
    if whale:
        signals.append("whale_tail")
    return signals, guard


# -- asymmetric exits -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ExitPolicy:
    """Exit thresholds on the NET liquidation ratio (net sale value / cost basis of the open lot)."""

    stop_loss: float  # sell everything at ratio <= 1 - stop_loss (bounded to 15-20 % by design)
    cover_ratio: float  # first slice once the net ratio covers fees, slippage and this margin
    cover_fraction: float  # share of the position sold at cover_ratio
    tiers: tuple  # later multiples at which tier_fraction of the remaining position is sold
    tier_fraction: float
    trail_base: float  # trailing drawdown from the high, active after the first slice
    trail_slope: float  # widening per unit of ln(high): lets explosive runs breathe
    trail_max: float = 0.45
    floor: float = 1.0  # after the first slice, never close the remainder below this ratio
    asset_domain: str = ASSET_DOMAIN

    def trail(self, high: float) -> float:
        return min(self.trail_max, self.trail_base + self.trail_slope * math.log(max(1.0, high)))

    def __post_init__(self):
        if not 0.15 <= self.stop_loss <= 0.20:
            raise ValueError("stop-loss must stay between 15 % and 20 %")
        if not (1.0 < self.cover_ratio and 0 < self.cover_fraction < 1 and 0 < self.tier_fraction <= 1):
            raise ValueError("invalid take-profit tiers")
        if list(self.tiers) != sorted(self.tiers) or (self.tiers and self.tiers[0] <= self.cover_ratio):
            raise ValueError("tiers must increase above the cover ratio")


# Legacy thresholds of the paper session before calibration (memecoin_simulator.process), kept for comparison.
LEGACY = {"stop_loss": 0.17, "tiers": ((1.5, 0.5), (2.0, 0.5)), "trailing": 0.20}
# Selected by memecoin_calibrator.calibrate() with its default grid and seeds (see tests/test_memecoin_calibrator.py).
CALIBRATED = ExitPolicy(stop_loss=0.20, cover_ratio=1.25, cover_fraction=0.20, tiers=(3.0, 6.0, 10.0), tier_fraction=0.25,
                        trail_base=0.25, trail_slope=0.15)
KELLY_MULTIPLIER = 0.5  # fractional (half) Kelly selected by the calibrator, still capped at MAX_FRACTION
MAX_FRACTION = 0.10  # never more than 10 % of equity on one position
LIQUIDITY_REFERENCE_USD = 50_000.0  # below this pool depth, the Kelly fraction shrinks linearly
MAX_IMPACT = 0.03  # entry size such that the price impact stays under 3 %
RECENT_WINDOW = 20  # completed trades per strategy used for the reallocation
RECENT_DECAY = 0.9  # weight of a trade decays by 10 % for each newer trade of the same strategy
MIN_WEIGHT = 0.05  # every strategy keeps a small exploration budget
PROBE_FRACTION = 0.01  # while a strategy has fewer than RECENT_WINDOW trades, a zero Kelly still allows a 1 % probe


def exit_decision(pos: dict, ratio: float, policy: ExitPolicy = CALIBRATED):
    """Decide on an open lot. ``pos`` carries ``high`` (best ratio seen) and ``stage`` (slices already sold).

    Returns ``(reason, fraction_of_remaining_to_sell)`` or ``None`` to hold. Updates ``pos["high"]``."""
    pos["high"] = max(pos.get("high", 1.0), ratio)
    stage = pos.get("stage", 0)
    if ratio <= 1 - policy.stop_loss:
        return "stop_loss", 1.0
    if stage == 0:
        return ("take_profit", policy.cover_fraction) if ratio >= policy.cover_ratio else None
    if ratio <= policy.floor:
        return "break_even_stop", 1.0
    if ratio <= pos["high"] * (1 - policy.trail(pos["high"])):
        return "trailing_stop", 1.0
    k = stage - 1
    if k < len(policy.tiers) and ratio >= policy.tiers[k]:
        return "take_profit", policy.tier_fraction
    return None


def kelly(p: float, odds: float, multiplier: float = KELLY_MULTIPLIER, cap: float = MAX_FRACTION) -> float:
    """Fractional Kelly ``multiplier * (p - (1 - p) / odds)``, floored at 0 and capped."""
    if odds <= 0:
        return 0.0
    return min(cap, max(0.0, multiplier * (p - (1 - p) / odds)))


def liquidity_factor(liquidity_usd) -> float:
    if not liquidity_usd or liquidity_usd <= 0:
        return 0.0
    return min(1.0, liquidity_usd / LIQUIDITY_REFERENCE_USD)


def impact_cap_usd(liquidity_usd) -> float:
    """Largest order whose constant-product impact (q / (q + L/2)) stays under ``MAX_IMPACT``."""
    if not liquidity_usd or liquidity_usd <= 0:
        return 0.0
    return MAX_IMPACT * (liquidity_usd / 2) / (1 - MAX_IMPACT)


def confidence(signals, guard=None) -> float:
    """0..1 from the agreement of independent signals; a guardian refusal means no confidence at all."""
    if guard is not None and not guard.get("allowed", False):
        return 0.0
    n = len(set(signals or ()))
    return 0.0 if n == 0 else min(1.0, 0.55 + 0.15 * (n - 1) + (0.15 if "whale_tail" in signals else 0.0))


@dataclass
class MemecoinIsolatedLearner:
    """Per-strategy sizing and budget weights learned only from completed memecoin positions.

    ``fractions`` (capped fractional Kelly with shrinkage) and ``weights`` (recency-weighted growth) keep the interface
    used by the paper simulator; ``size`` adds the confidence and liquidity scaling and the impact cap."""

    completed: list
    multiplier: float = KELLY_MULTIPLIER
    window: int = RECENT_WINDOW
    decay: float = RECENT_DECAY
    asset_domain: str = field(default=ASSET_DOMAIN, init=False)

    def __post_init__(self):
        scores, self.fractions, self.stats = {}, {}, {}
        for name in NAMES:
            rows = [x for x in self.completed if x.get("strategy") == name and x.get("asset_domain", ASSET_DOMAIN) == ASSET_DOMAIN]
            returns = [x["return"] for x in rows][-self.window:]
            w = [self.decay ** (len(returns) - 1 - i) for i in range(len(returns))]
            wins = [(r, k) for r, k in zip(returns, w) if r > 0]
            losses = [(-r, k) for r, k in zip(returns, w) if r < 0]
            total = sum(w)
            growth = sum(k * math.log(max(0.001, 1 + r)) for r, k in zip(returns, w)) / (4 + total)
            scores[name] = math.exp(max(-3, min(3, 4 * growth)))
            # Shrink small samples toward one win and one loss of equal size (weighted counts).
            p = (sum(k for _, k in wins) + 1) / (total + 2)
            avg_win = (sum(r * k for r, k in wins) + 0.2) / (sum(k for _, k in wins) + 1)
            avg_loss = (sum(r * k for r, k in losses) + 0.2) / (sum(k for _, k in losses) + 1)
            odds = avg_win / avg_loss
            f = kelly(p, odds, self.multiplier)
            if 0 < len(returns) < self.window and f == 0.0:
                f = PROBE_FRACTION  # keep learning on a small sample instead of freezing a strategy forever
            self.fractions[name] = f if returns else 0.04
            self.stats[name] = {"trades": len(returns), "p": p, "odds": odds, "growth": growth}
        raw = {name: s / sum(scores.values()) for name, s in scores.items()}
        floored = {name: max(MIN_WEIGHT, v) for name, v in raw.items()}
        self.weights = {name: v / sum(floored.values()) for name, v in floored.items()}

    def size(self, strategy: str, equity: float, *, confidence_score: float = 1.0, liquidity_usd=None) -> float:
        """USD to commit: equity x Kelly fraction x budget weight x confidence x liquidity, within the impact cap."""
        f = self.fractions[strategy] * self.weights[strategy] * len(NAMES) * max(0.0, min(1.0, confidence_score))
        if liquidity_usd is not None:
            f *= liquidity_factor(liquidity_usd)
        usd = equity * min(MAX_FRACTION, f)
        return min(usd, impact_cap_usd(liquidity_usd)) if liquidity_usd is not None else usd
