"""Module A — détection des nouveaux pools Solana et collecte BORNÉE de données publiques (aucune surveillance permanente).

Sources (lectures publiques, client verrouillé du hub) : GeckoTerminal (nouveaux pools, bougies), DexScreener (second avis
sur la liquidité et le prix), RPC Solana (autorités du mint, concentration des détenteurs), Rugcheck (signalements),
Jupiter (devis sans adresse de portefeuille : un aller-retour d'achat puis de revente mesure la revendabilité effective).

Chaque réponse garde son horodatage et son empreinte. Une source qui ne répond pas laisse la donnée ABSENTE (jamais
inventée) ; le veto en tire une abstention. La qualité des sources (disponibilité, latence, fraîcheur, complétude,
accord entre sources) est mesurée sur la collecte elle-même.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..dmh import dex
from ..dmh.net import PublicClient, SourceUnavailable
from ..dmh.tokens import TokenError, TokenId
from . import SOL_MINT

GT = "api.geckoterminal.com"
DS = "api.dexscreener.com"
PROBE_LAMPORTS = 50_000_000  # 0,05 SOL : taille d'essai de l'aller-retour (ordre de grandeur d'un ordre de 10 €)


def _ms(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


class Stats:
    """Disponibilité et latence par source, mesurées sur la collecte."""

    def __init__(self):
        self.calls: dict[str, list] = {}

    def run(self, source: str, fn: Callable):
        t0 = time.monotonic()
        try:
            out = fn()
        except (SourceUnavailable, KeyError, TypeError, ValueError, TokenError) as exc:
            self.calls.setdefault(source, []).append((False, (time.monotonic() - t0) * 1000, f"{type(exc).__name__}: {str(exc)[:90]}"))
            return None
        self.calls.setdefault(source, []).append((True, (time.monotonic() - t0) * 1000, None))
        return out

    def summary(self) -> dict:
        out = {}
        for s, rows in sorted(self.calls.items()):
            lat = sorted(r[1] for r in rows if r[0])
            out[s] = {"requests": len(rows), "ok": sum(r[0] for r in rows), "availability": sum(r[0] for r in rows) / len(rows),
                      "latency_ms_p50": statistics.median(lat) if lat else None,
                      "latency_ms_p90": lat[min(len(lat) - 1, int(0.9 * len(lat)))] if lat else None,
                      "errors": sorted({r[2] for r in rows if r[2]})}
        return out


def parse_pool(item: dict, observed_ms: int) -> dict | None:
    a, rel = item.get("attributes") or {}, item.get("relationships") or {}
    base = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
    quote = ((rel.get("quote_token") or {}).get("data") or {}).get("id", "")
    if not base.startswith("solana_") or not a.get("address"):
        return None
    tx = a.get("transactions") or {}
    return {"kind": "pool", "observed_ms": observed_ms, "pool": a["address"], "name": a.get("name"), "mint": base[7:], "quote_mint": quote[7:],
            "dex": ((rel.get("dex") or {}).get("data") or {}).get("id"), "created_ms": _ms(a.get("pool_created_at")),
            "reserve_usd": _f(a.get("reserve_in_usd")), "price_usd": _f(a.get("base_token_price_usd")), "fdv_usd": _f(a.get("fdv_usd")),
            "price_native": _f(a.get("base_token_price_native_currency")), "quote_price_usd": _f(a.get("quote_token_price_usd")),
            "volume_usd": {k: _f(v) for k, v in (a.get("volume_usd") or {}).items()},
            "price_change_pct": {k: _f(v) for k, v in (a.get("price_change_percentage") or {}).items()},
            "tx": {w: {k: int(v or 0) for k, v in (tx.get(w) or {}).items()} for w in ("m5", "m15", "m30", "h1", "h24")}}


def new_pools(client: PublicClient, stats: Stats, pages: int) -> list[dict]:
    out, seen = [], set()
    for page in range(1, pages + 1):
        f = stats.run("geckoterminal:new_pools", lambda: client.get(GT, "/api/v2/networks/solana/new_pools", {"page": page}))
        if f is None:
            continue
        for item in f.json().get("data") or []:
            p = parse_pool(item, f.received_ms)
            if p and p["pool"] not in seen:
                seen.add(p["pool"])
                out.append(p | {"sha256": f.sha256})
    return out


def ohlcv(client: PublicClient, stats: Stats, pool: str, *, limit: int = 1000, wait_s: float = 65.0, sleep=time.sleep,
          before_s: int | None = None) -> dict | None:
    """Bougies minute (au plus ``limit``) ; ``before_s`` borne la fin de la fenêtre (secondes UTC) pour relire un passé."""
    params = {"aggregate": 1, "limit": limit, "currency": "usd"} | ({"before_timestamp": int(before_s)} if before_s else {})

    def get():
        return client.get(GT, f"/api/v2/networks/solana/pools/{pool}/ohlcv/minute", params)
    f = stats.run("geckoterminal:ohlcv", get)
    last = (stats.calls.get("geckoterminal:ohlcv") or [(True, 0, None)])[-1]
    if f is None and last[2] and ("429" in last[2] or "coupe-circuit" in last[2]) and wait_s > 0:
        sleep(wait_s)  # limite de débit de la source : une pause, puis UN nouvel essai (jamais de forçage)
        f = stats.run("geckoterminal:ohlcv", get)
    if f is None:
        return None
    rows = ((f.json().get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    candles = sorted([int(r[0]) * 1000, *(float(x) for x in r[1:6])] for r in rows if len(r) >= 6)
    return {"kind": "ohlcv", "pool": pool, "observed_ms": f.received_ms, "timeframe": "minute", "candles": candles, "sha256": f.sha256}


def security(client: PublicClient, stats: Stats, mint: str) -> dict:
    """Tout ce que le veto lit, à l'instant de la collecte. Une lecture manquante reste ``None`` (abstention en aval)."""
    rec = {"kind": "security", "mint": mint, "observed_ms": int(time.time() * 1000)}
    try:
        token = TokenId("solana", mint)
    except TokenError:
        return rec | {"invalid_mint": True}
    sol = TokenId("solana", SOL_MINT)
    info = stats.run("solana:mint", lambda: dex.solana_mint(client, token))
    rec["mint_info"] = info
    rec["rugcheck"] = stats.run("rugcheck", lambda: _rugcheck(client, mint))  # détenteurs, créateur, verrouillage, signalements
    rec["dexscreener"] = stats.run("dexscreener", lambda: _dexscreener(client, mint))
    buy = stats.run("jupiter:buy", lambda: dex.jupiter_quote(client, sol, token, PROBE_LAMPORTS))
    sell = None
    if buy is not None:
        sell = stats.run("jupiter:sell", lambda: dex.jupiter_quote(client, token, sol, buy.amount_out))
    rec["round_trip"] = None if buy is None else {
        "lamports_in": PROBE_LAMPORTS, "tokens": buy.amount_out, "lamports_back": sell.amount_out if sell else None,
        "loss": (1 - sell.amount_out / PROBE_LAMPORTS) if sell else None, "buy_impact_bps": buy.price_impact_bps,
        "sell_impact_bps": sell.price_impact_bps if sell else None, "buy_route": [r.get("label") for r in buy.route],
        "sell_route": [r.get("label") for r in sell.route] if sell else None, "sell_unavailable": sell is None}
    return rec


