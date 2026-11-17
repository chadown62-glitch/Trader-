"""Mission 52 — durcissement du hub : plafonds de bout en bout, petits ordres, reliquats, coûts incertains, seuils."""
import logging

import pytest

from sentinelle.dmh.cex import Rules
from sentinelle.dmh.router import Intent, build_routes, choose, economic_minimum
from sentinelle.dmh.security import SecurityRefusal
from sentinelle.dmh.service import HubError
from sentinelle.storage.db import Database

from tests import dmh_support as S

logging.disable(logging.CRITICAL)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "h.db")
    yield d
    d.close()


def execute(svc, pv, rid):
    return svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": rid, "confirm": True})


def vetoes(pv):
    return {v["code"] for v in pv["verdict"]["vetoes"]}


# -- 1. plafonds de bout en bout -------------------------------------------------------------------------------------------
def test_an_order_above_the_position_limit_is_refused_end_to_end(db):
    svc = S.service(db, S.market())
    pv = svc.preview(S.buy(n=12.0))
    assert pv["decision"] == "ROUTE" and "position" in vetoes(pv)  # limite par défaut 10 €
    op = execute(svc, pv, "cap-000001")
    assert op["state"] == "REJECTED" and svc.ledger.cash.total() == pytest.approx(30.0) and svc.ledger.holdings() == []


def test_an_order_above_the_absolute_cap_cannot_even_be_previewed_and_caps_bind_user_settings(db):
    svc = S.service(db, S.market())
    with pytest.raises(HubError):
        svc.preview(S.buy(n=16.0))  # au-delà du plafond absolu de 15 €
    with pytest.raises(SecurityRefusal):
        svc.set_limits({"max_position_eur": 16.0}, "user")
    svc.set_limits({"max_position_eur": 15.0}, "user")
    pv = svc.preview(S.buy(n=15.0))
    assert pv["verdict"]["allowed"], pv["explanation"]
    assert execute(svc, pv, "cap-000002")["state"] == "FILLED"


def test_small_orders_are_refused_by_default_under_10_eur(db):
    svc = S.service(db, S.market())
    pv = svc.preview(S.buy(n=9.0))
    assert "minimum" in vetoes(pv) and execute(svc, pv, "small-0001")["state"] == "REJECTED"
    with pytest.raises(SecurityRefusal):
        svc.set_limits({"min_order_eur": 0.5}, "user")  # plancher absolu : 1 €
    with pytest.raises(SecurityRefusal):
        svc.set_limits({"min_order_eur": 5.0}, "llm")  # jamais à la demande de l'IA
    svc.set_limits({"min_order_eur": 5.0}, "user")  # décision explicite de l'utilisateur
    assert "minimum" not in vetoes(svc.preview(S.buy(n=9.0)))


def test_cumulative_exposure_reserve_and_concurrent_asset_limits(db):
    v = S.market()
    v.set_book("binance", "SOL/EUR", 70.0, min_notional=5.0)
    svc = S.service(db, v)
    assert execute(svc, svc.preview(S.buy("BTC")), "cum-000001")["state"] == "FILLED"
    pv = svc.preview(S.buy("BTC"))
    assert "position_cumulee" in vetoes(pv)
    assert execute(svc, svc.preview(S.buy("ETH")), "cum-000002")["state"] == "FILLED"
    pv = svc.preview(S.buy("SOL"))
    assert {"reserve", "exposition"} <= vetoes(pv)  # 10 € restants : réserve de 6 € et exposition de 80 % dépassées
    assert execute(svc, pv, "cum-000003")["state"] == "REJECTED"
    assert svc.ledger.reconcile()["ok"]


# -- 2. reliquats et capital immobilisé --------------------------------------------------------------------------------------
def test_routes_with_excessive_immobilised_capital_are_refused(db):
    v = S.FakeView()
    v.set_book("binance", "BTC/EUR", 60_000.0, step=1e-5, min_notional=5.0)  # un pas ≈ 0,60 € : reliquat ≈ 6 % de 10 €
    svc = S.service(db, v)
    pv = svc.preview(S.buy())
    assert pv["decision"] == "ABSTENTION"
    reasons = " ".join(r for a in pv["alternatives"] for r in a["reasons"])
    assert "capital immobilisé" in reasons
    v.set_book("kraken", "BTC/EUR", 60_000.0, taker=40.0, maker=25.0, min_notional=0.45)  # 8 décimales : aucun reliquat
    pv = svc.preview(S.buy())
    assert pv["decision"] == "ROUTE" and pv["route"]["label"].startswith("kraken"), pv["explanation"]
    m = pv["money"]
    assert m["immobilized_eur"] < 0.01 and m["fees_paid_eur"] > 0  # pas de 1e-8 : reliquat négligeable
    assert m["cash_available_after_eur"] == pytest.approx(m["cash_available_now_eur"] - 10.0, abs=0.02)


