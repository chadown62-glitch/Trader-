"""Adaptateurs des réseaux décentralisés : DEVIS NON SIGNÉS et lectures publiques uniquement.

- Solana : devis de l'agrégateur Jupiter (Swap API V2, `GET /swap/v2/order` SANS portefeuille : la réponse ne
  contient alors aucune transaction ; si elle en contenait une, elle serait refusée), frais de priorité récents
  et dépôt de location d'un compte de token (lus sur un nœud public) ;
- Ethereum et Base : devis Uniswap V3 obtenus par `eth_call` sur le contrat QuoterV2 officiel (aucune
  transaction), existence du pool vérifiée auprès de la fabrique officielle, prix du gaz récent, et sur Base
  frais de publication sur Ethereum (oracle du réseau) ;
- lecture des tokens sur la chaîne : décimales, symbole, autorités d'émission et de gel, concentration.

Adresses officielles des contrats (documentation Uniswap consultée le 16/11/2026, pages archivées et hachées
dans research/market-hub-v1/sources.json).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from .. import safety
from .abi import decode_string, decode_words, encode_call
from .net import MalformedResponse, PublicClient, SourceUnavailable
from .tokens import TokenId

UNISWAP_V3 = {
    "ethereum": {"factory": "0x1f98431c8ad98523631ae4a59f267346ea31f984", "quoter_v2": "0x61ffe014ba17989e743c5f6cb21bf9697530b21e",
                 "universal_router": "0x66a9893cc07d91d95644aedd05d03f95e1dba8af"},
    "base": {"factory": "0x33128a8fc17869897dce68ed026d694621f6fdfd", "quoter_v2": "0x3d4e44eb1374240ce5f1b871ab261cd16335b76a",
             "universal_router": "0x6ff5693b99212da76ad316178a184ab56d299b43"},
}
FEE_TIERS = (100, 500, 3000, 10000)  # centièmes de pb : 0,01 %, 0,05 %, 0,3 %, 1 %
RPC_HOSTS = {"ethereum": ("ethereum-rpc.publicnode.com",), "base": ("mainnet.base.org", "base-rpc.publicnode.com"),
             "solana": ("api.mainnet-beta.solana.com", "solana-rpc.publicnode.com")}
BASE_GAS_ORACLE = "0x420000000000000000000000000000000000000f"
SPL_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SPL_TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_ACCOUNT_SIZE = 165  # octets d'un compte de token SPL
SOLANA_BASE_FEE_LAMPORTS = 5000  # par signature (documentation Solana « Fees »)


def _rpc(client: PublicClient, network: str, method: str, params: list | None = None):
    """Appel en lecture sur le premier nœud public qui répond ; l'échec de tous rend la donnée indisponible."""
    last: Exception | None = None
    for host in RPC_HOSTS[network]:
        try:
            return client.rpc(host, method, params)
        except SourceUnavailable as exc:
            last = exc
    raise last or SourceUnavailable(network, "aucun nœud public")


def _hex(x) -> int:
    if not isinstance(x, str) or not x.startswith("0x"):
        raise ValueError("entier hexadécimal attendu")
    return int(x, 16)


# ---------------------------------------------------------------------------------------------------------------
# Jupiter (Solana)
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DexQuote:
    network: str
    venue: str  # agrégateur ou protocole
    token_in: TokenId
    token_out: TokenId
    amount_in: int  # unités entières (avant décimales)
    amount_out: int
    min_out: int | None
    price_impact_bps: float | None
    fee_bps: float  # frais de la plateforme d'agrégation (en plus des frais de pool déjà déduits du montant reçu)
    pool_fee_bps: float | None  # frais de pool quand ils sont connus séparément (Uniswap)
    gas_units: int | None
    route: tuple[dict, ...]
    received_ms: int
    sha256: str
    latency_ms: float
    request_id: str | None = None
    evidence: dict = field(default_factory=dict, compare=False)


