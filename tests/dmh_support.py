"""Outils de test du hub : marché factice déterministe, registre de tokens, service sans réseau."""
from __future__ import annotations

from sentinelle.costs.evidence import hypothesis
from sentinelle.dmh.cex import Rules, Trade
from sentinelle.dmh.dex import DexQuote, EvmGas, SolanaFees
from sentinelle.dmh.net import PublicClient, SourceUnavailable
from sentinelle.dmh.observe import Book
from sentinelle.dmh.service import HubService
from sentinelle.dmh.tokens import TokenId, TokenInfo, TokenRegistry
from sentinelle.dmh.view import MarketView

T0 = 1_794_800_000_000
SOL = TokenId("solana", "So11111111111111111111111111111111111111112")
USDC_S = TokenId("solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
BONK = TokenId("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
USDC_B = TokenId("base", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH_B = TokenId("base", "0x4200000000000000000000000000000000000006")
USDC_E = TokenId("ethereum", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
WETH_E = TokenId("ethereum", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
FAKE_USDC_B = TokenId("base", "0x1111111111111111111111111111111111111111")


def registry(extra: list[TokenInfo] = ()) -> TokenRegistry:
    toks = [TokenInfo(SOL, "SOL", 9, "SOL", "VERIFIED"), TokenInfo(USDC_S, "USDC", 6, "USDC", "VERIFIED"),
            TokenInfo(BONK, "BONK", 5, "BONK", "VERIFIED"), TokenInfo(USDC_B, "USDC", 6, "USDC", "VERIFIED"),
            TokenInfo(WETH_B, "WETH", 18, "ETH", "VERIFIED"), TokenInfo(USDC_E, "USDC", 6, "USDC", "VERIFIED"),
            TokenInfo(WETH_E, "WETH", 18, "ETH", "VERIFIED"), *extra]
    return TokenRegistry(toks, {"sha256": "0" * 64, "status": "TEST"})


def no_network(*_a, **_k):
    raise AssertionError("aucun accès réseau dans les tests")


class FakeRisk:
    def __init__(self, unsafe: dict | None = None):
        self.unsafe = unsafe or {}

    def assess(self, token):
        if token.key in self.unsafe:
            return {"status": "UNSAFE", "flags": [self.unsafe[token.key]]}
        return {"status": "SAFE", "flags": []}


class FakeView(MarketView):
    mode = "test"

    def __init__(self, now_ms: int = T0):
        self.t = now_ms
        self.books: dict[tuple, Book] = {}
        self.rule: dict[tuple, Rules] = {}
        self.fee: dict[str, dict] = {}
        self.quotes: dict[str, object] = {}
        self.gasp: dict[str, EvmGas] = {}
        self.sol = SolanaFees(5000, 1000.0, 5000.0, 1_488_440, now_ms, "b" * 64, 150)
        self.tr: dict[tuple, list[Trade]] = {}
        self.down: set = set()

    def now_ms(self):
        return self.t

    def venues(self):
        return sorted({v for v, _ in self.books})

    def supports(self, venue, pair):
        return (venue, pair) in self.books

    def set_book(self, venue, pair, mid, *, hs_bps=1.0, qty=1e6, levels=10, age_ms=0, step=1e-8, min_notional=1.0, tick=None,
                 taker=10.0, maker=10.0, trading=True):
        h = mid * hs_bps / 1e4
        tick = tick or mid * 1e-6
        bids = [(mid - h - i * tick, qty) for i in range(levels)]
        asks = [(mid + h + i * tick, qty) for i in range(levels)]
        self.books[(venue, pair)] = Book.build(venue, pair, bids, asks, received_ms=self.t - age_ms, sha256="a" * 64)
        self.rule[(venue, pair)] = Rules(venue, pair, step, tick, 0.0, min_notional, trading)
        self.fee[venue] = {"taker": hypothesis(taker, "test", low=taker, high=taker), "maker": hypothesis(maker, "test", low=maker, high=maker)}

    def book(self, venue, pair):
        if (venue, pair) in self.down or venue in self.down:
            raise SourceUnavailable(venue, "réseau coupé (test)")
        if (venue, pair) not in self.books:
            raise SourceUnavailable(venue, "absent")
        return self.books[(venue, pair)]

    def rules(self, venue, pair):
        return self.rule[(venue, pair)]

    def trades(self, venue, pair, since_ms):
        return [t for t in self.tr.get((venue, pair), []) if t.ts_ms >= since_ms]

    def fees(self, venue):
        return self.fee[venue]

    def gas(self, network):
        if network in self.down:
            raise SourceUnavailable(network, "nœud coupé (test)")
        return self.gasp.get(network) or EvmGas(network, 5_000_000, 1_000_000, 2_000_000, 10**10, 2 * 10**10, 1, self.t, "c" * 64)

    def solana(self):
        if "solana" in self.down:
            raise SourceUnavailable("solana", "nœud coupé (test)")
        return self.sol

    def set_quote(self, network, fn):
        """fn(token_in, token_out, amount) -> amount_out (unités entières)."""
        self.quotes[network] = fn

    def dex_quotes(self, network, token_in, token_out, amount_in):
        if network in self.down:
            raise SourceUnavailable(network, "devis indisponible (test)")
        fn = self.quotes.get(network)
        if fn is None:
            raise SourceUnavailable(network, "aucun devis")
        out = int(fn(token_in, token_out, amount_in))
        route = ({"amm": "x", "label": "Whirlpool", "input": token_in.address, "output": token_out.address, "percent": 100},) \
            if network == "solana" else ({"pool": "0x" + "9" * 40, "fee_tier": 500},)
        return [DexQuote(network, "jupiter" if network == "solana" else "uniswap-v3", token_in, token_out, int(amount_in), out, None,
                         1.0, 0.0, None if network == "solana" else 5.0, None if network == "solana" else 90_000, route, self.t,
                         "d" * 64, 10.0)]


def market(view: FakeView | None = None, btc=60_000.0, eth=1_650.0, sol=70.0, usdc=0.92):
    v = view or FakeView()
    v.set_book("binance", "BTC/EUR", btc, step=1e-8, min_notional=5.0)
    v.set_book("binance", "ETH/EUR", eth, step=1e-8, min_notional=5.0)
    v.set_book("binance", "EUR/USDC", 1 / usdc, step=0.01, min_notional=5.0)
    v.set_book("kraken", "BTC/EUR", btc * 1.0001, taker=40.0, maker=25.0, min_notional=0.45)
    v.set_book("kraken", "SOL/EUR", sol, taker=40.0, maker=25.0, min_notional=0.45)
    v.set_book("kraken", "ETH/EUR", eth, taker=40.0, maker=25.0, min_notional=0.45)
    v.set_book("kraken", "USDC/EUR", usdc, taker=40.0, maker=25.0, min_notional=0.45)
    v.set_book("bitvavo", "BONK/EUR", 0.000012, taker=25.0, maker=15.0, min_notional=5.0, step=1.0)
    v.set_book("coinbase", "SOL/EUR", sol, taker=120.0, maker=60.0, min_notional=0.84)
    v.set_book("coinbase", "ETH/EUR", eth, taker=120.0, maker=60.0, min_notional=0.84)
    return v


def service(db, view, *, risk=None, reg=None, account_id=1, clock=None):
    import time
    return HubService(db, account_id, view_factory=lambda: view, registry=reg or registry(), risk=risk or FakeRisk(),
                      client=PublicClient(transport=no_network), clock=clock or time.time)


def buy(asset="BTC", n=8.0, edge=200.0, **extra):
    return {"mode": "paper", "side": "buy", "asset": asset, "notional_eur": n, "edge_bps": edge, "networks": [], **extra}
