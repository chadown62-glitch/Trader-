"""Couverture des dates du rejeu en EUR, données de test isolées."""
from copy import deepcopy

import pytest

from sentinelle.backtest.engine import run_backtest
from sentinelle.config import DEFAULT_SETTINGS
from sentinelle.market.fx import FxQuote, FxService, FxUnavailable
from sentinelle.market.fx_book import FXBook, FXQuote
from tests.helpers import make_series

START = 1787860800
BAR = 4 * 3600


@pytest.mark.parametrize("coverage", ["none", "middle_gap", "all"])
def test_coverage_is_checked_before_features_or_simulated_orders(db, monkeypatch, coverage):
    config = deepcopy(DEFAULT_SETTINGS)
    frame = make_series(90 * 6, seed=42)
    frame["ts"] = [(START + k * BAR) * 1000 for k in range(len(frame))]
    service = FxService("EUR", [lambda *_: pytest.fail("Live FX consulted")], db=db)
    if coverage != "none":
        for k in range(len(frame) + 1):
            if coverage == "middle_gap" and k == 45:
                continue
            ts = START + k * BAR
            book = FXBook([FXQuote("EUR", "USDC", 1.05, 1.06, ts, "fixture", 10000, 10000)])
            service._remember(FxQuote.from_book("EUR", "USDC", book, 60))
    args = dict(initial=30, account_currency="EUR", historical_fx=service.historical, reference_policy="cash")
    if coverage == "all":
        result = run_backtest({"BTC/USDC": frame}, "4h", config, None, **args)
        assert result["fx_coverage"]["checked"] == 541 and result["final_equity"] == 30
    else:
        monkeypatch.setattr("sentinelle.backtest.engine.compute_features", lambda *a, **kw: pytest.fail("Replay started"))
        text = "0/541.*2026-08-27T20:00:00" if coverage == "none" else "540/541"
        with pytest.raises(FxUnavailable, match=text):
            run_backtest({"BTC/USDC": frame}, "4h", config, None, **args)

def test_panel_rejects_an_empty_archive_before_creating_job(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sentinelle.api.server import create_api
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource

    app = App(tmp_path, source=SyntheticSource(["BTC/USDC"], days=2), start=False,
              settings_override={"market": {"source": "synthetic"}})
    try:
        # Same offline object; select archived FX instead of the explicit demo generator.
        app.settings["market"]["source"] = "binance"
        monkeypatch.setattr(app.jobs, "submit", lambda *a: pytest.fail("Job started"))
        with TestClient(create_api(app), headers={"X-Sentinelle": "1"}) as client:
            assert client.post("/api/auth/setup", json={"password": "isolated-test-password"}).is_success
            response = client.post("/api/ai/backtest", json={"days": 90, "use_model": False, "capital": 30})
            assert response.status_code == 400
            assert "aucun carnet historique EUR/USDC" in response.json()["detail"]
    finally:
        app.close()

@pytest.mark.parametrize("rate", [0., -2., 999., float("nan")])
def test_historical_mark_must_match_archived_book(db, rate):
    import json
    service = FxService("EUR", [], db=db)
    book = FXBook([FXQuote("EUR", "USDC", 1.05, 1.06, START, "fixture", 30, 30)])
    service._remember(FxQuote.from_book("EUR", "USDC", book, 60))
    row = json.loads(db.scalar("SELECT snapshot FROM fx_snapshots"))
    row["rate"] = rate
    db.execute("UPDATE fx_snapshots SET snapshot=?", (json.dumps(row),))
    with pytest.raises(FxUnavailable, match="Taux de valorisation"):
        service.historical("USDC", START)
