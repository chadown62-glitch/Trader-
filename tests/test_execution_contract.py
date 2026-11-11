"""Contrat d'exécution unique : l'étiquetage, la simulation accélérée et le trading en argent fictif
donnent les mêmes résultats sur un même flux artificiel déterministe, sans jamais utiliser un prix futur."""

import copy

import numpy as np
import pandas as pd
import pytest

from sentinelle.agent.agent import TradingAgent
from sentinelle.ai.exits import ExitState, check_hits, r_multiple
from sentinelle.ai.features import compute_features
from sentinelle.ai.labeling import label_candidates, simulate_candidate
from sentinelle.ai.strategies import STRATEGIES
from sentinelle.backtest.engine import SimClock, run_backtest
from sentinelle.config import DEFAULT_SETTINGS, apply_update
from sentinelle.execution.base import OrderError
from sentinelle.execution.contract import (entry_possible, limit_fill, limit_hit, market_fill, signal_time, stop_fill,
                                           stop_hit, valid_bar)
from sentinelle.execution.paper import PaperBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.market.markets import MarketInfo
from sentinelle.risk.manager import RiskManager
from sentinelle.storage.db import Database

from .helpers import HOUR, make_series

SYM = "BTC/USDC"
TF = "1h"
FEE, SLIP = 0.001, 0.0005
# marché sans arrondi de prix ni minimum : les moteurs peuvent être comparés au centime de centime près
FINE = MarketInfo(SYM, "BTC", "USDC", amount_step=1e-10, price_step=1e-10, min_amount=0.0, min_notional=0.0)
STOP_LABELS = {"stop", "breakeven", "trailing"}


def settings():
    """Réglages permissifs : aucune limite de risque ne doit masquer une différence entre moteurs."""
    return apply_update(DEFAULT_SETTINGS, {
        "general": {"watchlist": [SYM]},
        "paper": {"fee_rate": FEE, "slippage_bps": SLIP * 10_000},
        "ai": {"timeframe": TF, "regime_filter": False, "exploration": True, "strategies": {name: True for name in STRATEGIES}},
        "risk": {"risk_per_trade": 0.001, "max_positions": 5, "max_exposure": 1.0, "max_exposure_per_asset": 0.5, "daily_loss_limit": 0.10,
                 "weekly_loss_limit": 0.20, "max_drawdown": 0.25, "max_consecutive_losses": 20, "pause_hours_after_losses": 1,
                 "min_reward_to_fees": 1.0, "cooldown_minutes_after_loss": 0},
    })


@pytest.fixture(scope="module")
def flow():
    """Flux artificiel déterministe : 15 000 bougies d'une heure, avec des phases de hausse et de baisse.

    Les 4 800 premières (200 jours) servent à établir le climat de marché : aucun trade n'y est pris."""
    df = make_series(15_000, seed=21, start=30_000, vol=0.007,
                     drift_fn=lambda ts: 0.0006 * np.sin(np.arange(len(ts)) / 350.0))
    feat = compute_features(df, TF)
    labels = label_candidates(feat, SYM, TF, list(STRATEGIES), FEE, SLIP)
    bt = run_backtest({SYM: df}, TF, settings(), None, exploration=True, markets={SYM: FINE})
    return df, feat, labels, bt


def label_of(labels, trade):
    """Exemple étiqueté correspondant à un trade : même stratégie, décision prise à l'instant de l'entrée."""
    rows = labels[(labels["strategy"] == trade["strategy"]) & (labels["decision_ts"] == int(round(trade["entry_ts"] * 1000)))]
    assert len(rows) == 1, f"pas d'exemple étiqueté pour le trade {trade['strategy']} du {trade['entry_ts']}"
    return rows.iloc[0]


def reason_matches(label: str, reason_close: str) -> bool:
    if label == "target":
        return reason_close == "Objectif atteint"
    if label == "time":
        return reason_close == "Durée maximale atteinte"
    return label in STOP_LABELS and reason_close in ("Stop-loss touché", "Stop dépassé : sortie immédiate")


