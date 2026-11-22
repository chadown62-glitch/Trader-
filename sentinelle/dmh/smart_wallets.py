"""Bounded finalized-transaction samples. Eligibility uses observed, fully closed WSOL round trips.

Recency: closed cycles are weighted by age (half-life 24 h, nothing counts beyond 72 h); a wallet qualifies only on its
recent record. Anti-manipulation: a wallet that resold within seconds of its own buy INTO later buyers (followers who
bought after it) is flagged as a dumper and excluded for good, whatever its profits.

Attribution (who created the pool and deployed the mint, excluded from the tracked wallets): the fresh-pool scanner
records the creation signature; an established pool (GeckoTerminal, M58) only carries its creation date, so the creation
is reconstructed from the pool's FIRST on-chain transaction, read within a bounded history and verified as a Raydium
AMM v4 / CPMM initialisation of this pool and mint. Three cases are kept apart: a missing signature (reconstructed), an
identity that public data cannot establish (``asset["identity"]["status"] == "unknown"``: whale_tail abstains, not an
error) and an RPC failure (raised and reported, then retried after ``IDENTITY_RETRY_S``). Nothing is ever invented.
"""

import hashlib
import math
from collections import defaultdict

from .dex import _rpc
from .net import SourceUnavailable
from .solana_scanner import AMM4, CPMM, LAYOUTS, QUOTE_MINTS

from .tokens import b58decode

DOMAIN = "memecoin"
WSOL = "So11111111111111111111111111111111111111112"
HALF_LIFE_S = 24 * 3600  # a 24 h old cycle counts half as much as a fresh one
HORIZON_S = 72 * 3600  # older cycles no longer count toward eligibility
DUMP_WINDOW_S = 30  # a resale within 30 s of the wallet's own buy...
DUMP_SHARE = 0.5  # ...of at least half of the lot...
MAX_BUY_LOG = 16  # buys remembered per wallet and mint (to recognise followers)
HISTORY_PAGE = 1000  # signatures per getSignaturesForAddress call (the RPC maximum)
MAX_HISTORY_PAGES = 3  # at most 3,000 signatures read per address to reach its first transaction
IDENTITY_RETRY_S = 900  # after an RPC failure (reported once), the attribution is retried 15 minutes later
CONFLUENCE_WINDOW_S = 120  # independent qualified buys of the same mint within the last 2 minutes
FIRST_TRANSACTION = "rpc:first-transaction"


def recency_weight(age_s: float) -> float:
    """Exponential decay with a 24 h half-life; zero beyond 72 h or for an unknown / future date."""
    if age_s is None or age_s < 0 or age_s > HORIZON_S:
        return 0.0
    return math.pow(0.5, age_s / HALF_LIFE_S)


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


def first_signature(client, address):
    """Oldest signature row of ``address`` when its whole history fits in ``MAX_HISTORY_PAGES`` pages; ``None`` when the
    address has no transaction; ``False`` when its history is longer (the first transaction is out of reach)."""
    before, last = None, None
    for _ in range(MAX_HISTORY_PAGES):
        options = {"limit": HISTORY_PAGE, "commitment": "finalized"}
        if before:
            options["before"] = before
        rows, _ = _rpc(client, "solana", "getSignaturesForAddress", [address, options])
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("signature"), str) for r in rows):
            raise ValueError("historique de signatures malformé")
        if rows:
            last = rows[-1]  # newest first: the last row of a page is its oldest
        if len(rows) < HISTORY_PAGE:
            return last
        before = last["signature"]
    return False


def _pool_initialisation(tx, signature, pool, mint, quote):
    """Program of a top-level Raydium initialisation of ``pool`` for ``mint``/``quote`` in ``tx``, or ``None``."""
    if not tx or tx["transaction"]["signatures"][0] != signature:
        return None
    for ix in tx["transaction"]["message"]["instructions"]:
        program = ix.get("programId")
        if program not in LAYOUTS:
            continue
        prefix, length, pool_i, a_i, b_i = LAYOUTS[program]
        encoded, accounts = ix.get("data"), ix.get("accounts") or []
        if not isinstance(encoded, str) or len(encoded) > 50 or len(accounts) <= max(pool_i, a_i, b_i):
            continue
        data = b58decode(encoded)
        if len(data) == length and data.startswith(prefix) and accounts[pool_i] == pool and quote in QUOTE_MINTS \
                and {accounts[a_i], accounts[b_i]} == {mint, quote}:
            return program
    return None


