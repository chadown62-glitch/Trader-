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
    """Top ten token accounts, including AMM vaults and exchanges; not beneficial owners."""
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
            verdict.veto("token_program", "Le Guardian V0 couvre uniquement SPL Token classique", "guardian")
        for authority in ("mint_authority", "freeze_authority"):
            if mint[authority] is not None:
                verdict.veto(authority, f"Autorité active : {authority}", "guardian")
        source = "concentration"
        holders = dex.solana_concentration(rpc, token, mint["supply"])
        evidence[source] = holders
        share = holders["top10_share"]
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
