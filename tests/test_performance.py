"""Tableau de rentabilité : chiffres vérifiés à la main sur un historique fabriqué."""

import json

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.common import UserError
from sentinelle.services.core import App
from sentinelle.services.performance import (DAY, MIN_TRADES, geometric_mean_return, grouped, max_drawdown, refusal_group,
                                             trade_stats)

H = {"X-Sentinelle": "1"}
BASE = 1_699_920_000  # 14 novembre 2023, minuit (temps universel)
HOUR = 3600


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


def at(day, hour):
    return BASE + day * DAY + hour * HOUR


# ---------------------------------------------------------------------------
# 1. Briques de calcul
# ---------------------------------------------------------------------------
def test_geometric_mean_and_drawdown():
    assert geometric_mean_return([]) is None
    assert geometric_mean_return([0.0, 0.0, 0.0]) == 0.0
    # +10 % puis −10 % : la moyenne simple vaut 0, mais on a perdu 1 % ; la moyenne géométrique le dit
    g = geometric_mean_return([0.10, -0.10])
    assert g == pytest.approx(0.99 ** 0.5 - 1) and g < 0
    assert (1 + geometric_mean_return([0.02, 0.0, -0.01, 0.0])) ** 4 == pytest.approx(1.02 * 0.99)
    assert geometric_mean_return([0.5, -1.0]) == -1.0  # capital perdu
    assert max_drawdown([30, 31, 29.45, 30.5, 28.0]) == pytest.approx(1 - 28.0 / 31)
    assert max_drawdown([30, 30.5, 31]) == 0.0 and max_drawdown([]) == 0.0


def test_refusal_reasons_are_grouped():
    assert refusal_group("Gestionnaire de risque : Ordre trop petit pour la plateforme (minimum 5 USDC, stop compris).") == "Ordre trop petit ou impossible avec ce capital"
    assert refusal_group("Gestionnaire de risque : Ordre impossible avec ce capital : la plateforme exige au moins 5 USDC") == "Ordre trop petit ou impossible avec ce capital"
    assert refusal_group("Espérance insuffisante selon l'IA (+0.02 R, seuil +0.10 R)") == "Avantage jugé insuffisant par l'IA"
    assert refusal_group("Climat baissier sur le marché (BTC sous ses moyennes de fond) : l'IA reste à l'abri") == "Climat de marché baissier"
    assert refusal_group("Gestionnaire de risque : Limite de perte du jour atteinte (-2.1 %). Reprise demain.") == "Limite de perte ou arrêt de sécurité"
    assert refusal_group("Bouclier temps réel : mouvement brutal") == "Bouclier temps réel"
    assert refusal_group("Taux de change indisponible : aucun ordre par prudence") == "Données ou taux de change indisponibles"
    assert refusal_group("Aucun modèle validé : l'IA observe sans trader") == "Aucun modèle validé : l'IA observe"
    assert refusal_group("raison jamais vue") == "Autre motif" and refusal_group(None) == "Autre motif"


def test_trade_stats_on_known_trades():
    def tr(pnl, r, cost, eq, hours, **kw):
        return {"pnl": pnl, "pnl_r": r, "fees": 0.01, "ret_position": pnl / cost, "contribution": pnl / eq, "duration_s": hours * 3600, **kw}

    trades = [tr(0.30, 1.5, 9.0, 30.0, 6, strategy="a"), tr(-0.45, -1.0, 10.0, 30.3, 49, strategy="b"), tr(0.10, None, 5.0, 29.85, 1, strategy="a")]
    s = trade_stats(trades)
    assert (s["n"], s["wins"], s["losses"]) == (3, 2, 1) and s["win_rate"] == pytest.approx(2 / 3)
    assert s["pnl"] == pytest.approx(-0.05) and s["expectancy"] == pytest.approx(-0.05 / 3)
    assert s["avg_win"] == pytest.approx(0.20) and s["avg_loss"] == pytest.approx(-0.45)
    assert s["profit_factor"] == pytest.approx(0.40 / 0.45) and s["fees"] == pytest.approx(0.03)
    assert s["mean_r"] == pytest.approx(0.25) and s["avg_duration_h"] == pytest.approx(56 / 3)
    assert s["mean_ret_position"] == pytest.approx((0.30 / 9 - 0.045 + 0.02) / 3) and s["median_ret_position"] == pytest.approx(0.02)
    assert s["mean_contribution"] == pytest.approx((0.30 / 30 - 0.45 / 30.3 + 0.10 / 29.85) / 3)
    assert s["enough"] is False and MIN_TRADES == 30
    empty = trade_stats([])
    assert empty["n"] == 0 and empty["win_rate"] is None and empty["expectancy"] is None and empty["profit_factor"] is None
    g = {x["key"]: x for x in grouped(trades, "strategy", {"a": "Stratégie A"})}
    assert g["a"]["n"] == 2 and g["a"]["label"] == "Stratégie A" and g["a"]["pnl"] == pytest.approx(0.40) and g["b"]["win_rate"] == 0.0
    assert trade_stats([tr(0.1, 0.5, 5, 30, 1)])["profit_factor"] is None  # aucune perte : pas de ratio inventé


