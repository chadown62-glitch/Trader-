"""Connecteurs PUBLICS des plateformes centralisées (marché au comptant uniquement, aucun compte).

Chaque connecteur lit : le carnet d'ordres, les règles du marché (pas de quantité, pas de prix, minimum) et les
transactions publiques récentes (pour juger, sans rien inventer, si un ordre à cours limité aurait été rempli).
Aucun connecteur ne connaît de point d'accès privé : le client réseau les refuse de toute façon.

Les commissions ne sont publiées par aucune de ces interfaces publiques : elles sont des HYPOTHÈSES datées,
avec une fourchette, à confirmer par l'utilisateur ; le routeur vérifie que son choix résiste à la fourchette.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ..costs.evidence import Value, hypothesis
from .net import MalformedResponse, PublicClient, SourceUnavailable
from .observe import Book, InvalidData

FEE_NOTE = "grille publique non lisible par une interface publique dans cet environnement ; niveau le plus bas (petit volume)"


def _fee(value: float, low: float, high: float, source: str) -> Value:
    return hypothesis(value, FEE_NOTE, low=low, high=high, source=source)


@dataclass(frozen=True)
class Rules:
    venue: str
    pair: str
    step: float  # pas de quantité (actif de base)
    tick: float  # pas de prix
    min_qty: float
    min_notional: float  # en devise de cotation
    trading: bool
    evidence: dict = field(default_factory=dict, compare=False)

    def floor_qty(self, qty: float) -> float:
        if self.step <= 0:
            return qty
        return math.floor(qty / self.step + 1e-9) * self.step


@dataclass(frozen=True)
class Trade:
    ts_ms: int
    price: float
    qty: float


class Venue:
    name = ""
    host = ""
    fees: dict[str, Value] = {}
    pairs: tuple[str, ...] = ()

    def __init__(self, client: PublicClient):
        self.client = client

    def symbol(self, pair: str) -> str:
        raise NotImplementedError

    def supports(self, pair: str) -> bool:
        return pair in self.pairs

    def _book(self, pair: str, fetched, bids, asks, source_ms=None) -> Book:
        try:
            return Book.build(self.name, pair, bids, asks, received_ms=fetched.received_ms, source_ms=source_ms,
                              sha256=fetched.sha256, latency_ms=fetched.latency_ms, evidence=fetched.evidence())
        except InvalidData as exc:
            raise MalformedResponse(self.host, str(exc)) from exc

    def book(self, pair: str, depth: int = 50) -> Book:
        raise NotImplementedError

    def rules(self, pair: str) -> Rules:
        raise NotImplementedError

    def trades(self, pair: str, since_ms: int) -> list[Trade]:
        raise NotImplementedError

    def server_time_ms(self) -> tuple[int, int, float] | None:
        """(heure du serveur, heure locale de réception, latence) ; None si la plateforme ne la publie pas."""
        return None


def epoch_ms(value) -> int:
    """Heure en ms quelle que soit l'unité de la source (s, ms, µs, ns) ; refuse une valeur absurde."""
    v = float(value)
    for scale in (1e-6, 1e-3, 1.0, 1e3):  # ns, µs, ms, s
        ms = v * scale
        if 631_152_000_000 <= ms <= 4_102_444_800_000:
            return int(ms)
    raise ValueError("horodatage hors de toute unité plausible")


def _num(x) -> float:
    v = float(x)
    if not math.isfinite(v):
        raise ValueError("non fini")
    return v


