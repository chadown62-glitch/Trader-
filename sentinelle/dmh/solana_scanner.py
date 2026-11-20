"""On-demand, bounded sample of finalized Raydium pool initializations; observation only."""
import hashlib
import math
import threading
import time

from ..ai.features_memecoin import extract_features
from .dex import _rpc
from .net import HOSTS, PublicClient, SourceUnavailable
from .tokenrisk import assess_candidate
from .tokens import TokenId, b58decode, load_registry

# Mainnet layouts from raydium-io/raydium-cp-swap Initialize and raydium-io/raydium-amm Initialize2.
CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
AMM4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
# Discriminator, instruction byte length, pool account, first mint, second mint.
LAYOUTS = {CPMM: (hashlib.sha256(b"global:initialize").digest()[:8], 32, 3, 4, 5),
           AMM4: (bytes([1]), 26, 4, 8, 9)}
QUOTE_MINTS = {"So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}
WINDOW_S = 900
SIGNATURE_LIMIT = 8
CANDIDATE_LIMIT = 4


def _creations(tx, program, signature, now):
    if not isinstance(tx, dict) or not isinstance(tx.get("meta"), dict):
        raise ValueError("transaction indisponible")
    if tx["meta"]["err"] is not None:
        return
    ts = tx["blockTime"]
    if type(ts) is not int or not 0 <= now - ts <= WINDOW_S:
        return
    transaction = tx["transaction"]
    if transaction["signatures"][0] != signature:
        raise ValueError("signature de transaction incohérente")
    prefix, length, pool_i, a_i, b_i = LAYOUTS[program]
    # Top-level instructions only: an unsuccessful inner invocation can be caught by its caller.
    for ix in transaction["message"]["instructions"]:
        if ix.get("programId") != program:
            continue
        encoded = ix.get("data")
        if not isinstance(encoded, str) or len(encoded) > 50:
            continue
        data = b58decode(encoded)
        if len(data) != length or not data.startswith(prefix):
            continue
        accounts = ix["accounts"]
        pool, a, b = (TokenId("solana", accounts[i]).address for i in (pool_i, a_i, b_i))
        if (a in QUOTE_MINTS) != (b in QUOTE_MINTS):
            yield pool, (b if a in QUOTE_MINTS else a), (a if a in QUOTE_MINTS else b)


def _observe(client, db, bus, mint, quote, pool, creation, minimum):
    key = f"scanner:memecoin:solana:{mint}"
    if db.get_meta(key) is not None:
        return None
    pair, pair_hash, market_error = {}, None, None
    try:
        fetched = client.get("api.dexscreener.com", f"/token-pairs/v1/solana/{mint}")
        pair_hash = fetched.sha256
        rows = fetched.json()
        if not isinstance(rows, list):
            raise ValueError("réponse DexScreener malformée")
        pair = next((p for p in rows if isinstance(p, dict) and p.get("chainId") == "solana"
                     and p.get("dexId") == "raydium" and p.get("pairAddress") == pool
                     and (p.get("baseToken") or {}).get("address") == mint
                     and (p.get("quoteToken") or {}).get("address") == quote), {})
        if not pair:
            raise ValueError("paire DexScreener exacte indisponible")
    except (SourceUnavailable, ValueError, AttributeError) as exc:
        market_error = str(exc)
    observed_ms = int(client.clock() * 1000)
    features = extract_features(pair, observed_ms=observed_ms, created_ms=creation["block_time"] * 1000)
    guardian = assess_candidate(client, TokenId("solana", mint), features["liquidity_usd"], min_liquidity_usd=minimum)
    kind = "observation_accepted" if guardian["allowed"] else "observation_refused"
    reasons = [r["message"] for r in guardian["vetoes"]]
    if market_error:
        reasons.append(market_error)
    message = "Observation acceptée" if guardian["allowed"] else "Observation refusée : " + " ; ".join(reasons)
    event = {"asset_domain": "memecoin", "agent": "solana_scanner", "symbol": mint, "kind": kind, "message": message,
             "pool": pool, "quote_mint": quote, "creation": creation, "observed_ms": observed_ms,
             "features": features, "guardian": guardian, "dex_sha256": pair_hash}
    with db.transaction():
        if db.get_meta(key) is not None:
            return None
        decision_id = db.decision(None, "solana_scanner", mint, kind, message, event, ts=observed_ms / 1000)
        db.set_meta(key, str(decision_id))
        bus.publish("decision", {**event, "decision_id": decision_id})
    return event


def scan_once(client, db, bus, *, min_liquidity_usd=25000, registry=None):
    """Sample latest signatures, without a cursor or a claim of exhaustive coverage."""
    if type(min_liquidity_usd) not in (float, int) or not 0 < min_liquidity_usd <= 1e300:
        raise ValueError("minimum de liquidité USD invalide")
    registry = load_registry() if registry is None else registry
    decisions, errors = [], []
    now = client.clock()
    for program in LAYOUTS:
        try:
            rows, _ = _rpc(client, "solana", "getSignaturesForAddress",
                           [program, {"limit": SIGNATURE_LIMIT, "commitment": "finalized"}])
            if not isinstance(rows, list):
                raise ValueError("liste de signatures indisponible")
        except (SourceUnavailable, ValueError) as exc:
            errors.append({"program": program, "reason": str(exc)})
            continue
        for row in rows[:SIGNATURE_LIMIT]:
            try:
                if row["err"] is not None or type(row["blockTime"]) is not int or not 0 <= now - row["blockTime"] <= WINDOW_S:
                    continue
                sig = row["signature"]
                if not isinstance(sig, str) or len(sig) > 88 or len(b58decode(sig)) != 64:
                    raise ValueError("signature RPC malformée")
                tx, evidence = _rpc(client, "solana", "getTransaction",
                                    [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}])
                for pool, mint, quote in _creations(tx, program, sig, now):
                    if registry.get(TokenId("solana", mint)) is not None:
                        continue  # catalogued assets belong to the existing hub domain
                    creation = {"program": program, "signature": sig, "slot": tx["slot"], "block_time": tx["blockTime"],
                                "transaction_sha256": evidence.sha256}
                    event = _observe(client, db, bus, mint, quote, pool, creation, min_liquidity_usd)
                    if event:
                        decisions.append(event)
                        if len(decisions) == CANDIDATE_LIMIT:
                            return {"asset_domain": "memecoin", "decisions": decisions, "errors": errors, "limit_reached": True}
            except (SourceUnavailable, ValueError, TypeError, KeyError, IndexError) as exc:
                errors.append({"program": program, "reason": str(exc)})
    return {"asset_domain": "memecoin", "decisions": decisions, "errors": errors, "limit_reached": False}


