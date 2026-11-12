"""Real SQLite abrupt-exit test: no mocks of the simulated execution or journal."""
import copy
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sentinelle.agent.agent import TradingAgent
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.execution.paper import PaperBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.risk.manager import RiskManager
from sentinelle.storage.db import Database
from tests.conftest import FakeClock, MARKETS, Prices
from tests.test_risk_agent_backtest import _feat_with_signal


def scenario(path, fault):
    db=Database(path)
    clock=FakeClock()
    if not db.one('SELECT id FROM accounts'):
        aid=db.insert('accounts',{'name':'isolated fault test','kind':'paper','role':'manual','quote':'USDC','initial_balance':10000,'created_at':clock()})
    else:aid=db.scalar('SELECT id FROM accounts')
    _,feat,idx=_feat_with_signal();sub=feat.iloc[:idx[-1]+1].reset_index(drop=True)
    prices=Prices({'ETH/USDC':float(sub.close.iloc[-1])})
    broker=PaperBroker(db,aid,'USDC',lambda s:MARKETS[s],prices,clock=clock)
    if not db.one('SELECT 1 FROM balances'):broker.set_balance('USDC',10000)
    pm=PositionManager(db,broker,clock=clock)
    cfg=copy.deepcopy(DEFAULT_SETTINGS);cfg['ai']['regime_filter']=False
    cfg['ai']['strategies']={k:k=='trend_breakout' for k in cfg['ai']['strategies']}
    cfg['risk']['min_reward_to_fees']=1.
    risk=RiskManager(db,aid,lambda:cfg,clock)
    agent=TradingAgent('fault-test','training',db,pm,risk,lambda:cfg,lambda:None,timeframe='1h',clock=clock)
    agent.running=True
    original=pm.open
    def open_then_crash(*a,**kw):
        assert db.one("SELECT id FROM candidates WHERE decision='préparé'")
        result=original(*a,**kw)
        assert db.scalar('SELECT COUNT(*) FROM fills')>0
        if fault=='exit':os._exit(77)  # no Python cleanup, transaction still open
        if fault=='exception':raise RuntimeError('power failure after fill')
        return result
    pm.open=open_then_crash
    if fault=='exception':
        try:agent.on_bar('ETH/USDC',sub)
        except RuntimeError:pass
        assert not agent.last_bar and agent.stats['entries']==0
        pm.open=original
    agent.on_bar('ETH/USDC',sub)
    db.close()


def test_abrupt_exit_then_two_fresh_process_restarts(tmp_path):
    path=tmp_path/'fault.db'
    def child(fault):
        return subprocess.run([sys.executable,__file__,str(path),fault],capture_output=True,text=True)
    r=child('exit');assert r.returncode==77,r.stderr
    db=Database(path)
    assert db.scalar('SELECT COUNT(*) FROM fills')==0
    assert db.scalar("SELECT COUNT(*) FROM candidates WHERE decision='préparé'")==1
    assert db.scalar("SELECT total FROM balances WHERE asset='USDC'")==10000
    db.close()
    for phase in ('ok','ok'):
        r=child(phase);assert r.returncode==0,r.stderr
        db=Database(path)
        assert db.scalar('SELECT COUNT(*) FROM fills')==1
        assert db.scalar('SELECT COUNT(*) FROM positions')==1
        assert db.scalar("SELECT COUNT(*) FROM candidates WHERE decision='pris'")==1
        assert db.scalar('SELECT COUNT(*) FROM prospective_bars')==1
        db.close()


def test_exception_rolls_back_and_same_process_can_retry(tmp_path):
    scenario(tmp_path/'exception.db','exception')
    db=Database(tmp_path/'exception.db')
    assert db.scalar('SELECT COUNT(*) FROM fills')==1
    assert db.scalar("SELECT COUNT(*) FROM candidates WHERE decision='pris'")==1
    db.close()


if __name__=='__main__':scenario(sys.argv[1],sys.argv[2])