POOL_TYPES = ("AMM", "LOCKER")  # comptes connus de pools et de verrous : exclus de la concentration des détenteurs


def _rugcheck(client: PublicClient, mint: str) -> dict:
    """Rapport complet : signalements, détenteurs (hors pools et verrous connus), créateur, verrouillage de la liquidité.
    (La méthode RPC publique des plus gros comptes est limitée en débit : Rugcheck sert de source des détenteurs.)"""
    f = client.get("api.rugcheck.xyz", f"/v1/tokens/{mint}/report")
    d = f.json()
    known = d.get("knownAccounts") or {}
    pools = {k for k, v in known.items() if (v or {}).get("type") in POOL_TYPES}
    holders = [h for h in d.get("topHolders") or [] if h.get("address") not in pools and h.get("owner") not in pools]
    pct = sorted((float(h.get("pct") or 0) / 100 for h in holders), reverse=True)
    locked = [float((m.get("lp") or {}).get("lpLockedPct") or 0) for m in d.get("markets") or []]
    return {"score": d.get("score_normalised", d.get("score")), "rugged": bool(d.get("rugged")),
            "risks": [{"name": str(r.get("name")), "level": str(r.get("level")).lower()} for r in d.get("risks") or []],
            "top1_share": pct[0] if pct else None, "top10_share": sum(pct[:10]) if pct else None,
            "insiders": sum(1 for h in holders if h.get("insider")), "holders": d.get("totalHolders"),
            "creator": d.get("creator"), "creator_share": (float(d.get("creatorBalance") or 0) / float((d.get("token") or {}).get("supply") or 1))
            if (d.get("token") or {}).get("supply") else None, "lp_locked_pct_max": max(locked) if locked else None,
            "market_liquidity_usd": _f(d.get("totalMarketLiquidity")), "launchpad": (d.get("launchpad") or {}).get("platform"),
            "transfer_fee_pct": _f((d.get("transferFee") or {}).get("pct")), "mint_authority": d.get("mintAuthority"),
            "freeze_authority": d.get("freezeAuthority"), "insider_networks": len(d.get("insiderNetworks") or []), "sha256": f.sha256}


