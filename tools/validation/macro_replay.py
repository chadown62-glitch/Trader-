"""Separate preregistered counterfactual replay; never writes the main wallet or v2/v3."""
import bisect
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from sentinelle.ai.evaluation import compare_portfolios,portfolio_metrics
from sentinelle.backtest.engine import run_backtest
from sentinelle.execution.liquidity import BookSnapshot
from sentinelle.market.markets import MarketInfo,generic_market
from sentinelle.news.shadow.sources import SOURCES
from tools.validation.archive import encoded,digest,immutable,canonical_rows

ARMS=('prices_only','prices_calendar','prices_calendar_news')


def data_hash(histories):
    return digest(encoded({s:histories[s][['ts','open','high','low','close','volume']].values.tolist() for s in sorted(histories)}))


def context_hash(store,as_of):
    # Pin all observed versions in scope, including revisions; do not mutate the sidecar.
    result={}
    for table in ('observations','analyses','fetches'):
        result[table]=store.rows(f'SELECT * FROM {table} WHERE stored_at<=? ORDER BY id',(as_of,)) if table!='analyses' else store.rows('SELECT * FROM analyses WHERE available_at<=? ORDER BY id',(as_of,))
    return digest(encoded(result))


class BookTape:
    def __init__(self,events):
        self.events=sorted(events,key=lambda e:(e[0],e[1]))
        self.by_symbol={}
        seen=set()
        for stamp,symbol,book in self.events:
            if not isinstance(book,BookSnapshot) or book.timestamp!=stamp or (stamp,symbol) in seen:
                raise ValueError('Invalid or duplicate L2 replay event')
            seen.add((stamp,symbol))
            self.by_symbol.setdefault(symbol,[]).append((stamp,book))
        self.sha=digest(encoded(
            [[t,s,[[str(p),str(q)] for p,q in b.bids],[[str(p),str(q)] for p,q in b.asks],b.source] for t,s,b in self.events]))
    def get(self,symbol,now):
        rows=self.by_symbol.get(symbol,[])
        index=bisect.bisect_right([r[0] for r in rows],now)-1
        if index<0:raise ValueError('No causally available book')
        return rows[index][1]
    def between(self,start,end):
        return [e for e in self.events if start<e[0]<=end]


def prepare(path,histories,settings,model,store,*,start_ms,end_ms,tape=None,markets=None,now=None):
    if settings['general']['quote']!='EUR' or settings['portfolio']['initial']!=30:
        raise ValueError('EUR30 only; non-EUR requires reviewed dated FX, never a stablecoin assumption')
    if not start_ms<end_ms or not histories:raise ValueError('Empty replay period')
    if any(not s.endswith('/EUR') for s in histories):raise ValueError('Non-EUR histories require reviewed dated FX')
    for frame in histories.values():
        canonical_rows(frame[['ts','open','high','low','close','volume']].values.tolist())
        if frame.ts.diff().dropna().ne(14_400_000).any():raise ValueError('Replay refuses gaps/duplicates')
    code={}
    root=Path(__file__).resolve().parents[2]
    for name in ('sentinelle/agent/agent.py','sentinelle/backtest/engine.py','sentinelle/risk/manager.py',
                 'sentinelle/execution/paper.py','tools/validation/macro_replay.py'):
        code[name]=digest((root/name).read_bytes())
    model_sha=hashlib.sha256(encoded({'version':model.version,'params':model.params,'threshold':model.threshold,'trained_until':model.trained_until})).hexdigest() if model else None
    # Exact serialized artifact is mandatory for actual model replay.
    import io,joblib
    if model:
        buffer=io.BytesIO();joblib.dump(model,buffer);model_sha=digest(buffer.getvalue())
        if model.trained_until is None or model.trained_until>=start_ms:raise ValueError('Model has seen replay observations')
    protocol={'schema':1,'name':'macro-replay-v1','registered_at':time.time() if now is None else now,
              'origin':'synthetic_fixture','purpose':'engineering_validation_only','promotion_allowed':False,
              'arms':ARMS,'initial_eur':30,'settings':settings,'model_sha':model_sha,
              'markets':{s:asdict(markets[s] if markets else generic_market(s)) for s in histories},
              'data_sha':data_hash(histories),'context_sha':context_hash(store,end_ms/1000),
              'book_sha':tape.sha if tape else None,'execution':'recorded_l2' if tape else 'OHLC_cost_approximation_no_historical_book',
              'start_ms':start_ms,'end_ms':end_ms,'code':code,
              'rules':{'calendar':'abstain on frozen shadow calendar_would_abstain',
                       'news':'abstain on frozen shadow news_would_abstain; no directional model'},
              'criteria':{'min_trades':30,'min_days':28,'block_days':7,'reps':2000,'contrasts':2,
                          'no_promotion_from_fixture':True,'common_costs_risk_capital':True}}
    immutable(Path(path),encoded(protocol))
    immutable(Path(str(path)+'.sha256'),(digest(encoded(protocol))+'\n').encode())
    return protocol


