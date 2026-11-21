"""Local paper-only rules. Returns are learned exclusively from completed memecoin positions.

Asymmetric management (calibrated offline by ``memecoin_calibrator`` on deterministic synthetic Solana regimes):
- cut losses fast: the stop is a trigger at -15 % to -20 % of the net liquidation value (a rug can still gap through it);
- take profit in tiers: a first slice as soon as fees and slippage are covered, then slices at x2, x3, x5...;
- after the first slice, an adaptive trailing stop that widens with the multiple reached, so x3-x10+ runs can continue,
  and a floor at break-even so a covered winner never closes as a loss;
- size by capped fractional Kelly, scaled by a confidence score and the pool liquidity, and reallocate the budget
  toward the strategies that performed best on the recent window.

Signals on established pools (M58): ``sniper`` fires on a sudden, verifiable acceleration between two successive
observations of an admissible pool (``sniper_check``); the anti-rug veto is evaluated first and suppresses every signal.
A candidate that did not come through M58 (the fresh-pool live radar) keeps the former age rule for display only: the
paper simulator drops ``sniper`` for such a candidate (``memecoin_simulator.tradable_signals``).
The confidence of an M58 entry is continuous and explainable (``confidence_breakdown``, 0.45 to 0.95).

Nothing here places an order: the paper simulator alone consumes these rules, with fictitious funds.
"""

import math
from dataclasses import dataclass, field

from . import established_pools
from .tokenrisk import assess_candidate
from .tokens import TokenId

ASSET_DOMAIN = "memecoin"
NAMES = ("sniper", "momentum", "dip_hunter", "whale_tail")