def jupiter_quote(client: PublicClient, token_in: TokenId, token_out: TokenId, amount_in: int) -> DexQuote:
    if token_in.network != "solana" or token_out.network != "solana":
        raise ValueError("Jupiter ne cote que des tokens Solana")
    if amount_in <= 0:
        raise ValueError("montant à échanger nul")
    f = client.get("api.jup.ag", "/swap/v2/order", {"inputMint": token_in.address, "outputMint": token_out.address,
                                                     "amount": str(int(amount_in))})
    d = f.json()
    if not isinstance(d, dict):
        raise MalformedResponse("api.jup.ag", "devis malformé")
    if d.get("transaction"):
        safety.forbid("devis Jupiter accompagné d'une transaction à signer (refusée sans lecture)")
    if d.get("error") or d.get("errorCode"):
        raise SourceUnavailable("api.jup.ag", f"pas de devis : {str(d.get('error') or d.get('errorCode'))[:120]}")
    try:
        if d["inputMint"] != token_in.address or d["outputMint"] != token_out.address or int(d["inAmount"]) != int(amount_in):
            raise MalformedResponse("api.jup.ag", "le devis ne correspond pas à la demande")
        out = int(d["outAmount"])
        thr = int(d["otherAmountThreshold"]) if d.get("otherAmountThreshold") is not None else None
        route = tuple({"amm": (r.get("swapInfo") or {}).get("ammKey"), "label": (r.get("swapInfo") or {}).get("label"),
                       "input": (r.get("swapInfo") or {}).get("inputMint"), "output": (r.get("swapInfo") or {}).get("outputMint"),
                       "percent": r.get("percent")} for r in d.get("routePlan") or [])
        impact = d.get("priceImpactPct")
        impact_bps = abs(float(impact)) * 1e4 if impact not in (None, "") else None
        fee = float(d.get("feeBps") or (d.get("platformFee") or {}).get("feeBps") or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedResponse("api.jup.ag", f"devis malformé ({exc.__class__.__name__})") from exc
    if out <= 0 or not route:
        raise SourceUnavailable("api.jup.ag", "aucune route")
    ev = f.evidence() | {"router": d.get("router"), "mode": d.get("mode"), "swap_type": d.get("swapType"),
                         "gasless": d.get("gasless"), "in_usd": d.get("inUsdValue"), "out_usd": d.get("outUsdValue")}
    return DexQuote("solana", "jupiter", token_in, token_out, int(amount_in), out, thr, impact_bps, fee, None, None, route,
                    f.received_ms, f.sha256, f.latency_ms, d.get("requestId"), ev)


@dataclass(frozen=True)
class SolanaFees:
    base_fee_lamports: int
    priority_micro_lamports_p50: float
    priority_micro_lamports_p90: float
    rent_token_account_lamports: int
    received_ms: int
    sha256: str
    samples: int


def solana_fees(client: PublicClient) -> SolanaFees:
    fees, f1 = _rpc(client, "solana", "getRecentPrioritizationFees", [])
    rent, _ = _rpc(client, "solana", "getMinimumBalanceForRentExemption", [TOKEN_ACCOUNT_SIZE])
    try:
        vals = sorted(float(x["prioritizationFee"]) for x in fees)
        rent = int(rent)
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedResponse("solana", "frais de priorité malformés") from exc
    if not vals or rent <= 0:
        raise SourceUnavailable("solana", "aucun frais de priorité récent")
    p90 = vals[min(len(vals) - 1, int(0.9 * len(vals)))]
    return SolanaFees(SOLANA_BASE_FEE_LAMPORTS, statistics.median(vals), p90, rent, f1.received_ms, f1.sha256, len(vals))


def solana_mint(client: PublicClient, mint: TokenId) -> dict:
    """Autorités d'émission et de gel, décimales, programme (Token-2022 : extensions de frais de transfert)."""
    res, f = _rpc(client, "solana", "getAccountInfo", [mint.address, {"encoding": "jsonParsed"}])
    try:
        value = res["value"]
        if value is None:
            return {"exists": False, "sha256": f.sha256, "received_ms": f.received_ms}
        info = value["data"]["parsed"]["info"]
        exts = [e.get("extension") for e in info.get("extensions", []) if isinstance(e, dict)]
        return {"exists": True, "program": value["owner"], "token_2022": value["owner"] == SPL_TOKEN_2022,
                "decimals": int(info["decimals"]), "supply": int(info["supply"]), "mint_authority": info.get("mintAuthority"),
                "freeze_authority": info.get("freezeAuthority"), "extensions": exts,
                "transfer_fee": "transferFeeConfig" in exts, "transfer_hook": "transferHook" in exts,
                "permanent_delegate": "permanentDelegate" in exts, "sha256": f.sha256, "received_ms": f.received_ms}
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedResponse("solana", "compte de mint malformé") from exc


def solana_concentration(client: PublicClient, mint: TokenId, supply: int) -> dict:
    res, f = _rpc(client, "solana", "getTokenLargestAccounts", [mint.address])
    try:
        amounts = sorted((int(a["amount"]) for a in res["value"]), reverse=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedResponse("solana", "plus gros comptes malformés") from exc
    top10 = sum(amounts[:10])
    return {"top10_share": top10 / supply if supply else None, "accounts": len(amounts), "sha256": f.sha256,
            "received_ms": f.received_ms, "note": "inclut les pools et les plateformes : indicatif"}


# ---------------------------------------------------------------------------------------------------------------
# Uniswap V3 (Ethereum, Base)
# ---------------------------------------------------------------------------------------------------------------
def evm_call(client: PublicClient, network: str, to: str, data: str) -> tuple[str, object]:
    res, f = _rpc(client, network, "eth_call", [{"to": to, "data": data}, "latest"])
    if not isinstance(res, str) or not res.startswith("0x"):
        raise MalformedResponse(network, "réponse eth_call malformée")
    return res, f


def uniswap_pool(client: PublicClient, network: str, a: TokenId, b: TokenId, fee: int) -> str | None:
    data = encode_call("getPool(address,address,uint24)", [("address", a.address), ("address", b.address), ("uint24", fee)])
    res, _ = evm_call(client, network, UNISWAP_V3[network]["factory"], data)
    pool = "0x" + f"{decode_words(res, 1)[0]:064x}"[-40:]
    return None if int(pool, 16) == 0 else pool


def uniswap_quote(client: PublicClient, token_in: TokenId, token_out: TokenId, amount_in: int, fee: int) -> DexQuote:
    network = token_in.network
    if network not in UNISWAP_V3 or token_out.network != network:
        raise ValueError("Uniswap V3 : tokens du même réseau EVM attendus")
    if fee not in FEE_TIERS or amount_in <= 0:
        raise ValueError("palier de frais ou montant invalide")
    pool = uniswap_pool(client, network, token_in, token_out, fee)
    if pool is None:
        raise SourceUnavailable(network, f"aucun pool Uniswap V3 officiel au palier {fee / 100:g} pb")
    data = encode_call("quoteExactInputSingle((address,address,uint256,uint24,uint160))",
                       [("address", token_in.address), ("address", token_out.address), ("uint256", amount_in), ("uint24", fee),
                        ("uint160", 0)])
    res, f = evm_call(client, network, UNISWAP_V3[network]["quoter_v2"], data)
    out, _sqrt, ticks, gas = decode_words(res, 4)
    if out <= 0:
        raise SourceUnavailable(network, "devis nul")
    return DexQuote(network, "uniswap-v3", token_in, token_out, int(amount_in), int(out), None, None, 0.0, fee / 100, int(gas),
                    ({"pool": pool, "fee_tier": fee, "ticks_crossed": ticks},), f.received_ms, f.sha256, f.latency_ms, None,
                    f.evidence() | {"pool": pool, "quoter": UNISWAP_V3[network]["quoter_v2"]})


@dataclass(frozen=True)
class EvmGas:
    network: str
    base_fee_wei: int
    priority_p50_wei: int
    priority_p90_wei: int
    l1_fee_wei_800b: int  # frais de publication sur Ethereum d'une transaction de ~800 octets (Base) ; 0 sur Ethereum
    l1_fee_wei_1500b: int
    block: int
    received_ms: int
    sha256: str

    def price_wei(self, pct: str = "p50") -> int:
        return self.base_fee_wei + (self.priority_p50_wei if pct == "p50" else self.priority_p90_wei)


def evm_gas(client: PublicClient, network: str) -> EvmGas:
    hist, f = _rpc(client, network, "eth_feeHistory", ["0x5", "latest", [50, 90]])
    try:
        base = _hex(hist["baseFeePerGas"][-1])  # base prévue pour le prochain bloc
        rewards = [[_hex(x) for x in r] for r in hist.get("reward") or []]
        p50 = int(statistics.median(r[0] for r in rewards)) if rewards else 0
        p90 = int(max(r[1] for r in rewards)) if rewards else 0
        block = _hex(hist["oldestBlock"]) + len(hist["baseFeePerGas"]) - 1
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise MalformedResponse(network, "historique des frais malformé") from exc
    l1a = l1b = 0
    if network == "base":
        for size in (800, 1500):
            res, _ = evm_call(client, network, BASE_GAS_ORACLE, encode_call("getL1FeeUpperBound(uint256)", [("uint256", size)]))
            if size == 800:
                l1a = decode_words(res, 1)[0]
            else:
                l1b = decode_words(res, 1)[0]
    return EvmGas(network, base, p50, p90, l1a, l1b, block, f.received_ms, f.sha256)


def erc20_info(client: PublicClient, token: TokenId) -> dict:
    code, f = _rpc(client, token.network, "eth_getCode", [token.address, "latest"])
    if not isinstance(code, str) or code in ("0x", "0x0"):
        return {"exists": False, "sha256": f.sha256, "received_ms": f.received_ms}
    dec, _ = evm_call(client, token.network, token.address, encode_call("decimals()", []))
    sym, _ = evm_call(client, token.network, token.address, encode_call("symbol()", []))
    sup, _ = evm_call(client, token.network, token.address, encode_call("totalSupply()", []))
    return {"exists": True, "decimals": decode_words(dec, 1)[0], "symbol": decode_string(sym), "total_supply": decode_words(sup, 1)[0],
            "code_bytes": (len(code) - 2) // 2, "sha256": f.sha256, "received_ms": f.received_ms}


def gas_cost_native(gas_units: int, gas: EvmGas, pct: str = "p50", l1: str = "800b") -> float:
    """Coût d'une transaction en unité native (ETH), publication sur Ethereum comprise sur Base."""
    wei = gas_units * gas.price_wei(pct) + (gas.l1_fee_wei_800b if l1 == "800b" else gas.l1_fee_wei_1500b)
    v = wei / 1e18
    if not math.isfinite(v) or v < 0:
        raise ValueError("coût du gaz invalide")
    return v