def test_dust_is_shown_separately_from_fees_and_available_cash(db):
    v = S.FakeView()
    v.set_book("binance", "BTC/EUR", 60_000.0, step=2e-6, min_notional=5.0)  # reliquat ≈ 0,12 € (1,2 % de 10 €)
    svc = S.service(db, v)
    pv = svc.preview(S.buy())
    assert "capital immobilisé" in " ".join(r for a in pv["alternatives"] for r in a["reasons"])
    svc.set_limits({"max_immobilized_pct": 2.0}, "user")
    pv = svc.preview(S.buy())
    assert pv["verdict"]["allowed"] and 1.0 < pv["money"]["immobilized_pct"] < 2.0
    assert execute(svc, pv, "dust-00001")["state"] == "FILLED"
    pos = svc.positions()
    p = pos["positions"][0]
    assert p["dust_qty"] > 0 and p["dust_eur"] > 0 and abs(p["sellable_qty"] / 2e-6 - round(p["sellable_qty"] / 2e-6)) < 1e-6
    assert pos["fees_paid_eur"] > 0 and pos["dust_value_eur"] == pytest.approx(p["dust_eur"])
    assert pos["cash_available"] == pytest.approx(30.0 - 10.0, abs=0.05)  # l'arrondi au pas laisse quelques centimes non dépensés


# -- 3. coûts incertains ------------------------------------------------------------------------------------------------------
def _two_venues(narrow_fee=10.0, wide=(0.0, 60.0), wide_central=8.0):
    v = S.FakeView()
    v.set_book("binance", "BTC/EUR", 60_000.0, taker=narrow_fee, min_notional=5.0)
    v.set_book("kraken", "BTC/EUR", 60_000.0, taker=wide_central, taker_range=wide, min_notional=0.45)
    return v


def test_the_cheapest_route_at_central_prices_is_not_chosen_when_it_is_fragile():
    v = _two_venues()
    routes = build_routes(v, S.registry(), Intent("BTC", 10.0, 200.0, (), False))
    central = min((r for r in routes if not r.reasons), key=lambda r: r.totals()["cost_eur"])
    assert central.label.startswith("kraken")  # la moins chère au prix central (commission hypothétique 0,08 %)
    c = choose(routes, 200.0)
    assert c.decision == "ROUTE" and c.route.label.startswith("binance")  # plus sûre dans le scénario défavorable
    assert c.fee_dependent and any("NON VÉRIFIÉES" in x for x in c.sensitivity)
    assert c.scenario_winners["commissions_basses"].startswith("kraken") and c.scenario_winners["commissions_hautes"].startswith("binance")


def test_abstention_when_profit_exists_only_at_central_costs():
    v = S.FakeView()
    v.set_book("kraken", "BTC/EUR", 60_000.0, taker=8.0, taker_range=(0.0, 60.0), min_notional=0.45)
    routes = build_routes(v, S.registry(), Intent("BTC", 10.0, 40.0, (), False))
    c = choose(routes, 40.0)
    assert c.decision == "ABSTENTION" and "défavorable" in " ".join(c.reasons)
    assert choose(build_routes(v, S.registry(), Intent("BTC", 10.0, 200.0, (), False)), 200.0).decision == "ROUTE"


def test_scenarios_bracket_the_central_cost_and_flag_unverified_fees():
    v = _two_venues()
    for r in build_routes(v, S.registry(), Intent("BTC", 10.0, 100.0, (), False)):
        if r.reasons:
            continue
        sc = r.totals()["scenarios_eur"]
        assert sc["commissions_basses"] <= sc["central"] <= sc["commissions_hautes"] <= sc["defavorable"] + 1e-12
        assert r.totals()["unverified_fees_eur"] > 0  # commissions de test : hypothèses


# -- 4. seuil économique par plateforme et par actif ----------------------------------------------------------------------------
def test_economic_minimum_per_venue_and_asset():
    coarse = economic_minimum(Rules("binance", "BTC/EUR", 1e-5, 0.01, 1e-5, 5.0, True), 60_000.0, 0.001)
    assert coarse["step_value_eur"] == pytest.approx(0.6) and coarse["economic_min_eur"] == pytest.approx(60.0)
    assert coarse["binding"] == "reliquat"  # BTC sur Binance : jamais économique sous 60 € avec la règle de 1 %
    fine = economic_minimum(Rules("kraken", "BTC/EUR", 1e-8, 0.1, 6e-5, 0.45, True), 60_000.0, 0.004)
    assert fine["economic_min_eur"] == 10.0 and fine["binding"] == "plancher prudent"
    big_min = economic_minimum(Rules("bitstamp", "BTC/EUR", 1e-8, 0.01, 0.0, 10.0, True), 60_000.0, 0.004)
    assert big_min["economic_min_eur"] > 10.0 and big_min["binding"] == "minimum de la plateforme"
