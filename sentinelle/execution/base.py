"""Éléments communs aux brokers (simulation et réel)."""

from __future__ import annotations

from typing import Callable, Protocol

FillListener = Callable[[dict, dict], None]

OPEN_STATUSES = ("new", "open", "partially_filled")


class OrderError(Exception):
    """Ordre refusé. `code` permet au code appelant de réagir finement."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code
        self.message = message


class Broker(Protocol):
    kind: str
    account_id: int
    quote: str
    listeners: list[FillListener]

    def place_order(
        self,
        symbol: str,
        side: str,
        type: str,
        amount: float,
        price: float | None = None,
        stop_price: float | None = None,
        *,
        origin: str = "manual",
        agent: str | None = None,
        purpose: str = "manual",
        position_id: int | None = None,
        oco_group: str | None = None,
        reason: str | None = None,
    ) -> dict: ...

    def cancel_order(self, order_id: int) -> dict: ...

    def open_orders(self, symbol: str | None = None) -> list[dict]: ...

    def balances(self) -> dict[str, float]: ...

    def available(self, asset: str) -> float: ...

    def equity(self) -> tuple[float, float]: ...


def fee_in_quote(fee: float, fee_asset: str | None, base: str, quote: str, price: float) -> float:
    """Convertit des frais dans la monnaie de cotation."""
    if not fee:
        return 0.0
    if fee_asset in (None, "", quote):
        return float(fee)
    if fee_asset == base:
        return float(fee) * price
    return 0.0  # autre jeton (ex. BNB) : non converti, journalisé par l'appelant
