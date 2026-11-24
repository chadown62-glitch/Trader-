"""Poche « actions et fonds indiciels » : allocation par tendance entre grandes classes d'actifs (simulation exclusive).

Règles publiées (M. Faber, « A Quantitative Approach to Tactical Asset Allocation », 2007), fixées avant mesure et non
réglées sur nos données (research/scalp-v1/trials.jsonl) :
- univers : actions américaines (SPY), pays développés hors États-Unis (EFA), pays émergents (EEM), obligations d'État
  américaines à 7-10 ans (IEF) et à plus de 20 ans (TLT), or (GLD), matières premières (DBC), immobilier coté (VNQ) ;
- G1 : à chaque fin de mois, chaque fonds dont le cours ajusté (dividendes compris) dépasse sa moyenne de 10 mois reçoit
  1/8 du capital ; sinon cette part reste en liquidités (rémunérées à 0 %, hypothèse prudente) ;
- G2 : parmi les fonds au-dessus de leur moyenne de 10 mois, les 3 meilleurs selon la moyenne de leurs rendements à 1,
  3, 6 et 12 mois reçoivent 1/3 du capital chacun ;
- exécution au cours de clôture du premier jour de bourse suivant la fin de mois (un jour de décalage), 0,10 % de
  frais et d'écart par côté sur chaque montant échangé.
Données : historique journalier public de Yahoo Finance (cours ajustés), conservé avec son empreinte SHA-256 et sa date.
Aucune connexion à un courtier, aucun ordre.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

UNIVERSE = ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC", "VNQ"]
SIDE_COST = 0.001
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"


def download(ticker: str, cache_dir, *, end_ts: int, client: httpx.Client | None = None) -> pd.Series:
    """Cours de clôture ajustés journaliers (index : date UTC), conservés avec l'empreinte et l'heure de téléchargement."""
    path = Path(cache_dir) / "etf" / f"{ticker}.json"
    if not path.exists():
        own = client is None
        client = client or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (Sentinelle simulation)"})
        try:
            r = client.get(URL.format(t=ticker), params={"period1": 946684800, "period2": end_ts, "interval": "1d",
                                                          "events": "div,splits"})
            r.raise_for_status()
        finally:
            if own:
                client.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        manifest = path.with_suffix(".sha256")
        manifest.write_text(f"{hashlib.sha256(r.content).hexdigest()}  {path.name}  téléchargé {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
                            encoding="utf-8")
    return parse(json.loads(path.read_text(encoding="utf-8")))


def parse(payload: dict) -> pd.Series:
    res = payload["chart"]["result"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
    s = pd.Series(adj, index=idx, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")]


def month_end_signals(prices: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Poids voulus décidés à chaque fin de mois (dernier jour de bourse du mois), sur les seuls cours connus à cette date."""
    monthly = prices.resample("ME").last()
    sma10 = monthly.rolling(10, min_periods=10).mean()
    above = monthly > sma10
    if rule == "G1":
        w = above.astype(float) / len(prices.columns)
    elif rule == "G2":
        score = sum(monthly / monthly.shift(k) - 1 for k in (1, 3, 6, 12)) / 4
        rank = score.where(above).rank(axis=1, ascending=False, method="first")
        w = (rank <= 3).astype(float) / 3
    else:
        raise ValueError(rule)
    w[sma10.isna()] = np.nan  # historique insuffisant : pas de décision
    return w.dropna(how="all")


def run(prices: pd.DataFrame, rule: str, start, end, capital: float = 1000.0) -> dict:
    """Rejeu jour par jour : chaque décision de fin de mois s'exécute au premier jour de bourse suivant."""
    prices = prices.ffill()
    days = prices.loc[start:end].index
    decisions = month_end_signals(prices, rule)
    cash, units, curve, turnover = capital, {t: 0.0 for t in prices.columns}, [], 0.0
    pending = {}
    for when, row in decisions.iterrows():
        nxt = prices.index[prices.index > when]
        if len(nxt):
            pending[nxt[0]] = row.to_numpy(float)
    for d in days:
        px = prices.loc[d]
        if d in pending:
            weights = dict(zip(prices.columns, pending[d]))
            value = cash + sum(u * px[t] for t, u in units.items())
            for t in prices.columns:
                target = value * (weights[t] if np.isfinite(weights[t]) else 0.0)
                trade = target - units[t] * px[t]
                if abs(trade) > 1e-9 and np.isfinite(px[t]):
                    cost = abs(trade) * SIDE_COST
                    units[t] += trade / px[t]
                    cash -= trade + cost
                    turnover += abs(trade)
        curve.append((d, cash + sum(u * px[t] for t, u in units.items() if np.isfinite(px[t]))))
    eq = np.array([capital] + [v for _, v in curve])
    years = (days[-1] - days[0]).days / 365.25
    return {"rule": rule, "curve": curve, "final": float(eq[-1]),
            "return_pct": 100 * (eq[-1] / capital - 1), "annual_pct": 100 * ((eq[-1] / capital) ** (1 / years) - 1),
            "max_drawdown_pct": float(100 * np.max(1 - eq / np.maximum.accumulate(eq))), "turnover": turnover / capital}


def benchmark(prices: pd.DataFrame, weights: dict, start, end, capital: float = 1000.0) -> dict:
    """Référence à poids fixes, rééquilibrée chaque mois (achat-conservation si un seul fonds)."""
    w = pd.DataFrame([weights] * len(prices.resample("ME").last()), index=prices.resample("ME").last().index)
    w = w.reindex(columns=prices.columns).fillna(0.0)
    sub = prices.ffill()[list(weights)]
    days = sub.loc[start:end].index
    units, cash, curve = {t: 0.0 for t in weights}, capital, []
    month = None
    for d in days:
        px = sub.loc[d]
        if month != (d.year, d.month):
            month = (d.year, d.month)
            value = cash + sum(u * px[t] for t, u in units.items())
            for t, wt in weights.items():
                trade = value * wt - units[t] * px[t]
                units[t] += trade / px[t]
                cash -= trade + abs(trade) * SIDE_COST
        curve.append((d, cash + sum(u * px[t] for t, u in units.items())))
    eq = np.array([capital] + [v for _, v in curve])
    years = (days[-1] - days[0]).days / 365.25
    return {"curve": curve, "return_pct": 100 * (eq[-1] / capital - 1), "annual_pct": 100 * ((eq[-1] / capital) ** (1 / years) - 1),
            "max_drawdown_pct": float(100 * np.max(1 - eq / np.maximum.accumulate(eq)))}
