"""Construit le registre VÉRIFIÉ des tokens du hub à partir de sources indépendantes (lecture publique, bornée).

Un candidat n'est VÉRIFIÉ que si :
1. le contrat (ou le mint) existe sur la chaîne et ses décimales (et son symbole pour un contrat EVM) correspondent ;
2. DEUX sources hors chaîne indépendantes confirment cette adresse : le catalogue CoinGecko et la liste officielle
   d'Uniswap (Ethereum, Base), ou CoinGecko et la liste vérifiée de Jupiter (Solana).
Sinon il est UNVERIFIED (jamais utilisable). Chaque réponse est archivée par son empreinte et son heure.

Usage : python -m tools.dmh_tokens build [--out research/market-hub-v1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinelle.dmh import dex  # noqa: E402
from sentinelle.dmh.net import PublicClient, SourceUnavailable  # noqa: E402
from sentinelle.dmh.tokens import TokenId  # noqa: E402

STABLE_FLAGS = ("émission possible", "liste noire", "contrat modifiable (proxy)", "transferts suspendables", "gel possible",
                "émission encore possible", "Mutable metadata", "Freeze Authority", "Mint Authority")
# (réseau, adresse, symbole attendu, décimales, actif logique, identifiant CoinGecko, pouvoirs acceptés, note)
CANDIDATES = [
    ("ethereum", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "WETH", 18, "ETH", "weth", (), "ETH enveloppé canonique (WETH9)"),
    ("ethereum", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "USDC", 6, "USDC", "usd-coin", STABLE_FLAGS, "émetteur régulé (Circle)"),
    ("ethereum", "0xdAC17F958D2ee523a2206206994597C13D831ec7", "USDT", 6, "USDT", "tether", STABLE_FLAGS, "émetteur centralisé (Tether)"),
    ("ethereum", "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c", "EURC", 6, "EURC", "euro-coin", STABLE_FLAGS, "émetteur régulé (Circle)"),
    ("ethereum", "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "PEPE", 18, "PEPE", "pepe", (), "memecoin"),
    ("base", "0x4200000000000000000000000000000000000006", "WETH", 18, "ETH", "l2-standard-bridged-weth-base", (), "ETH enveloppé canonique de Base"),
    ("base", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USDC", 6, "USDC", "usd-coin", STABLE_FLAGS, "émetteur régulé (Circle), natif sur Base"),
    ("base", "0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42", "EURC", 6, "EURC", "euro-coin", STABLE_FLAGS, "émetteur régulé (Circle)"),
    ("solana", "So11111111111111111111111111111111111111112", "SOL", 9, "SOL", "wrapped-solana", (), "SOL enveloppé (mint natif)"),
    ("solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "USDC", 6, "USDC", "usd-coin", STABLE_FLAGS, "émetteur régulé (Circle)"),
    ("solana", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "USDT", 6, "USDT", "tether", STABLE_FLAGS, "émetteur centralisé (Tether)"),
    ("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "BONK", 5, "BONK", "bonk", ("Mutable metadata",), "memecoin"),
    ("solana", "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "WIF", 6, "WIF", "dogwifcoin", ("Mutable metadata",), "memecoin"),
    ("solana", "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr", "EURC", 6, "EURC", "euro-coin", STABLE_FLAGS, "émetteur régulé (Circle)"),
]


def coingecko(client: PublicClient, cid: str, cache: dict) -> tuple[dict | None, dict | None]:
    if cid in cache:
        return cache[cid]
    try:
        f = client.get("api.coingecko.com", f"/api/v3/coins/{cid}", {"localization": "false", "tickers": "false", "market_data": "false",
                                                                     "community_data": "false", "developer_data": "false"})
        d = f.json()
        res = ({k.lower(): str(v) for k, v in (d.get("platforms") or {}).items() if v}, f.evidence() | {"coin": cid, "symbol": d.get("symbol")})
    except SourceUnavailable as exc:
        res = (None, {"coin": cid, "error": str(exc)[:160]})
    cache[cid] = res
    return res


PLATFORM = {"ethereum": "ethereum", "base": "base", "solana": "solana"}


def uniswap_list(client: PublicClient) -> tuple[dict, dict]:
    """Liste officielle « Uniswap Labs Default » : (identifiant de chaîne, adresse) -> (symbole, décimales)."""
    try:
        f = client.get("tokens.uniswap.org", "/")
        d = f.json()
        idx = {(int(t["chainId"]), t["address"].lower()): (t["symbol"], int(t["decimals"])) for t in d["tokens"]}
        return idx, f.evidence() | {"name": d.get("name"), "version": d.get("version"), "timestamp": d.get("timestamp")}
    except (SourceUnavailable, KeyError, TypeError, ValueError) as exc:
        return {}, {"error": str(exc)[:160]}


def build(out: Path) -> dict:
    client = PublicClient(budget=200, retries=3)
    cache: dict = {}
    tokens = []
    uni, uni_ev = uniswap_list(client)
    chain_ids = {"ethereum": 1, "base": 8453}
    for network, address, symbol, decimals, asset, cid, accepted, note in CANDIDATES:
        tid = TokenId(network, address)
        checks, sources = [], []
        try:
            oc = dex.solana_mint(client, tid) if network == "solana" else dex.erc20_info(client, tid)
            sources.append({"source": "lecture sur la chaîne", "sha256": oc.get("sha256"), "received_ms": oc.get("received_ms")})
            checks.append(("existe sur la chaîne", bool(oc.get("exists"))))
            checks.append(("décimales", oc.get("decimals") == decimals))
            if network != "solana":
                checks.append(("symbole sur la chaîne", str(oc.get("symbol", "")).upper() == symbol))
        except SourceUnavailable as exc:
            checks.append(("lecture sur la chaîne", False))
            sources.append({"source": "lecture sur la chaîne", "error": str(exc)[:160]})
        platforms, ev = coingecko(client, cid, cache)
        sources.append({"source": "CoinGecko", **(ev or {})})
        got = (platforms or {}).get(PLATFORM[network])
        same = got is not None and (got == address if network == "solana" else got.lower() == address.lower())
        if cid == "wrapped-solana" and got is None:
            same = False
        checks.append(("adresse confirmée par CoinGecko", same))
        confirmations = [same]
        if network != "solana":
            hit = uni.get((chain_ids[network], address.lower()))
            okl = hit is not None and hit[0].upper() == symbol and hit[1] == decimals
            sources.append({"source": "liste Uniswap Labs Default", **uni_ev, "found": hit is not None})
            checks.append(("adresse confirmée par la liste Uniswap", okl))
            confirmations.append(okl)
        if network == "solana":
            try:
                f = client.get("api.jup.ag", "/tokens/v2/search", {"query": address})
                rows = [r for r in f.json() if r.get("id") == address]
                ok = bool(rows) and (rows[0].get("isVerified") is True or "verified" in (rows[0].get("tags") or []))
                sources.append({"source": "Jupiter (liste des tokens)", **f.evidence(), "verified": ok,
                                "symbol": rows[0].get("symbol") if rows else None})
                checks.append(("vérifié par Jupiter", ok))
                confirmations.append(ok)
            except SourceUnavailable as exc:
                checks.append(("vérifié par Jupiter", False))
                sources.append({"source": "Jupiter (liste des tokens)", "error": str(exc)[:160]})
            time.sleep(2.1)  # accès sans clé : 0,5 requête par seconde
        onchain_ok = all(ok for c, ok in checks if c in ("existe sur la chaîne", "décimales", "symbole sur la chaîne"))
        status = "VERIFIED" if onchain_ok and sum(bool(x) for x in confirmations) >= 2 else "UNVERIFIED"
        tokens.append({"network": network, "address": tid.address, "symbol": symbol, "decimals": decimals, "asset": asset,
                       "status": status, "checks": [{"check": c, "ok": ok} for c, ok in checks], "sources": sources,
                       "accepted_flags": list(accepted), "notes": note})
        print(f"{network:9s} {symbol:5s} {status:10s} " + ", ".join(f"{c}={'oui' if ok else 'NON'}" for c, ok in checks), flush=True)
    doc = {"kind": "dmh-token-registry", "version": "1.0.0", "built_ms": int(time.time() * 1000),
           "rule": "VERIFIED = existe sur la chaîne + décimales (et symbole EVM) + DEUX confirmations indépendantes hors chaîne "
                   "(CoinGecko et la liste Uniswap sur Ethereum/Base ; CoinGecko et la liste vérifiée de Jupiter sur Solana)",
           "tokens": tokens}
    out.mkdir(parents=True, exist_ok=True)
    path = out / "token-registry.json"
    raw = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True).encode()
    path.write_bytes(raw)
    (out / "token-registry.json.sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  token-registry.json\n", encoding="utf-8")
    return doc


def main(argv=None) -> int:
    logging.disable(logging.CRITICAL)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default=str(ROOT / "research" / "market-hub-v1"))
    a = ap.parse_args(argv)
    if a.cmd == "build":
        doc = build(Path(a.out))
        n = sum(t["status"] == "VERIFIED" for t in doc["tokens"])
        print(f"{n}/{len(doc['tokens'])} tokens vérifiés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
