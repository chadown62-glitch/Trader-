"""Complete public-GET fixture -> frozen-model observer -> EUR30 ledger, no real network."""
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinelle.ai.bench import make_candidates
from sentinelle.ai.model import MetaModel,payoff_table
from sentinelle.services.core import App
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.bus import EventBus
from sentinelle.storage.db import Database
from tools.prospective import prepare
from tools.public_capture import PublicCapture,RetryLater,SYMBOLS,load_capture
from tools.observe import step,preflight
from tools.validation.archive import STEP_MS,encoded,digest

ROOT=Path(__file__).resolve().parents[1]
END=int(datetime(2026,11,15,4,tzinfo=timezone.utc).timestamp()*1000)
NOW=END/1000+10


class FrozenDate(datetime):
    @classmethod
    def now(cls,tz=None):return datetime.fromtimestamp(NOW,tz or timezone.utc)


def fixtures():
    rows=[]
    for i in range(1500):
        close=100*math.exp(.0003*i+.006*math.sin(i/8))
        if i==1499:close*=1.04
        opening=rows[-1][4] if rows else close
        rows.append([END-(1500-i)*STEP_MS,opening,max(opening,close)*1.002,min(opening,close)*.998,close,10000+100*i])
    return rows


def public_client(*,fail=None,crash=False):
    rows=fixtures()
    def transport(request):
        path=request.url.path.rsplit('/',1)[-1]
        q=request.url.params
        if path=='time':return httpx.Response(200,json={'serverTime':int(NOW*1000)})
        if path=='exchangeInfo':
            return httpx.Response(200,json={'symbols':[{'baseAsset':s.split('/')[0],'quoteAsset':'EUR','status':'TRADING',
              'filters':[{'filterType':'LOT_SIZE','stepSize':'.000001','minQty':'.000001'},
                         {'filterType':'PRICE_FILTER','tickSize':'.01'},{'filterType':'NOTIONAL','minNotional':'1'}]} for s in SYMBOLS]})
        if path=='klines':
            selected=[r for r in rows if int(q['startTime'])<=r[0]<=int(q['endTime'])][:1000]
            if selected and selected[0][0]==rows[1000][0] and q['symbol']=='BTCEUR':
                if crash:os._exit(77)
                if fail and fail[0]:
                    fail[0]=False
                    return httpx.Response(429,headers={'Retry-After':'2'},json={'error':'rate limited fixture'})
            return httpx.Response(200,json=selected)
        if path=='depth':
            close=rows[-1][4]
            return httpx.Response(200,json={'lastUpdateId':1,'bids':[[str(close*.999),'100']],
                                         'asks':[[str(close*1.001),'100']]})
        raise AssertionError('Unexpected public endpoint '+path)
    return httpx.Client(transport=httpx.MockTransport(transport))


def setup(root,*,real_model=False):
    cfg=json.loads((ROOT/'research/prospective-v2/settings.json').read_bytes())
    app=App(root/'app',start=False,source=SyntheticSource(SYMBOLS,days=2),settings_override=cfg)
    app.db.close()
    if real_model:
        protocol_root=ROOT/'research/prospective-v2'
    else:
        rows=make_candidates(400,'simple',seed=1)[0]
        model=MetaModel('4h',recipe='prudent').fit(rows)
        model.payoffs=payoff_table(rows);model.threshold=-1e9;model.version='synthetic-coverage-only'
        model.metrics={'verdict':'validé'}
        path=root/'fixture.joblib';model.save(path)
        protocol_root=root/'fixture-protocol'
        prepare(path,cfg,protocol_root,'2026-11-13T00:00:00+00:00','2027-02-13T00:00:00+00:00',
                now=datetime(2026,11,12,tzinfo=timezone.utc))
    return protocol_root


def capture(root):
    with public_client() as client:
        c=PublicCapture(root/'capture',client,clock=lambda:NOW)
        try:return c.collect(origin='synthetic_fixture')
        finally:c.close()


