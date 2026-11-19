"""DIRECT MARKET HUB — les 20 scénarios de résistance obligatoires de la mission 51 (aucun accès réseau)."""
import json
import logging
import threading
from types import MappingProxyType

import pytest

from sentinelle.dmh import guard
from sentinelle.dmh.cex import Binance, Trade
from sentinelle.dmh.dex import jupiter_quote
from sentinelle.dmh.ledger import Blocked, Ledger, LedgerError, StandaloneCash
from sentinelle.dmh.llm import validate_draft
from sentinelle.dmh.net import MalformedResponse, PublicClient
from sentinelle.dmh.observe import Book, InvalidData
from sentinelle.dmh.router import Route, RouteError, build_routes, choose, Intent
from sentinelle.dmh.security import (ABSOLUTE, DEFAULT_LIMITS, SecurityRefusal, Verdict, assess_token, check_route, effective,
                                     validate_limits)
from sentinelle.dmh.service import HubError
from sentinelle.dmh.simulate import PaperExecutor
from sentinelle.dmh.tokens import TokenError, TokenId, TokenInfo, TokenRegistry
from sentinelle.safety import RealTradingForbidden
from sentinelle.storage.db import Database

from tests import dmh_support as S

logging.disable(logging.CRITICAL)
PX = {"USDC": 0.92, "SOL": 70.0, "ETH": 1650.0, "BONK": 0.000012}
DEC = {S.SOL.key: 9, S.USDC_S.key: 6, S.BONK.key: 5, S.USDC_B.key: 6, S.WETH_B.key: 18}
ASSET = {S.SOL.key: "SOL", S.USDC_S.key: "USDC", S.BONK.key: "BONK", S.USDC_B.key: "USDC", S.WETH_B.key: "ETH"}


def fair(t_in, t_out, amount):
    return amount / 10 ** DEC[t_in.key] * PX[ASSET[t_in.key]] / PX[ASSET[t_out.key]] * 10 ** DEC[t_out.key] * 0.9995


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "hub.db")
    yield d
    d.close()


@pytest.fixture
def view():
    v = S.market()
    v.set_quote("solana", fair)
    v.set_quote("base", fair)
    return v


@pytest.fixture
def svc(db, view):
    return S.service(db, view)


def go(svc, body, rid):
    pv = svc.preview(body)
    return pv, svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": rid, "confirm": True})


def seed(svc, asset, loc, qty, eur, rid):
    """Position de départ cohérente (opération réglée), par exemple des USDC déjà sur la chaîne."""
    led = svc.ledger
    led.open_operation(rid, origin="test", side="buy", asset=asset, intent={}, route={}, verdict={}, config_hash="t",
                       evidence_mode="test", reserve=("EUR", "cash", eur))
    led.settle(rid, "FILLED", [{"leg": 0, "venue": "test", "side": "buy", "asset": asset, "location": loc, "qty": qty,
                               "counter_asset": "EUR", "counter_location": "cash", "counter_qty": eur, "eur": eur}], {})


# 1 -------------------------------------------------------------------------------------------------------------------
def test_01_an_ai_asking_for_a_real_order_is_refused(svc):
    with pytest.raises(RealTradingForbidden):
        svc.preview(S.buy() | {"mode": "live"}, origin="ai")
    with pytest.raises(RealTradingForbidden):
        svc.execute({"mode": "real", "preview_id": "x", "request_id": "abcdefgh", "confirm": True}, origin="ai")
    for bad in ({"mode": "live"}, {"execute": True}, {"order": {"side": "buy"}}, {"confirm": True}):
        with pytest.raises(SecurityRefusal):
            validate_draft({"asset": "BTC", "side": "buy", "capital_eur": 5, "objective_bps": 50, "horizon": "1j",
                            "entry_conditions": "x", "exit_conditions": "y", "explanation": "z", **bad}, assets={"BTC"}, max_capital=10)


# 2 (voir aussi test_dmh_api.py : contournement par l'API HTTP) --------------------------------------------------------
@pytest.mark.parametrize("body", [{"mode": "paper", "live": True}, {"mode": "paper", "broadcast": True}, {"mode": "PAPER"},
                                  {"mode": "paper", "nested": {"signed_transaction": "0x"}}, {"side": "buy"}, ["mode", "paper"]])
