"""Offline registered exploratory evaluation using the existing Sentinelle pipeline."""
import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from sentinelle.ai.evaluation import block_interval, compare_portfolios, portfolio_metrics
from sentinelle.ai.trainer import build_dataset, train_walk_forward
from sentinelle.backtest.engine import run_backtest
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.market.markets import MarketInfo
from tools.validation.production import forecast
from tools.validation.reproducibility import runtime
from tools.validation.archive import digest, encoded, immutable, load_archive


def stamp(date):
    return int(datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)


def register(root, output):
    """Freeze before training or viewing outcomes. Historical windows were already researched."""
    assets={}
    for s in ('BTC','ETH','SOL'):
        path=Path(root)/f'{s}-EUR'
        _,m=load_archive(path)
        assets[f'{s}/EUR']={'directory':f'{s}-EUR','manifest_sha256':digest((path/'manifest.json').read_bytes()),
                           'dataset_sha256':m['dataset_sha256']}
    cfg=deepcopy(DEFAULT_SETTINGS)
    cfg['general'].update(quote='EUR',watchlist=list(assets))
    cfg['ai'].update(timeframe='4h',recipe='mixte',exploration_risk_factor=1.)
    cfg['paper'].update(fee_rate=.001,slippage_bps=10.)
    protocol={'schema':1,'kind':'exploratory_historical','registered_at_utc':datetime.now(timezone.utc).isoformat(),
              'independent_final':False,'promotion_allowed':False,
              'reason':'2023-2026 was used in previous strategy research (docs/RESULTATS.md). No independent historical final claim.',
              'assets':assets,'timeframe':'4h','initial_eur':30.,'sizes':[500,1500],
              'periods':[['2025-07-01','2026-01-01'],['2026-01-01','2026-07-01']],
              'development':'Resolved candidates strictly before evaluation start; most recent N; pipeline temporal split 48/16/16/20 percent of decision clock, purged exits at every boundary.',
              'champion':'first prespecified size 500, experimental only; no deployment registry modification',
              'challenger':'size 1500, designated before viewing results',
              'references':['cash','passive','all_signals','trend_follow'],
              'market_constraints':{'min_notional_eur':5.,'amount_step':1e-6,'price_step':.01,
                  'scope':'fixed modeled spot constraints, NOT reconstructed historical venue filters'},
              'settings':cfg,'statistical':{'block_days':7,'bootstrap_reps':2000,'trials':2,
                 'minimum_trades':30,'minimum_days':28,'positive_absolute_and_relative_lower_bounds':True},
              'metrics':['net_return','daily','net_eur_per_trade','n_trades','capital_eur','fees_eur','max_drawdown',
                         'profit_factor','mean_win_eur','mean_loss_eur','auc','brier','calibration','exposure'],
              'future_reserved_observation':['2026-11-13','2027-02-13'],
              'future_note':'Do not launch persistent collector here; reserve actual unseen observations separately before any promotion.'}
    immutable(Path(output),encoded(protocol))
    return protocol


