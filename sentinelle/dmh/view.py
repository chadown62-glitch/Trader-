"""Vue de marché : une seule interface pour les données en direct, rejouées ou de test.

- `LiveView` : lectures publiques à la demande (aucun service permanent), avec un cache court par décision ;
- `ReplayView` : rejoue une capture archivée (`tools/dmh_capture.py`) : mêmes données, mêmes résultats, et les
  exécutions qui en découlent sont marquées « reconstituées », jamais confondues avec une observation prospective ;
- toute donnée absente lève `SourceUnavailable` : rien n'est interpolé ni inventé.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from typing import Callable

from .cex import Rules, Trade, Venue, connectors
from .dex import DexQuote, EvmGas, SolanaFees, evm_gas, jupiter_quote, solana_fees, uniswap_quote, FEE_TIERS
from .net import PublicClient, SourceUnavailable
from .observe import Book, Consensus, consensus
from .tokens import TokenId

STABLES = ("USDC", "USDT")
REFERENCE_MAX_AGE_MS = 30_000


class MarketView:
    mode = "abstract"

    def now_ms(self) -> int:
        raise NotImplementedError

    def venues(self) -> list[str]:
        raise NotImplementedError

    def supports(self, venue: str, pair: str) -> bool:
        raise NotImplementedError

    def book(self, venue: str, pair: str) -> Book:
        raise NotImplementedError

    def rules(self, venue: str, pair: str) -> Rules:
        raise NotImplementedError

    def trades(self, venue: str, pair: str, since_ms: int) -> list[Trade]:
        raise NotImplementedError

    def dex_quotes(self, network: str, token_in: TokenId, token_out: TokenId, amount_in: int) -> list[DexQuote]:
        raise NotImplementedError

    def gas(self, network: str) -> EvmGas:
        raise NotImplementedError

    def solana(self) -> SolanaFees:
        raise NotImplementedError

    def fees(self, venue: str) -> dict:
        raise NotImplementedError

    # -- prix de référence en euros : consensus des plateformes centralisées -------------------------------------
    def books_for(self, pair: str) -> list[Book]:
        out = []
        for v in self.venues():
            if self.supports(v, pair):
                try:
                    out.append(self.book(v, pair))
                except SourceUnavailable:
                    continue
        return out

    def eur_reference(self, asset: str) -> tuple[float | None, Consensus | None]:
        """Prix médian en euros d'un actif sur les plateformes fraîches et cohérentes ; None si indisponible."""
        if asset == "EUR":
            return 1.0, None
        now = self.now_ms()
        # prix de référence (valorisation, comparaison) : 30 s tolérées ; les étapes exécutées gardent la limite stricte
        c = consensus(f"{asset}/EUR", self.books_for(f"{asset}/EUR"), now, max_age_ms=REFERENCE_MAX_AGE_MS)
        if c.status in ("OK", "SOURCE_UNIQUE") and c.usable:
            return statistics_median([c.sources[v] for v in c.usable]), c
        if asset in STABLES or asset in ("BTC", "ETH", "SOL"):
            # EUR/USDT inversé (Binance) comme seconde voie pour les stables
            inv = consensus("EUR/USDT", self.books_for("EUR/USDT"), now, max_age_ms=REFERENCE_MAX_AGE_MS)
            if asset == "USDT" and inv.usable:
                return 1.0 / statistics_median([inv.sources[v] for v in inv.usable]), inv
        return None, c


def statistics_median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


class LiveView(MarketView):
    mode = "prospective"

    def __init__(self, client: PublicClient | None = None, *, clock: Callable[[], float] = time.time,
                 ttl_ms: dict | None = None, recorder: dict | None = None):
        self.client = client or PublicClient()
        self.clock = clock
        self.cx: dict[str, Venue] = connectors(self.client)
        # cache d'une décision ; la route retenue est relue (refresh) avant le contrôle de sécurité et avant l'exécution
        self.ttl = {"book": 20_000, "rules": 3_600_000, "gas": 20_000, "solana": 20_000, "dex": 8_000} | (ttl_ms or {})
        self._cache: dict[tuple, tuple[int, object]] = {}
        self._lock = threading.Lock()
        self.recorder = recorder  # capture facultative de tout ce qui est lu (rejeu reproductible)

    def now_ms(self):
        return int(self.clock() * 1000)

    def venues(self):
        return list(self.cx)

    def supports(self, venue, pair):
        return venue in self.cx and self.cx[venue].supports(pair)

    def _cached(self, kind: str, key: tuple, fn):
        now = self.now_ms()
        with self._lock:
            hit = self._cache.get((kind, *key))
            if hit and now - hit[0] <= self.ttl[kind]:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[(kind, *key)] = (now, value)
        if self.recorder is not None:
            self.recorder.setdefault(kind, {})[json.dumps(key, default=str)] = _record(value)
        return value

    def prefetch(self, pairs) -> None:
        """Lit en parallèle les carnets nécessaires (une file par plateforme : les limites de débit restent respectées)."""
        from concurrent.futures import ThreadPoolExecutor
        jobs = [(v, p) for v in self.venues() for p in dict.fromkeys(pairs) if self.supports(v, p)]
        by_venue: dict[str, list[str]] = {}
        for v, p in jobs:
            by_venue.setdefault(v, []).append(p)

        def run(v):
            for p in by_venue[v]:
                try:
                    self.book(v, p)
                    self.rules(v, p)
                except Exception:
                    continue  # l'absence sera constatée (et motivée) au moment du calcul de la route
        with ThreadPoolExecutor(max_workers=max(1, len(by_venue))) as ex:
            list(ex.map(run, list(by_venue)))

    def refresh(self) -> None:
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if k[0] == "rules"}

    def book(self, venue, pair):
        if not self.supports(venue, pair):
            raise SourceUnavailable(venue, f"paire {pair} non cotée")
        return self._cached("book", (venue, pair), lambda: self.cx[venue].book(pair))

    def rules(self, venue, pair):
        return self._cached("rules", (venue, pair), lambda: self.cx[venue].rules(pair))

    def trades(self, venue, pair, since_ms):
        return self.cx[venue].trades(pair, since_ms)

    def fees(self, venue):
        return self.cx[venue].fees

    def gas(self, network):
        return self._cached("gas", (network,), lambda: evm_gas(self.client, network))

    def solana(self):
        return self._cached("solana", ("solana",), lambda: solana_fees(self.client))

    def dex_quotes(self, network, token_in, token_out, amount_in):
        def fetch():
            if network == "solana":
                return [jupiter_quote(self.client, token_in, token_out, amount_in)]
            out = []
            for fee in FEE_TIERS:
                try:
                    out.append(uniswap_quote(self.client, token_in, token_out, amount_in, fee))
                except SourceUnavailable:
                    continue
            if not out:
                raise SourceUnavailable(network, "aucun pool Uniswap V3 officiel pour cette paire")
            return out
        return self._cached("dex", (network, token_in.key, token_out.key, int(amount_in)), fetch)


