"""Backtest événementiel : rejoue l'historique bougie par bougie avec exactement
le même agent, le même gestionnaire de risque et le même broker simulé que
l'entraînement en direct. C'est la « simulation accélérée ».

Déroulement d'une bougie k (contrat d'exécution unique, execution/contract.py) :
1. à son OUVERTURE, premier prix disponible après la clôture de la bougie k−1 : l'agent
   applique les décisions fondées sur la bougie k−1 (entrées, sorties à la durée maximale,
   relèvement des stops). Un achat est donc exécuté à l'ouverture de k, jamais à la clôture
   de la bougie qui a donné le signal ;
2. PENDANT la bougie : les stops puis les objectifs touchés sont exécutés ;
3. à sa CLÔTURE : le capital est relevé.

La simulation tourne dans sa propre base de données en mémoire : elle ne peut pas
toucher au portefeuille principal.
"""

from __future__ import annotations

import copy
import time
from typing import Callable

import numpy as np
import pandas as pd

from ..agent.agent import TradingAgent
from ..ai.features import compute_features
from ..ai.metrics import equity_metrics, trade_metrics
from ..ai.model import MetaModel
from ..ai.strategies import STRATEGIES
from ..config import timeframe_ms
from ..execution.contract import valid_bar
from ..execution.paper import PaperBroker
from ..execution.positions import PositionManager
from ..market.markets import MarketInfo, generic_market
from ..risk.manager import RiskManager
from ..storage.db import Database


class SimClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def run_backtest(
    histories: dict[str, pd.DataFrame],
    timeframe: str,
    settings: dict,
    model: MetaModel | None,
    *,
    start_ts: int | None = None,
    end_ts: int | None = None,
    initial: float = 10_000.0,
    exploration: bool = False,
    markets: dict[str, MarketInfo] | None = None,
    btc: pd.DataFrame | None = None,
    progress: Callable[[float, str], None] | None = None,
    extra_cost: float = 0.0,
) -> dict:
    """`initial` : capital de départ, dans la monnaie de cotation. `extra_cost` : coût supplémentaire par
    opération (fraction), par exemple la conversion en euros du portefeuille principal."""
    t_run = time.time()
    tf = timeframe_ms(timeframe)
    cfg = copy.deepcopy(settings)
    cfg["ai"]["exploration"] = exploration
    cfg["paper"]["fee_rate"] = cfg["paper"]["fee_rate"] + max(0.0, extra_cost)
    quote = cfg["general"]["quote"]
    db = Database(":memory:")
    clock = SimClock(0.0)
    acc = db.insert("accounts", {"name": "backtest", "kind": "paper", "role": "backtest", "quote": quote, "initial_balance": initial, "created_at": 0})
    prices: dict[str, float] = {}
    mk = dict(markets or {})

    def market_fn(symbol: str) -> MarketInfo:
        if symbol not in mk:
            mk[symbol] = generic_market(symbol)
        return mk[symbol]

    broker = PaperBroker(db, acc, quote, market_fn, prices.get, cfg["paper"]["fee_rate"], cfg["paper"]["slippage_bps"], clock)
    broker.set_balance(quote, initial)
    pm = PositionManager(db, broker, clock=clock)
    risk = RiskManager(db, acc, lambda: cfg, clock)
    agent = TradingAgent("backtest", "backtest", db, pm, risk, lambda: cfg, lambda: model, timeframe=timeframe, clock=clock, record=False)
    agent.running = True

    # Pré-calcul (sans fuite d'information : chaque valeur n'utilise que le passé)
    btc_sym = next((s for s in histories if s.split("/")[0] == "BTC"), None)
    btc_df = btc if btc is not None else (histories.get(btc_sym) if btc_sym else None)
    enabled = [n for n, on in cfg["ai"]["strategies"].items() if on]
    data = {}
    for sym, df in histories.items():
        df = df.reset_index(drop=True)
        ctx = None if (sym == btc_sym and btc is None) else btc_df
        feat = compute_features(df, timeframe, btc=ctx)
        sig = {n: STRATEGIES[n].signal(feat).to_numpy() for n in enabled}
        data[sym] = (feat, sig)
    all_ts = np.unique(np.concatenate([d[0]["ts"].to_numpy() for d in data.values()])) if data else np.array([])
    if start_ts is not None:
        all_ts = all_ts[all_ts >= start_ts]
    if end_ts is not None:
        all_ts = all_ts[all_ts <= end_ts]
    pos_index = {sym: {int(t): k for k, t in enumerate(d[0]["ts"].to_numpy())} for sym, d in data.items()}

    equity_curve: list[list[float]] = []
    bh_start: dict[str, float] = {}
    skipped = 0
    n = len(all_ts)
    for step_i, ts in enumerate(all_ts):
        ts = int(ts)
        bars = []
        for sym in data:
            k = pos_index[sym].get(ts)
            if k is None:
                continue
            f = data[sym][0]
            o, h, l, c = (float(f[col].iat[k]) for col in ("open", "high", "low", "close"))
            if not valid_bar(o, h, l, c):
                skipped += 1  # bougie incohérente : ni décision ni exécution
                continue
            bars.append((sym, k, o, h, l, c))
        # 1) ouverture de la bougie : premier prix disponible après les décisions de la clôture précédente
        clock.t = ts / 1000
        for sym, _k, o, _h, _l, _c in bars:
            prices[sym] = o
            bh_start.setdefault(sym, o)
        for sym, k, *_ in bars:
            if k >= 1:
                feat, sig = data[sym]
                agent.on_bar(sym, feat, idx=k - 1, sig_at={name: bool(sig[name][k - 1]) for name in enabled})
        # 2) pendant la bougie : stops puis objectifs
        for sym, _k, o, h, l, c in bars:
            broker.process_bar(sym, o, h, l, c, ts)
            prices[sym] = c
        # 3) clôture : relevé du capital
        clock.t = (ts + tf) / 1000
        eq, cash = broker.equity()
        db.execute("INSERT OR REPLACE INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (acc, clock.t, eq, cash))
        equity_curve.append([ts + tf, eq])
        if progress and step_i % 500 == 0:
            progress(step_i / max(1, n), f"Simulation : bougie {step_i}/{n}")

    # bilan
    closed = db.query("SELECT * FROM positions WHERE status = 'closed' ORDER BY exit_ts")
    open_left = db.query("SELECT * FROM positions WHERE status = 'open'")
    rs = [p["pnl_r"] for p in closed if p["pnl_r"] is not None]
    eqs = [e[1] for e in equity_curve] or [initial]
    bh = None
    if bh_start and equity_curve:
        rets = [prices[s] / bh_start[s] - 1 for s in bh_start if bh_start[s]]
        bh = float(np.mean(rets) * 100) if rets else None
    step = max(1, len(equity_curve) // 400)
    result = {
        "timeframe": timeframe,
        "start": int(all_ts[0]) if n else None,
        "end": int(all_ts[-1] + tf) if n else None,
        "initial": initial,
        "final_equity": eqs[-1],
        "equity": equity_curve[::step] + ([equity_curve[-1]] if equity_curve and (len(equity_curve) - 1) % step else []),
        "trades": [
            {k: p[k] for k in ("symbol", "strategy", "entry_ts", "exit_ts", "entry_price", "exit_price", "realized_pnl", "pnl_r", "reason_close", "p_win", "expected_r",
                               "initial_stop", "target_price", "qty", "initial_qty", "fees", "risk_amount")}
            for p in closed[-500:]
        ],
        "metrics": trade_metrics(rs),
        "equity_metrics": equity_metrics(eqs),
        "buy_hold_pct": bh,
        "open_positions": len(open_left),
        "fees_paid": float(sum(p["fees"] for p in closed)),
        "exposure_pct": None,
        "duration_s": round(time.time() - t_run, 1),
        "agent_stats": agent.stats,
        "halted": risk.halted()[0],
        "skipped_bars": skipped,
        "execution": "ouverture de la bougie suivant le signal",
    }
    db.close()
    return result
