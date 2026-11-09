"""Caractéristiques d'une paire (pas de quantité, de prix, minimums)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


def _round_step(x: float, step: float, mode) -> float:
    if step is None or step <= 0:
        return float(x)
    d = Decimal(repr(float(x)))
    s = Decimal(repr(float(step)))
    return float((d / s).to_integral_value(rounding=mode) * s)


@dataclass(frozen=True)
class MarketInfo:
    symbol: str
    base: str
    quote: str
    amount_step: float = 1e-8
    price_step: float = 0.01
    min_amount: float = 0.0
    min_notional: float = 5.0
    taker_fee: float = 0.001
    maker_fee: float = 0.001
    order_types: tuple = field(default_factory=tuple)

    def floor_amount(self, amount: float) -> float:
        """Arrondit une quantité vers le bas au pas autorisé (jamais plus que demandé)."""
        return max(0.0, _round_step(amount, self.amount_step, ROUND_DOWN))

    def round_price(self, price: float) -> float:
        return _round_step(price, self.price_step, ROUND_HALF_UP)

    def validate(self, amount: float, price: float) -> str | None:
        """Renvoie un message d'erreur si l'ordre ne respecte pas les règles de la paire."""
        if amount <= 0:
            return "La quantité doit être positive"
        if self.min_amount and amount + 1e-15 < self.min_amount:
            return f"Quantité minimale : {self.min_amount:g} {self.base}"
        notional = amount * price
        if self.min_notional and notional + 1e-9 < self.min_notional:
            return f"Montant minimal par ordre : {self.min_notional:g} {self.quote}"
        return None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "quote": self.quote,
            "amount_step": self.amount_step,
            "price_step": self.price_step,
            "min_amount": self.min_amount,
            "min_notional": self.min_notional,
            "taker_fee": self.taker_fee,
            "maker_fee": self.maker_fee,
        }


def generic_market(symbol: str, price_hint: float | None = None) -> MarketInfo:
    """Caractéristiques par défaut (marché simulé)."""
    base, quote = symbol.split("/")
    price_step = 0.01
    if price_hint is not None and price_hint < 1:
        price_step = 0.0001
    return MarketInfo(
        symbol=symbol,
        base=base,
        quote=quote,
        amount_step=1e-6,
        price_step=price_step,
        min_amount=1e-6,
        min_notional=5.0,
        order_types=("MARKET", "LIMIT", "STOP_LOSS"),
    )