def creation_record(client, asset):
    """``(creation, tx, None)`` with a verified creation of the pool (``tx`` when already fetched), or
    ``(None, None, reason)`` when public data cannot establish it. The scanner's record is used when it carries a
    signature; otherwise the creation is reconstructed from the pool's first transaction. RPC failures are raised,
    never turned into an unknown identity."""
    creation = asset.get("creation") or {}
    if creation.get("signature") and creation.get("program") in LAYOUTS:
        return creation, None, None
    row = first_signature(client, asset["pool"])
    if row is False:
        return None, None, (f"historique du pool de plus de {MAX_HISTORY_PAGES * HISTORY_PAGE} transactions : "
                            "première transaction hors de portée")
    if row is None:
        return None, None, "aucune transaction connue pour ce pool"
    tx = transaction(client, row["signature"])
    program = _pool_initialisation(tx, row["signature"], asset["pool"], asset["symbol"], asset.get("quote_mint"))
    if program is None:
        return None, None, "première transaction du pool sans initialisation Raydium (AMM v4 ou CPMM) vérifiable"
    return {"program": program, "signature": row["signature"], "slot": tx.get("slot"), "block_time": tx.get("blockTime"),
            "source": FIRST_TRANSACTION}, tx, None


def _mint_deployer(tx, mint):
    """Signers and mint authority of a witnessed ``initializeMint`` of ``mint`` in ``tx``, or ``None``."""
    if not tx:
        return None
    msg = tx["transaction"]["message"]
    for ix in msg["instructions"]:
        parsed = ix.get("parsed") or {}
        info = parsed.get("info", {})
        if ix.get("program") == "spl-token" and parsed.get("type") in ("initializeMint", "initializeMint2") and info.get("mint") == mint:
            return {k["pubkey"] for k in msg["accountKeys"] if k["signer"]} | ({info["mintAuthority"]} if info.get("mintAuthority") else set())
    return None