def _record(value):
    if isinstance(value, list):
        return [_record(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        d = asdict(value)
        for k, v in list(d.items()):
            if isinstance(v, TokenId):
                d[k] = v.key
        return {"__type__": type(value).__name__, **d}
    return value


def _revive(obj):
    if isinstance(obj, list):
        return [_revive(x) for x in obj]
    if not isinstance(obj, dict) or "__type__" not in obj:
        return obj
    d = {k: v for k, v in obj.items() if k != "__type__"}
    t = obj["__type__"]
    if t == "Book":
        d["bids"] = tuple(tuple(x) for x in d["bids"])
        d["asks"] = tuple(tuple(x) for x in d["asks"])
        return Book(**d)
    if t == "Rules":
        return Rules(**d)
    if t == "DexQuote":
        tin, tout = d["token_in"], d["token_out"]
        d["token_in"] = TokenId.parse(tin["network"] + ":" + tin["address"]) if isinstance(tin, dict) else TokenId.parse(tin)
        d["token_out"] = TokenId.parse(tout["network"] + ":" + tout["address"]) if isinstance(tout, dict) else TokenId.parse(tout)
        d["route"] = tuple(d["route"])
        return DexQuote(**d)
    if t == "EvmGas":
        return EvmGas(**d)
    if t == "SolanaFees":
        return SolanaFees(**d)
    return d


def _at(obj, t0: int):
    """Date une donnée rejouée à l'instant du rejeu ; l'heure réelle de réception reste dans sa preuve."""
    from dataclasses import replace
    if isinstance(obj, list):
        return [_at(x, t0) for x in obj]
    if isinstance(obj, Book):
        return replace(obj, received_ms=t0, source_ms=None,
                       evidence=dict(obj.evidence or {}) | {"captured_received_ms": obj.received_ms, "captured_source_ms": obj.source_ms})
    if isinstance(obj, (DexQuote, EvmGas, SolanaFees)):
        return replace(obj, received_ms=t0)
    return obj


class ReplayView(MarketView):
    """Rejoue une capture : `data[kind][json(clé)]`.

    La capture est traitée comme UN SEUL INSTANT (son heure de fin) : chaque donnée y est datée de cet instant, et
    son heure réelle de réception est conservée dans sa preuve. L'écart réel entre deux lectures est au plus la durée
    de la capture, publiée avec elle.
    """
    mode = "replay"

    def __init__(self, capture: dict, *, fees: dict[str, dict] | None = None, shift_ms: int = 0):
        self.capture = capture
        self.data = capture["data"]
        self.t0 = int(capture.get("finished_ms") or capture["captured_ms"]) + shift_ms
        from .cex import VENUES
        self._venues = {cls.name: cls for cls in VENUES}
        self._fees = fees

    def now_ms(self):
        return self.t0

    def venues(self):
        return list(self._venues)

    def supports(self, venue, pair):
        return json.dumps([venue, pair]) in self.data.get("book", {})

    def _get(self, kind, key):
        raw = self.data.get(kind, {}).get(json.dumps(key, default=str))
        if raw is None:
            raise SourceUnavailable(str(key[0]), f"absent de la capture ({kind})")
        if isinstance(raw, dict) and raw.get("__error__"):
            raise SourceUnavailable(str(key[0]), raw["__error__"])
        return _at(_revive(raw), self.t0)

    def book(self, venue, pair):
        return self._get("book", [venue, pair])

    def rules(self, venue, pair):
        return self._get("rules", [venue, pair])

    def trades(self, venue, pair, since_ms):
        rows = self.data.get("trades", {}).get(json.dumps([venue, pair]), [])
        return [Trade(**t) for t in rows if t["ts_ms"] >= since_ms]

    def fees(self, venue):
        if self._fees and venue in self._fees:
            return self._fees[venue]
        return self._venues[venue].fees

    def gas(self, network):
        return self._get("gas", [network])

    def solana(self):
        return self._get("solana", ["solana"])

    def dex_quotes(self, network, token_in, token_out, amount_in):
        return self._get("dex", [network, token_in.key, token_out.key, int(amount_in)])
