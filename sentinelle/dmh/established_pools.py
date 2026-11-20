"""Pools établis (M58) : candidats memecoin Solana déjà liquides, actifs et revendables. Observation seulement.

Leçon de M56 : à leur détection, les pools nés depuis quelques minutes avaient une liquidité médiane d'environ 1 600 $,
un tiers n'avaient aucune route de revente et aucun ne passait le veto. On part donc des pools Solana en tendance
(GeckoTerminal), puis on applique, dans cet ordre et du moins coûteux au plus coûteux :

1. liquidité d'au moins 20 000 $ ;
2. pool âgé d'au moins une heure ;
3. dernière heure active : au moins 5 000 $ de volume, 10 achats et 10 ventes ;
4. aller-retour Jupiter de 0,05 SOL coté sans portefeuille (achat puis revente) : perte d'au plus 10 % ;
5. veto anti-arnaque (``tokenrisk.assess_candidate``), toujours en dernier mot.

Un devis Jupiter sans portefeuille n'est pas une transaction : rien n'est construit, signé ni diffusé.
Chaque décision est consignée (agent ``established_pools``) et ne se rejoue qu'après un délai de six heures.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

from ..ai.features_memecoin import extract_features
from . import dex
from .net import SourceUnavailable
from .solana_scanner import QUOTE_MINTS
from .tokenrisk import assess_candidate
from .tokens import TokenId, load_registry

AGENT = "established_pools"
DOMAIN = "memecoin"
GT = "api.geckoterminal.com"
TRENDING = "/api/v2/networks/solana/trending_pools"
SOL = "So11111111111111111111111111111111111111112"
MIN_LIQUIDITY_USD = 20_000.0
MIN_AGE_MINUTES = 60.0
MIN_VOLUME_H1_USD = 5_000.0
MIN_TRADES_H1 = 10  # achats ET ventes sur la dernière heure
PROBE_LAMPORTS = 50_000_000  # 0,05 SOL : l'ordre de grandeur d'un ordre fictif de 10 €
MAX_ROUND_TRIP_LOSS = 0.10
COOLDOWN_S = 6 * 3600
CANDIDATE_LIMIT = 4  # contrôles complets (Jupiter, chaîne, Rugcheck) par passage
STAGES = ("liquidity", "age", "activity", "round_trip", "guardian")
SOURCE_ERRORS = (SourceUnavailable, KeyError, TypeError, ValueError, IndexError, OverflowError)


def _number(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x >= 0 else None


def _ms(iso):
    if not isinstance(iso, str):
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def parse(item):
    """Un pool GeckoTerminal en champs utiles ; ``None`` s'il ne cote pas un token Solana contre SOL ou USDC."""
    a, rel = item.get("attributes") or {}, item.get("relationships") or {}
    ident = lambda key: ((rel.get(key) or {}).get("data") or {}).get("id") or ""  # noqa: E731
    base, quote = ident("base_token"), ident("quote_token")
    if not base.startswith("solana_") or not quote.startswith("solana_") or not isinstance(a.get("address"), str):
        return None
    mint, quote_mint = base[7:], quote[7:]
    if quote_mint not in QUOTE_MINTS or mint in QUOTE_MINTS:
        return None
    tx = (a.get("transactions") or {}).get("h1") or {}
    volume = a.get("volume_usd") or {}
    m5 = (a.get("transactions") or {}).get("m5") or {}
    return {"pool": a["address"], "mint": mint, "quote_mint": quote_mint, "name": str(a.get("name") or "")[:40],
            "dex_id": ident("dex"), "created_ms": _ms(a.get("pool_created_at")), "liquidity_usd": _number(a.get("reserve_in_usd")),
            "price_usd": _number(a.get("base_token_price_usd")), "market_cap_usd": _number(a.get("market_cap_usd") or a.get("fdv_usd")),
            "volume_h1_usd": _number(volume.get("h1")), "volume_m5_usd": _number(volume.get("m5")),
            "buys_h1": _number(tx.get("buys")), "sells_h1": _number(tx.get("sells")),
            "buys_m5": _number(m5.get("buys")), "sells_m5": _number(m5.get("sells"))}


