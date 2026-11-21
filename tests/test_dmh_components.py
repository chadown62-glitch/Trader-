"""DIRECT MARKET HUB — composants : keccak/ABI, client public, observations, identités, routeur, registre (propriétés)."""
import hashlib
import json
import logging
import random
from pathlib import Path

import pytest

from sentinelle.dmh import abi, assumptions as A
from sentinelle.dmh.cex import epoch_ms
from sentinelle.dmh.ledger import Ledger, LedgerError, StandaloneCash
from sentinelle.dmh.llm import is_loopback_url, draft_strategy
from sentinelle.dmh.net import PublicClient, RequestNotAllowed, SourceUnavailable
from sentinelle.dmh.observe import Book, SeriesGuard, consensus, dex_vs_reference
from sentinelle.dmh.router import Intent, build_routes, choose, walk
from sentinelle.dmh.security import SecurityRefusal
from sentinelle.dmh.tokens import TokenId, b58decode, skeleton
from sentinelle.dmh.view import ReplayView, LiveView
from sentinelle.safety import RealTradingForbidden
from sentinelle.storage.db import Database

from tests import dmh_support as S

logging.disable(logging.CRITICAL)


# -- keccak et ABI ------------------------------------------------------------------------------------------------------
def test_keccak_matches_known_vectors_and_the_sha3_permutation():
    assert abi.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert abi.keccak256(b"The quick brown fox jumps over the lazy dog").hex() == \
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15"
    rng = random.Random(3)
    for n in (0, 1, 135, 136, 137, 300, 1000):
        data = bytes(rng.randrange(256) for _ in range(n))
        assert abi._sponge(data, 0x06) == hashlib.sha3_256(data).digest()
    assert abi.selector("transfer(address,uint256)") == "a9059cbb" and abi.selector("decimals()") == "313ce567"


def test_abi_encoding_and_decoding_are_strict():
    call = abi.encode_call("balanceOf(address)", [("address", "0x" + "ab" * 20)])
    assert call == "0x70a08231" + "0" * 24 + "ab" * 20
    with pytest.raises(ValueError):
        abi.encode_call("x(address)", [("address", "0x12")])
    with pytest.raises(ValueError):
        abi.decode_words("0x" + "00" * 31, 1)
    s = "0x" + f"{32:064x}" + f"{4:064x}" + b"USDC".hex().ljust(64, "0")
    assert abi.decode_string(s) == "USDC"


# -- client public -------------------------------------------------------------------------------------------------------
def test_public_client_allowlist_rate_limit_breaker_and_budget():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        assert "Authorization" not in headers and "X-Api-Key" not in headers
        return 500, {}, b""
    t = {"now": 0.0}
    c = PublicClient(transport=transport, monotonic=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s), retries=2,
                     breaker_after=3, budget=10, seed=1)
    with pytest.raises(SourceUnavailable):
        c.get("api.kraken.com", "/0/public/Time")
    assert len(calls) == 3  # deux reprises bornées
    with pytest.raises(SourceUnavailable) as e:
        c.get("api.kraken.com", "/0/public/Time")
    assert "coupe-circuit" in str(e.value) and len(calls) == 3  # aucun appel tant que le circuit est ouvert
    with pytest.raises(RequestNotAllowed):
        c.get("evil.example.com", "/")
    with pytest.raises(RealTradingForbidden):
        c.rpc("mainnet.base.org", "eth_sendRawTransaction", ["0x"])
    with pytest.raises(RealTradingForbidden):
        c.rpc("mainnet.base.org", "eth_call", [{"to": "0x" + "1" * 40, "data": "0x", "from": "0x" + "2" * 40}, "latest"])
    with pytest.raises(RequestNotAllowed):
        c.rpc("mainnet.base.org", "debug_traceTransaction", [])


def test_public_client_honours_retry_after_and_counts_the_budget():
    replies = iter([(429, {"retry-after": "3"}, b""), (200, {}, b'{"ok": 1}')])
    t = {"now": 0.0}
    c = PublicClient(transport=lambda *a: next(replies), monotonic=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s),
                     budget=5)
    f = c.get("api.coingecko.com", "/api/v3/coins/usd-coin")
    assert f.json() == {"ok": 1} and t["now"] >= 3 and c.used == 2
    c2 = PublicClient(transport=lambda *a: (200, {}, b"{}"), budget=1)
    c2.get("api.kraken.com", "/0/public/Time")
    with pytest.raises(SourceUnavailable):
        c2.get("api.kraken.com", "/0/public/Time")