def identities(client, asset):
    """Wallets to exclude (pool creator and fee payer, mint deployer and authority), or ``[]`` with
    ``asset["identity"]["status"] == "unknown"`` when they cannot be established. Unknown deployer => no attribution."""
    creation, tx, reason = creation_record(client, asset)
    if creation and tx is None:
        tx = transaction(client, creation["signature"])
    if creation and not tx:
        reason = "transaction de création en échec ou introuvable"
    if not tx:
        asset["identity"] = {"status": "unknown", "reason": reason}
        return []
    signers = {k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"] if k["signer"]}
    # Include the AMM creator explicitly, even if an instruction uses a separate fee payer.
    for ix in tx["transaction"]["message"]["instructions"]:
        if ix.get("programId") == creation["program"] and asset["pool"] in ix.get("accounts", []):
            signers.add(ix["accounts"][0 if ix["programId"] == CPMM else 17])
    deployer = None
    if creation.get("source") != FIRST_TRANSACTION:  # a fresh pool: the mint was initialised among its latest transactions
        for row in recent(client, asset["symbol"], 8):
            deployer = _mint_deployer(transaction(client, row["signature"]), asset["symbol"])
            if deployer:
                break
    if not deployer:  # an established token: its initialisation is its first transaction
        row = first_signature(client, asset["symbol"])  # None (no history) or False (beyond the bound): unknown
        deployer = _mint_deployer(transaction(client, row["signature"]), asset["symbol"]) if row else None
    if not deployer:
        asset["identity"] = {"status": "unknown", "reason": "initialisation du mint non observée : déployeur inconnu"}
        return []
    excluded = sorted(signers | deployer)
    asset["identity"] = {"status": "verified", "source": creation.get("source", "scanner"), "program": creation["program"],
                         "creation_signature": creation["signature"], "excluded": len(excluded)}
    return excluded


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


def _followers(wallets, mint, wallet, after, until):
    """Other memecoin wallets that bought ``mint`` strictly after ``after`` and no later than ``until``."""
    return sorted(
        k
        for k, w in wallets.items()
        if k != wallet and w.get("asset_domain") == DOMAIN and any(after < t <= until for t in w.get("buys", {}).get(mint, ()))
    )


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
            lot["opened_ts"] = trade["ts"]
        lot["qty"] += qty
        lot["spent"] -= sol
        lot["last_buy_ts"] = trade["ts"]
        buys = w.setdefault("buys", {}).setdefault(mint, [])
        buys.append(trade["ts"])
        del buys[:-MAX_BUY_LOG]
        if eligible and trade["ts"] > w.get("qualified_since", trade["ts"]):
            w["last_buy"][mint] = trade["ts"]
    elif 0 < -qty <= lot["qty"] + 1e-10:
        bought = lot.get("last_buy_ts")
        if bought is not None and 0 <= trade["ts"] - bought <= DUMP_WINDOW_S and -qty >= DUMP_SHARE * lot["qty"] - 1e-10:
            followers = _followers(wallets, mint, trade["wallet"], bought, trade["ts"])
            if followers:  # resold into the wallets that followed it: manipulation, excluded for good
                w["dumper"] = True
                w.setdefault("dumps", []).append({"mint": mint, "bought": bought, "sold": trade["ts"], "followers": followers[:8]})
        lot["qty"] += qty
        lot["received"] += sol
        if abs(lot["qty"]) <= 1e-10 and lot["spent"] > 0:
            w["cycles"].append({"mint": mint, "pnl_sol": lot["received"] - lot["spent"], "early": lot["early"], "ts": trade["ts"],
                                "opened_ts": lot.get("opened_ts")})
            del w["lots"][mint]
    else:
        w["lots"].pop(mint, None)  # incomplete history cannot establish profitability


def recent_record(w, now):
    """Recency-weighted record over the last 72 h: weighted win rate, weighted P&L (SOL) and weighted cycle count.
    Cycles without a date (recorded before this rule) count as old: they no longer qualify a wallet."""
    rows = [(c, recency_weight(now - c["ts"]) if now is not None and c.get("ts") is not None else 0.0) for c in w.get("cycles", [])]
    weight = sum(k for _, k in rows)
    return {
        "weight": weight,
        "recent_cycles": sum(1 for _, k in rows if k > 0),
        "win_rate": sum(k for c, k in rows if c["pnl_sol"] > 0) / weight if weight else None,
        "pnl_sol": sum(k * c["pnl_sol"] for c, k in rows),
    }


def profitable(w, winners, now=None):
    """Eligibility. Without ``now`` (legacy call): the whole record. With ``now``: only the last 72 h, weighted by age.
    A flagged dumper or a wallet of another asset domain is never eligible."""
    cycles = w["cycles"]
    w["win_rate"] = sum(c["pnl_sol"] > 0 for c in cycles) / len(cycles) if cycles else None
    if w.get("dumper") or w.get("asset_domain", DOMAIN) != DOMAIN:
        return False
    base = (
        len(cycles) >= 2
        and w["win_rate"] > 0.5
        and sum(c["pnl_sol"] for c in cycles) > 0
        and any(c["early"] and c["pnl_sol"] > 0 and c["mint"] in winners for c in cycles)
    )
    if not base or now is None:
        return base
    rec = recent_record(w, now)
    w["recent"] = rec
    return rec["recent_cycles"] >= 2 and rec["win_rate"] > 0.5 and rec["pnl_sol"] > 0 and any(
        c["early"] and c["pnl_sol"] > 0 and c["mint"] in winners and recency_weight(now - c.get("ts", float("-inf"))) > 0 for c in cycles
    )


def ranking(wallets, now):
    """Qualified, non-dumper memecoin wallets ordered by recency-weighted P&L (best first)."""
    rows = [(k, recent_record(w, now)) for k, w in wallets.items() if w.get("qualified") and not w.get("dumper")
            and w.get("asset_domain") == DOMAIN]
    return [k for k, r in sorted(rows, key=lambda kr: (-kr[1]["pnl_sol"], kr[0]))]


def confluence(wallets, mint, now, excluded):
    candidates = {
        k: w["origin"]
        for k, w in wallets.items()
        if w.get("qualified")
        and not w.get("dumper")
        and w.get("asset_domain") == DOMAIN
        and w.get("origin")
        and k not in excluded
        and w["origin"] not in excluded
        and 0 <= now - w["last_buy"].get(mint, float("-inf")) <= CONFLUENCE_WINDOW_S
    }
    return len({origin for origin in candidates.values() if origin not in candidates}) >= 2


def follow(client, asset, wallets, winners, now, observed=None):
    """Whether independent qualified wallets bought ``mint`` together; each tracked-wallet swap read on this call is also
    appended to ``observed`` when given (the session journals them, pseudonymized). Nothing else changes."""
    mint = asset["symbol"]
    if not asset.get("excluded"):
        identity = asset.get("identity") or {}
        if identity.get("status") == "unknown":
            return False  # abstain: public data cannot attribute this pool (cached, not an error)
        if identity.get("status") == "retry" and now < identity.get("retry_at", 0):
            return False  # the RPC failure was reported; the attribution is retried later
        try:
            asset["excluded"] = identities(client, asset)
        except (SourceUnavailable, ValueError, KeyError, TypeError) as exc:
            asset["identity"] = {"status": "retry", "reason": str(exc)[:200], "retry_at": now + IDENTITY_RETRY_S}
            raise  # a real outage or malformed data is reported by the caller
    if not asset["excluded"]:
        return False
    seen = set(asset.get("seen", []))
    rows = recent(client, asset["pool"])
    qualified_wallets = ranking(wallets, now)  # best recent performers are watched first
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
            if observed is not None:
                observed.append(t)
        seen.add(sig)
    asset["seen"] = sorted(seen)
    for owner, w in wallets.items():
        qualified = profitable(w, winners, now)
        if qualified and not w.get("qualified"):
            w["qualified_since"] = now
        if not qualified:
            w["last_buy"].clear()
        w["qualified"] = qualified
        if w["qualified"] and not w.get("origin"):
            w["origin"] = funding_origin(client, owner, w.get("first_trade"))
    return confluence(wallets, mint, now, asset["excluded"])
