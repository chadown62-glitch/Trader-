"""Caractéristiques (« features ») utilisées par les stratégies et par l'IA.

Elles sont sans unité (rendements, distances exprimées en ATR, ratios) pour
qu'un même modèle puisse apprendre sur plusieurs cryptos à la fois.
Chaque valeur à la bougie t n'utilise que les bougies ≤ t (testé).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import timeframe_minutes
from .indicators import atr, bollinger, donchian_prev, ema, macd, rsi, sma

# Colonnes données au modèle d'IA
FEATURE_COLUMNS = [
    "r1", "r3", "r6", "r12", "r24", "r72",
    "vol24", "vol_ratio", "atr_pct",
    "rsi14", "rsi_slope",
    "d_ema20", "d_ema50", "d_ema200",
    "slope_ema20", "slope_ema50",
    "bb_pctb", "bb_width", "macd_hist_atr",
    "don_pos", "breakout_atr",
    "vol_z", "body", "upper_wick", "lower_wick",
    "hour_sin", "hour_cos",
    "trend_50d", "trend_200d", "sma50_200", "dd_30d",
    "btc_r24", "btc_trend_200d",
]

REGIMES = ("bull", "neutral", "bear", "unknown")


def bars_per_day(timeframe: str) -> int:
    return max(1, 1440 // timeframe_minutes(timeframe))


def warmup_bars(timeframe: str) -> int:
    """Nombre de bougies d'historique nécessaires pour des valeurs exactes."""
    return 200 * bars_per_day(timeframe) + 300


def compute_features(df: pd.DataFrame, timeframe: str, btc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Calcule toutes les colonnes utiles. `df` : bougies clôturées triées (ts, open, high, low, close, volume).

    `btc` : bougies du BTC pour le contexte de marché (None si la paire est le BTC lui-même).
    """
    bpd = bars_per_day(timeframe)
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
    out = pd.DataFrame({"ts": df["ts"].to_numpy(), "open": o.to_numpy(), "high": h.to_numpy(),
                        "low": l.to_numpy(), "close": c.to_numpy()}, index=df.index)
    logc = np.log(c)
    r1 = logc.diff()
    for k in (1, 3, 6, 12, 24, 72):
        out[f"r{k}"] = logc.diff(k)
    out["vol24"] = r1.rolling(24, min_periods=24).std()
    vol168 = r1.rolling(168, min_periods=168).std()
    out["vol_ratio"] = out["vol24"] / vol168.replace(0.0, np.nan)
    a = atr(df, 14)
    out["atr"] = a
    out["atr_pct"] = a / c
    rs = rsi(c, 14)
    out["rsi14"] = rs
    out["rsi_slope"] = rs - rs.shift(3)
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    out["ema20"], out["ema50"], out["ema200"] = e20, e50, e200
    a_safe = a.replace(0.0, np.nan)
    out["d_ema20"] = (c - e20) / a_safe
    out["d_ema50"] = (c - e50) / a_safe
    out["d_ema200"] = (c - e200) / a_safe
    out["slope_ema20"] = (e20 - e20.shift(6)) / a_safe
    out["slope_ema50"] = (e50 - e50.shift(6)) / a_safe
    mid, up, lo = bollinger(c, 20, 2.0)
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, up, lo
    width = (up - lo).replace(0.0, np.nan)
    out["bb_pctb"] = (c - lo) / width
    out["bb_width"] = (up - lo) / mid
    _, _, hist = macd(c)
    out["macd_hist"] = hist
    out["macd_hist_atr"] = hist / a_safe
    dh, dl = donchian_prev(h, l, 20)
    out["don_high"], out["don_low"] = dh, dl
    out["don_high55"] = donchian_prev(h, l, 55)[0]
    out["don_pos"] = (c - dl) / (dh - dl).replace(0.0, np.nan)
    out["breakout_atr"] = (c - dh) / a_safe
    lv = np.log1p(v)
    out["vol_z"] = (lv - lv.rolling(48, min_periods=48).mean()) / lv.rolling(48, min_periods=48).std().replace(0.0, np.nan)
    rng = (h - l).replace(0.0, np.nan)
    out["body"] = (c - o) / rng
    out["upper_wick"] = (h - np.maximum(o, c)) / rng
    out["lower_wick"] = (np.minimum(o, c) - l) / rng
    hours = ((df["ts"] // 3_600_000) % 24).astype(float)
    out["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    s50 = sma(c, 50 * bpd)
    s200 = sma(c, 200 * bpd)
    out["trend_50d"] = c / s50 - 1
    out["trend_200d"] = c / s200 - 1
    out["sma50_200"] = s50 / s200 - 1
    out["dd_30d"] = c / c.rolling(30 * bpd, min_periods=30 * bpd).max() - 1
    out["regime"] = classify_regime(c, s50, s200)

    if btc is not None and len(btc):
        bc = btc[["ts", "close"]].copy()
        blog = np.log(bc["close"])
        bc["btc_r24"] = blog.diff(24)
        bc["btc_trend_200d"] = bc["close"] / sma(bc["close"], 200 * bpd) - 1
        s50b = sma(bc["close"], 50 * bpd)
        s200b = sma(bc["close"], 200 * bpd)
        bc["btc_regime"] = classify_regime(bc["close"], s50b, s200b)
        merged = out[["ts"]].merge(bc[["ts", "btc_r24", "btc_trend_200d", "btc_regime"]], on="ts", how="left")
        out["btc_r24"] = merged["btc_r24"].to_numpy()
        out["btc_trend_200d"] = merged["btc_trend_200d"].to_numpy()
        out["btc_regime"] = merged["btc_regime"].fillna("unknown").to_numpy()
    else:
        out["btc_r24"] = out["r24"]
        out["btc_trend_200d"] = out["trend_200d"]
        out["btc_regime"] = out["regime"]
    return out


def classify_regime(c: pd.Series, s50: pd.Series, s200: pd.Series) -> pd.Series:
    """Climat du marché : haussier, baissier ou neutre (inconnu si historique insuffisant)."""
    bull = (c > s200) & (s50 > s200)
    bear = (c < s200) & (s50 < s200)
    reg = np.where(bull, "bull", np.where(bear, "bear", "neutral"))
    reg = np.where(s200.isna() | s50.isna(), "unknown", reg)
    return pd.Series(reg, index=c.index, dtype=object)


REGIME_LABELS = {"bull": "haussier", "neutral": "neutre", "bear": "baissier", "unknown": "inconnu"}
