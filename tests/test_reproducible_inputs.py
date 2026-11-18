import numpy as np
import pytest

from tools.validation.production import market_frame
from tools.validation.reproducibility import canonical_history, load_snapshot, save_snapshot


def test_snapshot_preserves_exact_values_and_schema(tmp_path):
    frame,_=market_frame('trend',41,512)
    h={'LAB/EUR':frame}
    original=canonical_history(h)
    # CSV/JSON encoders and incidental dataframe column order cannot alter this representation.
    assert canonical_history({'LAB/EUR':frame[frame.columns[::-1]]})==original
    save_snapshot(tmp_path,h,{'scenario':'trend','seed':41,'bars':512})
    restored,manifest=load_snapshot(tmp_path)
    assert canonical_history(restored)==original
    assert 'numpy' in manifest['runtime']['packages']
    changed=frame.copy()
    changed.loc[0,'volume']=np.nextafter(changed.loc[0,'volume'],np.inf)
    assert canonical_history({'LAB/EUR':changed})!=original  # never mask one-ULP differences
    path=tmp_path/'inputs.jsonl.gz'
    path.write_bytes(path.read_bytes()+b'x')
    with pytest.raises(ValueError,match='integrity'):
        load_snapshot(tmp_path)


def test_frozen_inputs_replay_the_same_experiment():
    from tools.validation.production import ASSET_SEED_OFFSETS, evaluate
    history={'LAB/EUR':market_frame('noise',8,1800)[0]}
    for index, offset in enumerate(ASSET_SEED_OFFSETS[1:],2):
        history[f'LAB{index}/EUR']=market_frame('noise',8+offset,1800)[0]
    a=evaluate('noise',8,bars=1800,test_bars=60,sizes=(100,))
    b=evaluate('noise',8,bars=1800,test_bars=60,sizes=(100,),frozen_history=history)
    assert a==b
    history['LAB/EUR'].loc[0,'ts']+=1
    with pytest.raises(ValueError,match='Chronologie'):
        evaluate('noise',8,bars=1800,test_bars=60,sizes=(100,),frozen_history=history)


def test_replay_exposure_trace_and_refusal_counts_are_reconciled():
    from copy import deepcopy
    from sentinelle.backtest.engine import run_backtest
    from sentinelle.config import DEFAULT_SETTINGS
    frame,_=market_frame('trend',41,1800)
    cfg=deepcopy(DEFAULT_SETTINGS);cfg['general']['quote']='EUR'
    result=run_backtest({'LAB/EUR':frame},'4h',cfg,None,initial=30,account_currency='EUR',
                        exploration=True,start_ts=int(frame.ts.iloc[-100]),research_trace=True)
    assert len(result['equity_cash'])==len(result['equity'])
    assert all(0<=cash<=equity+1e-9 for _,equity,cash in result['equity_cash'])
    assert sum(result['refusal_reasons'].values())==result['agent_stats']['refused']
