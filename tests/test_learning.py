"""Capacités d'apprentissage : recettes du modèle, critère de validation, banc d'essai, fiches par crypto."""

import json
import time

import numpy as np
import pytest

from sentinelle.ai.bench import RULES, learning_rate, make_candidates, run_bench, run_case
from sentinelle.ai.coins import COINS, coin_info, detect_assets, glossary, resolve_asset
from sentinelle.ai.knowledge import brief, compute_profile, daily_frame
from sentinelle.ai.labeling import MODEL_COLUMNS
from sentinelle.ai.metrics import proven_positive, trade_metrics
from sentinelle.ai.model import DEFAULT_RECIPE, PRESETS, RECIPES, MetaModel
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App

from .helpers import HOUR, make_series


# -- modèle : recettes, oubli progressif, compatibilité ---------------------------------------
def test_recipes_and_default():
    assert DEFAULT_RECIPE == "mixte" and RECIPES["mixte"] == ["prudent", "lin"] and set(RECIPES["comite"]) == set(PRESETS)
    cand, _ = make_candidates(1500, "simple", seed=1)
    for recipe, n_members in (("mixte", 2), ("prudent", 1), ("lineaire", 1), ("comite", 3)):
        m = MetaModel("4h", recipe=recipe).fit(cand)
        assert len(m.members) == n_members and m.chosen == recipe
        p, er = m.predict(cand.tail(40))
        assert ((p > 0) & (p < 1)).all() and np.isfinite(er).all()


def test_auto_recipe_picks_a_level_on_unseen_data():
    cand, _ = make_candidates(3000, "combinaison", seed=2)
    m = MetaModel("4h", recipe="auto").fit(cand)
    assert m.chosen in RECIPES and set(m.selection) == set(RECIPES)
    assert max(m.selection.values()) > 0.6  # la règle cachée est bien visible sur la partie jamais vue
    small, _ = make_candidates(250, "simple", seed=3)
    assert MetaModel("4h", recipe="auto").fit(small).chosen == "mixte"  # trop peu de données pour comparer : recette de référence


def test_forgetting_gives_more_weight_to_recent_samples():
    cand, _ = make_candidates(400, "simple", seed=4)
    w = MetaModel("4h", half_life_days=365)._weights(cand)
    assert w[-1] == pytest.approx(1.0) and w[0] < 0.05 and (np.diff(w) >= 0).all()  # 6 ans d'écart : poids divisé par 64
    assert MetaModel("4h")._weights(cand) is None


def test_models_saved_by_a_previous_version_still_load(tmp_path):
    cand, _ = make_candidates(600, "simple", seed=5)
    m = MetaModel("4h", recipe="riche").fit(cand)
    state = {k: v for k, v in m.__dict__.items() if k not in ("recipe", "half_life_days", "members", "chosen", "selection")}
    old = MetaModel.__new__(MetaModel)
    old.__setstate__(state)  # fichier d'avant : un seul modèle, pas de recette
    assert old.recipe == "riche" and old.members == []
    assert np.allclose(old.predict(cand.tail(20))[0], m.predict(cand.tail(20))[0])
    path = tmp_path / "m.joblib"
    m.save(path)
    assert np.allclose(MetaModel.load(path).predict(cand.tail(20))[0], m.predict(cand.tail(20))[0])


# -- critère de validation -----------------------------------------------------------------------
def test_validation_requires_95_percent_confidence():
    rng = np.random.default_rng(0)
    weak = trade_metrics(rng.choice([2.0, -1.0], 270, p=[0.345, 0.655]))  # espérance vraie ≈ +0,03 R : indiscernable du hasard
    strong = trade_metrics(rng.choice([2.0, -1.0], 270, p=[0.45, 0.55]))  # espérance vraie +0,35 R
    assert not proven_positive(weak) and proven_positive(strong)
    assert not proven_positive(trade_metrics([])) and not proven_positive(trade_metrics([1.0]))


