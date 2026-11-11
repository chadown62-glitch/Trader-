"""Deterministic, side-effect-free L2 execution estimates for paper simulation.

The caller owns the order book lifecycle and must not reuse consumed liquidity.
This calculator does not place orders, update balances or simulate queue priority.
"""
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_DOWN
from math import isfinite


class LiquidityError(ValueError):
    pass


def _d(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (DecimalException, ValueError):
        raise LiquidityError('Valeur numérique invalide') from None


def _floor(value, step):
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True)
class BookSnapshot:
    bids: tuple
    asks: tuple
    timestamp: float
    source: str

    def __post_init__(self):
        if not isinstance(self.timestamp, (int, float)) or isinstance(self.timestamp, bool) or not isfinite(self.timestamp):
            raise LiquidityError('Horodatage invalide')
        if not isinstance(self.source, str) or not self.source.strip():
            raise LiquidityError('Source manquante')
        for name in ('bids', 'asks'):
            levels = []
            try:
                for raw in getattr(self, name):
                    if len(raw) < 2:
                        raise LiquidityError('Niveau incomplet')
                    px, qty = _d(raw[0]), _d(raw[1])
                    if px <= 0 or qty <= 0:
                        raise LiquidityError('Niveau invalide')
                    levels.append((px, qty))
            except TypeError:
                raise LiquidityError('Carnet invalide') from None
            if not levels:
                raise LiquidityError('Carnet incomplet')
            prices = [px for px, _ in levels]
            if any((prices[i] <= prices[i + 1] if name == 'bids' else prices[i] >= prices[i + 1]) for i in range(len(prices) - 1)):
                raise LiquidityError('Niveaux non triés ou dupliqués')
            object.__setattr__(self, name, tuple(levels))
        if self.bids[0][0] >= self.asks[0][0]:
            raise LiquidityError('Carnet croisé ou verrouillé')


def simulate_taker(book, side, quantity, *, now, max_age, fee_rate=0, amount_step='0.00000001',
                   limit_price=None, available_quote=None, available_base=None):
    if not isinstance(book, BookSnapshot):
        raise LiquidityError('Carnet invalide')
    if not isinstance(now, (int, float)) or isinstance(now, bool) or not isfinite(now) or not isinstance(max_age, (int, float)) or not isfinite(max_age) or max_age < 0:
        raise LiquidityError('Horloge invalide')
    if book.timestamp > now or now - book.timestamp > max_age:
        raise LiquidityError('Carnet périmé ou futur')
    if side not in ('buy', 'sell'):
        raise LiquidityError('Sens invalide')
    requested, step, fee = _d(quantity), _d(amount_step), _d(fee_rate)
    if requested <= 0 or step <= 0 or not 0 <= fee < 1:
        raise LiquidityError('Quantité, pas ou frais invalides')
    limit = None if limit_price is None else _d(limit_price)
    if limit is not None and limit <= 0:
        raise LiquidityError('Prix limite invalide')
    quote = None if available_quote is None else _d(available_quote)
    base = None if available_base is None else _d(available_base)
    if (quote is not None and quote < 0) or (base is not None and base < 0):
        raise LiquidityError('Solde disponible invalide')

    remaining = _floor(requested, step)
    if remaining <= 0:
        raise LiquidityError('Quantité inférieure au pas du marché')
    levels = book.asks if side == 'buy' else book.bids
    fills = []
    for price, liquidity in levels:
        if remaining <= 0:
            break
        if limit is not None and ((side == 'buy' and price > limit) or (side == 'sell' and price < limit)):
            break
        qty = min(remaining, _floor(liquidity, step))
        if side == 'buy' and quote is not None:
            qty = min(qty, _floor(quote / (price * (1 + fee)), step))
        if side == 'sell' and base is not None:
            qty = min(qty, _floor(base, step))
        if qty <= 0:
            break
        gross = qty * price
        charged = gross * fee
        fills.append({'price': price, 'quantity': qty, 'notional': gross, 'fee': charged})
        remaining -= qty
        if side == 'buy' and quote is not None:
            quote -= gross + charged
        if side == 'sell' and base is not None:
            base -= qty

    filled = sum((f['quantity'] for f in fills), Decimal(0))
    notional = sum((f['notional'] for f in fills), Decimal(0))
    fees = sum((f['fee'] for f in fills), Decimal(0))
    return {'requested': requested, 'filled': filled, 'remaining': requested - filled,
            'status': 'filled' if filled == requested else 'partial' if filled else 'unfilled',
            'average_price': notional / filled if filled else None, 'notional': notional,
            'fee': fees, 'quote_change': -(notional + fees) if side == 'buy' else notional - fees,
            'fills': fills, 'book_timestamp': book.timestamp, 'source': book.source}


class LiquidityPool:
    """Atomically consume a shared snapshot; callers still own the cash ledger."""
    def __init__(self, book):
        import threading
        if not isinstance(book, BookSnapshot):
            raise LiquidityError('Carnet invalide')
        self._lock = threading.RLock()
        self._book = book
        self._bids, self._asks = list(book.bids), list(book.asks)

    def refresh(self, book):
        if not isinstance(book, BookSnapshot):
            raise LiquidityError('Carnet invalide')
        with self._lock:
            if book.source != self._book.source or book.timestamp <= self._book.timestamp:
                raise LiquidityError('Actualisation non monotone ou source différente')
            self._book = book
            self._bids, self._asks = list(book.bids), list(book.asks)

    def execute(self, side, quantity, **kwargs):
        with self._lock:
            if not self._bids or not self._asks:
                raise LiquidityError('Carnet épuisé : nouvelle cotation nécessaire')
            book = BookSnapshot(tuple(self._bids), tuple(self._asks), self._book.timestamp, self._book.source)
            result = simulate_taker(book, side, quantity, **kwargs)
            levels = self._asks if side == 'buy' else self._bids
            for fill in result['fills']:
                index = next(i for i, (price, _) in enumerate(levels) if price == fill['price'])
                price, available = levels[index]
                if price != fill['price'] or fill['quantity'] > available:
                    raise LiquidityError('Incohérence de liquidité')
                remaining = available - fill['quantity']
                if remaining == 0:
                    levels.pop(index)
                else:
                    levels[index] = (price, remaining)
            return result