# ---------------------------------------------------------------------------
# 2. Rapport complet sur un historique fabriqué
# ---------------------------------------------------------------------------
@pytest.fixture
def story(tmp_path):
    """Cinq jours de vie du portefeuille de 30 € :
    jour 0 : un trade IA gagnant (+0,30 €) ; jour 1 : rien ; jour 2 : Sentinelle éteint ;
    jour 3 : un trade IA perdant (−0,45 €) et un trade manuel gagnant (+0,10 €) ; jour 4 : une position ouverte."""
    clock = Clock(at(0, 10))
    app = App(tmp_path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=5, clock=clock), start=False,
              settings_override={"market": {"source": "synthetic"}, "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}})
    app._startup()
    main = app.main_account()["id"]
    for ts, eq in ((at(0, 12), 30.0), (at(0, 20), 30.30), (at(1, 6), 30.30), (at(1, 18), 30.30),
                   (at(3, 9.5), 29.85), (at(3, 12), 29.95)):
        app.db.execute("INSERT INTO equity(account_id, ts, equity, cash) VALUES (?, ?, ?, ?)", (main, ts, eq, eq))

    def trade(symbol, origin, strategy, entry, exit_, cost, pnl, r, fees, model=None, data=None):
        qty = cost / (50_000.0 * 0.9)
        app.db.insert("positions", {
            "account_id": main, "symbol": symbol, "origin": origin, "agent": "ia_entrainement" if origin == "ai" else None,
            "status": "closed", "qty": 0.0, "initial_qty": qty, "entry_price": 50_000.0, "entry_fx": 0.9, "currency": "EUR",
            "entry_ts": entry, "exit_ts": exit_, "exit_price": 50_500.0, "realized_pnl": pnl, "pnl_r": r, "fees": fees,
            "strategy": strategy, "model_version": model, "timeframe": "4h" if origin == "ai" else None,
            "reason_close": "Objectif atteint" if pnl > 0 else "Stop-loss touché", "data": json.dumps(data or {})})

    trade("BTC/USDC", "ai", "trend_breakout", at(0, 13), at(0, 19), 9.0, 0.30, 1.5, 0.04, "m1", {"mode": "model", "regime": "bull"})
    trade("ETH/USDC", "ai", "trend_follow", at(1, 8), at(3, 9), 10.0, -0.45, -1.0, 0.05, None, {"mode": "exploration", "regime": "range"})
    trade("BTC/USDC", "manual", None, at(3, 10), at(3, 11), 5.0, 0.10, None, 0.02)
    # un trade d'avant le lancement ne doit jamais compter
    trade("BTC/USDC", "ai", "trend_breakout", at(-3, 1), at(-2, 1), 9.0, 5.0, 9.0, 0.04)

    def cand(ts, strategy, decision, reason, outcome_r):
        app.db.execute(
            "INSERT INTO candidates(symbol, timeframe, ts, strategy, source, decision, decision_reason, account_id, outcome, outcome_r) "
            "VALUES ('BTC/USDC', '4h', ?, ?, 'live', ?, ?, ?, ?, ?)",
            (int(ts * 1000), strategy, decision, reason, main, None if outcome_r is None else ("target" if outcome_r > 0 else "stop"), outcome_r))

    cand(at(0, 12), "trend_breakout", "pris", "Achat BTC", 1.5)
    cand(at(1, 4), "trend_follow", "pris", "Achat ETH", -1.0)
    cand(at(1, 8), "trend_breakout", "refusé", "Gestionnaire de risque : Ordre trop petit pour la plateforme (minimum 5 USDC, stop compris).", -1.0)
    cand(at(2, 4), "trend_breakout", "refusé", "Gestionnaire de risque : Ordre impossible avec ce capital : la plateforme exige au moins 5 USDC", 0.4)
    cand(at(3, 4), "trend_follow", "refusé", "Espérance insuffisante selon l'IA (+0.02 R, seuil +0.10 R)", None)
    cand(at(3, 8), "trend_breakout", "refusé", "Climat baissier sur le marché (BTC sous ses moyennes de fond) : l'IA reste à l'abri", -1.0)
    cand(at(-3, 0), "trend_breakout", "refusé", "Climat baissier sur le marché", -1.0)  # avant le lancement : ignoré

    rt = app.runtimes[main]
    rt.broker.set_balance("EUR", 29.95)  # la caisse reflète les trois trades clôturés
    clock.t = at(4, 10)
    app.hub.poll()
    opened = app.place_manual_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 10.0})
    yield app, main, clock, opened["position"]
    app.db.close()