def test_full_get_archive_model_paper_journal_report_and_repeat(tmp_path):
    protocol=setup(tmp_path);capture(tmp_path)
    dbpath=tmp_path/'app/sentinelle.db'
    before=dbpath.read_bytes()
    dry=step(tmp_path/'app',protocol,tmp_path/'capture',clock=lambda:NOW)
    assert dry['ready'] and dry['mode']=='dry_run' and dbpath.read_bytes()==before
    with patch('tools.prospective.datetime',FrozenDate):
        report=step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW)
    db=Database(dbpath)
    n=db.scalar('SELECT COUNT(*) FROM fills')
    assert n>0
    assert report['performance']['capital']['initial']==30
    assert report['performance']['result']['check_gap']==pytest.approx(0,abs=1e-8)
    assert report['journal']['rows'] and report['origin']=='synthetic_fixture'
    assert db.scalar('SELECT COUNT(*) FROM candidates WHERE decision="préparé"')==0
    payloads=db.query('SELECT id,event FROM event_outbox')
    assert payloads and report['outbox_pending']>0
    db.close()
    with patch('tools.prospective.datetime',FrozenDate):
        step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW)
    db=Database(dbpath)
    assert db.scalar('SELECT COUNT(*) FROM fills')==n
    assert db.query('SELECT id,event FROM event_outbox')==payloads
    db.close()


def test_real_v2_artifact_and_features_traverse_bounded_observer(tmp_path):
    protocol=setup(tmp_path,real_model=True);capture(tmp_path)
    raw=(protocol/'model.joblib').read_bytes()
    with patch('tools.prospective.datetime',FrozenDate):
        report=step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW)
    assert report['ready'] and report['steps_complete']
    assert report['journal']['rows']
    assert (protocol/'model.joblib').read_bytes()==raw
    assert report['performance']['capital']['initial']==30


def test_429_retains_pages_and_resumes_without_download_of_completed_requests(tmp_path):
    failed=[True]; clock=[NOW]
    with public_client(fail=failed) as client:
        c=PublicCapture(tmp_path,client,clock=lambda:clock[0])
        with pytest.raises(RetryLater):c.collect(origin='synthetic_fixture')
        before={p:p.read_bytes() for p in (tmp_path/'http').glob('*.json')}
        with pytest.raises(RetryLater,match='backoff'):c.collect(origin='synthetic_fixture')
        clock[0]+=2
        m=c.collect(origin='synthetic_fixture');c.close()
    assert len(m['archives'])==3
    assert all(p.read_bytes()==v for p,v in before.items())
    assert len(load_capture(tmp_path)[1]['BTC/EUR'])==1500


@pytest.mark.parametrize('age',[181,-1])
def test_stale_or_future_capture_refused_without_wallet_write(tmp_path,age):
    protocol=setup(tmp_path);capture(tmp_path)
    path=tmp_path/'app/sentinelle.db';before=path.read_bytes()
    result=step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:END/1000+age)
    assert not result['ready'] and path.read_bytes()==before


def test_corrupt_raw_capture_refused_without_overwrite(tmp_path):
    protocol=setup(tmp_path);capture(tmp_path)
    p=next((tmp_path/'capture/http').glob('*.json'));p.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='integrity'):preflight(tmp_path/'app',protocol,tmp_path/'capture',now=NOW)
    assert p.read_bytes()==b'corrupt'


def test_missing_installation_never_created(tmp_path):
    with pytest.raises(ValueError,match='Existing installation'):preflight(tmp_path/'absent',ROOT/'research/prospective-v2',tmp_path/'capture',now=NOW)
    assert not (tmp_path/'absent').exists()


def test_rollback_never_emits_phantom_notification_and_failed_listener_retries(tmp_path):
    db=Database(tmp_path/'outbox.db');bus=EventBus(db);seen=[]
    bus.add_listener(lambda _,event:seen.append(event['event_id']))
    with pytest.raises(RuntimeError):
        with db.transaction():
            bus.publish('position_opened',{'position_id':1})
            assert seen==[]
            raise RuntimeError('before commit')
    assert not seen and db.scalar('SELECT COUNT(*) FROM event_outbox')==0
    with db.transaction():
        bus.publish('position_opened',{'position_id':2})
        with pytest.raises(RuntimeError):
            with db.transaction():
                bus.publish('decision',{'kind':'pris'})
                raise RuntimeError('nested rollback')
        assert not seen
    assert len(seen)==1 and bus.drain()==0
    db.close()


