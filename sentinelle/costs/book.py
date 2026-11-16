"""Carnets d'ordres observés (sondes publiques datées) : écart, profondeur, coût d'un ordre au marché.

Réutilise le calculateur d'exécution existant du projet (``sentinelle.execution.liquidity``) : le coût
d'un ordre au marché est obtenu en « remontant » le carnet observé, pas recalculé autrement.
Les carnets publics de Binance n'ont pas d'heure de plateforme : seule l'heure de réception est
connue (qualité ``receipt_only``), ce qui est consigné avec chaque mesure.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from ..execution.liquidity import BookSnapshot, LiquidityError, simulate_taker
from .tape import Tape, quote_at


@dataclass(frozen=True)
class BookMeasure:
    symbol: str
    received_ms: int
    sha256: str
    bid: float
    ask: float
    half_spread_bps: float
    depth_quote_top: float  # montant disponible au meilleur prix vendeur
    depth_quote_10bps: float  # montant vendeur à moins de 10 pb du milieu
    impact_bps: dict  # montant -> coût au-delà du demi-écart (pb), None si le carnet ne suffit pas

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def snapshot(raw: bytes, received_ms: int, source: str = "binance depth (public, receipt_only)") -> BookSnapshot:
    data = json.loads(raw)
    return BookSnapshot(tuple(tuple(x) for x in data["bids"]), tuple(tuple(x) for x in data["asks"]), received_ms / 1000, source)


def measure(raw: bytes, symbol: str, received_ms: int, sizes_quote: tuple[float, ...], *, step: str = "0.00000001") -> BookMeasure:
    book = snapshot(raw, received_ms)
    bid, ask = float(book.bids[0][0]), float(book.asks[0][0])
    mid = (bid + ask) / 2
    top = float(book.asks[0][0] * book.asks[0][1])
    near = float(sum(p * q for p, q in book.asks if float(p) <= mid * 1.001))
    impact = {}
    for size in sizes_quote:
        qty = (Decimal(str(size)) / Decimal(str(ask)) / Decimal(step)).to_integral_value(rounding="ROUND_FLOOR") * Decimal(step)
        if qty <= 0:
            impact[str(size)] = None
            continue
        try:
            r = simulate_taker(book, "buy", qty, now=book.timestamp, max_age=1.0, fee_rate=0, amount_step=step)
        except LiquidityError:
            impact[str(size)] = None
            continue
        if r["status"] != "filled":
            impact[str(size)] = None
            continue
        impact[str(size)] = float((float(r["average_price"]) - ask) / mid * 1e4)
    return BookMeasure(symbol, received_ms, hashlib.sha256(raw).hexdigest(), bid, ask, (ask - bid) / 2 / mid * 1e4, top, near, impact)


def estimator_error(book: BookMeasure, tape: Tape, *, lookback_ms: int = 60_000) -> dict | None:
    """Écart entre l'estimation par le ruban (juste avant la réception du carnet) et le carnet observé."""
    q = quote_at(tape, book.received_ms, lookback_ms=lookback_ms)
    if q is None:
        return None
    return {"symbol": book.symbol, "received_ms": book.received_ms, "observed_half_spread_bps": book.half_spread_bps,
            "estimated_half_spread_bps": q.half_spread_bps, "error_bps": q.half_spread_bps - book.half_spread_bps,
            "mid_error_bps": (q.mid - (book.bid + book.ask) / 2) / ((book.bid + book.ask) / 2) * 1e4,
            "quote_age_ms": max(q.bid_age_ms, q.ask_age_ms)}
