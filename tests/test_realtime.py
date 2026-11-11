"""Bouclier temps réel, calendrier économique automatique, dépêches regroupées et temps réel."""

import json
import time

import httpx
import pytest

from sentinelle.config import DEFAULT_SETTINGS, apply_update
from sentinelle.market.context import ContextService, fetch_calendar
from sentinelle.market.sources import SyntheticSource
from sentinelle.news.service import NewsService, similarity, tokens
from sentinelle.risk.shield import Shield
from sentinelle.services.core import App

from .conftest import FakeClock


@pytest.fixture
def settings():
    return apply_update(DEFAULT_SETTINGS, {})


def calm_book(px=100.0):
    return {"bids": [[px - 0.01 * (k + 1), 50.0] for k in range(20)], "asks": [[px + 0.01 * (k + 1), 50.0] for k in range(20)]}


def feed_minutes(shield, symbol, clock, ranges_pct, px=100.0):
    """Une micro-bougie par minute, d'amplitude donnée (en %)."""
    for r in ranges_pct:
        clock.advance(60)
        end = int(clock() * 1000)
        shield.on_tick(symbol, px, px * (1 + r / 200), px * (1 - r / 200), px, end - 5000, end)
    clock.advance(60)
    end = int(clock() * 1000)
    shield.on_tick(symbol, px, px, px, px, end - 5000, end)  # clôt la dernière minute


# -- bouclier ---------------------------------------------------------------------
def test_volatility_spike_pauses_entries_then_expires(settings):
    clock = FakeClock()
    sh = Shield(lambda: settings, book_fn=lambda s: calm_book(), clock=clock)
    feed_minutes(sh, "ETH/USDC", clock, [0.1] * 40)
    assert sh.check_entry("ETH/USDC", 500) is None
    feed_minutes(sh, "ETH/USDC", clock, [2.0])  # bougie de 2 % : 20 fois la normale
    reason = sh.check_entry("ETH/USDC", 500)
    assert reason and "brutal" in reason and "2,00 % en une minute" in reason  # nombres au format français
    clock.advance(31 * 60)
    assert sh.check_entry("ETH/USDC", 500) is None


def test_spike_alert_is_published_once_and_listeners_can_read_state(settings):
    from sentinelle.services.bus import EventBus

    clock = FakeClock()
    bus = EventBus()
    sh = Shield(lambda: settings, book_fn=lambda s: calm_book(), bus=bus, clock=clock)
    seen = []
    # un abonné qui relit l'état du bouclier pendant l'alerte ne doit pas bloquer le moteur
    bus.add_listener(lambda topic, ev: seen.append((ev["data"]["message"], sh.status()["volatility"])) if topic == "alert" else None)
    feed_minutes(sh, "SOL/USDC", clock, [0.1] * 40 + [2.0, 2.5])  # deux minutes agitées d'affilée
    assert len(seen) == 1  # une seule alerte pour la même pause
    assert "Bouclier : mouvement brutal sur SOL/USDC" in seen[0][0] and "SOL/USDC" in seen[0][1]


def test_btc_shock_blocks_whole_market(settings):
    clock = FakeClock()
    sh = Shield(lambda: settings, book_fn=lambda s: calm_book(), clock=clock)
    feed_minutes(sh, "BTC/USDC", clock, [0.08] * 40 + [1.5])
    assert sh.check_entry("SOL/USDC", 500) is not None


def test_small_absolute_moves_are_not_spikes(settings):
    clock = FakeClock()
    sh = Shield(lambda: settings, book_fn=lambda s: calm_book(), clock=clock)
    feed_minutes(sh, "ETH/USDC", clock, [0.01] * 40 + [0.3])  # 30× la normale mais seulement 0,3 %
    assert sh.check_entry("ETH/USDC", 500) is None


