"""Prepare/check an immutable prospective run. Never starts a service."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from tools.validation.archive import digest, encoded, immutable


def prepare(model, settings, output, start, end, *, now=None):
    now=now or datetime.now(timezone.utc)
    begin=datetime.fromisoformat(start);finish=datetime.fromisoformat(end)
    if begin.tzinfo is None or finish.tzinfo is None or not now<begin<finish:
        raise ValueError('Prospective period must be future, timezone-aware and nonempty')
    if settings['general']['quote']!='EUR' or settings['portfolio']['initial']!=30:
        raise ValueError('Requires EUR paper wallet with initial capital 30')
    settings=json.loads(json.dumps(settings))
    settings['ai']['learning']=False
    output=Path(output)
    immutable(output/'model.joblib',Path(model).read_bytes())
    immutable(output/'settings.json',encoded(settings))
    protocol={'schema':1,'registered_at':now.isoformat(),'start':begin.isoformat(),'end':finish.isoformat(),
              'model_sha256':digest((output/'model.joblib').read_bytes()),
              'settings_sha256':digest((output/'settings.json').read_bytes()),'initial_eur':30,
              'execution':'paper_only','promotion_allowed':False,'supervised_only':True,
              'criteria':{'block_days':7,'bootstrap_reps':2000,'minimum_closed_trades':30,'minimum_days':28,
                          'positive_absolute_and_paired_lower_bounds_required':True,
                          'historical_reuse_for_promotion':False},
              'metrics':['net_pnl','daily_returns','fees','drawdown','refusals','auc','brier','calibration'],
              'launch_status':'PREPARED_NOT_STARTED'}
    immutable(output/'protocol.json',encoded(protocol))
    immutable(output/'protocol.sha256',(digest(encoded(protocol))+'\n').encode())
    return protocol


def check(root):
    root=Path(root)
    if (root/'INVALID.txt').exists():raise ValueError('Invalid preparation; use the corrected prospective-v2 snapshot')
    raw=(root/'protocol.json').read_bytes()
    if digest(raw)!=(root/'protocol.sha256').read_text().strip():raise ValueError('Protocol integrity failure')
    p=json.loads(raw)
    for name in ('model','settings'):
        path=root/('model.joblib' if name=='model' else 'settings.json')
        if digest(path.read_bytes())!=p[name+'_sha256']:raise ValueError(name+' integrity failure')
    return p


def main():
    c=argparse.ArgumentParser(description=__doc__);c.add_argument('action',choices=['prepare','check'])
    c.add_argument('--root',type=Path,required=True);c.add_argument('--model',type=Path)
    c.add_argument('--settings',type=Path);c.add_argument('--start');c.add_argument('--end');a=c.parse_args()
    p=check(a.root) if a.action=='check' else prepare(a.model,json.loads(a.settings.read_text()),a.root,a.start,a.end)
    print(json.dumps(p,indent=2))
if __name__=='__main__':main()


class SupervisedObserver:
    """Explicit, finite calls only. No thread, downloader, scheduler or auto-retraining."""
    def __init__(self, app, root):
        from sentinelle.agent.agent import TradingAgent
        from sentinelle.ai.model import MetaModel
        self.root=Path(root);self.protocol=check(root)
        self.protocol_sha=digest((self.root/'protocol.json').read_bytes())
        self.settings=json.loads((self.root/'settings.json').read_bytes())
        account=app.main_account()
        if account['quote']!='EUR' or account['initial_balance']!=30:
            raise ValueError('Expected existing main EUR paper wallet, never reset it')
        if app.settings != self.settings or app.settings['market']['source']=='synthetic':
            raise ValueError('Application must use exactly the frozen public-market settings')
        if any(a.running for a in app.agents.values()):
            raise ValueError('Stop other autonomous agents before frozen observation')
        rt=app.runtime(account['id'])
        model=MetaModel.load(self.root/'model.joblib')  # trusted, locally prepared artifact only
        self.app=app
        app.models.lock_policy(self.protocol_sha,begin_timestamp(self.protocol['start']),begin_timestamp(self.protocol['end']),
                               'prospective-v2: modèle et politique gelés')
        self.agent=TradingAgent('prospective-frozen','training',app.db,rt.positions,rt.risk,
                               lambda:self.settings,lambda:model,timeframe=self.settings['ai']['timeframe'],
                               clock=app.market_clock,fresh_fn=lambda s:app.hub.is_fresh(s,180))
        self.agent.running=True

    def on_bar(self, symbol, features):
        if self.app.settings != self.settings:
            raise ValueError('Application settings changed during observation')
        p=check(self.root)
        if digest((self.root/'protocol.json').read_bytes())!=self.protocol_sha:
            raise ValueError('Protocol replaced after initialization')
        now=datetime.now(timezone.utc)
        if not datetime.fromisoformat(p['start'])<=now<datetime.fromisoformat(p['end']):
            raise ValueError('Outside reserved observation period')
        if symbol not in self.settings['general']['watchlist'] or len(features)==0:
            raise ValueError('Asset or features unavailable')
        available=(int(features.ts.iloc[-1])+self.agent.tf_ms)/1000
        if not 0<=self.app.market_clock()-available<=180:
            raise ValueError('Unclosed or stale observation')
        self.agent.on_bar(symbol,features)


def begin_timestamp(value):
    return datetime.fromisoformat(value).timestamp()
