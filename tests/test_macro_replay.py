"""Independent EUR30 counterfactual wallets using the common execution engine."""
from copy import deepcopy
import json

import pytest

from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.execution.liquidity import BookSnapshot
from sentinelle.market.markets import MarketInfo
from sentinelle.news.shadow.store import ShadowStore
from tools.validation.macro_replay import ARMS,BookTape,prepare,replay
from tools.validation.archive import STEP_MS
from tests.test_observation_e2e import fixtures,END
from sentinelle.market.history import to_frame


def setup(tmp_path,*,l2=False,safe_calendar=False,rumor_delay=-1):
    rows=fixtures();last=rows[-1][4]
    # Explicit next opening is required: the signal at the final close cannot fill at that same close.
    rows.append([END,last,last*1.002,last*.998,last,10000])
    frame=to_frame(rows)
    hist={'BTC/EUR':frame}
    cfg=deepcopy(DEFAULT_SETTINGS)
    cfg['general'].update(quote='EUR',watchlist=['BTC/EUR'])
    cfg['ai'].update(regime_filter=False,exploration_risk_factor=1.)
    cfg['ai']['strategies']={k:k=='trend_breakout' for k in cfg['ai']['strategies']}
    cfg['risk']['min_reward_to_fees']=1.
    markets={'BTC/EUR':MarketInfo('BTC/EUR','BTC','EUR',amount_step=.000001,min_notional=1.)}
    store=ShadowStore(tmp_path/'macro.db',clock=lambda:END/1000)
    if safe_calendar:
        from datetime import datetime,timezone
        from email.utils import formatdate
        from tests.test_macro_shadow import feed
        from sentinelle.news.shadow.parsers import forex,rss,sha
        from sentinelle.news.shadow.sources import SOURCES
        received=END/1000-60
        raw=json.dumps([{'id':'fixture-low','title':'Fixture low impact','country':'EUR','impact':'Low',
                         'date':datetime.fromtimestamp(END/1000+86400,timezone.utc).isoformat()}]).encode()
        store.append('forex_factory',forex(raw),sha(raw),received,stored_at=received)
        store.conn.execute('INSERT INTO fetches(source,url,status,received_at,stored_at,retry_at) VALUES(?,?,?,?,?,?)',
                           ('forex_factory',SOURCES['forex_factory']['url'],'ok',received,received,received+3600))
        raw=feed(title='Rumor Bitcoin fixture',date=formatdate(END/1000-60,usegmt=True))
        stamp=END/1000+rumor_delay
        store.append('coindesk',rss(raw,SOURCES['coindesk']),sha(raw),stamp,stored_at=stamp)
    # Empty calendar remains unknown: both macro arms abstain, never fabricate profits.
    start=int(frame.ts.iloc[-25]);end=END+STEP_MS
    events=[]
    if l2:
        for row in frame.iloc[-26:].itertuples():
            for delta,qty in ((0,'.01'),(1,'100')):
                at=row.ts/1000+delta
                events.append((at,'BTC/EUR',BookSnapshot(((str(row.open*.999),qty),),((str(row.open*1.001),qty),),at,'synthetic-L2')))
        # Subsequent independent observations trigger and partially fill the protective stop.
        for delta,qty,price in ((2,'.02',last*.80),(3,'100',last*.79)):
            at=END/1000+delta
            events.append((at,'BTC/EUR',BookSnapshot(((str(price),qty),),((str(price*1.002),qty),),at,'synthetic-L2')))
    tape=BookTape(events) if l2 else None
    path=tmp_path/'replay.json'
    prepare(path,hist,cfg,None,store,start_ms=start,end_ms=end,tape=tape,markets=markets,now=start/1000-10)
    return path,hist,store,tape,markets


@pytest.mark.parametrize('delay',[-1,10])
def test_news_arm_only_uses_rumor_received_before_decision(tmp_path,delay):
    path,hist,store,tape,markets=setup(tmp_path,l2=True,safe_calendar=True,rumor_delay=delay)
    try:
        result=replay(path,hist,None,store,tape=tape,markets=markets)
        prices=result['arms']['prices_only']['trace']['fills']
        calendar=result['arms']['prices_calendar']['trace']['fills']
        news=result['arms']['prices_calendar_news']['trace']['fills']
        assert prices and prices==calendar
        if delay<0:assert not news
        else:assert news==prices  # old publication with late receipt must not rewrite an earlier entry
    finally:store.close()


