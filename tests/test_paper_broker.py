import pytest

from sentinelle.execution.base import OrderError


def test_market_buy_applies_slippage_and_fees(paper):
    o = paper.place_order("BTC/USDC", "buy", "market", 0.1)
    assert o["status"] == "filled"
    fill_px = 50_000 * 1.001  # 10 points de base de glissement
    assert o["avg_price"] == pytest.approx(fill_px)
    cost = 0.1 * fill_px
    fee = cost * 0.001
    bal = paper.balances()
    assert bal["BTC"] == pytest.approx(0.1)
    assert bal["USDC"] == pytest.approx(10_000 - cost - fee)


def test_market_sell_and_round_trip_loses_fees(paper, prices):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    paper.place_order("BTC/USDC", "sell", "market", 0.1)
    bal = paper.balances()
    assert "BTC" not in bal
    # aller-retour au même prix : on perd frais + glissement, jamais l'inverse
    assert bal["USDC"] < 10_000
    assert bal["USDC"] == pytest.approx(10_000 - 0.1 * 50_000 * (0.002 + 0.002), rel=1e-3)


def test_insufficient_funds_rejected(paper):
    with pytest.raises(OrderError) as e:
        paper.place_order("BTC/USDC", "buy", "market", 1.0)
    assert e.value.code == "insufficient_funds"
    assert paper.balances()["USDC"] == 10_000


def test_min_notional_and_amount_step(paper):
    with pytest.raises(OrderError) as e:
        paper.place_order("BTC/USDC", "buy", "market", 0.00005)  # 2,50 USDC < 5 minimum
    assert e.value.code == "min_size"
    o = paper.place_order("BTC/USDC", "buy", "market", 0.0123456789)
    assert o["amount"] == pytest.approx(0.01234)  # arrondi vers le bas au pas de 0.00001


def test_sell_more_than_owned_rejected(paper):
    paper.place_order("ETH/USDC", "buy", "market", 1.0)
    with pytest.raises(OrderError):
        paper.place_order("ETH/USDC", "sell", "market", 1.5)


def test_limit_buy_reserves_funds_and_fills_when_touched(paper, clock):
    o = paper.place_order("BTC/USDC", "buy", "limit", 0.1, price=49_000)
    assert o["status"] == "open"
    assert paper.available("USDC") == pytest.approx(10_000 - 0.1 * 49_000 * (1 + 0.001 + 0.001))
    clock.advance(10)
    # bougie qui ne touche pas le prix limite
    assert paper.process_bar("BTC/USDC", 50_000, 50_100, 49_500, 49_800, int(clock() * 1000)) == 0
    clock.advance(10)
    assert paper.process_bar("BTC/USDC", 49_800, 49_900, 48_900, 49_100, int(clock() * 1000)) == 1
    o = paper.get_order(o["id"])
    assert o["status"] == "filled" and o["avg_price"] == 49_000


def test_stop_does_not_trigger_on_bar_before_creation(paper, clock):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    start = int(clock() * 1000) - 5_000  # micro-bougie commencée avant la création de l'ordre
    paper.place_order("BTC/USDC", "sell", "stop", 0.1, stop_price=49_000)
    assert paper.process_bar("BTC/USDC", 50_000, 50_000, 48_000, 49_500, start) == 0


def test_stop_gap_fills_at_worse_open(paper, clock):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    s = paper.place_order("BTC/USDC", "sell", "stop", 0.1, stop_price=49_000)
    clock.advance(60)
    # ouverture déjà sous le stop (trou de cotation) : exécution au prix d'ouverture
    paper.process_bar("BTC/USDC", 48_000, 48_500, 47_500, 48_200, int(clock() * 1000))
    s = paper.get_order(s["id"])
    assert s["status"] == "filled"
    assert s["avg_price"] == pytest.approx(48_000 * (1 - 0.001))


def test_stop_that_would_trigger_immediately_is_rejected(paper):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    with pytest.raises(OrderError) as e:
        paper.place_order("BTC/USDC", "sell", "stop", 0.1, stop_price=51_000)
    assert e.value.code == "would_trigger"


def test_oco_reserves_once_and_cancels_sibling(paper, clock):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    s = paper.place_order("BTC/USDC", "sell", "stop", 0.1, stop_price=49_000, oco_group="g1")
    t = paper.place_order("BTC/USDC", "sell", "limit", 0.1, price=52_000, oco_group="g1")
    assert paper.available("BTC") == pytest.approx(0.0)
    clock.advance(60)
    paper.process_bar("BTC/USDC", 50_000, 52_500, 49_900, 52_100, int(clock() * 1000))
    assert paper.get_order(t["id"])["status"] == "filled"
    assert paper.get_order(s["id"])["status"] == "cancelled"
    assert "BTC" not in paper.balances()


def test_stop_and_target_same_bar_assumes_stop_first(paper, clock):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    s = paper.place_order("BTC/USDC", "sell", "stop", 0.1, stop_price=49_000, oco_group="g")
    t = paper.place_order("BTC/USDC", "sell", "limit", 0.1, price=52_000, oco_group="g")
    clock.advance(60)
    paper.process_bar("BTC/USDC", 50_000, 53_000, 48_000, 50_000, int(clock() * 1000))
    assert paper.get_order(s["id"])["status"] == "filled"
    assert paper.get_order(t["id"])["status"] == "cancelled"


def test_equity_marks_to_market(paper, prices):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    eq0, cash = paper.equity()
    prices["BTC/USDC"] = 55_000.0
    eq1, _ = paper.equity()
    assert eq1 - eq0 == pytest.approx(0.1 * 5_000)
    assert cash < 10_000


def test_reset_restores_balance(paper):
    paper.place_order("BTC/USDC", "buy", "market", 0.1)
    paper.place_order("BTC/USDC", "sell", "stop", 0.05, stop_price=40_000)
    paper.reset(5_000)
    assert paper.balances() == {"USDC": 5_000}
    assert paper.open_orders() == []