class Binance(Venue):
    name, host = "binance", "data-api.binance.vision"
    fees = {"taker": _fee(10.0, 7.5, 10.0, "https://www.binance.com/en/fee/schedule"),
            "maker": _fee(10.0, 7.5, 10.0, "https://www.binance.com/en/fee/schedule")}
    pairs = ("BTC/EUR", "ETH/EUR", "SOL/EUR", "BTC/USDT", "ETH/USDT", "SOL/USDT", "EUR/USDT", "BTC/USDC", "ETH/USDC",
             "SOL/USDC", "USDC/USDT", "EUR/USDC", "DOGE/USDT", "PEPE/USDT", "BONK/USDT", "WIF/USDT")

    def symbol(self, pair):
        return pair.replace("/", "")

    def book(self, pair, depth=50):
        f = self.client.get(self.host, "/api/v3/depth", {"symbol": self.symbol(pair), "limit": min(depth, 100)})
        d = f.json()
        if not isinstance(d, dict) or "bids" not in d:
            raise MalformedResponse(self.host, "carnet malformé")
        return self._book(pair, f, d["bids"], d["asks"])

    def rules(self, pair):
        f = self.client.get(self.host, "/api/v3/exchangeInfo", {"symbol": self.symbol(pair)})
        try:
            s = f.json()["symbols"][0]
            flt = {x["filterType"]: x for x in s["filters"]}
            notional = flt.get("NOTIONAL") or flt.get("MIN_NOTIONAL") or {}
            return Rules(self.name, pair, _num(flt["LOT_SIZE"]["stepSize"]), _num(flt["PRICE_FILTER"]["tickSize"]),
                         _num(flt["LOT_SIZE"]["minQty"]), _num(notional.get("minNotional", 0)), s.get("status") == "TRADING",
                         f.evidence())
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, f"règles de marché malformées ({exc.__class__.__name__})") from exc

    def server_time_ms(self):
        f = self.client.get(self.host, "/api/v3/time")
        return epoch_ms(f.json()["serverTime"]), f.received_ms, f.latency_ms

    def trades(self, pair, since_ms):
        f = self.client.get(self.host, "/api/v3/aggTrades", {"symbol": self.symbol(pair), "startTime": int(since_ms), "limit": 1000})
        try:
            return [Trade(epoch_ms(t["T"]), _num(t["p"]), _num(t["q"])) for t in f.json()]
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "transactions malformées") from exc


class Kraken(Venue):
    name, host = "kraken", "api.kraken.com"
    fees = {"taker": _fee(40.0, 25.0, 40.0, "https://www.kraken.com/features/fee-schedule"),
            "maker": _fee(25.0, 0.0, 25.0, "https://www.kraken.com/features/fee-schedule")}
    pairs = ("BTC/EUR", "ETH/EUR", "SOL/EUR", "USDT/EUR", "USDC/EUR", "BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/EUR", "PEPE/EUR", "BONK/EUR")
    _codes = {"BTC": "XBT", "DOGE": "XDG"}

    def symbol(self, pair):
        b, q = pair.split("/")
        return self._codes.get(b, b) + q

    def _result(self, f):
        d = f.json()
        if not isinstance(d, dict) or d.get("error"):
            raise SourceUnavailable(self.host, f"erreur Kraken : {str(d.get('error') if isinstance(d, dict) else d)[:120]}")
        res = d.get("result") or {}
        rows = [v for k, v in res.items() if k != "last"]
        if len(rows) != 1:
            raise MalformedResponse(self.host, "réponse Kraken ambiguë")
        return rows[0], res

    def book(self, pair, depth=50):
        f = self.client.get(self.host, "/0/public/Depth", {"pair": self.symbol(pair), "count": min(depth, 500)})
        r, _ = self._result(f)
        return self._book(pair, f, [x[:2] for x in r.get("bids", [])], [x[:2] for x in r.get("asks", [])])

    def rules(self, pair):
        f = self.client.get(self.host, "/0/public/AssetPairs", {"pair": self.symbol(pair)})
        r, _ = self._result(f)
        try:
            return Rules(self.name, pair, 10 ** -int(r["lot_decimals"]), _num(r.get("tick_size") or 10 ** -int(r["pair_decimals"])),
                         _num(r.get("ordermin", 0)), _num(r.get("costmin", 0)), r.get("status", "online") == "online", f.evidence())
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "règles de marché malformées") from exc

    def server_time_ms(self):
        f = self.client.get(self.host, "/0/public/Time")
        r = f.json().get("result") or {}
        return epoch_ms(r["unixtime"]), f.received_ms, f.latency_ms

    def trades(self, pair, since_ms):
        f = self.client.get(self.host, "/0/public/Trades", {"pair": self.symbol(pair), "since": int(since_ms) * 1_000_000})
        r, _ = self._result(f)
        try:
            return [Trade(int(float(t[2]) * 1000), _num(t[0]), _num(t[1])) for t in r]
        except (IndexError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "transactions malformées") from exc


