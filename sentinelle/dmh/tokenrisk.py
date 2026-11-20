"""Risque d'un token : lecture sur la chaîne + services publics de contrôle, traités comme des SIGNAUX.

Un token n'est SÛR que s'il est vérifié dans le registre ET qu'aucune source ne signale de risque non accepté.
Une source qui ne répond pas n'est pas un feu vert : sans lecture sur la chaîne, le token n'est pas sûr.
Les pouvoirs connus d'un émetteur régulé (ex. gel ou émission d'un jeton stable) sont acceptés explicitement
dans le registre (« accepted_flags ») et restent affichés.
"""
from __future__ import annotations

import math
import threading
import time

from . import dex
from .net import PublicClient, SourceUnavailable
from .security import Verdict, assess_token
from .tokens import NETWORKS, TokenId, TokenRegistry


def _goplus_evm(client: PublicClient, token: TokenId) -> dict | None:
    chain = NETWORKS[token.network]["chain_id"]
    f = client.get("api.gopluslabs.io", f"/api/v1/token_security/{chain}", {"contract_addresses": token.address})
    r = (f.json().get("result") or {}).get(token.address.lower())
    if not r:
        return None
    flags = [label for key, label in (("is_mintable", "émission possible"), ("owner_change_balance", "le propriétaire peut modifier les soldes"),
                                       ("hidden_owner", "propriétaire caché"), ("selfdestruct", "autodestruction possible"),
                                       ("transfer_pausable", "transferts suspendables"), ("is_blacklisted", "liste noire"),
                                       ("slippage_modifiable", "taxe modifiable"), ("trading_cooldown", "délai entre transactions"),
                                       ("is_proxy", "contrat modifiable (proxy)")) if str(r.get(key)) == "1"]
    if str(r.get("is_open_source")) == "0":
        flags.append("code source non publié")
    tax = lambda k: float(r.get(k) or 0)  # noqa: E731
    return {"honeypot": str(r.get("is_honeypot")) == "1", "cannot_sell": str(r.get("cannot_sell_all")) == "1",
            "buy_tax": tax("buy_tax"), "sell_tax": tax("sell_tax"), "flags": flags, "sha256": f.sha256, "received_ms": f.received_ms}


def _honeypot_is(client: PublicClient, token: TokenId) -> dict | None:
    chain = NETWORKS[token.network]["chain_id"]
    f = client.get("api.honeypot.is", "/v2/IsHoneypot", {"address": token.address, "chainID": chain})
    d = f.json()
    hp = (d.get("honeypotResult") or {}).get("isHoneypot")
    sim = d.get("simulationResult") or {}
    return {"honeypot": bool(hp), "buy_tax": float(sim.get("buyTax") or 0), "sell_tax": float(sim.get("sellTax") or 0),
            "flags": [], "sha256": f.sha256, "received_ms": f.received_ms}


def _rugcheck(client: PublicClient, token: TokenId) -> dict | None:
    f = client.get("api.rugcheck.xyz", f"/v1/tokens/{token.address}/report/summary")
    d = f.json()
    if not isinstance(d, dict) or d.get("error") or not isinstance(d.get("risks"), list):
        raise SourceUnavailable("rugcheck", "liste des risques absente")
    flags = []
    for risk in d["risks"]:
        if (not isinstance(risk, dict) or risk.get("level") not in ("danger", "warn", "info")
                or not isinstance(risk.get("name"), str) or not risk["name"].strip()):
            raise SourceUnavailable("rugcheck", "signal de risque malformé")
        if risk["level"] != "info":
            flags.append(str(risk["name"]))
    if d.get("rugged") is True:
        flags.append("rugged")
    return {"honeypot": False, "flags": flags, "score": d.get("score_normalised", d.get("score")), "sha256": f.sha256,
            "received_ms": f.received_ms}