def _dexscreener(client: PublicClient, mint: str) -> dict:
    f = client.get(DS, f"/token-pairs/v1/solana/{mint}")
    pairs = f.json() or []
    best = max(pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")) or 0.0) if pairs else None
    return {"pairs": len(pairs), "liquidity_usd": _f((best.get("liquidity") or {}).get("usd")) if best else None,
            "price_usd": _f(best.get("priceUsd")) if best else None, "pair": best.get("pairAddress") if best else None, "sha256": f.sha256}


def sol_price(client: PublicClient, stats: Stats) -> dict | None:
    """Prix du SOL en dollars (Jupiter), pour convertir les frais du réseau et la rente."""
    f = stats.run("jupiter:price", lambda: client.get("api.jup.ag", "/price/v3", {"ids": SOL_MINT}))
    if f is None:
        return None
    px = _f((f.json().get(SOL_MINT) or {}).get("usdPrice"))
    return {"kind": "sol_price", "usd": px, "observed_ms": f.received_ms, "sha256": f.sha256} if px else None


def write_capture(path: Path, records: list[dict]) -> str:
    payload = gzip.compress("".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in records).encode("utf-8"), mtime=0)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def read_capture(path: Path) -> list[dict]:
    payload = path.read_bytes()
    side = path.with_name(path.name + ".sha256")
    if side.exists() and hashlib.sha256(payload).hexdigest() != side.read_text().split()[0]:
        raise ValueError("Capture altérée")
    return [json.loads(ln) for ln in gzip.decompress(payload).decode("utf-8").splitlines()]


def collect(client: PublicClient, *, pages: int = 5, max_security: int = 60, clock=time.time) -> list[dict]:
    """Collecte bornée : ``pages`` pages de nouveaux pools, leurs bougies, et les contrôles de sécurité des ``max_security``
    premiers. Renvoie les enregistrements (pools, bougies, sécurité, qualité des sources)."""
    stats = Stats()
    started = int(clock() * 1000)
    pools = new_pools(client, stats, pages)
    records: list[dict] = list(pools)
    sp = sol_price(client, stats)
    if sp:
        records.append(sp)
    for p in pools:
        c = ohlcv(client, stats, p["pool"])
        if c:
            records.append(c)
    for p in pools[:max_security]:
        records.append(security(client, stats, p["mint"]))
    records.append({"kind": "collection", "started_ms": started, "finished_ms": int(clock() * 1000), "pages": pages,
                    "max_security": max_security, "pools": len(pools), "requests": client.used, "sources": stats.summary(),
                    "quality": quality(records)})
    return records


FOLLOW_WINDOW_MIN = 960  # 16 h de bougies minute après la décision (la source en sert au plus 1000 par requête)


def follow(client: PublicClient, previous: list[dict], *, clock=time.time) -> list[dict]:
    """Seconde lecture des mêmes pools, plus tard : l'issue observée APRÈS la décision du veto (contrôle causal du veto).
    La fenêtre lue va de la création du pool à 16 h après la collecte, même si la relecture a lieu des jours plus tard."""
    stats = Stats()
    started = int(clock() * 1000)
    out = [r for r in [sol_price(client, stats)] if r]
    for p in (r for r in previous if r["kind"] == "pool"):
        end_s = min(int(clock()), p["observed_ms"] // 1000 + FOLLOW_WINDOW_MIN * 60)
        c = ohlcv(client, stats, p["pool"], before_s=end_s)
        if c:
            out.append(c | {"follow": True})
    out.append({"kind": "collection", "follow": True, "started_ms": started, "finished_ms": int(clock() * 1000), "requests": client.used,
                "sources": stats.summary()})
    return out


def quality(records: list[dict]) -> dict:
    """Fraîcheur de la détection, complétude des champs, accord GeckoTerminal ↔ DexScreener sur la liquidité et le prix."""
    pools = [r for r in records if r["kind"] == "pool"]
    sec = {r["mint"]: r for r in records if r["kind"] == "security"}
    delay = sorted((p["observed_ms"] - p["created_ms"]) / 60000 for p in pools if p["created_ms"])
    fields = ("reserve_usd", "price_usd", "fdv_usd", "created_ms", "dex")
    agree_liq, agree_px = [], []
    for p in pools:
        ds = (sec.get(p["mint"]) or {}).get("dexscreener")
        if ds and ds.get("liquidity_usd") and p["reserve_usd"]:
            agree_liq.append(abs(ds["liquidity_usd"] / p["reserve_usd"] - 1))
        if ds and ds.get("price_usd") and p["price_usd"]:
            agree_px.append(abs(ds["price_usd"] / p["price_usd"] - 1))

    def q(x, k):
        return x[min(len(x) - 1, int(k * len(x)))] if x else None

    agree_liq.sort()
    agree_px.sort()
    return {"detection_delay_minutes": {"p10": q(delay, 0.1), "p50": q(delay, 0.5), "p90": q(delay, 0.9)},
            "completeness": {f: (sum(1 for p in pools if p.get(f) not in (None, "")) / len(pools)) if pools else None for f in fields},
            "dex": {d: sum(1 for p in pools if p["dex"] == d) for d in sorted({p["dex"] for p in pools if p["dex"]})},
            "liquidity_gap_vs_dexscreener": {"n": len(agree_liq), "p50": q(agree_liq, 0.5), "p90": q(agree_liq, 0.9)},
            "price_gap_vs_dexscreener": {"n": len(agree_px), "p50": q(agree_px, 0.5), "p90": q(agree_px, 0.9)}}
