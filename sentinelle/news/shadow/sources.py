"""Bounded public fetches with a process-shared SQLite lease/cache. No background thread."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import math

import httpx

from ...market.context import CALENDAR_URL
from . import parsers

SOURCES = {
    'forex_factory': {'url':CALENDAR_URL,'host':'faireconomy.media','parser':'forex','official':False,'dependency_group':'fair_economy','interval':10800,'max_age':21600,'rights_required':True},
    'fed': {'url':'https://www.federalreserve.gov/feeds/press_all.xml','host':'federalreserve.gov','parser':'rss','official':True,'dependency_group':'federal_reserve','interval':3600,'max_age':21600},
    'bls': {'url':'https://www.bls.gov/feed/bls_latest.rss','host':'bls.gov','parser':'rss','official':True,'dependency_group':'bls','interval':3600,'max_age':21600},
    'bls_cpi_index': {'url':'https://api.bls.gov/publicAPI/v2/timeseries/data/CUUR0000SA0','host':'bls.gov','parser':'bls_series','official':True,'dependency_group':'bls','interval':21600,'max_age':86400},
    'bea': {'url':'https://apps.bea.gov/rss/rss.xml','host':'bea.gov','parser':'rss','official':True,'dependency_group':'bea','interval':3600,'max_age':21600},
    'ecb': {'url':'https://www.ecb.europa.eu/rss/press.html','host':'ecb.europa.eu','parser':'rss','official':True,'dependency_group':'ecb','interval':3600,'max_age':21600},
    'eurostat': {'url':'https://ec.europa.eu/eurostat/api/dissemination/catalogue/rss/en/statistics-update.rss','host':'ec.europa.eu','parser':'rss','official':True,'dependency_group':'eurostat','interval':3600,'max_age':21600},
    'coindesk': {'url':'https://www.coindesk.com/arc/outboundfeeds/rss/','host':'coindesk.com','parser':'rss','official':False,'dependency_group':'coindesk','interval':3600,'max_age':21600},
}
MAX_BYTES = 2_000_000


def parse(source, raw):
    kind = SOURCES[source]['parser']
    if kind == 'forex':return parsers.forex(raw)
    if kind == 'bls_series':return parsers.bls_series(raw)
    return parsers.rss(raw,SOURCES[source])


class Collector:
    def __init__(self, store, client, *, forex_rights_confirmed=False):
        self.store=store
        self.client=client
        self.forex_rights_confirmed=forex_rights_confirmed

    def fetch_once(self, source):
        spec=SOURCES[source]
        if spec.get('rights_required') and not self.forex_rights_confirmed:
            return {'source':source,'status':'rights_not_confirmed','downloaded':False}
        now=self.store.clock()
        with self.store.transaction():
            state=self.store.conn.execute('SELECT * FROM poll_state WHERE source=?',(source,)).fetchone()
            if state and now<state['next_at']:
                return {'source':source,'status':'cached_or_backoff','next_at':state['next_at'],'downloaded':False}
            failures=state['failures'] if state else 0
            # Reserve before HTTP. Abrupt process death remains rate-limited until this lease expires.
            self.store.conn.execute('INSERT INTO poll_state VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET next_at=excluded.next_at',
                                    (source,now+spec['interval'],failures))
        received=now;raw=b'';code=None;updated=None;error=None;count=0;retry=now+spec['interval']
        try:
            # No redirects: a redirect must be explicitly reviewed as another public source.
            with self.client.stream('GET',spec['url'],follow_redirects=False,timeout=20) as response:
                code=response.status_code
                updated=response.headers.get('Last-Modified')
                if code==429:
                    value=response.headers.get('Retry-After','')
                    try: delay=float(value)
                    except ValueError:
                        try:delay=parsedate_to_datetime(value).timestamp()-now
                        except (ValueError,TypeError):delay=spec['interval']
                    # Never shorten backoff; a server date beyond this run stays respected.
                    if not math.isfinite(delay):delay=spec["interval"]
                    retry=max(retry,now+max(0,delay))
                response.raise_for_status()
                if 'html' in response.headers.get('Content-Type','').lower() and spec['parser']!='rss':
                    raise ValueError('HTML error/challenge instead of data')
                for chunk in response.iter_bytes():
                    raw+=chunk
                    if len(raw)>MAX_BYTES:raise ValueError('Feed exceeds bounded response size')
            received=self.store.clock()
            entries=parse(source,raw)
            # Receipt and parsing/storage have separate clocks. No historical backfill of knowledge.
            count=self.store.append(source,entries,parsers.sha(raw),received)
            status='ok';failures=0
        except Exception as exc:
            received=self.store.clock();status='error';failures+=1
            retry=max(retry,received+min(86400,spec['interval']*(2**min(failures,5))))
            error=f'{type(exc).__name__}: {str(exc)[:160]}'
        stored=self.store.clock()
        with self.store.transaction():
            self.store.conn.execute('INSERT INTO fetches(source,url,status,received_at,stored_at,raw_sha,source_updated_at,http_status,error,retry_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                    (source,spec['url'],status,received,stored,parsers.sha(raw) if raw else None,updated,code,error,retry))
            self.store.conn.execute('UPDATE poll_state SET next_at=?,failures=? WHERE source=?',(retry,failures,source))
        return {'source':source,'status':status,'new_observations':count,'http_status':code,'error':error,'retry_at':retry,'downloaded':True}


def analyze_once(store, observation_id, client=None):
    """Reuse the local RSS analyzer, with no tool/function calling or authority to trade."""
    from ..service import OllamaClient
    row=store.rows('SELECT * FROM observations WHERE id=? AND kind=?',(observation_id,'news'))
    if not row:raise ValueError('News observation not found')
    import json
    data=json.loads(row[0]['payload'])
    owned=client is None
    client=client or OllamaClient('http://127.0.0.1:11434','qwen2.5:7b')
    try:
        answer=client.analyze(data['title'],data['excerpt'],'BTC, ETH, SOL')
        store.add_analysis(observation_id,answer,'ollama-local-shadow-v1')
    finally:
        if owned:client.client.close()
