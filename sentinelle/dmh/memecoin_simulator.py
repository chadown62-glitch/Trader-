"""On-demand simulation in USD of a separate fictitious 30 USD endowment.

Exits are versioned per position. Every paper session driven by ``tick`` (worker, cloud runner, cockpit route) adopts
the calibrated asymmetric policy of ``memecoin_strategies`` (selected by ``memecoin_calibrator``): stop at -20 % of the
net liquidation value, 20 % sold once the net ratio reaches 1.25 (fees and slippage covered), then a break-even floor
on the remaining balance, take-profit slices at x3, x6 and x10 and an adaptive trailing stop. A position keeps the
policy it was opened under (a lot never changes rules mid-flight); positions opened before this version, or by a raw
state that never went through ``tick``, keep the legacy ladder (-17 %, 50 % at 1.5x and 2x, trailing 20 %).
Candidates (M58): established pools (``established_pools``: liquidity >= 20,000 USD, at least one hour old, an active
last hour, a Jupiter round trip quoted without a wallet, then the anti-rug veto) instead of pools born minutes ago.
Before any fictitious entry the agent fleet (``sentinelle.fleet``) is consulted: a veto, a risk refusal, an alert or an
objection (impact, cost, unconfirmed resale) means no entry; exits are never blocked.
A new stake is the Kelly target scaled by the bounded multiplier of ``memecoin_learner`` (0.5x-1.5x, learned from the
closed lots), within the unchanged hard limits; each position records its entry conditions and why it was so sized.
Paper only: no order, no key; every fill is tagged ``asset_domain="memecoin"`` and ``simulated``."""

import json
import math

from ..ai.features_memecoin import extract_features
from ..config import paper_capital
from ..fleet import agents as fleet_agents, coordinator as fleet_coordinator
from . import established_pools, memecoin_learner, smart_wallets
from .dex import _rpc
from .memecoin_strategies import CALIBRATED, MemecoinIsolatedLearner, NAMES, confidence, evaluate, exit_decision
from .net import SourceUnavailable

KEY = "memecoin:paper-session:v1"
DOMAIN = "memecoin"
INITIAL = 30.0
DEX_FEE = 0.003  # scenario assumption, not a measured pool fee
LEGACY_EXITS, CALIBRATED_EXITS = "legacy", "calibrated-v1"
EXIT_POLICIES = {CALIBRATED_EXITS: CALIBRATED}  # stop -20 %, 20 % at 1.25x, break-even floor, x3/x6/x10, adaptive trailing
# The cockpit labels four reasons; the break-even floor is a protective trailing exit, its exact cause stays in ``detail``.
DISPLAY_REASON = {"break_even_stop": "trailing_stop"}


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


def load(db):
    with db.transaction():
        db.execute(
            "CREATE TABLE IF NOT EXISTS smart_wallets(address TEXT PRIMARY KEY, asset_domain TEXT NOT NULL "
            "CHECK(asset_domain='memecoin'), data TEXT NOT NULL)"
        )
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


def quote(client, asset, now):
    mint = asset["symbol"]
    pairs = client.get("api.dexscreener.com", f"/token-pairs/v1/solana/{mint}").json()
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
    if not math.isfinite(price) or price <= 0 or not point["liquidity_usd"]:
        raise ValueError("prix ou profondeur indisponible")
    h1 = (pair.get("volume") or {}).get("h1")
    finite = isinstance(h1, (int, float)) and not isinstance(h1, bool) and math.isfinite(h1) and h1 >= 0
    point.update(price=price, ts=now, volume_h1_usd=float(h1) if finite else None)
    return point


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
    impact = max(0.01, mid / (mid + point["liquidity_usd"] / 2))
    gross = mid * (1 - impact)
    return max(0.0, gross * (1 - DEX_FEE) - network), mid * impact, gross * DEX_FEE


def equity(state):
    return state["cash"] + sum(pos["mark"] for pos in state["positions"].values())


