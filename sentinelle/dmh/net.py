"""Client réseau PUBLIC du hub : lectures listées une à une, bornées et traçables.

Règles (refus par défaut) :
- seuls les hôtes, chemins et méthodes JSON-RPC listés ci-dessous sont accessibles ; tout le reste est refusé ;
- les méthodes qui signent ou diffusent (envoi de transaction, signature, comptes) sont refusées comme une
  opération réelle (`safety.forbid`), même si un hôte les accepterait ;
- aucun en-tête d'authentification n'est envoyé, aucun identifiant n'est accepté ;
- limite de débit par hôte, budget de requêtes, délai maximal, taille maximale de réponse ;
- reprise bornée (lectures seulement) avec temporisation croissante, et coupe-circuit par hôte après
  plusieurs échecs : une source en panne devient INDISPONIBLE, elle n'est jamais remplacée par une valeur inventée ;
- chaque réponse est horodatée (UTC, millisecondes) et hachée (SHA-256) pour servir de preuve.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

from .. import safety

MAX_BYTES = 2_000_000

# Méthodes qui écrivent, signent ou exposent des comptes : refusées comme une opération réelle.
WRITE_RPC = frozenset({
    "eth_sendRawTransaction", "eth_sendTransaction", "eth_sign", "eth_signTransaction", "eth_signTypedData",
    "eth_signTypedData_v4", "eth_accounts", "eth_requestAccounts", "personal_sign", "personal_unlockAccount",
    "wallet_addEthereumChain", "wallet_switchEthereumChain", "sendTransaction", "simulateTransaction",
    "requestAirdrop", "sendBundle", "eth_sendBundle",
})
EVM_READ = frozenset({"eth_call", "eth_chainId", "eth_blockNumber", "eth_gasPrice", "eth_feeHistory", "eth_getCode",
                      "eth_maxPriorityFeePerGas", "eth_getBlockByNumber"})
SOLANA_READ = frozenset({"getSlot", "getRecentPrioritizationFees", "getMinimumBalanceForRentExemption", "getAccountInfo",
                         "getTokenSupply", "getTokenLargestAccounts", "getHealth", "getBlockTime", "getGenesisHash",
                         "getEpochInfo", "getSignaturesForAddress", "getTransaction"})
# Paramètres qui transformeraient un devis en transaction à signer : refusés.
# (comparaison sans tenir compte de la casse)
FORBIDDEN_PARAMS = frozenset({"taker", "payer", "userpublickey", "wallet", "feeaccount", "referralaccount", "signature",
                              "from", "privatekey", "apikey", "secret"})


@dataclass(frozen=True)
class Host:
    name: str
    paths: tuple[str, ...]  # expressions régulières complètes des chemins autorisés
    rps: float
    rpc: frozenset[str] = frozenset()
    chain: str | None = None

    def allows(self, path: str) -> bool:
        return any(re.fullmatch(p, path) for p in self.paths)


_SYM = r"[A-Za-z0-9]{2,20}"
HOSTS: dict[str, Host] = {h.name: h for h in (
    Host("data-api.binance.vision", (r"/api/v3/(depth|exchangeInfo|aggTrades|time|ticker/bookTicker)",), 5.0),
    Host("api.kraken.com", (r"/0/public/(Depth|AssetPairs|Trades|Time)",), 1.0),
    Host("api.exchange.coinbase.com", (rf"/products/{_SYM}-{_SYM}(/book|/trades)?", r"/time"), 3.0),
    Host("www.bitstamp.net", (rf"/api/v2/(order_book|transactions)/{_SYM}/", r"/api/v2/trading-pairs-info/"), 2.0),
    Host("api.bitvavo.com", (rf"/v2/{_SYM}-{_SYM}/(book|trades)", r"/v2/markets", r"/v2/time"), 2.0),
    Host("api.jup.ag", (r"/swap/v2/order", r"/tokens/v2/search", r"/price/v3"), 0.5),
    Host("ethereum-rpc.publicnode.com", (r"/",), 2.0, EVM_READ, "ethereum"),
    Host("mainnet.base.org", (r"/",), 2.0, EVM_READ, "base"),
    Host("base-rpc.publicnode.com", (r"/",), 2.0, EVM_READ, "base"),
    Host("api.mainnet-beta.solana.com", (r"/",), 1.0, SOLANA_READ, "solana"),
    Host("solana-rpc.publicnode.com", (r"/",), 2.0, SOLANA_READ, "solana"),
    Host("api.gopluslabs.io", (r"/api/v1/(token_security/\d{1,6}|solana/token_security)",), 0.5),
    Host("api.honeypot.is", (r"/v2/IsHoneypot",), 0.5),
    Host("api.rugcheck.xyz", (r"/v1/tokens/[1-9A-HJ-NP-Za-km-z]{32,44}/report/summary", r"/v1/tokens/[1-9A-HJ-NP-Za-km-z]{32,44}/report"), 0.5),
    Host("api.coingecko.com", (r"/api/v3/coins/[a-z0-9-]{1,60}", r"/api/v3/simple/price"), 0.05),
    Host("tokens.uniswap.org", (r"/",), 0.2),
    Host("api.dexscreener.com", (r"/latest/dex/tokens/[0-9A-Za-z,]{32,400}", r"/token-pairs/v1/[a-z]{2,20}/[0-9A-Za-z]{32,44}"), 1.0),
    # GeckoTerminal (M58, pools établis) : pools Solana en tendance, lecture publique ; refuse au-delà d'environ 9 req/min (M56)
    Host("api.geckoterminal.com", (r"/api/v2/networks/solana/trending_pools",), 0.12),
)}
# Points d'accès d'exécution ou privés, par hôte : refusés comme une opération réelle, avant même la liste blanche.
FORBIDDEN_PATHS: dict[str, tuple[str, ...]] = {
    "api.jup.ag": ("/swap/v2/execute", "/swap/v2/build", "/swap/v1/swap", "/ultra/", "/trigger/", "/recurring/"),
    "api.bitvavo.com": ("/v2/order", "/v2/balance", "/v2/withdrawal", "/v2/deposit", "/v2/account"),
    "api.kraken.com": ("/0/private/",),
    "api.exchange.coinbase.com": ("/orders", "/accounts", "/fills", "/transfers", "/withdrawals", "/deposits", "/profiles"),
    "data-api.binance.vision": ("/api/v3/order", "/api/v3/account", "/api/v3/myTrades", "/sapi/"),
    "www.bitstamp.net": ("/api/v2/buy", "/api/v2/sell", "/api/v2/balance", "/api/v2/withdrawal", "/api/v2/user_transactions"),
}


class SourceUnavailable(RuntimeError):
    """La source n'a pas répondu correctement : la donnée est INDISPONIBLE (jamais remplacée)."""

    def __init__(self, host: str, reason: str):
        super().__init__(f"{host} : {reason}")
        self.host = host
        self.reason = reason