# ---------------------------------------------------------------------------
# 1. Les formules du contrat
# ---------------------------------------------------------------------------
def test_contract_formulas():
    assert signal_time(3_600_000, 3_600_000) == 7_200_000  # un signal n'existe qu'à la clôture de sa bougie
    assert market_fill(100.0, "buy", 0.001) == pytest.approx(100.1) and market_fill(100.0, "sell", 0.001) == pytest.approx(99.9)
    assert market_fill(100.0, "buy", 0.001, 0.002) == pytest.approx(100.3)  # demi-écart du carnet en plus du glissement
    assert entry_possible(99.01, 99.0) and not entry_possible(99.0, 99.0) and not entry_possible(98.0, 99.0) and entry_possible(1.0, None)
    # stop : au pire du stop et de l'ouverture (trou de cotation), glissement compris
    assert stop_hit(95.0, 101.0, 94.0, "sell") and not stop_hit(95.0, 101.0, 95.5, "sell")
    assert stop_fill(95.0, 100.0, "sell", 0.001) == pytest.approx(95.0 * 0.999)
    assert stop_fill(95.0, 92.0, "sell", 0.001) == pytest.approx(92.0 * 0.999)  # ouverture sous le stop
    # ordre à cours limité : au prix limite, ou à l'ouverture si elle est meilleure ; pas de glissement
    assert limit_hit(110.0, 111.0, 100.0, "sell") and not limit_hit(110.0, 109.0, 100.0, "sell")
    assert limit_fill(110.0, 105.0, "sell") == 110.0 and limit_fill(110.0, 112.0, "sell") == 112.0
    assert limit_fill(90.0, 95.0, "buy") == 90.0 and limit_fill(90.0, 88.0, "buy") == 88.0
    # données incohérentes
    assert valid_bar(100, 101, 99, 100.5)
    for bad in ((100, 99, 98, 100), (100, 101, 100.5, 100), (0, 1, 0, 1), (float("nan"), 101, 99, 100), (100, float("inf"), 99, 100),
                (-1, 1, -2, 0.5), (None, 1, 1, 1)):
        assert not valid_bar(*bad)


def test_stop_comes_before_target_in_the_same_bar_everywhere(db, paper, pm, prices, clock):
    """Stop et objectif touchés dans la même bougie : le stop d'abord, dans l'étiquetage comme chez le courtier."""
    state = ExitState(entry=100.0, stop=98.0, target=103.0, initial_stop=98.0, horizon_bars=10, fee_rate=FEE, slip=SLIP)
    px, reason = check_hits(state, 100.0, 104.0, 97.0)
    assert reason == "stop" and px == pytest.approx(98.0 * (1 - SLIP))

    paper.slip = SLIP
    pos = pm.open("BTC/USDC", 0.01, origin="manual", stop=49_000.0, target=51_500.0)
    clock.advance(60)
    paper.process_bar("BTC/USDC", 50_000.0, 52_000.0, 48_500.0, 50_500.0, int(clock() * 1000))
    closed = pm.get(pos["id"])
    assert closed["status"] == "closed" and closed["reason_close"] == "Stop-loss touché"
    assert closed["exit_price"] == pytest.approx(49_000.0 * (1 - SLIP))


def test_gap_below_the_stop_fills_at_the_open_everywhere(db, paper, pm, prices, clock):
    state = ExitState(entry=100.0, stop=98.0, target=None, initial_stop=98.0, horizon_bars=10, fee_rate=FEE, slip=SLIP)
    px, reason = check_hits(state, 96.0, 97.0, 95.0)
    assert reason == "stop" and px == pytest.approx(96.0 * (1 - SLIP))

    paper.slip = SLIP
    pos = pm.open("BTC/USDC", 0.01, origin="manual", stop=49_000.0)
    entry = pm.get(pos["id"])["entry_price"]
    clock.advance(60)
    paper.process_bar("BTC/USDC", 48_000.0, 48_200.0, 47_500.0, 47_900.0, int(clock() * 1000))
    closed = pm.get(pos["id"])
    assert closed["exit_price"] == pytest.approx(48_000.0 * (1 - SLIP))
    # le résultat en R du courtier est exactement la formule de l'étiquetage
    assert closed["pnl_r"] == pytest.approx(r_multiple(entry, 48_000.0 * (1 - SLIP), 49_000.0, 0.001), abs=1e-9)
    assert closed["pnl_r"] < -1.0  # un trou de cotation coûte plus que le risque prévu


