"""On-demand simulation in USD of a separate fictitious 30 USD endowment.

Exits are versioned per position. Every paper session driven by ``tick`` (worker, cloud runner, cockpit route) adopts
the calibrated asymmetric policy of ``memecoin_strategies`` (selected by ``memecoin_calibrator``): stop at -20 % of the
net liquidation value, 20 % sold once the net ratio reaches 1.25 (fees and slippage covered), then a break-even floor
on the remaining balance, take-profit slices at x3, x6 and x10 and an adaptive trailing stop. A position keeps the
policy it was opened under (a lot never changes rules mid-flight); positions opened before this version, or by a raw
state that never went through ``tick``, keep the legacy ladder (-17 %, 50 % at 1.5x and 2x, trailing 20 %).
Candidates (M58): established pools (``established_pools``: liquidity >= 20,000 USD, at least one hour old, an active
last hour, a Jupiter round trip quoted without a wallet, then the anti-rug veto) instead of pools born minutes ago.
The four strategies run on these pools: ``sniper`` on a verified acceleration between two observations, ``whale_tail``
only when the pool's creator and deployer can be attributed from public data (otherwise it abstains, which is not a
source failure); each M58 entry records a continuous confidence (0.45-0.95) with its components.
Before any fictitious entry the agent fleet (``sentinelle.fleet``) is consulted: a veto, a risk refusal, an alert or an
objection (impact, cost, unconfirmed resale) means no entry; exits are never blocked. In the session, an entry decided on
one observation is filled on a later one, after the Guardian and the fleet's risk limits are judged again on it.
A new stake is the Kelly target scaled by the bounded multiplier of ``memecoin_learner`` (0.5x-1.5x, learned from the
closed lots and the mature or partially sold open lots), never under its exploration floor, within the unchanged hard
limits; each position records its opening time, its entry conditions and why it was so sized. A lot that has neither
hit its stop nor sold a slice after ``MAX_HOLD_S`` (24 h by default, ``state["max_hold_s"]`` per session) is closed by a
time exit, which frees its budget.
Paper only: no order, no key; every fill is tagged ``asset_domain="memecoin"`` and ``simulated``."""

import hashlib
import hmac
import json
import math
import secrets

from ..ai.features_memecoin import extract_features
from ..config import paper_capital
from ..fleet import agents as fleet_agents, coordinator as fleet_coordinator
from . import established_pools, memecoin_learner, smart_wallets
from .dex import _rpc
from .memecoin_strategies import (CALIBRATED, DEFAULT_PROFILE, DIP_PEAK_MAX_AGE_S, PROFILES, PROFILES_VERSION,
                                  MemecoinIsolatedLearner, NAMES, entry_confidence, entry_signals, evaluate,
                                  exit_decision, route)
from .net import SourceUnavailable

KEY = "memecoin:paper-session:v1"
DOMAIN = "memecoin"
INITIAL = 30.0
DEX_FEE = 0.003  # scenario assumption, not a measured pool fee
LEGACY_EXITS, CALIBRATED_EXITS = "legacy", "calibrated-v1"
EXIT_POLICIES = {CALIBRATED_EXITS: CALIBRATED}  # stop -20 %, 20 % at 1.25x, break-even floor, x3/x6/x10, adaptive trailing
# The cockpit labels four reasons; the break-even floor is a protective trailing exit, its exact cause stays in ``detail``.
DISPLAY_REASON = {"break_even_stop": "trailing_stop"}
MAX_HOLD_S = 24 * 3600  # time exit of a stagnant lot (no stop, no slice sold); per session: state["max_hold_s"]
ORDER_TTL_S = 600  # a fictitious entry decided at T is cancelled without a valid later quote within 10 minutes
# An entry whose price moved by more than 10 % (either way) between the decision and the fill is cancelled: the decision's
# snapshot no longer describes the market the order would enter. Fixed before any result (SPEC.md, campaign parameters):
# half the calibrated stop distance (20 %), so that a fill never starts already half-way to its stop or chasing a move
# the signal did not see. Exits are never blocked by it.
MAX_ENTRY_DRIFT = 0.10
MAX_QUOTE_AGE_S = 120  # an execution quote received more than 2 minutes before the tick is too old
ORDER_LOG = 200  # resolved orders (executed, cancelled, expired) kept for audit
# A held lot without a valid quote of its exact pool blocks new entries for as long as an entry order may wait for its
# own quote (ORDER_TTL_S): a shorter outage is treated as transient. Beyond it the lot is durably unavailable: it stays
# open and re-quoted every cycle, its value is unknown, and new entries resume, sized as if it were worth nothing.
UNAVAILABLE_GRACE_S = 600
# Assets watched besides the held lots and the assets carrying a pending order: the most recently admitted pools only.
# Four of them are quoted per cycle in rotation; with a measured cycle of 35 to 75 s, 24 assets are each revisited every
# six cycles (210 to 450 s), within the 600 s the sniper and momentum signals accept between two observations.
WATCH_LIMIT = 24
ROTATION = 4


def opened_at(state, key, pos):
    """Opening time of an open lot; lots opened before it was recorded fall back to their latest buy fill."""
    if pos.get("opened_at") is not None:
        return pos["opened_at"]
    return next((f.get("ts") for f in reversed(state["fills"]) if f["side"] == "buy" and f["strategy"] + "/" + f["mint"] == key), None)


def time_exit(state, key, pos, now):
    """A lot that has neither hit its stop nor sold any take-profit slice is closed once held ``max_hold_s``."""
    limit = state.get("max_hold_s", MAX_HOLD_S)
    start = opened_at(state, key, pos)
    if pos.get("stage", 0) or start is None or not isinstance(limit, (int, float)) or limit <= 0:
        return None
    return ("time_exit", 1.0) if now - start >= limit else None


def adopt_calibrated_exits(state):
    """New positions of a session driven by ``tick`` use the calibrated exits; open lots keep their own policy."""
    state["exit_policy"] = CALIBRATED_EXITS
    return state


def legacy_exit(pos, ratio):
    """The ladder used before calibration, kept verbatim for positions opened under it."""
    pos["high"] = max(pos["high"], ratio)
    if ratio <= 0.83:
        return "stop_loss", 1
    if pos["stage"] and ratio <= pos["high"] * 0.8:
        return "trailing_stop", 1
    if pos["stage"] < 2 and ratio >= (1.5, 2.0)[pos["stage"]]:
        return "take_profit", 0.5
    return None


def exit_for(pos, ratio):
    policy = pos.get("exit_policy", LEGACY_EXITS)
    if policy == LEGACY_EXITS:
        return legacy_exit(pos, ratio)
    return exit_decision(pos, ratio, EXIT_POLICIES[policy])  # updates pos["high"]
SOURCE_ERRORS = (SourceUnavailable, KeyError, TypeError, ValueError, IndexError, OverflowError)


JOURNAL_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS memecoin_journal(id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL UNIQUE, "
    "ts INTEGER NOT NULL, asset_domain TEXT NOT NULL CHECK(asset_domain='memecoin'), data TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS memecoin_journal_no_update BEFORE UPDATE ON memecoin_journal "
    "BEGIN SELECT RAISE(ABORT,'journal memecoin en ajout seul'); END",
    "CREATE TRIGGER IF NOT EXISTS memecoin_journal_no_delete BEFORE DELETE ON memecoin_journal "
    "BEGIN SELECT RAISE(ABORT,'journal memecoin en ajout seul'); END",
)


def load(db):
    with db.transaction():
        db.execute(
            "CREATE TABLE IF NOT EXISTS smart_wallets(address TEXT PRIMARY KEY, asset_domain TEXT NOT NULL "
            "CHECK(asset_domain='memecoin'), data TEXT NOT NULL)"
        )
        for statement in JOURNAL_SCHEMA:
            db.execute(statement)
        wallets = {r["address"]: json.loads(r["data"]) for r in db.query("SELECT address,data FROM smart_wallets")}
        raw = db.get_meta(KEY)
    state = (
        json.loads(raw)
        if raw
        else {"revision": 0, "cash": INITIAL, "assets": {}, "positions": {}, "fills": [], "completed": [], "curve": [], "errors": []}
    )
    return state, wallets


