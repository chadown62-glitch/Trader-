"""Explicit one-shot collection, local analysis or annotation. Never starts a service."""
import argparse
import json
from pathlib import Path
import time

import httpx

from sentinelle.news.shadow.parsers import sha
from sentinelle.news.shadow.service import MacroShadow
from sentinelle.news.shadow.sources import Collector, SOURCES, analyze_once


def load_protocol(path):
    raw=Path(path).read_bytes();protocol=json.loads(raw)
    expected=Path(str(path)+'.sha256').read_text().strip()
    if sha(raw)!=expected or protocol['mode']!='shadow_only' or protocol['can_trade']:
        raise ValueError('Shadow protocol integrity/policy failure')
    if protocol.get('source_catalogue')!=SOURCES:
        raise ValueError('Source catalogue changed after shadow protocol registration')
    for name, expected_hash in protocol.get('implementation_sha256',{}).items():
        actual=Path(__file__).resolve().parents[1]/'sentinelle/news/shadow'/name
        if sha(actual.read_bytes())!=expected_hash:
            raise ValueError('Shadow implementation changed after protocol registration')
    if not protocol['observation_start']<=time.time()<protocol['observation_end']:
        raise ValueError('Outside the separate shadow observation period')
    return protocol


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action',choices=['fetch','view','analyze','annotate'])
    cli.add_argument('--data',type=Path,required=True)
    cli.add_argument('--protocol',type=Path,default=Path('research/macro-shadow-v3/protocol.json'))
    cli.add_argument('--sources',nargs='+',choices=list(SOURCES),default=['fed','bls','bea','ecb','eurostat'])
    cli.add_argument('--forex-rights-confirmed',action='store_true',help='Confirm rights for local use/storage of FF weekly export; no redistribution')
    cli.add_argument('--observation-id',type=int)
    cli.add_argument('--trading-db',type=Path)
    a=cli.parse_args();service=MacroShadow(a.data)
    try:
        if a.action=='view':
            print(json.dumps(service.dashboard(),ensure_ascii=False,indent=2));return
        load_protocol(a.protocol)  # reject ingestion/analysis/annotation outside the frozen new period
        if a.action=='fetch':
            with httpx.Client(headers={'User-Agent':'Sentinelle/0.1 public RSS shadow reader'}) as client:
                collector=Collector(service.store,client,forex_rights_confirmed=a.forex_rights_confirmed)
                for source in a.sources:print(json.dumps(collector.fetch_once(source)),flush=True)
        elif a.action=='analyze':
            if a.observation_id is None:cli.error('--observation-id required')
            analyze_once(service.store,a.observation_id)
        else:
            if a.trading_db is None:cli.error('--trading-db required')
            print(service.annotate(a.trading_db,a.protocol))
    finally:service.stop()


if __name__=='__main__':main()
