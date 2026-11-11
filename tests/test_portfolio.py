"""Portefeuille principal de 30 € fictifs : comptabilité en euros, conversion explicite, réalisme, migration."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from sentinelle.api.server import create_api
from sentinelle.backtest.engine import run_backtest
from sentinelle.config import DEFAULT_SETTINGS, SettingsError, apply_update, validate_settings
from sentinelle.execution.base import OrderError
from sentinelle.execution.paper import PaperBroker
from sentinelle.execution.positions import PositionManager
from sentinelle.market.fx import FxQuote, FxService, FxUnavailable, ecb_fetcher, market_fetcher, synthetic_fetcher
from sentinelle.market.markets import MarketInfo
from sentinelle.market.sources import SyntheticSource
from sentinelle.risk.manager import RiskManager
from sentinelle.services.common import UserError
from sentinelle.services.core import App
from sentinelle.storage.db import Database

from .conftest import market_fn
from .helpers import make_series

H = {"X-Sentinelle": "1"}
RATE = 0.90  # 1 USDC = 0,90 €


# ---------------------------------------------------------------------------
# 1. Taux de change : source et horodatage explicites, jamais de taux inventé
# ---------------------------------------------------------------------------
class FakeSource:
    name = "bourse-test"

    def __init__(self, pairs, now=1_700_000_000.0):
        self.pairs, self.now, self.calls = dict(pairs), now, 0

    def now_ms(self):
        return int(self.now * 1000)

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=2):
        self.calls += 1
        if symbol not in self.pairs:
            raise KeyError(symbol)
        px = self.pairs[symbol]
        minute = int(self.now // 60) * 60_000
        return [[minute - 60_000, px, px, px, px, 1.0], [minute, px, px, px, px, 1.0]]


def test_fx_reads_the_market_pair_with_source_and_timestamp():
    src = FakeSource({"EUR/USDC": 1.25, "EUR/USDT": 1.20})
    fx = FxService("EUR", [market_fetcher(src, 600)], clock=lambda: src.now)
    q = fx.quote("USDC")
    assert q.rate == pytest.approx(0.8) and q.source == "bourse-test EUR/USDC" and not q.approx
    assert src.now - 60 <= q.ts <= src.now and q.max_age == 600
    assert fx.quote("EUR").rate == 1.0  # même monnaie : pas de conversion
    d = q.to_dict(src.now)
    assert d["inverse"] == pytest.approx(1.25) and d["stale"] is False and d["age_s"] <= 60
    # pas de paire directe : EUR/USDT, signalé comme approximation
    usd = fx.quote("USD")
    assert usd.rate == pytest.approx(1 / 1.20) and usd.approx and "≈" in usd.source


def test_fx_cache_fallback_and_refusal_when_stale():
    src = FakeSource({"EUR/USDC": 1.25})
    fx = FxService("EUR", [market_fetcher(src, 600)], clock=lambda: src.now, ttl=30)
    assert fx.quote("USDC").rate == pytest.approx(0.8)
    calls = src.calls
    fx.quote("USDC")
    assert src.calls == calls  # relevé récent : pas de nouvel appel réseau
    src.pairs.clear()  # la source ne répond plus
    src.now += 120
    assert fx.quote("USDC").rate == pytest.approx(0.8)  # dernier taux connu, encore assez récent
    src.now += 3600
    with pytest.raises(FxUnavailable) as err:  # trop ancien : aucun ordre
        fx.quote("USDC")
    assert err.value.code == "no_fx" and "trop ancien" in err.value.message
    stale = fx.quote("USDC", for_trading=False)  # mais l'affichage garde le dernier taux, signalé comme ancien
    assert stale.rate == pytest.approx(0.8) and stale.to_dict(src.now)["stale"] is True
    with pytest.raises(FxUnavailable):
        fx.quote("JPY")  # monnaie inconnue : refus, jamais de taux inventé


def test_fx_remembers_the_last_rate_across_restarts(db):
    src = FakeSource({"EUR/USDC": 1.25})
    FxService("EUR", [market_fetcher(src)], clock=lambda: src.now, db=db).quote("USDC")
    row = db.one("SELECT * FROM fx_rates")
    assert row["quote"] == "USDC" and row["rate"] == pytest.approx(0.8) and row["source"] == "bourse-test EUR/USDC"
    offline = FxService("EUR", [market_fetcher(FakeSource({}))], clock=lambda: src.now + 10, db=db)
    assert offline.quote("USDC").rate == pytest.approx(0.8)  # redémarrage sans réseau : dernier taux mémorisé


def test_fx_ecb_reference_rate_is_parsed_and_marked(monkeypatch):
    import httpx

    xml = "<Cube><Cube time='2026-11-10'><Cube currency='USD' rate='1.1214'/><Cube currency='JPY' rate='170.1'/></Cube></Cube>"
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: type("R", (), {"text": xml})())
    fetch = ecb_fetcher()
    usd = fetch("EUR", "USD")
    assert usd.rate == pytest.approx(1 / 1.1214) and not usd.approx and "BCE" in usd.source
    usdc = fetch("EUR", "USDC")
    assert usdc.approx and "1 USDC ≈ 1 USD" in usdc.source
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(usdc.ts)) == "2026-11-10 14:15"
    assert fetch("EUR", "BTC") is None and fetch("CHF", "USD") is None


def test_fx_synthetic_rate_is_deterministic():
    a = synthetic_fetcher(lambda: 1_700_000_000.0)("EUR", "USDC")
    b = synthetic_fetcher(lambda: 1_700_000_000.0)("EUR", "USDC")
    assert a == b and 0.85 < a.rate < 0.95 and "simulé" in a.source


# ---------------------------------------------------------------------------
# 2. Courtier simulé : compte en euros, paires en USDC
# ---------------------------------------------------------------------------
class Fx:
    """Taux réglable pour les tests."""

    def __init__(self, rate=RATE, now=lambda: 1_700_000_000.0):
        self.rate, self.now, self.down = rate, now, False

    def __call__(self, currency, for_trading=True):
        if self.down:
            raise FxUnavailable("Taux de change EUR/USDC indisponible")
        return FxQuote("EUR", currency, self.rate, "taux-test", self.now(), False, 1800.0)


@pytest.fixture
def fx(clock):
    return Fx(now=clock)


@pytest.fixture
def euro(db, prices, clock, fx):
    """Compte de 30 € sur des paires cotées en USDC : frais 0,1 %, glissement 0,10 %, conversion 0,05 %."""
    acc = db.insert("accounts", {"name": "principal", "kind": "paper", "role": "main", "quote": "EUR", "initial_balance": 30, "created_at": clock()})
    b = PaperBroker(db, acc, "EUR", market_fn, prices, fee_rate=0.001, slippage_bps=10, clock=clock,
                    fx_fn=fx, fx_cost_bps=5, trade_quote="USDC")
    b.set_balance("EUR", 30.0, "depot", "Capital de départ")
    return b


def ledger_matches_balances(db, account_id):
    sums = {r["asset"]: r["s"] for r in db.query("SELECT asset, SUM(amount) AS s FROM ledger WHERE account_id = ? GROUP BY asset", (account_id,))}
    bal = {r["asset"]: r["total"] for r in db.query("SELECT asset, total FROM balances WHERE account_id = ?", (account_id,))}
    assert set(sums) == set(bal)
    for asset, total in bal.items():
        assert total >= 0, f"solde négatif : {asset}"
        assert sums[asset] == pytest.approx(total, abs=1e-9)
    last = {r["asset"]: r["balance_after"] for r in db.query("SELECT asset, balance_after FROM ledger WHERE account_id = ? ORDER BY id", (account_id,))}
    assert last == pytest.approx(bal)


def test_euro_account_buy_converts_explicitly(db, euro, clock):
    order = euro.place_order("BTC/USDC", "buy", "market", 0.0002)
    assert order["status"] == "filled"
    px = 50_000 * 1.001  # glissement défavorable
    gross = 0.0002 * px  # 10,01 USDC
    fee = gross * 0.001
    conv = (gross + fee) * RATE * 0.0005
    spent = (gross + fee) * RATE + conv
    assert euro.balances()["EUR"] == pytest.approx(30 - spent, abs=1e-9)
    assert euro.balances()["BTC"] == pytest.approx(0.0002)
    assert "USDC" not in euro.balances()  # aucune poche en USDC : tout est tenu en euros
    fill = db.one("SELECT * FROM fills")
    assert fill["price"] == pytest.approx(px) and fill["fee"] == pytest.approx(fee) and fill["fee_asset"] == "USDC"
    assert fill["fx_rate"] == RATE and fill["fx_source"] == "taux-test" and fill["fx_ts"] == clock()
    assert fill["fx_cost"] == pytest.approx(conv) and fill["cash_amount"] == pytest.approx(-spent) and fill["cash_asset"] == "EUR"
    rows = db.query("SELECT kind, asset, amount, fx_rate, fx_source, fill_id FROM ledger WHERE account_id = ? ORDER BY id", (euro.account_id,))
    assert [(r["kind"], r["asset"]) for r in rows] == [("depot", "EUR"), ("achat", "EUR"), ("frais", "EUR"), ("conversion", "EUR"), ("achat", "BTC")]
    assert rows[1]["amount"] == pytest.approx(-gross * RATE) and rows[2]["amount"] == pytest.approx(-fee * RATE)
    assert all(r["fx_rate"] == RATE and r["fx_source"] == "taux-test" and r["fill_id"] == fill["id"] for r in rows[1:])
    ledger_matches_balances(db, euro.account_id)
    total, cash = euro.equity()
    assert cash == pytest.approx(30 - spent) and total == pytest.approx(cash + 0.0002 * 50_000 * RATE)
    assert total < 30  # les frais, le glissement et la conversion ont été payés


def test_euro_account_never_goes_negative_no_leverage_no_short(db, euro, prices):
    with pytest.raises(OrderError) as err:  # 0,001 BTC = 50 USDC = 45 € : plus que les 30 € disponibles
        euro.place_order("BTC/USDC", "buy", "market", 0.001)
    assert err.value.code == "insufficient_funds" and "EUR" in err.value.message
    with pytest.raises(OrderError):  # rien à vendre : pas de vente à découvert
        euro.place_order("BTC/USDC", "sell", "market", 0.0002)
    assert euro.balances() == {"EUR": 30.0}
    euro.place_order("BTC/USDC", "buy", "market", 0.0005)  # 25 USDC ≈ 22,5 €
    with pytest.raises(OrderError):  # le reste ne suffit plus
        euro.place_order("BTC/USDC", "buy", "market", 0.0005)
    with pytest.raises(OrderError):  # on ne vend pas plus que ce que l'on détient
        euro.place_order("BTC/USDC", "sell", "market", 0.0006)
    with pytest.raises(OrderError) as err:  # écriture directe qui rendrait un solde négatif : refusée
        euro._adjust("EUR", -1_000.0)
    assert "négatif" in err.value.message
    with pytest.raises(OrderError):
        euro.set_balance("EUR", -1.0)
    euro.place_order("BTC/USDC", "sell", "market", 0.0005)
    assert euro.balances().get("BTC", 0.0) == 0.0
    assert all(v >= 0 for v in euro.balances().values())
    ledger_matches_balances(db, euro.account_id)
    assert db.scalar("SELECT MIN(balance_after) FROM ledger") >= 0


def test_order_is_refused_without_a_usable_rate(db, euro, fx):
    fx.down = True
    with pytest.raises(OrderError) as err:
        euro.place_order("BTC/USDC", "buy", "market", 0.0002)
    assert err.value.code == "no_fx"
    assert euro.balances() == {"EUR": 30.0} and db.scalar("SELECT COUNT(*) FROM fills") == 0
    fx.down = False
    order = euro.place_order("BTC/USDC", "buy", "limit", 0.0002, price=49_000.0)  # ordre en attente
    assert order["status"] == "open"
    fx.down = True
    assert euro.process_bar("BTC/USDC", 49_500, 49_600, 48_900, 49_100, int(euro.clock() * 1000)) == 0  # touché, mais sans taux : pas d'exécution
    assert db.one("SELECT status, error FROM orders WHERE id = ?", (order["id"],))["status"] == "rejected"
    assert euro.balances() == {"EUR": 30.0}


def test_euro_account_without_fx_cannot_trade_a_usdc_pair(db, prices, clock):
    acc = db.insert("accounts", {"name": "x", "kind": "paper", "role": "main", "quote": "EUR", "created_at": 1})
    b = PaperBroker(db, acc, "EUR", market_fn, prices, clock=clock)
    b.set_balance("EUR", 30.0)
    with pytest.raises(OrderError) as err:
        b.place_order("BTC/USDC", "buy", "market", 0.0002)
    assert err.value.code == "no_fx" and b.equity() == (30.0, 30.0)


def test_available_reserved_and_invested(db, euro, prices):
    euro.place_order("BTC/USDC", "buy", "market", 0.0002)
    pending = euro.place_order("ETH/USDC", "buy", "limit", 0.004, price=2_000.0)  # 8 USDC en attente
    parts = euro.breakdown()
    reserve = 0.004 * 2_000 * (1 + 0.001 + 0.001) * RATE * 1.0005
    assert parts["currency"] == "EUR" and parts["reserved"] == pytest.approx(reserve)
    assert parts["available"] == pytest.approx(parts["cash"] - reserve)
    assert parts["invested"] == pytest.approx(0.0002 * 50_000 * RATE)
    assert parts["equity"] == pytest.approx(parts["cash"] + parts["invested"])
    with pytest.raises(OrderError):  # les liquidités réservées ne peuvent pas être dépensées deux fois
        euro.place_order("BTC/USDC", "buy", "market", 0.00035)  # 15,8 € demandés, 13,7 € disponibles
    euro.cancel_order(pending["id"])
    assert euro.breakdown()["reserved"] == 0.0
    assert euro.place_order("BTC/USDC", "buy", "market", 0.00035)["status"] == "filled"  # réserve libérée


def test_position_result_uses_the_rate_at_entry_and_at_exit(db, euro, prices, clock, fx):
    pm = PositionManager(db, euro, clock=clock)
    pos = pm.open("BTC/USDC", 0.0002, origin="manual", stop=47_000.0)
    assert pos["status"] == "open" and pos["currency"] == "EUR" and pos["entry_fx"] == pytest.approx(RATE)
    entry = 50_000 * 1.001
    assert pos["risk_amount"] == pytest.approx((entry - 47_000) * 0.0002 * RATE)  # risque en euros
    entry_fees = (0.0002 * entry * 0.001) * RATE + (0.0002 * entry * 1.001) * RATE * 0.0005
    assert pos["entry_fees"] == pytest.approx(entry_fees)
    # même prix en USDC, mais l'euro a baissé : 1 USDC vaut maintenant 0,95 €
    fx.rate = 0.95
    u = pm.unrealized(pm.get(pos["id"]))
    assert u["value"] == pytest.approx(0.0002 * 50_000 * 0.95) and u["fx"] == 0.95 and u["pnl"] > 0
    closed = pm.close(pos["id"], "test")
    exit_px = 50_000 * 0.999
    gross = 0.0002 * exit_px
    exit_fees = gross * 0.001 * 0.95 + (gross - gross * 0.001) * 0.95 * 0.0005
    expected = gross * 0.95 - 0.0002 * entry * RATE - exit_fees - entry_fees
    assert closed["status"] == "closed" and closed["realized_pnl"] == pytest.approx(expected, abs=1e-9)
    assert closed["fees"] == pytest.approx(entry_fees + exit_fees)
    assert euro.balances()["EUR"] == pytest.approx(30 + expected, abs=1e-9)  # le résultat annoncé est exactement ce qui est en caisse
    assert closed["pnl_r"] == pytest.approx(expected / pos["risk_amount"])
    ledger_matches_balances(db, euro.account_id)


def test_rejected_fill_is_recorded_as_rejected(db, euro, prices):
    order = euro.place_order("BTC/USDC", "buy", "limit", 0.0005, price=49_000.0)
    euro.set_balance("EUR", 1.0)  # l'argent n'est plus là au moment de l'exécution
    euro.process_bar("BTC/USDC", 49_500, 49_600, 48_900, 49_100, int(euro.clock() * 1000))
    row = db.one("SELECT status, error FROM orders WHERE id = ?", (order["id"],))
    assert row["status"] == "rejected" and "insuffisants" in row["error"]
    assert euro.balances() == {"EUR": 1.0}


def test_same_currency_account_is_unchanged(db, paper):
    """Compte tenu dans la monnaie de cotation (anciens comptes, backtests) : aucun taux, aucun coût de conversion."""
    paper.place_order("BTC/USDC", "buy", "market", 0.01)
    fill = db.one("SELECT * FROM fills")
    assert fill["fx_rate"] == 1.0 and fill["fx_source"] is None and fill["fx_cost"] == 0
    px = 50_000 * 1.001
    assert paper.balances()["USDC"] == pytest.approx(10_000 - 0.01 * px * 1.001)
    ledger_matches_balances(db, paper.account_id)


def test_observed_spread_makes_fills_worse(db, prices, clock):
    acc = db.insert("accounts", {"name": "x", "kind": "paper", "role": "main", "quote": "USDC", "created_at": 1})
    b = PaperBroker(db, acc, "USDC", market_fn, prices, fee_rate=0.0, slippage_bps=10, clock=clock, spread_fn=lambda s: 0.002)
    b.set_balance("USDC", 1_000.0)
    buy = b.place_order("BTC/USDC", "buy", "market", 0.01)
    sell = b.place_order("BTC/USDC", "sell", "market", 0.01)
    assert buy["avg_price"] == pytest.approx(50_000 * 1.003) and sell["avg_price"] == pytest.approx(50_000 * 0.997)
    b.spread_fn = lambda s: 1 / 0  # carnet indisponible : seul le glissement s'applique
    assert b.place_order("BTC/USDC", "buy", "market", 0.01)["avg_price"] == pytest.approx(50_000 * 1.001)


# ---------------------------------------------------------------------------
# 3. Gestionnaire de risque avec 30 € : minimums de la plateforme
# ---------------------------------------------------------------------------
BTC = MarketInfo("BTC/USDC", "BTC", "USDC", amount_step=1e-5, price_step=0.01, min_amount=1e-5, min_notional=5.0)


def risk_for(db, account_id, clock, **risk):
    settings = apply_update(DEFAULT_SETTINGS, {"risk": {"cooldown_minutes_after_loss": 0, **risk}})
    return RiskManager(db, account_id, lambda: settings, clock)


def decide(rm, entry, stop, target=None, equity=30.0, cash=30.0):
    return rm.evaluate_entry("BTC/USDC", entry, stop, target, equity=equity, cash=cash, market=BTC,
                             fee_rate=0.001, slip=0.0005, fx=RATE, fx_cost=0.0005)


def test_small_capital_order_is_raised_to_the_minimum_when_risk_stays_low(db, euro, clock):
    rm = risk_for(db, euro.account_id, clock)
    # 0,5 % de 30 € = 0,15 € de risque ; stop à 3 % : taille prudente 0,00011 BTC = 5,5 USDC, mais 5,33 USDC au stop
    d = decide(rm, 50_000.0, 48_500.0)
    assert d.approved and not d.bumped and d.qty == pytest.approx(0.00011)
    assert d.risk_amount == pytest.approx(0.00011 * 1_500 * RATE) and d.notional == pytest.approx(0.00011 * 50_000 * RATE)
    assert d.costs == pytest.approx(d.notional * (0.002 + 0.001 + 0.001))
    # stop à 4 % : taille prudente 0,00008 BTC = 4 USDC, sous le minimum ; portée à 0,00011 BTC (5,28 USDC au stop)
    d = decide(rm, 50_000.0, 48_000.0)
    assert d.approved and d.bumped and d.qty == pytest.approx(0.00011)
    assert d.qty * 48_000 >= 5.0 and d.risk_amount / 30 <= 0.01
    assert any("minimum" in c["detail"] for c in d.checks if c["name"] == "taille")


def test_impossible_or_too_risky_small_orders_are_refused(db, euro, clock):
    rm = risk_for(db, euro.account_id, clock)
    # stop à 10 % : au minimum (0,00012 BTC), le risque serait de 1,8 % du capital, au-dessus du plafond de 1 %
    d = decide(rm, 50_000.0, 45_000.0)
    assert not d.approved and "plafond" in d.reasons[0] and "minimum 5 USDC" in d.reasons[0]
    # plus assez de liquidités pour le minimum de la plateforme
    d = decide(rm, 50_000.0, 48_500.0, cash=3.0)
    assert not d.approved and "Ordre impossible avec ce capital" in d.reasons[0] and "liquidités" in d.reasons[0]
    # capital trop faible pour la règle d'exposition par crypto (30 % de 12 € = 3,6 €)
    d = decide(rm, 50_000.0, 48_500.0, equity=12.0, cash=12.0)
    assert not d.approved and "par crypto" in d.reasons[0]
    # taux de change inutilisable
    d = rm.evaluate_entry("BTC/USDC", 50_000.0, 48_500.0, None, equity=30.0, cash=30.0, market=BTC, fee_rate=0.001, slip=0.0005, fx=0.0)
    assert not d.approved and "change" in d.reasons[0].lower()
    # un plafond plus haut (dans la limite absolue de 2 %) autorise le premier cas
    assert decide(risk_for(db, euro.account_id, clock, min_order_max_risk=0.02), 50_000.0, 45_000.0).approved
    with pytest.raises(SettingsError):
        apply_update(DEFAULT_SETTINGS, {"risk": {"min_order_max_risk": 0.05}})


def test_conversion_cost_counts_in_the_fee_check(db, euro, clock):
    rm = risk_for(db, euro.account_id, clock, min_reward_to_fees=3.0)
    near = 50_000 * (1 + 3 * 0.0031)  # objectif à 3 × (frais + glissement), sans la conversion
    assert rm.evaluate_entry("BTC/USDC", 50_000.0, 48_500.0, near, equity=30.0, cash=30.0, market=BTC, fee_rate=0.001, slip=0.0005).approved
    d = decide(rm, 50_000.0, 48_500.0, near)  # avec la conversion, le même objectif ne couvre plus 3 fois les coûts
    assert not d.approved and "frais" in d.reasons[0]


# ---------------------------------------------------------------------------
# 4. Application : portefeuille unique, partagé, réaliste
# ---------------------------------------------------------------------------
def make_app(path, **overrides):
    app = App(path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=5), start=False, settings_override={
        "market": {"source": "synthetic"}, "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}, **overrides})
    app._startup()
    app.hub.poll()
    return app


@pytest.fixture
def app(tmp_path):
    instance = make_app(tmp_path)
    yield instance
    instance.db.close()


def test_new_install_has_one_shared_portfolio_of_30_euros(app):
    accounts = app.db.query("SELECT * FROM accounts")
    assert len(accounts) == 1
    main = accounts[0]
    assert (main["role"], main["kind"], main["quote"], main["initial_balance"]) == ("main", "paper", "EUR", 30.0)
    assert app.portfolio_mode() == "main" and app.ai_account()["id"] == main["id"]
    assert app.agents_status()[0]["account_id"] == main["id"]  # l'IA et le trading manuel partagent ce compte
    summary = app.account_summary(main["id"])
    assert summary["equity"] == summary["cash"] == summary["available"] == 30.0
    assert summary["reserved"] == 0 and summary["invested"] == 0 and summary["quote"] == "EUR" and summary["trade_quote"] == "USDC"
    fx = summary["fx"]
    assert fx["base"] == "EUR" and fx["quote"] == "USDC" and fx["source"] and fx["ts"] > 0 and fx["stale"] is False
    book = app.ledger(main["id"])
    assert book["currency"] == "EUR" and book["check"] == {"ok": True, "gaps": {}, "negative": [], "entries": 1}
    assert (book["rows"][0]["kind"], book["rows"][0]["amount"], book["rows"][0]["balance_after"]) == ("depot", 30.0, 30.0)
    status = app.overview()["portfolio"]
    assert status["mode"] == "main" and status["migration_available"] is False and status["legacy_accounts"] == []
    assert status["fx"]["rates"]["USDC"]["source"]
    with pytest.raises(UserError):  # un seul portefeuille papier
        app.create_paper_account("Deuxième compte", 1_000)


def test_manual_order_in_euros_and_impossible_orders(app):
    main = app.main_account()["id"]
    last = app.hub.price("BTC/USDC")
    rate = app.fx.quote("USDC").rate
    prev = app.preview_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 10.0, "stop_loss": last * 0.95})
    assert prev["error"] is None and prev["currency"] == "EUR" and prev["quote"] == "USDC"
    assert prev["fx"]["rate"] == pytest.approx(rate) and prev["fx"]["source"] and prev["fx"]["ts"]
    assert prev["total_cash"] <= 10.0 and prev["total_cash"] == pytest.approx(10.0, rel=0.02)  # frais et conversion compris
    assert prev["risk_pct"] == pytest.approx(prev["risk_amount"] / 30 * 100)
    # 2 € : sous le minimum de la plateforme (5 USDC) ; refusé, rien ne bouge
    with pytest.raises(UserError) as err:
        app.place_manual_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 2.0})
    assert "minimal" in str(err.value).lower()
    # 5 € avec un stop à 20 % : l'achat passerait, mais le stop serait refusé par la plateforme
    with pytest.raises(UserError) as err:
        app.place_manual_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 5.0, "stop_loss": last * 0.8})
    assert "stop" in str(err.value)
    with pytest.raises(UserError):  # plus que le capital
        app.place_manual_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 40.0})
    assert app.account_summary(main)["equity"] == 30.0 and app.db.scalar("SELECT COUNT(*) FROM fills") == 0

    res = app.place_manual_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 10.0, "stop_loss": last * 0.95})
    assert res["position"]["status"] == "open"
    s = app.account_summary(main)
    assert s["cash"] == pytest.approx(30 - prev["total_cash"], abs=0.02) and s["cash"] >= 20.0
    assert s["invested"] == pytest.approx(res["position"]["qty"] * last * rate, rel=1e-6)
    assert s["equity"] == pytest.approx(s["cash"] + s["invested"]) and 29.9 < s["equity"] < 30.0
    assert s["available"] == pytest.approx(s["cash"]) and s["positions"][0]["value"] == pytest.approx(s["invested"], rel=1e-6)
    assert app.ledger(main)["check"]["ok"] is True
    closed = app.close_position(res["position"]["id"])
    assert closed["status"] == "closed" and closed["realized_pnl"] < 0  # aller-retour immédiat : on paie les frais
    s = app.account_summary(main)
    assert s["invested"] == 0 and s["equity"] == pytest.approx(30 + closed["realized_pnl"], abs=1e-9)
    assert app.ledger(main)["check"]["ok"] is True


def test_backtest_and_training_cannot_touch_the_main_portfolio(app):
    main = app.main_account()["id"]
    before = json.dumps([app.db.query(f"SELECT * FROM {t} ORDER BY 1") for t in ("accounts", "balances", "ledger", "positions", "orders", "fills", "equity")], default=str)
    hist = {"BTC/USDC": make_series(9000, seed=31, start=30_000), "ETH/USDC": make_series(9000, seed=32, start=2_000)}
    cfg = apply_update(app.settings, {"ai": {"regime_filter": False, "timeframe": "1h"}, "risk": {"min_reward_to_fees": 1.0}})
    result = run_backtest(hist, "1h", cfg, None, exploration=True)
    assert result["metrics"]["n"] > 0  # la simulation a bien tradé… dans sa propre base en mémoire
    after = json.dumps([app.db.query(f"SELECT * FROM {t} ORDER BY 1") for t in ("accounts", "balances", "ledger", "positions", "orders", "fills", "equity")], default=str)
    assert before == after
    assert app.account_summary(main)["equity"] == 30.0


def test_reset_returns_to_the_starting_capital_and_keeps_a_trace(app):
    main = app.main_account()["id"]
    app.place_manual_order({"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 10.0})
    app.reset_paper_account(main, None)
    s = app.account_summary(main)
    assert s["equity"] == s["cash"] == 30.0 and s["balances"] == {"EUR": 30.0} and s["positions"] == []
    kinds = [r["kind"] for r in app.ledger(main)["rows"]]
    assert kinds.count("reinitialisation") == 2 and "achat" in kinds and app.ledger(main)["check"]["ok"] is True
    app.reset_paper_account(main, 50.0)
    assert app.account_summary(main)["equity"] == 50.0 and app.main_account()["initial_balance"] == 50.0
    with pytest.raises(UserError):
        app.reset_paper_account(main, 5.0)


def test_portfolio_settings_are_bounded():
    s = validate_settings(DEFAULT_SETTINGS)
    assert s["portfolio"] == {"currency": "EUR", "initial": 30.0, "fx_cost_bps": 5.0, "fx_max_age_minutes": 30}
    for bad in ({"initial": 5.0}, {"currency": "USD"}, {"fx_cost_bps": -1.0}, {"fx_max_age_minutes": 0}):
        with pytest.raises(SettingsError):
            apply_update(DEFAULT_SETTINGS, {"portfolio": bad})


# ---------------------------------------------------------------------------
# 5. Installation existante : migration explicite, rien n'est remplacé en silence
# ---------------------------------------------------------------------------
def legacy_install(path):
    """Base telle qu'une ancienne version l'a laissée : deux comptes papier de 10 000 USDC, avec un historique."""
    db = Database(path / "sentinelle.db")
    ids = {}
    for role, name, cash in (("ai_training", "Entraînement IA", 9_500.0), ("manual", "Mon compte papier", 10_000.0)):
        ids[role] = db.insert("accounts", {"name": name, "kind": "paper", "role": role, "quote": "USDC", "initial_balance": 10_000.0,
                                           "created_at": 1.0, "data": "{}"})
        db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, 'USDC', ?)", (ids[role], cash))
    db.execute("INSERT INTO balances(account_id, asset, total) VALUES (?, 'BTC', 0.01)", (ids["ai_training"],))
    db.insert("positions", {"account_id": ids["ai_training"], "symbol": "BTC/USDC", "origin": "ai", "agent": "ia_entrainement", "status": "open",
                            "qty": 0.01, "initial_qty": 0.01, "entry_price": 50_000.0, "entry_ts": 2.0, "entry_fees": 0.5, "fees": 0.5})
    db.insert("positions", {"account_id": ids["ai_training"], "symbol": "ETH/USDC", "origin": "ai", "status": "closed", "qty": 0.0,
                            "initial_qty": 1.0, "entry_price": 2_000.0, "entry_ts": 2.0, "exit_ts": 3.0, "exit_price": 2_100.0, "realized_pnl": 96.0})
    db.execute("INSERT INTO equity(account_id, ts, equity, cash) VALUES (?, 5.0, 10096.0, 9500.0)", (ids["ai_training"],))
    db.close()
    return ids


def test_existing_install_keeps_its_accounts_until_explicit_migration(tmp_path):
    ids = legacy_install(tmp_path)
    app = make_app(tmp_path)
    try:
        assert app.portfolio_mode() == "legacy" and app.main_account() is None  # rien n'est créé ni remplacé en silence
        assert {r["role"] for r in app.db.query("SELECT role FROM accounts")} == {"ai_training", "manual"}
        assert app.db.one("SELECT total FROM balances WHERE account_id = ? AND asset = 'USDC'", (ids["ai_training"],))["total"] == 9_500.0
        assert app.ai_account()["id"] == ids["ai_training"] and set(app.runtimes) == set(ids.values())
        status = app.portfolio_status()
        assert status["migration_available"] is True and status["initial"] == 30.0
        assert {a["id"] for a in status["legacy_accounts"]} == set(ids.values())
        assert next(a for a in status["legacy_accounts"] if a["role"] == "ai_training")["open_positions"] == 1
        assert app.ledger(ids["manual"])["check"]["ok"] is True  # soldes repris tels quels dans le grand livre
        # l'ancien compte fonctionne toujours, dans sa monnaie
        order = app.place_manual_order({"account_id": ids["manual"], "symbol": "BTC/USDC", "side": "buy", "quote_amount": 500})
        assert order["position"]["status"] == "open" and order["position"]["entry_fx"] == 1.0

        with pytest.raises(UserError):  # la migration exige une confirmation explicite
            app.activate_main_portfolio()
        assert app.portfolio_mode() == "legacy"

        res = app.activate_main_portfolio(confirm=True)
        assert app.portfolio_mode() == "main" and res["positions_closed"] == 2
        main = app.main_account()
        assert (main["quote"], main["initial_balance"]) == ("EUR", 30.0) and app.account_summary(main["id"])["equity"] == 30.0
        assert app.ai_account()["id"] == main["id"] and set(app.runtimes) == {main["id"]}
        # anciens comptes : toujours là, non archivés, historique intact, positions fermées au prix du marché simulé
        old = {r["id"]: r for r in app.db.query("SELECT * FROM accounts WHERE role != 'main'")}
        assert set(old) == set(ids.values()) and all(a["archived"] == 0 and a["initial_balance"] == 10_000.0 for a in old.values())
        assert app.db.scalar("SELECT COUNT(*) FROM positions WHERE account_id = ? AND status = 'closed'", (ids["ai_training"],)) == 2
        assert app.db.scalar("SELECT COUNT(*) FROM positions WHERE status IN ('open', 'pending')") == 0
        assert app.db.one("SELECT total FROM balances WHERE account_id = ? AND asset = 'USDC'", (ids["ai_training"],))["total"] > 9_500.0
        assert app.db.scalar("SELECT COUNT(*) FROM equity WHERE account_id = ?", (ids["ai_training"],)) >= 2
        summary = app.account_summary(ids["ai_training"])
        assert summary["tradable"] is False and summary["archived_paper"] is True and summary["equity"] > 9_500.0
        with pytest.raises(UserError) as err:  # lecture seule
            app.place_manual_order({"account_id": ids["manual"], "symbol": "BTC/USDC", "side": "buy", "quote_amount": 50})
        assert "portefeuille principal" in str(err.value)
        with pytest.raises(UserError):
            app.activate_main_portfolio(confirm=True)  # déjà fait
        assert app.portfolio_status()["migration_available"] is False
    finally:
        app.db.close()
    again = make_app(tmp_path)  # redémarrage : le choix est conservé
    try:
        assert again.portfolio_mode() == "main" and set(again.runtimes) == {again.main_account()["id"]}
    finally:
        again.db.close()


# ---------------------------------------------------------------------------
# 6. API
# ---------------------------------------------------------------------------
def test_portfolio_api(tmp_path):
    ids = legacy_install(tmp_path)
    app = make_app(tmp_path)
    try:
        with TestClient(create_api(app)) as c:
            assert c.post("/api/auth/setup", json={"password": "faux-mot-de-passe-de-test"}, headers=H).status_code == 200
            st = c.get("/api/portfolio").json()
            assert st["mode"] == "legacy" and st["migration_available"] is True and st["fx"]["rates"]["USDC"]["source"]
            assert c.post("/api/portfolio/activate", json={}, headers=H).status_code == 400  # sans confirmation
            r = c.post("/api/portfolio/activate", json={"confirm": True}, headers=H)
            assert r.status_code == 200 and r.json()["status"]["mode"] == "main"
            main = r.json()["main_account_id"]
            book = c.get(f"/api/accounts/{main}/ledger").json()
            assert book["currency"] == "EUR" and book["check"]["ok"] and book["rows"][0]["label"].startswith("Dépôt")
            prev = c.post("/api/orders/preview", json={"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 8}, headers=H).json()
            assert prev["currency"] == "EUR" and prev["fx"]["source"] and prev["error"] is None
            assert c.post("/api/orders", json={"account_id": main, "symbol": "BTC/USDC", "side": "buy", "cash_amount": 8}, headers=H).status_code == 200
            assert c.post("/api/orders", json={"account_id": ids["manual"], "symbol": "BTC/USDC", "side": "buy", "quote_amount": 50}, headers=H).status_code == 400
            assert c.post("/api/accounts", json={"name": "autre", "initial_balance": 100}, headers=H).status_code == 400
            ov = c.get("/api/overview").json()
            mine = next(a for a in ov["accounts"] if a["id"] == main)
            assert mine["quote"] == "EUR" and mine["available"] < 30 and mine["invested"] > 0 and mine["fx"]["rate"] > 0
    finally:
        app.db.close()
