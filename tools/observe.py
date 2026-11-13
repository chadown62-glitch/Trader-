"""Finite, offline preflight/execution of previously captured public data. Dry-run by default."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import time
import tempfile

import httpx

from sentinelle.ai.features import compute_features, warmup_bars
from sentinelle.ai.model import MetaModel
from sentinelle.execution.liquidity import BookSnapshot
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App
from sentinelle.storage.db import dumps
from tools.prospective import SupervisedObserver, check
from tools.public_capture import PublicCapture, CaptureSource, load_capture
from tools.validation.archive import digest, encoded, immutable, STEP_MS


def existing_installation(data,settings):
    path=Path(data)/'sentinelle.db'
    if not path.is_file():raise ValueError('Existing installation required; no wallet creation')
    from sentinelle.preflight import _read_only
    with tempfile.TemporaryDirectory(prefix='sentinelle-preflight-') as scratch:
        db=_read_only(path,Path(scratch))
        try:
            return _check_installation(db,settings)
        finally:db.close()


def _check_installation(db,settings):
    db.row_factory=sqlite3.Row
    accounts=db.execute("SELECT * FROM accounts WHERE role='main' AND kind='paper' AND archived=0").fetchall()
    if len(accounts)!=1 or (accounts[0]['quote'],accounts[0]['initial_balance'])!=('EUR',30):
        raise ValueError('Existing single main EUR30 wallet required')
    row=db.execute('SELECT data FROM settings WHERE id=1').fetchone()
    if not row or json.loads(row[0])!=settings:raise ValueError('Existing settings differ from frozen protocol')
    if db.execute('SELECT COUNT(*) FROM agents WHERE enabled=1').fetchone()[0]:
        raise ValueError('Stop existing autonomous agents before supervised observation')
    return dict(accounts[0])

def preflight(data,root,capture,*,now=None):
    now=time.time() if now is None else now
    if not math.isfinite(now):raise ValueError('Invalid UTC clock')
    protocol=check(root)
    settings=json.loads((Path(root)/'settings.json').read_bytes())
    model=MetaModel.load(Path(root)/'model.joblib')  # trusted locally frozen artifact, never remote joblib
    if model.timeframe!=settings['ai']['timeframe']:
        raise ValueError('Frozen model timeframe incompatible with settings')
    account=existing_installation(data,settings)
    manifest,histories=load_capture(capture)
    reasons=[]
    if any(e['latency'] is None or not 0<=e['latency']<=5 for e in manifest['http_evidence']):
        reasons.append('HTTP latency exceeds 5s or unavailable')
    if settings['general']['quote']!='EUR' or settings['ai']['timeframe']!='4h':
        reasons.append('Runner supports frozen EUR/4h only')
    if manifest['origin'] not in ('public_preflight','synthetic_fixture'):reasons.append('Unrecognized observation origin')
    if not datetime.fromisoformat(protocol['start']).timestamp()<=now<datetime.fromisoformat(protocol['end']).timestamp():
        reasons.append('Outside frozen observation period')
    if set(settings['general']['watchlist'])!=set(histories):reasons.append('Incomplete or incompatible universe')
    if not 0<=now-manifest['end_ms']/1000<=180:reasons.append('Closed bar stale or future')
    if not 0<=now-manifest['created_at']<=180:reasons.append('Capture stale or future')
    for symbol,frame in histories.items():
        if len(frame)<warmup_bars('4h') or frame.ts.diff().dropna().ne(STEP_MS).any():
            reasons.append(symbol+': insufficient warmup or gap')
        raw=manifest['books'].get(symbol)
        if not raw:
            reasons.append(symbol+': missing book');continue
        BookSnapshot(raw['bids'],raw['asks'],raw['timestamp']/1000,'binance-rest-receipt')
        if not 0<=now-raw['received_at']<=30 or raw['timestamp']!=raw['received_at']*1000:
            reasons.append(symbol+': book stale or future')
    return {'ready':not reasons,'reasons':reasons,'as_of':now,'account_id':account['id'],
            'origin':manifest['origin'],'bar_close_ms':manifest['end_ms'],
            'bar_age_seconds':now-manifest['end_ms']/1000,'capture_age_seconds':now-manifest['created_at'],
            'book_event_time':'unavailable; REST receipt timestamp only','capture_sha256':digest((Path(capture)/'capture.json').read_bytes()),
            'protocol_sha256':digest((Path(root)/'protocol.json').read_bytes())},manifest,histories


def step(data,root,capture,*,execute=False,clock=time.time,macro=None,macro_protocol=None):
    report,manifest,histories=preflight(data,root,capture,now=clock())
    report['mode']='paper_step' if execute else 'dry_run'
    if not execute or not report['ready']:return report
    if macro is not None:
        if macro_protocol is None:raise ValueError('Macro protocol required before execution')
        from tools.macro_shadow import load_protocol
        load_protocol(macro_protocol)
    source=CaptureSource(manifest,histories,clock)
    app=App(Path(data),source=source,start=False)  # settings/wallet already verified read-only
    try:
        if isinstance(source,SyntheticSource):raise ValueError('Invalid capture adapter')
        app.hub.load_markets()
        app.hub.set_watch(histories)
        for symbol,raw in manifest['books'].items():
            price=(float(raw['bids'][0][0])+float(raw['asks'][0][0]))/2
            app.hub.prices[symbol]=price
            app.hub.price_ts[symbol]=raw['received_at']
            app.hub.price_source_ts[symbol]=raw['received_at']
        app.db.execute('CREATE TABLE IF NOT EXISTS observation_steps(key TEXT PRIMARY KEY,protocol_sha TEXT,capture_sha TEXT,status TEXT,report TEXT)')
        app.db.execute('CREATE TABLE IF NOT EXISTS observation_reports(sha TEXT PRIMARY KEY,step_key TEXT,at REAL,report TEXT)')
        key=str(report['account_id'])+':'+str(manifest['end_ms'])
        with app.db.transaction():
            old=app.db.one('SELECT * FROM observation_steps WHERE key=?',(key,))
            if old and (old['capture_sha']!=report['capture_sha256'] or old['protocol_sha']!=report['protocol_sha256']):
                raise ValueError('Conflicting capture or protocol for reserved observation')
            app.db.execute('INSERT OR IGNORE INTO observation_steps VALUES(?,?,?,?,?)',
                           (key,report['protocol_sha256'],report['capture_sha256'],'prepared',dumps(report)))
        observer=SupervisedObserver(app,root)
        expire_intents(app,clock())
        btc=histories.get('BTC/EUR')
        for symbol,frame in sorted(histories.items()):
            # Process outstanding orders/stops only on a fresh captured L2 book.
            observer.agent.broker.process_book(symbol)
            features=compute_features(frame,'4h',btc=None if symbol=='BTC/EUR' else btc)
            observer.on_bar(symbol,features)
        app._snapshot_equity()
        # Sidecar writes follow the committed trading state. Retries do not repeat orders.
        if macro is not None:
            report['annotations']=macro.annotate(app.db.path,macro_protocol)
        report['journal']=app.decision_journal(limit=50)
        report['performance']=app.performance()
        report['outbox_pending']=app.db.scalar('SELECT COUNT(*) FROM event_outbox WHERE dispatched_at IS NULL',default=0)
        report['steps_complete']=True
        with app.db.transaction():
            app.db.execute("UPDATE observation_steps SET status='complete',report=? WHERE key=? AND status!='complete'",(dumps(report),key))
            app.db.execute('INSERT OR IGNORE INTO observation_reports VALUES(?,?,?,?)',
                           (digest(encoded(report)),key,clock(),dumps(report)))
        return report
    finally:
        app.close()


def protect(data,root,quotes,*,clock=time.time):
    """Fresh public L2 maintenance only. Never calls an agent, predicts or creates an entry."""
    check(root)
    settings=json.loads((Path(root)/'settings.json').read_bytes())
    existing_installation(data,settings)
    now=clock()
    if set(quotes['books'])!=set(settings['general']['watchlist']):raise ValueError('Incomplete quotes')
    for raw in quotes['books'].values():
        if not 0<=now-raw['timestamp']/1000<=30:raise ValueError('Stale or future protective quote')
        BookSnapshot(raw['bids'],raw['asks'],raw['timestamp']/1000,'binance-rest-receipt')
    source=CaptureSource(quotes,{},clock)
    app=App(Path(data),source=source,start=False)
    try:
        app.hub.load_markets()
        for symbol,raw in quotes['books'].items():
            app.hub.prices[symbol]=(float(raw['bids'][0][0])+float(raw['asks'][0][0]))/2
            app.hub.price_ts[symbol]=app.hub.price_source_ts[symbol]=raw['timestamp']/1000
        broker=app.runtime(app.main_account()['id']).broker
        expire_intents(app,now)
        for symbol in sorted(quotes['books']):broker.process_book(symbol)
        app._snapshot_equity()
        return {'origin':quotes['origin'],'maintenance_only':True,'performance':app.performance()}
    finally:app.close()


def expire_intents(app,now):
    """Preserve the forecast, finalize a missed entry as refused; never replay an old order."""
    from sentinelle.config import timeframe_ms
    with app.db.transaction():
        for c in app.db.query("SELECT * FROM candidates WHERE source='live' AND decision='préparé' AND account_id=?",(app.main_account()['id'],)):
            if (c['ts']+timeframe_ms(c['timeframe']))/1000>=now-180:continue
            reason='Reprise : prédiction conservée, signal expiré, aucun ordre tardif'
            app.db.update('candidates',c['id'],{'decision':'refusé','decision_reason':reason})
            app.db.decision(c['account_id'],'prospective-frozen',c['symbol'],'refusé',reason,{'candidate_id':c['id']},ts=now)
            app.bus.publish('decision',{'candidate_id':c['id'],'kind':'refusé','reason':reason})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['collect','run'])
    parser.add_argument('--data',type=Path)
    parser.add_argument('--root',type=Path,default=Path('research/prospective-v2'))
    parser.add_argument('--capture',type=Path,nargs='+',required=True)
    parser.add_argument('--execute',action='store_true',help='One explicit paper step per capture; default read-only dry-run')
    parser.add_argument('--macro-data',type=Path,help='Optional existing v3 sidecar; never collects news or FF')
    parser.add_argument('--macro-protocol',type=Path,default=Path('research/macro-shadow-v3/protocol.json'))
    parser.add_argument('--max-seconds',type=int,default=120)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=len(args.capture)<=3 or not 1<=args.max_seconds<=180:parser.error('1–3 captures and 1–180 seconds required')
    started=time.monotonic();results=[]
    for capture in args.capture:
        try:
            if time.monotonic()-started>args.max_seconds:raise TimeoutError('Finite runner deadline reached')
            if args.action=='collect':
                with httpx.Client() as client:
                    collector=PublicCapture(capture,client,deadline=started+args.max_seconds)
                    try:results.append(collector.collect())
                    finally:collector.close()
            else:
                if args.data is None:raise ValueError('--data required; never initializes a new installation')
                macro=None
                try:
                    if args.macro_data:
                        from tools.macro_shadow import load_protocol
                        load_protocol(args.macro_protocol)
                        if not (args.macro_data/'macro-shadow.sqlite').is_file():raise ValueError('Existing macro sidecar required')
                        if args.execute:
                            from sentinelle.news.shadow.service import MacroShadow
                            macro=MacroShadow(args.macro_data)
                    results.append(step(args.data,args.root,capture,execute=args.execute,macro=macro,macro_protocol=args.macro_protocol))
                finally:
                    if macro is not None:macro.stop()
        except Exception as exc:
            results.append({'ready':False,'error':type(exc).__name__,'reason':str(exc),'capture':str(capture)})
            break
    result={'elapsed_seconds':time.monotonic()-started,'permanent_service':False,'results':results}
    immutable(args.report,encoded(result))  # new report path each invocation, no overwrite
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    if any(r.get('ready') is False for r in results):raise SystemExit(1)


if __name__=='__main__':main()