def configure(db, client, amount, currency):
    """Convert the initial fictitious deposit once; all trading and P&L use USD."""
    if amount is None and currency is None:
        return
    if type(amount) not in (int, float) or currency not in ("EUR", "USD"):
        raise ValueError("Montant numérique et devise USD ou EUR requis")
    amount = paper_capital(amount)

    def matches(state):
        deposit = state.get("deposit", {"amount": INITIAL, "currency": "USD"})
        return deposit["currency"] == currency and deposit["amount"] == amount

    state, _ = load(db)
    if state["revision"]:
        if not matches(state):
            raise ValueError("Le capital de cette session existe déjà")
        return
    fx = {"rate": 1.0, "source": "USD"}
    if currency == "EUR":
        response = client.get("api.kraken.com", "/0/public/Ticker", {"pair": "EURUSD"})
        try:
            ticker = response.json()
            if ticker["error"]:
                raise ValueError
            rate = float(ticker["result"]["ZEURZUSD"]["b"][0])
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("Cours acheteur EUR/USD indisponible") from exc
        fx = {"rate": rate, "source": "Kraken EUR/USD bid", "observed_ms": response.received_ms, "sha256": response.sha256}
    with db.transaction():
        state, _ = load(db)
        if state["revision"]:
            if not matches(state):
                raise ValueError("Le capital de cette session existe déjà")
            return
        state.update(
            cash=amount * fx["rate"], initial=amount * fx["rate"], revision=1, deposit={"amount": amount, "currency": currency, "fx": fx}
        )
        db.set_meta(KEY, json.dumps(state, allow_nan=False))


def scan_once(client, db, bus, *, registry=None):
    """Session candidates (M58): established pools rather than micro-pools created a few minutes ago."""
    return established_pools.scan(client, db, bus, registry=registry)


POOL_MARKET_FIELDS = ("pairAddress", "priceUsd", "priceNative", "liquidity", "volume", "txns", "priceChange", "fdv", "marketCap")