def test_public_client_budget_renews_over_a_sliding_window():
    t = {"now": 0.0}
    c = PublicClient(transport=lambda *a: (200, {}, b"{}"), monotonic=lambda: t["now"], sleep=lambda s: t.__setitem__("now", t["now"] + s),
                     budget=3, budget_window_s=60)
    for _ in range(3):
        c.get("api.kraken.com", "/0/public/Time")
    with pytest.raises(SourceUnavailable) as e:
        c.get("api.kraken.com", "/0/public/Time")
    assert "fenêtre" in str(e.value) and c.used == 3  # a refused request is not sent and not counted
    first = t["now"]
    t["now"] = 61.0  # every request of the window has aged out: the budget is renewed, still capped
    for _ in range(3):
        c.get("api.kraken.com", "/0/public/Time")
    assert c.used == 6 and c.health()["budget_window"] == {"seconds": 60, "used": 3} and first < 60
    with pytest.raises(SourceUnavailable):
        c.get("api.kraken.com", "/0/public/Time")
    for bad in (0, -5, "60"):
        with pytest.raises(ValueError):
            PublicClient(budget_window_s=bad)
    lifetime = PublicClient(transport=lambda *a: (200, {}, b"{}"), budget=1)  # unchanged default: a lifetime cap
    lifetime.get("api.kraken.com", "/0/public/Time")
    with pytest.raises(SourceUnavailable):
        lifetime.get("api.kraken.com", "/0/public/Time")
    assert lifetime.health()["budget_window"] is None


def test_the_hub_client_uses_a_renewable_bounded_budget():
    from sentinelle.dmh import service
    assert (service.HUB_BUDGET, service.HUB_BUDGET_WINDOW_S) == (500, 300.0)
    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "PublicClient(budget=HUB_BUDGET, budget_window_s=HUB_BUDGET_WINDOW_S)" in source


def test_unit_conversion_of_source_timestamps():
    assert epoch_ms(1794849028261810159) == 1794849028261  # ns (Bitvavo)
    assert epoch_ms("1794848333123456") == 1794848333123  # µs (Bitstamp)
    assert epoch_ms(1794848333) == 1794848333000  # s
    with pytest.raises(ValueError):
        epoch_ms(12)


# -- observations ---------------------------------------------------------------------------------------------------------
def test_consensus_excludes_outliers_and_refuses_contradictions():
    b = [Book.build(v, "BTC/EUR", [(m - 1, 1)], [(m + 1, 1)], received_ms=S.T0) for v, m in (("a", 60_000), ("b", 60_010), ("c", 61_000))]
    c = consensus("BTC/EUR", b, S.T0)
    assert c.status == "OK" and c.outliers == ("c",) and set(c.usable) == {"a", "b"}
    c2 = consensus("BTC/EUR", b[::2], S.T0)
    assert c2.status == "INCOHERENT" and c2.usable == ()
    assert consensus("BTC/EUR", b, S.T0 + 60_000).status == "INDISPONIBLE"  # rien de frais : rien d'inventé
    ok, dev = dex_vs_reference(1.03, 1.0)
    assert not ok and dev == pytest.approx(300)


def test_series_guard_flags_duplicates_rewinds_and_gaps():
    g = SeriesGuard(expected_every_ms=1000)
    assert g.check(("x",), 1000, "a") == "ok"
    assert g.check(("x",), 1000, "a") == "doublon"
    assert g.check(("x",), 500, "b") == "retour_en_arriere"
    assert g.check(("x",), 10_000, "c") == "trou"


# -- identités ---------------------------------------------------------------------------------------------------------------
def test_token_identity_normalisation_and_lookalikes():
    assert TokenId("base", "0x833589FCD6EDB6E08F4C7C32D4F71B54BDA02913") == S.USDC_B
    assert len(b58decode(S.SOL.address)) == 32
    with pytest.raises(ValueError):
        TokenId("solana", "0OIl" * 9)
    assert skeleton("USDС") == skeleton("usdc") == skeleton("U S D C") == skeleton("USDC.e".replace(".e", ""))
    assert skeleton("S0L") == skeleton("SOL")