class TokenRiskService:
    """Évaluation à la demande (cache d'une heure) ; aucune surveillance permanente."""

    def __init__(self, client: PublicClient, registry: TokenRegistry, *, ttl_s: float = 3600.0, clock=time.time):
        self.client, self.registry, self.ttl, self.clock = client, registry, ttl_s, clock
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def assess(self, token: TokenId) -> dict:
        with self._lock:
            hit = self._cache.get(token.key)
            if hit and self.clock() - hit[0] < self.ttl:
                return hit[1]
        info = self.registry.get(token)
        onchain = None
        try:
            if token.network == "solana":
                onchain = dex.solana_mint(self.client, token)
            else:
                onchain = dex.erc20_info(self.client, token)
        except SourceUnavailable:
            onchain = None
        external: dict[str, dict | None] = {}
        sources = [("goplus", _goplus_evm), ("honeypot.is", _honeypot_is)] if token.network != "solana" else [("rugcheck", _rugcheck)]
        for name, fn in sources:
            try:
                external[name] = fn(self.client, token)
            except (SourceUnavailable, KeyError, TypeError, ValueError):
                external[name] = None
        accepted = info.accepted_flags if info else ()
        res = assess_token(info, onchain, external, accepted_flags=accepted)
        res |= {"token": token.key, "symbol": info.symbol if info else None, "ts_ms": int(self.clock() * 1000),
                "responded": [k for k, v in external.items() if v is not None]}
        if not res["responded"] and res["status"] == "SAFE":
            res["status"] = "UNSAFE"
            res["flags"] = res["flags"] + ["aucun service de contrôle n'a répondu"]
        with self._lock:
            self._cache[token.key] = (self.clock(), res)
        return res


# Token-2022 : extensions purement descriptives (nom, symbole, image, regroupement), celles des tokens pump.fun. Toute
# autre extension (frais ou crochet de transfert, délégué permanent, état gelé par défaut, non transférable…) reste un veto.
SAFE_TOKEN_2022_EXTENSIONS = frozenset({"metadataPointer", "tokenMetadata", "groupPointer", "tokenGroup",
                                        "groupMemberPointer", "tokenGroupMember"})
# Détenteurs réels (rapport Rugcheck, hors pools et verrous connus) : seuils fixés dans M56.
BENEFICIAL_MAX_TOP10 = 0.35
BENEFICIAL_MAX_TOP1 = 0.15
EXCLUDED_HOLDER_TYPES = ("AMM", "LOCKER")


def _rugcheck_holders(client: PublicClient, token: TokenId) -> dict:
    """Plus gros détenteurs hors pools et verrous connus (rapport Rugcheck complet).

    Lu en premier : le RPC public refuse (HTTP 403) ou limite (HTTP 429, avec de longues attentes) la lecture des plus
    gros comptes, si bien qu'aucun token ne pouvait être évalué. Sans liste exploitable, le Guardian revient à la
    chaîne ; si la chaîne échoue aussi, la concentration est indisponible, donc un veto.
    """
    f = client.get("api.rugcheck.xyz", f"/v1/tokens/{token.address}/report")
    d = f.json()
    holders = d.get("topHolders") if isinstance(d, dict) else None
    if not isinstance(holders, list) or not holders:
        raise ValueError("détenteurs Rugcheck absents")
    known = d.get("knownAccounts") or {}
    excluded = {k for k, v in known.items() if isinstance(v, dict) and v.get("type") in EXCLUDED_HOLDER_TYPES}
    shares = []
    for h in holders:
        if not isinstance(h, dict):
            raise ValueError("détenteur Rugcheck malformé")
        if h.get("address") in excluded or h.get("owner") in excluded:
            continue
        pct = float(h["pct"]) / 100
        if not (math.isfinite(pct) and 0 <= pct <= 1):
            raise ValueError("part de détention incohérente")
        shares.append(pct)
    shares.sort(reverse=True)
    return {"source": "rugcheck", "top10_share": sum(shares[:10]), "top1_share": shares[0] if shares else 0.0,
            "listed": len(holders), "excluded_pools_or_lockers": len(holders) - len(shares), "sha256": f.sha256,
            "received_ms": f.received_ms, "note": "hors pools et verrous connus"}


class _GuardianRPC:
    """Validate raw fields that the general-purpose SPL readers normalize or omit."""
    def __init__(self, client):
        self.client = client

    def rpc(self, host, method, params):
        result, evidence = self.client.rpc(host, method, params)
        value = result["value"]
        if method == "getAccountInfo":
            parsed = value["data"]["parsed"]
            info = parsed["info"]
            if (parsed["type"] != "mint" or info["isInitialized"] is not True
                    or not {"mintAuthority", "freezeAuthority"} <= info.keys()):
                raise ValueError("mint non initialisé ou autorités non renseignées")
        elif method == "getTokenLargestAccounts":
            if not isinstance(value, list) or not value:
                raise ValueError("liste des comptes vide")
            addresses = set()
            for row in value:
                address = TokenId("solana", row["address"]).address
                if address in addresses or not str(row["amount"]).isascii() or not str(row["amount"]).isdigit():
                    raise ValueError("compte dupliqué ou montant invalide")
                addresses.add(address)
        return result, evidence


