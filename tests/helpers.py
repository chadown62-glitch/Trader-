"""Générateurs de données de test (marchés artificiels contrôlés)."""

from __future__ import annotations

import numpy as np
import pandas as pd

HOUR = 3_600_000


def make_series(
    n: int,
    seed: int = 0,
    vol: float = 0.006,
    start: float = 100.0,
    start_ts: int = 1_600_000_000_000,
    drift_fn=None,
    tf_ms: int = HOUR,
) -> pd.DataFrame:
    """Marche aléatoire log-normale. `drift_fn(ts_array) -> dérive par bougie`."""
    rng = np.random.default_rng(seed)
    ts = start_ts - start_ts % tf_ms + tf_ms * np.arange(n, dtype=np.int64)
    drift = np.zeros(n) if drift_fn is None else drift_fn(ts)
    rets = drift + vol * rng.standard_normal(n)
    close = start * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start], close[:-1]])
    wick_h = np.abs(rng.standard_normal(n)) * vol * 0.6 * close
    wick_l = np.abs(rng.standard_normal(n)) * vol * 0.6 * close
    high = np.maximum(open_, close) + wick_h
    low = np.minimum(open_, close) - wick_l
    volume = np.exp(rng.standard_normal(n) * 0.4) * 1000
    return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def hour_of_day_drift(mu: float = 0.002):
    """Motif « planté » : hausse entre 0 h et 8 h UTC, baisse le reste du temps (dérive nette nulle)."""

    def fn(ts):
        hours = (ts // HOUR) % 24
        return np.where(hours < 8, mu, -mu / 2)

    return fn