# -- banc d'essai ----------------------------------------------------------------------------------
def test_bench_data_is_reproducible_and_hides_a_real_rule():
    a, pa = make_candidates(2000, "simple", seed=7)
    b, pb = make_candidates(2000, "simple", seed=7)
    assert a.equals(b) and np.array_equal(pa, pb)
    assert set(MODEL_COLUMNS) <= set(a.columns)
    from sklearn.metrics import roc_auc_score

    y = (a["outcome_r"] > 0).astype(int)
    assert roc_auc_score(y, pa) > 0.7  # la règle existe vraiment
    assert abs(a["outcome_r"].mean()) < 0.25  # mais « tout prendre » ne rapporte presque rien
    n, pn = make_candidates(2000, "bruit", seed=7)
    assert np.allclose(pn, pn[0])  # bruit pur : même probabilité pour tous
    assert learning_rate(0.70, 0.75) == pytest.approx(0.8) and learning_rate(0.45, 0.75) == 0.0 and learning_rate(0.6, 0.5) is None


def test_ai_recovers_hidden_rules_and_invents_nothing():
    simple = run_case("simple", 2000, seed=11)
    assert simple["verdict"] == "validé" and simple["rate"] > 0.85
    assert simple["model_r"] > simple["base_r"] + 0.3  # le tri de l'IA rapporte nettement plus que « tout prendre »
    combo = run_case("combinaison", 3000, seed=12)
    assert combo["rate"] > 0.85
    noise = run_case("bruit", 2000, seed=13)
    assert noise["rate"] is None and not (noise["verdict"] == "validé" and noise["mode"] == "ia")
    assert abs(noise["auc"] - 0.5) < 0.06


def test_rule_change_is_not_validated():
    """La règle s'inverse : l'IA ne s'adapte pas instantanément, mais elle ne se valide pas à tort."""
    r = run_case("changement", 3000, seed=14)
    assert not (r["verdict"] == "validé" and r["mode"] == "ia")


def test_bench_report_structure():
    seen = []
    rep = run_bench(sizes=(600,), seeds=1, progress=lambda x, m: seen.append(x))
    assert [r["name"] for r in rep["rules"]] == list(RULES)
    assert 0.0 <= rep["score"] <= 1.0 and rep["noise_runs"] == 1 and seen[-1] == 1.0
    json.dumps(rep, allow_nan=False)  # lisible par le panel (aucune valeur indéfinie)


# -- connaissances générales ------------------------------------------------------------------------
def test_coin_knowledge_and_news_entity_recognition():
    allowed = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK"}
    assert allowed <= set(COINS)
    for c in COINS.values():
        assert c["what"] and c["drivers"] and c["risks"] and all(a == a.lower() for a in c["aliases"])
    assert detect_assets("Ripple wins appeal as SEC drops case", allowed) == ["XRP"]
    assert detect_assets("BTC and ETH slide; $SOL outperforms", allowed) == ["BTC", "ETH", "SOL"]
    assert detect_assets("New link between inflation and jobs", allowed) == []  # mot courant, pas Chainlink
    assert detect_assets("ADA compliance rules for websites", allowed) == []
    assert detect_assets("Chainlink partners with Swift, Cardano founder reacts", allowed) == ["ADA", "LINK"]
    assert [resolve_asset(x, allowed) for x in ("Ripple", "btc", "$SOL", "Ether", "Tether")] == ["XRP", "BTC", "SOL", "ETH", None]
    assert "XRP (XRP, Ripple)" in glossary(allowed)
    assert coin_info("zzz")["known"] is False and coin_info("btc")["name"] == "Bitcoin"


def test_news_analysis_maps_names_to_tickers():
    from sentinelle.news.service import validate_analysis

    a = validate_analysis({"assets": ["Ripple", "Bitcoin", "Tether"], "market_wide": False, "event_type": "régulation", "sentiment": 0.5,
                           "importance": 0.6, "confidence": 0.7, "summary_fr": "x"}, {"BTC", "XRP", "ETH"})
    assert a["assets"] == ["BTC", "XRP"]


# -- fiche calculée ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def series():
    btc = make_series(9000, seed=21, start=30_000, vol=0.008, tf_ms=4 * HOUR)
    alt = btc.copy()
    rng = np.random.default_rng(22)
    ratio = np.exp(np.cumsum(rng.standard_normal(len(btc)) * 0.004))
    growth = (btc["close"] / btc["close"].iloc[0]) ** 0.5  # suit le BTC en amplifiant ses mouvements d'une fois et demie
    for col in ("open", "high", "low", "close"):
        alt[col] = btc[col] * growth * ratio / 300
    return btc, alt


