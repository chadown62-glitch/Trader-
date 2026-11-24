"""Étude des familles de signaux (signaux-v1) : chercher un avantage explicable, famille par famille (simulation exclusive).

Protocole consigné dans research/scalp-v1/trials.jsonl avant tout calcul (« signaux-v1 », commit 7cf2654) :
- univers défini à la date : chaque mois, les 10 paires USDT les plus échangées des 30 jours précédents parmi 42 candidates,
  cryptos disparues comprises, séries recotées coupées (recherche-v1b) ;
- 11 familles de signaux aux réglages standard fixés d'avance (élan, tendance, ligne de tendance, cassure confirmée,
  support, volume et pression acheteuse, régime, élan ajusté du risque, volatilité, multi-horizon, faux signaux filtrés),
  testées seules puis comme filtre de l'élan, avant toute combinaison ;
- signal calculé sur les clôtures jusqu'à d − 1, position à l'ouverture de d, rendement d'ouverture à ouverture, frais ;
- stabilité définie d'avance : rendement hebdomadaire géométrique, fenêtres glissantes de 52 semaines, semaines positives,
  pire baisse, pire semaine, séries de pertes, durée sous le plus haut, marchés baissiers, petit capital, références.
Toutes les données jusqu'au 22 novembre 2026 ont déjà été consultées : rien ici n'est une période aveugle.
Les actualités ne sont pas testables sur l'historique (archive depuis le 23 novembre 2026 seulement).

Lancement : python -m sentinelle.scalp.signaux
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import actifs, moteur, recherche
from .data import months, read_month

DAY_MS = moteur.DAY_MS
TOP_N = 10
MIN_HISTORY = 60
COST = 0.00075 + 0.0005
STRESS = 0.0025
SMALL_CRYPTO = 0.0010 + 0.0005
SMALL_ETF_FIXED_EUR, SMALL_ETF_SPREAD, SMALL_CAPITAL = 1.0, 0.0005, 1000.0
REFERENCE = moteur.WEEKLY_REFERENCE
FIRST, LAST = "2018-01-01", "2026-11-22"
WARMUP = "2017-08-01"
FAMILIES = ["F1_momentum", "F2_tendance", "F3_ligne_de_tendance", "F4_cassure_confirmee", "F5_support",
            "F6_volume_pression", "F7_regime", "F8_momentum_ajuste", "F9_volatilite_calme", "F10_multi_horizon",
            "F11_faux_signaux_filtres"]
BOOT_BLOCK, BOOT_DRAWS = 20, 2000


# --- Données ------------------------------------------------------------------------------------------------------------

def load_bars(symbol: str, cache_dir, client) -> tuple[pd.DataFrame, dict]:
    """Bougies journalières complètes (o, h, l, c, v, achats au marché), cryptos retirées comprises, coupées à une
    recotation ; avec l'empreinte publiée de chaque archive lue."""
    prev = (pd.Timestamp(LAST).replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    span = recherche.published_range(symbol, prev, client)
    if span is None:
        return pd.DataFrame(), {"symbol": symbol, "listed": None, "files": []}
    first, last = span
    periods = months(first, last)
    if last == prev:
        stop = pd.Timestamp(LAST)
        periods += [(stop.replace(day=1) + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(stop.day)]
    frames, files, missing = [], [], []
    for p in periods:
        try:
            path = moteur._archive(symbol, p, cache_dir, client)
        except httpx.HTTPStatusError:
            missing.append(p)
            continue
        frames.append(read_month(path))
        sidecar = path.with_name(path.name + ".CHECKSUM")
        files.append([p, sidecar.read_text(encoding="utf-8").split()[0]])
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.assign(day=raw["t"].astype("int64") // DAY_MS).drop_duplicates("day").set_index("day").sort_index()
    bars = raw.reindex(np.arange(raw.index[0], raw.index[-1] + 1))[["o", "h", "l", "c", "v", "taker_buy"]].astype(float)
    bars, cut = recherche.cut_at_break(bars)
    return bars, {"symbol": symbol, "listed": first, "last_month": last, "missing_periods": missing,
                  "days_without_quote": int(bars["c"].isna().sum()), "files": files,
                  "cut_at": str(pd.to_datetime(cut * DAY_MS, unit="ms").date()) if cut else None}


def panel(loaded: dict) -> dict:
    keep = {s: b for s, (b, _) in loaded.items() if len(b)}
    grid = pd.RangeIndex(moteur._day(WARMUP), moteur._day(LAST) + 1)
    return {k: pd.DataFrame({s: b[k] for s, b in keep.items()}).reindex(grid) for k in ("o", "h", "l", "c", "v", "taker_buy")}


def members(p: dict, n: int = TOP_N) -> pd.DataFrame:
    """Univers à la date : top n du volume échangé (connu avant le mois), avec au moins 60 jours d'historique."""
    top = recherche.point_in_time_members(p["v"] * p["c"], n)
    history = p["c"].notna().cumsum() >= MIN_HISTORY
    return top & history


# --- Familles de signaux (ligne t : données jusqu'à la clôture de t ; utilisées à l'ouverture de t + 1) ---------------

def _ema(x: pd.DataFrame, n: int) -> pd.DataFrame:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def _trendline(c: pd.DataFrame, n: int = 30) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pente et R² de la régression du log du cours sur les n derniers jours (fenêtre glissante, sans regard en avant)."""
    y = np.log(c)
    t = pd.DataFrame(np.repeat(np.arange(len(c), dtype=float)[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)
    t = t.where(y.notna())
    r = lambda x: x.rolling(n, min_periods=n).mean()  # noqa: E731
    cov = r(t * y) - r(t) * r(y)
    var_t, var_y = r(t * t) - r(t) ** 2, r(y * y) - r(y) ** 2
    slope = cov / var_t
    r2 = (cov ** 2) / (var_t * var_y)
    return slope, r2


def _breakout_state(c: np.ndarray, level: np.ndarray, exit_level: np.ndarray, allow: np.ndarray) -> np.ndarray:
    """Machine à états de la cassure confirmée : entrée quand deux clôtures dépassent le même niveau de résistance
    (et que « allow » l'autorise), sortie sur clôture sous le plus bas de 10 jours."""
    on = np.zeros_like(c, dtype=bool)
    for j in range(c.shape[1]):
        state = False
        for i in range(1, c.shape[0]):
            if state:
                state = not (c[i, j] < exit_level[i, j])
            elif np.isfinite(level[i, j]) and c[i, j] > level[i, j] and c[i - 1, j] > level[i, j] and allow[i, j]:
                state = True
            on[i, j] = state and np.isfinite(c[i, j])
    return on


def families(p: dict, btc: str = "BTCUSDT") -> dict[str, pd.DataFrame]:
    o, h, lo, c, v, tb = (p[k] for k in ("o", "h", "l", "c", "v", "taker_buy"))
    ret = np.log(c).diff()
    vol20, vol120 = ret.rolling(20).std(), ret.rolling(120).std()
    r28 = c / c.shift(28) - 1
    ema20, ema50 = _ema(c, 20), _ema(c, 50)
    f = {}
    f["F1_momentum"] = r28 > 0
    f["F2_tendance"] = (c > ema50) & (ema50 > ema50.shift(5))
    slope, r2 = _trendline(c)
    f["F3_ligne_de_tendance"] = (slope > 0) & (r2 >= 0.6)
    level = h.rolling(20, min_periods=20).max().shift(2)  # les 20 jours qui précèdent la veille (jour de cassure)
    exit_level = lo.rolling(10, min_periods=10).min().shift(1)
    allow_all = np.ones(c.shape, dtype=bool)
    f["F4_cassure_confirmee"] = pd.DataFrame(_breakout_state(c.to_numpy(), level.to_numpy(), exit_level.to_numpy(), allow_all),
                                             index=c.index, columns=c.columns)
    touch = f["F2_tendance"] & (lo <= ema20) & (c > ema20)
    f["F5_support"] = touch.astype(float).rolling(5, min_periods=1).max().astype(bool)
    pressure = tb.rolling(7).sum() / v.rolling(7).sum()
    f["F6_volume_pression"] = (pressure > 0.5) & (v.rolling(7).mean() > v.rolling(30).mean())
    sma200 = c[btc].rolling(200, min_periods=200).mean()
    regime = (c[btc] > sma200).fillna(False)
    f["F7_regime"] = pd.DataFrame(np.repeat(regime.to_numpy()[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns) & c.notna()
    f["F8_momentum_ajuste"] = (r28 / (vol20 * np.sqrt(28))) > 1
    f["F9_volatilite_calme"] = vol20 < vol120
    days = c.index.to_numpy()
    sunday = ((days + 3) % 7) == 6  # dernier jour (dimanche) de la semaine du lundi au dimanche
    weekly = c[sunday]
    wema = _ema(weekly, 10)
    wok = (weekly > wema).astype(float).reindex(c.index).ffill().fillna(0.0).astype(bool)
    f["F10_multi_horizon"] = wok & f["F2_tendance"]
    f["F11_faux_signaux_filtres"] = pd.DataFrame(
        _breakout_state(c.to_numpy(), level.to_numpy(), exit_level.to_numpy(), f["F6_volume_pression"].to_numpy()),
        index=c.index, columns=c.columns)
    return {k: v.fillna(False).astype(bool) for k, v in f.items()}


# --- Portefeuille et mesures --------------------------------------------------------------------------------------------

def asset_returns(p: dict) -> pd.DataFrame:
    """Rendement d'ouverture à ouverture ; le dernier jour d'une série (retrait ou coupure) se solde à la clôture."""
    o, c = p["o"], p["c"]
    nxt = o.shift(-1)
    return (nxt.where(nxt.notna(), c) / o - 1).where(o.notna())


def positions(signal: pd.DataFrame, member: pd.DataFrame, p: dict, vol_target: float | None = None):
    """À l'ouverture de d : actifs à détenir (signal vrai à la clôture de d − 1, dans l'univers, cotés) et mise d'entrée
    (1/10 du capital, réduite à volatilité visée si demandé, volatilité connue la veille)."""
    on = signal.shift(1, fill_value=False) & member & p["o"].notna()
    size = pd.DataFrame(1.0 / TOP_N, index=on.index, columns=on.columns)
    if vol_target:
        vol = (np.log(p["c"]).diff().rolling(20).std() * np.sqrt(365)).shift(1)
        size = size * (vol_target / vol).clip(upper=1.0).fillna(0.0)
    return on, size


def simulate(on: pd.DataFrame, size: pd.DataFrame, r: pd.DataFrame, cost: float) -> tuple[pd.Series, np.ndarray]:
    """Rendement journalier net. Une position entre à sa mise d'entrée, puis vit sa vie (pas de rééquilibrage
    quotidien) jusqu'à la fin du signal ; une entrée ne dépasse jamais la trésorerie disponible ; frais sur chaque
    montant acheté ou vendu. Renvoie aussi les poids détenus chaque jour (part du capital)."""
    ON, SZ, R = on.to_numpy(), size.fillna(0.0).to_numpy(), r.fillna(0.0).to_numpy()
    out, W = np.zeros(len(ON)), np.zeros(ON.shape, dtype=float)
    held = np.zeros(ON.shape[1])
    for i in range(len(ON)):
        keep = np.where(ON[i] & (held > 0), held, 0.0)
        new = ON[i] & (held <= 0) & (SZ[i] > 0)
        want = np.where(new, SZ[i], 0.0)
        room = max(0.0, 1.0 - keep.sum())
        if want.sum() > room:
            want *= room / want.sum()
        w = keep + want
        turnover = np.abs(w - held).sum()
        gross = float(w @ R[i])
        out[i] = gross - cost * turnover
        W[i] = w
        total = 1.0 + gross
        held = w * (1 + R[i]) / total if total > 0 else np.zeros_like(held)
    return pd.Series(out, index=on.index), W


def net(signal, member, p, r, cost, vol_target=None):
    on, size = positions(signal, member, p, vol_target)
    return simulate(on, size, r, cost)


def edge(signal: pd.DataFrame, member: pd.DataFrame, r: pd.DataFrame, seed: int = 0) -> dict:
    """Écart quotidien moyen (avant frais) entre les actifs en signal et l'univers, intervalle à 95 % par
    rééchantillonnage par blocs circulaires."""
    on = signal.shift(1, fill_value=False) & member & r.notna()
    univ = (member & r.notna())
    diff = (r.where(on).mean(axis=1) - r.where(univ).mean(axis=1)).dropna()
    x = diff.to_numpy()
    if len(x) < 2 * BOOT_BLOCK:
        return {"days": int(len(x)), "mean_daily_pct": None, "ci95_daily_pct": [None, None]}
    rng = np.random.default_rng(seed)
    n, b = len(x), BOOT_BLOCK
    starts = rng.integers(0, n, size=(BOOT_DRAWS, -(-n // b)))
    idx = (starts[:, :, None] + np.arange(b)[None, None, :]).reshape(BOOT_DRAWS, -1)[:, :n] % n
    means = x[idx].mean(axis=1)
    return {"days": int(n), "mean_daily_pct": float(100 * x.mean()),
            "ci95_daily_pct": [float(100 * np.percentile(means, 2.5)), float(100 * np.percentile(means, 97.5))],
            "annualized_pct": float(100 * x.mean() * 365)}


def timing_edge(regime: pd.Series, member: pd.DataFrame, r: pd.DataFrame, seed: int = 0) -> dict:
    """Diagnostic a posteriori pour un filtre de marché (F7) : l'écart transversal est nul par construction, car le filtre
    allume tout l'univers à la fois. Mesure ici l'écart temporel : rendement moyen de l'univers les jours où le filtre
    était vrai la veille, moins les autres jours, avant frais, intervalle par blocs circulaires."""
    day = r.where(member & r.notna()).mean(axis=1)
    on = regime.shift(1, fill_value=False).reindex(day.index).fillna(False).to_numpy()
    x, ok = day.to_numpy(), day.notna().to_numpy()
    x, on = x[ok], on[ok]
    if on.all() or not on.any():
        return {"days_on": int(on.sum()), "days_off": int((~on).sum()), "diff_daily_pct": None, "ci95_daily_pct": [None, None]}
    rng = np.random.default_rng(seed)
    n, b = len(x), BOOT_BLOCK
    starts = rng.integers(0, n, size=(BOOT_DRAWS, -(-n // b)))
    idx = (starts[:, :, None] + np.arange(b)[None, None, :]).reshape(BOOT_DRAWS, -1)[:, :n] % n
    xs, ons = x[idx], on[idx]
    diffs = np.nanmean(np.where(ons, xs, np.nan), axis=1) - np.nanmean(np.where(~ons, xs, np.nan), axis=1)
    return {"days_on": int(on.sum()), "days_off": int((~on).sum()),
            "diff_daily_pct": float(100 * (x[on].mean() - x[~on].mean())),
            "ci95_daily_pct": [float(100 * np.nanpercentile(diffs, 2.5)), float(100 * np.nanpercentile(diffs, 97.5))],
            "note": "diagnostic a posteriori : ne change pas la réussite préenregistrée"}


def stability(r: pd.Series, btc_close: pd.Series | None = None) -> dict:
    """Définition préenregistrée de la stabilité, sur le rendement journalier net (index : numéro du jour UTC)."""
    days = r.index.to_numpy()
    eq = np.cumprod(1 + r.to_numpy())
    week = (days + 3) // 7
    last = np.r_[week[1:] != week[:-1], True]
    weq = eq[last]
    wr = weq / np.r_[1.0, weq[:-1]] - 1
    n = len(wr)
    geo = float(100 * (eq[-1] ** (1 / n) - 1))
    roll = (weq[52:] / weq[:-52]) ** (1 / 52) - 1 if n > 52 else np.array([])
    flat = np.abs(wr) < 1e-12
    neg = wr < -1e-12
    streak = best = 0
    for x in neg:
        streak = streak + 1 if x else 0
        best = max(best, streak)
    peak = np.maximum.accumulate(np.r_[1.0, weq])[1:]
    under = weq < peak - 1e-12
    uw = cur = 0
    for x in under:
        cur = cur + 1 if x else 0
        uw = max(uw, cur)
    dd = float(100 * np.max(1 - eq / np.maximum.accumulate(np.r_[1.0, eq])[1:]))
    years = pd.to_datetime(days * DAY_MS, unit="ms").year.to_numpy()
    out = {"geometric_week_pct": geo, "annual_pct": float(100 * (eq[-1] ** (365.25 / len(days)) - 1)),
           "return_pct": float(100 * (eq[-1] - 1)), "weeks": int(n),
           "positive_weeks": int((wr > 1e-12).sum()), "negative_weeks": int(neg.sum()), "flat_weeks": int(flat.sum()),
           "positive_share_of_nonflat": float((wr > 1e-12).sum() / max(1, (~flat).sum())),
           "max_drawdown_pct": dd, "worst_week_pct": float(100 * wr.min()), "longest_losing_streak_weeks": int(best),
           "longest_underwater_weeks": int(uw),
           "rolling52": ({"min": float(100 * roll.min()), "p10": float(100 * np.percentile(roll, 10)),
                          "p25": float(100 * np.percentile(roll, 25)), "median": float(100 * np.median(roll)),
                          "p75": float(100 * np.percentile(roll, 75)), "p90": float(100 * np.percentile(roll, 90)),
                          "max": float(100 * roll.max()), "share_at_reference": float((roll >= REFERENCE / 100).mean()),
                          "share_positive": float((roll > 0).mean()), "windows": int(len(roll))} if len(roll) else None),
           "years": {int(y): round(float(100 * (np.prod(1 + r.to_numpy()[years == y]) - 1)), 2) for y in np.unique(years)}}
    if btc_close is not None:
        b = btc_close.ffill()
        bear = (b < 0.7 * b.cummax()).reindex(r.index).fillna(False).to_numpy()  # plus haut pris sur tout l'historique
        out["bear_days"] = int(bear.sum())
        out["bear_return_pct"] = float(100 * (np.prod(1 + r.to_numpy()[bear]) - 1)) if bear.any() else None
    return out


def passes(e: dict, s: dict, bench: dict) -> bool:
    """Critère préenregistré de réussite d'une famille."""
    if e["ci95_daily_pct"][0] is None or e["ci95_daily_pct"][0] <= 0:
        return False
    calmar = s["annual_pct"] / max(1e-9, s["max_drawdown_pct"])
    bench_calmar = bench["annual_pct"] / max(1e-9, bench["max_drawdown_pct"])
    positive_years = sum(v > 0 for k, v in s["years"].items() if k <= 2026)
    return calmar > bench_calmar and positive_years >= 5


def equal_weight_monthly(member: pd.DataFrame, r: pd.DataFrame, cost: float) -> pd.Series:
    """Univers à poids égaux, ramené à 1/n le 1er de chaque mois (frais du rééquilibrage comptés)."""
    days = member.index.to_numpy()
    month = pd.Index(pd.to_datetime(days * DAY_MS, unit="ms").strftime("%Y-%m"))
    first = np.r_[True, month[1:] != month[:-1]]
    w = pd.DataFrame(0.0, index=member.index, columns=member.columns)
    target = None
    for i, d in enumerate(member.index):
        if first[i] or target is None:
            m = member.loc[d] & r.loc[d].notna()
            target = m.astype(float) / max(1, int(m.sum()))
        w.loc[d] = target
    # entre deux 1er du mois, les poids dérivent : seul le rééquilibrage mensuel paie des frais
    W, R = w.to_numpy(), r.fillna(0.0).to_numpy()
    out, held = np.zeros(len(W)), np.zeros(W.shape[1])
    for i in range(len(W)):
        cur = W[i] if first[i] or i == 0 else held
        turnover = np.abs(cur - held).sum() if (first[i] or i == 0) else 0.0
        gross = float(cur @ R[i])
        out[i] = gross - cost * turnover
        total = 1.0 + gross
        held = cur * (1 + R[i]) / total if total > 0 else np.zeros_like(held)
    return pd.Series(out, index=member.index)


@contextlib.contextmanager
def etf_side_cost(value: float):
    """Coût par côté des fonds pour un calcul (petit capital), rétabli ensuite."""
    old = actifs.SIDE_COST
    actifs.SIDE_COST = value
    try:
        yield
    finally:
        actifs.SIDE_COST = old


def etf_returns(prices: pd.DataFrame, grid: pd.RangeIndex) -> pd.Series:
    eq = moteur.actions_pocket(prices, int(grid[0]) - 400, int(grid[-1]))["equity"].reindex(grid).ffill()
    return eq.pct_change().fillna(0.0)


def mix(rc: pd.Series, rg: pd.Series, share: float = 0.35) -> pd.Series:
    """Poche crypto (35 %) et fonds G2 (65 %) sans transfert : chaque poche évolue seule."""
    eq = share * np.cumprod(1 + rc.to_numpy()) + (1 - share) * np.cumprod(1 + rg.to_numpy())
    return pd.Series(eq / np.r_[1.0, eq[:-1]] - 1, index=rc.index)


def abstention(signal: pd.DataFrame, member: pd.DataFrame, p: dict, regime: pd.DataFrame | None) -> dict:
    """Motif de chaque jour sans position : univers vide, régime défavorable, aucun signal."""
    on = signal.shift(1, fill_value=False) & member & p["o"].notna()
    any_member = member.any(axis=1)
    reg = regime.shift(1, fill_value=False).any(axis=1) if regime is not None else pd.Series(True, index=member.index)
    reason = np.where(on.any(axis=1), "en position", np.where(~any_member, "univers vide",
                      np.where(~reg, "régime BTC défavorable", "aucun signal")))
    s = pd.Series(reason, index=member.index)
    years = pd.to_datetime(member.index.to_numpy() * DAY_MS, unit="ms").year
    return {str(y): s[years == y].value_counts().to_dict() for y in sorted(set(years))}


def trades(W: np.ndarray, index, columns) -> list:
    """Entrées et sorties simulées : [date, actif, « entrée » ou « sortie », part du capital]."""
    rows, prev = [], np.zeros(W.shape[1])
    for i, day in enumerate(index):
        date = str(pd.to_datetime(int(day) * DAY_MS, unit="ms").date())
        for j in np.flatnonzero((W[i] > 0) & (prev <= 0)):
            rows.append([date, columns[j].replace("USDT", ""), "entrée", round(float(W[i, j]), 4)])
        for j in np.flatnonzero((W[i] <= 0) & (prev > 0)):
            rows.append([date, columns[j].replace("USDT", ""), "sortie", round(float(prev[j]), 4)])
        prev = W[i]
    return rows


# --- Programme ----------------------------------------------------------------------------------------------------------

def run(p: dict, prices: pd.DataFrame) -> dict:
    grid = p["c"].index
    first = moteur._day(FIRST)
    sl = lambda x: x.loc[first:]  # noqa: E731
    member = members(p)
    r = asset_returns(p)
    fam = families(p)
    btc = p["c"]["BTCUSDT"]
    bench = {"CASH": pd.Series(0.0, index=grid), "BTC": r["BTCUSDT"].fillna(0.0),
             "univers à poids égaux": equal_weight_monthly(member, r, COST)}
    spy = prices["SPY"].dropna()
    spy_day = pd.Series(spy.to_numpy(), index=[int(d.timestamp()) // 86400 for d in spy.index])
    bench["SPY"] = moteur._calendar(spy_day, int(grid[0]), int(grid[-1]), np.nan).ffill().pct_change().fillna(0.0).reindex(grid)
    b6040 = actifs.benchmark(prices.dropna(subset=["SPY", "IEF"]), {"SPY": 0.6, "IEF": 0.4}, pd.to_datetime(int(grid[0]) * DAY_MS, unit="ms"),
                             pd.to_datetime(int(grid[-1]) * DAY_MS, unit="ms"))
    e6040 = pd.Series({int(pd.Timestamp(d).timestamp()) // 86400: v for d, v in b6040["curve"]})
    bench["60/40"] = moteur._calendar(e6040, int(grid[0]), int(grid[-1]), 1000.0).pct_change().fillna(0.0)
    rg = etf_returns(prices, grid)
    bench["fonds G2"] = rg
    out = {"references": {k: stability(sl(v), btc) for k, v in bench.items()}}
    ew = out["references"]["univers à poids égaux"]
    step1, passed = {}, []
    for name in FAMILIES:
        s = fam[name]
        alone, W = net(s, member, p, r, COST)
        filt, _ = net(s & fam["F1_momentum"], member, p, r, COST)
        e = edge(sl(s), sl(member), sl(r))
        st = stability(sl(alone), btc)
        ok = passes(e, st, ew)
        step1[name] = {"avantage": e, "seule": st, "seule_stress": stability(sl(net(s, member, p, r, STRESS)[0]), btc),
                       "filtre_de_F1": stability(sl(filt), btc),
                       "exposition_moyenne": float(W[grid >= first].sum(axis=1).mean()), "reussite": ok}
        if ok:
            passed.append(name)
    out["etape1"] = step1
    out["familles_reussies"] = passed
    regime_line = fam["F7_regime"]["BTCUSDT"]
    out["diagnostic_a_posteriori_F7_temporel"] = timing_edge(sl(regime_line), sl(member), sl(r))
    combos = {}
    with etf_side_cost(SMALL_ETF_FIXED_EUR / (SMALL_CAPITAL * 0.65 / 3) + SMALL_ETF_SPREAD):
        rg_small = etf_returns(prices, grid)
    if len(passed) >= 2:
        stack = np.stack([fam[n].to_numpy() for n in passed])
        rules = {"C_ET": stack.all(axis=0), "C_VOTE": stack.sum(axis=0) >= -(-len(passed) // 2)}
        regime = fam["F7_regime"] if "F7_regime" in passed else None
        for cname, arr in rules.items():
            sig = pd.DataFrame(arr, index=grid, columns=p["c"].columns)
            for size, vt in (("1/10 par place", None), ("volatilité visée 30 %", 0.30)):
                rc, W = net(sig, member, p, r, COST, vt)
                small, _ = net(sig, member, p, r, SMALL_CRYPTO, vt)
                key = f"{cname}, {size}"
                keep = grid >= first
                combos[key] = {
                    "seule": stability(sl(rc), btc), "seule_stress": stability(sl(net(sig, member, p, r, STRESS, vt)[0]), btc),
                    "seule_petit_capital": stability(sl(small), btc),
                    "avec_fonds_35_65": stability(mix(sl(rc), sl(rg)), btc),
                    "avec_fonds_35_65_petit_capital": stability(mix(sl(small), sl(rg_small)), btc),
                    "exposition_moyenne": float(W[keep].sum(axis=1).mean()),
                    "abstention": abstention(sl(sig), sl(member), {k: sl(v) for k, v in p.items()}, sl(regime) if regime is not None else None),
                    "transactions": trades(W[keep], grid[keep], list(p["c"].columns))}
    out["etape2"] = combos
    target = {}
    for group, rows in (("references", out["references"]), ("combinaisons", {k: v["avec_fonds_35_65"] for k, v in combos.items()}),
                        ("combinaisons seules", {k: v["seule"] for k, v in combos.items()}),
                        ("familles seules", {k: v["seule"] for k, v in step1.items()})):
        for k, s in rows.items():
            hit = (s["geometric_week_pct"] >= REFERENCE and s["rolling52"] and s["rolling52"]["median"] >= REFERENCE
                   and s["max_drawdown_pct"] <= 25)
            target[f"{group} : {k}"] = bool(hit)
    out["cible_atteinte_en_historique"] = target
    out["verdict"] = ("cible atteinte en historique par au moins une stratégie (à confirmer en prospectif)" if any(target.values())
                      else "verdict négatif : aucune stratégie n'atteint +1,30 % par semaine en moyenne géométrique avec une pire baisse ≤ 25 %")
    out["univers_exemple"] = {str(pd.to_datetime(int(d) * DAY_MS, unit="ms").date()): [s.replace("USDT", "") for s in row[row].index]
                              for d, row in member.loc[first:].iterrows()
                              if pd.to_datetime(int(d) * DAY_MS, unit="ms").day == 1 and pd.to_datetime(int(d) * DAY_MS, unit="ms").month == 1}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Étude des familles de signaux (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/signaux-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: load_bars(s, args.cache, client), recherche.CANDIDATES)))
    prices = moteur.etf_prices(args.cache, LAST)
    p = panel(loaded)
    out = run(p, prices)
    etf_manifest = {}
    for t in actifs.UNIVERSE:
        m = Path(args.cache) / "etf" / f"{t}.sha256"
        etf_manifest[t] = m.read_text(encoding="utf-8").strip() if m.exists() else None
    out = {"schema": "scalp-signaux-v1", "status": "simulation ; protocole préenregistré (7cf2654) ; historique déjà consulté, aucune période aveugle",
           "periode": {"de": FIRST, "a": LAST}, "couts": {"base_par_cote": COST, "stress_par_cote": STRESS, "petit_capital_crypto_par_cote": SMALL_CRYPTO,
                                                          "petit_capital_fonds": f"{SMALL_ETF_FIXED_EUR} € par ordre + {SMALL_ETF_SPREAD:.2%}"},
           **out,
           "donnees": {"crypto": [{k: v for k, v in r.items()} for _, r in loaded.values()], "fonds_yahoo": etf_manifest},
           "seconds": round(time.time() - t0)}
    for name, row in out["etape1"].items():
        s, e = row["seule"], row["avantage"]
        print(f"{name:26} avantage {e['mean_daily_pct'] if e['mean_daily_pct'] is None else round(e['mean_daily_pct'], 3)} %/j "
              f"IC {[None if x is None else round(x, 3) for x in e['ci95_daily_pct']]} | seule {s['geometric_week_pct']:+.2f} %/sem géo, "
              f"{s['annual_pct']:+.1f} %/an, baisse {s['max_drawdown_pct']:.0f} % | filtre F1 {row['filtre_de_F1']['geometric_week_pct']:+.2f} | "
              f"{'RÉUSSIE' if row['reussite'] else 'échec'}")
    for k, v in out["references"].items():
        print(f"référence {k:22} {v['geometric_week_pct']:+.2f} %/sem géo, {v['annual_pct']:+.1f} %/an, baisse {v['max_drawdown_pct']:.0f} %")
    for k, v in out["etape2"].items():
        for part in ("seule", "avec_fonds_35_65", "avec_fonds_35_65_petit_capital"):
            s = v[part]
            print(f"{k:34} {part:32} {s['geometric_week_pct']:+.2f} %/sem géo, {s['annual_pct']:+.1f} %/an, baisse {s['max_drawdown_pct']:.0f} %, "
                  f"sem + {s['positive_weeks']}/{s['weeks']}, 52 sem. médiane {s['rolling52']['median']:+.2f} %")
    print(out["verdict"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "signaux-v1",
                                 "familles_reussies": out["familles_reussies"], "verdict": out["verdict"],
                                 "etape1": {k: {"avantage_jour_pct": v["avantage"]["mean_daily_pct"], "ic95": v["avantage"]["ci95_daily_pct"],
                                                "geo_semaine_pct": v["seule"]["geometric_week_pct"], "baisse_pct": v["seule"]["max_drawdown_pct"],
                                                "reussite": v["reussite"]} for k, v in out["etape1"].items()},
                                 "etape2": {k: {p: {m: v[p][m] for m in ("geometric_week_pct", "annual_pct", "max_drawdown_pct")}
                                                for p in ("seule", "avec_fonds_35_65", "avec_fonds_35_65_petit_capital")} for k, v in out["etape2"].items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
