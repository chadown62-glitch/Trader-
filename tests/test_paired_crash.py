"""Real abrupt exit after a paper fill: durable paired intent, rolled-back execution."""
import copy
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinelle.agent.agent import TradingAgent
from sentinelle.ai.bench import make_candidates
from sentinelle.ai.model import MetaModel, payoff_table
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.execution.paper import PaperBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.market.markets import MarketInfo
from sentinelle.risk.manager import RiskManager
from sentinelle.services.champion import RegisteredComparison, register_pair
from sentinelle.services.models import ModelStore
from sentinelle.storage.db import Database, loads
from tests.conftest import FakeClock, Prices
from tests.test_risk_agent_backtest import _feat_with_signal


def scenario(path, fault):
    db = Database(path)
    _, feat, idx = _feat_with_signal()
    sub = feat.iloc[:idx[-1]+1].reset_index(drop=True)
    now = (int(sub.ts.iloc[-1])+3_600_000)/1000
    clock = FakeClock(now)
    cfg = copy.deepcopy(DEFAULT_SETTINGS)
    cfg["general"].update(quote="EUR", watchlist=["ETH/EUR"])
    cfg["market"]["source"] = "synthetic"
    cfg["ai"].update(timeframe="1h", regime_filter=False)
    cfg["ai"]["strategies"] = {k:k=="trend_breakout" for k in cfg["ai"]["strategies"]}
    cfg["risk"]["min_reward_to_fees"] = 1.
    store = ModelStore(db, Path(path).parent/"models")
    if not db.one("SELECT id FROM accounts"):
        aid = db.insert("accounts", {"name":"isolated EUR30 crash", "kind":"paper", "quote":"EUR", "initial_balance":30})
        rows = make_candidates(350, "simple", seed=9)[0]
        for version in ("crash-a", "crash-b"):
            m = MetaModel("1h", recipe="prudent").fit(rows)
            m.payoffs = payoff_table(rows); m.version = version
            m.trained_until = int((now-86400)*1000)
            store.register(SimpleNamespace(final_model=m,ok=False,trial=False,reasons=["fixture"]), {})
        registered = register_pair(db,store,champion="crash-a",challenger="crash-b",settings=cfg,
                                   start=now,end=now+86400,now=now-10)
        protocol_sha = registered["sha256"]
    else:
        aid = db.scalar("SELECT id FROM accounts")
        protocol_sha = db.scalar("SELECT sha256 FROM learning_protocols")
    comparison = RegisteredComparison(db,store,protocol_sha,clock=clock)
    prices = Prices({"ETH/EUR":float(sub.close.iloc[-1])})
    market = MarketInfo("ETH/EUR","ETH","EUR",amount_step=1e-8,price_step=.001,min_amount=1e-8,min_notional=1.)
    broker = PaperBroker(db,aid,"EUR",lambda s:market,prices,clock=clock)
    if not db.one("SELECT 1 FROM balances"): broker.set_balance("EUR",30)
    pm = PositionManager(db,broker,clock=clock)
    risk = RiskManager(db,aid,lambda:cfg,clock)
    def record(c):
        equity,cash = broker.equity()
        comparison.record(c,cfg,{"equity_eur":equity,"exposure_eur":max(0,equity-cash),"available_at":clock()})
    agent = TradingAgent("paired-fault","training",db,pm,risk,lambda:cfg,lambda:None,
                         timeframe="1h",clock=clock,shadow_fn=record)
    agent.running=True
    original=pm.open
    def fill_then_crash(*a,**kw):
        assert db.scalar("SELECT COUNT(*) FROM paired_predictions")==1
        c=db.one("SELECT features FROM candidates")
        assert loads(c["features"])["plan"]["decision"]=="préparé"
        result=original(*a,**kw)
        assert db.scalar("SELECT COUNT(*) FROM fills")==1
        if fault=="exit": os._exit(77)
        return result
    pm.open=fill_then_crash
    agent.on_bar("ETH/EUR",sub)
    db.close()


def test_paired_forecasts_and_plan_survive_exit_without_duplicate_order(tmp_path):
    path=tmp_path/"paired.db"
    def child(fault):
        return subprocess.run([sys.executable,__file__,str(path),fault],capture_output=True,text=True)
    r=child("exit");assert r.returncode==77,r.stderr
    db=Database(path)
    assert db.scalar("SELECT COUNT(*) FROM fills")==0
    assert db.scalar("SELECT COUNT(*) FROM paired_predictions")==1
    original=db.scalar("SELECT payload FROM paired_predictions")
    assert db.scalar("SELECT total FROM balances WHERE asset='EUR'")==30
    db.close()
    for _ in range(2):
        r=child("ok");assert r.returncode==0,r.stderr
        db=Database(path)
        assert db.scalar("SELECT COUNT(*) FROM fills")==1
        assert db.scalar("SELECT COUNT(*) FROM positions")==1
        assert db.scalar("SELECT COUNT(*) FROM prospective_bars")==1
        assert db.scalar("SELECT COUNT(*) FROM paired_predictions")==1
        assert db.scalar("SELECT payload FROM paired_predictions")==original
        assert db.scalar("SELECT COUNT(*) FROM paired_outcomes")==0
        assert db.scalar("SELECT decision FROM candidates")=="pris"
        assert db.scalar("SELECT candidate_id FROM positions")==db.scalar("SELECT id FROM candidates")
        assert db.scalar("SELECT total FROM balances WHERE asset='EUR'")>=0
        db.close()


def test_frozen_v2_v3_bytes_and_pinned_shadow_implementation():
    import json
    from tools.prospective import check
    from tools.macro_shadow import load_protocol
    from unittest.mock import patch
    root=Path(__file__).resolve().parents[1]
    assert check(root/"research/prospective-v2")["promotion_allowed"] is False
    p=json.loads((root/"research/macro-shadow-v3/protocol.json").read_text())
    with patch('tools.macro_shadow.time.time', return_value=p['observation_start']):
        assert load_protocol(root/"research/macro-shadow-v3/protocol.json")["promotion_allowed"] is False
    assert hashlib.sha256((root/"research/prospective-v2/model.joblib").read_bytes()).hexdigest()==p["baseline_model_sha256"]


if __name__=="__main__": scenario(sys.argv[1],sys.argv[2])
