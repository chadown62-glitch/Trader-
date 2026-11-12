"""Plan écrit avant chaque trade et journal des décisions : raisons, scénarios, issue, cause probable, leçon prudente."""

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sentinelle.agent.agent import TradingAgent
from sentinelle.agent.plan import build_plan, build_scenarios, probability_margin
from sentinelle.ai.bench import make_candidates
from sentinelle.ai.features import FEATURE_COLUMNS
from sentinelle.ai.model import MetaModel, payoff_table
from sentinelle.ai.strategies import STRATEGIES
from sentinelle.api.server import create_api
from sentinelle.config import DEFAULT_SETTINGS, apply_update
from sentinelle.market.sources import SyntheticSource
from sentinelle.risk.manager import RiskDecision, RiskManager
from sentinelle.services.core import App
from sentinelle.services.journal import SHRINK, explain_outcome

from .test_risk_agent_backtest import _feat_with_signal

H = {"X-Sentinelle": "1"}
HOUR = 3_600_000


class Model:
    """Modèle factice : 60 % de chances de gain, +0,5 R, avec une calibration et des issues passées connues."""

    threshold = 0.1

    def __init__(self, verdict="validé", calibration=True):
        self.version = "m-test"
        self.metrics = {"verdict": verdict}
        self.meta = {"calibration": [{"p_mean": 0.35, "win_rate": 0.33, "n": 120}, {"p_mean": 0.58, "win_rate": 0.52, "n": 100}]} if calibration else {}
        self.outcome_mix = {"trend_breakout": {"mean_win": 2.4, "stop_share_of_losses": 0.6}}

    def predict(self, cand):
        return np.full(len(cand), 0.6), np.full(len(cand), 0.5)


# ---------------------------------------------------------------------------
# 1. Le plan
# ---------------------------------------------------------------------------
def test_scenarios_share_one_hundred_percent_and_use_past_outcomes():
    sc = build_scenarios("trend_breakout", 0.6, Model(), cost_r=0.1, reward_r=2.0)
    assert [s["nom"] for s in sc] == ["haussier", "baissier", "neutre"]
    assert sum(s["probabilite"] for s in sc) == pytest.approx(1.0)
    assert sc[1]["probabilite"] == pytest.approx(0.4 * 0.6) and sc[2]["probabilite"] == pytest.approx(0.4 * 0.4)
    assert sc[0]["resultat_r"] == 2.4 and sc[1]["resultat_r"] == pytest.approx(-1.1) and sc[2]["resultat_r"] == pytest.approx(-0.1)
    # sans issues passées pour cette stratégie : part de stops par défaut, gain = rapport gain/risque visé
    other = build_scenarios("trend_follow", 0.5, Model(), cost_r=0.1, reward_r=None)
    assert other[1]["probabilite"] == pytest.approx(0.5 * 0.75) and other[0]["resultat_r"] is None
    # sans modèle : aucune probabilité n'est inventée
    assert all(s["probabilite"] is None for s in build_scenarios("trend_breakout", None, None, 0.1, 2.0))


def test_probability_margin_comes_from_measured_calibration():
    m = probability_margin(Model(), 0.6)
    # la tranche la plus proche annonçait 58 % et a observé 52 % sur 100 cas : écart + incertitude d'échantillon
    se = (0.52 * 0.48 / 100) ** 0.5
    assert m["marge"] == pytest.approx(0.06 + 1.96 * se) and m["n"] == 100 and "100 situations jamais vues" in m["source"]
    assert probability_margin(Model(calibration=False), 0.6)["marge"] is None
    assert probability_margin(None, 0.6)["marge"] is None and probability_margin(Model(), None)["marge"] is None


