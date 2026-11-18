"""Build FX snapshots solely from public, timestamped spot order books.

A missing exchange timestamp is not replaced with receipt time. Only the first
level of each side may be consumed; deeper liquidity is not inferred.
"""
from decimal import Decimal, InvalidOperation
from math import isfinite

from .fx_book import FXBook, FXError, FXQuote


class PublicFXProvider:
    def __init__(self, source, markets, clock, max_age=60, max_spread=Decimal('0.03'), max_skew=5):
        self.source, self.markets, self.clock = source, markets, clock
        self.max_age, self.max_spread, self.max_skew = max_age, max_spread, max_skew

    def snapshot(self, base, quote, via=None):
        if base == quote:
            raise FXError('Les monnaies doivent être différentes')
        valid = {'EUR', 'USD', 'USDC', 'USDT'}
        if base not in valid or quote not in valid or (via is not None and (via not in valid or via in (base, quote))):
            raise FXError('Paire ou intermédiaire invalide')
        path = [base, quote] if via is None else [base, via, quote]
        quotes = []
        for a, b in zip(path, path[1:]):
            symbol = f'{a}/{b}' if f'{a}/{b}' in self.markets else f'{b}/{a}'
            market = self.markets.get(symbol)
            if market is None or (market.base, market.quote) != tuple(symbol.split('/')):
                raise FXError(f'Marché public indisponible : {a}/{b}')
            try:
                book = self.source.fetch_order_book(symbol, limit=5)
                timestamp = book.get('timestamp')
                if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not isfinite(timestamp) or timestamp <= 0:
                    raise FXError(f'Horodatage du marché manquant : {symbol}')
                bid, bid_qty = (Decimal(str(x)) for x in book['bids'][0][:2])
                ask, ask_qty = (Decimal(str(x)) for x in book['asks'][0][:2])
                if not all(x.is_finite() and x > 0 for x in (bid, bid_qty, ask, ask_qty)) or ask < bid:
                    raise FXError(f'Carnet invalide : {symbol}')
                if (ask - bid) / bid > self.max_spread:
                    raise FXError(f'Écart achat/vente trop élevé : {symbol}')
                name = getattr(self.source, 'name', None)
                if not isinstance(name, str) or not name.strip():
                    raise FXError('Source de prix inconnue')
                q = FXQuote(market.base, market.quote, bid, ask, timestamp / 1000, name, bid_qty, ask_qty)
                # Check the source timestamp now and again when the ledger commits.
                FXBook([q]).convert(1, a, b, now=self.clock(), max_age=self.max_age)
                quotes.append(q)
            except FXError:
                raise
            except (KeyError, IndexError, TypeError, ValueError, InvalidOperation, AttributeError) as exc:
                raise FXError(f'Carnet de marché inutilisable : {symbol}') from exc
            except Exception as exc:
                raise FXError(f'Lecture de marché impossible : {symbol}') from exc
        if len(quotes) > 1 and max(q.timestamp for q in quotes) - min(q.timestamp for q in quotes) > self.max_skew:
            raise FXError('Horodatages de change trop éloignés')
        return FXBook(quotes)
