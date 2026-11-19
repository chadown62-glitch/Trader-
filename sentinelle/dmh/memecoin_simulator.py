"""On-demand simulation in USD of a separate fictitious 30 USD endowment."""

import json
import math

from ..ai.features_memecoin import extract_features
from . import smart_wallets
from .dex import _rpc
from .memecoin_strategies import MemecoinIsolatedLearner, NAMES, evaluate
from .net import SourceUnavailable
from .solana_scanner import scan_once

KEY = "memecoin:paper-session:v1"
DOMAIN = "memecoin"
INITIAL = 30.0
DEX_FEE = 0.003  # scenario assumption, not a measured pool fee
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


def quote(client, asset, now):
    mint = asset["symbol"]
    pairs = client.get("api.dexscreener.com", f"/token-pairs/v1/solana/{mint}").json()
    pair = next(
        (
            p
            for p in pairs
            if p["chainId"] == "solana"
            and p["dexId"] == "raydium"
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
    point.update(price=price, ts=now)
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
    for strategy in NAMES:
        key = strategy + "/" + mint
        pos = state["positions"].get(key)
        if pos:
            liquidation, _, _ = value(pos["qty"], point, network)
            pos["mark"] = liquidation
            ratio = liquidation / pos["basis"]
            pos["high"] = max(pos["high"], ratio)
            if ratio <= 0.83:
                reason, fraction = "stop_loss", 1
            elif pos["stage"] and ratio <= pos["high"] * 0.8:
                reason, fraction = "trailing_stop", 1
            elif pos["stage"] < 2 and ratio >= (1.5, 2.0)[pos["stage"]]:
                reason, fraction = "take_profit", 0.5
            else:
                continue
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
            if fraction == 1:
                state["completed"].append({"strategy": strategy, "mint": mint, "return": pos["pnl"] / pos["initial"]})
                del state["positions"][key]
            side = "sell"
        elif strategy in signals:
            eq = equity(state)
            used = sum(p["basis"] for p in state["positions"].values() if p["mint"] == mint)
            strategy_used = sum(p["basis"] for p in state["positions"].values() if p["strategy"] == strategy)
            size = min(
                eq * learner.fractions[strategy] * learner.weights[strategy],
                0.10 * eq - used,
                eq * learner.weights[strategy] - strategy_used,
                state["cash"] - 0.25 * eq,
            )
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
            }
            side, reason, pnl = "buy", "signal", 0.0
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
    return {
        "asset_domain": DOMAIN,
        "currency": "USD",
        "initial_capital": INITIAL,
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
    }


def tick(hub, db, bus):
    state, wallets = load(db)
    base = state["revision"]
    start = len(state["fills"])
    client = hub.client
    scanned = scan_once(client, db, bus, registry=hub.registry)
    state["errors"] = list(scanned["errors"])
    now = int(client.clock())
    assets = state["assets"]
    # Include tokens previously found by the standalone scanner too.
    for row in db.query(
        "SELECT data FROM decisions WHERE agent='solana_scanner' AND kind='observation_accepted' ORDER BY id DESC LIMIT 32"
    ):
        event = json.loads(row["data"])
        if event.get("asset_domain") == DOMAIN:
            assets.setdefault(event["symbol"], {k: event[k] for k in ("symbol", "pool", "creation", "quote_mint")})
    try:
        network = network_fee(client) if assets else None
    except SOURCE_ERRORS as exc:
        state["errors"].append(str(exc))
        network = None
    winners = {m for m, a in assets.items() if a.get("winner") and not a.get("blocked")}
    held = {p["mint"] for p in state["positions"].values()}
    pending = sorted(set(assets) - held, key=lambda m: assets[m].get("checked", 0))
    points = {}
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