def pool_fingerprint(pair):
    """SHA-256 of the market state of the exact pool (price, depth, volumes, trade counts, caps). The other pairs of the
    same DexScreener response and the pair's descriptive metadata (images, links, boosts) never change it."""
    market = {k: pair.get(k) for k in POOL_MARKET_FIELDS}
    return hashlib.sha256(json.dumps(market, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def quote(client, asset, now):
    mint = asset["symbol"]
    fetched = client.get("api.dexscreener.com", f"/token-pairs/v1/solana/{mint}")
    pairs = fetched.json()
    # Established pools live on several DEXs (PumpSwap, Raydium, Meteora, Orca): the exact pair address identifies them.
    venue = None if asset.get("dex_id") else "raydium"
    pair = next(
        (
            p
            for p in pairs
            if p["chainId"] == "solana"
            and (venue is None or p["dexId"] == venue)
            and p["pairAddress"] == asset["pool"]
            and p["baseToken"]["address"] == mint
            and p["quoteToken"]["address"] == asset["quote_mint"]
        ),
        None,
    )
    if not pair:
        raise ValueError("paire exacte indisponible")
    point = extract_features(pair, observed_ms=now * 1000, created_ms=asset["creation"]["block_time"] * 1000)
    price = float(pair["priceUsd"])
    if not math.isfinite(price) or price <= 0 or point["liquidity_usd"] is None:
        raise ValueError("prix ou profondeur indisponible")  # a pool reported with zero depth is still an observation
    h1 = (pair.get("volume") or {}).get("h1")
    finite = isinstance(h1, (int, float)) and not isinstance(h1, bool) and math.isfinite(h1) and h1 >= 0
    # Reception time of the response and fingerprint of the exact pool. DexScreener exposes no publication time for the
    # price: none is invented. The response fingerprint is kept for provenance only: it also covers the token's other
    # pairs, so a change elsewhere in the response would make an unchanged quote of this pool look new.
    received, fingerprint = getattr(fetched, "received_ms", None), getattr(fetched, "sha256", None)
    point.update(price=price, ts=now, volume_h1_usd=float(h1) if finite else None,
                 received_ms=received if isinstance(received, int) else None,  # missing: never fills an order
                 source_sha256=fingerprint if isinstance(fingerprint, str) else None, pool_sha256=pool_fingerprint(pair))
    return point


# Market regime: memecoins move with SOL. When SOL loses 5 % or more over the last hour (a rare, market-wide shock for an
# asset whose hourly moves are usually around 1 %), no new entry is decided or filled; exits are never blocked. Fixed
# before any result (SPEC.md, campaign parameters). An unknown regime (missing data) blocks nothing and is recorded.
REGIME_DROP_H1 = 0.05


def market_regime(client):
    """SOL regime from the deepest public WSOL/USDC pool: ``normal``, ``risk_off`` (1 h change at or below -5 %) or
    ``unknown`` with the reason; the 1 h change is DexScreener's own, recorded as such."""
    pairs = client.get("api.dexscreener.com", f"/token-pairs/v1/solana/{smart_wallets.WSOL}").json()
    pools = [p for p in pairs if p["chainId"] == "solana" and p["baseToken"]["address"] == smart_wallets.WSOL
             and p["quoteToken"]["address"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]
    if not pools:
        return {"status": "unknown", "reason": "cours SOL/USD indisponible"}
    change = (max(pools, key=lambda p: float(p["liquidity"]["usd"])).get("priceChange") or {}).get("h1")
    if not _finite(change):
        return {"status": "unknown", "reason": "variation SOL sur 1 h non publiée"}
    return {"status": "risk_off" if change <= -100 * REGIME_DROP_H1 else "normal", "sol_change_h1_pct": float(change)}


def network_fee(client):
    pairs = client.get("api.dexscreener.com", f"/token-pairs/v1/solana/{smart_wallets.WSOL}").json()
    pools = [
        p
        for p in pairs
        if p["chainId"] == "solana"
        and p["baseToken"]["address"] == smart_wallets.WSOL
        and p["quoteToken"]["address"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    ]
    if not pools:
        raise ValueError("cours SOL/USD indisponible")
    sol = float(max(pools, key=lambda p: float(p["liquidity"]["usd"]))["priceUsd"])
    samples, _ = _rpc(client, "solana", "getRecentPrioritizationFees", [])
    fees = sorted(x["prioritizationFee"] for x in samples if type(x["prioritizationFee"]) is int and x["prioritizationFee"] >= 0)
    if not fees or not math.isfinite(sol) or sol <= 0:
        raise ValueError("frais réseau indisponibles")
    # One signature, 200k compute units, observed 75th percentile micro-lamports/CU.
    return sol * (5000 + math.ceil(200000 * fees[3 * len(fees) // 4] / 1e6)) / 1e9


def value(qty, point, network):
    mid = qty * point["price"]
    depth = mid + point["liquidity_usd"] / 2
    impact = max(0.01, mid / depth) if depth > 0 else 1.0  # no depth at all: nothing is realizable
    gross = mid * (1 - impact)
    return max(0.0, gross * (1 - DEX_FEE) - network), mid * impact, gross * DEX_FEE


def tradable_signals(asset, signals):
    """Signals the paper session may act on: ``sniper`` only on a pool admitted by M58 (``dex_id``), never on the former
    fresh-pool rule, so no entry ever bypasses the established-pool filter."""
    return [s for s in signals if s != "sniper" or asset.get("dex_id")]


def _entry(point, asset, signals):
    """Entry conditions of a new lot. An M58 pool records its continuous confidence and why (components, missing
    inputs, measured round trip); any other candidate keeps the legacy agreement score, unchanged."""
    judged = entry_confidence(signals, point, asset)
    entry = {"liquidity_usd": point["liquidity_usd"], "volume_h1_usd": point.get("volume_h1_usd"),
             "confidence": judged["score"], "signals": sorted(signals)}
    if judged["model"] != "legacy-agreement":
        entry.update(confidence_model=judged["model"], confidence_components=judged["components"],
                     confidence_missing=judged["missing"], round_trip_loss=asset.get("round_trip_loss"))
    return entry


def equity(state):
    return state["cash"] + sum(pos["mark"] for pos in state["positions"].values())


def sizing_equity(state):
    """Equity for sizing and risk limits: cash plus the lots valued on a valid quote of this cycle. A lot without one
    (``stale_marks``) counts for nothing here, so an unknown value never enlarges a new stake."""
    stale = set(state.get("stale_marks", []))
    return state["cash"] + sum(p["mark"] for p in state["positions"].values() if p["mint"] not in stale and _finite(p.get("mark")))


def known_equity(state):
    """Cash plus the last confirmed liquidation value of every lot, or ``None`` when a lot has no usable mark."""
    marks = [p.get("mark") for p in state["positions"].values()]
    return state["cash"] + sum(marks) if all(_finite(m) for m in marks) else None


def _mark(pos, point, network):
    """Liquidation value of an open lot on this observation (exit impact, DEX fee and network fee included), confirmed
    at the observation time ``marked_at``."""
    pos["mark"] = value(pos["qty"], point, network)[0]
    pos["marked_at"] = point.get("ts")
    return pos["mark"]


def _exit(state, key, pos, point):
    """Exit decision for an open lot already marked on this observation: ``(detail, fraction)`` or ``None``."""
    return exit_for(pos, pos["mark"] / pos["basis"]) or time_exit(state, key, pos, point["ts"])


def _fill(state, point, strategy, mint, side, reason, detail, exit_policy, pnl, quantity, slip, fee, network, extra=None):
    fill = {"asset_domain": DOMAIN, "simulated": True, "ts": point["ts"], "strategy": strategy, "mint": mint, "side": side,
            "reason": reason, "detail": detail, "exit_policy": exit_policy, "pnl": pnl, "quantity": quantity,
            "slippage_usd": slip, "dex_fee_usd": fee, "network_fee_usd": network, **(extra or {})}
    state["fills"].append(fill)
    return fill


def _sell(state, mint, strategy, key, pos, point, network, detail, fraction, extra=None):
    """Fictitious sale of ``fraction`` of the lot at this observation's liquidation value; ``None`` when nothing is
    realizable (no fictitious cash is ever credited for an unsellable lot)."""
    reason = DISPLAY_REASON.get(detail, detail)
    sold = pos["qty"] * fraction
    net, slip, fee = value(sold, point, network)
    if net <= 0:
        return None  # the remaining dust has no realizable value
    basis = pos["basis"] * fraction
    pnl = net - basis
    state["cash"] += net
    pos["pnl"] += pnl
    pos["qty"] -= sold
    pos["basis"] -= basis
    pos["stage"] += 1
    pos["mark"] = value(pos["qty"], point, network)[0]
    pos["marked_at"] = point.get("ts")
    if fraction >= 1:
        state["completed"].append({"strategy": strategy, "mint": mint, "return": pos["pnl"] / pos["initial"],
                                   **{k: pos[k] for k in ("entry", "sizing", "opened_at") if k in pos},
                                   "closed_at": point["ts"], "exit": detail})
        del state["positions"][key]
    return _fill(state, point, strategy, mint, "sell", reason, detail, pos.get("exit_policy", LEGACY_EXITS), pnl, sold, slip,
                 fee, network, extra)


def profile_for(state):
    """The session's experimental risk profile (MODERATE when none was chosen), checked against the hard caps it can
    never lift: the fleet's open-lot and session-loss limits, the M58 pool depth and round trip, no leverage."""
    profile = PROFILES.get(state.get("profile", DEFAULT_PROFILE))
    if profile is None:
        raise ValueError(f"profil inconnu : {state.get('profile')}")
    rc = fleet_agents.RiskCommander
    if (profile.max_open > rc.MEMECOIN_MAX_OPEN or profile.session_loss_stop > rc.MEMECOIN_MAX_SESSION_LOSS
            or profile.min_liquidity_usd < established_pools.MIN_LIQUIDITY_USD
            or profile.max_round_trip_loss > established_pools.MAX_ROUND_TRIP_LOSS):
        raise ValueError(f"le profil {profile.id} dépasse une limite de la flotte ou du filtre M58")
    return profile


def profile_refusal(state, asset, point):
    """``None`` when the session's profile admits a new entry on this observation; otherwise why not. Only the limits
    stricter than the hard caps are checked here: at the hard cap itself the fleet and the Guardian keep deciding, with
    their own reasons (a MODERATE session is therefore unchanged)."""
    profile = profile_for(state)
    rc = fleet_agents.RiskCommander
    if profile.max_open < rc.MEMECOIN_MAX_OPEN and len(state["positions"]) >= profile.max_open:
        return f"profil {profile.id} : {profile.max_open} lots ouverts au plus"
    if (profile.session_loss_stop < rc.MEMECOIN_MAX_SESSION_LOSS
            and sizing_equity(state) <= (1 - profile.session_loss_stop) * state.get("initial", INITIAL)):
        return f"profil {profile.id} : perte de session au-delà de {100 * profile.session_loss_stop:.0f} %"
    if profile.min_liquidity_usd > established_pools.MIN_LIQUIDITY_USD and not (
            _finite(point.get("liquidity_usd")) and point["liquidity_usd"] >= profile.min_liquidity_usd):
        return f"profil {profile.id} : liquidité sous {profile.min_liquidity_usd:,.0f} $".replace(",", " ")
    loss = asset.get("round_trip_loss")
    if profile.max_round_trip_loss < established_pools.MAX_ROUND_TRIP_LOSS and not (_finite(loss) and loss <= profile.max_round_trip_loss):
        return f"profil {profile.id} : aller-retour Jupiter non mesuré ou au-delà de {100 * profile.max_round_trip_loss:.0f} %"
    return None


def select_profile(db, profile_id):
    """Choose the experimental profile of a session that has not traded yet (each profile runs in its own session)."""
    if profile_id not in PROFILES:
        raise ValueError(f"profil inconnu : {profile_id}")
    with db.transaction():
        raw = db.get_meta(KEY)
        state = json.loads(raw) if raw else load(db)[0]
        if state.get("fills") or state.get("positions") or state.get("orders"):
            if state.get("profile", DEFAULT_PROFILE) != profile_id:
                raise ValueError("Le profil d'une session ayant déjà négocié ne change pas")
            return
        state.update(profile=profile_id, profiles_version=PROFILES_VERSION)
        db.set_meta(KEY, json.dumps(state, allow_nan=False))


def _buy(state, mint, strategy, key, point, network, signals, learner, adaptive, asset, entry=None, extra=None):
    """Fictitious entry at this observation: Kelly target x bounded multiplier x the profile's stake scale, never under
    the exploration floor, within the profile's token cap, the strategy budget and the profile's cash reserve (and its
    exposure cap when the reserve does not already imply it); fees and slippage are paid from the stake. Targets and
    caps use ``sizing_equity`` (an unquoted lot counts for nothing); the reserve uses the larger equity (stricter)."""
    profile = profile_for(state)
    eq = sizing_equity(state)
    if not point.get("liquidity_usd") or point["liquidity_usd"] <= 0:
        return None  # no depth: no entry
    used = sum(p["basis"] for p in state["positions"].values() if p["mint"] == mint)
    strategy_used = sum(p["basis"] for p in state["positions"].values() if p["strategy"] == strategy)
    target = eq * learner.fractions[strategy] * learner.weights[strategy]
    learned = adaptive[strategy]
    # The learned multiplier scales the Kelly target only: the hard limits below never move. A strategy whose
    # Kelly fraction fell to zero still explores with a small stake instead of freezing.
    desired = target * learned["multiplier"] * profile.stake_scale
    floor = memecoin_learner.exploration_floor(eq, learned["multiplier"])
    limits = {
        "exploration_floor" if floor > desired else "kelly_target": max(desired, floor),
        "token_cap": profile.token_cap * eq - used,
        "strategy_budget": eq * learner.weights[strategy] - strategy_used,
        "cash_reserve": state["cash"] - profile.cash_reserve * max(eq, known_equity(state) or eq),
    }
    if profile.max_exposure + profile.cash_reserve < 1 - 1e-9:  # otherwise the reserve already bounds the exposure
        stale = set(state.get("stale_marks", []))
        exposed = sum(p["mark"] for p in state["positions"].values() if p["mint"] not in stale and _finite(p.get("mark")))
        limits["exposure_cap"] = profile.max_exposure * eq - exposed
    size = min(limits.values())
    if size <= network * 2 or size < 0.05:
        return None
    gross = (size - network) / (1 + DEX_FEE)
    impact = max(0.01, gross / (point["liquidity_usd"] / 2))
    qty = gross / (point["price"] * (1 + impact))
    slip, fee = gross - qty * point["price"], gross * DEX_FEE
    state["cash"] -= size
    state["positions"][key] = {
        "mint": mint,
        "strategy": strategy,
        "qty": qty,
        "basis": size,
        "initial": size,
        "mark": value(qty, point, network)[0],
        "marked_at": point.get("ts"),
        "high": 1.0,
        "stage": 0,
        "pnl": 0.0,
        "exit_policy": state.get("exit_policy", LEGACY_EXITS),
        "asset_domain": DOMAIN,
        # Entry conditions, compared later between winning and losing lots (threshold recommendations).
        "entry": entry if entry is not None else _entry(point, asset, signals),
        "opened_at": point["ts"],
        "sizing": {"learner": memecoin_learner.VERSION, "scoring": memecoin_learner.SCORING,
                   "multiplier": learned["multiplier"], "score": learned["score"],
                   "closed_trades": learned["closed_trades"], "open_lots": learned["open_lots"],
                   "kelly_target_usd": target, "exploration_floor_usd": floor, "size_usd": size,
                   "binding": min(limits, key=limits.get), "reason": learned["reason"]},
        "profile": profile.id,
    }
    return _fill(state, point, strategy, mint, "buy", "signal", "signal", state["positions"][key]["exit_policy"], 0.0, qty, slip,
                 fee, network, extra)


def _observe(asset, point):
    """Keep this observation as the asset's latest, and its high of the last ``DIP_PEAK_MAX_AGE_S`` (with its time): a
    higher price, a first observation or a high older than that window restarts the high from this observation."""
    asset["last"] = point
    ts = point.get("ts")
    stale = _finite(ts) and _finite(asset.get("peak_ts")) and ts - asset["peak_ts"] > DIP_PEAK_MAX_AGE_S
    if stale or "peak" not in asset or point["price"] >= asset["peak"]:
        asset["peak"], asset["peak_ts"] = point["price"], ts
        asset["peak_liquidity"] = point["liquidity_usd"] if stale else max(asset.get("peak_liquidity", 0), point["liquidity_usd"])
    else:
        asset["peak_liquidity"] = max(asset.get("peak_liquidity", 0), point["liquidity_usd"])
        asset.setdefault("peak_ts", ts)  # a high recorded before its time was kept: its window starts now


def process(state, mint, point, signals, network):
    """Immediate decision AND fill on one observation, for direct callers (offline replays, tests). The paper session
    (``tick``) never uses it: there, a decision taken on an observation is filled on a later one."""
    asset = state["assets"][mint]
    learner = MemecoinIsolatedLearner(state["completed"])
    # Bounded 0.5x-1.5x, from the closed lots and the mature or partially sold open lots (no survivorship bias).
    adaptive = memecoin_learner.multipliers_for_state(state)
    for strategy in NAMES:
        key = strategy + "/" + mint
        pos = state["positions"].get(key)
        if pos:
            _mark(pos, point, network)
            decision = _exit(state, key, pos, point)
            if decision is not None:
                _sell(state, mint, strategy, key, pos, point, network, *decision)
        elif strategy in signals:
            _buy(state, mint, strategy, key, point, network, signals, learner, adaptive, asset)
    _observe(asset, point)


# -- causal execution of the paper session -----------------------------------------------------------------------------
def observation(point):
    """Provenance of an observation: tick time, reception time, fingerprints of the exact pool and of the response, price."""
    return {"ts": point["ts"], "received_ms": point.get("received_ms"), "pool_sha256": point.get("pool_sha256"),
            "response_sha256": point.get("source_sha256"), "price": point["price"]}


def execution_check(order, point, asset, now):
    """``None`` when ``point`` may fill ``order``; otherwise why not. The fill needs a quote received strictly after the
    decision, at a later tick, in which the exact pool differs from the decision snapshot and the previous snapshot, and
    fresh. Only the pool's own market state counts: a change in another pair of the response proves nothing."""
    received, fingerprint = point.get("received_ms"), point.get("pool_sha256")
    decided = order["decision"]
    if not isinstance(received, int) or not isinstance(fingerprint, str):
        return "cotation sans heure de réception ni empreinte du pool"
    if not isinstance(decided.get("received_ms"), int) or received <= decided["received_ms"] or point["ts"] <= order["decided_at"]:
        return "cotation non postérieure à la décision (chronologie ambiguë)"
    if fingerprint == decided.get("pool_sha256"):
        return "cotation du pool identique au snapshot de la décision"
    if fingerprint == (asset.get("last") or {}).get("pool_sha256"):
        return "cotation du pool identique au snapshot précédent"
    if now * 1000 - received > MAX_QUOTE_AGE_S * 1000:
        return "cotation trop ancienne"
    return None


def _causal(oid, order, point):
    decided = order["decision"]
    return {"order_id": oid, "decided_at": order["decided_at"], "decision_received_ms": decided["received_ms"],
            "decision_pool_sha256": decided.get("pool_sha256"), "decision_response_sha256": decided.get("response_sha256"),
            "execution_received_ms": point["received_ms"], "execution_pool_sha256": point["pool_sha256"],
            "execution_response_sha256": point.get("source_sha256")}


def _resolve(state, oid, status, reason, now):
    order = state["orders"].pop(oid)
    entry = {"id": oid, "kind": order["kind"], "key": order["key"], "status": status, "reason": reason,
             "decided_at": order["decided_at"], "resolved_at": now}
    state.setdefault("order_log", []).append(entry)
    del state["order_log"][:-ORDER_LOG]
    if "_cycle" in state:
        state["_cycle"]["resolved"].append(entry)  # the journal keeps every resolution, whatever the in-session cap


def _decide(state, kind, key, mint, strategy, point, now, **details):
    oid = f"{state['revision'] + 1}:{kind}:{key}"  # one decision per kind and lot per tick
    state["orders"][oid] = {"kind": kind, "key": key, "mint": mint, "strategy": strategy, "decided_at": now,
                            "decision": observation(point), "asset_domain": DOMAIN, **details}
    return oid


def _execute_sell(state, oid, order, point, network, now, executed, asset):
    if oid in executed:
        return _resolve(state, oid, "duplicate", "déjà exécuté avant la reprise", now)
    pos = state["positions"].get(order["key"])
    if pos is None:
        return _resolve(state, oid, "cancelled", "lot déjà clôturé", now)
    problem = execution_check(order, point, asset, now)
    if problem:
        order["last_refusal"] = problem
        return None  # a protective exit stays pending until a later valid quote
    fill = _sell(state, order["mint"], order["strategy"], order["key"], pos, point, network, order["detail"], order["fraction"],
                 extra=_causal(oid, order, point))
    if fill is None:
        order["last_refusal"] = "vente impossible : valeur de liquidation nulle, aucun cash fictif crédité"
        order["unsellable"] = order.get("unsellable", 0) + 1
        return None
    return _resolve(state, oid, "executed", None, now)


def _execute_buy(state, oid, order, point, network, now, executed, asset, guard, reliable, fleet):
    if oid in executed:
        return _resolve(state, oid, "duplicate", "déjà exécuté avant la reprise", now)
    if now - order["decided_at"] > ORDER_TTL_S:
        return _resolve(state, oid, "expired", "aucune cotation postérieure valide dans les 10 minutes", now)
    if order["key"] in state["positions"]:
        return _resolve(state, oid, "cancelled", "lot déjà ouvert", now)
    if not guard.get("allowed"):
        return _resolve(state, oid, "cancelled", "veto du Guardian à l'exécution", now)
    if not reliable:
        order["last_refusal"] = "valorisation non fiable : entrée suspendue"
        return None
    problem = execution_check(order, point, asset, now)
    if problem:
        order["last_refusal"] = problem
        return None
    decided_price = order["decision"].get("price")
    if _finite(decided_price) and decided_price > 0 and abs(point["price"] / decided_price - 1) > MAX_ENTRY_DRIFT:
        drift = point["price"] / decided_price - 1
        return _resolve(state, oid, "cancelled",
                        f"dérive de prix depuis la décision : {100 * drift:+.1f} % (limite ±{100 * MAX_ENTRY_DRIFT:.0f} %)", now)
    refusal = profile_refusal(state, asset, point)
    if refusal:
        return _resolve(state, oid, "cancelled", refusal, now)
    # The fleet's risk limits again, on the execution observation (depth, round trip, open lots, session loss).
    judged = recheck_fleet(fleet, state, order, asset, point, guard, network, now)
    if judged["decision"] != fleet_coordinator.PROPOSAL:
        return _resolve(state, oid, "cancelled", f"refus de la flotte à l'exécution : {judged['decision']} ({'; '.join(judged['why'])})", now)
    learner = MemecoinIsolatedLearner(state["completed"])
    adaptive = memecoin_learner.multipliers_for_state(state)
    fill = _buy(state, order["mint"], order["strategy"], order["key"], point, network, order["signals"], learner, adaptive, asset,
                entry=order["entry"], extra={**_causal(oid, order, point), "fleet_execution": judged})
    return _resolve(state, oid, "executed" if fill else "cancelled",
                    None if fill else "mise sous le minimum ou plafonds d'exposition atteints", now)


FLEET_LOG = 50  # dernières décisions de la flotte gardées dans la session


def _candidate(state, mint, asset, point, signals, guard, network):
    """Ce que la flotte juge pour une entrée sur cette observation : plafond, impact, aller-retour, lots ouverts, équité.
    L'équité retenue ne compte un lot sans cotation valide pour rien (``sizing_equity``), comme le dimensionnement."""
    profile = profile_for(state)
    eq = sizing_equity(state)
    used = sum(p["basis"] for p in state["positions"].values() if p["mint"] == mint)
    reserve = profile.cash_reserve * max(eq, known_equity(state) or eq)
    notional = max(0.0, min(profile.token_cap * eq - used, state["cash"] - reserve))  # plafond de _buy pour ce token
    liquidity = point.get("liquidity_usd") or 0.0
    impact = notional / (notional + liquidity / 2) if notional > 0 and liquidity > 0 else None
    data = {"asset_domain": DOMAIN, "observed_ms": int(point["ts"] * 1000), "price": point["price"], "signals": list(signals),
            "guardian": {"allowed": bool(guard.get("allowed")), "vetoes": [v.get("message", "") for v in guard.get("vetoes", [])]},
            "round_trip_loss": asset.get("round_trip_loss"), "liquidity_usd": liquidity, "notional_usd": notional,
            "impact": impact, "round_trip_cost": 2 * (DEX_FEE + impact) + 2 * network / notional if impact is not None else None,
            "open_positions": len(state["positions"]), "equity_usd": eq, "initial_usd": state.get("initial", INITIAL)}
    return data


def consult_fleet(coordinator, state, mint, asset, point, signals, guard, network, now):
    """Avis de la flotte sur une entrée fictive ; renvoie les signaux conservés (tous, ou aucun)."""
    data = _candidate(state, mint, asset, point, signals, guard, network)
    record = coordinator.decide(fleet_agents.Event("memecoin_candidate", mint, int(now * 1000), data), {})
    accepted = record["decision"] == fleet_coordinator.PROPOSAL
    entry = {
        "asset_domain": DOMAIN, "ts": now, "mint": mint, "signals": list(signals), "decision": record["decision"],
        "why": record["why"][:4], "pivotal": sorted(a for a, changed in record["contribution"].items() if changed),
        "findings": [{"agent": f["agent"], "stance": f["stance"], "summary": f["summary"]} for f in record["findings"]]}
    state.setdefault("fleet", []).append(entry)
    del state["fleet"][:-FLEET_LOG]
    if "_cycle" in state:
        state["_cycle"]["fleet"].append(entry)
    return list(signals) if accepted else []


def recheck_fleet(coordinator, state, order, asset, point, guard, network, now):
    """Avis de la flotte sur l'exécution différée d'une entrée décidée : mêmes agents et mêmes limites, sur l'observation
    d'exécution (Guardian revérifié, profondeur, aller-retour Jupiter le plus récent, frais réseau, lots ouverts et équité
    après les exécutions déjà faites). Les signaux restent ceux de la décision : seul le risque est rejugé. Ce second avis
    n'entre pas dans le journal des décisions de la flotte ; il est conservé dans le fill ou dans le journal des ordres."""
    data = _candidate(state, order["mint"], asset, point, order["signals"], guard, network)
    record = coordinator.decide(fleet_agents.Event("memecoin_candidate", order["mint"], int(now * 1000), data), {})
    return {"decision": record["decision"], "why": record["why"][:4]}


DAY_S = 86400


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valuation(state):
    """``(status, last_marked_equity)``: ``validated`` when every open lot was marked on a fresh quote this tick, ``stale``
    when a lot keeps an older mark (the last known equity is not a current value), ``unavailable`` when a lot has no
    usable mark at all (no equity can be stated)."""
    if any(not _finite(p.get("mark")) for p in state["positions"].values()):
        return "unavailable", None
    return ("stale" if state.get("stale_marks") else "validated"), equity(state)


DAY_TOLERANCE_S = 900  # a one-day return is measured over 24 h to 24 h 15 min, never over a longer period


def _hours(seconds):
    return f"{seconds / 3600:.1f}".replace(".", ",") + " h"


def _bound(curve, end, key):
    """Same one-day window as ``return_24h`` on the curve field ``key`` (prudent bounds), or ``None``."""
    target = end["ts"] - DAY_S
    if not _finite(end.get(key)) or curve[0]["ts"] > target:
        return None
    start = next((p for p in reversed(curve) if target - DAY_TOLERANCE_S <= p["ts"] <= target and _finite(p.get(key)) and p[key] > 0), None)
    if start is None:
        return None
    return {"pct": 100 * (end[key] / start[key] - 1), "from_ts": start["ts"], "window_s": end["ts"] - start["ts"]}


def _estimated(result, curve, end):
    floor = _bound(curve, end, "floor")
    if floor is None:
        return result
    return {**result, "status": "estimated", "estimate": {"floor": floor, "last_known": _bound(curve, end, "last_known")}}


def estimate(state):
    """Prudent estimate, shown apart and never as a validated value. ``None`` when every lot is valued on this cycle's
    quotes. Otherwise ``floor_usd`` counts the lots without a valid quote for nothing and ``last_known_usd`` at their last
    confirmed liquidation value, each listed with the time of that value: neither is a current price."""
    stale = set(state.get("stale_marks", []))
    if not stale:
        return None
    lots = [{"key": k, "mint": p["mint"], "last_confirmed_usd": p.get("mark") if _finite(p.get("mark")) else None,
             "marked_at": p.get("marked_at"), "unavailable_since": p.get("unavailable_since")}
            for k, p in sorted(state["positions"].items()) if p["mint"] in stale]
    return {"status": "estimate", "floor_usd": sizing_equity(state), "last_known_usd": known_equity(state), "unavailable_lots": lots}


def return_24h(state):
    """One-day equity return between two VALIDATED curve points: the latest one and the latest validated one taken 24 h to
    24 h 15 min earlier. ``window_s`` states the period actually measured. Otherwise ``status`` is ``not_evaluable`` and
    ``reason`` says why: unvalidated end bound, history under 24 h, a gap in the curve (a longer period is never presented
    as one day) or only unvalidated points 24 h earlier. ``unvalidated_points`` counts the stale points in between.
    When the day is not measurable only because some lot had no valid quote, ``status`` is ``estimated``: ``pct`` stays
    ``None`` and ``estimate`` gives the same window on prudent bounds (unquoted lots counted for nothing, or at their last
    confirmed value). An estimate is never presented as a measured return."""
    curve = [p for p in state.get("curve", []) if _finite(p.get("ts"))]
    out = {"status": "not_evaluable", "reason": None, "pct": None, "from_ts": None, "to_ts": None, "window_s": None,
           "target_s": DAY_S, "tolerance_s": DAY_TOLERANCE_S, "unvalidated_points": None}
    if not curve:
        return {**out, "reason": "aucune valorisation enregistrée"}
    end = curve[-1]
    out["to_ts"] = end["ts"]
    if state.get("stale_marks") or not _finite(end.get("equity")):
        return _estimated({**out, "reason": "valorisation de fin non validée"}, curve, end)
    target = end["ts"] - DAY_S
    if curve[0]["ts"] > target:
        return {**out, "reason": f"historique de {_hours(end['ts'] - curve[0]['ts'])}, moins de 24 h"}
    window = [p for p in curve if target - DAY_TOLERANCE_S <= p["ts"] <= target]
    start = next((p for p in reversed(window) if _finite(p.get("equity")) and p["equity"] > 0), None)
    if start is None:
        if window:
            return _estimated({**out, "reason": "aucune valorisation validée 24 h plus tôt (points non validés)"}, curve, end)
        before = next(p for p in reversed(curve) if p["ts"] < target - DAY_TOLERANCE_S)
        return {**out, "reason": f"trou dans la courbe : le point précédent date de {_hours(end['ts'] - before['ts'])}, "
                                 "pas d'une journée mesurée"}
    return {**out, "status": "measured", "pct": 100 * (end["equity"] / start["equity"] - 1), "from_ts": start["ts"],
            "window_s": end["ts"] - start["ts"],
            "unvalidated_points": sum(1 for p in curve if start["ts"] < p["ts"] < end["ts"] and not _finite(p.get("equity")))}


def report(state, wallets):
    strategies = {n: {"pnl": 0.0, "trades": 0} for n in NAMES}
    tokens = {}
    for fill in state["fills"]:
        strategy = strategies[fill["strategy"]]
        strategy["pnl"] += fill["pnl"]
        strategy["trades"] += int(fill["side"] == "buy")
        mint = fill["mint"]
        tokens[mint] = tokens.get(mint, 0.0) + fill["pnl"]
    for pos in state["positions"].values():
        if not _finite(pos.get("mark")):
            continue  # unavailable value: no latent P&L is stated
        latent = pos["mark"] - pos["basis"]
        tokens[pos["mint"]] += latent
        strategies[pos["strategy"]]["pnl"] += latent
    completed = state["completed"]
    status, last = valuation(state)
    # Realized exits include partial sales; subtract their P&L on still-open lots.
    closed_pnl = sum(f["pnl"] for f in state["fills"]) - sum(p["pnl"] for p in state["positions"].values())
    return {
        "revision": state["revision"],
        "deposit": state.get("deposit", {"amount": INITIAL, "currency": "USD"}),
        "closed_trades": len(completed),
        "win_rate": sum(t["return"] > 0 for t in completed) / len(completed) if completed else None,
        "mean_closed_pnl": closed_pnl / len(completed) if completed else None,
        "tracked_wallet_count": sum(bool(w.get("qualified")) for w in wallets.values()),
        "asset_domain": DOMAIN,
        "currency": "USD",
        "initial_capital": state.get("initial", INITIAL),
        "cash": state["cash"],
        "equity": last if status == "validated" else None,
        "last_marked_equity": last,
        "equity_status": status,
        "estimate": estimate(state),
        "profile": {"id": profile_for(state).id, "label": profile_for(state).label,
                    "version": state.get("profiles_version", PROFILES_VERSION)},
        "entry_gate": state.get("entry_gate"),
        "return_24h": return_24h(state),
        "stale_marks": state.get("stale_marks", []),
        "pending_orders": [{"id": oid, "kind": o["kind"], "key": o["key"], "decided_at": o["decided_at"],
                            "last_refusal": o.get("last_refusal")} for oid, o in sorted(state.get("orders", {}).items())],
        "equity_curve": state["curve"],
        "positions": state["positions"],
        "strategies": strategies,
        "weights": MemecoinIsolatedLearner(state["completed"]).weights,
        "smart_wallets": wallets,
        "activity": state["fills"][-100:],
        "errors": state["errors"],
        "token_ranking": sorted(tokens.items(), key=lambda t: -t[1]),
        "fleet": state.get("fleet", [])[-20:],
    }


def journal(db, since_revision=0):
    """Cycle traces of the session, oldest first (append-only: never updated, never deleted)."""
    load(db)  # creates the table on a session recorded before the journal existed
    return [json.loads(r["data"]) for r in db.query(
        "SELECT data FROM memecoin_journal WHERE revision>? ORDER BY revision", (since_revision,))]


def replay(rows):
    """Results rebuilt from the journal alone: fills, resolved orders, fleet decisions, equity curve and final cash."""
    out = {"fills": [], "order_log": [], "fleet": [], "curve": [], "cash": None, "revision": None}
    for row in rows:
        out["fills"] += row["fills"]
        out["order_log"] += row["resolved"]
        out["fleet"] += row["fleet"]
        out["curve"].append(row["curve_point"])
        out["cash"], out["revision"] = row["valuation"]["cash_usd"], row["revision"]
    return out


# What the journal keeps of a quote: its provenance and every input of the entry rules (memecoin_strategies.entry_signals).
QUOTE_TRACE = ("received_ms", "pool_sha256", "source_sha256", "ts", "price", "liquidity_usd", "volume_h1_usd",
               "volume_usd_per_min_m5", "buy_sell_count_ratio_m5", "pool_age_minutes")
PREVIOUS_TRACE = ("ts", "price", "liquidity_usd", "volume_usd_per_min_m5", "buy_sell_count_ratio_m5")


def _quote_trace(point):
    return {k: point.get(k) for k in QUOTE_TRACE}


def _signal_inputs(asset):
    """Everything ``entry_signals`` reads besides the quote itself, as seen when the signals were evaluated: the M58
    admission, the recent high and the previous observation. Each journal row is then auditable on its own."""
    last = asset.get("last")
    return {"m58": bool(asset.get("dex_id")), "peak": asset.get("peak"), "peak_ts": asset.get("peak_ts"),
            "peak_liquidity": asset.get("peak_liquidity"),
            "previous": {k: last.get(k) for k in PREVIOUS_TRACE} if isinstance(last, dict) else None}


def audit_signals(rows):
    """Re-derive every entry signal of the journal from the journal alone (``entry_signals`` on the journaled quote and
    inputs, then ``tradable_signals``), for the evaluations the Guardian allowed. ``whale_tail`` follows the tracked
    wallets, which the journal does not hold: it is left out of the comparison. Rows recorded before the inputs were
    journaled are counted as unverifiable, never as matches."""
    out = {"checked": 0, "matched": 0, "mismatches": [], "unverifiable": 0}
    for row in rows:
        for mint, ev in row.get("evaluations", {}).items():
            if not ev.get("allowed"):
                continue
            quote, inputs = row.get("quotes", {}).get(mint) or {}, ev.get("inputs")
            if inputs is None or "volume_usd_per_min_m5" not in quote:
                out["unverifiable"] += 1
                continue
            asset = {"dex_id": "m58" if inputs["m58"] else None, "last": inputs["previous"], "peak": inputs["peak"],
                     "peak_ts": inputs["peak_ts"], "peak_liquidity": inputs["peak_liquidity"]}
            derived = set(tradable_signals(asset, entry_signals(asset, dict(quote))))
            journaled = set(ev.get("signals", [])) - {"whale_tail"}
            out["checked"] += 1
            if derived == journaled:
                out["matched"] += 1
            else:
                out["mismatches"].append({"revision": row.get("revision"), "mint": mint, "journaled": sorted(journaled),
                                          "derived": sorted(derived)})
    return out


def _pseudonym(state, address):
    """Keyed pseudonym of a wallet address: equal addresses stay equal within the session, and the journal never holds
    the address itself. The key lives in the session state only, never in the journal."""
    if not isinstance(address, str):
        return None
    key = state.setdefault("journal_key", secrets.token_hex(32))
    return hmac.new(bytes.fromhex(key), address.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _whale_trace(state, asset, wallets, observed, signal, failure):
    """What ``whale_tail`` saw at the decision, pseudonymized: the attribution status of the pool, the swaps of tracked
    wallets read on this cycle (side, block time, SHA-256 of the signature) and the state the confluence rule reads (for
    each wallet holding a recent buy of this mint: qualification, dumper flag, funding origin, last buy, exclusions)."""
    mint, excluded = asset["symbol"], set(asset.get("excluded") or ())
    identity = asset.get("identity") or {}
    snapshot = []
    for address, w in sorted(wallets.items()):
        last = (w.get("last_buy") or {}).get(mint)
        if last is None:
            continue
        origin = w.get("origin")
        snapshot.append({"wallet": _pseudonym(state, address), "origin": _pseudonym(state, origin), "last_buy": last,
                         "qualified": bool(w.get("qualified")), "dumper": bool(w.get("dumper")),
                         "memecoin": w.get("asset_domain") == DOMAIN, "excluded": address in excluded,
                         "origin_excluded": origin in excluded})
    return {"identity": identity.get("status"), "reason": identity.get("reason"), "attributed": bool(excluded),
            "qualified_wallets": sum(bool(w.get("qualified")) for w in wallets.values()),
            "swaps": [{"wallet": _pseudonym(state, t["wallet"]), "side": "buy" if t["qty"] > 0 else "sell", "ts": t["ts"],
                       "tx_sha256": hashlib.sha256(t["signature"].encode("utf-8")).hexdigest()} for t in observed],
            "wallets": snapshot, "window_s": smart_wallets.CONFLUENCE_WINDOW_S, "signal": bool(signal), "error": failure}


def _whale_confluence(trace, now):
    """The confluence rule re-implemented on the journaled snapshot alone (independently of ``smart_wallets``): nothing
    without an attribution of the pool's insiders; then at least two distinct funding origins among the qualified,
    non-dumper, non-excluded wallets that bought within the window, an origin that is itself one of them not counting."""
    if not trace["attributed"]:
        return False
    candidates = {w["wallet"]: w["origin"] for w in trace["wallets"]
                  if w["qualified"] and not w["dumper"] and w["memecoin"] and w["origin"] and not w["excluded"]
                  and not w["origin_excluded"] and 0 <= now - w["last_buy"] <= trace["window_s"]}
    return len({o for o in candidates.values() if o not in candidates}) >= 2


def audit_whale(rows):
    """Re-derive every ``whale_tail`` decision of the journal from its whale trace alone, and flag any evidence dated
    after its row (information from the future). Allowed evaluations without a trace (recorded before it existed, or
    with an attribution failure) are unverifiable, never matches."""
    out = {"checked": 0, "matched": 0, "mismatches": [], "unverifiable": 0, "future": []}
    for row in rows:
        now = row.get("ts")
        for mint, ev in row.get("evaluations", {}).items():
            if not ev.get("allowed"):
                continue
            trace = ev.get("whale")
            if not isinstance(trace, dict) or trace.get("error"):
                out["unverifiable"] += 1
                continue
            late = [t for t in [s["ts"] for s in trace["swaps"]] + [w["last_buy"] for w in trace["wallets"]] if t > now]
            if late:
                out["future"].append({"revision": row.get("revision"), "mint": mint, "after_s": max(late) - now})
            journaled, derived = "whale_tail" in ev.get("signals", []), _whale_confluence(trace, now)
            out["checked"] += 1
            if journaled == derived == trace["signal"]:
                out["matched"] += 1
            else:
                out["mismatches"].append({"revision": row.get("revision"), "mint": mint, "journaled": journaled,
                                          "derived": derived})
    return out



def audit_routing(rows):
    """Replay the inactive router (``memecoin_strategies.route``) on the journal and compare it with the engine, event by
    event (a token with tradable signals in an evaluation the Guardian allowed): the engine's lots decided on the event,
    the router's single decision, the lots beyond one per event, the strategy pairs, the abstentions and their reason
    (entry gate, then profile, then fleet: the engine's order), and the P&L realised to date by the lots of a same
    event. Report only: the router decides nothing."""
    out = {"events": 0, "engine_lots": 0, "router_lots": 0, "extra_lots": 0, "router_without_engine_lot": 0,
           "multi_strategy_events": 0, "pairs": {}, "abstentions": {}, "same_event_pnl": []}
    lots, current, realised = {}, {}, {}  # event -> buy order ids; lot key -> its current buy; buy order id -> P&L
    for row in rows:
        decided = [o for o in row.get("decided", []) if ":buy:" in o]
        fleet = {f["mint"]: f["decision"] for f in row.get("fleet", [])}
        gate = (row.get("valuation") or {}).get("entry_gate") or {}
        for mint, ev in sorted(row.get("evaluations", {}).items()):
            if not ev.get("allowed") or not ev.get("signals"):
                continue
            engine = sorted(o.split(":buy:", 1)[1].split("/", 1)[0] for o in decided if o.endswith("/" + mint))
            refusal = (gate.get("reason") if gate.get("open") is False else None) or ev.get("profile_refusal") or (
                f"flotte : {fleet[mint]}" if fleet.get(mint, fleet_coordinator.PROPOSAL) != fleet_coordinator.PROPOSAL else None)
            routed = route(ev["signals"], refusal)
            out["events"] += 1
            out["engine_lots"] += len(engine)
            out["extra_lots"] += max(0, len(engine) - 1)
            if routed["decision"] == "enter":
                out["router_lots" if engine else "router_without_engine_lot"] += 1
            else:
                out["abstentions"][routed["reason"]] = out["abstentions"].get(routed["reason"], 0) + 1
            if len(engine) > 1:
                out["multi_strategy_events"] += 1
                pair = "+".join(engine)
                out["pairs"][pair] = out["pairs"].get(pair, 0) + 1
        for f in row.get("fills", []):
            key = f"{f['strategy']}/{f['mint']}"
            if f["side"] == "buy":
                lots.setdefault((f["mint"], f["decided_at"]), []).append(f["order_id"])
                current[key], realised[f["order_id"]] = f["order_id"], 0.0
            elif key in current:
                realised[current[key]] += f.get("pnl") or 0.0
    for (mint, decided_at), ids in sorted(lots.items()):
        if len(ids) > 1:
            out["same_event_pnl"].append({"mint": mint, "decided_at": decided_at,
                                          "lots": {o.split(":buy:", 1)[1].split("/", 1)[0]: realised[o] for o in sorted(ids)}})
    return out

def watch_rotation(assets, priority):
    """Assets quoted in rotation this cycle besides ``priority`` (held lots, pending orders): among the ``WATCH_LIMIT``
    most recently admitted pools, the ``ROTATION`` least recently checked. Older candidates stay recorded, unquoted."""
    candidates = sorted(set(assets) - priority, key=lambda m: (-assets[m].get("admitted_ms", 0), m))[:WATCH_LIMIT]
    return sorted(candidates, key=lambda m: (assets[m].get("checked", 0), m))[:ROTATION]


def entry_gate(state, now, network, regime=None):
    """Written entry rule: entries need the network fee of this cycle, and no held lot whose valid quote has been missing
    for less than ``UNAVAILABLE_GRACE_S`` (a transient outage). A lot unavailable for longer no longer blocks entries:
    it stays open and re-quoted, and ``sizing_equity`` counts it for nothing."""
    if network is None:
        return {"open": False, "reason": "frais réseau indisponibles", "blocking": []}
    if regime and regime.get("status") == "risk_off":
        return {"open": False, "reason": f"régime de marché : SOL {regime['sol_change_h1_pct']:+.1f} % sur 1 h "
                                         f"(seuil −{100 * REGIME_DROP_H1:.0f} %)", "blocking": []}
    blocking = sorted({p["mint"] for p in state["positions"].values()
                       if p["mint"] in set(state.get("stale_marks", [])) and now - p.get("unavailable_since", now) < UNAVAILABLE_GRACE_S})
    if blocking:
        return {"open": False, "reason": "valorisation momentanément indisponible", "blocking": blocking}
    return {"open": True, "reason": None, "blocking": []}


def tick(hub, db, bus):
    state, wallets = load(db)
    adopt_calibrated_exits(state)
    base = state["revision"]
    start = len(state["fills"])
    client = hub.client
    scanned = scan_once(client, db, bus, registry=hub.registry)
    state["errors"] = list(scanned["errors"])
    now = int(client.clock())
    assets = state["assets"]
    refreshed = set()
    # Include tokens previously found by the standalone scanner too.
    for row in db.query(
        "SELECT data FROM decisions WHERE agent IN ('solana_scanner', 'established_pools') AND kind='observation_accepted' "
        "ORDER BY id DESC LIMIT 32"
    ):
        event = json.loads(row["data"])
        if event.get("asset_domain") == DOMAIN:
            asset = assets.setdefault(event["symbol"], {k: event[k] for k in ("symbol", "pool", "creation", "quote_mint", "dex_id") if k in event})
            if event["symbol"] not in refreshed:
                if isinstance(event.get("round_trip"), dict):
                    asset["round_trip_loss"] = event["round_trip"].get("loss")  # aller-retour Jupiter le plus récent
                if isinstance(event.get("observed_ms"), int):
                    asset["admitted_ms"] = max(asset.get("admitted_ms", 0), event["observed_ms"])  # admission la plus récente
            refreshed.add(event["symbol"])
    try:
        network = network_fee(client) if assets else None
    except SOURCE_ERRORS as exc:
        state["errors"].append(str(exc))
        network = None
    try:
        regime = market_regime(client) if assets else None
    except SOURCE_ERRORS as exc:
        regime = {"status": "unknown", "reason": str(exc)[:200]}
    state["market_regime"] = regime
    winners = {m for m, a in assets.items() if a.get("winner") and not a.get("blocked")}
    held = {p["mint"] for p in state["positions"].values()}
    orders = state.setdefault("orders", {})
    ordered = {o["mint"] for o in orders.values() if o["mint"] in assets} - held
    points = {}
    fleet = fleet_coordinator.Coordinator(clock=client.clock)  # cycle fini : une flotte par passage, sans pouvoir d'ordre
    trace = {"quotes": {}, "evaluations": {}}
    state["_cycle"] = {"resolved": [], "fleet": []}  # transient: what this cycle resolved and asked the fleet
    for mint in sorted(held) + sorted(ordered) + watch_rotation(assets, held | ordered):
        try:
            points[mint] = quote(client, assets[mint], now)
            trace["quotes"][mint] = _quote_trace(points[mint])
        except SOURCE_ERRORS as exc:
            state["errors"].append({"mint": mint, "reason": str(exc)})
            trace["quotes"][mint] = {"error": str(exc)[:200]}
        assets[mint]["checked"] = now
    state["stale_marks"] = sorted(held - points.keys() if network is not None else held)
    executed = {f["order_id"] for f in state["fills"] if f.get("order_id")}
    marked = set()
    # Phase 1: value the open lots on the fresh quotes, then fill the exits decided earlier (protective, no guardian call).
    if network is not None:
        for mint, point in points.items():
            try:
                for pos in [p for p in state["positions"].values() if p["mint"] == mint]:
                    _mark(pos, point, network)
                    pos.pop("unavailable_since", None)
                marked.add(mint)
                for oid in sorted(o for o, order in orders.items() if order["kind"] == "sell" and order["mint"] == mint):
                    _execute_sell(state, oid, orders[oid], point, network, now, executed, assets[mint])
            except SOURCE_ERRORS as exc:
                state["errors"].append({"mint": mint, "reason": str(exc), "stage": "valorisation"})
    state["stale_marks"] = sorted({p["mint"] for p in state["positions"].values()} - marked)
    for pos in state["positions"].values():
        if pos["mint"] not in marked:
            pos.setdefault("unavailable_since", now)
    gate = entry_gate(state, now, network, regime)  # written rules: transient valuation outage, market regime
    # Phase 2: safety re-check, pending entries, then new decisions (filled on a later observation, never on this one).
    for mint, point in points.items():
        asset = assets[mint]
        try:
            previous = asset.get("last") or {}
            inputs = _signal_inputs(asset)  # before the signals are evaluated and before this observation is kept
            signals, guard = evaluate(client, asset, point, False)
            signals = tradable_signals(asset, signals)
            trace["evaluations"][mint] = {"signals": sorted(signals), "allowed": bool(guard.get("allowed")),
                                          "vetoes": [v.get("code") for v in guard.get("vetoes", [])],
                                          "gap_s": point["ts"] - previous["ts"] if _finite(previous.get("ts")) else None,
                                          "inputs": inputs}
            asset["blocked"] = not guard["allowed"]
            if not guard["allowed"]:
                winners.discard(mint)
                asset["guardian_failed"] = True
            if guard["allowed"]:
                asset.setdefault("first_price", point["price"])
                asset["winner"] = not asset.get("guardian_failed") and (
                    asset.get("winner", False) or point["price"] >= 3 * asset["first_price"]
                )
                observed, whale, failure = [], False, None
                try:
                    whale = smart_wallets.follow(client, asset, wallets, winners, now, observed)
                    if whale:
                        signals.append("whale_tail")
                        trace["evaluations"][mint]["signals"] = sorted(signals)
                except SOURCE_ERRORS as exc:
                    state["errors"].append({"mint": mint, "wallets": str(exc)})
                    failure = str(exc)[:200]
                trace["evaluations"][mint]["whale"] = _whale_trace(state, asset, wallets, observed, whale, failure)
            if network is not None:
                opened_now = set()
                for oid in sorted(o for o, order in orders.items() if order["kind"] == "buy" and order["mint"] == mint):
                    key = orders[oid]["key"]
                    _execute_buy(state, oid, orders[oid], point, network, now, executed, asset, guard, gate["open"], fleet)
                    if not gate["open"] and oid in orders:
                        orders[oid]["last_refusal"] = f"entrée suspendue : {gate['reason']}"  # the written rule that applied
                    if key in state["positions"] and state["positions"][key].get("opened_at") == now:
                        opened_now.add(key)
                        marked.add(mint)
                if mint in marked:
                    for key, pos in [(k, p) for k, p in state["positions"].items() if p["mint"] == mint]:
                        if key in opened_now or any(o["kind"] == "sell" and o["key"] == key for o in orders.values()):
                            continue
                        decision = _exit(state, key, pos, point)
                        if decision is not None:
                            _decide(state, "sell", key, mint, pos["strategy"], point, now, detail=decision[0], fraction=decision[1])
                if signals and gate["open"] and profile_refusal(state, asset, point):
                    trace["evaluations"].setdefault(mint, {})["profile_refusal"] = profile_refusal(state, asset, point)
                    signals = []  # the session's profile refuses this entry before the fleet is asked
                if signals and gate["open"]:  # no new entry while a lot's value is only transiently unknown
                    signals = consult_fleet(fleet, state, mint, asset, point, signals, guard, network, now)
                    entry = _entry(point, asset, signals) if signals else None  # conditions seen at the decision
                    for strategy in [n for n in NAMES if n in signals]:
                        key = strategy + "/" + mint
                        if key not in state["positions"] and not any(o["kind"] == "buy" and o["key"] == key for o in orders.values()):
                            _decide(state, "buy", key, mint, strategy, point, now, signals=list(signals), entry=entry)
            _observe(asset, point)
        except SOURCE_ERRORS as exc:
            state["errors"].append({"mint": mint, "reason": str(exc)})
    for oid in [o for o, order in orders.items() if order["kind"] == "buy" and now - order["decided_at"] > ORDER_TTL_S]:
        _resolve(state, oid, "expired", "aucune cotation postérieure valide dans les 10 minutes", now)
    # Valuation after processing: a lot not re-marked on a fresh quote this tick is stale.
    state["stale_marks"] = sorted({p["mint"] for p in state["positions"].values()} - marked)
    state["entry_gate"] = entry_gate(state, now, network, regime)
    state["revision"] += 1
    state["curve"].append({"ts": now, "equity": None if state["stale_marks"] else equity(state),
                           "floor": sizing_equity(state), "last_known": known_equity(state)})
    status, _ = valuation(state)
    trace.update(revision=state["revision"], ts=now, asset_domain=DOMAIN, network_fee_usd=network, market_regime=regime,
                 decided=sorted(o for o, order in orders.items() if order["decided_at"] == now),
                 **state.pop("_cycle"),  # resolved orders and fleet decisions of this cycle, never capped
                 fills=state["fills"][start:], errors=state["errors"], curve_point=state["curve"][-1],
                 valuation={"status": status, "cash_usd": state["cash"], "stale_marks": state["stale_marks"],
                            "entry_gate": state["entry_gate"]})
    with db.transaction():
        prior = json.loads(db.get_meta(KEY, "{}"))
        if prior.get("revision", 0) != base:
            raise ValueError("session modifiée par un autre tick")
        db.set_meta(KEY, json.dumps(state, allow_nan=False))
        db.execute("INSERT INTO memecoin_journal(revision, ts, asset_domain, data) VALUES(?,?,?,?)",
                   (state["revision"], now, DOMAIN, json.dumps(trace, allow_nan=False)))  # same transaction as the state
        for address, w in wallets.items():
            db.execute("INSERT OR REPLACE INTO smart_wallets VALUES(?,?,?)", (address, DOMAIN, json.dumps(w, allow_nan=False)))
        for fill in state["fills"][start:]:
            db.after_commit(lambda fill=fill: bus.publish("memecoin_fill", fill))
        summary = report(state, wallets)
        event = {k: summary[k] for k in ("asset_domain", "equity", "strategies", "token_ranking")}
        db.after_commit(lambda: bus.publish("memecoin_session", event))
    return summary
