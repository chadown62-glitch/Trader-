"""Tailles réelles d'un ordre fictif : pas de quantité, montant minimal, actif des frais, reliquats.

Pour un portefeuille de 30 €, ces contraintes ne sont pas des détails :

- la quantité est arrondie au pas inférieur : la taille réelle s'écarte de la taille voulue ;
- sous le montant minimal, l'ordre est refusé, ou relevé au minimum si le risque le permet ;
- si la commission d'un achat est prélevée dans l'actif acheté (règle de Binance sans BNB), la quantité
  détenue n'est plus un multiple du pas : à la revente, il reste un **reliquat** invendable (au plus un
  pas). Il n'est pas perdu (il reste détenu, à sa valeur de marché), mais il est immobilisé jusqu'à un
  prochain achat du même actif ou une conversion des petits soldes (frais hypothétiques).

Tout est calculé en ``Decimal`` pour reproduire l'arrondi de la plateforme.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

FEE_IN = ("received", "platform_token", "quote")


def floor_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_CEILING) * step


@dataclass(frozen=True)
class RoundTrip:
    target_quote: Decimal  # montant voulu
    status: str  # ok | below_minimum
    buy_qty: Decimal  # quantité achetée (multiple du pas)
    buy_notional: Decimal
    sizing_error: float  # (montant réel - montant voulu) / montant voulu
    held_qty: Decimal  # détenu après la commission d'achat
    sellable_qty: Decimal  # vendable (multiple du pas, au-dessus des minimums)
    dust_qty: Decimal  # reliquat invendable
    dust_value: Decimal  # au prix d'achat, en devise de cotation
    dust_share: float  # part du montant acheté immobilisée

    def to_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


def round_trip(price: Decimal, target_quote: Decimal, *, step: Decimal, min_notional: Decimal, min_qty: Decimal = Decimal(0),
               fee_rate: Decimal, fee_in: str = "received", raise_to_minimum: bool = True) -> RoundTrip:
    """Achat de ``target_quote`` (devise de cotation) au prix ``price``, puis revente de tout ce qui peut l'être."""
    if fee_in not in FEE_IN:
        raise ValueError(fee_in)
    if price <= 0 or target_quote <= 0 or step <= 0 or not 0 <= fee_rate < 1:
        raise ValueError("Paramètres invalides")
    qty = floor_to(target_quote / price, step)
    if qty * price < min_notional or qty < min_qty:
        if not raise_to_minimum:
            return RoundTrip(target_quote, "below_minimum", Decimal(0), Decimal(0), -1.0, Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0.0)
        qty = max(ceil_to(min_notional / price, step), ceil_to(min_qty, step))
    notional = qty * price
    held = qty * (1 - fee_rate) if fee_in == "received" else qty
    sellable = floor_to(held, step)
    if sellable * price < min_notional or sellable < min_qty:
        sellable = Decimal(0)  # tout le solde est sous les minimums : rien n'est vendable
    dust = held - sellable
    return RoundTrip(target_quote, "ok", qty, notional, float((notional - target_quote) / target_quote), held, sellable, dust,
                     dust * price, float(dust * price / notional) if notional else 0.0)


def step_value(price: Decimal, step: Decimal) -> Decimal:
    """Valeur d'un pas de quantité : ordre de grandeur du reliquat laissé par un aller-retour."""
    return price * step