def test_spread_and_depth_checks(settings):
    wide = {"bids": [[99.0, 100.0]], "asks": [[101.0, 100.0]]}
    sh = Shield(lambda: settings, book_fn=lambda s: wide, clock=FakeClock())
    assert "écart achat/vente trop large (200,0 points de base, maximum 20)" in sh.check_entry("ETH/USDC", 500)
    thin = {"bids": [[99.99, 1.0]], "asks": [[100.01, 1.0]]}
    sh = Shield(lambda: settings, book_fn=lambda s: thin, clock=FakeClock())
    assert "liquidité" in sh.check_entry("ETH/USDC", 5_000)
    assert sh.check_entry("ETH/USDC", 20) is None


def test_book_failure_never_blocks(settings):
    def broken(symbol):
        raise RuntimeError("réseau")

    sh = Shield(lambda: settings, book_fn=broken, clock=FakeClock())
    assert sh.check_entry("ETH/USDC", 500) is None


def okx_transport(liqs, calls=None):
    """Imite l'API d'OKX : plus récentes d'abord, 100 par page, pagination par « after »."""
    def handler(request: httpx.Request):
        path = request.url.path
        if path.endswith("/instruments"):
            return httpx.Response(200, json={"data": [{"ctVal": "0.01" if "BTC" in str(request.url) else "0.1"}]})
        if path.endswith("/liquidation-orders"):
            if calls is not None:
                calls.append(dict(request.url.params))
            rows = sorted(liqs.get(request.url.params["uly"], []), key=lambda d: -int(d["ts"]))
            after = request.url.params.get("after")
            if after:
                rows = [d for d in rows if int(d["ts"]) < int(after)]
            return httpx.Response(200, json={"code": "0", "data": [{"details": rows[:100]}] if rows else []})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def liq(ts_ms, usd, px=50_000, ct=0.01):
    return {"ts": str(int(ts_ms)), "sz": str(usd / (ct * px)), "bkPx": str(px), "side": "sell"}


@pytest.fixture
def no_sleep(monkeypatch):
    import sentinelle.risk.shield as shield_mod

    monkeypatch.setattr(shield_mod.time, "sleep", lambda s: None)


def test_liquidations_backfill_then_cascade_without_double_count(settings, no_sleep):
    clock = FakeClock()
    now_ms = clock() * 1000
    # journée normale : 0,1 M$ liquidé par minute sur le BTC pendant 24 h (1,5 M$ par quart d'heure)
    data = {"BTC-USDT": [liq(now_ms - k * 60_000 - 30_000, 100_000) for k in range(16, 24 * 60)]}
    calls = []
    sh = Shield(lambda: settings, clock=clock, transport=okx_transport(data, calls))
    sh.poll_liquidations()
    assert len([c for c in calls if c["uly"] == "BTC-USDT"]) >= 15  # les 24 h ont été lues page par page
    assert sh.metrics["liquidations_ref_musd"] == pytest.approx(1.5, rel=0.05)
    assert sh.check_entry("ETH/USDC", 500) is None
    # vague de ventes forcées : 400 liquidations de 0,5 M$ en 10 minutes (bien plus qu'une page)
    clock.advance(60)
    now_ms = clock() * 1000
    data["BTC-USDT"] += [liq(now_ms - 600_000 + i * 1500, 500_000) for i in range(400)]
    calls.clear()
    sh.poll_liquidations()
    assert 4 <= len([c for c in calls if c["uly"] == "BTC-USDT"]) <= 6  # s'arrête aux données déjà connues
    assert sh.metrics["liquidations_15m_musd"] == pytest.approx(200, rel=0.01)
    reason = sh.check_entry("ETH/USDC", 500)
    assert reason and "liquidations" in reason
    sh.poll_liquidations()  # rien de nouveau : pas de double comptage
    assert sh.metrics["liquidations_15m_musd"] == pytest.approx(200, rel=0.01)
    # deux liquidations identiques dans la même page : deux événements, et la lecture continue
    twin = liq(clock() * 1000 - 5_000, 1_000_000)
    data["BTC-USDT"] += [dict(twin), dict(twin)] + [liq(clock() * 1000 - 4_000 + i, 10_000) for i in range(150)]
    sh.poll_liquidations()
    assert sh.metrics["liquidations_15m_musd"] == pytest.approx(203.5, rel=0.001)
    clock.advance(61 * 60)
    assert sh.check_entry("ETH/USDC", 500) is None  # la pause dure une heure