def test_profile_contents(series):
    btc, alt = series
    prof = compute_profile("ALT/USDC", alt, "4h", btc=btc)
    json.dumps(prof, allow_nan=False)
    assert prof["history"]["bars"] == 9000 and prof["history"]["years"] == pytest.approx(9000 / 6 / 365, abs=0.02)
    v, e = prof["volatility"], prof["extremes"]
    assert 0 < v["typical_day"] < 0.2 and v["annual"] > 0 and 0 <= v["rank_now"] <= 1
    assert e["worst_day"]["return"] < 0 < e["best_day"]["return"] and e["max_drawdown"]["depth"] < 0
    assert prof["btc"]["corr_all"] > 0.9 and prof["btc"]["beta_1y"] > 1.2  # liée au BTC, mouvements plus amples
    assert set(prof["regime"]["share"]) == {"bull", "neutral", "bear"} and prof["regime"]["current"] in ("bull", "neutral", "bear")
    assert set(prof["strategies"]) >= {"trend_follow", "trend_breakout"}
    assert prof["seasonality"]["notable"] == []  # marche aléatoire : aucune régularité « fiable » ne doit apparaître
    assert len(prof["reading"]) >= 5 and all(isinstance(x, str) and x.endswith(".") for x in prof["reading"])
    b = brief(prof)
    assert b["symbol"] == "ALT/USDC" and b["max_drawdown"] == e["max_drawdown"]["depth"]
    solo = compute_profile("BTC/USDC", btc, "4h")
    assert "btc" not in solo and solo["info"]["name"] == "Bitcoin"
    short = compute_profile("NEW/USDC", btc.iloc[:200], "4h")
    assert short.get("insufficient") is True


def test_daily_frame_drops_incomplete_days():
    df = make_series(6 * 10 + 3, seed=1, tf_ms=4 * HOUR, start_ts=1_600_000_000_000 - 1_600_000_000_000 % 86_400_000)
    d = daily_frame(df)
    assert len(d) == 10 and (d["bars"] == 6).all()
    assert d["close"].iloc[0] == df["close"].iloc[5]