def prefilter(pool, now_ms):
    """Filtres sans requête : (étape, message) du premier critère manqué, ou ``None``. Donnée absente = refus."""
    liq = pool["liquidity_usd"]
    if liq is None or liq < MIN_LIQUIDITY_USD:
        return "liquidity", f"liquidité {liq or 0:,.0f} $ (< {MIN_LIQUIDITY_USD:,.0f} $)".replace(",", " ")
    created = pool["created_ms"]
    age = (now_ms - created) / 60000 if created and created <= now_ms else None
    if age is None or age < MIN_AGE_MINUTES:
        return "age", "pool trop récent" if age is not None else "date de création inconnue"
    vol, buys, sells = pool["volume_h1_usd"], pool["buys_h1"], pool["sells_h1"]
    if vol is None or vol < MIN_VOLUME_H1_USD or (buys or 0) < MIN_TRADES_H1 or (sells or 0) < MIN_TRADES_H1:
        return "activity", (f"dernière heure peu active : {vol or 0:,.0f} $, {int(buys or 0)} achats, "
                            f"{int(sells or 0)} ventes").replace(",", " ")
    return None


def round_trip(client, mint):
    """Achat puis revente cotés par Jupiter, sans portefeuille. La revente impossible est un refus, jamais un feu vert."""
    sol, token = TokenId("solana", SOL), TokenId("solana", mint)
    buy = dex.jupiter_quote(client, sol, token, PROBE_LAMPORTS)
    out = {"lamports_in": PROBE_LAMPORTS, "tokens": buy.amount_out, "lamports_back": None, "loss": None}
    try:
        sell = dex.jupiter_quote(client, token, sol, buy.amount_out)
    except SOURCE_ERRORS as exc:
        return out | {"sell_error": str(exc)[:160]}
    return out | {"lamports_back": sell.amount_out, "loss": 1 - sell.amount_out / PROBE_LAMPORTS}