def test_entry_is_abandoned_when_the_first_price_is_already_at_the_stop(db, paper, pm, prices):
    o = np.array([100.0, 97.9, 98.0]); h = o + 1; l = o - 1; c = o + 0.5
    assert simulate_candidate(o, h, l, c, 0, 98.0, 104.0, "trend_breakout", FEE, SLIP, TF) is None  # ouverture sous le stop
    paper.slip = SLIP
    prices["BTC/USDC"] = 48_990.0
    with pytest.raises(OrderError):
        pm.open("BTC/USDC", 0.01, origin="ai", stop=49_000.0)
    with pytest.raises(OrderError):  # prix exactement au niveau du stop : abandon aussi
        prices["BTC/USDC"] = 49_000.0
        pm.open("BTC/USDC", 0.01, origin="ai", stop=49_000.0)
    assert db.scalar("SELECT COUNT(*) FROM fills") == 0
    # c'est le prix relevé qui compte, pas le prix d'exécution : le glissement ne « sauve » pas une entrée
    o2 = np.array([100.0, 98.0, 98.0]); assert simulate_candidate(o2, o2 + 1, o2 - 1, o2 + 0.5, 0, 98.0, 104.0, "trend_breakout", FEE, SLIP, TF) is None


def test_invalid_bars_trigger_neither_decision_nor_execution(db, paper, pm, prices, clock):
    paper.slip = SLIP
    pos = pm.open("BTC/USDC", 0.01, origin="manual", stop=49_000.0, target=51_000.0)
    clock.advance(60)
    for bad in ((50_000.0, 49_000.0, 48_000.0, 50_000.0), (float("nan"), 52_000.0, 48_000.0, 50_000.0), (0.0, 52_000.0, 0.0, 50_000.0)):
        assert paper.process_bar("BTC/USDC", *bad, int(clock() * 1000)) == 0
    assert pm.get(pos["id"])["status"] == "open"
    # étiquetage : une bougie d'entrée incohérente rend le trade impossible
    o = np.array([100.0, 100.0, 100.0]); h = np.array([101.0, 99.0, 101.0]); l = np.array([99.0, 98.0, 99.0]); c = np.array([100.5, 100.0, 100.5])
    assert simulate_candidate(o, h, l, c, 0, 95.0, 110.0, "trend_breakout", FEE, SLIP, TF) is None
    # simulation accélérée : la bougie est ignorée et comptée
    df = make_series(400, seed=5, start=30_000)
    broken = df.copy()
    broken.loc[300, "high"] = broken.loc[300, "low"] * 0.5
    res = run_backtest({SYM: broken}, TF, settings(), None, exploration=True, markets={SYM: FINE})
    assert res["skipped_bars"] == 1 and run_backtest({SYM: df}, TF, settings(), None, exploration=True, markets={SYM: FINE})["skipped_bars"] == 0


