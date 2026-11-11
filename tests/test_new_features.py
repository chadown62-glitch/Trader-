"""Nouveautés : signaux de canaux, calendrier macro, vérification croisée, 2FA, raccord d'historique, sauvegardes."""

import time

import numpy as np
import pandas as pd
import pytest

from sentinelle.api.auth import Auth, totp
from sentinelle.config import DEFAULT_SETTINGS, SettingsError, apply_update
from sentinelle.market.crosscheck import CrossChecker
from sentinelle.market.history import splice
from sentinelle.news.signals import parse_signal, simulate_signal
from sentinelle.risk.macro import blackout, parse_event, upcoming

from .conftest import MARKETS

HOUR = 3_600_000


@pytest.mark.parametrize("text,expected", [
    ("🚀 #BTC/USDT LONG\nEntry: 65,000 - 66,000\nTP1: 68,000\nTP2: 70,000\nSL: 63,500", ("BTC", "long", 65500, 63500, [68000, 70000])),
    ("ACHAT ETHUSDT entrée 2100 objectif 2250 objectif 2400 stop 2020", ("ETH", "long", 2100, 2020, [2250, 2400])),
    ("SHORT SOL/USDT Entry 150 Targets 140 135 SL 158", ("SOL", "short", 150, 158, [140, 135])),
    ("#DOGE buy zone 0,070-0,072 tp 0,08 sl 0,065", ("DOGE", "long", 0.071, 0.065, [0.08])),
])
def test_signal_parser(text, expected):
    s = parse_signal(text)
    assert s["parsed"]
    assert (s["symbol"], s["side"]) == expected[:2]
    assert s["entry"] == pytest.approx(expected[2]) and s["stop"] == pytest.approx(expected[3])
    assert s["targets"] == pytest.approx(expected[4])


def test_signal_parser_rejects_noise_and_incoherence():
    assert not parse_signal("Bonjour, le marché est calme aujourd'hui")["parsed"]
    assert not parse_signal("BTC/USDT LONG entry 100 TP 120 SL 130")["parsed"]  # stop au-dessus de l'entrée


def _candles(path, start=1_700_000_000_000):
    ts = start + HOUR * np.arange(len(path))
    c = np.asarray(path, float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c})


def test_signal_simulation_target_stop_and_pending():
    candles = _candles([100, 100, 103, 106, 111, 112])
    sig = {"ts": 1_700_000_000, "side": "long", "stop": 95, "targets": [110]}
    res = simulate_signal(sig, candles, fee=0.0, slip=0.0)
    assert res["outcome"] == "objectif" and res["outcome_r"] == pytest.approx(2.0)
    down = _candles([100, 99, 96, 94, 93, 92])
    res = simulate_signal(sig, down, fee=0.0, slip=0.0)
    # la bougie ouvre déjà sous le stop (94 < 95) : sortie au prix d'ouverture, donc un peu plus de 1 R perdu
    assert res["outcome"] == "stop" and res["outcome_r"] == pytest.approx(-1.2)
    flat = _candles([100, 101, 100, 101])
    assert simulate_signal(sig, flat, horizon_hours=72) is None  # pas encore assez de recul


def test_macro_blackout():
    s = apply_update(DEFAULT_SETTINGS, {"macro": {"events": ["2030-01-15 19:00 Fed"], "blackout_minutes": 60}})
    ts, _ = parse_event("2030-01-15 19:00 Fed")
    assert blackout(s, ts - 30 * 60) is not None
    assert blackout(s, ts - 3 * 3600) is None
    assert upcoming(s, ts - 86400)[0]["label"] == "Fed"
    with pytest.raises(SettingsError):
        apply_update(DEFAULT_SETTINGS, {"macro": {"events": ["15/01/2030 Fed"]}})


def test_macro_blackout_blocks_risk(db, paper, clock):
    from sentinelle.risk.manager import RiskManager

    s = apply_update(DEFAULT_SETTINGS, {"macro": {"events": [time.strftime("%Y-%m-%d %H:%M", time.gmtime(clock() + 600)) + " CPI"]}})
    risk = RiskManager(db, paper.account_id, lambda: s, clock)
    d = risk.evaluate_entry("ETH/USDC", 2500.0, 2450.0, 2600.0, equity=10_000, cash=10_000, market=MARKETS["ETH/USDC"], fee_rate=0.001, slip=0.0005)
    assert not d.approved and "macro" in d.reasons[0]


class FakeTickerExchange:
    def __init__(self, prices):
        self.prices = prices

    def load_markets(self):
        return {k: {} for k in self.prices}

    def fetch_ticker(self, symbol):
        return {"last": self.prices[symbol]}


def test_crosscheck_detects_gap_and_ignores_missing_pairs():
    cc = CrossChecker("binance", exchange=FakeTickerExchange({"BTC/USDC": 50_000}))
    assert cc.check("BTC/USDC", 50_400, 2.0) is None
    assert "incohérent" in cc.check("BTC/USDC", 53_000, 2.0)
    assert cc.check("XYZ/USDC", 1.0, 2.0) is None  # paire absente de la 2e plateforme : pas de blocage


def test_totp_known_vector_and_replay(db):
    # vecteur de la RFC 6238 (secret « 12345678901234567890 »), 6 chiffres
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"
    auth = Auth(db)
    setup = auth.totp_setup()
    assert setup["uri"].startswith("otpauth://totp/")
    code = totp(setup["secret"])
    assert auth.totp_enable(code)
    assert auth.totp_enabled
    assert not auth.totp_check(code)  # un code ne sert qu'une fois
    assert not auth.totp_check("000000")


def test_splice_history():
    old = _candles([10, 11, 12, 13, 14, 15])
    new = _candles([14.1, 15.1, 16.1], start=1_700_000_000_000 + 4 * HOUR)
    out = splice(old, new)
    assert len(out) == 7 and out["ts"].is_monotonic_increasing
    assert out["close"].iloc[-3:].tolist() == [14.1, 15.1, 16.1]
    assert out["close"].iloc[3] == pytest.approx(13 * 14.1 / 14)  # prix raccordés au point de jonction


def test_backup_creates_copy(tmp_path):
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.core import App

    app = App(tmp_path, source=SyntheticSource(["BTC/USDC"], days=3), start=False,
              settings_override={"general": {"quote": "USDC", "watchlist": ["BTC/USDC"]}, "market": {"source": "synthetic"}})
    dest = app.backup_if_due(force=True)
    assert dest and dest.exists() and dest.stat().st_size > 0
    assert app.backup_if_due() is None  # une seule sauvegarde par jour
    app.db.close()