def test_plan_contains_everything_needed_before_entering():
    st = STRATEGIES["trend_breakout"]
    rd = RiskDecision(approved=True, qty=0.0001, risk_amount=0.15, notional=5.4, costs=0.02, bumped=True)
    plan = build_plan(symbol="BTC/USDC", strategy=st, close=60_000.0, stop=57_000.0, target=66_000.0, atr_pct=0.012, p_win=0.6,
                      expected_r=0.5, threshold=0.1, model=Model(), mode="model", regime="bull", btc_regime="range", cost_r=0.08,
                      horizon_bars=48, timeframe_minutes=240, rd=rd, equity=30.0, currency="EUR", decision="pris", reason="Achat")
    assert plan["decision"] == "pris" and "Cassure de tendance" in plan["raisons"][0] and "haussier" in plan["raisons"][1]
    assert "1,2 %" in plan["raisons"][2] and "60 % de chances" in plan["raisons"][3] and "+0.10 R" in plan["raisons"][3]
    assert plan["entree"]["reference"] == 60_000.0 and "premier prix disponible" in plan["entree"]["regle"]
    assert plan["stop"]["prix"] == 57_000.0 and plan["stop"]["distance_pct"] == pytest.approx(5.0)
    assert plan["objectif"]["rapport_gain_risque"] == pytest.approx(2.0) and plan["objectif"]["distance_pct"] == pytest.approx(10.0)
    assert plan["duree_max"] == {"bougies": 48, "jours": 8.0}
    assert sum(s["probabilite"] for s in plan["scenarios"]) == pytest.approx(1.0) and plan["probabilite"]["marge"] > 0
    assert plan["frais"]["en_r"] == 0.08 and plan["frais"]["montant"] == 0.02 and plan["frais"]["monnaie"] == "EUR"
    assert plan["taille"] == {"quantite": 0.0001, "montant": 5.4, "risque": 0.15, "monnaie": "EUR", "risque_pct": pytest.approx(0.5),
                              "portee_au_minimum": True}
    assert len(plan["abandon"]) == 3 and "stop" in plan["abandon"][0]
    json.dumps(plan, allow_nan=False)
    # stratégie sans objectif fixe, sans modèle : stop suiveur expliqué, aucune probabilité annoncée
    follow = build_plan(symbol="ETH/USDC", strategy=STRATEGIES["trend_follow"], close=3000.0, stop=2800.0, target=None, atr_pct=None,
                        p_win=None, expected_r=None, threshold=None, model=None, mode="exploration", regime="bull", btc_regime="bull",
                        cost_r=0.05, horizon_bars=360, timeframe_minutes=240)
    assert follow["objectif"]["prix"] is None and "stop suiveur" in follow["objectif"]["regle"]
    assert "Aucun modèle validé" in follow["raisons"][-1] and follow["probabilite"]["gain"] is None and follow["esperance_r"] is None
    assert follow["taille"] is None and all(s["probabilite"] is None for s in follow["scenarios"])