# ---------------------------------------------------------------------------
# 2. Étiquetage = simulation accélérée, trade par trade
# ---------------------------------------------------------------------------
def test_backtest_trades_match_the_labelled_examples(flow):
    df, feat, labels, bt = flow
    trades = bt["trades"]
    assert len(trades) >= 60 and bt["open_positions"] <= 1 and bt["skipped_bars"] == 0
    assert bt["execution"] == "ouverture de la bougie suivant le signal"
    ts = feat["ts"].to_numpy()
    opens, closes = feat["open"].to_numpy(), feat["close"].to_numpy()
    index = {int(t): k for k, t in enumerate(ts)}
    seen = set()
    for t in trades:
        lab = label_of(labels, t)
        i = int(lab["idx"])  # bougie du signal
        entry_ms = int(round(t["entry_ts"] * 1000))
        # 1) le signal de la bougie i n'est disponible qu'à sa clôture ; l'achat a lieu à l'ouverture de i+1
        assert entry_ms == signal_time(ts[i], HOUR) == ts[i + 1]
        assert t["entry_price"] == pytest.approx(market_fill(opens[i + 1], "buy", SLIP), rel=1e-9)
        if abs(opens[i + 1] - closes[i]) > 1e-9:
            assert t["entry_price"] != pytest.approx(market_fill(closes[i], "buy", SLIP), rel=1e-12)  # jamais la clôture du signal
        # 2) mêmes niveaux que l'exemple étiqueté
        assert t["initial_stop"] == pytest.approx(lab["stop"], rel=1e-9)
        assert (t["target_price"] is None) == bool(np.isnan(lab["target"]))
        # 3) même issue, même résultat en R, frais et glissement compris
        assert lab["outcome"] is not None, "un trade clôturé doit correspondre à un exemple résolu"
        assert reason_matches(lab["outcome"], t["reason_close"]), (lab["outcome"], t["reason_close"])
        assert t["pnl_r"] == pytest.approx(lab["outcome_r"], abs=1e-6)
        # 4) la sortie a lieu pendant la bougie désignée par l'étiquetage (ou à l'ouverture suivante pour la durée maximale)
        exit_ms = int(round(t["exit_ts"] * 1000))
        assert lab["exit_ts"] - HOUR <= exit_ms <= lab["exit_ts"]
        if lab["outcome"] == "time":
            j = index[int(lab["exit_ts"])]
            assert exit_ms == ts[j] and t["exit_price"] == pytest.approx(market_fill(opens[j], "sell", SLIP), rel=1e-9)
        # 5) le résultat en argent est cohérent avec le résultat en R
        assert t["realized_pnl"] == pytest.approx(t["pnl_r"] * t["risk_amount"], rel=1e-9)
        seen.add("stop" if lab["outcome"] in STOP_LABELS else lab["outcome"])
    assert seen == {"stop", "target", "time"}, f"le flux doit couvrir tous les types de sortie : {seen}"
    assert {lab for lab in labels["outcome"].dropna().unique()} >= {"stop", "target", "time", "trailing"}
    # la simulation ne prend qu'une position à la fois par crypto : elle retient moins d'exemples que l'étiquetage n'en compte
    assert len(trades) < labels["outcome"].notna().sum()