def _iso_ms(text: str) -> int:
    t = text.replace("Z", "+00:00")
    if "." in t:
        head, frac = t.split(".", 1)
        digits = "".join(ch for ch in frac if ch.isdigit())
        tz = frac[len(digits):]
        t = f"{head}.{digits[:6]}{tz}"
    return int(datetime.fromisoformat(t).timestamp() * 1000)


class Coinbase(Venue):
    name, host = "coinbase", "api.exchange.coinbase.com"
    fees = {"taker": _fee(120.0, 60.0, 120.0, "https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/advanced-trade-fees"),
            "maker": _fee(60.0, 40.0, 60.0, "https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/advanced-trade-fees")}
    pairs = ("BTC/EUR", "ETH/EUR", "SOL/EUR", "USDC/EUR", "USDT/EUR", "DOGE/EUR")  # BTC-USDC : HTTP 404 (USDC fondu dans USD)

    def symbol(self, pair):
        return pair.replace("/", "-")

    def book(self, pair, depth=50):
        f = self.client.get(self.host, f"/products/{self.symbol(pair)}/book", {"level": 2})
        d = f.json()
        if not isinstance(d, dict) or "bids" not in d:
            raise MalformedResponse(self.host, "carnet malformé")
        if d.get("auction_mode"):
            raise SourceUnavailable(self.host, "marché en enchère : pas de cotation continue")
        src = _iso_ms(d["time"]) if d.get("time") else None
        return self._book(pair, f, [x[:2] for x in d["bids"][:depth]], [x[:2] for x in d["asks"][:depth]], src)

    def rules(self, pair):
        f = self.client.get(self.host, f"/products/{self.symbol(pair)}")
        d = f.json()
        try:
            ok = d.get("status") == "online" and not d.get("trading_disabled") and not d.get("cancel_only") and not d.get("limit_only")
            return Rules(self.name, pair, _num(d["base_increment"]), _num(d["quote_increment"]), 0.0, _num(d.get("min_market_funds", 0)),
                         bool(ok), f.evidence())
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise MalformedResponse(self.host, "règles de marché malformées") from exc

    def server_time_ms(self):
        f = self.client.get(self.host, "/time")
        return epoch_ms(f.json()["epoch"]), f.received_ms, f.latency_ms

    def trades(self, pair, since_ms):
        f = self.client.get(self.host, f"/products/{self.symbol(pair)}/trades", {"limit": 1000})
        try:
            rows = [Trade(_iso_ms(t["time"]), _num(t["price"]), _num(t["size"])) for t in f.json()]
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "transactions malformées") from exc
        return sorted((t for t in rows if t.ts_ms >= since_ms), key=lambda t: t.ts_ms)


