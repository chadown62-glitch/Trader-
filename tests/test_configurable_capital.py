"""Portefeuilles fictifs et empreintes des protocoles scientifiques gelés."""
import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.dmh.ledger import Ledger, StandaloneCash
from sentinelle.dmh.security import Verdict, check_financial
from tests.test_portfolio import make_app

HEADERS = {"X-Sentinelle": "1"}


@pytest.mark.parametrize("capital", [30, 100, 1000, 10000])
def test_paper_wallet_via_api(tmp_path, capital):
    app = make_app(tmp_path)
    try:
        with TestClient(create_api(app), headers=HEADERS) as api:
            assert api.post("/api/auth/setup", json={"password": "mot-de-passe-de-test"}).is_success
            old = app.main_account()["id"]
            old_ledger = api.get(f"/api/accounts/{old}/ledger").json()
            body = {} if capital == 30 else {"initial_balance": capital}
            response = api.post("/api/accounts", json=body)
            assert response.is_success, response.text
            aid = response.json()["id"]
            assert [a["id"] for a in api.get("/api/accounts").json()] == [aid]
            accounts = api.get("/api/accounts?include_archived=true").json()
            assert [(a["id"], a["archived"]) for a in accounts] == [(old, 1), (aid, 0)]
            assert api.post(f"/api/accounts/{old}/reset", json=body).is_client_error
            status = api.get("/api/dmh/status")
            assert status.is_success, status.text
            pf = app.dmh.portfolio()
            limits = app.dmh.limits()
            assert pf.initial == pf.cash_available == capital
            for key, amount in zip(("max_position_eur", "min_cash_reserve_eur", "max_daily_loss_eur",
                                    "max_total_loss_eur"), (capital / 3, capital / 5, capital / 20, capital / 5)):
                assert limits[key] == pytest.approx(amount)
            for code, state, size in (
                ("position", pf, capital / 3 + .01),
                ("reserve", replace(pf, cash_total=capital / 5 + 9), 10),
                ("perte_jour", replace(pf, equity=capital * .94), 10),
                ("perte_totale", replace(pf, equity=capital * .79), 10),
            ):
                verdict = Verdict()
                check_financial(verdict, limits, state, side="buy", asset="BTC", notional=size, route_totals=None)
                assert code in {reason.code for reason in verdict.reasons}
            order = api.post("/api/orders", json={"account_id": aid, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 10})
            assert order.is_success, order.text
            assert api.get(f"/api/accounts/{aid}").json()["cash"] < capital
            assert api.post(f"/api/accounts/{aid}/reset", json={"initial_balance": capital}).is_success
            assert api.get(f"/api/accounts/{aid}").json()["cash"] == capital
            assert api.get(f"/api/accounts/{old}/ledger").json() == old_ledger
            ledger = Ledger(app.db, aid)
            assert StandaloneCash(ledger).total() == capital
            assert StandaloneCash(ledger, 200).total() == capital
    finally:
        app.close()


# SHA-256 du manifeste complet des fichiers modifiés par chaque PR par rapport à sa base.
# Chaque ligne contient le SHA-256 des octets Git, deux espaces, le chemin UTF-8 et LF.
FROZEN = [
    ('ff4650933eceb915e1c45d011da36b4dec280e54',
     '5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18',
     '3892ab610954fce7551581b1e7526bcefdcff1b10e0637cc6e7ac7b6688a857f'),
    ('5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18',
     '18b31bb901f656d6126b370f47c510775f135c57',
     '4686e4b9f866a7d73afa6bd547ac454a3c04af73ae1236c0cde3bfa5603d7b2b'),
]


@pytest.mark.parametrize(("base", "head", "expected"), FROZEN)
def test_frozen_scientific_files(base, head, expected):
    root = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)

    paths = git("diff", "--no-renames", "--name-only", "-z", base, head).split(b"\0")
    manifest = hashlib.sha256()
    for path in sorted(filter(None, paths)):
        data = git("cat-file", "blob", head + ":" + path.decode("utf-8"))
        manifest.update(hashlib.sha256(data).hexdigest().encode() + b"  " + path + b"\n")
    assert manifest.hexdigest() == expected
