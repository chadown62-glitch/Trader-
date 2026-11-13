"""Descriptive replay on previously explored archives. Four isolated EUR30 portfolios, no training."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import subprocess
from datetime import datetime, timezone

from sentinelle.ai.evaluation import block_interval, portfolio_metrics
from sentinelle.ai.model import MetaModel
from sentinelle.backtest.engine import run_backtest
from sentinelle.market.markets import MarketInfo
from tools.validation.archive import digest, encoded, immutable, load_archive

ROOT = Path(__file__).resolve().parents[2]
PERIODS = [('2025-07-01','2026-01-01'),('2026-01-01','2026-07-01')]


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()*1000)


def run(archives, output):
    from tools.prospective import check
    protocol = check(ROOT/'research/prospective-v2')
    settings = json.loads((ROOT/'research/prospective-v2/settings.json').read_bytes())
    model = MetaModel.load(ROOT/'research/prospective-v2/model.joblib')
    output = Path(output)
    history, provenance = {}, {}
    for symbol in ('BTC/EUR','ETH/EUR','SOL/EUR'):
        folder = Path(archives)/symbol.replace('/','-')
        history[symbol], manifest = load_archive(folder)
        provenance[symbol] = {'manifest_sha256': digest((folder/'manifest.json').read_bytes()),
                              'dataset_sha256': manifest['dataset_sha256'], 'bars': len(history[symbol])}
    specification = {'schema': 'sentinelle.baselines/v1', 'category': 'historical_public', 'independent': False,
                     'promotion_allowed': False, 'periods': PERIODS, 'initial_eur_per_isolated_arm': 30,
                     'model_sha256': protocol['model_sha256'], 'settings': settings, 'archives': provenance,
                     'code_revision': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                     'limitations': ['Already explored historical windows; no independent holdout.',
                                     'OHLC backtest with modeled fees/slippage and fixed minimums, no historical L2.',
                                     'Backtest execution differs from operational training/on-trial sizing; not pilot forecast.',
                                     'Four research wallets do not multiply the principal operational EUR30 ledger.']}
    immutable(output/'specification.json', encoded(specification))
    markets = {s: MarketInfo(s,*s.split('/'),amount_step=1e-6,price_step=.01,min_notional=5) for s in history}
    results = []
    for start,end in PERIODS:
        # Future bars never enter the engine, including feature computation.
        hist = {s: f[f.ts < stamp(end)].copy() for s,f in history.items()}
        period = {'start': start, 'end_exclusive': end, 'arms': {}}
        for name in ('CASH','HOLD','TREND','V2'):
            cfg = deepcopy(settings)
            if name == 'TREND':
                cfg['ai']['strategies'] = {k: k=='trend_follow' for k in cfg['ai']['strategies']}
            bt = run_backtest(hist,'4h',cfg,model if name=='V2' else None, initial=30, account_currency='EUR',
                              markets=markets,start_ts=stamp(start),research_trace=True,
                              reference_policy={'CASH':'cash','HOLD':'passive'}.get(name), exploration=name!='V2')
            metrics = portfolio_metrics(bt)
            values = list(metrics['daily'].values())
            metrics['absolute_daily_interval'] = block_interval(values,block=7,confidence=1-.05/8)
            trace = {'closed_positions': bt['closed_positions'], 'equity_cash': bt['equity_cash'],
                     'refusal_reasons': bt['refusal_reasons'], 'agent_stats': bt['agent_stats']}
            raw = encoded(trace)
            immutable(output/f'{start}-{name}-trace.json', raw)
            period['arms'][name] = {'metrics': metrics, 'trace_sha256': digest(raw),
                                    'verdict': 'NOT_ENOUGH_EVIDENCE', 'realized_avoided_profit': None}
        results.append(period)
    report = {'schema': 'sentinelle.baselines/v1', 'category': 'historical_public',
              'specification_sha256': digest(encoded(specification)), 'results': results,
              'promotion_allowed': False, 'verdict': 'DESCRIPTIVE_ONLY'}
    immutable(output/'results.json', encoded(report))
    immutable(output/'results.sha256', (digest(encoded(report))+'\n').encode())
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.archives,args.output),ensure_ascii=False))