def process(state, mint, point, signals, network):
    asset = state["assets"][mint]
    learner = MemecoinIsolatedLearner(state["completed"])
    adaptive = memecoin_learner.multipliers(state["completed"])  # bounded 0.5x-1.5x, from closed lots only
    for strategy in NAMES:
        key = strategy + "/" + mint
        pos = state["positions"].get(key)
        if pos:
            liquidation, _, _ = value(pos["qty"], point, network)
            pos["mark"] = liquidation
            decision = exit_for(pos, liquidation / pos["basis"])
            if decision is None:
                continue
            detail, fraction = decision
            reason = DISPLAY_REASON.get(detail, detail)
            sold = pos["qty"] * fraction
            net, slip, fee = value(sold, point, network)
            if net <= 0:
                continue  # the remaining dust has no realizable value
            basis = pos["basis"] * fraction
            pnl = net - basis
            state["cash"] += net
            pos["pnl"] += pnl
            pos["qty"] -= sold
            pos["basis"] -= basis
            pos["stage"] += 1
            pos["mark"] = value(pos["qty"], point, network)[0]
            if fraction >= 1:
                state["completed"].append({"strategy": strategy, "mint": mint, "return": pos["pnl"] / pos["initial"],
                                           **{k: pos[k] for k in ("entry", "sizing") if k in pos}})
                del state["positions"][key]
            side = "sell"
        elif strategy in signals:
            eq = equity(state)
            used = sum(p["basis"] for p in state["positions"].values() if p["mint"] == mint)
            strategy_used = sum(p["basis"] for p in state["positions"].values() if p["strategy"] == strategy)
            target = eq * learner.fractions[strategy] * learner.weights[strategy]
            learned = adaptive[strategy]
            # The learned multiplier scales the Kelly target only: the hard limits below never move.
            limits = {
                "kelly_target": target * learned["multiplier"],
                "token_cap": 0.10 * eq - used,
                "strategy_budget": eq * learner.weights[strategy] - strategy_used,
                "cash_reserve": state["cash"] - 0.25 * eq,
            }
            size = min(limits.values())
            if size <= network * 2 or size < 0.05:
                continue
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
                "high": 1.0,
                "stage": 0,
                "pnl": 0.0,
                "exit_policy": state.get("exit_policy", LEGACY_EXITS),
                "asset_domain": DOMAIN,
                # Entry conditions, compared later between winning and losing lots (threshold recommendations).
                "entry": {"liquidity_usd": point["liquidity_usd"], "volume_h1_usd": point.get("volume_h1_usd"),
                          "confidence": confidence(signals), "signals": sorted(signals)},
                "sizing": {"learner": memecoin_learner.VERSION, "multiplier": learned["multiplier"], "score": learned["score"],
                           "closed_trades": learned["closed_trades"], "kelly_target_usd": target, "size_usd": size,
                           "binding": min(limits, key=limits.get), "reason": learned["reason"]},
            }
            side, reason, detail, pnl = "buy", "signal", "signal", 0.0
        else:
            continue
        state["fills"].append(
            {
                "asset_domain": DOMAIN,
                "simulated": True,
                "ts": point["ts"],
                "strategy": strategy,
                "mint": mint,
                "side": side,
                "reason": reason,
                "detail": detail,
                "exit_policy": (pos if side == "sell" else state["positions"][key]).get("exit_policy", LEGACY_EXITS),
                "pnl": pnl,
                "quantity": sold if side == "sell" else qty,
                "slippage_usd": slip,
                "dex_fee_usd": fee,
                "network_fee_usd": network,
            }
        )
    asset["last"] = point
    asset["peak"] = max(asset.get("peak", 0), point["price"])
    asset["peak_liquidity"] = max(asset.get("peak_liquidity", 0), point["liquidity_usd"])


FLEET_LOG = 50  # dernières décisions de la flotte gardées dans la session