@pytest.mark.parametrize('l2',[False,True])
def test_three_independent_wallets_and_costs_not_avoided_profit(tmp_path,l2):
    path,hist,store,tape,markets=setup(tmp_path,l2=l2)
    try:
        r=replay(path,hist,None,store,tape=tape,markets=markets)
        assert r['origin']=='synthetic_fixture' and not r['promotion_allowed']
        assert set(r['arms'])==set(ARMS)
        for arm in r['arms'].values():
            assert arm['trace']['initial']==30 and arm['trace']['currency']=='EUR'
            fills=arm['trace']['fills']
            cash=30+sum(f['cash_amount'] for f in fills)
            assert cash>=0
            assert arm['metrics']['fees_eur']==pytest.approx(sum(f['fee']+float(f.get('fx_cost') or 0) for f in fills))
        assert r['arms']['prices_only']['trace']['fills']
        assert not r['arms']['prices_calendar']['trace']['fills']
        assert r['arms']['prices_calendar']['metrics']['capital_eur']==30
        assert r['arms']['prices_calendar_news']['metrics']['capital_eur']==30
        assert all(c['verdict']=='FIXTURE UNIQUEMENT — AUCUNE PROMOTION' for c in r['comparisons'].values())
        if l2:
            fills=r['arms']['prices_only']['trace']['fills']
            assert all(f['price_source']=='synthetic-L2' for f in fills)
            assert any(f['amount']<=.01 for f in fills)
            assert any(f['amount']>.01 for f in fills)  # same order remainder filled at a later real fixture event
            assert len([f for f in fills if f['side']=='sell'])==2
            assert r['arms']['prices_only']['trace']['closed_positions']
            assert r['arms']['prices_only']['metrics']['capital_eur']==pytest.approx(
                30+sum(f['cash_amount'] for f in fills))
        json.dumps(r,allow_nan=False)
    finally:store.close()


def test_future_context_does_not_change_past_replay(tmp_path):
    path,hist,store,tape,markets=setup(tmp_path)
    try:
        before=replay(path,hist,None,store,markets=markets)
        future=(END+STEP_MS)/1000+10
        payload={'published_at':future,'title':'Future fixture','actual':None}
        store.append('fed',[('news','future',payload)],'a'*64,future,stored_at=future)
        after=replay(path,hist,None,store,markets=markets)
        for arm in ARMS:
            assert before['arms'][arm]['metrics']==after['arms'][arm]['metrics']
            assert before['arms'][arm]['trace']['fills']==after['arms'][arm]['trace']['fills']
            assert before['arms'][arm]['decisions']==after['arms'][arm]['decisions']
    finally:store.close()


def test_changed_data_or_market_rejected_and_no_live_gate(tmp_path):
    path,hist,store,tape,markets=setup(tmp_path)
    try:
        changed={'BTC/EUR':hist['BTC/EUR'].copy()}
        changed['BTC/EUR'].loc[0,'close']*=1.001
        with pytest.raises(ValueError,match='inputs changed'):replay(path,changed,None,store,markets=markets)
        markets['BTC/EUR']=MarketInfo('BTC/EUR','BTC','EUR',min_notional=100)
        with pytest.raises(ValueError,match='market limits'):replay(path,hist,None,store,markets=markets)
        cfg=deepcopy(DEFAULT_SETTINGS);cfg['general']['quote']='USDT'
        with pytest.raises(ValueError,match='dated FX'):
            prepare(tmp_path/'bad.json',hist,cfg,None,store,start_ms=1,end_ms=2)
        from tests.conftest import Prices,MARKETS
        from sentinelle.agent.agent import TradingAgent
        from sentinelle.execution.paper import PaperBroker
        from sentinelle.execution.positions import PositionManager
        from sentinelle.risk.manager import RiskManager
        from sentinelle.storage.db import Database
        db=Database()
        broker=PaperBroker(db,1,'EUR',lambda s:MARKETS[s],Prices({}))
        with pytest.raises(ValueError,match='isolated backtests'):
            TradingAgent('no-live-gate','training',db,PositionManager(db,broker),RiskManager(db,1,lambda:cfg),
                         lambda:cfg,lambda:None,timeframe='4h',entry_gate=lambda s,t:None)
        db.close()
    finally:store.close()
