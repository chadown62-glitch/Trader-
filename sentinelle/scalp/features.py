"""Lecture du marché par un trader : bougies de 5 minutes et contexte, calculés sans regard vers le futur.

Toutes les grandeurs d'une bougie k n'utilisent que les bougies déjà clôturées jusqu'à k. Une bougie de
5 minutes à laquelle manque une minute est invalide : aucune décision n'y est prise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BAR_MS = 300_000
DAY = 288  # bougies de 5 minutes par jour
FEATURES = ["trend_1h", "slope_1h", "trend_4h", "ret_30m", "ret_1h", "ret_4h", "atr_pct", "vol_z", "taker_ratio",
            "vwap_dist", "range_pos", "range_height", "squeeze", "realized_vol", "hour_sin", "hour_cos",
            "btc_trend_1h", "btc_ret_1h", "btc_ret_4h"]
DAILY = ["trend_1d", "slope_1d"]  # tendance de fond (moyenne de 20 jours), utilisée par la variante « tendance »


def five_minute_bars(minutes: pd.DataFrame) -> pd.DataFrame:
    """Agrège la grille d'une minute en bougies de 5 minutes ; « m_next » est la minute d'entrée possible (ouverture suivante)."""
    t = minutes["t"].to_numpy("int64")
    key = t // BAR_MS
    g = minutes.assign(key=key, pos=np.arange(len(minutes))).groupby("key", sort=True)
    bars = pd.DataFrame({
        "t": g["t"].first().to_numpy("int64"),
        "o": g["o"].first().to_numpy(float), "h": g["h"].max().to_numpy(float),
        "l": g["l"].min().to_numpy(float), "c": g["c"].last().to_numpy(float),
        "v": g["v"].sum(min_count=1).to_numpy(float), "taker_buy": g["taker_buy"].sum(min_count=1).to_numpy(float),
        "complete": (g["o"].count() == 5).to_numpy(),
        "first_minute": g["pos"].min().to_numpy("int64"),
    })
    bars["m_next"] = bars["first_minute"] + 5  # première minute de la bougie suivante
    bars.loc[~bars["complete"], ["o", "h", "l", "c", "v", "taker_buy"]] = np.nan
    return bars.reset_index(drop=True)


def _ema(x: pd.Series, span: int) -> pd.Series:
    return x.ewm(span=span, adjust=False, min_periods=span).mean()


def compute(bars: pd.DataFrame, btc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Caractéristiques causales d'une crypto ; « btc » apporte le contexte du marché (mêmes horodatages)."""
    f = bars.copy()
    c, h, l, o, v = (f[k].ffill() for k in ("c", "h", "l", "o", "v"))
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    f["atr"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f["atr_pct"] = f["atr"] / c
    f["ema20"], f["ema50"] = _ema(c, 20), _ema(c, 50)
    e1h, e4h = _ema(c, 600), _ema(c, 2400)  # moyennes 50 sur 1 heure et 50 sur 4 heures, approchées en 5 minutes
    f["trend_1h"], f["trend_4h"] = c / e1h - 1, c / e4h - 1
    f["slope_1h"] = e1h / e1h.shift(12) - 1
    e1d = _ema(c, 20 * DAY)
    f["trend_1d"], f["slope_1d"] = c / e1d - 1, e1d / e1d.shift(DAY) - 1
    for name, n in (("ret_30m", 6), ("ret_1h", 12), ("ret_4h", 48)):
        f[name] = c / c.shift(n) - 1
    lv = np.log1p(v)
    f["vol_z"] = (lv - lv.rolling(DAY, min_periods=DAY // 2).mean().shift(1)) / lv.rolling(DAY, min_periods=DAY // 2).std().shift(1)
    tb = f["taker_buy"].ffill()
    f["taker_ratio"] = tb.rolling(3).sum() / v.rolling(3).sum().replace(0, np.nan)
    pv = (c * v).rolling(DAY, min_periods=DAY // 2).sum() / v.rolling(DAY, min_periods=DAY // 2).sum()
    f["vwap"] = pv
    f["vwap_dist"] = (c - pv) / f["atr"]
    f["hi24"], f["lo24"] = h.rolling(24).max().shift(1), l.rolling(24).min().shift(1)
    height = (f["hi24"] - f["lo24"]).replace(0, np.nan)
    f["range_pos"] = (c - f["lo24"]) / height
    f["range_height"] = height / f["atr"]
    std20 = c.rolling(20).std()
    f["bb_mid"], f["bb_up"] = c.rolling(20).mean(), c.rolling(20).mean() + 2 * std20
    width = std20 / c
    f["squeeze"] = width / width.rolling(2 * DAY, min_periods=DAY).min().replace(0.0, np.nan)  # cours figé : pas de mesure
    r = np.log(c).diff()
    f["realized_vol"] = r.rolling(DAY, min_periods=DAY // 2).std()
    hour = (f["t"] // 3_600_000) % 24
    f["hour_sin"], f["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    if btc is None:
        btc = f
    ctx = btc.set_index("t")[["trend_1h", "ret_1h", "ret_4h"]].rename(
        columns={"trend_1h": "btc_trend_1h", "ret_1h": "btc_ret_1h", "ret_4h": "btc_ret_4h"})
    f = f.join(ctx, on="t")
    f[FEATURES + DAILY] = f[FEATURES + DAILY].replace([np.inf, -np.inf], np.nan)  # jamais de valeur infinie
    f.loc[~f["complete"], FEATURES + DAILY] = np.nan
    return f
