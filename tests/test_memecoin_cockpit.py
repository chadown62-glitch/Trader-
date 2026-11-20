"""M58 — métriques du cockpit Memecoin : entonnoir des pools établis et décisions de la flotte (lecture seule, hors ligne)."""
import json

from fastapi.testclient import TestClient

from sentinelle.dmh import established_pools as est, memecoin_simulator as sim
from tests import test_configurable_capital as frozen
from tests import test_dmh_api
from tests.test_memecoin_engine import EstablishedFeed, NOW, established

api_setup = test_dmh_api.setup
URL = "/api/dmh/memecoin/funnel"


def test_funnel_route_is_authenticated_read_only_and_isolated(api_setup):
    app, c, _ = api_setup
    assert TestClient(c.app).get(URL).status_code == 401
    established(app.db, EstablishedFeed())  # décision datée de NOW, postérieure à la fenêtre de 24 h de la route
    state, _ = sim.load(app.db)
    state["fleet"] = [{"asset_domain": "memecoin", "ts": NOW, "mint": "M" * 32, "decision": "VETO", "why": ["veto"], "pivotal": [],
                       "findings": []}]
    app.db.set_meta(sim.KEY, json.dumps(state))
    before = app.db.scalar("SELECT COUNT(*) FROM decisions"), app.db.scalar("SELECT COUNT(*) FROM fills")
    data = c.get(URL).json()
    assert data["asset_domain"] == "memecoin" and data["accepted"] == 1 and data["tokens"] >= 1
    assert data["candidates"][0]["liquidity_usd"] == 50000 and data["thresholds"]["max_round_trip_loss"] == 0.10
    assert data["fleet"]["counts"] == {"VETO": 1} and data["fleet"]["recent"][0]["decision"] == "VETO"
    assert (app.db.scalar("SELECT COUNT(*) FROM decisions"), app.db.scalar("SELECT COUNT(*) FROM fills")) == before
    assert c.post(URL, headers={"X-Sentinelle": "1"}, json={}).status_code == 405


def test_summary_keeps_the_latest_decision_per_token_and_counts_refusals_by_stage(db):
    thin = EstablishedFeed()
    thin.pool["reserve_in_usd"] = "1600"
    established(db, thin)
    assert est.summary(db, 0)["refused_by_stage"]["liquidity"] == 1
    established(db, EstablishedFeed(), NOW + est.COOLDOWN_S)  # six heures plus tard, le pool s'est étoffé
    summary = est.summary(db, 0)
    assert summary["tokens"] == summary["accepted"] == 1 and summary["refused_by_stage"]["liquidity"] == 0
    assert est.summary(db, NOW + est.COOLDOWN_S + 1)["tokens"] == 0  # hors fenêtre


def test_scientific_manifests_remain_frozen():
    for base, head, expected in frozen.FROZEN:
        frozen.test_frozen_scientific_files(base, head, expected)