def replay(path,histories,model,store,*,tape=None,markets=None):
    raw=Path(path).read_bytes()
    if digest(raw)!=Path(str(path)+'.sha256').read_text().strip():raise ValueError('Replay protocol integrity failure')
    p=json.loads(raw)
    if p['origin']!='synthetic_fixture' or p['promotion_allowed']:raise ValueError('Only separately reviewed fixture replay enabled')
    import io,joblib
    model_sha=None
    if model:
        buffer=io.BytesIO();joblib.dump(model,buffer);model_sha=digest(buffer.getvalue())
    if p['model_sha']!=model_sha or p['data_sha']!=data_hash(histories) or p['book_sha']!=(tape.sha if tape else None):
        raise ValueError('Replay inputs changed after registration')
    if p['context_sha']!=context_hash(store,p['end_ms']/1000):raise ValueError('Replay context changed after registration')
    root=Path(__file__).resolve().parents[2]
    if any(digest((root/f).read_bytes())!=sha for f,sha in p['code'].items()):raise ValueError('Replay implementation changed')
    output={}
    if markets and encoded({s:asdict(markets[s]) for s in histories})!=encoded(p['markets']):
        raise ValueError('Replay market limits changed')
    markets={s:MarketInfo(**v) for s,v in p['markets'].items()}
    for arm in ARMS:
        decisions=[]
        def gate(symbol,at,arm=arm,decisions=decisions):
            context=store.context(at,SOURCES).to_dict()
            advisory=context['advisory']
            refuse=arm!='prices_only' and advisory['calendar_would_abstain']
            refuse=refuse or (arm=='prices_calendar_news' and advisory['news_would_abstain'])
            decisions.append({'as_of':at,'symbol':symbol,'context_sha':digest(encoded(context)),'refused':bool(refuse)})
            return 'Macro contrefactuel préenregistré : abstention' if refuse else None
        result=run_backtest(histories,'4h',p['settings'],model,start_ts=p['start_ms'],end_ts=p['end_ms']-1,
                            initial=30,account_currency='EUR',exploration=model is None,markets=markets,
                            research_trace=True,entry_gate=gate,
                            book_provider=tape.get if tape else None,book_events=tape.between if tape else None)
        output[arm]={'metrics':portfolio_metrics(result),'trace':result,'decisions':decisions}
    comparisons={}
    for arm in ARMS[1:]:
        comparison=compare_portfolios(output[arm]['metrics'],output['prices_only']['metrics'],trials=2)
        # A fixture cannot approve or promote a model, regardless of bootstrap direction.
        comparison['statistical_diagnostic']=comparison.pop('verdict')
        comparison['verdict']='FIXTURE UNIQUEMENT — AUCUNE PROMOTION'
        comparisons[arm]=comparison
    return {'protocol_sha':digest(raw),'origin':p['origin'],'execution':p['execution'],'promotion_allowed':False,
            'arms':output,'comparisons':comparisons,'note':'Three isolated EUR30 wallets; no subtraction of skipped trade PnL.'}
