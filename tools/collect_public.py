"""Download-only public Binance EUR spot candles; never used by offline tests/backtests."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from tools.validation.archive import STEP_MS, digest, encoded, finalize, immutable, load_archive

ENDPOINT = 'https://data-api.binance.vision/api/v3/klines'
SYMBOLS = ('BTC/EUR', 'ETH/EUR', 'SOL/EUR')


def download(root, symbol, start_ms, end_ms, fetch, *, observed_at_ms=None):
    root = Path(root)
    if symbol not in SYMBOLS or start_ms % STEP_MS or end_ms % STEP_MS or start_ms >= end_ms:
        raise ValueError('Only BTC/ETH/SOL EUR, 4h, aligned nonempty periods supported')
    config = {'symbol': symbol, 'timeframe': '4h', 'start_ms': start_ms, 'end_ms': end_ms, 'endpoint': ENDPOINT}
    if (root/'request.json').exists():
        request = json.loads((root/'request.json').read_bytes())
        if any(request[k] != v for k, v in config.items()):
            raise ValueError('Resume parameters differ; use a new archive directory')
    else:
        request = {**config, 'observed_at_ms': observed_at_ms if observed_at_ms is not None else int(time.time()*1000)}
        if end_ms > request['observed_at_ms'] // STEP_MS * STEP_MS:
            raise ValueError('Requested period includes incomplete candles')
        immutable(root/'request.json', encoded(request))
    if (root/'manifest.json').exists():
        return load_archive(root)[1]
    cursor, pages = start_ms, []
    while cursor < end_ms:
        path = root/'raw'/f'{cursor}.json'
        if path.exists():
            raw = path.read_bytes()  # interruption recovery: never refetch an existing page
        else:
            raw = fetch(symbol, cursor, end_ms)
            # Save original bytes before parsing, including malformed/error responses for inspection.
            immutable(path, raw)
        batch = json.loads(raw)
        if not isinstance(batch, list) or not batch:
            raise ValueError(f'No candles at {cursor}; partial archive preserved')
        stamps = [int(r[0]) for r in batch]
        if stamps[0] != cursor or stamps != sorted(set(stamps)):
            raise ValueError('Pagination gap, duplicate or out-of-order response')
        pages.append({'path': path.relative_to(root).as_posix(), 'sha256': digest(raw),
                      'start_ms': cursor, 'last_ms': stamps[-1]})
        nxt = stamps[-1]+STEP_MS
        if nxt <= cursor:
            raise ValueError('Pagination did not advance')
        cursor = nxt
    return finalize(root, request, pages)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--start', required=True, help='UTC date inclusive')
    parser.add_argument('--end', required=True, help='UTC date exclusive')
    args = parser.parse_args()
    def stamp(s):
        return int(datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)
    session = requests.Session()
    def fetch(symbol, start, end):
        response = session.get(ENDPOINT, params={'symbol':symbol.replace('/', ''), 'interval':'4h',
                                               'startTime':start, 'endTime':end-1, 'limit':1000}, timeout=30)
        response.raise_for_status()
        return response.content
    for symbol in SYMBOLS:
        m = download(args.root/symbol.replace('/', '-'), symbol, stamp(args.start), stamp(args.end), fetch)
        print(symbol, m['quality'], m['dataset_sha256'], flush=True)


if __name__ == '__main__':
    main()