def test_report_capital_results_and_accounting_check(story):
    app, main, clock, pos = story
    rep = app.performance()
    assert rep["account"] == {"id": main, "name": "Portefeuille principal", "role": "main", "currency": "EUR", "tradable": True}
    assert rep["launch_ts"] == at(0, 10) and rep["now"] == at(4, 10) and rep["period"] == "all"
    cap, res = rep["capital"], rep["result"]
    live = app.runtimes[main].broker.breakdown()
    assert cap["initial"] == 30.0 and cap["current"] == pytest.approx(live["equity"]) and 29.90 < cap["current"] < 29.95
    assert cap["invested"] == pytest.approx(live["invested"]) and cap["available"] == pytest.approx(live["available"]) and cap["reserved"] == 0
    assert cap["cash"] + cap["invested"] == pytest.approx(cap["current"])
    # réalisé (trades clôturés) et latent (position ouverte) sont séparés, et rapprochés de la variation du capital
    assert res["realized"] == pytest.approx(-0.05) and res["fees"] == pytest.approx(0.11)
    assert res["latent"] < 0 and res["exit_costs_estimate"] > 0
    assert res["net"] == pytest.approx(cap["current"] - 30.0) and res["net_pct"] == pytest.approx(cap["current"] / 30.0 - 1)
    assert res["net"] == pytest.approx(res["realized"] + res["latent"] + res["exit_costs_estimate"], abs=1e-9)
    assert abs(res["check_gap"]) < 1e-9
    assert len(rep["open_positions"]) == 1 and rep["open_positions"][0]["id"] == pos["id"] and rep["open_positions"][0]["pnl"] == pytest.approx(res["latent"])
    assert any("simulé" in c for c in rep["caution"]) and any("3 trade(s)" in c and "trop peu" in c for c in rep["caution"])
    assert any("1 jour(s) sans relevé" in c for c in rep["caution"]) and rep["simulated_market"] is True and rep["min_trades"] == 30


def test_daily_returns_include_days_without_trades(story):
    app, main, clock, pos = story
    rep = app.performance()
    current = rep["capital"]["current"]
    daily = rep["detail"]["daily"]
    assert [d["day"] for d in daily] == ["2023-11-14", "2023-11-15", "2023-11-16", "2023-11-17", "2023-11-18"]
    assert [d["trades"] for d in daily] == [1, 0, 0, 2, 0]  # les jours sans trade restent dans la série
    assert [d["observed"] for d in daily] == [True, True, False, True, True]  # jour 2 : Sentinelle éteint, capital inchangé
    assert [d["equity"] for d in daily[:4]] == [30.30, 30.30, 30.30, 29.95] and daily[4]["equity"] == pytest.approx(current)
    expected = [30.30 / 30 - 1, 0.0, 0.0, 29.95 / 30.30 - 1, current / 29.95 - 1]
    assert [d["ret"] for d in daily] == pytest.approx(expected)
    assert [d["realized"] for d in daily] == pytest.approx([0.30, 0.0, 0.0, -0.35, 0.0])
    s = rep["detail"]["summary"]
    assert (s["days"], s["days_with_trades"], s["days_unobserved"], s["positive_days"], s["negative_days"]) == (5, 2, 1, 1, 2)
    assert s["mean_daily_return"] == pytest.approx(sum(expected) / 5)
    # le rendement géométrique redonne exactement la variation du capital sur les cinq jours
    assert (1 + s["geo_daily_return"]) ** 5 == pytest.approx(current / 30.0)
    assert s["best_day"] == pytest.approx(0.01) and s["worst_day"] == pytest.approx(29.95 / 30.30 - 1)
    assert s["max_drawdown"] == pytest.approx(1 - 29.85 / 30.30)
    assert s["max_exposure"] == pytest.approx(rep["capital"]["invested"] / current) and 0.30 < s["max_exposure"] < 0.36
    assert rep["detail"]["equity"][0] == [at(0, 10) * 1000, 30.0] and rep["detail"]["equity"][-1] == [at(4, 10) * 1000, pytest.approx(current)]
    assert min(v for _, v in rep["detail"]["drawdown"]) == pytest.approx(-(1 - 29.85 / 30.30) * 100)


