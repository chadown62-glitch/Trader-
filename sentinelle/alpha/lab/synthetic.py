"""Marchés journaliers synthétiques à vérité connue, pour éprouver le laboratoire lui-même.

- ``noise`` : aucun avantage à trouver (témoin négatif) ;
- ``momentum`` : chaque actif a une dérive cachée et persistante : les gagnants récents continuent
  de gagner (témoin positif pour le momentum transversal) ;
- ``trend`` : le marché entier alterne de longues phases de hausse et de baisse (témoin positif
  pour le suivi de tendance).
Le panneau contient aussi ce qui piège un simulateur naïf : cotations tardives, retraits de la cote
précédés d'un effondrement, suspension de cotation, monnaie stable, volumes inégaux.
"""
from __future__ import annotations

import numpy as np

from .data import Panel, from_series

START_DAY = 17_532  # 2018-01-01


def synthetic_panel(seed: int, *, kind: str = "noise", days: int = 900, assets: int = 30, edge: float = 1.0,
                    delisted: int = 3, late: int = 6, halted: bool = True, stable: bool = True) -> Panel:
    if kind not in ("noise", "momentum", "trend"):
        raise ValueError(kind)
    rng = np.random.default_rng(seed)
    names = ["BTC", "ETH"] + [f"A{k:02d}" for k in range(3, assets + 1)]
    market = rng.normal(0.0, 0.025, days)
    if kind == "trend":
        state, drift = 1.0, np.zeros(days)
        for t in range(days):
            if rng.random() < 1 / 90:
                state = -state
            drift[t] = 0.004 * edge * state
        market = market * 0.6 + drift
    series = {}
    for k, name in enumerate(names):
        beta = 1.0 if k < 2 else rng.uniform(0.7, 1.4)
        idio = rng.normal(0.0, 0.02 if k < 2 else 0.04, days)
        hidden = np.zeros(days)
        if kind == "momentum" and k >= 2:
            state = rng.choice([-1.0, 0.0, 1.0])
            for t in range(days):
                if rng.random() < 1 / 45:
                    state = rng.choice([-1.0, 0.0, 1.0])
                hidden[t] = 0.007 * edge * state
        r = beta * market + idio + hidden
        first, last = 0, days
        if 2 <= k < 2 + late:
            first = int(rng.integers(60, days // 2))
        if k >= len(names) - delisted:
            last = int(rng.integers(days // 2, days - 60))
            r[last - 6:last] -= 0.25  # effondrement avant le retrait de la cote
        close = (20_000.0 if k == 0 else 1_500.0 if k == 1 else float(rng.uniform(0.05, 80.0))) * np.exp(np.cumsum(np.log1p(np.clip(r, -0.6, 2.0))))
        gap = rng.normal(0.0, 0.002, days)
        opens = np.concatenate([[close[0]], close[:-1]]) * (1 + gap)
        span = np.abs(rng.normal(0.0, 0.012, days))
        high, low = np.maximum(opens, close) * (1 + span), np.minimum(opens, close) * (1 - span)
        scale = 5e8 if k == 0 else 2e8 if k == 1 else float(rng.uniform(2e5, 4e7))
        qvol = scale * np.exp(rng.normal(0.0, 0.5, days))
        idx = np.arange(first, last)
        if halted and k == 5:  # suspension de dix jours : la série est coupée en deux
            idx = np.concatenate([np.arange(first, days // 2), np.arange(days // 2 + 10, last)])
        series[name] = {"days": START_DAY + idx, "open": opens[idx], "high": high[idx], "low": low[idx], "close": close[idx], "qvol": qvol[idx]}
    if stable:  # monnaie stable très échangée, non répertoriée : seul le filtre de volatilité l'écarte
        px = 1 + rng.normal(0.0, 0.0003, days)
        series["STBL"] = {"days": START_DAY + np.arange(days), "open": px, "high": px * 1.0002, "low": px * 0.9998, "close": px,
                          "qvol": np.full(days, 9e8)}
    return from_series(series, {"synthetic": {"seed": seed, "kind": kind, "edge": edge}})
