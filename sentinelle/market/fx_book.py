"""Conservative, offline FX quote validation; no order or network integration."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite


class FXError(ValueError):
    pass


def _decimal(value):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FXError('Montant ou cours invalide') from None
    if not d.is_finite():
        raise FXError('Montant ou cours non fini')
    return d


@dataclass(frozen=True)
class FXQuote:
    base: str
    quote: str
    bid: Decimal
    ask: Decimal
    timestamp: float
    source: str
    bid_depth: Decimal | None = None
    ask_depth: Decimal | None = None

    def __post_init__(self):
        currencies = {'EUR', 'USD', 'USDC', 'USDT'}
        if self.base not in currencies or self.quote not in currencies or self.base == self.quote:
            raise FXError('Paire de devises invalide')
        bid, ask = _decimal(self.bid), _decimal(self.ask)
        if bid <= 0 or ask < bid:
            raise FXError('Fourchette de cours invalide')
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)) or not isfinite(self.timestamp) or not isinstance(self.source, str) or not self.source.strip():
            raise FXError('Date ou source manquante')
        object.__setattr__(self, 'bid', bid)
        object.__setattr__(self, 'ask', ask)
        for field in ('bid_depth', 'ask_depth'):
            value = getattr(self, field)
            if value is not None:
                depth = _decimal(value)
                if depth <= 0:
                    raise FXError('Profondeur de marché invalide')
                object.__setattr__(self, field, depth)


class FXBook:
    def __init__(self, quotes):
        self.quotes = {}
        for q in quotes:
            if not isinstance(q, FXQuote):
                raise FXError('Cours non validé')
            key = (q.base, q.quote)
            if key in self.quotes:
                raise FXError('Cours dupliqué pour une paire')
            self.quotes[key] = q

    def _leg(self, base, quote, now, max_age):
        q = self.quotes.get((base, quote))
        inverse = False
        if q is None:
            q = self.quotes.get((quote, base))
            inverse = True
        if q is None:
            raise FXError(f'Cours manquant : {base}/{quote}')
        if q.timestamp > now or now - q.timestamp > max_age:
            raise FXError(f'Cours périmé ou futur : {base}/{quote}')
        return (Decimal(1) / q.ask if inverse else q.bid), q, inverse

    def convert(self, amount, base, quote, *, now, max_age, via=None, fee_rate=0, require_depth=False):
        amount, fee = _decimal(amount), _decimal(fee_rate)
        if amount < 0 or fee < 0 or fee >= 1:
            raise FXError('Montant ou frais invalides')
        if not isfinite(now) or not isfinite(max_age) or max_age < 0:
            raise FXError('Horloge invalide')
        valid = {'EUR', 'USD', 'USDC', 'USDT'}
        if base not in valid or quote not in valid or (via is not None and (via not in valid or via in (base, quote))):
            raise FXError('Devise ou intermédiaire invalide')
        if base == quote:
            if via is not None:
                raise FXError('Intermédiaire inutile')
            return {'amount': amount, 'legs': []}
        path = [base, quote] if via is None else [base, via, quote]
        legs = []
        for a, b in zip(path, path[1:]):
            rate, q, inverse = self._leg(a, b, now, max_age)
            depth = q.ask_depth * q.ask if inverse and q.ask_depth is not None else q.bid_depth if not inverse else None
            if require_depth and (depth is None or amount > depth):
                raise FXError(f'Liquidité insuffisante ou inconnue : {a}/{b}')
            source_amount = amount
            gross = source_amount * rate
            paid_fee = gross * fee
            amount = gross - paid_fee
            legs.append({'base': a, 'quote': b, 'rate': rate, 'timestamp': q.timestamp, 'source': q.source,
                         'market_base': q.base, 'market_quote': q.quote, 'bid': q.bid, 'ask': q.ask,
                         'bid_depth': q.bid_depth, 'ask_depth': q.ask_depth,
                         'input': source_amount, 'gross': gross, 'fee': paid_fee, 'output': amount})
        return {'amount': amount, 'legs': legs}