def test_liquidation_threshold_adapts_to_busy_days(settings, no_sleep):
    clock = FakeClock()
    now_ms = clock() * 1000
    # journée agitée : 1 M$ par minute -> 15 M$ par quart d'heure ; un pic à 25 M$ reste « normal »
    data = {"BTC-USDT": [liq(now_ms - k * 60_000 - 30_000, 1_000_000) for k in range(16, 24 * 60)]}
    data["BTC-USDT"] += [liq(now_ms - 120_000 - i * 1000, 500_000) for i in range(50)]
    sh = Shield(lambda: settings, clock=clock, transport=okx_transport(data))
    sh.poll_liquidations()
    assert sh.metrics["liquidations_15m_musd"] == pytest.approx(25, rel=0.01)
    assert sh.check_entry("ETH/USDC", 500) is None


def test_liquidation_reference_needs_two_hours_of_observation(settings, no_sleep):
    clock = FakeClock()
    now_ms = clock() * 1000
    # l'API ne renvoie qu'une heure : pas de référence, seul le plancher (10 M$) s'applique
    data = {"BTC-USDT": [liq(now_ms - k * 60_000, 50_000) for k in range(0, 60)]}
    data["ETH-USDT"] = [liq(now_ms - k * 60_000, 50_000, px=3_000, ct=0.1) for k in range(0, 60)]
    data["BTC-USDT"] += [liq(now_ms - 60_000 - i * 100, 500_000) for i in range(30)]  # 15 M$
    sh = Shield(lambda: settings, clock=clock, transport=okx_transport(data))
    sh.poll_liquidations()
    assert sh.metrics["liquidations_ref_musd"] is None
    assert sh.metrics["liquidations_15m_musd"] == pytest.approx(16.5, rel=0.01)  # 15 + 0,75 + 0,75 M$
    assert "liquidations" in sh.check_entry("ETH/USDC", 500)


def test_major_news_pause(settings, db):
    clock = FakeClock()
    db.insert("news", {"uid": "x", "source": "s", "title": "Exchange majeur piraté", "published_ts": clock() - 600,
                       "importance": 0.95, "confidence": 0.8, "sentiment": -0.9, "assets": json.dumps(["*"])})
    sh = Shield(lambda: settings, db=db, clock=clock)
    assert "actualité majeure" in sh.check_entry("BTC/USDC", 500)
    settings["shield"]["news_pause"] = False
    assert sh.check_entry("BTC/USDC", 500) is None


def test_shield_disabled(settings):
    settings["shield"]["enabled"] = False
    wide = {"bids": [[90.0, 1.0]], "asks": [[110.0, 1.0]]}
    assert Shield(lambda: settings, book_fn=lambda s: wide, clock=FakeClock()).check_entry("ETH/USDC", 500) is None


# -- calendrier économique automatique -------------------------------------------------
FF = [
    {"title": "CPI m/m", "country": "USD", "date": "2030-01-15T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.6%"},
    {"title": "Retail Sales m/m", "country": "GBP", "date": "2030-01-15T07:00:00-05:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Bank Holiday", "country": "USD", "date": "2030-01-16T00:00:00-05:00", "impact": "Holiday"},
    {"title": "Main Refinancing Rate", "country": "EUR", "date": "2030-01-16T08:15:00-05:00", "impact": "High", "forecast": "2.0%", "previous": "2.0%"},
]