def test_real_registry_file_is_intact_and_conservative():
    from sentinelle.dmh.tokens import load_registry
    reg = load_registry()
    assert reg.meta.get("sha256") and len(reg) >= 10
    usdt_sol = reg.get(TokenId("solana", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"))
    assert usdt_sol is not None and usdt_sol.status == "UNVERIFIED"  # non confirmé par deux sources : jamais utilisé
    for t in reg.all():
        if t.verified:
            assert sum(1 for s in t.sources if s.get("source") != "lecture sur la chaîne" and not s.get("error")) >= 2


# -- routeur ------------------------------------------------------------------------------------------------------------------
def test_book_walk_properties_on_random_books():
    rng = random.Random(7)
    for _ in range(200):
        mid = rng.uniform(0.001, 100_000)
        levels = rng.randint(1, 30)
        asks = [(mid * (1 + 1e-4 * (i + 1)), rng.uniform(0.0001, 5)) for i in range(levels)]
        bids = [(mid * (1 - 1e-4 * (i + 1)), rng.uniform(0.0001, 5)) for i in range(levels)]
        book = Book.build("x", "A/B", bids, asks, received_ms=S.T0)
        q = rng.uniform(0, sum(x for _, x in asks) * 1.2)
        got, spent, avg, complete = walk(book, "buy", qty=q)
        assert got <= q + 1e-12 and spent >= got * book.ask - 1e-9 and complete == (got >= q - 1e-12)
        got2, spent2, _, _ = walk(book, "sell", qty=q)
        assert spent2 <= got2 * book.bid + 1e-9


def test_router_never_assumes_a_dex_is_cheaper_and_abstains_without_edge():
    v = S.market()
    v.set_quote("solana", lambda a, b, amt: amt)
    routes = build_routes(v, S.registry(), Intent("SOL", 8.0, None, ("solana",), True))
    funded = [r for r in routes if r.kind == "dex_funded"]
    assert funded and all(r.reasons or r.totals()["cost_bps"] > 300 for r in funded)  # retraits et gaz : absurde à 8 €
    c = choose(routes, None)
    assert c.decision == "ABSTENTION" and "aucun avantage" in " ".join(c.reasons)
    c2 = choose(routes, 300.0)
    assert c2.decision == "ROUTE" and c2.route.kind != "cex_limit"  # l'ordre passif n'est jamais retenu d'office


def test_every_eligible_route_carries_its_exit_and_bounds():
    v = S.market()
    for r in build_routes(v, S.registry(), Intent("BTC", 8.0, 100.0, (), True)):
        if r.reasons:
            continue
        t = r.totals()
        assert r.exit and t["cost_low_eur"] <= t["cost_eur"] <= t["cost_high_eur"]
        assert {c.level for leg in r.legs for c in leg.costs} <= {"OBSERVED", "ESTIMATED", "DECLARED", "HYPOTHETICAL"}


# -- registre : propriétés sur des séquences aléatoires -----------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(8))
def test_ledger_invariants_hold_under_random_operation_sequences(tmp_path, seed):
    rng = random.Random(seed)
    db = Database(tmp_path / f"p{seed}.db")
    led = Ledger(db, 1)
    led.cash = StandaloneCash(led)
    held = 0.0
    for i in range(60):
        rid = f"op-{seed}-{i:04d}"
        if held > 0 and rng.random() < 0.4:
            q = held * rng.uniform(0.1, 1.2)  # parfois plus que détenu : doit être refusé
            try:
                led.open_operation(rid, origin="p", side="sell", asset="BTC", intent={}, route={}, verdict={}, config_hash="h",
                                   evidence_mode="test", reserve=("BTC", "cex:x", q))
                led.settle(rid, "FILLED", [{"leg": 0, "venue": "x", "side": "sell", "asset": "BTC", "location": "cex:x", "qty": q,
                                            "counter_asset": "EUR", "counter_location": "cash", "counter_qty": q * 60_000, "eur": q * 60_000}], {})
                held -= q
            except LedgerError:
                assert q > held - 1e-12
        else:
            eur = rng.uniform(0.5, 12)
            try:
                led.open_operation(rid, origin="p", side="buy", asset="BTC", intent={}, route={}, verdict={}, config_hash="h",
                                   evidence_mode="test", reserve=("EUR", "cash", eur))
                if rng.random() < 0.2:
                    led.mark(rid, "ABANDONED")
                    continue
                q = eur / 60_000 * 0.999
                led.settle(rid, "FILLED", [{"leg": 0, "venue": "x", "side": "buy", "asset": "BTC", "location": "cex:x", "qty": q,
                                            "counter_asset": "EUR", "counter_location": "cash", "counter_qty": eur, "eur": eur}], {})
                held += q
            except LedgerError:
                assert eur > led.cash.available() - 1e-9
        assert led.cash.total() >= 0 and led.reserved() == 0
        h = led.holding("BTC", "cex:x")
        assert abs((h["qty"] if h else 0.0) - held) < 1e-9
    rep = led.reconcile()
    assert rep["ok"], rep
    assert led.cash.total() + held * 60_000 <= 30.0 + 1e-6  # rien n'est créé : chaque achat coûte au moins ce qu'il rapporte
    db.close()


def test_journal_is_chained_and_immutable(tmp_path):
    db = Database(tmp_path / "j.db")
    led = Ledger(db, 1)
    led.cash = StandaloneCash(led)
    led.halt("test", "user")
    led.resume("user")
    assert led.journal_ok()[0]
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="journal immuable"):
        db.execute("UPDATE dmh_journal SET data = '{}' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError, match="journal immuable"):
        db.execute("DELETE FROM dmh_journal")
    db.close()


# -- hypothèses, IA locale, rejeu ------------------------------------------------------------------------------------------------
def test_assumptions_are_versioned_and_hashed():
    d1, d2 = A.digest(), A.digest()
    assert d1 == d2 and len(d1) == 64 and A.export()["VERSION"] == A.VERSION
    assert all(lo <= c <= hi for c, lo, hi, _ in A.WITHDRAWAL_EUR.values())


def test_llm_must_be_local_and_its_output_is_strictly_validated():
    assert is_loopback_url("http://127.0.0.1:11434") and is_loopback_url("http://localhost:11434") and is_loopback_url("http://[::1]:1")
    assert not is_loopback_url("https://api.example.com") and not is_loopback_url("http://192.168.1.10:11434")
    with pytest.raises(SecurityRefusal):
        draft_strategy("bonjour", url="https://ia.example.com", model="m", assets={"BTC"}, max_capital=10)
    import httpx

    def handler(request):
        content = json.dumps({"asset": "BTC", "side": "buy", "capital_eur": 5, "objective_bps": 80, "horizon": "3 jours",
                              "entry_conditions": "cassure", "exit_conditions": "objectif ou stop", "explanation": "hypothèse"})
        return httpx.Response(200, json={"message": {"content": content}})
    out = draft_strategy("test", url="http://127.0.0.1:11434", model="m", assets={"BTC"}, max_capital=10,
                         transport=httpx.MockTransport(handler))
    assert out["available"] and out["draft"]["objective_is_hypothesis"] and out["draft"]["capital_eur"] == 5
    down = draft_strategy("test", url="http://127.0.0.1:9", model="m", assets={"BTC"}, max_capital=10,
                          transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refusé"))))
    assert down["available"] is False and down["draft"] is None  # aucune IA distante de secours


def test_replay_reproduces_a_recorded_decision_exactly():
    rec: dict = {}
    v = S.market()
    live_like = LiveView.__new__(LiveView)  # enregistreur seul : on rejoue ce que le marché factice a servi
    capture = {"captured_ms": v.t, "data": {"book": {}, "rules": {}}}
    for (venue, pair), b in v.books.items():
        from sentinelle.dmh.view import _record
        capture["data"]["book"][json.dumps([venue, pair])] = _record(b)
        capture["data"]["rules"][json.dumps([venue, pair])] = _record(v.rule[(venue, pair)])
    fees = {k: x for k, x in v.fee.items()}
    r1 = build_routes(ReplayView(capture, fees=fees), S.registry(), Intent("BTC", 8.0, 100.0, (), False))
    r2 = build_routes(ReplayView(json.loads(json.dumps(capture)), fees=fees), S.registry(), Intent("BTC", 8.0, 100.0, (), False))
    t1 = [(r.id, round(r.totals()["cost_eur"], 12)) for r in r1 if not r.reasons]
    t2 = [(r.id, round(r.totals()["cost_eur"], 12)) for r in r2 if not r.reasons]
    assert t1 == t2 and t1 and live_like is not None and rec == {}


# -- adaptateur vers les contrats de la PR #47 (autre lignée, non fusionnée) ---------------------------------------------------
def test_c00_adapter_keeps_identity_and_causality_rules():
    from sentinelle.dmh import adapters
    reg = S.registry()
    bonk = adapters.asset_ref_onchain(reg.get(S.BONK))
    assert bonk["family"] == "memecoin" and bonk["network"] == "solana" and bonk["instrument_id"] == S.BONK.address
    with pytest.raises(ValueError):
        adapters.asset_ref_cex("bitvavo", "BONK")  # memecoin sans réseau ni identifiant : refusé, comme dans #47
    b = Book.build("binance", "BTC/EUR", [(59_999, 1)], [(60_001, 1)], received_ms=S.T0)
    ev = adapters.evidence_from_book(b)
    snap = adapters.snapshot(adapters.asset_ref_cex("binance", "BTC"), S.T0, [ev])
    assert snap["observations"][0]["value"] == 60_000
    with pytest.raises(ValueError):
        adapters.snapshot(adapters.asset_ref_cex("binance", "BTC"), S.T0 - 1, [ev])  # information future