class RadarClient(PublicClient):
    """Allow the fixed public boost listing only on this read-only radar client."""

    @staticmethod
    def check(host, path, params=None, rpc_method=None):
        if host == "api.dexscreener.com" and path == "/token-boosts/latest/v1" and not params and rpc_method is None:
            return HOSTS[host]
        return PublicClient.check(host, path, params, rpc_method)


def _number(value):
    if type(value) in (float, int) and abs(value) <= 1e300:
        return float(value)
    return None


def _radar_rows(client, assets, wallets, now):
    from .memecoin_strategies import evaluate
    from .smart_wallets import confluence

    host = "api.dexscreener.com"
    addresses = []
    errors = []
    registry = load_registry()
    try:
        boosts = client.get(host, "/token-boosts/latest/v1").json()
        if not isinstance(boosts, list):
            raise ValueError("liste de boosts invalide")
        for item in boosts:
            if isinstance(item, dict) and item.get("chainId") == "solana":
                addresses.append(item.get("tokenAddress"))
    except (SourceUnavailable, ValueError) as exc:
        errors.append(str(exc))
    # Include pools already discovered on-chain even when boost discovery is offline.
    candidates = []
    seen = set()
    for address in [*list(assets)[-2:], *addresses]:
        if not isinstance(address, str) or address in seen:
            continue
        try:
            mint = TokenId("solana", address)
        except ValueError:
            continue
        if registry.get(mint) or address in QUOTE_MINTS:
            continue
        seen.add(address)
        if len(seen) > 4:
            break
        try:
            pairs = client.get(host, f"/token-pairs/v1/solana/{address}").json()
            if not isinstance(pairs, list):
                raise ValueError("réponse paires invalide")
            best = None
            for pair in pairs:
                if not isinstance(pair, dict) or pair.get("chainId") != "solana" or (pair.get("baseToken") or {}).get("address") != address:
                    continue
                depth = _number((pair.get("liquidity") or {}).get("usd"))
                if depth and depth > 0 and (best is None or depth > best[0]):
                    best = depth, pair
            if best is None:
                continue
            depth, pair = best
            point = extract_features(pair, observed_ms=now * 1000, created_ms=pair.get("pairCreatedAt"))
            stored = assets.get(address, {})
            asset = stored if stored.get("pool") == pair.get("pairAddress") else {}
            price = float(pair.get("priceUsd") or 0)
            # Missing price/age cannot authorize any strategy, but risk remains reportable.
            whale = bool(asset.get("excluded")) and confluence(wallets, address, now, asset["excluded"])
            can_evaluate = (point["pool_age_minutes"] is not None and price > 0 and math.isfinite(price)
                            and pair.get("dexId") == "raydium" and (pair.get("quoteToken") or {}).get("address") in QUOTE_MINTS)
            if can_evaluate:
                signals, guard = evaluate(client, {**asset, "symbol": address}, {**point, "price": price}, whale)
            else:
                signals, guard = [], assess_candidate(client, mint, depth)
            rug = guard["evidence"].get("rugcheck", {})
            score = _number(rug.get("score"))
            risk = "veto" if rug.get("flags") else "unknown" if score is None else "veto" if not guard["allowed"] else "passed"
            if not rug:
                errors.append(f"{address} : RugCheck indisponible")
            vol = pair.get("volume") or {}
            candidates.append({"mint": address, "symbol": str(pair["baseToken"].get("symbol") or address)[:32],
                               "liquidity_usd": depth, "volume_5m": _number(vol.get("m5")), "volume_1h": _number(vol.get("h1")),
                               "change_5m_pct": _number((pair.get("priceChange") or {}).get("m5")), "rug_score": score,
                               "risk": risk, "eligible_strategies": signals if risk == "passed" else [], "source": "live"})
        except (SourceUnavailable, KeyError, TypeError, AttributeError, ValueError, OverflowError) as exc:
            errors.append(str(exc))
    # Ordinal quality tier, not a probability. Liquidity breaks ties within each tier.
    tiers = {"passed": 2, "unknown": 1, "veto": 0}
    for row in candidates:
        row["quality_tier"] = tiers[row["risk"]]
    candidates.sort(key=lambda row: (-row["quality_tier"], -row["liquidity_usd"], row["mint"]))
    if not candidates and errors:
        candidates.append({"mint": None, "symbol": "DÉMO", "quality_tier": None, "liquidity_usd": 30000, "volume_5m": 500,
                           "volume_1h": 6000, "change_5m_pct": 0, "rug_score": None, "risk": "unknown",
                           "eligible_strategies": ["momentum"], "source": "fallback"})
    return {"asset_domain": "memecoin", "observed_at": now, "tokens": candidates, "errors": errors}


class LiveRadar:
    def __init__(self, client_factory=None, monotonic=time.monotonic):
        self.client_factory = client_factory or (lambda: RadarClient(timeout=2, retries=0, budget=48))
        self.monotonic = monotonic
        self.lock = threading.Lock()
        self.snapshot = None
        self.expires = 0

    def get_live_radar_snapshot(self, assets=None, wallets=None):
        with self.lock:
            if self.snapshot is None or self.monotonic() >= self.expires:
                client = self.client_factory()
                self.snapshot = _radar_rows(client, assets or {}, wallets or {}, int(client.clock()))
                self.expires = self.monotonic() + 60
            return self.snapshot