def test_trade_metrics_and_breakdowns(story):
    app, main, clock, pos = story
    d = app.performance()["detail"]
    t = d["summary"]["trades"]
    assert (t["n"], t["wins"], t["losses"]) == (3, 2, 1) and t["win_rate"] == pytest.approx(2 / 3)  # le trade d'avant le lancement est exclu
    assert t["pnl"] == pytest.approx(-0.05) and t["expectancy"] == pytest.approx(-0.05 / 3) and t["profit_factor"] == pytest.approx(0.40 / 0.45)
    assert t["avg_win"] == pytest.approx(0.20) and t["avg_loss"] == pytest.approx(-0.45) and t["fees"] == pytest.approx(0.11)
    assert t["avg_duration_h"] == pytest.approx((6 + 49 + 1) / 3) and t["mean_r"] == pytest.approx(0.25) and t["enough"] is False
    # rendement net de chaque trade sur le montant engagé, et contribution au portefeuille (capital au moment de l'entrée)
    by_id = {x["strategy"] or "manuel": x for x in d["trades"]}
    assert by_id["trend_breakout"]["ret_position"] == pytest.approx(0.30 / 9.0) and by_id["trend_breakout"]["contribution"] == pytest.approx(0.30 / 30.0)
    assert by_id["trend_follow"]["ret_position"] == pytest.approx(-0.45 / 10.0) and by_id["trend_follow"]["contribution"] == pytest.approx(-0.45 / 30.30)
    assert by_id["manuel"]["ret_position"] == pytest.approx(0.10 / 5.0) and by_id["manuel"]["contribution"] == pytest.approx(0.10 / 29.85)
    assert t["mean_ret_position"] == pytest.approx((0.30 / 9 - 0.045 + 0.02) / 3)
    assert t["mean_contribution"] == pytest.approx((0.30 / 30 - 0.45 / 30.30 + 0.10 / 29.85) / 3)

    def keyed(name):
        return {g["key"]: g for g in d[name]}

    strat = keyed("by_strategy")
    assert strat["trend_breakout"]["label"] == "Cassure de tendance" and strat["trend_breakout"]["pnl"] == pytest.approx(0.30)
    assert strat["trend_follow"]["pnl"] == pytest.approx(-0.45) and strat[None]["label"] == "trade manuel"
    assert {k: g["n"] for k, g in keyed("by_symbol").items()} == {"BTC/USDC": 2, "ETH/USDC": 1}
    origin = keyed("by_origin")
    assert origin["ai"]["n"] == 2 and origin["ai"]["pnl"] == pytest.approx(-0.15) and origin["manual"]["label"] == "Vous (manuel)"
    regime = keyed("by_regime")
    assert regime["bull"]["label"] == "haussier" and regime["range"]["label"] == "neutre" and regime[None]["label"] == "non précisé"
    assert {g["label"]: g["n"] for g in d["by_horizon"]} == {"moins d'un jour": 2, "1 à 3 jours": 1}
    assert {g["key"]: g["n"] for g in d["by_timeframe"]} == {"4h": 2, None: 1}
    # comparaison des versions successives du modèle : seulement les trades de l'IA, dans l'ordre d'apparition
    models = d["by_model"]
    assert [m["label"] for m in models] == ["m1", "sans modèle (exploration)"] and [m["n"] for m in models] == [1, 1]
    assert models[0]["modes"] == ["model"] and models[1]["modes"] == ["exploration"] and models[0]["mean_r"] == pytest.approx(1.5)