def test_calendar_parsing_and_blackout(tmp_path):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=FF))
    with httpx.Client(transport=transport) as c:
        events = fetch_calendar(c, ["USD", "EUR"])
    labels = [e["label"] for e in events]
    assert labels == ["USD : Inflation (CPI)", "EUR : BCE : taux directeur"]
    assert events[0]["ts"] == pytest.approx(1894714200)  # 13 h 30 UTC
    assert events[0]["forecast"] == "0.3%"
    app = App(tmp_path, source=SyntheticSource(["BTC/USDC"], days=3), start=False,
              settings_override={"general": {"quote": "USDC", "watchlist": ["BTC/USDC"]}, "market": {"source": "synthetic"}})
    ctx = ContextService(app, transport=transport)
    with httpx.Client(transport=transport) as c:
        assert ctx.refresh_calendar(c) == 2
        assert ctx.refresh_calendar(c) == 0  # relu au plus toutes les 3 heures…
        assert ctx.refresh_calendar(c, force=True) == 2  # …sauf demande explicite (bouton ↻)
    from sentinelle.risk.macro import blackout
    from sentinelle.storage.db import loads

    auto = loads(app.db.get_meta("calendar_events"), [])
    assert blackout(app.settings, 1894714200 - 1200, auto)[1] == "USD : Inflation (CPI)"
    assert blackout(app.settings, 1894714200 - 7200, auto) is None
    app.db.close()


# -- dépêches : regroupement et temps réel ------------------------------------------------
def test_title_similarity():
    a = tokens("Major exchange hacked, $200M stolen from hot wallets")
    b = tokens("Exchange hacked: $200M stolen from hot wallets, withdrawals paused")
    c = tokens("Bitcoin ETF inflows hit record high")
    assert similarity(a, b) >= 0.6 > similarity(a, c)


@pytest.fixture
def news_app(tmp_path):
    a = App(tmp_path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=3), start=False,
            settings_override={"general": {"quote": "USDC", "watchlist": ["BTC/USDC", "ETH/USDC"]}, "market": {"source": "synthetic"},
                               "news": {"enabled": True, "telegram_channels": ["@flashs"]}})
    yield a
    a.db.close()


def ollama(calls):
    def handler(req: httpx.Request):
        calls.append(1)
        ans = {"assets": ["BTC"], "market_wide": True, "event_type": "piratage", "sentiment": -0.85, "importance": 0.8,
               "confidence": 0.7, "summary_fr": "Piratage d'une plateforme."}
        return httpx.Response(200, json={"message": {"content": json.dumps(ans)}})

    return httpx.MockTransport(handler)


def test_same_event_analyzed_once_and_confirmed_by_two_sources(news_app):
    calls = []
    news = NewsService(news_app, transport=ollama(calls))
    now = time.time()
    news.insert("a", "Source A", "Major exchange hacked, $200M stolen from hot wallets", "", now - 120, "")
    news.insert("b", "Source B", "Exchange hacked: $200M stolen from hot wallets, withdrawals paused", "", now - 60, "")
    rows = news_app.db.query("SELECT cluster FROM news")
    assert len({r["cluster"] for r in rows}) == 1
    news.analyze_pending()
    assert len(calls) == 1  # une seule analyse pour le même événement
    s = news.score_for("ETH/USDC")
    assert s["n"] == 1 and s["confirmed"] is True


def test_telegram_flash_is_ingested_and_analyzed_immediately(news_app):
    calls = []
    news = NewsService(news_app, transport=ollama(calls))
    news_app.extensions.append(news)
    from sentinelle.news.signals import SignalWatcher

    watcher = SignalWatcher(news_app)
    watcher.route_message("@flashs", 42, time.time(), "BREAKING: Major exchange hacked. Withdrawals halted for all users.")
    row = news_app.db.one("SELECT * FROM news WHERE uid = 'tg:@flashs:42'")
    assert row and row["source"] == "Telegram @flashs"
    assert news._fast.is_set() and news.state["realtime"] == 1
    assert news.analyze_pending() == 1 and calls
    # un canal de dépêches n'alimente pas les statistiques de signaux
    assert news_app.db.scalar("SELECT COUNT(*) FROM signals", (), 0) == 0
    # un canal de signaux n'est lu que si la lecture des signaux est activée
    news_app.settings["signals"].update(channels=["@calls"], enabled=False)
    watcher.route_message("@calls", 7, time.time(), "BTC/USDT LONG entry 100 TP 120 SL 90")
    assert news_app.db.scalar("SELECT COUNT(*) FROM signals", (), 0) == 0
    news_app.settings["signals"]["enabled"] = True
    watcher.route_message("@calls", 8, time.time(), "BTC/USDT LONG entry 100 TP 120 SL 90")
    assert news_app.db.scalar("SELECT COUNT(*) FROM signals", (), 0) == 1