def child(root,phase):
    if phase=='fetch':
        with public_client(crash=True) as client:
            c=PublicCapture(root/'capture',client,clock=lambda:NOW)
            c.collect(origin='synthetic_fixture')
    if phase in ('before','after','dispatch'):
        db=Database(root/'events.db');bus=EventBus(db)
        inbox=sqlite3.connect(root/'inbox.db')
        inbox.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,event TEXT)');inbox.commit()
        def sink(_,event):
            inbox.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(event['event_id'],json.dumps(event,sort_keys=True)));inbox.commit()
            if phase=='dispatch':os._exit(77)  # receipt committed, outbox ack not yet committed
        bus.add_listener(sink)
        if phase=='before':
            with db.transaction():
                bus.publish('decision',{'kind':'pris'})
                os._exit(77)
        if phase=='after':
            db.after_commit=lambda _:None  # deterministic injection after DB commit, before dispatch
            with db.transaction():bus.publish('decision',{'kind':'pris'})
            os._exit(77)
        if phase=='dispatch':bus.drain()
    if phase in ('fill','annotation'):
        from sentinelle.execution.positions import PositionManager
        from sentinelle.news.shadow.service import MacroShadow
        from tools.macro_shadow import load_protocol
        original=PositionManager.open
        def crash_after_fill(self,*a,**kw):
            original(self,*a,**kw)
            assert self.db.scalar('SELECT COUNT(*) FROM fills')>0
            os._exit(77)
        if phase=='fill':PositionManager.open=crash_after_fill
        macro=MacroShadow(root/'macro')
        original_annotation=macro.annotate
        def annotate(*a,**kw):
            result=original_annotation(*a,**kw)
            os._exit(77)
            return result
        macro.annotate=annotate
        with patch('tools.prospective.datetime',FrozenDate),patch('tools.macro_shadow.time.time',return_value=NOW):
            load_protocol(ROOT/'research/macro-shadow-v3/protocol.json')
            step(root/'app',root/'fixture-protocol',root/'capture',execute=True,clock=lambda:NOW,
                 macro=macro if phase=='annotation' else None,macro_protocol=ROOT/'research/macro-shadow-v3/protocol.json')


def test_real_process_crashes_fetch_fill_annotation_and_idempotent_resumption(tmp_path):
    def run(phase):return subprocess.run([sys.executable,__file__,str(tmp_path),phase],capture_output=True,text=True)
    protocol=setup(tmp_path)
    r=run('fetch');assert r.returncode==77,r.stderr
    capture(tmp_path)  # partial successful pages reused
    r=run('fill');assert r.returncode==77,r.stderr
    db=Database(tmp_path/'app/sentinelle.db')
    assert db.scalar('SELECT COUNT(*) FROM fills')==0
    assert db.scalar('SELECT COUNT(*) FROM candidates WHERE decision="préparé"')>0
    assert db.scalar('SELECT COUNT(*) FROM event_outbox')==0
    db.close()
    r=run('annotation');assert r.returncode==77,r.stderr
    db=Database(tmp_path/'app/sentinelle.db');fills=db.query('SELECT * FROM fills');db.close()
    from sentinelle.news.shadow.service import MacroShadow
    macro=MacroShadow(tmp_path/'macro')
    annotations=macro.store.rows('SELECT * FROM decision_contexts')
    assert annotations
    for _ in range(2):
        with patch('tools.prospective.datetime',FrozenDate),patch('tools.macro_shadow.time.time',return_value=NOW):
            step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW,macro=macro,
                 macro_protocol=ROOT/'research/macro-shadow-v3/protocol.json')
        db=Database(tmp_path/'app/sentinelle.db')
        assert db.query('SELECT * FROM fills')==fills
        assert macro.store.rows('SELECT * FROM decision_contexts')==annotations
        db.close()
    macro.stop()


def test_outbox_crashes_before_commit_after_commit_and_after_receipt(tmp_path):
    def run(phase):return subprocess.run([sys.executable,__file__,str(tmp_path),phase],capture_output=True,text=True)
    r=run('before');assert r.returncode==77,r.stderr
    db=Database(tmp_path/'events.db')
    assert db.scalar('SELECT COUNT(*) FROM event_outbox')==0;db.close()
    r=run('after');assert r.returncode==77,r.stderr
    r=run('dispatch');assert r.returncode==77,r.stderr
    db=Database(tmp_path/'events.db');bus=EventBus(db)
    inbox=sqlite3.connect(tmp_path/'inbox.db')
    def sink(_,event):
        inbox.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)',(event['event_id'],json.dumps(event,sort_keys=True)));inbox.commit()
    bus.add_listener(sink)
    assert bus.drain()==1 and bus.drain()==0
    assert inbox.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]==1
    assert db.scalar('SELECT COUNT(*) FROM event_outbox WHERE dispatched_at IS NULL')==0
    db.close();inbox.close()