def run_agent(db, paper, pm, prices, clock, model, risk_pct=0.005):
    settings = apply_update(DEFAULT_SETTINGS, {"ai": {"regime_filter": False}, "risk": {"min_reward_to_fees": 1.0, "risk_per_trade": risk_pct}})
    settings["ai"]["strategies"] = {k: k == "trend_breakout" for k in settings["ai"]["strategies"]}
    df, feat, idx = _feat_with_signal()
    sub = feat.iloc[: idx[-1] + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    agent = TradingAgent("ia", "training", db, pm, RiskManager(db, paper.account_id, lambda: settings, clock), lambda: settings,
                         lambda: model, timeframe="1h", clock=clock)
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    return sub


def test_agent_records_the_plan_with_taken_and_refused_decisions(db, paper, pm, prices, clock):
    sub = run_agent(db, paper, pm, prices, clock, Model())
    cand = db.one("SELECT * FROM candidates")
    plan = json.loads(cand["features"])["plan"]
    assert cand["decision"] == "pris" and plan["decision"] == "pris" and plan["raison_decision"] == cand["decision_reason"]
    assert plan["entree"]["reference"] == pytest.approx(float(sub["close"].iloc[-1])) and plan["stop"]["prix"] == pytest.approx(cand["stop"])
    assert plan["taille"]["quantite"] > 0 and plan["taille"]["monnaie"] == "USDC" and plan["probabilite"]["gain"] == pytest.approx(0.6)
    pos = db.one("SELECT * FROM positions")
    assert pos["status"] == "open" and json.loads(pos["data"])["bar_ts"] == cand["ts"]  # lien entre la décision et la position
    assert plan["taille"]["quantite"] == pytest.approx(pos["initial_qty"], rel=1e-6)


def test_refused_decision_also_keeps_its_plan(db, paper, pm, prices, clock):
    run_agent(db, paper, pm, prices, clock, Model(verdict="non validé"))  # modèle non validé, exploration : risque réduit… sauf refus
    cand = db.one("SELECT * FROM candidates")
    plan = json.loads(cand["features"])["plan"]
    assert plan["decision"] == cand["decision"] and plan["scenarios"] and plan["stop"]["prix"] == pytest.approx(cand["stop"])
    db.execute("DELETE FROM candidates")
    db.execute("DELETE FROM positions")
    db.execute("DELETE FROM orders")


# ---------------------------------------------------------------------------
# 2. Pourquoi gagné, pourquoi perdu
# ---------------------------------------------------------------------------
def test_outcome_explanations():
    assert explain_outcome(None, 0.1, 0.6, 0.0) is None  # issue inconnue : rien à expliquer
    fees = explain_outcome(-0.05, 0.12, 0.6, 0.0)
    assert fees["cause"] == "frais" and "+0,07 R avant frais" in fees["detail"]
    assert explain_outcome(-1.1, 0.1, 0.55, -0.05)["cause"] == "marché"  # tout le marché a baissé
    up = explain_outcome(2.0, 0.1, 0.45, 0.06)
    assert up["cause"] == "marché" and "+6,0 %" in up["detail"]
    assert explain_outcome(-1.1, 0.1, 0.70, 0.01)["cause"] == "signal"  # confiante, marché calme : le signal s'est trompé
    lucky = explain_outcome(-1.1, 0.1, 0.40, 0.0)
    assert lucky["cause"] == "hasard probable" and "40 %" in lucky["detail"] and "ne prouve rien" in lucky["detail"]
    assert explain_outcome(1.5, 0.1, 0.5, 0.0)["cause"] == "hasard probable"
    assert explain_outcome(-1.1, 0.1, None, None)["cause"] == "inconnu"


# ---------------------------------------------------------------------------
# 3. Journal complet dans l'application
# ---------------------------------------------------------------------------
@pytest.fixture
def app(tmp_path):
    a = App(tmp_path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=5), start=False,
            settings_override={"market": {"source": "synthetic"}, "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}})
    a._startup()
    yield a
    a.db.close()


ROWS = make_candidates(500, "simple", seed=1)[0]


def features(k, regime="bull"):
    row = ROWS.iloc[k]
    return {**{c: float(row[c]) for c in FEATURE_COLUMNS}, "cost_r": 0.1, "contexte": {"regime": regime},
            "plan": {"decision": "x", "scenarios": []}}


def fill(app, model_version="m-ancien"):
    main = app.main_account()["id"]
    t0 = 1_760_000_000_000

    def cand(k, decision, p_win, outcome_r, hours, regime="bull", symbol="ETH/USDC"):
        ts = t0 + k * 24 * HOUR
        app.db.execute(
            "INSERT INTO candidates(symbol, timeframe, ts, strategy, source, entry, stop, target, features, p_win, expected_r, model_version, "
            "decision, decision_reason, account_id, outcome, outcome_r, exit_ts) VALUES (?, '4h', ?, 'trend_breakout', 'live', 100, 95, 110, ?, ?, 0.2, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, ts, json.dumps(features(k, regime)), p_win, model_version, decision, "Achat" if decision == "pris" else "Climat baissier",
             main, None if outcome_r is None else ("target" if outcome_r > 0 else "stop"), outcome_r, None if outcome_r is None else ts + hours * HOUR))
        return ts

    # 0 : pris, gagné pendant une forte hausse du marché ; 1 : pris, perdu par les frais ; 2 : pris, perdu, IA confiante
    # 3 : refusé, aurait perdu ; 4 : pris, en cours ; 5 : pris, perdu alors que l'IA n'annonçait que 40 %
    ts0 = cand(0, "pris", 0.55, 1.8, 28)
    ts1 = cand(1, "pris", 0.55, -0.04, 24)
    cand(2, "pris", 0.72, -1.1, 24)
    cand(3, "refusé", 0.30, -1.0, 24, regime="bear", symbol="BTC/USDC")
    ts4 = cand(4, "pris", 0.55, None, 0)
    cand(5, "pris", 0.40, -1.1, 24)
    # positions réelles (en argent fictif) liées aux décisions 0, 1 et 4
    for ts, status, pnl, r in ((ts0, "closed", 0.27, 1.8), (ts1, "closed", -0.006, -0.04), (ts4, "open", 0.0, None)):
        app.db.insert("positions", {"account_id": main, "symbol": "ETH/USDC", "origin": "ai", "agent": "ia_entrainement", "status": status,
                                    "qty": 0.0 if status == "closed" else 0.003, "initial_qty": 0.003, "entry_price": 2000.0, "entry_ts": (ts + 4 * HOUR) / 1000,
                                    "exit_ts": (ts + 28 * HOUR) / 1000 if status == "closed" else None, "realized_pnl": pnl, "pnl_r": r,
                                    "strategy": "trend_breakout", "reason_close": "Objectif atteint" if pnl > 0 else ("Stop-loss touché" if status == "closed" else None),
                                    "data": json.dumps({"bar_ts": ts})})
    # marché (Bitcoin) : +6 % pendant le premier trade, calme ensuite
    closes_ts = [t0 + h * HOUR for h in range(0, 24 * 8, 4)]
    closes_px = [100.0 * (1.06 if t0 + 26 * HOUR <= t < t0 + 30 * HOUR else 1.0) if t < t0 + 30 * HOUR else 106.0 for t in closes_ts]
    app._btc_closes = lambda tf: (closes_ts, closes_px)
    return main


def test_journal_reviews_each_decision(app):
    fill(app)
    j = app.decision_journal(limit=20)
    rows = {round((r["ts"] * 1000 - 1_760_000_000_000 - 4 * HOUR) / (24 * HOUR)): r for r in j["rows"]}
    assert j["timeframe"] == "4h" and len(j["rows"]) == 6 and j["rows"][0]["ts"] > j["rows"][-1]["ts"]  # plus récentes d'abord
    # 0 : gain pendant que tout le marché montait → le marché, pas le signal ; issue réelle (position), montant en euros
    assert rows[0]["review"]["cause"] == "marché" and rows[0]["outcome"]["pnl"] == 0.27 and rows[0]["outcome"]["currency"] == "EUR"
    assert rows[0]["outcome"]["theoretical"] is False and rows[0]["outcome"]["market_move"] == pytest.approx(0.06)
    assert rows[1]["review"]["cause"] == "frais" and rows[1]["outcome"]["reason_close"] == "Stop-loss touché"
    assert rows[2]["review"]["cause"] == "signal"
    # 3 : refusé ; on suit quand même son issue (théorique) pour juger le refus
    assert rows[3]["decision"] == "refusé" and rows[3]["outcome"]["theoretical"] is True and rows[3]["outcome"]["r"] == -1.0
    assert rows[3]["review"]["cause"] == "hasard probable" and rows[3]["context"] == {"regime": "bear"}
    assert rows[4]["outcome"]["status"] == "en cours" and rows[4]["review"] is None
    assert rows[5]["review"]["cause"] == "hasard probable" and "ne prouve rien" in rows[5]["review"]["detail"]
    # ce que l'IA savait au moment de décider, et son plan
    assert set(rows[0]["known"]) >= {"rsi14", "atr_pct", "cost_r"} and rows[0]["plan"] == {"decision": "x", "scenarios": []}
    assert rows[0]["today"] is None  # aucun modèle aujourd'hui : rien à comparer
    # filtres
    assert {r["decision"] for r in app.decision_journal(decision="refusé")["rows"]} == {"refusé"}
    assert {r["symbol"] for r in app.decision_journal(symbol="BTC/USDC")["rows"]} == {"BTC/USDC"}


def test_journal_says_what_the_current_model_would_do(app):
    fill(app)
    m = MetaModel("4h", recipe="prudent").fit(ROWS)
    m.payoffs, m.threshold, m.version = payoff_table(ROWS), 0.0, "m-actuel"
    m.metrics = {"verdict": "validé"}
    m.meta = {"train_end": 1}
    app.models.register(type("R", (), {"final_model": m, "ok": True, "trial": False, "reasons": ["test"]})(), {})
    rows = app.decision_journal()["rows"]
    today = [r["today"] for r in rows]
    assert all(t is not None and t["model"] == "m-actuel" and 0 <= t["p_win"] <= 1 for t in today)
    p, er = m.predict(pd.DataFrame([{**features(2), **{f"s_{n}": float(n == "trend_breakout") for n in STRATEGIES}, "strategy": "trend_breakout"}]))
    r2 = next(r for r in rows if r["p_win"] == 0.72)
    assert r2["today"]["p_win"] == pytest.approx(p[0]) and r2["today"]["take"] == bool(er[0] >= 0.0)
    assert r2["today"]["changed"] == (not r2["today"]["take"]) and "m-actuel" in r2["today"]["text"]


def test_lessons_summarize_causes_and_keep_memory_prudent(app):
    fill(app)
    les = app.decision_journal()["lessons"]
    assert les["resolved"] == 5 and les["taken_resolved"] == 4
    causes = {c["cause"]: c for c in les["causes"]}
    assert causes["marché"]["gains"] == 1 and causes["frais"]["pertes"] == 1 and causes["signal"]["pertes"] == 1
    assert causes["hasard probable"]["pertes"] == 1
    mem = {(g["kind"], g["key"]): g for g in les["memory"]}
    eth = mem[("crypto", "ETH/USDC")]
    assert eth["n"] == 4 and eth["mean_r"] == pytest.approx((1.8 - 0.04 - 1.1 - 1.1) / 4)
    assert eth["prudent_mean_r"] == pytest.approx(eth["mean_r"] * 4 / (4 + SHRINK)) and eth["enough"] is False
    assert mem[("climat", "baissier")]["n"] == 1 and mem[("climat", "haussier")]["n"] == 4
    assert "ne servent" not in les["note"] and "prudent" in les["note"]


def test_journal_api(app):
    fill(app)
    with TestClient(create_api(app)) as c:
        assert c.post("/api/auth/setup", json={"password": "faux-mot-de-passe-de-test"}, headers=H).status_code == 200
        j = c.get("/api/ai/journal", params={"limit": 3}).json()
        assert len(j["rows"]) == 3 and j["lessons"]["resolved"] == 5
        json.dumps(j, allow_nan=False)