def test_02_paper_lock_cannot_be_bypassed_by_request_shape(body):
    with pytest.raises(RealTradingForbidden):
        guard.inspect_request(body, "test")


# 3 -------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cfg", [
    {"wallet": {"key": "0x" + "4f" * 32}},
    {"note": "legal winner thank year wave sausage worth useful legal winner thank yellow"},
    {"solana": list(range(64))},
    {"pem": "-----BEGIN EC PRIVATE KEY-----\nMHQC..."},
    {"private_key": "x"},
    {"exchange": {"api_secret": "y"}},
])
def test_03_a_private_key_in_a_configuration_is_refused(cfg):
    assert guard.scan_secrets(cfg)
    with pytest.raises(RealTradingForbidden) as e:
        guard.assert_no_secrets(cfg)
    assert "0x" + "4f" * 32 not in str(e.value) and "sausage" not in str(e.value)  # le secret n'est jamais recopié


def test_03b_hashes_are_not_mistaken_for_keys():
    assert guard.scan_secrets({"sha256": "ab" * 32, "book_sha256": "cd" * 32, "tx_id": "ef" * 32}) == []


# 4 -------------------------------------------------------------------------------------------------------------------
def test_04_several_agents_cannot_commit_the_same_30_eur(db):
    led = Ledger(db, 7)
    led.cash = StandaloneCash(led)
    ok, ko = [], []
    barrier = threading.Barrier(12)

    def agent(i):
        barrier.wait()
        try:
            led.open_operation(f"agent-{i:04d}", origin="agent", side="buy", asset="BTC", intent={}, route={}, verdict={},
                               config_hash="h", evidence_mode="test", reserve=("EUR", "cash", 10.0))
            ok.append(i)
        except LedgerError:
            ko.append(i)
    ts = [threading.Thread(target=agent, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 3 and len(ko) == 9
    assert led.reserved("EUR", "cash") == pytest.approx(30.0) and led.cash.available() == pytest.approx(0.0)


# 5 -------------------------------------------------------------------------------------------------------------------
def test_05_a_stale_quote_is_refused(svc, view):
    view.set_book("binance", "BTC/EUR", 60_000.0, age_ms=10_000, min_notional=5.0)
    del view.books[("kraken", "BTC/EUR")]
    pv = svc.preview(S.buy())
    assert not pv["verdict"]["allowed"] and any(v["code"] == "perime" for v in pv["verdict"]["vetoes"])
    op = svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": "stale-0001", "confirm": True})
    assert op["state"] == "REJECTED" and svc.ledger.cash.total() == pytest.approx(30.0)


# 6 -------------------------------------------------------------------------------------------------------------------
def test_06_a_token_imitating_another_is_never_confused():
    fake = TokenInfo(S.FAKE_USDC_B, "USDС", 6, "USDC", "UNVERIFIED")  # « С » cyrillique
    reg = S.registry([fake])
    assert reg.resolve("base", "USDC").id == S.USDC_B
    with pytest.raises(TokenError):
        reg.resolve("base", "USDС")
    assert reg.impersonation(S.FAKE_USDC_B, "USDС").id == S.USDC_B
    with pytest.raises(TokenError):
        reg.require(S.FAKE_USDC_B)
    with pytest.raises(TokenError):  # deux tokens VÉRIFIÉS indiscernables sur un même réseau : registre refusé
        TokenRegistry([TokenInfo(S.USDC_B, "USDC", 6, "USDC", "VERIFIED"), TokenInfo(S.FAKE_USDC_B, "U S D C", 6, "USDC", "VERIFIED")])


# 7 -------------------------------------------------------------------------------------------------------------------
def test_07_a_price_move_before_execution_triggers_a_new_check(svc, view):
    pv = svc.preview(S.buy())
    assert pv["verdict"]["allowed"]
    view.set_book("binance", "BTC/EUR", 60_600.0, min_notional=5.0)  # +100 pb entre la prévisualisation et la confirmation
    op = svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": "moved-0001", "confirm": True})
    assert op["state"] == "REJECTED" and "bougé" in json.dumps(op["result"], ensure_ascii=False)
    assert svc.ledger.holdings() == [] and svc.ledger.reserved() == 0


# 8 -------------------------------------------------------------------------------------------------------------------
def test_08_gas_above_budget_is_refused(svc, view):
    from sentinelle.dmh.dex import EvmGas
    seed(svc, S.USDC_B.key, "chain:base", 20.0, 18.4, "seed-usdc-b")
    seed(svc, "native:base", "chain:base", 0.001, 1.65, "seed-gas-b")
    view.gasp["base"] = EvmGas("base", 400 * 10**9, 50 * 10**9, 100 * 10**9, 10**12, 2 * 10**12, 1, view.t, "c" * 64)  # 400 gwei
    r = [x for x in build_routes(view, svc.registry, Intent("ETH", 8.0, 200.0, ("base",), False), ledger=svc.ledger)
         if x.kind == "dex_onchain"][0]
    v = Verdict()
    check_route(v, effective(dict(DEFAULT_LIMITS)), r.to_dict(200.0), view.t, svc.registry, {})
    assert any(x.code == "gaz" for x in v.reasons if x.veto)


# 9 -------------------------------------------------------------------------------------------------------------------
def test_09_an_excessive_spread_is_refused(svc, view):
    del view.books[("kraken", "BTC/EUR")]
    view.set_book("binance", "BTC/EUR", 60_000.0, hs_bps=30.0, min_notional=5.0)  # écart complet 60 pb > 50 pb
    pv = svc.preview(S.buy())
    assert any(v["code"] == "ecart" for v in pv["verdict"]["vetoes"])


# 10 ------------------------------------------------------------------------------------------------------------------
def test_10_vanishing_liquidity_is_refused_or_partially_filled_realistically(svc, view, db):
    pv = svc.preview(S.buy())
    view.set_book("binance", "BTC/EUR", 60_000.0, qty=0.00001, levels=3, min_notional=1.0)  # ~1,8 € de profondeur
    op = svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": "thin-00001", "confirm": True})
    assert op["state"] == "REJECTED" and svc.ledger.cash.total() == pytest.approx(30.0)
    # exécution partielle réaliste : seule la profondeur sous le prix de protection est prise, le reste n'est pas exécuté
    ex = PaperExecutor(view, svc.ledger, limits=svc.limits())
    leg = {"kind": "cex", "step": "achat", "venue": "binance", "asset_in": "EUR", "loc_in": "cash", "qty_in": 8.0, "asset_out": "BTC",
           "loc_out": "cex:binance", "data": {"venue": "binance", "pair": "BTC/EUR", "side": "buy", "fee": 0.001}}
    res = ex._cex_market(leg, {}, 0)
    assert res["status"] == "PARTIAL" and res["fills"][0]["counter_qty"] < 2.0


# 11 ------------------------------------------------------------------------------------------------------------------
def test_11_network_failure_during_an_operation_resumes_idempotently(db, view):
    svc = S.service(db, view)
    pv = svc.preview(S.buy())
    view.down.add("binance")
    op = svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": "down-00001", "confirm": True})
    assert op["state"] == "REJECTED" and svc.ledger.reserved() == 0 and svc.ledger.cash.total() == pytest.approx(30.0)
    view.down.clear()
    # panne APRÈS l'enregistrement de la transaction simulée sur la chaîne, avant le règlement
    del view.books[("kraken", "SOL/EUR")], view.books[("coinbase", "SOL/EUR")]
    view.set_book("kraken", "SOL/EUR", 70.0, taker=300.0, maker=300.0, min_notional=0.45)  # plateforme hors de prix : route sur la chaîne
    seed(svc, S.USDC_S.key, "chain:solana", 20.0, 18.4, "seed-usdc-s")
    seed(svc, "native:solana", "chain:solana", 0.02, 1.4, "seed-gas-s")
    svc.set_limits({"max_immobilized_pct": 2.0, "max_position_eur": 15.0}, "user")  # choix explicites de l'utilisateur (sous les plafonds)
    pv = svc.preview({"mode": "paper", "side": "buy", "asset": "SOL", "notional_eur": 10.0, "edge_bps": 200, "networks": ["solana"]})
    assert pv["route"]["kind"] == "dex_onchain" and pv["verdict"]["allowed"], pv["explanation"]
    real_settle = svc.ledger.settle
    svc.ledger.settle = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("coupure au moment du règlement"))
    with pytest.raises(ConnectionError):
        svc.execute({"mode": "paper", "preview_id": pv["id"], "request_id": "dex-crash-01", "confirm": True})
    svc.ledger.settle = real_settle
    assert svc.ledger.operation("dex-crash-01")["state"] == "SUBMITTED"
    restarted = S.service(db, view)  # redémarrage : reprise d'après la trace, sans nouvel essai
    assert restarted.recovered["settled_from_trace"] == 1
    assert S.service(db, view).recovered == {"abandoned": 0, "settled_from_trace": 0, "expired": 0}
    fills = restarted.ledger.fills("dex-crash-01")
    assert len([f for f in fills if f["side"] == "buy"]) == 1 and restarted.ledger.reconcile()["ok"]
    replay = restarted.execute({"mode": "paper", "preview_id": pv["id"], "request_id": "dex-crash-01", "confirm": True})
    assert replay.get("replayed") and len(restarted.ledger.fills("dex-crash-01")) == len(fills)


# 12 ------------------------------------------------------------------------------------------------------------------
def test_12_an_inconsistent_server_answer_is_refused():
    with pytest.raises(InvalidData):
        Book.build("x", "BTC/EUR", [(101, 1)], [(100, 1)], received_ms=S.T0)  # carnet croisé
    with pytest.raises(InvalidData):
        Book.build("x", "BTC/EUR", [(100, 1), (101, 1)], [(102, 1)], received_ms=S.T0)  # côté non trié
    with pytest.raises(InvalidData):
        Book.build("x", "BTC/EUR", [(100, 1)], [(102, 1)], received_ms=S.T0, source_ms=S.T0 + 60_000)  # heure future
    replies = iter([b'{"lastUpdateId":1,"bids":[["101","1"]],"asks":[["100","1"]]}'])
    client = PublicClient(transport=lambda *a: (200, {}, next(replies)), sleep=lambda s: None)
    with pytest.raises(MalformedResponse):
        Binance(client).book("BTC/EUR")
    bad = json.dumps({"inputMint": S.USDC_S.address, "outputMint": S.SOL.address, "inAmount": "999", "outAmount": "5",
                      "routePlan": [{"swapInfo": {"label": "Whirlpool"}}]}).encode()
    client = PublicClient(transport=lambda *a: (200, {}, bad), sleep=lambda s: None)
    with pytest.raises(MalformedResponse):
        jupiter_quote(client, S.USDC_S, S.SOL, 1000)
    signed = json.dumps({"transaction": "AQAB...", "inputMint": S.USDC_S.address}).encode()
    client = PublicClient(transport=lambda *a: (200, {}, signed), sleep=lambda s: None)
    with pytest.raises(RealTradingForbidden):
        jupiter_quote(client, S.USDC_S, S.SOL, 1000)


# 13 ------------------------------------------------------------------------------------------------------------------
def test_13_a_desynchronised_portfolio_blocks_until_reconciled(svc, db):
    _, op = go(svc, S.buy(), "sync-00001")
    assert op["state"] == "FILLED"
    db.execute("UPDATE dmh_holdings SET qty = qty * 2")
    rep = svc.ledger.reconcile()
    assert not rep["ok"] and svc.ledger.state("desync")
    with pytest.raises(Blocked):
        svc.preview(S.buy())
    db.execute("UPDATE dmh_holdings SET qty = qty / 2")
    assert svc.ledger.reconcile()["ok"] and svc.ledger.state("desync") is None
    assert svc.preview(S.buy(n=10.0))["decision"] == "ROUTE"


# 14 ------------------------------------------------------------------------------------------------------------------
def test_14_an_unsellable_token_blocks_entries_and_is_reported(db, view):
    rep = assess_token(TokenInfo(S.BONK, "BONK", 5, "BONK", "VERIFIED"), {"exists": True, "decimals": 5},
                       {"goplus": {"honeypot": True, "cannot_sell": True, "sell_tax": 0.99}})
    assert rep["status"] == "UNSAFE" and any("revendre" in f for f in rep["flags"])
    svc = S.service(db, view, risk=S.FakeRisk({S.BONK.key: "impossible à revendre (pot de miel)"}))
    view.set_book("bitvavo", "BONK/EUR", 0.000012, taker=300.0, maker=300.0, min_notional=5.0, step=1.0)
    seed(svc, S.USDC_S.key, "chain:solana", 20.0, 18.4, "seed-usdc-s")
    seed(svc, "native:solana", "chain:solana", 0.02, 1.4, "seed-gas-s")
    svc.set_limits({"max_immobilized_pct": 2.0}, "user")
    pv = svc.preview({"mode": "paper", "side": "buy", "asset": "BONK", "notional_eur": 10.0, "edge_bps": 300, "networks": ["solana"]})
    assert pv["route"]["kind"] == "dex_onchain"
    assert any(v["code"] == "token_risque" and "revendre" in v["message"] for v in pv["verdict"]["vetoes"])


# 15 ------------------------------------------------------------------------------------------------------------------
def test_15_a_quote_promising_profit_without_its_exit_is_rejected(svc, view):
    r = [x for x in build_routes(view, svc.registry, Intent("BTC", 8.0, 50.0, (), False)) if x.kind == "cex_direct"][0]
    no_exit = Route(r.id, r.label, r.kind, r.notional_eur, r.entry, None)
    with pytest.raises(RouteError):
        no_exit.totals()
    c = choose([no_exit], 50.0)
    assert c.decision == "ABSTENTION" and "sortie" in " ".join(no_exit.reasons)
    v = Verdict()
    check_route(v, effective(dict(DEFAULT_LIMITS)), {"entry": [], "exit": None}, S.T0, svc.registry)
    assert any(x.code == "sortie_absente" for x in v.reasons)


# 16 ------------------------------------------------------------------------------------------------------------------
def test_16_an_llm_cannot_change_the_absolute_risk(svc, db):
    with pytest.raises(SecurityRefusal):
        svc.set_limits({"max_position_eur": 5.0}, origin="llm")
    with pytest.raises(SecurityRefusal):
        svc.set_limits({"max_position_eur": 100.0}, origin="user")  # au-delà du plafond absolu
    with pytest.raises(SecurityRefusal):
        validate_limits({"capital_eur": 1000}, dict(DEFAULT_LIMITS), "user")
    with pytest.raises(TypeError):
        ABSOLUTE["max_position_eur"] = 1000  # type: ignore[index]
    assert isinstance(ABSOLUTE, MappingProxyType)
    db.execute("INSERT OR REPLACE INTO dmh_state(key, value, updated_ms) VALUES ('limits', ?, 0)",
               (json.dumps({"max_position_eur": 1000, "min_cash_reserve_eur": 0}),))
    lim = svc.limits()
    assert lim["max_position_eur"] == ABSOLUTE["max_position_eur"] and lim["min_cash_reserve_eur"] == ABSOLUTE["min_cash_reserve_eur"]
    with pytest.raises(SecurityRefusal):
        validate_draft({"asset": "BTC", "side": "buy", "capital_eur": 5, "objective_bps": 50, "horizon": "1j", "entry_conditions": "x",
                        "exit_conditions": "y", "explanation": "z", "limits": {"max_position_eur": 30}}, assets={"BTC"}, max_capital=10)


# 17 ------------------------------------------------------------------------------------------------------------------
def test_17_a_limit_order_touching_its_price_is_not_filled(svc, view):
    ex = PaperExecutor(view, svc.ledger, limits=svc.limits())
    op = {"result": {"limit": {"price": 59_990.0, "placed_ms": S.T0, "expires_ms": S.T0 + 300_000, "venue": "binance", "pair": "BTC/EUR",
                               "budget": 8.0, "step": 1e-8, "fee_maker": 0.001, "asset_out": "BTC", "loc_out": "cex:binance"}}}
    touch = [Trade(S.T0 + 1000 * i, 59_990.0, 1.0) for i in range(1, 50)]
    assert ex.evaluate_limit(op, touch, S.T0 + 60_000).state == "OPEN"
    assert ex.evaluate_limit(op, touch, S.T0 + 300_000).state == "EXPIRED"
    through = touch + [Trade(S.T0 + 70_000, 59_989.0, 1.0)]
    assert ex.evaluate_limit(op, through, S.T0 + 80_000).state == "FILLED"


# 18 ------------------------------------------------------------------------------------------------------------------
def test_18_borrowing_or_short_selling_is_refused(svc):
    for field in ("short", "leverage", "borrow", "margin"):
        with pytest.raises(RealTradingForbidden):
            svc.preview(S.buy() | {field: True})
    with pytest.raises(HubError):
        svc.preview({"mode": "paper", "side": "sell", "holding_asset": "BTC", "holding_location": "cex:binance", "fraction": 1})
    _, op = go(svc, S.buy(), "short-0001")
    led = svc.ledger
    led.open_operation("short-0002", origin="test", side="sell", asset="BTC", intent={}, route={}, verdict={}, config_hash="t",
                       evidence_mode="test", reserve=("BTC", "cex:binance", 1e-9))
    with pytest.raises(LedgerError):
        led.settle("short-0002", "FILLED", [{"leg": 0, "venue": "binance", "side": "sell", "asset": "BTC", "location": "cex:binance",
                                             "qty": 1.0, "counter_asset": "EUR", "counter_location": "cash", "counter_qty": 60_000, "eur": 60_000}], {})
    assert led.cash.total() < 30 and led.reconcile()["ok"]


# 19 ------------------------------------------------------------------------------------------------------------------
def test_19_same_symbol_on_two_networks_keeps_distinct_identities(svc):
    reg = svc.registry
    a, b = reg.resolve("solana", "USDC"), reg.resolve("base", "USDC")
    assert a.id != b.id and a.id.network == "solana" and b.id.network == "base"
    assert reg.by_asset("USDC", "solana").id == S.USDC_S and reg.by_asset("USDC", "base").id == S.USDC_B
    pepe_base = TokenId("base", "0xB4fDe59a779991bfB6a52253B51947828b982be3")
    assert reg.get(pepe_base) is None
    with pytest.raises(TokenError):
        reg.require(pepe_base)
    with pytest.raises(TokenError):
        TokenId("polygon", "0x" + "1" * 40)


# 20 ------------------------------------------------------------------------------------------------------------------
def test_20_a_crash_while_saving_never_duplicates_a_paper_trade(db, view):
    svc = S.service(db, view)
    led = svc.ledger
    led.open_operation("crash-0001", origin="user", side="buy", asset="BTC", intent={}, route={}, verdict={}, config_hash="t",
                       evidence_mode="test", reserve=("EUR", "cash", 8.0))
    fills = [{"leg": 0, "venue": "binance", "side": "buy", "asset": "BTC", "location": "cex:binance", "qty": 0.0001,
              "counter_asset": "EUR", "counter_location": "cash", "counter_qty": 6.0, "eur": 6.0},
             {"leg": 1, "venue": "binance", "side": "buy", "asset": "BTC", "location": "cex:binance", "qty": 0.00003,
              "counter_asset": "EUR", "counter_location": "cash", "counter_qty": 1.9, "eur": 1.9}]
    with pytest.raises(RuntimeError):
        led.settle("crash-0001", "FILLED", fills, {}, fail_after="fill:1")
    assert led.operation("crash-0001")["state"] == "RESERVED" and led.fills("crash-0001") == [] and led.holdings() == []
    restarted = S.service(db, view)
    assert restarted.recovered["abandoned"] == 1
    assert restarted.ledger.operation("crash-0001")["state"] == "ABANDONED" and restarted.ledger.cash.available() == pytest.approx(30.0)
    assert restarted.ledger.settle("crash-0001", "FILLED", fills, {}).get("replayed")  # déjà terminée : rien n'est réappliqué
    assert restarted.ledger.fills("crash-0001") == [] and restarted.ledger.reconcile()["ok"]