# -- sniper on established pools (M58) ------------------------------------------------------------------------------
# A sudden, verifiable acceleration between two successive observations of an ADMISSIBLE established pool. The pool
# rules of M58 are re-checked here (defence in depth): the signal never widens the universe the scanner admitted.
SNIPER_MIN_AGE_MINUTES = established_pools.MIN_AGE_MINUTES  # 60 min: M58 never admits a younger pool
M58_MIN_LIQUIDITY_USD = established_pools.MIN_LIQUIDITY_USD  # 20,000 USD
SNIPER_MIN_LIQUIDITY_USD = M58_MIN_LIQUIDITY_USD
SNIPER_MIN_GAP_S = 1  # the previous observation must be strictly older (same snapshot: no signal)
SNIPER_MAX_GAP_S = 600  # ...and at most 10 minutes old: an older baseline says nothing about a "sudden" move
SNIPER_VOLUME_JUMP = 3.0  # 5-min volume pace at least 3x the previous one (momentum asks 1.5x: a burst, not a trend)
SNIPER_HOURLY_PACE = 2.0  # and at least twice the average pace of the last hour (acceleration against the hour)
SNIPER_MIN_PACE_USD_MIN = 100.0  # at least 100 USD/min (500 USD in 5 min): a jump on dust volume is noise
SNIPER_BUY_SELL = 2.0  # at least two buys per sell over 5 minutes, and more than at the previous observation
SNIPER_PRICE_MOVE = (0.02, 0.25)  # price up 2 % to 25 %: a real move, not already a parabolic candle
SNIPER_LIQUIDITY_KEPT = 0.95  # liquidity kept within 5 %: no withdrawal during the burst
LEGACY_SNIPER_MAX_AGE_MINUTES = 3  # former fresh-pool rule, only for candidates that did not come through M58 (display)
# Momentum compares the 5-minute windows (volume, buy/sell counts) of two observations of the same pool. Fixed before any
# result (SPEC.md, campaign parameters): beyond 600 s the two 5-minute windows are not adjacent, so a "jump" between them
# says nothing about an acceleration; the same bound as the sniper's baseline. The previous observation must also be
# strictly older (a replayed snapshot is no evidence).
MOMENTUM_MIN_GAP_S = 1
MOMENTUM_MAX_GAP_S = 600
_SNIPER_FIELDS = ("price", "liquidity_usd", "volume_usd_per_min_m5", "buy_sell_count_ratio_m5", "ts")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sniper_check(asset, point):
    """``{"signal": bool, "reason": str, ...}`` for a sudden acceleration on an admissible established pool.

    Deterministic and stateless: it compares the current observation with the previous one kept in ``asset["last"]``
    and refuses on missing, inconsistent or stale data, or on an unchanged observation (no repeated trigger)."""
    previous = asset.get("last")
    if not isinstance(previous, dict):
        return {"signal": False, "reason": "aucune observation précédente"}
    for name, obs in (("courante", point), ("précédente", previous)):
        if not all(_number(obs.get(k)) for k in _SNIPER_FIELDS):
            return {"signal": False, "reason": f"observation {name} incomplète"}
        if obs["price"] <= 0 or obs["liquidity_usd"] <= 0 or obs["volume_usd_per_min_m5"] < 0 or obs["buy_sell_count_ratio_m5"] < 0:
            return {"signal": False, "reason": f"observation {name} incohérente"}
    age, hour = point.get("pool_age_minutes"), point.get("volume_h1_usd")
    if not _number(age) or age < SNIPER_MIN_AGE_MINUTES:
        return {"signal": False, "reason": "pool hors M58 : âge inconnu ou inférieur à 60 min"}
    if point["liquidity_usd"] < SNIPER_MIN_LIQUIDITY_USD:
        return {"signal": False, "reason": "pool hors M58 : liquidité sous 20 000 $"}
    if not _number(hour) or hour <= 0:
        return {"signal": False, "reason": "volume sur 1 h inconnu"}
    gap = point["ts"] - previous["ts"]
    if gap < SNIPER_MIN_GAP_S:
        return {"signal": False, "reason": "même observation ou observation antérieure"}
    if gap > SNIPER_MAX_GAP_S:
        return {"signal": False, "reason": f"observation précédente périmée ({gap:.0f} s > {SNIPER_MAX_GAP_S} s)"}
    if all(point[k] == previous[k] for k in _SNIPER_FIELDS[:4]):
        return {"signal": False, "reason": "observation inchangée"}
    pace, before = point["volume_usd_per_min_m5"], previous["volume_usd_per_min_m5"]
    move = point["price"] / previous["price"] - 1
    metrics = {"volume_jump": pace / before if before > 0 else None, "hourly_pace": pace / (hour / 60),
               "buy_sell": point["buy_sell_count_ratio_m5"], "price_move": move,
               "liquidity_kept": point["liquidity_usd"] / previous["liquidity_usd"], "gap_s": gap}
    checks = (
        (pace >= SNIPER_MIN_PACE_USD_MIN, "volume sur 5 min trop faible"),
        (before > 0 and pace >= SNIPER_VOLUME_JUMP * before, "pas de saut de volume (x3) depuis l'observation précédente"),
        (metrics["hourly_pace"] >= SNIPER_HOURLY_PACE, "rythme sur 5 min inférieur au double de la moyenne horaire"),
        (point["buy_sell_count_ratio_m5"] >= SNIPER_BUY_SELL
         and point["buy_sell_count_ratio_m5"] > previous["buy_sell_count_ratio_m5"], "pression acheteuse insuffisante"),
        (SNIPER_PRICE_MOVE[0] <= move <= SNIPER_PRICE_MOVE[1], "variation de prix hors de +2 % à +25 %"),
        (metrics["liquidity_kept"] >= SNIPER_LIQUIDITY_KEPT, "liquidité en retrait"),
    )
    for ok, reason in checks:
        if not ok:
            return {"signal": False, "reason": reason, **metrics}
    return {"signal": True, "reason": "accélération vérifiée", **metrics}