def assess_candidate(client, token, liquidity_usd, *, min_liquidity_usd=25000, max_top10_share=0.50):
    """Veto anti-arnaque d'un candidat memecoin Solana.

    Concentration : les détenteurs réels selon Rugcheck (hors pools et verrous ; 10 premiers ≤ 35 %, premier ≤ 15 %) ;
    à défaut, les dix plus gros comptes lus sur la chaîne (pools compris, ≤ ``max_top10_share``). Token-2022 n'est
    admis qu'avec des extensions purement descriptives.
    """
    if token.network != "solana" or not (0 < min_liquidity_usd <= 1e300 and 0 < max_top10_share <= 1):
        raise ValueError("politique du Guardian invalide")
    verdict = Verdict()
    evidence = {}
    if liquidity_usd is None or not math.isfinite(liquidity_usd) or liquidity_usd < min_liquidity_usd:
        verdict.veto("liquidity", f"Liquidité USD {liquidity_usd} ; minimum {min_liquidity_usd}", "guardian")
    rpc = _GuardianRPC(client)
    source = "mint"
    try:
        mint = dex.solana_mint(rpc, token)
        evidence[source] = mint
        if not mint["exists"] or mint["supply"] <= 0:
            raise ValueError("mint ou offre positive indisponible")
        if mint["program"] != dex.SPL_TOKEN:
            risky = sorted(set(mint.get("extensions") or ()) - SAFE_TOKEN_2022_EXTENSIONS)
            if mint["program"] != dex.SPL_TOKEN_2022:
                verdict.veto("token_program", "Programme de token non couvert par le Guardian", "guardian")
            elif risky:
                verdict.veto("token_program", "Extensions Token-2022 à risque : " + ", ".join(risky), "guardian")
        for authority in ("mint_authority", "freeze_authority"):
            if mint[authority] is not None:
                verdict.veto(authority, f"Autorité active : {authority}", "guardian")
        source = "concentration"
        try:  # détenteurs réels d'abord : le RPC public refuse (403) ou limite (429) la lecture des plus gros comptes
            holders = _rugcheck_holders(client, token)
        except (SourceUnavailable, ValueError, KeyError, TypeError) as exc:
            holders = dex.solana_concentration(rpc, token, mint["supply"]) | {"rugcheck_error": str(exc)[:160]}
        evidence[source] = holders
        share = holders["top10_share"]
        if holders.get("source") == "rugcheck":
            if not 0 <= share <= 1:
                raise ValueError("concentration incohérente")
            if share > BENEFICIAL_MAX_TOP10:
                verdict.veto(source, f"Top 10 {share:.2%} > {BENEFICIAL_MAX_TOP10:.0%}, hors pools et verrous", "guardian")
            if holders["top1_share"] > BENEFICIAL_MAX_TOP1:
                verdict.veto(source, f"Premier détenteur {holders['top1_share']:.2%} > {BENEFICIAL_MAX_TOP1:.0%}", "guardian")
        else:
            if not 0 < share <= 1:
                raise ValueError("concentration incohérente")
            if share > max_top10_share:
                verdict.veto(source, f"Top 10 {share:.2%} > {max_top10_share:.2%}, comptes de pools inclus", "guardian")
    except (SourceUnavailable, KeyError, TypeError, ValueError, OverflowError) as exc:
        verdict.veto(source + "_unavailable", str(exc), "guardian")
    try:
        report = _rugcheck(client, token)
        evidence["rugcheck"] = report
        for flag in report["flags"]:
            verdict.veto("rugcheck", flag, "guardian")
    except (SourceUnavailable, TypeError, ValueError, KeyError) as exc:
        verdict.veto("rugcheck_unavailable", str(exc), "guardian")
    return {**verdict.to_dict(), "evidence": evidence,
            "min_liquidity_usd": min_liquidity_usd, "max_top10_share": max_top10_share}