class Bitstamp(Venue):
    name, host = "bitstamp", "www.bitstamp.net"
    fees = {"taker": _fee(40.0, 30.0, 50.0, "https://www.bitstamp.net/fee-schedule/"),
            "maker": _fee(30.0, 0.0, 40.0, "https://www.bitstamp.net/fee-schedule/")}
    pairs = ("BTC/EUR", "ETH/EUR", "SOL/EUR", "USDC/EUR", "USDT/EUR", "BTC/USDT", "ETH/USDT")

    def symbol(self, pair):
        return pair.replace("/", "").lower()

    def book(self, pair, depth=50):
        f = self.client.get(self.host, f"/api/v2/order_book/{self.symbol(pair)}/")
        d = f.json()
        if not isinstance(d, dict) or "bids" not in d:
            raise MalformedResponse(self.host, "carnet malformé")
        src = epoch_ms(d["microtimestamp"]) if d.get("microtimestamp") else None
        return self._book(pair, f, d["bids"][:depth], d["asks"][:depth], src)

    def rules(self, pair):
        f = self.client.get(self.host, "/api/v2/trading-pairs-info/")
        try:
            row = next(r for r in f.json() if r["url_symbol"] == self.symbol(pair))
            minimum = _num(str(row.get("minimum_order", "0")).split()[0])
            return Rules(self.name, pair, 10 ** -int(row["base_decimals"]), 10 ** -int(row["counter_decimals"]), 0.0, minimum,
                         row.get("trading") == "Enabled", f.evidence())
        except (StopIteration, KeyError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "règles de marché malformées") from exc

    def trades(self, pair, since_ms):
        f = self.client.get(self.host, f"/api/v2/transactions/{self.symbol(pair)}/", {"time": "hour"})
        try:
            rows = [Trade(int(t["date"]) * 1000, _num(t["price"]), _num(t["amount"])) for t in f.json()]
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "transactions malformées") from exc
        return sorted((t for t in rows if t.ts_ms >= since_ms), key=lambda t: t.ts_ms)


class Bitvavo(Venue):
    name, host = "bitvavo", "api.bitvavo.com"
    fees = {"taker": _fee(25.0, 15.0, 25.0, "https://bitvavo.com/en/fees"),
            "maker": _fee(15.0, 0.0, 15.0, "https://bitvavo.com/en/fees")}
    pairs = ("BTC/EUR", "ETH/EUR", "SOL/EUR", "USDC/EUR", "DOGE/EUR", "PEPE/EUR", "BONK/EUR", "WIF/EUR", "SHIB/EUR")  # USDT-EUR : HTTP 400

    def symbol(self, pair):
        return pair.replace("/", "-")

    def book(self, pair, depth=50):
        f = self.client.get(self.host, f"/v2/{self.symbol(pair)}/book", {"depth": min(depth, 1000)})
        d = f.json()
        if not isinstance(d, dict) or "bids" not in d:
            raise MalformedResponse(self.host, "carnet malformé")
        try:
            src = epoch_ms(d["timestamp"]) if d.get("timestamp") is not None else None  # nanosecondes chez Bitvavo
        except (TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "horodatage du carnet illisible") from exc
        return self._book(pair, f, [x[:2] for x in d["bids"]], [x[:2] for x in d["asks"]], src)

    def rules(self, pair):
        f = self.client.get(self.host, "/v2/markets", {"market": self.symbol(pair)})
        d = f.json()
        try:
            qd = int(d.get("quantityDecimals", 8))
            return Rules(self.name, pair, 10 ** -qd, _num(d.get("tickSize") or 0), _num(d.get("minOrderInBaseAsset", 0)), _num(d.get("minOrderInQuoteAsset", 0)),
                         d.get("status") == "trading", f.evidence())
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise MalformedResponse(self.host, "règles de marché malformées") from exc

    def server_time_ms(self):
        f = self.client.get(self.host, "/v2/time")
        return epoch_ms(f.json()["time"]), f.received_ms, f.latency_ms

    def trades(self, pair, since_ms):
        f = self.client.get(self.host, f"/v2/{self.symbol(pair)}/trades", {"limit": 500})  # « start » refusé (HTTP 400)
        try:
            rows = [Trade(epoch_ms(t["timestamp"]), _num(t["price"]), _num(t["amount"])) for t in f.json()]
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedResponse(self.host, "transactions malformées") from exc
        return sorted((t for t in rows if t.ts_ms >= since_ms), key=lambda t: t.ts_ms)


VENUES = (Binance, Kraken, Coinbase, Bitstamp, Bitvavo)


def connectors(client: PublicClient) -> dict[str, Venue]:
    return {cls.name: cls(client) for cls in VENUES}
