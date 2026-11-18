"""Risque d'un token : lecture sur la chaîne + services publics de contrôle, traités comme des SIGNAUX.

Un token n'est SÛR que s'il est vérifié dans le registre ET qu'aucune source ne signale de risque non accepté.
Une source qui ne répond pas n'est pas un feu vert : sans lecture sur la chaîne, le token n'est pas sûr.
Les pouvoirs connus d'un émetteur régulé (ex. gel ou émission d'un jeton stable) sont acceptés explicitement
dans le registre (« accepted_flags ») et restent affichés.
"""
from __future__ import annotations

import threading
import time

from . import dex
from .net import PublicClient, SourceUnavailable
from .security import assess_token
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
    flags = [str(r.get("name")) for r in d.get("risks") or [] if str(r.get("level")).lower() in ("danger", "warn")]
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