# ---------------------------------------------------------------------------
# 3. Trading en argent fictif au fil des prix = simulation accélérée
# ---------------------------------------------------------------------------
def run_paper_stream(df, feat, cfg, sub_ticks: bool):
    """Moteur « en direct » : à chaque clôture, l'agent décide puis l'ordre part au premier prix relevé ensuite.

    Les prix arrivent ensuite par relevés successifs. Avec `sub_ticks`, chaque bougie est découpée en trois
    relevés (ouverture → plus bas → plus haut → clôture), comme le ferait un relevé toutes les quelques secondes."""
    db = Database(":memory:")
    clock = SimClock(0.0)
    acc = db.insert("accounts", {"name": "flux", "kind": "paper", "role": "main", "quote": "USDC", "initial_balance": 10_000, "created_at": 0})
    prices: dict[str, float] = {}
    broker = PaperBroker(db, acc, "USDC", lambda s: FINE, prices.get, FEE, SLIP * 10_000, clock)
    broker.set_balance("USDC", 10_000.0)
    pm = PositionManager(db, broker, clock=clock)
    risk = RiskManager(db, acc, lambda: cfg, clock)
    agent = TradingAgent("ia", "training", db, pm, risk, lambda: cfg, lambda: None, timeframe=TF, clock=clock)
    agent.running = True
    enabled = [n for n, on in cfg["ai"]["strategies"].items() if on]
    sig = {n: STRATEGIES[n].signal(feat).to_numpy() for n in enabled}
    ts = feat["ts"].to_numpy()
    o, h, l, c = (feat[k].to_numpy() for k in ("open", "high", "low", "close"))
    for k in range(len(ts)):
        t0 = int(ts[k])
        clock.t = t0 / 1000
        prices[SYM] = float(o[k])  # premier prix relevé après la clôture de la bougie k−1
        if k >= 1:
            agent.on_bar(SYM, feat, idx=k - 1, sig_at={n: bool(sig[n][k - 1]) for n in enabled})
        if sub_ticks:
            steps = [(o[k], o[k], l[k], l[k]), (l[k], h[k], l[k], h[k]), (h[k], h[k], c[k], c[k])]
        else:
            steps = [(o[k], h[k], l[k], c[k])]
        for n_step, (so, sh, sl, sc) in enumerate(steps):
            broker.process_bar(SYM, float(so), float(sh), float(sl), float(sc), t0 + n_step)
            prices[SYM] = float(sc)
        db.execute("INSERT OR REPLACE INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (acc, (t0 + HOUR) / 1000, *broker.equity()))
    closed = db.query("SELECT * FROM positions WHERE status = 'closed' ORDER BY exit_ts, id")
    shadows = db.query("SELECT * FROM candidates WHERE source = 'live' ORDER BY ts, strategy")
    equity = broker.equity()[0]
    db.close()
    return closed, shadows, equity


@pytest.mark.parametrize("sub_ticks", [False, True])
def test_paper_stream_matches_the_backtest(flow, sub_ticks):
    df, feat, labels, bt = flow
    closed, shadows, equity = run_paper_stream(df, feat, settings(), sub_ticks)
    trades = bt["trades"]
    assert len(closed) == len(trades)
    for p, t in zip(closed, trades):
        assert (p["symbol"], p["strategy"]) == (t["symbol"], t["strategy"])
        assert p["entry_ts"] == pytest.approx(t["entry_ts"]) and p["entry_price"] == pytest.approx(t["entry_price"], rel=1e-12)
        assert p["exit_price"] == pytest.approx(t["exit_price"], rel=1e-12) and p["pnl_r"] == pytest.approx(t["pnl_r"], abs=1e-9)
        assert p["realized_pnl"] == pytest.approx(t["realized_pnl"], rel=1e-9, abs=1e-12) and p["reason_close"] == t["reason_close"]
        assert p["initial_qty"] == pytest.approx(t["initial_qty"], rel=1e-9) and p["fees"] == pytest.approx(t["fees"], rel=1e-9)
    assert equity == pytest.approx(bt["final_equity"], rel=1e-9)

    # Les situations suivies « en direct » (prises ou refusées) reçoivent exactement l'issue de l'étiquetage
    lab = labels.set_index(["ts", "strategy"])
    resolved = [s for s in shadows if s["outcome"] in ("stop", "breakeven", "trailing", "target", "time")]
    assert len(resolved) >= 200
    for s in resolved:
        row = lab.loc[(s["ts"], s["strategy"])]
        assert s["outcome"] == row["outcome"] and s["outcome_r"] == pytest.approx(row["outcome_r"], abs=1e-9)
        assert s["exit_ts"] == row["exit_ts"]
    # et un exemple encore inconnu de l'étiquetage (fin des données) n'est jamais résolu en direct
    pending = {(int(r["ts"]), r["strategy"]) for _, r in labels[labels["outcome"].isna()].iterrows()}
    assert not pending & {(s["ts"], s["strategy"]) for s in resolved}


# ---------------------------------------------------------------------------
# 4. Aucune décision n'utilise un prix futur
# ---------------------------------------------------------------------------
def test_decisions_do_not_depend_on_future_prices(flow):
    df, feat, labels, bt = flow
    cut = 11_000
    past = df.iloc[:cut].reset_index(drop=True)
    # même passé, avenir totalement différent
    other = make_series(len(df) - cut, seed=999, start=float(df["close"].iloc[cut - 1]), vol=0.02,
                        start_ts=int(df["ts"].iloc[cut]))
    altered = pd.concat([past, other], ignore_index=True)
    cutoff = float(df["ts"].iloc[cut - 2]) / 1000  # décisions prises avant la fin du passé commun

    def before(trades):
        return [t for t in trades if t["entry_ts"] <= cutoff]

    full = before(bt["trades"])
    short = before(run_backtest({SYM: past}, TF, settings(), None, exploration=True, markets={SYM: FINE})["trades"])
    alt = before(run_backtest({SYM: altered}, TF, settings(), None, exploration=True, markets={SYM: FINE})["trades"])
    keys = ("strategy", "entry_ts", "entry_price", "initial_stop", "target_price", "initial_qty")
    assert len(full) >= 30

    def entries(trades, until):
        return [tuple(t[k] for k in keys) for t in trades if t["entry_ts"] <= until]

    # toutes les entrées décidées dans le passé commun sont identiques, quel que soit l'avenir
    last_common = min(short[-1]["entry_ts"], alt[-1]["entry_ts"], full[-1]["entry_ts"])
    assert entries(full, last_common) == entries(short, last_common) == entries(alt, last_common)
    # et les trades entièrement terminés dans le passé commun ont exactement le même résultat
    done = [t for t in full if t["exit_ts"] <= cutoff]
    done_alt = {(t["strategy"], t["entry_ts"]): t for t in alt}
    assert len(done) >= 30
    for t in done:
        twin = done_alt[(t["strategy"], t["entry_ts"])]
        assert twin["exit_price"] == t["exit_price"] and twin["pnl_r"] == t["pnl_r"] and twin["exit_ts"] == t["exit_ts"]

    # les caractéristiques et les signaux d'une bougie ne changent pas quand l'avenir change
    f_alt = compute_features(altered, TF)
    cols = [c for c in feat.columns if feat[c].dtype.kind == "f"]
    np.testing.assert_allclose(feat[cols].iloc[:cut].to_numpy(), f_alt[cols].iloc[:cut].to_numpy(), rtol=1e-9, atol=1e-12, equal_nan=True)
    for name, st in STRATEGIES.items():
        assert st.signal(feat).iloc[:cut].tolist() == st.signal(f_alt).iloc[:cut].tolist(), name
    # étiquetage : les niveaux d'un exemple ne dépendent que du passé ; son issue, que des bougies jusqu'à sa sortie
    lab_alt = label_candidates(f_alt, SYM, TF, list(STRATEGIES), FEE, SLIP).set_index(["ts", "strategy"])
    early = labels[labels["exit_ts"] <= df["ts"].iloc[cut - 2]].set_index(["ts", "strategy"])
    assert len(early) >= 300
    same = lab_alt.loc[early.index]
    for col in ("stop", "target", "outcome_r", "exit_ts", "cost_r"):
        np.testing.assert_allclose(early[col].to_numpy(dtype=float), same[col].to_numpy(dtype=float), rtol=1e-12, equal_nan=True)
    assert early["outcome"].tolist() == same["outcome"].tolist()


def test_last_bar_signal_is_never_executed_without_a_next_price(flow):
    """Un signal donné par la toute dernière bougie n'a pas encore de « premier prix disponible » : rien n'est exécuté ni étiqueté."""
    df, feat, labels, bt = flow
    last_ts = int(feat["ts"].iloc[-1])
    assert all(int(round(t["entry_ts"] * 1000)) <= last_ts for t in bt["trades"])
    tail = labels[labels["ts"] == last_ts]
    assert tail["outcome"].isna().all()
    # tronquer juste après un signal : l'exemple existe, sans issue, et la simulation n'a rien acheté à cette clôture
    sig_rows = labels[labels["outcome"].notna()]
    i = int(sig_rows.iloc[len(sig_rows) // 2]["idx"])
    cut = df.iloc[: i + 1].reset_index(drop=True)
    f_cut = compute_features(cut, TF)
    lab_cut = label_candidates(f_cut, SYM, TF, list(STRATEGIES), FEE, SLIP)
    assert lab_cut[lab_cut["idx"] == i]["outcome"].isna().all() and len(lab_cut[lab_cut["idx"] == i]) >= 1
    res = run_backtest({SYM: cut}, TF, settings(), None, exploration=True, markets={SYM: FINE})
    assert all(int(round(t["entry_ts"] * 1000)) <= int(cut["ts"].iloc[-1]) for t in res["trades"])


def test_extra_cost_and_capital_change_the_simulation_as_expected(flow):
    df, feat, labels, bt = flow
    cfg = settings()
    costly = run_backtest({SYM: df}, TF, cfg, None, exploration=True, markets={SYM: FINE}, extra_cost=0.0005)
    assert costly["fees_paid"] > bt["fees_paid"] and costly["final_equity"] < bt["final_equity"]
    assert copy.deepcopy(cfg)["paper"]["fee_rate"] == FEE  # les réglages d'origine ne sont pas modifiés
    # petit capital et minimum réel de la plateforme (5 USDC) : la taille prudente est trop petite, elle est portée
    # au minimum tant que le risque reste sous 1 % du capital ; le stop reste toujours acceptable par la plateforme
    real = MarketInfo(SYM, "BTC", "USDC", amount_step=1e-5, price_step=0.01, min_amount=1e-5, min_notional=5.0)
    small = run_backtest({SYM: df}, TF, cfg, None, exploration=True, markets={SYM: real}, initial=33.0)
    assert small["initial"] == 33.0 and small["metrics"]["n"] > 0
    for t in small["trades"]:
        assert t["initial_qty"] * t["initial_stop"] >= 5.0 and t["risk_amount"] <= 0.01 * 36.0
    # plafond de risque abaissé à 0,1 % : ces ordres ne peuvent plus être portés au minimum, ils sont refusés (pas simulés)
    strict_cfg = apply_update(cfg, {"risk": {"min_order_max_risk": 0.001}})
    strict = run_backtest({SYM: df}, TF, strict_cfg, None, exploration=True, markets={SYM: real}, initial=33.0)
    assert strict["metrics"]["n"] < small["metrics"]["n"] and strict["agent_stats"]["refused"] > small["agent_stats"]["refused"]


def test_panel_simulation_uses_the_main_portfolio_conditions(tmp_path):
    """La simulation lancée depuis le panel tourne dans les conditions du portefeuille : son capital, converti, et le coût de conversion."""
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.common import UserError
    from sentinelle.services.core import App

    app = App(tmp_path, source=SyntheticSource([SYM], days=5), start=False,
              settings_override={"market": {"source": "synthetic"}, "general": {"watchlist": [SYM]}})
    try:
        app.fx.quote = lambda *a, **kw: pytest.fail("Une cotation actuelle a été consultée")
        cond = app.backtest_conditions()
        assert cond["capital"] == 30.0 and cond["currency"] == "EUR" and cond["initial"] == 30.0
        assert cond["extra_cost"] == pytest.approx(0.0005) and "archivés" in cond["fx"]
        assert app.backtest_conditions(100.0)["initial"] == 100.0
        with pytest.raises(UserError):
            app.backtest_conditions(5.0)
        app.db.set_meta("portfolio_mode", "legacy")  # ancienne installation : simulation dans la monnaie de cotation, sans conversion
        assert app.backtest_conditions() == {"initial": 10_000.0, "extra_cost": 0.0, "currency": "USDC", "capital": 10_000.0, "fx": None}
    finally:
        app.db.close()
