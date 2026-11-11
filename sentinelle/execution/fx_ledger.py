"""Atomic fictitious currency conversion with an auditable quote snapshot."""
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_DOWN
import json
import math

from ..market.fx import FXBook, FXError
from ..safety import require_paper_account, require_paper_broker
from ..errors import UserError

_QUANTUM = Decimal('0.00000001')
_CURRENCIES = {'EUR', 'USD', 'USDC', 'USDT'}


def _amount(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(_QUANTUM):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise FXError('Montant invalide : maximum 8 décimales et strictement positif') from None
    return amount


class PaperFXLedger:
    def __init__(self, broker, clock, max_age=60):
        require_paper_broker(broker, 'conversion sur courtier non simulé', broker.db)
        self.broker, self.db, self.clock, self.max_age = broker, broker.db, clock, max_age

    def _authorize(self):
        require_paper_broker(self.broker, 'conversion sur courtier non simulé', self.db)
        acc = self.db.one('SELECT * FROM accounts WHERE id = ?', (self.broker.account_id,))
        require_paper_account(acc, 'conversion sur compte non simulé', self.db)
        if acc['role'] != 'principal' or acc['archived'] or acc['quote'] != 'EUR' or self.broker.quote != 'EUR':
            raise UserError('Conversion réservée au portefeuille principal fictif EUR')
        return acc

    def _replay(self, request_id, base, target, amount, via):
        if request_id is None:
            return None
        if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in request_id):
            raise FXError('Identifiant de requête invalide')
        row = self.db.one('SELECT * FROM fx_operations WHERE account_id = ? AND request_id = ?', (self.broker.account_id, request_id))
        if row is None:
            return None
        if (row['source_currency'], row['target_currency'], Decimal(row['amount_in']), row['via']) != (base, target, amount, via):
            raise FXError('Identifiant déjà utilisé pour une autre conversion')
        return {'id': row['id'], 'account_id': row['account_id'], 'timestamp': row['ts'],
                'source_currency': base, 'target_currency': target, 'amount_in': row['amount_in'],
                'amount_out': row['amount_out'], 'fee_rate': row['fee_rate'], 'legs': json.loads(row['legs']),
                'simulated': True, 'replayed': True}

    def replay(self, request_id, base, target, amount, via=None):
        amount = _amount(amount)
        with self.db.transaction():
            self._authorize()
            return self._replay(request_id, base, target, amount, via)

    def convert(self, base, target, amount, book, *, via=None, fee_rate=0, request_id=None):
        if base not in _CURRENCIES or target not in _CURRENCIES or base == target:
            raise FXError('Paire de conversion invalide')
        if not isinstance(book, FXBook):
            raise FXError('Cours de change indisponibles')
        amount = _amount(amount)
        with self.db.transaction():
            acc = self._authorize()
            previous = self._replay(request_id, base, target, amount, via)
            if previous is not None:
                return previous
            available = Decimal(str(self.broker.available(base))).quantize(_QUANTUM, rounding=ROUND_DOWN)
            if amount > available:
                raise FXError(f'Solde {base} disponible insuffisant')
            now = self.clock()
            try:
                result = book.convert(amount, base, target, now=now, max_age=self.max_age,
                                      via=via, fee_rate=fee_rate, require_depth=True)
                output = result['amount'].quantize(_QUANTUM, rounding=ROUND_DOWN)
            except DecimalException as exc:
                raise FXError('Montant de conversion impossible à représenter') from exc
            if output <= 0 or not math.isfinite(float(output)):
                raise FXError('Montant de conversion trop faible après arrondi')
            legs = [{k: str(v) if isinstance(v, Decimal) else v for k, v in leg.items()} for leg in result['legs']]
            self.broker._adjust(base, -float(amount))
            self.broker._adjust(target, float(output))
            operation = self.db.insert('fx_operations', {'account_id': acc['id'], 'ts': now,
                'source_currency': base, 'target_currency': target, 'amount_in': str(amount),
                'amount_out': str(output), 'fee_rate': str(fee_rate), 'via': via, 'request_id': request_id,
                'legs': json.dumps(legs, ensure_ascii=False)})
            self.db.log('info', 'simulation', f'Conversion fictive {base}/{target}', {'compte': acc['id'], 'operation': operation})
            return {'id': operation, 'account_id': acc['id'], 'timestamp': now, 'source_currency': base,
                    'target_currency': target, 'amount_in': str(amount), 'amount_out': str(output),
                    'fee_rate': str(fee_rate), 'legs': legs, 'simulated': True, 'replayed': False}