def run(root, protocol_path, output):
    pbytes=Path(protocol_path).read_bytes()
    p=json.loads(pbytes)
    if p['promotion_allowed'] or p['independent_final']:
        raise ValueError('This runner supports exploratory history only')
    # Consume once BEFORE evaluating; failures also reserve the attempt, never silently overwrite results.
    output=Path(output)
    immutable(output/'protocol.json',pbytes)
    with (output/'attempt.json').open('xb') as stream:
        stream.write(encoded({'protocol_sha256':digest(pbytes),'started_utc':datetime.now(timezone.utc).isoformat()}))
    history={s:load_archive(Path(root)/a['directory'],a['manifest_sha256'])[0] for s,a in p['assets'].items()}
    cfg=p['settings']; enabled=[s for s,on in cfg['ai']['strategies'].items() if on]
    fee,slip=cfg['paper']['fee_rate'],cfg['paper']['slippage_bps']/10000
    candidates,_=build_dataset(history,'4h',enabled,fee,slip)
    constraints=p['market_constraints']
    markets={s:MarketInfo(s,*s.split('/'),min_notional=constraints['min_notional_eur'],
                         amount_step=constraints['amount_step'],price_step=constraints['price_step']) for s in history}
    results=[]
    for begin,end in p['periods']:
        start_ms,end_ms=stamp(begin),stamp(end)
        hist={s:f[f.ts<end_ms].copy() for s,f in history.items()}
        training=candidates[(candidates.decision_ts<start_ms)&(candidates.exit_ts<start_ms)&candidates.outcome.notna()]
        test=candidates[(candidates.decision_ts>=start_ms)&(candidates.exit_ts<end_ms)&candidates.outcome.notna()]
        exp={'start':begin,'end_exclusive':end,'origin':'historical_public','independent':False,
             'models':[],'references':{},'available_candidates':len(training)}
        def replay(model=None,policy=None,settings=None):
            bt=run_backtest(hist,'4h',settings or cfg,model,initial=30.,account_currency='EUR',markets=markets,
                            start_ts=start_ms,research_trace=True,reference_policy=policy,exploration=model is None)
            m=portfolio_metrics(bt)
            exposures=[1-cash/eq for _,eq,cash in bt['equity_cash'] if eq>0]
            m['exposure']={'maximum':max(exposures,default=0.),'mean_per_bar':sum(exposures)/len(exposures) if exposures else None}
            pnl=[position['realized_pnl'] for position in bt['closed_positions']]
            m['win_rate']=sum(x>0 for x in pnl)/len(pnl) if pnl else None
            m['agent_stats']=bt['agent_stats']
            m['refusal_reasons']=bt['refusal_reasons']
            m['mean_daily_return']=sum(m['daily'].values())/len(m['daily']) if m['daily'] else None
            m['absolute_daily_interval']=block_interval(list(m['daily'].values()),block=7,confidence=1-.05/(2*2))
            return m
        for policy in ('cash','passive','all_signals','trend_follow'):
            settings=deepcopy(cfg)
            ref=None if policy in ('all_signals','trend_follow') else policy
            if policy=='trend_follow':settings['ai']['strategies']={s:s=='trend_follow' for s in settings['ai']['strategies']}
            exp['references'][policy]=replay(policy=ref,settings=settings)
        for size in p['sizes']:
            row={'size':size,'status':'unavailable'};exp['models'].append(row)
            if len(training)<size:
                row['reason']='Insufficient candidates';continue
            sample=training.tail(size)
            fit=train_walk_forward(sample,'4h',with_importance=False,recipe=cfg['ai']['recipe'],
                                   min_edge=cfg['ai']['min_edge_r'],half_life_days=cfg['ai']['half_life_days'])
            if fit.eval_model is None:
                row['reason']=fit.reasons;continue
            model=fit.eval_model
            if model.trained_until>=start_ms:raise ValueError('Temporal leakage')
            probs,_=model.predict(test)
            calibration=[]
            for lo,hi in ((0,.2),(.2,.4),(.4,.6),(.6,.8),(.8,1.00001)):
                mask=(probs>=lo)&(probs<hi)
                if mask.any():calibration.append({'lo':lo,'hi':min(hi,1),'n':int(mask.sum()),
                    'predicted':float(probs[mask].mean()),'observed':float(test.outcome_r.gt(0).to_numpy()[mask].mean())})
            row.update(status='evaluated',forecast=forecast(model,test),calibration=calibration,
                       periods=fit.report['period'],split_counts=fit.report['split_counts'],
                       training_sha256=digest(sample.to_csv(index=False,float_format='%.17g').encode()),
                       recipe=model.chosen,threshold=model.threshold,trained_until_ms=model.trained_until,
                       portfolio=replay(model),training_verdict=fit.verdict)
            row['inactivity_note']='No executed trades; see agent_stats/refusals and threshold. No retuning on this period.' if row['portfolio']['n_trades']==0 else None
        viable=[m for m in exp['models'] if m['status']=='evaluated']
        exp['comparison']={'verdict':'TESTS INCOMPLETS'}
        if len(viable)==2:
            exp['comparison']=compare_portfolios(viable[1]['portfolio'],viable[0]['portfolio'],trials=2)
        exp['promotion']='FORBIDDEN: previously researched historical period'
        results.append(exp)
        immutable(output/f'{begin}.json',encoded(exp))
    report={'code_revision':__import__('subprocess').check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'protocol_sha256':digest(pbytes),'runtime':runtime(),'source':'historical_public',
            'promotion_allowed':False,'results':results}
    immutable(output/'results.json',encoded(report))
    return report


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action',choices=['register','run'])
    cli.add_argument('--root',required=True,type=Path)
    cli.add_argument('--protocol',required=True,type=Path)
    cli.add_argument('--output',type=Path)
    a=cli.parse_args()
    if a.action=='register':register(a.root,a.protocol)
    elif a.output is None:cli.error('--output required for run')
    else:run(a.root,a.protocol,a.output)


if __name__=='__main__':main()