# -- service : fiches, suivi en direct, banc d'essai, API ---------------------------------------
@pytest.fixture
def app(tmp_path):
    a = App(tmp_path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=5), start=False,
            settings_override={"general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}, "market": {"source": "synthetic"}})
    yield a
    a.db.close()


def test_knowledge_service(app, series):
    btc, alt = series
    assert app.knowledge_list()["items"][0]["missing"] is True
    assert app.knowledge_get("BTC/USDC")["missing"] is True and app.knowledge_get("ZZZ/USDC") is None
    n = app.update_knowledge({"BTC/USDC": btc, "ETH/USDC": alt}, None, "4h", app.settings)
    assert n == 2
    lst = app.knowledge_list()
    assert [i["symbol"] for i in lst["items"]] == ["BTC/USDC", "ETH/USDC"] and lst["updated"] is not None
    assert {i["volatility_rank"]["rank"] for i in lst["items"]} == {1, 2}
    eth = app.knowledge_get("ETH/USDC")
    assert eth["btc"]["corr_all"] > 0.9 and eth["enabled_strategies"] == ["trend_breakout", "trend_follow"] or set(eth["enabled_strategies"]) == {"trend_follow", "trend_breakout"}
    assert eth["book"]["spread_bps"] > 0  # carnet d'ordres relevé au moment du calcul
    app.update_knowledge({"BTC/USDC": btc, "ETH/USDC": alt}, None, "4h", app.settings)  # recalcul : mise à jour, pas de doublon
    assert app.db.scalar("SELECT COUNT(*) FROM knowledge", (), 0) == 2


def test_learning_monitor_compares_forecasts_with_outcomes(app):
    mon = app.learning_monitor()
    assert mon["seen"] == 0 and "forecast" not in mon and mon["model"] is None
    rng = np.random.default_rng(3)
    for k in range(60):  # l'IA annonce 70 % de gains, il y en a environ 30 %
        win = rng.random() < 0.3
        app.db.insert("candidates", {"symbol": "BTC/USDC", "timeframe": "4h", "ts": k, "strategy": "trend_follow", "source": "live", "p_win": 0.7,
                                     "decision": "pris", "outcome": "target" if win else "stop", "outcome_r": 2.0 if win else -1.0, "resolved_at": time.time()})
    app.db.insert("candidates", {"symbol": "BTC/USDC", "timeframe": "4h", "ts": 999, "strategy": "trend_follow", "source": "live", "p_win": 0.5, "decision": "refusé"})
    mon = app.learning_monitor()
    assert mon["seen"] == 61 and mon["resolved"] == 60 and mon["pending"] == 1
    f = mon["forecast"]
    assert f["announced"] == pytest.approx(0.7) and f["observed"] < 0.45 and f["drift"] is True and f["optimistic"] is True
    assert mon["retraining"]["new_samples"] == 60


def test_learning_api(app, monkeypatch, series):
    from fastapi.testclient import TestClient

    import sentinelle.services.learning as learning
    from sentinelle.api.server import create_api

    monkeypatch.setattr(learning, "BENCH_SIZES", (500,))
    monkeypatch.setattr(learning, "BENCH_SEEDS", 1)
    btc, alt = series
    app.update_knowledge({"BTC/USDC": btc, "ETH/USDC": alt}, None, "4h", app.settings)
    with TestClient(create_api(app)) as c:
        h = {"X-Sentinelle": "1"}
        assert c.get("/api/ai/bench").status_code == 401
        assert c.post("/api/auth/setup", json={"password": "un-mot-de-passe-solide"}, headers=h).status_code == 200
        assert c.get("/api/ai/bench").json() == {"report": None, "running": False}
        job = c.post("/api/ai/bench", headers=h).json()
        assert job["kind"] == "bench"
        assert c.post("/api/ai/bench", headers=h).status_code == 400  # un seul test à la fois
        deadline = time.time() + 120
        while time.time() < deadline and c.get(f"/api/jobs/{job['id']}").json()["status"] == "running":
            time.sleep(0.3)
        rep = c.get("/api/ai/bench").json()["report"]
        assert rep["recipe"] == "mixte" and len(rep["rules"]) == len(RULES)
        assert c.get("/api/ai/learning").json()["seen"] == 0
        items = c.get("/api/knowledge").json()["items"]
        assert items[0]["symbol"] == "BTC/USDC" and items[0]["name"] == "Bitcoin"
        d = c.get("/api/knowledge/detail", params={"symbol": "ETH/USDC"}).json()
        assert d["info"]["name"] == "Ethereum" and d["reading"]
        assert c.get("/api/knowledge/detail", params={"symbol": "NOPE/USDC"}).status_code == 404
        s = c.put("/api/settings", json={"ai": {"recipe": "auto", "half_life_days": 730}}, headers=h).json()
        assert s["ai"]["recipe"] == "auto" and s["ai"]["half_life_days"] == 730
        assert c.put("/api/settings", json={"ai": {"recipe": "magique"}}, headers=h).status_code == 400


# -- chaîne complète du panel : historique -> entraînement -> modèle actif -> fiches ---------------
class HistorySource:
    """Source factice : renvoie des historiques fabriqués (aucun réseau)."""

    name = "factice"

    def __init__(self, series: dict):
        self.series = series
        self._now = int(max(df["ts"].iloc[-1] for df in series.values())) + HOUR + 1

    def now_ms(self) -> int:
        return self._now

    def load_markets(self) -> dict:
        return {}

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        df = self.series.get(symbol)
        if df is None:
            return []
        rows = df[df["ts"] >= (since or 0)].head(limit)
        return rows[["ts", "open", "high", "low", "close", "volume"]].to_numpy().tolist()


def test_panel_training_chain_learns_activates_and_updates_sheets(tmp_path):
    def drift(ts):  # motif caché : hausse entre 0 h et 12 h
        return np.where((ts // HOUR) % 24 < 12, 0.003, -0.003)

    series = {s: make_series(17_000, seed=10 + i, start=p, drift_fn=drift)
              for i, (s, p) in enumerate((("BTC/USDC", 30_000), ("ETH/USDC", 2_000), ("SOL/USDC", 50)))}
    app = App(tmp_path, source=HistorySource(series), start=False, settings_override={
        "general": {"watchlist": list(series)}, "market": {"source": "synthetic"},
        "ai": {"timeframe": "1h", "strategies": {k: True for k in ("trend_breakout", "pullback", "mean_reversion", "momentum", "trend_follow")}}})
    try:
        steps = []
        out = app._train(lambda x, m: steps.append(m), app.settings, "1h", 720, False)
        assert out["ok"] and out["verdict"] == "validé", out["reasons"]
        rep = out["report"]
        assert rep["learner"]["recipe"] == "mixte" and rep["learner"]["chosen"] == "mixte"
        assert rep["holdout"]["auc"] > 0.6 and rep["backtest_model"]["metrics"]["n"] > 0
        champion = app.models.champion("1h")
        assert champion is not None and champion.version == out["version"] and len(champion.members) == 2
        assert any("Téléchargement" in m for m in steps) and any("Simulation" in m for m in steps)
        # entraînement sur une période courte : les fiches sont calculées à part, sur l'historique complet
        deadline = time.time() + 120
        while time.time() < deadline and app.jobs.running("knowledge"):
            time.sleep(0.2)
        sheets = app.knowledge_list()["items"]
        assert [s["symbol"] for s in sheets] == list(series) and not any(s.get("missing") for s in sheets)
        assert app.knowledge_get("BTC/USDC")["history"]["bars"] == 17_000
        assert app.refresh_knowledge_if_stale() is False  # fiches fraîches : rien à refaire
        mon = app.learning_monitor()
        assert mon["model"]["version"] == out["version"] and "mixte" in mon["model"]["level"]
    finally:
        app.db.close()


# -- modèle « à l'essai » : prometteur, pas prouvé, argent fictif uniquement -------------------------
def test_promising_but_unproven_model_goes_on_trial():
    from sentinelle.ai.trainer import train_walk_forward

    cand, _ = make_candidates(2500, "faible", seed=2)
    res = train_walk_forward(cand, "4h", with_importance=False)
    h = res.report["holdout"]
    assert not res.ok and res.trial and res.report["summary"]["trial"] is True
    assert h["model"]["n"] >= 30 and h["model"]["mean_r"] >= 0.05 and h["model"]["mean_r"] - h["baseline"]["mean_r"] >= 0.05
    assert any("à l'essai" in r for r in res.reasons)
    proven = train_walk_forward(make_candidates(2500, "faible", seed=1)[0], "4h", with_importance=False)
    assert proven.ok and not proven.trial  # un modèle validé n'est pas « à l'essai »
    noise = train_walk_forward(make_candidates(2000, "bruit", seed=0)[0], "4h", with_importance=False)
    assert not noise.ok and not noise.trial  # rien de prometteur dans le bruit


def test_model_registry_handles_trial_status(app):
    from types import SimpleNamespace

    def result(ok, trial, version):
        m = MetaModel("4h").fit(make_candidates(400, "simple", seed=1)[0])
        m.version = version
        m.metrics = {"verdict": "validé" if ok else "non validé", "trial": trial}
        return SimpleNamespace(final_model=m, ok=ok, trial=trial, reasons=["test"])

    store = app.models
    assert store.trial("4h") is None and store.champion("4h") is None
    assert store.register(result(False, True, "m-essai-1"), {})["status"] == "trial"
    assert store.trial("4h").version == "m-essai-1" and store.champion("4h") is None
    with pytest.raises(ValueError):
        store.promote("m-essai-1")  # un modèle non validé ne peut jamais devenir le modèle actif
    assert store.register(result(False, True, "m-essai-2"), {})["status"] == "trial"
    assert store.trial("4h").version == "m-essai-2" and store.get("m-essai-1")["status"] == "retired"
    assert store.register(result(False, False, "m-rejet"), {})["status"] == "rejected"
    assert store.trial("4h") is None  # le dernier entraînement fait foi : plus de modèle à l'essai
    store.register(result(False, True, "m-essai-3"), {})
    assert store.register(result(True, False, "m-valide"), {})["status"] == "champion"
    assert store.champion("4h").version == "m-valide" and store.trial("4h") is None
    statuses = [a for a in app.agents_status()]
    assert all(a["model"] == "m-valide" and a["model_trial"] is False for a in statuses)


class StubModel:
    """Modèle factice : annonce 60 % de chances de gain et +0,5 R pour tout candidat."""

    threshold = 0.1

    def __init__(self, verdict):
        self.version = "m-" + verdict
        self.metrics = {"verdict": verdict}

    def predict(self, cand):
        return np.full(len(cand), 0.6), np.full(len(cand), 0.5)


def test_trial_model_trades_in_paper_only(db, paper, pm, prices, clock):
    from sentinelle.agent.agent import TradingAgent
    from sentinelle.config import DEFAULT_SETTINGS, apply_update
    from sentinelle.risk.manager import RiskManager

    from .test_risk_agent_backtest import _feat_with_signal

    settings = apply_update(DEFAULT_SETTINGS, {})
    df, feat, idx = _feat_with_signal()
    sub = feat.iloc[: idx[-1] + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    settings["ai"]["regime_filter"] = False
    settings["ai"]["strategies"] = {k: k == "trend_breakout" for k in settings["ai"]["strategies"]}
    settings["risk"]["min_reward_to_fees"] = 1.0
    risk = RiskManager(db, paper.account_id, lambda: settings, clock)
    trial = StubModel("non validé")
    # en réel : un modèle non validé n'est jamais utilisé
    live = TradingAgent("ia-reel", "live", db, pm, risk, lambda: settings, lambda: trial, timeframe="1h", clock=clock)
    live.running = True
    live.on_bar("ETH/USDC", sub)
    assert pm.open_positions() == []
    assert "Aucun modèle validé" in db.one("SELECT decision_reason FROM candidates")["decision_reason"]
    db.execute("DELETE FROM candidates")
    # en entraînement : il est mis à l'essai, avec une taille réduite de moitié
    agent = TradingAgent("ia", "training", db, pm, risk, lambda: settings, lambda: trial, timeframe="1h", clock=clock)
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    pos = pm.open_positions("ETH/USDC", agent="ia")
    assert len(pos) == 1 and pos[0]["model_version"] == "m-non validé"
    assert json.loads(pos[0]["data"])["mode"] == "essai" if isinstance(pos[0]["data"], str) else pos[0]["data"]["mode"] == "essai"
    assert pos[0]["risk_amount"] / paper.equity()[0] == pytest.approx(0.005 * 0.5, rel=0.15)
    cand = db.one("SELECT * FROM candidates")
    assert cand["decision"] == "pris" and "IA à l'essai" in cand["decision_reason"] and cand["p_win"] == pytest.approx(0.6)


def test_validated_model_trades_full_size(db, paper, pm, prices, clock):
    from sentinelle.agent.agent import TradingAgent
    from sentinelle.config import DEFAULT_SETTINGS, apply_update
    from sentinelle.risk.manager import RiskManager

    from .test_risk_agent_backtest import _feat_with_signal

    settings = apply_update(DEFAULT_SETTINGS, {})
    df, feat, idx = _feat_with_signal()
    sub = feat.iloc[: idx[-1] + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    settings["ai"]["regime_filter"] = False
    settings["ai"]["strategies"] = {k: k == "trend_breakout" for k in settings["ai"]["strategies"]}
    settings["risk"]["min_reward_to_fees"] = 1.0
    risk = RiskManager(db, paper.account_id, lambda: settings, clock)
    agent = TradingAgent("ia", "training", db, pm, risk, lambda: settings, lambda: StubModel("validé"), timeframe="1h", clock=clock)
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    pos = pm.open_positions("ETH/USDC", agent="ia")
    assert len(pos) == 1 and pos[0]["risk_amount"] / paper.equity()[0] == pytest.approx(0.005, rel=0.15)
    assert "IA : 60 % de chances de gain" in db.one("SELECT decision_reason FROM candidates")["decision_reason"]
