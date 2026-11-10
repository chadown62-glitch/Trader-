"""Historique des prix : téléchargement incrémental et cache."""

from sentinelle.market.history import HistoryStore, clean_frame, to_frame

HOUR = 3_600_000


class CountingSource:
    """Source factice : les données n'existent qu'à partir de `listed` (paire récente)."""

    name = "factice"

    def __init__(self, now_ms, listed_ms):
        self.now = now_ms
        self.listed = listed_ms
        self.calls = []

    def now_ms(self):
        return self.now

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls.append(since)
        start = max(since or self.listed, self.listed)
        start -= start % HOUR
        rows = []
        t = start
        while t + HOUR <= self.now + HOUR and len(rows) < limit:
            rows.append([t, 100.0, 101.0, 99.0, 100.5, 10.0])
            t += HOUR
        return rows


def test_incremental_update_and_listing_date_memory(tmp_path):
    now = 1_700_000_000_000 - 1_700_000_000_000 % HOUR
    src = CountingSource(now, listed_ms=now - 100 * HOUR)
    store = HistoryStore(tmp_path, src)
    df = store.update("NEW/USDC", "1h", days=30)  # demande 30 jours, la paire n'en a que ~4
    assert len(df) == 100 and df["ts"].is_monotonic_increasing
    n_calls = len(src.calls)
    src.now += 2 * HOUR  # deux nouvelles bougies
    df2 = store.update("NEW/USDC", "1h", days=30)
    assert len(df2) == 102
    # seule la fin a été téléchargée : la période avant la cotation n'est pas redemandée
    assert len(src.calls) - n_calls == 1
    assert src.calls[-1] == int(df["ts"].iloc[-1]) + HOUR


def test_incomplete_last_candle_is_never_stored(tmp_path):
    now = 1_700_000_000_000 - 1_700_000_000_000 % HOUR + 30 * 60_000  # au milieu d'une bougie
    src = CountingSource(now, listed_ms=now - 10 * HOUR)
    df = HistoryStore(tmp_path, src).update("X/USDC", "1h", days=1)
    assert (df["ts"] + HOUR <= now).all()


def test_clean_frame_fixes_inconsistencies():
    df = to_frame([[2, 10, 9, 11, 10, 1], [1, 10, 12, 9, 11, 1], [2, 10, 12, 9, 11, 1], [3, -1, 1, 1, 1, 1]])
    out, rep = clean_frame(df)
    assert out["ts"].tolist() == [1, 2]
    assert rep["duplicates"] == 1 and rep["dropped_invalid"] == 1
    assert (out["high"] >= out[["open", "close"]].max(axis=1)).all()
    assert (out["low"] <= out[["open", "close"]].min(axis=1)).all()


def _bars(start_h, n, price=100.0):
    return to_frame([[HOUR * 4 * (start_h + k), price, price * 1.01, price * 0.99, price, 10.0] for k in range(n)])


def test_history_never_spans_a_delisting_gap():
    """Une paire retirée puis recotée : l'apprentissage n'enjambe jamais le trou."""
    import pandas as pd

    from sentinelle.market.history import continuous_history, last_continuous

    old_listing = _bars(0, 100, price=50.0)  # première cotation
    relisted = _bars(100 + 6 * 400, 300, price=200.0)  # recotée 400 jours plus tard, à un tout autre prix
    usdc = pd.concat([old_listing, relisted], ignore_index=True)
    recent = last_continuous(usdc)
    assert len(recent) == 300 and recent["close"].iloc[0] == 200.0
    # une interruption de quelques heures (maintenance de la plateforme) ne coupe pas l'historique
    small_gap = pd.concat([_bars(0, 50), _bars(56, 50)], ignore_index=True)
    assert len(last_continuous(small_gap)) == 100
    # la paire USDT, cotée sans interruption, comble tout ce qui précède la recotation
    usdt = _bars(0, 100 + 6 * 400 + 300, price=202.0)
    full = continuous_history(usdc, usdt)
    ts = full["ts"].to_numpy()
    assert (ts[1:] - ts[:-1]).max() == HOUR * 4  # aucun trou
    assert len(full) == len(usdt)
    assert full["close"].iloc[-1] == 200.0  # la partie récente vient bien de la paire tradée
    assert abs(full["close"].iloc[0] - 200.0) < 1e-9  # l'ancien historique est raccordé au même niveau de prix
    # sans paire de remplacement : seule la partie récente continue est gardée
    assert len(continuous_history(usdc, None)) == 300
