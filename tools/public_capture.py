"""Bounded public GET capture. HTTP archive and paper execution are separate phases."""
from dataclasses import asdict
import json
import math
from pathlib import Path
import sqlite3
import time


from sentinelle.ai.features import warmup_bars
from sentinelle.execution.liquidity import BookSnapshot
from sentinelle.market.markets import MarketInfo
from tools.collect_public import download, SYMBOLS
from tools.validation.archive import STEP_MS, digest, encoded, immutable, load_archive

BASE = 'https://data-api.binance.vision'
ALLOWED = frozenset({'time', 'exchangeInfo', 'klines', 'depth'})


class RetryLater(RuntimeError):
    pass


class PublicCapture:
    def __init__(self, root, client, *, clock=time.time, deadline=None):
        self.deadline=deadline
        self.root, self.client, self.clock = Path(root), client, clock
        self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'fetch.sqlite',isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY,path TEXT,params TEXT,raw_path TEXT,sha TEXT,received REAL,next_at REAL DEFAULT 0,failures INTEGER DEFAULT 0)')
        self.db.execute('CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY,ts REAL,key TEXT,status INTEGER,body_sha TEXT)')
        if 'latency' not in {r[1] for r in self.db.execute('PRAGMA table_info(requests)')}:
            self.db.execute('ALTER TABLE requests ADD COLUMN latency REAL')
    def close(self):
        self.db.close()
    def get(self,path,params=None):
        if path not in ALLOWED:
            raise ValueError('Public endpoint not allowlisted')
        params=params or {}
        key=digest(encoded([path,params]))
        row=self.db.execute('SELECT * FROM requests WHERE key=?',(key,)).fetchone()
        if row and row['sha']:
            raw=(self.root/row['raw_path']).read_bytes()
            if digest(raw)!=row['sha']:raise ValueError('Public response integrity failure')
            return json.loads(raw),row['received']
        now=self.clock()
        if row and row['next_at']>now:raise RetryLater('Public request backoff active')
        self.db.execute('INSERT OR IGNORE INTO requests(key,path,params) VALUES(?,?,?)',(key,path,json.dumps(params,sort_keys=True)))
        # No retry loop or sleeping. A failed step terminates, preserving successful pages.
        budget=20 if self.deadline is None else min(20,self.deadline-time.monotonic())
        if budget<=0:raise TimeoutError('Capture deadline reached')
        requested=self.clock()
        chunks=[];size=0
        with self.client.stream('GET',BASE+'/api/v3/'+path,params=params,timeout=budget,follow_redirects=False) as response:
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>4_000_000:raise ValueError('Public response exceeds limit')
                if self.deadline is not None and time.monotonic()>self.deadline:raise TimeoutError('Capture deadline reached')
                chunks.append(chunk)
        raw=b''.join(chunks)
        received=self.clock()
        file=Path('http')/(key+'-'+digest(raw)+'.json')
        immutable(self.root/file,raw)  # retain even a 429 or malformed JSON, never poison a valid page
        if response.status_code!=200:
            failures=(row['failures'] if row else 0)+1
            try:delay=float(response.headers.get('Retry-After','60'))
            except ValueError:delay=60
            delay=max(1,min(86400,delay if math.isfinite(delay) else 60),min(3600,2**min(10,failures)))
            self.db.execute('INSERT INTO errors(ts,key,status,body_sha) VALUES(?,?,?,?)',(received,key,response.status_code,digest(raw)))
            self.db.execute('UPDATE requests SET next_at=?,failures=? WHERE key=?',(received+delay,failures,key))
            raise RetryLater(f'Public HTTP {response.status_code}; retry after {delay}s')
        try:value=json.loads(raw)
        except (ValueError,UnicodeError):
            self.db.execute('INSERT INTO errors(ts,key,status,body_sha) VALUES(?,?,?,?)',(received,key,200,digest(raw)))
            self.db.execute('UPDATE requests SET next_at=? WHERE key=?',(received+60,key))
            raise ValueError('Malformed public JSON; original bytes retained') from None
        self.db.execute('UPDATE requests SET raw_path=?,sha=?,received=?,latency=?,next_at=0 WHERE key=?',
                        (file.as_posix(),digest(raw),received,received-requested,key))
        return value,received
    def collect(self, *, origin='public_preflight'):
        if origin not in ('public_preflight','synthetic_fixture'):raise ValueError('Unknown capture origin')
        if (self.root/'capture.json').exists():return load_capture(self.root)[0]
        server,received=self.get('time')
        if abs(server['serverTime']/1000-received)>5:raise ValueError('UTC clock skew >5s')
        end=int(server['serverTime'])//STEP_MS*STEP_MS
        start=end-warmup_bars('4h')*STEP_MS
        info,_=self.get('exchangeInfo',{'symbols':json.dumps([s.replace('/','') for s in SYMBOLS],separators=(',',':'))})
        markets={}
        for item in info['symbols']:
            if item.get('status')!='TRADING' or item.get('quoteAsset')!='EUR':continue
            symbol=item['baseAsset']+'/EUR'
            if symbol not in SYMBOLS:continue
            f={x['filterType']:x for x in item['filters']}
            minimum=f.get('NOTIONAL',f.get('MIN_NOTIONAL',{})).get('minNotional')
            if minimum is None:raise ValueError('Unknown market minimum')
            market=MarketInfo(symbol,item['baseAsset'],'EUR',float(f['LOT_SIZE']['stepSize']),
                              float(f['PRICE_FILTER']['tickSize']),float(f['LOT_SIZE']['minQty']),float(minimum))
            if min(market.amount_step,market.price_step,market.min_amount,market.min_notional)<=0:raise ValueError('Invalid market limits')
            markets[symbol]=asdict(market)
        if set(markets)!=set(SYMBOLS):raise ValueError('Incomplete market catalogue')
        archives={}
        for symbol in SYMBOLS:
            def fetch(sym,cursor,finish):
                value,_=self.get('klines',{'symbol':sym.replace('/',''),'interval':'4h','startTime':cursor,'endTime':finish-1,'limit':1000})
                return encoded(value)
            directory=self.root/symbol.replace('/','-')
            m=download(directory,symbol,start,end,fetch,observed_at_ms=int(received*1000))
            archives[symbol]=digest((directory/'manifest.json').read_bytes())
            assert m['quality']['missing_count']==0
        books={}
        for symbol in SYMBOLS:
            raw,stamp=self.get('depth',{'symbol':symbol.replace('/',''),'limit':20})
            book=BookSnapshot(raw['bids'],raw['asks'],stamp,'binance-rest-receipt')
            books[symbol]={'bids':[[str(p),str(q)] for p,q in book.bids],'asks':[[str(p),str(q)] for p,q in book.asks],
                           'timestamp':stamp*1000,'received_at':stamp,'source_event_time':None,
                           'time_quality':'receipt_only; exchange event time unavailable','lastUpdateId':raw.get('lastUpdateId')}
        manifest={'schema':1,'origin':origin,'end_ms':end,'created_at':self.clock(),'archives':archives,'markets':markets,'books':books,
                  'endpoint':BASE,'http_evidence':[dict(r) for r in self.db.execute('SELECT key,raw_path,sha,received,latency FROM requests WHERE sha IS NOT NULL ORDER BY key')]}
        immutable(self.root/'capture.json',encoded(manifest))
        immutable(self.root/'capture.sha256',(digest(encoded(manifest))+'\n').encode())
        return manifest