def evaluate(client, asset, point, whale):
    # A pool admitted by M58 is re-checked with the M58 depth rule (20,000 USD), not the guardian's global 25,000 USD
    # default kept for every other caller; every other veto is unchanged.
    depth = {"min_liquidity_usd": M58_MIN_LIQUIDITY_USD} if asset.get("dex_id") else {}
    guard = assess_candidate(client, TokenId("solana", asset["symbol"]), point["liquidity_usd"], **depth)
    if not guard["allowed"]:
        return [], guard  # the anti-rug veto comes first: no signal at all
    previous = asset.get("last")
    signals = []
    if asset.get("dex_id"):  # an established pool admitted by M58: a verified acceleration between two observations
        if sniper_check(asset, point)["signal"]:
            signals.append("sniper")
    elif _number(point.get("pool_age_minutes")) and 0 <= point["pool_age_minutes"] <= LEGACY_SNIPER_MAX_AGE_MINUTES:
        signals.append("sniper")  # former fresh-pool rule (live radar display); the paper simulator never enters on it
    if previous:
        gap = point["ts"] - previous["ts"] if _number(point.get("ts")) and _number(previous.get("ts")) else None
        if (
            gap is not None
            and MOMENTUM_MIN_GAP_S <= gap <= MOMENTUM_MAX_GAP_S
            and previous["volume_usd_per_min_m5"]
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


# -- experimental risk profiles (SPEC.md, campaign guarantee 6) -------------------------------------------------------
@dataclass(frozen=True)
class RiskProfile:
    """Limits of one experimental variant of the paper session. A profile only bounds exposure and stakes: it never lifts
    the Guardian, the fleet's limits, the M58 pool filters, the ban on leverage and short selling, or the causal
    execution of #91, and no return target is attached to it. Each profile runs in its own session."""

    id: str
    label: str
    max_exposure: float  # value of the open lots at most this fraction of the sizing equity
    token_cap: float  # cost basis per token at most this fraction of the sizing equity
    cash_reserve: float  # cash kept: at least this fraction of the equity
    max_open: int  # open lots at most
    session_loss_stop: float  # no new entry once the sizing equity is at or below (1 - this) x the initial capital
    min_liquidity_usd: float  # pool depth at least
    max_round_trip_loss: float  # measured Jupiter round trip at most
    stake_scale: float  # multiplies the Kelly target only; the exploration floor and every cap still apply

    def __post_init__(self):
        if not (0 < self.token_cap <= self.max_exposure and 0 < self.cash_reserve < 1
                and self.max_exposure + self.cash_reserve <= 1 + 1e-12):
            raise ValueError("profil invalide : exposition, plafond par token ou réserve incohérents (aucun levier)")
        if not (1 <= self.max_open and 0 < self.session_loss_stop < 1 and self.min_liquidity_usd > 0
                and 0 < self.max_round_trip_loss < 1 and 0 < self.stake_scale):
            raise ValueError("profil invalide")


# Proposed values, versioned before any result: they are pre-registered jointly with the evaluation before a trial
# (SPEC.md) and never tuned on the final evaluation block. MODERATE reproduces the session limits in force before the
# profiles (token cap 10 %, reserve 25 %, the fleet's 12 lots and 50 % session loss, the M58 20,000 USD and 10 % round
# trip), so a session without a profile is unchanged. The other profiles move monotonically from SAFE to MAX and stay
# within the hard caps checked by memecoin_simulator.profile_for (fleet, M58, no leverage).
PROFILES_VERSION = "profiles-v1-proposed"
PROFILES = {p.id: p for p in (
    RiskProfile("SAFE", "SAFE", 0.30, 0.05, 0.50, 4, 0.10, 50_000.0, 0.05, 0.5),
    RiskProfile("MODERATE", "MODERATE", 0.75, 0.10, 0.25, 12, 0.50, 20_000.0, 0.10, 1.0),
    RiskProfile("RISK", "RISK", 0.80, 0.15, 0.20, 12, 0.50, 20_000.0, 0.10, 1.25),
    RiskProfile("ULTRA_RISK", "ULTRA RISK", 0.85, 0.20, 0.15, 12, 0.50, 20_000.0, 0.10, 1.5),
    RiskProfile("MAX", "MAX", 0.90, 0.25, 0.10, 12, 0.50, 20_000.0, 0.10, 2.0),
)}
DEFAULT_PROFILE = "MODERATE"


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
    """Legacy agreement score (0..1), kept verbatim for existing callers and for candidates that did not come
    through M58; a guardian refusal means no confidence at all. M58 entries use ``entry_confidence``."""
    if guard is not None and not guard.get("allowed", False):
        return 0.0
    n = len(set(signals or ()))
    return 0.0 if n == 0 else min(1.0, 0.55 + 0.15 * (n - 1) + (0.15 if "whale_tail" in signals else 0.0))


# -- continuous confidence of an admissible M58 candidate ---------------------------------------------------------
CONFIDENCE_MODEL = "m58-continuous-v1"
CONFIDENCE_FLOOR, CONFIDENCE_CEILING = 0.45, 0.95
CONFIDENCE_WEIGHTS = {"liquidity": 0.30, "turnover": 0.20, "round_trip": 0.30, "agreement": 0.20}
LIQUIDITY_FULL_USD = 1_000_000.0  # log scale from the M58 minimum (20,000 USD -> 0) to 1,000,000 USD (-> 1)
TURNOVER_FULL = 1.0  # 1 h volume equal to the pool depth (one full turnover per hour) -> 1
ROUND_TRIP_LIMIT = established_pools.MAX_ROUND_TRIP_LOSS  # measured Jupiter loss: 0 % -> 1, the M58 limit (10 %) -> 0


def _unit(value):
    return max(0.0, min(1.0, value))


def confidence_breakdown(signals, *, liquidity_usd, volume_h1_usd, round_trip_loss, guard=None) -> dict:
    """Continuous, bounded and explainable confidence of an admissible candidate, in [0.45, 0.95].

    Four components in [0, 1], combined with fixed weights: pool depth (log scale), 1 h turnover (volume / depth),
    measured Jupiter round-trip loss (lower is better) and the number of independent agreeing signals. A missing or
    invalid input scores 0 on its component: it never raises the confidence. A guardian veto returns 0, whatever the
    inputs, and no signal means no candidate (0)."""
    agreeing = sorted(set(signals or ()) & set(NAMES))
    if guard is not None and not guard.get("allowed", False):
        return {"model": CONFIDENCE_MODEL, "score": 0.0, "vetoed": True, "components": None, "missing": [], "signals": agreeing}
    if not agreeing:
        return {"model": CONFIDENCE_MODEL, "score": 0.0, "vetoed": False, "components": None, "missing": [], "signals": []}
    missing = []
    liq = liquidity_usd if _number(liquidity_usd) and liquidity_usd > 0 else None
    if liq is None:
        missing.append("liquidity_usd")
    vol = volume_h1_usd if _number(volume_h1_usd) and volume_h1_usd >= 0 else None
    if vol is None:
        missing.append("volume_h1_usd")
    loss = round_trip_loss if _number(round_trip_loss) and round_trip_loss >= 0 else None
    if loss is None:
        missing.append("round_trip_loss")
    low = math.log(M58_MIN_LIQUIDITY_USD)
    components = {
        "liquidity": 0.0 if liq is None else _unit((math.log(liq) - low) / (math.log(LIQUIDITY_FULL_USD) - low)),
        "turnover": 0.0 if liq is None or vol is None else _unit(vol / liq / TURNOVER_FULL),
        "round_trip": 0.0 if loss is None else _unit(1 - loss / ROUND_TRIP_LIMIT),
        "agreement": (min(len(agreeing), 3) - 1) / 2,
    }
    blend = sum(CONFIDENCE_WEIGHTS[k] * v for k, v in components.items())
    score = CONFIDENCE_FLOOR + (CONFIDENCE_CEILING - CONFIDENCE_FLOOR) * blend
    return {"model": CONFIDENCE_MODEL, "score": score, "vetoed": False, "components": components, "missing": missing,
            "signals": agreeing, "inputs": {"liquidity_usd": liq, "volume_h1_usd": vol, "round_trip_loss": loss}}


def entry_confidence(signals, point, asset, guard=None) -> dict:
    """Confidence recorded with a new fictitious entry. The model depends on the candidate's provenance, never on its
    missing data: a pool admitted by M58 (``dex_id`` set by ``established_pools``) gets the continuous score; any
    other candidate (legacy fresh-pool scanner, raw state) keeps the legacy agreement score."""
    if not asset.get("dex_id"):
        return {"model": "legacy-agreement", "score": confidence(signals, guard)}
    return confidence_breakdown(signals, liquidity_usd=point.get("liquidity_usd"), volume_h1_usd=point.get("volume_h1_usd"),
                                round_trip_loss=asset.get("round_trip_loss"), guard=guard)


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
