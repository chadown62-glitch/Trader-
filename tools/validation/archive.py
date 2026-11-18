"""Immutable public OHLCV archives. No network, wallet or broker access."""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path

COLUMNS = ['ts', 'open', 'high', 'low', 'close', 'volume']
STEP_MS = 14_400_000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8')


def immutable(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as out:
            out.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError(f'Archive already exists with different contents: {path}') from None


def decimal_text(value):
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid decimal') from None
    if not d.is_finite() or not math.isfinite(float(d)) or (d != 0 and float(d) == 0):
        raise ValueError('Nonfinite value')
    text = format(d, 'f') if d else '0'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def canonical_rows(rows):
    """Exact decimal values from public responses; never round to binary float for hashing."""
    output = []
    previous = None
    for row in rows:
        ts = int(row[0])
        if ts != Decimal(str(row[0])) or ts % STEP_MS or (previous is not None and ts <= previous):
            raise ValueError('Nonchronological, duplicate or unaligned timestamp')
        values = [decimal_text(v) for v in row[1:6]]
        o, h, low, c, v = map(Decimal, values)
        if min(o, h, low, c) <= 0 or v < 0 or low > min(o, c) or h < max(o, c) or low > h:
            raise ValueError('Incoherent OHLCV')
        output.append([ts, *values])
        previous = ts
    return output


def materialize(root: Path, pages, request):
    rows, incomplete, outside = [], 0, 0
    for page in pages:
        batch = json.loads((root/page['path']).read_bytes())
        if not isinstance(batch, list):
            raise ValueError('Public API did not return a candle list')
        for r in batch:
            ts = int(r[0])
            if ts + STEP_MS > request['observed_at_ms']:
                incomplete += 1
            elif request['start_ms'] <= ts < request['end_ms']:
                rows.append(r[:6])
            else:
                outside += 1
    rows = canonical_rows(rows)
    expected = list(range(request['start_ms'], request['end_ms'], STEP_MS))
    present = {r[0] for r in rows}
    missing = [t for t in expected if t not in present]
    anomalous = [rows[i][0] for i in range(1, len(rows))
                 if abs(Decimal(rows[i][4])/Decimal(rows[i-1][4])-1) > Decimal('.5')]
    quality = {'bars': len(rows), 'missing_count': len(missing), 'missing_first_20_ms': missing[:20],
               'incomplete_excluded': incomplete, 'outside_excluded': outside, 'moves_above_50pct_ms': anomalous}
    return rows, quality


def finalize(root, request, pages):
    root = Path(root)
    for p in pages:
        if digest((root/p['path']).read_bytes()) != p['sha256']:
            raise ValueError('Raw page integrity failure')
    rows, quality = materialize(root, pages, request)
    if quality['missing_count'] or not rows:
        raise ValueError(f'Incomplete coverage: {quality}')
    data = b''.join(encoded(r) for r in rows)
    immutable(root/'candles.jsonl', data)
    manifest = {'schema': 1, 'source': 'Binance public spot REST', 'origin': 'historical_public',
                'timezone': 'UTC', 'timestamp_unit': 'ms', 'columns': COLUMNS,
                'serialization': 'JSONL exact normalized decimal strings v1',
                'request': request, 'pages': pages, 'quality': quality, 'dataset_sha256': digest(data)}
    payload = encoded(manifest)
    immutable(root/'manifest.json', payload)
    immutable(root/'manifest.sha256', (digest(payload)+'\n').encode())
    return manifest


def load_archive(root, expected_manifest_sha256=None):
    """Verify all raw inputs AND the derived data on every reuse, offline."""
    root = Path(root)
    payload = (root/'manifest.json').read_bytes()
    pinned = expected_manifest_sha256 or (root/'manifest.sha256').read_text().strip()
    if digest(payload) != pinned:
        raise ValueError('Manifest integrity failure')
    manifest = json.loads(payload)
    if manifest['schema'] != 1 or manifest['request']['timeframe'] != '4h':
        raise ValueError('Unsupported archive schema/timeframe')
    for p in manifest['pages']:
        path = Path(p['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid raw page path')
        if digest((root/path).read_bytes()) != p['sha256']:
            raise ValueError('Raw page integrity failure')
    rows, quality = materialize(root, manifest['pages'], manifest['request'])
    data = b''.join(encoded(r) for r in rows)
    if (digest(data) != manifest['dataset_sha256'] or (root/'candles.jsonl').read_bytes() != data
            or quality != manifest['quality'] or quality['missing_count']):
        raise ValueError('Dataset integrity failure')
    import pandas as pd
    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame['ts'] = frame.ts.astype('int64')
    for c in COLUMNS[1:]:
        frame[c] = frame[c].astype('float64')
        if not frame[c].map(math.isfinite).all():
            raise ValueError('Values cannot be represented as finite float64')
    return frame, manifest