def test_refusals_and_their_reasons(story):
    app, main, clock, pos = story
    r = app.performance()["detail"]["refusals"]
    assert (r["seen"], r["taken"], r["refused"]) == (6, 2, 4)  # la situation d'avant le lancement est ignorée
    reasons = {g["label"]: g for g in r["reasons"]}
    small = reasons["Ordre trop petit ou impossible avec ce capital"]
    assert small["n"] == 2 and small["share"] == 0.5 and small["mean_r"] == pytest.approx(-0.3) and small["n_resolved"] == 2
    assert reasons["Avantage jugé insuffisant par l'IA"]["n"] == 1 and reasons["Avantage jugé insuffisant par l'IA"]["mean_r"] is None
    assert reasons["Climat de marché baissier"]["mean_r"] == pytest.approx(-1.0)
    assert r["reasons"][0]["label"] == "Ordre trop petit ou impossible avec ce capital"  # la raison la plus fréquente d'abord
    # ce que les situations refusées auraient donné, à comparer avec celles qui ont été prises
    assert r["refused_outcome"]["n_resolved"] == 3 and r["refused_outcome"]["mean_r"] == pytest.approx((-1.0 + 0.4 - 1.0) / 3)
    assert r["taken_outcome"]["n_resolved"] == 2 and r["taken_outcome"]["mean_r"] == pytest.approx(0.25)


def test_periods_today_7_30_90_days_and_since_launch(story):
    app, main, clock, pos = story
    rep = app.performance(period="today")
    current = rep["capital"]["current"]
    p = rep["periods"]
    assert list(p) == ["today", "7d", "30d", "90d", "all"] and [p[k]["label"] for k in p][0] == "Aujourd'hui"
    today = p["today"]
    assert today["start"] == at(4, 0) and today["capital_start"] == 29.95 and today["net"] == pytest.approx(current - 29.95)
    assert today["trades"]["n"] == 0 and today["days"] == 1 and today["realized"] == 0.0
    assert today["net_pct"] == pytest.approx(current / 29.95 - 1) and today["max_drawdown"] == pytest.approx(1 - current / 29.95)
    # le lancement date de quatre jours : 7, 30 et 90 jours couvrent toute l'histoire
    for key in ("7d", "30d", "90d", "all"):
        assert p[key]["start"] == at(0, 10) and p[key]["capital_start"] == 30.0 and p[key]["trades"]["n"] == 3 and p[key]["days"] == 5
    assert rep["period"] == "today" and rep["detail"]["summary"]["key"] == "today" and len(rep["detail"]["daily"]) == 1
    assert rep["detail"]["trades"] == [] and rep["detail"]["refusals"]["seen"] == 0
    # deux jours plus tard (le moteur a relevé le capital le jour 4, puis Sentinelle est resté éteint le jour 5)
    app._snapshot_equity()
    clock.t = at(6, 3)
    later = app.performance(period="7d")
    assert later["periods"]["today"]["start"] == at(6, 0) and later["periods"]["7d"]["days"] == 7
    assert [d["observed"] for d in later["detail"]["daily"]] == [True, True, False, True, True, False, True]
    # un relevé pris à minuit pile appartient au jour qui commence : le capital de départ du jour est celui d'avant
    app.db.execute("INSERT INTO equity(account_id, ts, equity, cash) VALUES (?, ?, 31.0, 31.0)", (main, at(6, 0)))
    edge = app.performance(period="today")
    assert edge["periods"]["today"]["capital_start"] == pytest.approx(later["capital"]["current"], abs=0.05) != 31.0
    assert edge["periods"]["today"]["net"] == pytest.approx(edge["detail"]["daily"][-1]["pnl"])
    with pytest.raises(UserError):
        app.performance(period="1an")
    with pytest.raises(UserError):
        app.performance(account_id=9999)


def test_reset_starts_a_new_measurement(story):
    app, main, clock, pos = story
    app.close_position(pos["id"])
    clock.t = at(5, 9)
    app.reset_paper_account(main, None)
    rep = app.performance()
    assert rep["launch_ts"] == at(5, 9) and rep["capital"]["current"] == 30.0 and rep["result"]["net"] == 0.0
    assert rep["detail"]["summary"]["trades"]["n"] == 0 and rep["result"]["realized"] == 0.0 and rep["detail"]["summary"]["days"] == 1
    assert abs(rep["result"]["check_gap"]) < 1e-9


def test_performance_api(story):
    app, main, clock, pos = story
    with TestClient(create_api(app)) as c:
        assert c.post("/api/auth/setup", json={"password": "faux-mot-de-passe-de-test"}, headers=H).status_code == 200
        rep = c.get("/api/performance").json()
        assert rep["account"]["id"] == main and rep["detail"]["summary"]["trades"]["n"] == 3 and rep["capital"]["initial"] == 30.0
        assert c.get("/api/performance", params={"period": "today", "account_id": main}).json()["period"] == "today"
        assert c.get("/api/performance", params={"period": "siecle"}).status_code == 400
        json.dumps(rep, allow_nan=False)  # aucun nombre non représentable dans la réponse
