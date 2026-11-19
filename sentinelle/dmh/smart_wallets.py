"""Bounded finalized-transaction samples. Eligibility uses observed, fully closed WSOL round trips."""

import hashlib
from collections import defaultdict

from .dex import _rpc
from .solana_scanner import AMM4, CPMM

from .tokens import b58decode

DOMAIN = "memecoin"
WSOL = "So11111111111111111111111111111111111111112"


def recent(client, address, limit=6, *, before=None):
    options = {"limit": limit, "commitment": "finalized"}
    if before:
        options["before"] = before
    rows, _ = _rpc(client, "solana", "getSignaturesForAddress", [address, options])
    return [r for r in reversed(rows) if r.get("err") is None and isinstance(r.get("blockTime"), int)]


def transaction(client, signature):
    tx, _ = _rpc(
        client,
        "solana",
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}],
    )
    if tx and tx["meta"]["err"] is None and tx["transaction"]["signatures"][0] == signature:
        return tx
    return None


def identities(client, asset):
    tx = transaction(client, asset["creation"]["signature"])
    if not tx:
        return []
    signers = {k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"] if k["signer"]}
    # Include the AMM creator explicitly, even if an instruction uses a separate fee payer.
    for ix in tx["transaction"]["message"]["instructions"]:
        if ix.get("programId") == asset["creation"]["program"] and asset["pool"] in ix.get("accounts", []):
            signers.add(ix["accounts"][0 if ix["programId"] == CPMM else 17])
    # Require a witnessed mint initialization. Unknown deployer => no wallet attribution.
    for row in recent(client, asset["symbol"], 8):
        mint_tx = transaction(client, row["signature"])
        if not mint_tx:
            continue
        msg = mint_tx["transaction"]["message"]
        for ix in msg["instructions"]:
            parsed = ix.get("parsed") or {}
            info = parsed.get("info", {})
            if (
                ix.get("program") == "spl-token"
                and parsed.get("type") in ("initializeMint", "initializeMint2")
                and info.get("mint") == asset["symbol"]
            ):
                return sorted(signers | {k["pubkey"] for k in msg["accountKeys"] if k["signer"]} | {info["mintAuthority"]})
    return []


def swap(tx, asset):
    if not tx or not asset.get("excluded") or asset["quote_mint"] != WSOL:
        return None
    msg, meta = tx["transaction"]["message"], tx["meta"]
    keys = msg["accountKeys"]
    signers = [k["pubkey"] for k in keys if k["signer"]]
    if len(signers) != 1 or signers[0] in asset["excluded"]:
        return None
    wallet = signers[0]
    swaps = []
    for ix in msg["instructions"]:
        pid = ix.get("programId")
        if pid not in (CPMM, AMM4):
            continue
        data = b58decode(ix.get("data", ""))
        if (pid == AMM4 and data[:1] in (b"\x09", b"\x0b")) or (
            pid == CPMM
            and data[:8] in [hashlib.sha256(f"global:{n}".encode()).digest()[:8] for n in ("swap_base_input", "swap_base_output")]
        ):
            swaps.append(ix)
    if len(swaps) != 1 or asset["pool"] not in swaps[0]["accounts"]:
        return None
    delta = defaultdict(float)
    for name, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for bal in meta[name]:
            if bal.get("owner") == wallet:
                amount = bal["uiTokenAmount"]
                delta[bal["mint"]] += sign * int(amount["amount"]) / 10 ** amount["decimals"]
    qty, sol = delta[asset["symbol"]], delta[WSOL]
    if qty * sol >= 0 or any(v for k, v in delta.items() if k not in (WSOL, asset["symbol"])):
        return None
    return {
        "wallet": wallet,
        "qty": qty,
        "sol": sol - meta["fee"] / 1e9,
        "ts": tx["blockTime"],
        "signature": tx["transaction"]["signatures"][0],
    }


def funding_origin(client, wallet, first_trade):
    # Only a witnessed initial native funding transfer counts as a distinct provenance.
    for row in recent(client, wallet, before=first_trade):
        tx = transaction(client, row["signature"])
        if not tx:
            continue
        msg = tx["transaction"]["message"]
        keys = [k["pubkey"] for k in msg["accountKeys"]]
        if wallet not in keys or tx["meta"]["preBalances"][keys.index(wallet)] != 0:
            continue
        for ix in msg["instructions"]:
            p = ix.get("parsed") or {}
            i = p.get("info", {})
            if ix.get("program") == "system" and p.get("type") == "transfer" and i.get("destination") == wallet:
                return i["source"]
    return None


def record(wallets, mint, trade, created, eligible):
    w = wallets.setdefault(trade["wallet"], {"asset_domain": DOMAIN, "cycles": [], "lots": {}, "last_buy": {}})
    watermark = w.setdefault("last_trade_ts", {}).get(mint)
    if watermark is not None and trade["ts"] < watermark:
        return
    w["last_trade_ts"][mint] = trade["ts"]
    w.setdefault("first_trade", trade["signature"])
    lot = w["lots"].setdefault(mint, {"qty": 0.0, "spent": 0.0, "received": 0.0, "early": False})
    qty, sol = trade["qty"], trade["sol"]
    if qty > 0:
        if not lot["qty"]:
            lot["early"] = 0 <= trade["ts"] - created <= 300
        lot["qty"] += qty
        lot["spent"] -= sol
        if eligible and trade["ts"] > w.get("qualified_since", trade["ts"]):
            w["last_buy"][mint] = trade["ts"]
    elif 0 < -qty <= lot["qty"] + 1e-10:
        lot["qty"] += qty
        lot["received"] += sol
        if abs(lot["qty"]) <= 1e-10 and lot["spent"] > 0:
            w["cycles"].append({"mint": mint, "pnl_sol": lot["received"] - lot["spent"], "early": lot["early"]})
            del w["lots"][mint]
    else:
        w["lots"].pop(mint, None)  # incomplete history cannot establish profitability


def profitable(w, winners):
    cycles = w["cycles"]
    w["win_rate"] = sum(c["pnl_sol"] > 0 for c in cycles) / len(cycles) if cycles else None
    return (
        len(cycles) >= 2
        and w["win_rate"] > 0.5
        and sum(c["pnl_sol"] for c in cycles) > 0
        and any(c["early"] and c["pnl_sol"] > 0 and c["mint"] in winners for c in cycles)
    )


def confluence(wallets, mint, now, excluded):
    candidates = {
        k: w["origin"]
        for k, w in wallets.items()
        if w.get("qualified")
        and w.get("origin")
        and k not in excluded
        and w["origin"] not in excluded
        and 0 <= now - w["last_buy"].get(mint, float("-inf")) <= 120
    }
    return len({origin for origin in candidates.values() if origin not in candidates}) >= 2


def follow(client, asset, wallets, winners, now):
    mint = asset["symbol"]
    if not asset.get("excluded"):
        asset["excluded"] = identities(client, asset)
    if not asset["excluded"]:
        return False
    seen = set(asset.get("seen", []))
    rows = recent(client, asset["pool"])
    qualified_wallets = sorted(k for k, w in wallets.items() if w.get("qualified"))
    offset = asset.get("wallet_cursor", 0) % max(1, len(qualified_wallets))
    batch = (qualified_wallets[offset:] + qualified_wallets[:offset])[:4]
    asset["wallet_cursor"] = offset + len(batch)
    for wallet in batch:
        rows.extend(recent(client, wallet, 3))
    for r in sorted(rows, key=lambda x: (x["slot"], x["blockTime"])):
        sig = r["signature"]
        if sig in seen or not asset["creation"]["block_time"] <= r["blockTime"] <= now:
            continue
        tx = transaction(client, sig)
        t = swap(tx, asset)
        if t:
            w = wallets.get(t["wallet"])
            record(wallets, mint, t, asset["creation"]["block_time"], bool(w and w.get("qualified")))
        seen.add(sig)
    asset["seen"] = sorted(seen)
    for owner, w in wallets.items():
        qualified = profitable(w, winners)
        if qualified and not w.get("qualified"):
            w["qualified_since"] = now
        if not qualified:
            w["last_buy"].clear()
        w["qualified"] = qualified
        if w["qualified"] and not w.get("origin"):
            w["origin"] = funding_origin(client, owner, w.get("first_trade"))
    return confluence(wallets, mint, now, asset["excluded"])
