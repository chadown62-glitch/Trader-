"""Nouvelles cotations Binance : sélection des annonces, sorties causales, coûts, portefeuille à 30 € et exclusions, sur
données construites (aucun réseau)."""

import httpx
import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import cotations as C

T0 = 1_700_000_040_000 - 1_700_000_040_000 % 60_000


def _bars(closes, highs=None, lows=None, opens=None):
    n = len(closes)
    c = np.asarray(closes, float)
    o = np.asarray(opens if opens is not None else np.r_[c[0], c[:-1]], float)
    h = np.asarray(highs if highs is not None else np.maximum(o, c), float)
    lo = np.asarray(lows if lows is not None else np.minimum(o, c), float)
    return pd.DataFrame({"o": o, "h": h, "l": lo, "c": c}, index=pd.Index(T0 + np.arange(n) * C.MINUTE, name="t"))


def test_only_spot_listing_announcements_are_kept():
    assert C.listing_tokens("Binance Will List Predict (PREDICT) with Seed Tag Applied") == ["PREDICT"]
    assert C.listing_tokens("Binance Will List Hyperliquid (HYPE) with Seed Tag Applied") == ["HYPE"]
    assert C.listing_tokens("Binance Will List Foo (FOO) and Bar (BAR)") == ["FOO", "BAR"]
    assert C.listing_tokens("Binance Futures Will Launch USDⓈ-Margined PREDICTUSDT Perpetual Contract") is None
    assert C.listing_tokens("Binance Will Add Hyperliquid (HYPE) on Earn, Buy Crypto, Convert, VIP Loan & Margin") is None
    assert C.listing_tokens("Binance Will List RWUSD and Introduce RWUSD Zero Trading Fee Promotion") is None


def test_time_exit_and_costs_on_both_sides():
    bars = _bars([100.0] * 10 + [110.0] * 10)
    r = C.exit_return(bars, T0, ("temps", 15), 0.005)
    assert r == pytest.approx(110 * 0.995 / (100 * 1.005) - 1)


def test_bracket_takes_the_stop_first_when_both_levels_are_touched_and_exits_gaps_at_the_open():
    both = _bars([100, 100, 100], highs=[100, 130, 100], lows=[100, 85, 100])
    assert C.exit_return(both, T0, ("crochet", 0.25, 0.10), 0.0) == pytest.approx(-0.10)
    tp = _bars([100, 120, 130], highs=[100, 120, 126], lows=[100, 99, 120])
    assert C.exit_return(tp, T0, ("crochet", 0.25, 0.10), 0.0) == pytest.approx(0.25)
    gap = _bars([100, 70, 70], opens=[100, 75, 70], highs=[100, 75, 70], lows=[100, 70, 70])
    assert C.exit_return(gap, T0, ("crochet", 0.25, 0.10), 0.0) == pytest.approx(-0.25)  # ouverture sous le stop


def test_trailing_stop_follows_the_highest_close_only():
    bars = _bars([100, 120, 150, 140, 128, 126, 200])
    assert C.exit_return(bars, T0, ("suiveur", 0.15), 0.0) == pytest.approx(126 / 100 - 1)  # 128 > 127,5 puis 126 ≤ 150 × 0,85 ; 200 n'est jamais vu


def test_the_30_euro_portfolio_holds_one_position_at_a_time():
    rows = [{"exclu": None, "entree_ms": T0 + k * C.DAY // 2, "rendements": {"T24h": 0.10}} for k in range(4)]
    p = C.portfolio(rows, "T24h")
    assert p["transactions"] == 2 and p["capital_final"] == pytest.approx(30 * 1.1 ** 2)  # une annonce sur deux tombe pendant la détention
    assert p["drawdown_max"] == 0.0


def test_a_pair_that_already_traded_before_the_announcement_is_excluded(monkeypatch, tmp_path):
    monkeypatch.setattr(C.data, "download_day", lambda *a, **k: "archive")
    monkeypatch.setattr(C.data, "read_month", lambda path: pd.DataFrame({"t": [T0], "o": [1.0], "h": [1.0], "l": [1.0], "c": [1.0], "v": [1.0], "taker_buy": [0.0]}))
    bars, why = C.listing_bars("OLDUSDT", T0, tmp_path, client=None)
    assert bars is None and why == "paire déjà cotée avant l'annonce"

    def missing(*a, **k):
        raise httpx.HTTPStatusError("404", request=httpx.Request("GET", "https://x"), response=httpx.Response(404))
    monkeypatch.setattr(C.data, "download_day", missing)
    assert C.listing_bars("NEWUSDT", T0, tmp_path, client=None) == (None, "aucune cotation USDT au comptant dans les 7 jours")