def test_required_macro_protocol_refused_before_any_fill(tmp_path):
    protocol=setup(tmp_path);capture(tmp_path)
    from sentinelle.news.shadow.service import MacroShadow
    macro=MacroShadow(tmp_path/'macro')
    try:
        with patch('tools.prospective.datetime',FrozenDate):
            with pytest.raises(ValueError,match='Macro protocol required'):
                step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW,macro=macro)
        db=Database(tmp_path/'app/sentinelle.db')
        assert db.scalar('SELECT COUNT(*) FROM fills')==0
        db.close()
    finally:macro.stop()


@pytest.mark.parametrize('change',['capture','protocol'])
def test_changed_capture_and_changed_protocol_do_not_reuse_reserved_bar(tmp_path,change):
    protocol=setup(tmp_path);capture(tmp_path)
    with patch('tools.prospective.datetime',FrozenDate):
        step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW)
    p=tmp_path/'capture/capture.json' if change=='capture' else protocol/'protocol.json'
    m=json.loads(p.read_bytes())
    if change=='capture':m['books']['BTC/EUR']['lastUpdateId']=2
    else:m['criteria']['test_conflict']='changed'
    p.write_bytes(encoded(m))
    checksum=p.with_suffix('.sha256') if change=='capture' else protocol/'protocol.sha256'
    checksum.write_text(digest(p.read_bytes())+'\n')
    with patch('tools.prospective.datetime',FrozenDate):
        with pytest.raises(ValueError,match='Conflicting capture|Depth receipt or identity mismatch'):
            step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW)


def test_failed_listener_leaves_event_pending_and_stable_id(tmp_path):
    db=Database(tmp_path/'retry.db');bus=EventBus(db);attempts=[]
    def sink(_,event):
        attempts.append(event['event_id'])
        if len(attempts)==1:raise RuntimeError('consumer unavailable')
    bus.add_listener(sink)
    with db.transaction():bus.publish('decision',{'kind':'refusé'})
    assert db.scalar('SELECT COUNT(*) FROM event_outbox WHERE dispatched_at IS NULL')==1
    assert bus.drain()==1 and bus.drain()==0
    assert len(attempts)==2 and len(set(attempts))==1
    db.close()


def test_malformed_response_is_retained_and_not_used_as_a_page(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'<html>bad response</html>'))) as client:
        collector=PublicCapture(tmp_path,client,clock=lambda:NOW)
        with pytest.raises(ValueError,match='original bytes retained'):collector.get('time')
        assert any(p.read_bytes()==b'<html>bad response</html>' for p in (tmp_path/'http').glob('*.json'))
        assert collector.db.execute('SELECT COUNT(*) FROM requests WHERE sha IS NOT NULL').fetchone()[0]==0
        collector.close()


def test_cli_failure_emits_bounded_report_without_installation(tmp_path):
    report=tmp_path/'failure.json'
    result=subprocess.run([sys.executable,'-m','tools.observe','run','--data',str(tmp_path/'absent'),
                '--capture',str(tmp_path/'none'),'--report',str(report)],cwd=ROOT,capture_output=True,text=True,timeout=15)
    assert result.returncode==1
    value=json.loads(report.read_bytes())
    assert value['permanent_service'] is False and value['results'][0]['ready'] is False
    assert not (tmp_path/'absent').exists()

def test_reboot_does_not_advance_existing_position_bar_counter_twice(tmp_path):
    protocol=setup(tmp_path);capture(tmp_path)
    def run():
        with patch('tools.prospective.datetime',FrozenDate):
            step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:NOW)
    run()
    db=Database(tmp_path/'app/sentinelle.db')
    # Isolated fixture models a position already open before the observed bar.
    db.execute('UPDATE positions SET entry_ts=? WHERE status="open"',(END/1000-2*STEP_MS/1000,))
    db.execute('DELETE FROM position_management_bars')
    db.close()
    run()
    db=Database(tmp_path/'app/sentinelle.db');positions=db.query('SELECT * FROM positions');events=db.query('SELECT * FROM event_outbox');db.close()
    run()
    db=Database(tmp_path/'app/sentinelle.db')
    assert db.query('SELECT * FROM positions')==positions
    assert db.query('SELECT * FROM event_outbox')==events
    db.close()


if __name__=='__main__':child(Path(sys.argv[1]),sys.argv[2])
