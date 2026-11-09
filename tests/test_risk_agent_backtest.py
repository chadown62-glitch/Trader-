import copy

import pandas as pd
import pytest

from sentinelle.agent.agent import TradingAgent
from sentinelle.ai.features import compute_features
from sentinelle.ai.strategies import STRATEGIES
from sentinelle.backtest.engine import run_backtest
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.risk.manager import RiskManager

from .conftest import MARKETS
from .helpers import make_series


@pytest.fixture
def settings():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    return s


@pytest.fixture
def risk(db, paper, settings, clock):
    return RiskManager(db, paper.account_id, lambda: settings, clock)


def _eval(risk, **kw):
    args = dict(equity=10_000.0, cash=10_000.0, market=MARKETS["ETH/USDC"], fee_rate=0.001, slip=0.0005)
    args.update(kw)
    return risk.evaluate_entry("ETH/USDC", 2_500.0, 2_450.0, 2_600.0, **args)


def test_position_size_from_risk_per_trade(risk):
    d = _eval(risk)
    assert d.approved
    # 0,5 % de 10 000 = 50 de risque ; 50 / (2500 - 2450) = 1 ETH ; plafond 30 % = 1,2 ETH
    assert d.qty == pytest.approx(1.0)
    assert d.risk_amount == pytest.approx(50.0)


def test_exposure_cap_limits_size(risk, settings):
    settings["risk"]["risk_per_trade"] = 0.02  # 200 de risque => 4 ETH, plafonné à 30 % du capital
    d = _eval(risk)
    assert d.approved
    assert d.qty == pytest.approx(10_000 * 0.3 / 2_500, rel=1e-3)
    assert any("par crypto" in c["detail"] for c in d.checks)


def test_rejects_when_reward_too_small_vs_fees(risk):
    d = risk.evaluate_entry("ETH/USDC", 2_500.0, 2_490.0, 2_505.0, equity=10_000, cash=10_000,
                            market=MARKETS["ETH/USDC"], fee_rate=0.001, slip=0.0005)
    assert not d.approved and "frais" in d.reasons[0]


def test_daily_loss_limit_blocks_entries(risk, db, paper, clock):
    db.execute("INSERT INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (paper.account_id, clock() - 3600, 10_000, 10_000))
    d = _eval(risk, equity=9_750.0)  # -2,5 % dans la journée
    assert not d.approved and "jour" in d.reasons[0]


def test_drawdown_triggers_halt_until_manual_resume(risk, db, paper, clock):
    db.execute("INSERT INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (paper.account_id, clock() - 30 * 86400, 12_000, 12_000))
    d = _eval(risk, equity=10_500.0)  # -12,5 % depuis le plus haut
    assert not d.approved and "Arrêt de sécurité" in d.reasons[0]
    assert risk.halted()[0]
    assert not _eval(risk, equity=12_000.0).approved  # reste bloqué même si ça remonte
    risk.resume()
    assert _eval(risk, equity=10_500.0).approved


def test_consecutive_losses_pause(risk, db, paper, clock):
    for k in range(4):
        db.insert("positions", {"account_id": paper.account_id, "symbol": "BTC/USDC", "origin": "ai", "status": "closed",
                                "realized_pnl": -10.0, "exit_ts": clock() - 3600 * (k + 1)})
    d = _eval(risk)
    assert not d.approved and "pertes d'affilée" in d.reasons[0]
    clock.advance(25 * 3600)
    assert _eval(risk).approved


def test_one_position_per_symbol_and_max_positions(risk, pm, settings):
    pm.open("ETH/USDC", 0.5, origin="ai", stop=2_300)
    assert "déjà ouverte" in _eval(risk).reasons[0]
    settings["risk"]["max_positions"] = 1
    d = risk.evaluate_entry("BTC/USDC", 50_000.0, 49_000.0, 53_000.0, equity=10_000, cash=8_000,
                            market=MARKETS["BTC/USDC"], fee_rate=0.001, slip=0.0005)
    assert not d.approved and "maximal" in d.reasons[0]


def _feat_with_signal():
    """Série de test contenant des signaux « cassure » avec un climat connu."""
    for seed in range(21, 60):
        df = make_series(7000, seed=seed, drift_fn=lambda ts: 0.0002 + 0 * ts)
        feat = compute_features(df, "1h")
        sig = STRATEGIES["trend_breakout"].signal(feat)
        idx = [i for i in range(len(feat)) if sig.iloc[i] and feat["regime"].iloc[i] not in ("unknown", "bear")]
        if idx:
            return df, feat, idx
    raise AssertionError("aucun signal trouvé")


def test_agent_exploration_opens_reduced_risk_position(db, paper, pm, risk, settings, prices, clock):
    df, feat, idx = _feat_with_signal()
    assert idx, "le jeu de test doit contenir au moins un signal"
    i = idx[-1]
    sub = feat.iloc[: i + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    settings["ai"]["regime_filter"] = False
    settings["ai"]["strategies"] = {k: k == "trend_breakout" for k in settings["ai"]["strategies"]}
    settings["risk"]["min_reward_to_fees"] = 1.0
    agent = TradingAgent("ia", "training", db, pm, risk, lambda: settings, lambda: None, timeframe="1h", clock=clock)
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    pos = pm.open_positions("ETH/USDC", agent="ia")
    assert len(pos) == 1
    p = pos[0]
    assert p["origin"] == "ai" and p["strategy"] == "trend_breakout"
    equity = paper.equity()[0]
    assert p["risk_amount"] / equity == pytest.approx(0.005 * 0.5, rel=0.15)  # exploration = demi-risque
    cand = db.one("SELECT * FROM candidates")
    assert cand["decision"] == "pris" and cand["source"] == "live"
    assert db.one("SELECT * FROM decisions WHERE kind = 'pris'") is not None


def test_agent_live_without_model_never_trades(db, paper, pm, risk, settings, prices, clock):
    df, feat, idx = _feat_with_signal()
    i = idx[-1]
    sub = feat.iloc[: i + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    settings["ai"]["regime_filter"] = False
    agent = TradingAgent("ia-reel", "live", db, pm, risk, lambda: settings, lambda: None, timeframe="1h", clock=clock)
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    assert pm.open_positions() == []
    assert "Aucun modèle validé" in db.one("SELECT decision_reason FROM candidates")["decision_reason"]


def test_backtest_runs_and_respects_limits(settings):
    hist = {"BTC/USDC": make_series(9000, seed=31, start=30_000), "ETH/USDC": make_series(9000, seed=32, start=2_000)}
    settings["risk"]["min_reward_to_fees"] = 1.0
    settings["ai"]["regime_filter"] = False
    res = run_backtest(hist, "1h", settings, None, exploration=True, initial=10_000)
    assert res["metrics"]["n"] > 5
    eq = [e[1] for e in res["equity"]]
    assert min(eq) > 0
    assert res["final_equity"] == pytest.approx(eq[-1])
    # sur un marché aléatoire, les frais font perdre de l'argent : le backtest ne doit pas inventer de gains
    assert res["metrics"]["mean_r"] < 0.15
    assert all(t["pnl_r"] is not None for t in res["trades"])