def consult_fleet(coordinator, state, mint, asset, point, signals, guard, network, now):
    """Avis de la flotte sur une entrée fictive ; renvoie les signaux conservés (tous, ou aucun)."""
    eq = equity(state)
    used = sum(p["basis"] for p in state["positions"].values() if p["mint"] == mint)
    notional = max(0.0, min(0.10 * eq - used, state["cash"] - 0.25 * eq))  # plafond de process() pour ce token
    liquidity = point.get("liquidity_usd") or 0.0
    impact = notional / (notional + liquidity / 2) if notional > 0 and liquidity > 0 else None
    data = {"asset_domain": DOMAIN, "observed_ms": int(point["ts"] * 1000), "price": point["price"], "signals": list(signals),
            "guardian": {"allowed": bool(guard.get("allowed")), "vetoes": [v.get("message", "") for v in guard.get("vetoes", [])]},
            "round_trip_loss": asset.get("round_trip_loss"), "liquidity_usd": liquidity, "notional_usd": notional,
            "impact": impact, "round_trip_cost": 2 * (DEX_FEE + impact) + 2 * network / notional if impact is not None else None,
            "open_positions": len(state["positions"]), "equity_usd": eq, "initial_usd": state.get("initial", INITIAL)}
    record = coordinator.decide(fleet_agents.Event("memecoin_candidate", mint, int(now * 1000), data), {})
    accepted = record["decision"] == fleet_coordinator.PROPOSAL
    state.setdefault("fleet", []).append({
        "asset_domain": DOMAIN, "ts": now, "mint": mint, "signals": list(signals), "decision": record["decision"],
        "why": record["why"][:4], "pivotal": sorted(a for a, changed in record["contribution"].items() if changed),
        "findings": [{"agent": f["agent"], "stance": f["stance"], "summary": f["summary"]} for f in record["findings"]]})
    del state["fleet"][:-FLEET_LOG]
    return list(signals) if accepted else []


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
        latent = pos["mark"] - pos["basis"]
        tokens[pos["mint"]] += latent
        strategies[pos["strategy"]]["pnl"] += latent
    completed = state["completed"]
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
        "equity": None if state.get("stale_marks") else equity(state),
        "last_marked_equity": equity(state),
        "stale_marks": state.get("stale_marks", []),
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
            if event["symbol"] not in refreshed and isinstance(event.get("round_trip"), dict):
                asset["round_trip_loss"] = event["round_trip"].get("loss")  # aller-retour Jupiter le plus récent
            refreshed.add(event["symbol"])
    try:
        network = network_fee(client) if assets else None
    except SOURCE_ERRORS as exc:
        state["errors"].append(str(exc))
        network = None
    winners = {m for m, a in assets.items() if a.get("winner") and not a.get("blocked")}
    held = {p["mint"] for p in state["positions"].values()}
    pending = sorted(set(assets) - held, key=lambda m: assets[m].get("checked", 0))
    points = {}
    fleet = fleet_coordinator.Coordinator(clock=client.clock)  # cycle fini : une flotte par passage, sans pouvoir d'ordre
    for mint in sorted(held) + pending[:4]:
        try:
            points[mint] = quote(client, assets[mint], now)
        except SOURCE_ERRORS as exc:
            state["errors"].append({"mint": mint, "reason": str(exc)})
        assets[mint]["checked"] = now
    state["stale_marks"] = sorted(held - points.keys() if network is not None else held)
    for mint, point in points.items():
        asset = assets[mint]
        try:
            signals, guard = evaluate(client, asset, point, False)
            asset["blocked"] = not guard["allowed"]
            if not guard["allowed"]:
                winners.discard(mint)
                asset["guardian_failed"] = True
            if guard["allowed"]:
                asset.setdefault("first_price", point["price"])
                asset["winner"] = not asset.get("guardian_failed") and (
                    asset.get("winner", False) or point["price"] >= 3 * asset["first_price"]
                )
                try:
                    if smart_wallets.follow(client, asset, wallets, winners, now):
                        signals.append("whale_tail")
                except SOURCE_ERRORS as exc:
                    state["errors"].append({"mint": mint, "wallets": str(exc)})
            if network is not None:
                if signals and not state["stale_marks"]:
                    signals = consult_fleet(fleet, state, mint, asset, point, signals, guard, network, now)
                process(state, mint, point, [] if state["stale_marks"] else signals, network)
        except SOURCE_ERRORS as exc:
            state["errors"].append({"mint": mint, "reason": str(exc)})
    state["revision"] += 1
    state["curve"].append({"ts": now, "equity": None if state["stale_marks"] else equity(state)})
    with db.transaction():
        prior = json.loads(db.get_meta(KEY, "{}"))
        if prior.get("revision", 0) != base:
            raise ValueError("session modifiée par un autre tick")
        db.set_meta(KEY, json.dumps(state, allow_nan=False))
        for address, w in wallets.items():
            db.execute("INSERT OR REPLACE INTO smart_wallets VALUES(?,?,?)", (address, DOMAIN, json.dumps(w, allow_nan=False)))
        for fill in state["fills"][start:]:
            db.after_commit(lambda fill=fill: bus.publish("memecoin_fill", fill))
        summary = report(state, wallets)
        event = {k: summary[k] for k in ("asset_domain", "equity", "strategies", "token_ranking")}
        db.after_commit(lambda: bus.publish("memecoin_session", event))
    return summary