class MalformedResponse(SourceUnavailable):
    pass


class RequestNotAllowed(ValueError):
    pass


@dataclass(frozen=True)
class Fetched:
    host: str
    path: str
    status: int
    body: bytes
    received_ms: int
    latency_ms: float
    sha256: str
    attempts: int

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MalformedResponse(self.host, f"réponse non JSON ({exc.__class__.__name__})") from exc

    def evidence(self) -> dict:
        return {"host": self.host, "path": self.path, "status": self.status, "received_ms": self.received_ms,
                "latency_ms": round(self.latency_ms, 1), "sha256": self.sha256, "bytes": len(self.body)}


@dataclass
class _HostState:
    next_slot: float = 0.0
    failures: int = 0
    open_until: float = 0.0
    calls: int = 0
    errors: int = 0
    last_error: str | None = None
    last_ok_ms: int | None = None
    latencies: list[float] = field(default_factory=list)


Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, dict, bytes]]


def httpx_transport() -> Transport:
    import httpx  # dépendance déjà présente dans l'application ; respecte le proxy et les certificats du système

    client = httpx.Client(timeout=10.0, follow_redirects=False, trust_env=True)

    def send(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        r = client.request(method, url, headers=headers, content=body, timeout=timeout)
        data = r.read()
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, data[:MAX_BYTES + 1]

    return send


class PublicClient:
    """Accès réseau unique du hub. Un seul objet par processus suffit ; il est sûr entre threads."""

    def __init__(self, transport: Transport | None = None, *, clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 budget: int = 500, timeout: float = 10.0, retries: int = 2, breaker_after: int = 3,
                 breaker_cooldown: float = 30.0, seed: int | None = None):
        self._transport = transport
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.budget, self.timeout, self.retries = budget, timeout, retries
        self.breaker_after, self.breaker_cooldown = breaker_after, breaker_cooldown
        self._lock = threading.Lock()
        self._state: dict[str, _HostState] = {}
        self._rng = random.Random(seed)
        self.used = 0

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            self._transport = httpx_transport()
        return self._transport

    # -- contrôle d'accès ---------------------------------------------------------------
    @staticmethod
    def check(host: str, path: str, params: dict | None = None, rpc_method: str | None = None) -> Host:
        h = HOSTS.get(host)
        if h is None:
            raise RequestNotAllowed(f"hôte non autorisé : {host}")
        if any(path.startswith(p) for p in FORBIDDEN_PATHS.get(host, ())):
            safety.forbid(f"appel d'un point d'exécution « {host}{path} »")
        if not h.allows(path):
            raise RequestNotAllowed(f"chemin non autorisé : {host}{path}")
        bad = sorted(k for k in (params or {}) if str(k).lower() in FORBIDDEN_PARAMS)
        if bad:
            safety.forbid(f"paramètre de transaction « {bad[0]} » sur {host}")
        if rpc_method is not None:
            if rpc_method in WRITE_RPC or rpc_method.startswith(("personal_", "wallet_")) or "send" in rpc_method.lower() \
                    or ("sign" in rpc_method.lower() and rpc_method != "getSignaturesForAddress"):
                safety.forbid(f"méthode d'écriture « {rpc_method} » sur {host}")
            if rpc_method not in h.rpc:
                raise RequestNotAllowed(f"méthode non autorisée sur {host} : {rpc_method}")
        elif h.rpc:
            raise RequestNotAllowed(f"{host} n'accepte que des appels JSON-RPC listés")
        return h

    # -- appels ---------------------------------------------------------------------------
    def get(self, host: str, path: str, params: dict | None = None) -> Fetched:
        h = self.check(host, path, params)
        url = f"https://{host}{path}" + (f"?{urlencode(params)}" if params else "")
        return self._send(h, "GET", url, path, None)

    def rpc(self, host: str, method: str, params: list | None = None) -> tuple[Any, Fetched]:
        h = self.check(host, "/", None, method)
        if method == "eth_call":
            call = (params or [{}])[0]
            if not isinstance(call, dict) or set(call) - {"to", "data"}:
                safety.forbid(f"eth_call avec champs non autorisés sur {host}")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
        f = self._send(h, "POST", f"https://{host}/", "/", body)
        data = f.json()
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
            raise MalformedResponse(host, "réponse JSON-RPC malformée")
        if "error" in data:
            raise SourceUnavailable(host, f"erreur JSON-RPC : {str(data['error'])[:160]}")
        if "result" not in data:
            raise MalformedResponse(host, "réponse JSON-RPC sans résultat")
        return data["result"], f

    def _send(self, h: Host, method: str, url: str, path: str, body: bytes | None) -> Fetched:
        st = self._state.setdefault(h.name, _HostState())
        attempts = 0
        last: Exception | None = None
        while attempts <= self.retries:
            with self._lock:
                now = self.monotonic()
                if now < st.open_until:
                    raise SourceUnavailable(h.name, f"coupe-circuit ouvert ({st.failures} échecs consécutifs)")
                if self.used >= self.budget:
                    raise SourceUnavailable(h.name, "budget de requêtes épuisé")
                wait = max(0.0, st.next_slot - now)
                st.next_slot = max(now, st.next_slot) + 1.0 / h.rps
                self.used += 1
                st.calls += 1
            if wait:
                self.sleep(wait)
            attempts += 1
            t0 = self.monotonic()
            try:
                headers = {"User-Agent": "sentinelle-paper-research/1.0", "Accept": "application/json"}
                if body is not None:
                    headers["Content-Type"] = "application/json"
                status, rh, data = self.transport(method, url, headers, body, self.timeout)
                latency = (self.monotonic() - t0) * 1000
                if len(data) > MAX_BYTES:
                    raise MalformedResponse(h.name, "réponse trop volumineuse")
                if status == 429 or status >= 500:
                    retry_after = rh.get("retry-after")
                    try:
                        wait_s = float(retry_after) if retry_after is not None else None
                    except ValueError:
                        wait_s = None
                    if wait_s is not None and 0 < wait_s <= 60 and attempts <= self.retries:
                        with self._lock:  # la source demande d'attendre : on respecte son délai, sans compter un échec
                            st.next_slot = max(st.next_slot, self.monotonic() + wait_s)
                        continue
                    raise SourceUnavailable(h.name, f"HTTP {status}" + (f" (réessayer après {retry_after} s)" if retry_after else ""))
                if status != 200:
                    raise SourceUnavailable(h.name, f"HTTP {status}")
                with self._lock:
                    st.failures = 0
                    st.last_ok_ms = int(self.clock() * 1000)
                    st.latencies = (st.latencies + [latency])[-200:]
                return Fetched(h.name, path, status, data, int(self.clock() * 1000), latency, hashlib.sha256(data).hexdigest(), attempts)
            except SourceUnavailable as exc:
                last = exc
                transient = not isinstance(exc, MalformedResponse) and ("HTTP 429" in exc.reason or "HTTP 5" in exc.reason)
            except Exception as exc:  # délai dépassé, connexion coupée, DNS…
                last = SourceUnavailable(h.name, f"{exc.__class__.__name__}: {str(exc)[:120]}")
                transient = True
            with self._lock:
                st.errors += 1
                st.failures += 1
                st.last_error = str(last)[:200]
                if st.failures >= self.breaker_after:
                    st.open_until = self.monotonic() + min(300.0, self.breaker_cooldown * 2 ** (st.failures - self.breaker_after))
            if not transient or attempts > self.retries:
                break
            self.sleep(min(8.0, 0.5 * 2 ** (attempts - 1)) * (0.75 + 0.5 * self._rng.random()))
        assert last is not None
        raise last

    def health(self) -> dict:
        with self._lock:
            now = self.monotonic()
            out = {}
            for name, st in self._state.items():
                lat = sorted(st.latencies)
                out[name] = {"calls": st.calls, "errors": st.errors, "consecutive_failures": st.failures,
                             "circuit_open": now < st.open_until, "last_error": st.last_error, "last_ok_ms": st.last_ok_ms,
                             "latency_ms_median": lat[len(lat) // 2] if lat else None}
            return {"used": self.used, "budget": self.budget, "hosts": out}
