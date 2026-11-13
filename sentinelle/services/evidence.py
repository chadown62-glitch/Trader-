"""Evidence Observatory v1. Reads private stable SQLite copies; no App, broker or downloader."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import tempfile
import time

from sentinelle.preflight import _read_only
from tools.validation.archive import digest

SCHEMA = 'sentinelle.evidence/v1'
CATEGORIES = ('synthetic_fixture', 'historical_public', 'prospective_recorded')


def _rows(db, table, where='', params=()):
    if not db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone():
        return []
    return [dict(r) for r in db.execute('SELECT * FROM '+table+where, params)]


def _read(path, callback):
    if not path.is_file():
        return None
    with tempfile.TemporaryDirectory(prefix='sentinelle-evidence-') as tmp:
        db = _read_only(path, Path(tmp))
        try:
            db.execute('PRAGMA query_only=ON')
            return callback(db)
        finally:
            db.close()


def probability_scores(predictions):
    """Only caller-validated mature binary labels. No theory-to-P&L conversion."""
    if not predictions:
        return {'n': 0, 'auc': None, 'brier': None, 'calibration': [], 'verdict': 'NOT_ENOUGH_EVIDENCE'}
    for p, y in predictions:
        if not math.isfinite(p) or not 0 <= p <= 1 or y not in (0, 1):
            raise ValueError('Invalid mature probability/label')
    bins = []
    for i in range(5):
        subset = [(p,y) for p,y in predictions if int(min(p*5,4)) == i]
        bins.append({'lower': i/5, 'upper': (i+1)/5, 'n': len(subset),
                     'predicted': sum(p for p,_ in subset)/len(subset) if subset else None,
                     'observed': sum(y for _,y in subset)/len(subset) if subset else None})
    positives = [p for p,y in predictions if y]
    negatives = [p for p,y in predictions if not y]
    # Rank-based AUC with ties; O(n log n), independent of class imbalance.
    ordered = sorted(predictions)
    rank_sum, k = 0., 0
    while k < len(ordered):
        end = k+1
        while end < len(ordered) and ordered[end][0] == ordered[k][0]:
            end += 1
        rank_sum += sum(y for _,y in ordered[k:end]) * ((k+1+end)/2)
        k = end
    auc = ((rank_sum-len(positives)*(len(positives)+1)/2)/(len(positives)*len(negatives))) if positives and negatives else None
    return {'n': len(predictions), 'auc': auc, 'brier': sum((p-y)**2 for p,y in predictions)/len(predictions),
            'calibration': bins, 'verdict': 'DESCRIPTIVE_ONLY'}


def _wallet(db, now):
    accounts = _rows(db, 'accounts', " WHERE role='main' AND kind='paper' AND archived=0")
    if len(accounts) != 1 or (accounts[0]['quote'], accounts[0]['initial_balance']) != ('EUR', 30):
        raise ValueError('Single principal EUR30 paper ledger required')
    aid = accounts[0]['id']
    positions = _rows(db, 'positions', ' WHERE account_id=?', (aid,))
    fills = _rows(db, 'fills', ' WHERE account_id=?', (aid,))
    ledger = _rows(db, 'ledger', ' WHERE account_id=? ORDER BY id', (aid,))
    balances = _rows(db, 'balances', ' WHERE account_id=?', (aid,))
    equity = _rows(db, 'equity', ' WHERE account_id=? AND ts<=? ORDER BY ts', (aid, now))
    totals = defaultdict(float)
    for row in ledger:
        totals[row['asset']] += row['amount']
    reconciliation = {r['asset']: r['total']-totals[r['asset']] for r in balances}
    for asset, total in totals.items():
        if asset not in reconciliation:
            reconciliation[asset] = -total
    # Never interpolate a shutdown or mark stale snapshot prices as current.
    last = equity[-1] if equity else None
    curve = [30.] + [r['equity'] for r in equity]
    peak, drawdown = 30., 0.
    for value in curve:
        peak = max(peak, value)
        drawdown = max(drawdown, 1-value/peak)
    return {'account_id': aid, 'initial_eur': 30, 'attribution': 'SHARED_MANUAL_AND_AI_UNATTRIBUTED',
            'category': 'unclassified_shared_ledger', 'balances': balances,
            'ledger_gap_by_asset': dict(reconciliation),
            'ledger_reconciled': all(abs(v) <= 1e-7 for v in reconciliation.values()),
            'realized_net_eur': sum(p['realized_pnl'] for p in positions),
            'position_fees_eur': sum(p['fees'] or 0 for p in positions),
            'closed_positions': sum(p['status']=='closed' for p in positions),
            'open_positions': sum(p['status']=='open' for p in positions),
            'fills': len(fills), 'last_equity_snapshot': last,
            'snapshot_age_seconds': now-last['ts'] if last else None,
            'current_mtm_eur': None, 'current_latent_eur': None,
            'snapshot_net_eur': last['equity']-30 if last else None,
            'snapshot_drawdown': drawdown if equity else None,
            'note': 'Last persisted valuation only; no live prices/FX fetched. Fees already included in net P&L.'}


def _observations(db, now):
    groups = {k: {'reports': [], 'daily': [], 'profit_claim': False} for k in CATEGORIES}
    unknown, future = 0, 0
    days = {k: defaultdict(Counter) for k in CATEGORIES}
    for row in _rows(db, 'observation_reports', ' ORDER BY at'):
        report = json.loads(row['report'])
        if row['at'] > now:
            future += 1
            continue
        source = report.get('origin')
        # Public captures alone are not proof that prediction preceded outcome.
        category = 'synthetic_fixture' if source == 'synthetic_fixture' else None
        if category is None:
            unknown += 1
            continue
        day = datetime.fromtimestamp(row['at'], timezone.utc).date().isoformat()
        days[category][day]['reports'] += 1
        groups[category]['reports'].append({'sha256': row['sha'], 'recorded_at': row['at'],
                                           'bar_close_ms': report.get('bar_close_ms'),
                                           'capture_sha256': report.get('capture_sha256'),
                                           'protocol_sha256': report.get('protocol_sha256'),
                                           'complete': report.get('steps_complete', False)})
    for category in CATEGORIES:
        groups[category]['daily'] = [{'utc_day': day, **dict(counts)} for day,counts in sorted(days[category].items())]
    return groups, {'unclassified_public_reports': unknown, 'future_reports_excluded': future,
                    'prospective_note': 'Prospective classification requires per-decision causal proof; public origin alone is insufficient.'}


def _decisions(db, now):
    """A later public journal cannot retroactively authenticate an old prediction."""
    from tools.prospective import check
    frozen = check(Path(__file__).resolve().parents[2]/'research/prospective-v2')
    origins = {(r['account_id'], r['bar_close_ms']): r for r in _rows(db, 'observation_provenance')}
    groups = {k: [] for k in CATEGORIES}
    scores = {k: [] for k in CATEGORIES}
    excluded = 0
    for row in _rows(db, 'candidates'):
        f = json.loads(row['features'] or '{}')
        available, recorded = f.get('signal_available_ms'), f.get('prediction_recorded_at')
        source = origins.get((row['account_id'], available))
        if (not source or recorded is None or recorded > now or not source['available_at'] <= source['recorded_at'] <= recorded
                or available/1000 > recorded or recorded-available/1000 > 180):
            excluded += 1
            continue
        if source['origin'] == 'synthetic_fixture':
            category = 'synthetic_fixture'
        elif (source['origin']=='public_preflight' and source['model_sha']==frozen['model_sha256']
              and source['settings_sha']==frozen['settings_sha256']
              and source['protocol_sha']==digest((Path(__file__).resolve().parents[2]/'research/prospective-v2/protocol.json').read_bytes())):
            category = 'prospective_recorded'
        else:
            excluded += 1
            continue
        mature = (row['exit_ts'] is not None and row['resolved_at'] is not None
                  and recorded < row['exit_ts']/1000 <= row['resolved_at'] <= now and row['outcome_r'] is not None)
        if mature and row['p_win'] is not None:
            scores[category].append((row['p_win'], int(row['outcome_r']>0)))
        groups[category].append({'candidate_id': row['id'], 'symbol': row['symbol'], 'decision': row['decision'],
                                 'reason': row['decision_reason'], 'available_at': available/1000, 'recorded_at': recorded,
                                 'p_win': row['p_win'], 'model_version': row['model_version'],
                                 'capture_sha256': source['capture_sha'], 'protocol_sha256': source['protocol_sha'],
                                 'maturity': 'MATURE' if mature else 'PENDING',
                                 'theoretical_outcome_r': row['outcome_r'] if mature else None,
                                 'realized_portfolio_pnl_eur': None})
    return groups, {k: probability_scores(v) for k,v in scores.items()}, excluded


def historical_baselines():
    """Only the reviewed repository-owned replay artifact, never user-selected paths."""
    folder = Path(__file__).resolve().parents[2]/'docs/validation/mesures/mission-control/baselines'
    if not (folder/'results.json').is_file():
        return None
    raw = (folder/'results.json').read_bytes()
    report = json.loads(raw)
    if digest(raw) != (folder/'results.sha256').read_text().strip():
        raise ValueError('Baseline artifact checksum mismatch')
    if (report['schema'] != 'sentinelle.baselines/v1' or report['category'] != 'historical_public'
            or report['promotion_allowed'] or report['specification_sha256'] != digest((folder/'specification.json').read_bytes())):
        raise ValueError('Invalid baseline classification')
    for period in report['results']:
        for name,arm in period['arms'].items():
            if name not in ('CASH','HOLD','TREND','V2') or period['start'] not in ('2025-07-01','2026-01-01'):
                raise ValueError('Unknown replay arm or period')
            if digest((folder/(period['start']+'-'+name+'-trace.json')).read_bytes()) != arm['trace_sha256']:
                raise ValueError('Baseline trace integrity failure')
    return report


def export(data, *, now=None):
    data = Path(data)
    now = time.time() if now is None else now
    if not math.isfinite(now):
        raise ValueError('Invalid observation clock')
    result = {'schema': SCHEMA, 'generated_at': now, 'generated_at_utc': datetime.fromtimestamp(now, timezone.utc).isoformat(),
              'paper_only': True, 'promotion_allowed': False, 'status': 'NOT_ENOUGH_EVIDENCE',
              'categories': {k: {'reports': [], 'daily': [], 'profit_claim': False} for k in CATEGORIES},
              'wallet': None, 'supervisor': None, 'errors': [],
              'operating_cost_eur': None, 'learning_speed': None, 'uptime_seconds': None,
              'independent_advantage': False, 'baselines': [],
              'limits': ['No live MTM or synthetic/historical/prospective aggregation.',
                         'Missing intervals are unknown, never flat-return or uptime evidence.',
                         'Abstention is not a realized profit. No independent advantage established.']}
    def observations(db):
        result['wallet'] = _wallet(db, now)
        result['categories'], result['classification'] = _observations(db, now)
        decisions, scores, excluded = _decisions(db, now)
        for category in CATEGORIES:
            result['categories'][category]['decisions'] = decisions[category]
            result['categories'][category]['scores'] = scores[category]
        result['classification']['unproven_decisions'] = excluded
        result['activity'] = {'decisions': len(_rows(db, 'decisions')),
                              'orders': len(_rows(db, 'orders')),
                              'partial_orders': len(_rows(db, 'orders', " WHERE filled>0 AND filled<amount")),
                              'outbox_pending': len(_rows(db, 'event_outbox', ' WHERE dispatched_at IS NULL'))}
    try:
        _read(data/'sentinelle.db', observations)
    except (OSError, ValueError, __import__('sqlite3').DatabaseError) as exc:
        result['errors'].append(type(exc).__name__+': '+str(exc)[:180])
        result['status'] = 'BLOCKED'
    def state(db):
        meta = {r['key']: json.loads(r['value']) for r in _rows(db, 'meta')}
        events = _rows(db, 'events')
        cycles = _rows(db, 'cycles')
        ticks = [json.loads(e['payload']) for e in events if e['kind']=='tick_measured']
        return {'status': meta.get('status'), 'heartbeat': meta.get('heartbeat'), 'failure': meta.get('failure'),
                'cycle_counts': dict(Counter(c['status'] for c in cycles)),
                'errors': dict(Counter(e['kind'] for e in events if 'error' in e['kind'])),
                'measured_tick_wall_seconds': sum(t['wall_seconds'] for t in ticks),
                'measured_tick_cpu_seconds': sum(t['cpu_seconds'] for t in ticks),
                'measured_ticks': len(ticks), 'continuous_uptime': None,
                'coverage_by_cycle': cycles}
    try:
        result['supervisor'] = _read(data/'supervisor/state.sqlite', state)
    except (OSError, ValueError, __import__('sqlite3').DatabaseError) as exc:
        result['errors'].append(type(exc).__name__+': '+str(exc)[:180])
        result['status'] = 'BLOCKED'
    try:
        result['historical_baselines'] = historical_baselines()
    except (OSError, ValueError, KeyError) as exc:
        result['errors'].append(type(exc).__name__+': '+str(exc)[:180])
        result['status'] = 'BLOCKED'
    return result