def _record(db, bus, pool, now_s, kind, stage, message, **extra):
    pair = {"liquidity": {"usd": pool["liquidity_usd"]}, "marketCap": pool["market_cap_usd"], "volume": {"m5": pool["volume_m5_usd"]},
            "txns": {"m5": {"buys": pool["buys_m5"], "sells": pool["sells_m5"]}}}
    features = extract_features(pair, observed_ms=now_s * 1000, created_ms=pool["created_ms"])
    event = {"asset_domain": DOMAIN, "agent": AGENT, "symbol": pool["mint"], "kind": kind, "stage": stage, "message": message,
             "pool": pool["pool"], "quote_mint": pool["quote_mint"], "dex_id": pool["dex_id"], "name": pool["name"],
             "creation": {"source": "geckoterminal", "block_time": pool["created_ms"] // 1000}, "observed_ms": now_s * 1000,
             "features": features, "filters": {k: pool[k] for k in ("liquidity_usd", "volume_h1_usd", "buys_h1", "sells_h1")}, **extra}
    key = f"established:{DOMAIN}:solana:{pool['mint']}"
    with db.transaction():
        decision_id = db.decision(None, AGENT, pool["mint"], kind, message, event, ts=now_s)
        db.set_meta(key, json.dumps({"ts": now_s, "decision_id": decision_id}))
        db.after_commit(lambda: bus.publish("decision", {**event, "decision_id": decision_id}))
    return event


def _recent(db, mint, now_s):
    raw = db.get_meta(f"established:{DOMAIN}:solana:{mint}")
    try:
        return raw is not None and now_s - json.loads(raw)["ts"] < COOLDOWN_S
    except (TypeError, ValueError, KeyError):
        return False


def scan(client, db, bus, *, registry=None, limit=CANDIDATE_LIMIT):
    """Un passage borné sur les pools en tendance. Renvoie les décisions prises, l'entonnoir et les erreurs de source."""
    registry = load_registry() if registry is None else registry
    now_s = int(client.clock())
    funnel = {"seen": 0, "cooldown": 0, **{s: 0 for s in STAGES}, "accepted": 0}
    decisions, errors = [], []
    try:
        fetched = client.get(GT, TRENDING, {"page": 1})
        rows = fetched.json().get("data")
        if not isinstance(rows, list):
            raise ValueError("liste des pools en tendance absente")
    except SOURCE_ERRORS as exc:
        return {"asset_domain": DOMAIN, "decisions": [], "errors": [{"source": GT, "reason": str(exc)}], "funnel": funnel}
    checked, expensive = set(), 0
    for item in rows:
        pool = parse(item) if isinstance(item, dict) else None
        if pool is None or pool["mint"] in checked:
            continue
        checked.add(pool["mint"])
        try:
            if registry.get(TokenId("solana", pool["mint"])) is not None:
                continue  # actif catalogué : il relève du hub principal, pas du domaine memecoin
        except ValueError:
            continue
        funnel["seen"] += 1
        if _recent(db, pool["mint"], now_s):
            funnel["cooldown"] += 1
            continue
        missed = prefilter(pool, now_s * 1000)
        if missed:
            funnel[missed[0]] += 1
            decisions.append(_record(db, bus, pool, now_s, "observation_refused", *missed, source_sha256=fetched.sha256))
            continue
        if expensive >= limit:
            continue  # contrôles coûteux bornés : le reste attend le passage suivant
        expensive += 1
        try:
            trip = round_trip(client, pool["mint"])
        except SOURCE_ERRORS as exc:
            errors.append({"mint": pool["mint"], "reason": f"Jupiter : {exc}"})
            funnel["round_trip"] += 1
            decisions.append(_record(db, bus, pool, now_s, "observation_refused", "round_trip",
                                     "achat non coté par Jupiter : revendabilité non confirmée", source_sha256=fetched.sha256))
            continue
        if trip["loss"] is None or trip["loss"] > MAX_ROUND_TRIP_LOSS:
            funnel["round_trip"] += 1
            message = ("aucune route de revente" if trip["loss"] is None
                       else f"aller-retour immédiat : {trip['loss']:.1%} de perte (> {MAX_ROUND_TRIP_LOSS:.0%})")
            decisions.append(_record(db, bus, pool, now_s, "observation_refused", "round_trip", message, round_trip=trip,
                                     source_sha256=fetched.sha256))
            continue
        guardian = assess_candidate(client, TokenId("solana", pool["mint"]), pool["liquidity_usd"], min_liquidity_usd=MIN_LIQUIDITY_USD)
        if not guardian["allowed"]:
            funnel["guardian"] += 1
            message = "veto : " + " ; ".join(v["message"] for v in guardian["vetoes"])
            decisions.append(_record(db, bus, pool, now_s, "observation_refused", "guardian", message, round_trip=trip,
                                     guardian=guardian, source_sha256=fetched.sha256))
            continue
        funnel["accepted"] += 1
        decisions.append(_record(db, bus, pool, now_s, "observation_accepted", "accepted", "pool établi retenu", round_trip=trip,
                                 guardian=guardian, source_sha256=fetched.sha256))
    return {"asset_domain": DOMAIN, "decisions": decisions, "errors": errors, "funnel": funnel}


def summary(db, since_s, limit=500):
    """Entonnoir des décisions consignées depuis ``since_s`` (dernière décision par token) : pour le cockpit."""
    latest = {}
    for row in db.query("SELECT data FROM decisions WHERE agent=? AND ts>=? ORDER BY id DESC LIMIT ?", (AGENT, since_s, limit)):
        event = json.loads(row["data"])
        if event.get("asset_domain") == DOMAIN:
            latest.setdefault(event["symbol"], event)
    stages = {s: 0 for s in STAGES}
    accepted = []
    for event in latest.values():
        if event["kind"] == "observation_accepted":
            accepted.append(event)
        else:
            stages[event["stage"]] = stages.get(event["stage"], 0) + 1
    liq = sorted(e["filters"]["liquidity_usd"] for e in latest.values() if e["filters"].get("liquidity_usd") is not None)
    median = (liq[len(liq) // 2] if len(liq) % 2 else (liq[len(liq) // 2 - 1] + liq[len(liq) // 2]) / 2) if liq else None
    return {"asset_domain": DOMAIN, "since": since_s, "tokens": len(latest), "refused_by_stage": stages, "accepted": len(accepted),
            "median_liquidity_usd": median,
            "candidates": [{"mint": e["symbol"], "name": e.get("name"), "dex_id": e.get("dex_id"),
                            "liquidity_usd": e["filters"]["liquidity_usd"], "volume_h1_usd": e["filters"]["volume_h1_usd"],
                            "round_trip_loss": (e.get("round_trip") or {}).get("loss"), "observed_ms": e["observed_ms"]}
                           for e in accepted][:20],
            "thresholds": {"min_liquidity_usd": MIN_LIQUIDITY_USD, "min_age_minutes": MIN_AGE_MINUTES,
                           "min_volume_h1_usd": MIN_VOLUME_H1_USD, "min_trades_h1": MIN_TRADES_H1,
                           "max_round_trip_loss": MAX_ROUND_TRIP_LOSS}}