def load_capture(root):
    root=Path(root)
    raw=(root/'capture.json').read_bytes()
    if digest(raw)!=(root/'capture.sha256').read_text().strip():raise ValueError('Capture integrity failure')
    m=json.loads(raw)
    if m['schema']!=1 or set(m['archives'])!=set(SYMBOLS) or m['endpoint']!=BASE:raise ValueError('Invalid capture provenance')
    histories={}
    for evidence in m['http_evidence']:
        p=Path(evidence['raw_path'])
        if p.is_absolute() or '..' in p.parts or digest((root/p).read_bytes())!=evidence['sha']:raise ValueError('HTTP evidence integrity failure')
    for symbol,expected in m['archives'].items():
        histories[symbol],_=load_archive(root/symbol.replace('/','-'),expected)
        if int(histories[symbol].ts.iloc[-1])+STEP_MS!=m['end_ms']:raise ValueError('Capture chronology mismatch')
    return m,histories


class CaptureSource:
    """Offline adapter only: cannot download or use financial keys."""
    name='public-capture'
    def __init__(self,manifest,histories,clock=time.time):
        self.manifest,self.histories,self.clock=manifest,histories,clock
    def now_ms(self):return int(self.clock()*1000)
    def load_markets(self):
        return {s:MarketInfo(**v) for s,v in self.manifest['markets'].items()}
    def fetch_order_book(self,symbol,limit=20):return self.manifest['books'][symbol]
    def fetch_ohlcv(self,symbol,timeframe,**kwargs):
        if timeframe!='4h':raise ValueError('Capture contains only completed 4h candles')
        return self.histories[symbol].values.tolist()
