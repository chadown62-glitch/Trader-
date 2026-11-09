import pytest

from sentinelle.execution.base import OrderError


def test_open_position_places_oco_protections(pm, paper):
    pos = pm.open("BTC/USDC", 0.1, origin="manual", stop=48_000, target=55_000)
    assert pos["status"] == "open"
    assert pos["qty"] == pytest.approx(0.1)
    prot = pm.protective_orders(pos["id"])
    assert sorted(o["purpose"] for o in prot) == ["stop", "target"]
    assert all(o["oco_group"] == f"pos{pos['id']}" for o in prot)
    assert pos["risk_amount"] == pytest.approx((pos["entry_price"] - 48_000) * 0.1)


def test_stop_hit_closes_position_with_negative_r(pm, paper, clock):
    pos = pm.open("BTC/USDC", 0.1, origin="ai", agent="ia", stop=48_000, target=55_000)
    clock.advance(60)
    paper.process_bar("BTC/USDC", 50_000, 50_100, 47_900, 48_100, int(clock() * 1000))
    p = pm.get(pos["id"])
    assert p["status"] == "closed"
    assert p["reason_close"] == "Stop-loss touché"
    # perte un peu supérieure à 1R à cause des frais et du glissement
    assert -1.2 < p["pnl_r"] < -1.0
    assert pm.protective_orders(pos["id"]) == []


def test_target_hit_gives_positive_pnl_net_of_fees(pm, paper, clock):
    pos = pm.open("BTC/USDC", 0.1, origin="ai", stop=48_000, target=54_000)
    clock.advance(60)
    paper.process_bar("BTC/USDC", 50_000, 54_100, 49_900, 54_000, int(clock() * 1000))
    p = pm.get(pos["id"])
    assert p["status"] == "closed" and p["reason_close"] == "Objectif atteint"
    gross = (54_000 - p["entry_price"]) * 0.1
    assert p["realized_pnl"] == pytest.approx(gross - p["fees"], rel=1e-6)
    assert p["realized_pnl"] > 0


def test_close_position_at_market(pm, paper, prices):
    pos = pm.open("ETH/USDC", 1.0, origin="manual", stop=2_300, target=3_000)
    prices["ETH/USDC"] = 2_600.0
    p = pm.close(pos["id"], "Clôture manuelle")
    assert p["status"] == "closed"
    assert p["reason_close"] == "Clôture manuelle"
    assert p["realized_pnl"] > 0
    assert paper.open_orders() == []
    assert "ETH" not in paper.balances()


def test_modify_stop_replaces_order(pm, paper):
    pos = pm.open("BTC/USDC", 0.1, origin="manual", stop=48_000, target=55_000)
    pm.modify(pos["id"], stop=49_000)
    prot = {o["purpose"]: o for o in pm.protective_orders(pos["id"])}
    assert prot["stop"]["stop_price"] == 49_000
    assert prot["target"]["price"] == 55_000
    with pytest.raises(OrderError):
        pm.modify(pos["id"], stop=60_000)


def test_invalid_stop_above_price_rejected(pm):
    with pytest.raises(OrderError):
        pm.open("BTC/USDC", 0.1, origin="manual", stop=51_000)


def test_manual_raw_sell_reduces_manual_position(pm, paper, prices):
    pos = pm.open("ETH/USDC", 2.0, origin="manual")  # sans protections
    paper.place_order("ETH/USDC", "sell", "market", 0.5)
    p = pm.get(pos["id"])
    assert p["status"] == "open" and p["qty"] == pytest.approx(1.5)
    paper.place_order("ETH/USDC", "sell", "market", 1.5)
    assert pm.get(pos["id"])["status"] == "closed"


def test_failed_entry_marks_position_cancelled(pm, db):
    with pytest.raises(OrderError):
        pm.open("BTC/USDC", 5.0, origin="manual")  # fonds insuffisants
    row = db.one("SELECT * FROM positions ORDER BY id DESC LIMIT 1")
    assert row["status"] == "cancelled"