def test_agent_refuses_entry_when_shield_blocks(db, paper, pm, settings, prices, clock):
    from sentinelle.agent.agent import TradingAgent
    from sentinelle.risk.manager import RiskManager

    from .test_risk_agent_backtest import _feat_with_signal

    df, feat, idx = _feat_with_signal()
    sub = feat.iloc[: idx[-1] + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    settings["ai"]["regime_filter"] = False
    settings["ai"]["strategies"] = {k: k == "trend_breakout" for k in settings["ai"]["strategies"]}
    settings["risk"]["min_reward_to_fees"] = 1.0
    risk = RiskManager(db, paper.account_id, lambda: settings, clock)
    agent = TradingAgent("ia", "training", db, pm, risk, lambda: settings, lambda: None, timeframe="1h", clock=clock,
                         shield_fn=lambda s, n: "cascade de liquidations", context_fn=lambda s: {"spread_bps": 1.2})
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    assert pm.open_positions() == []
    cand = db.one("SELECT * FROM candidates")
    assert "Bouclier" in cand["decision_reason"]
    assert json.loads(cand["features"])["contexte"] == {"spread_bps": 1.2}


def test_news_api_groups_events_and_drops_unsafe_links(news_app):
    from fastapi.testclient import TestClient

    from sentinelle.api.server import create_api

    news = NewsService(news_app)
    news_app.extensions.append(news)
    now = time.time()
    news.insert("g1", "Source A", "Major exchange hacked, $200M stolen from hot wallets", "https://a.test/1", now - 120, "")
    news.insert("g2", "Source B", "Exchange hacked: $200M stolen from hot wallets, withdrawals paused", "javascript:alert(1)", now - 60, "")
    news.insert("g3", "Source C", "Bitcoin ETF inflows hit record high", "https://c.test/3", now - 30, "")
    with TestClient(create_api(news_app)) as c:
        h = {"X-Sentinelle": "1"}
        assert c.post("/api/auth/setup", json={"password": "un-mot-de-passe-solide"}, headers=h).status_code == 200
        rows = c.get("/api/news").json()
    assert [r["uid"] for r in rows] == ["g3", "g1"]  # une fiche par événement, la première dépêche en tête
    assert [(a["source"], a["url"]) for a in rows[1]["also"]] == [("Source B", "")]  # lien « javascript: » supprimé
    assert rows[1]["url"] == "https://a.test/1"


def test_existing_settings_receive_official_feeds_once(tmp_path):
    from sentinelle.config import OFFICIAL_FEEDS
    from sentinelle.storage.db import Database, dumps

    old = apply_update(DEFAULT_SETTINGS, {"general": {"quote": "USDC", "watchlist": ["BTC/USDC"]}, "market": {"source": "synthetic"}})
    old["news"]["feeds"] = ["https://cointelegraph.com/rss", "https://mon-flux.example/rss"]  # réglages d'une version précédente
    db = Database(tmp_path / "sentinelle.db")
    db.execute("INSERT INTO settings(id, data, updated_at) VALUES (1, ?, ?)", (dumps(old), time.time()))
    db.close()
    for _ in range(2):  # deux démarrages : la migration ne s'applique qu'une fois
        app = App(tmp_path, source=SyntheticSource(["BTC/USDC"], days=3), start=False)
        feeds = app.settings["news"]["feeds"]
        assert feeds[:2] == ["https://cointelegraph.com/rss", "https://mon-flux.example/rss"]  # vos flux sont gardés
        assert feeds[2:] == OFFICIAL_FEEDS and len(feeds) == len(set(feeds))
        app.db.close()
