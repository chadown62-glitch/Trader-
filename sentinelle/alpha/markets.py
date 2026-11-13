"""Marchés synthétiques déterministes, avec vérité cachée et pièges adversariaux.

La vérité (régime, avantage planté) est rangée dans ``Universe.truth`` : elle sert UNIQUEMENT
à rapporter les résultats par régime. ``Universe.prefix`` la retire, et aucun agent ni modèle
ne la reçoit (vérifié par les tests).

Familles :
- ``regimes`` : tendances haussières / baissières (persistance), range (retour à la moyenne),
  calme, volatil ; ``edge`` règle la force de l'avantage planté (0 = aucun) ;
- ``noise`` : marche aléatoire de même volatilité, AUCUN avantage : rien ne doit y paraître rentable ;
- ``regime_shift`` : l'avantage de tendance disparaît et devient du retour à la moyenne sur la fin ;
- pièges : trous de liquidité (écarts énormes), annonces macro avec révisions tardives,
  colonne « fuite » (rendement futur) réservée aux tests de l'équipe rouge.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bars import TF_4H_MS, Bars, Universe

START_TS = 1_609_459_200_000  # 2021-01-01 UTC (date fictive : marché synthétique)
REGIMES = ("trend_up", "trend_down", "range", "calm", "volatile")


def _regime_path(rng: np.random.Generator, n: int, mean_len: int = 160) -> np.ndarray:
    out = np.empty(n, dtype=np.int8)
    i = 0
    while i < n:
        k = int(rng.integers(len(REGIMES)))
        length = max(30, int(rng.geometric(1 / mean_len)))
        out[i:i + length] = k
        i += length
    return out


def synthetic_universe(seed: int, *, kind: str = "regimes", edge: float = 1.0, n_bars: int = 4000,
                       symbols: tuple[str, ...] = ("AAA/EUR", "BBB/EUR", "CCC/EUR"),
                       liquidity_holes: float = 0.01, start_ts: int = START_TS) -> Universe:
    """Univers déterministe (même graine = mêmes bougies, au bit près)."""
    if kind not in ("regimes", "noise", "regime_shift"):
        raise ValueError("famille de marché inconnue")
    if kind == "noise":
        edge = 0.0
    rng = np.random.default_rng(seed)
    regimes = _regime_path(rng, n_bars)
    common = rng.standard_t(5, size=n_bars) / np.sqrt(5 / 3)
    bars, edges = {}, {}
    for k, sym in enumerate(symbols):
        srng = np.random.default_rng([seed, k])
        sigma = np.where(regimes == REGIMES.index("volatile"), 0.018, np.where(regimes == REGIMES.index("calm"), 0.006, 0.011))
        eps = 0.6 * common + 0.8 * srng.standard_t(5, size=n_bars) / np.sqrt(5 / 3)
        logp = np.empty(n_bars)
        p = anchor = np.log(100.0 * (1 + k))
        drift = np.zeros(n_bars)
        shift_at = int(n_bars * 0.6) if kind == "regime_shift" else n_bars
        previous = -1
        for t in range(n_bars):
            reg = REGIMES[regimes[t]]
            if regimes[t] != previous:
                anchor = p  # nouveau régime : le niveau d'équilibre part du prix courant
                previous = regimes[t]
            mu = 0.0
            if reg in ("trend_up", "trend_down") and t < shift_at:
                mu = (1 if reg == "trend_up" else -1) * 0.0016 * edge
            elif reg == "range" or (t >= shift_at and reg in ("trend_up", "trend_down")):
                mu = -0.06 * edge * (p - anchor)
            drift[t] = mu
            p = p + mu + sigma[t] * eps[t]
            logp[t] = p
        close = np.exp(logp)
        prev = np.concatenate([[close[0]], close[:-1]])
        open_ = prev * np.exp(srng.normal(0, 0.0008, n_bars))
        span = sigma * np.abs(srng.normal(0, 0.7, (2, n_bars)))
        high = np.maximum(open_, close) * np.exp(span[0])
        low = np.minimum(open_, close) * np.exp(-span[1])
        volume = np.exp(srng.normal(8, 0.35, n_bars)) * np.where(regimes == REGIMES.index("volatile"), 1.8, 1.0)
        spread = 3.0 + 400 * sigma * np.abs(srng.normal(0, 0.3, n_bars))
        holes = srng.random(n_bars) < liquidity_holes
        spread = np.where(holes, srng.uniform(60, 250, n_bars), spread)  # trous de liquidité
        volume = np.where(holes, volume * 0.1, volume)
        ts = start_ts + TF_4H_MS * np.arange(n_bars, dtype=np.int64)
        bars[sym] = Bars(sym, ts, open_, high, low, close, volume, spread, TF_4H_MS, f"synthetic:{kind}:seed={seed}:edge={edge}")
        edges[sym] = drift
    truth = {"kind": kind, "edge": edge, "seed": seed, "regimes": regimes, "regime_names": REGIMES,
             "drift": edges, "liquidity_holes": liquidity_holes}
    return Universe(bars, truth)


def leaky_feature(universe: Universe, symbol: str) -> np.ndarray:
    """PIÈGE : rendement de la bougie SUIVANTE. Ne doit jamais passer l'audit de causalité."""
    c = universe.bars[symbol].close
    out = np.full(len(c), np.nan)
    out[:-1] = np.log(c[1:] / c[:-1])
    return out


@dataclass(frozen=True)
class MacroEvent:
    """Annonce datée. ``available_at`` : moment où CETTE version est connue (calendrier ou révision)."""
    event_id: str
    version: int
    scheduled_at: int  # ms
    available_at: int  # ms
    impact: str  # "high" | "low"
    actual: float | None  # None tant que non publié
    revision_of: int | None = None


def synthetic_macro_calendar(universe: Universe, seed: int, *, every_bars: int = 42) -> tuple[MacroEvent, ...]:
    """Calendrier synthétique : annonce publiée 7 jours avant, chiffre publié à l'heure, révision 3 jours après.

    Aucun effet de prix n'est planté : l'agent macro ne doit pas « trouver » d'avantage ici,
    il doit seulement respecter les dates de disponibilité (piège : la révision).
    """
    rng = np.random.default_rng([seed, 99])
    ts = universe.ts
    tf = TF_4H_MS
    out = []
    for k, i in enumerate(range(every_bars, len(ts) - 1, every_bars)):
        at = int(ts[i]) + tf // 2
        impact = "high" if rng.random() < 0.4 else "low"
        first = float(np.round(rng.normal(0, 1), 2))
        eid = f"evt-{k:04d}"
        out.append(MacroEvent(eid, 0, at, at - 7 * 86_400_000, impact, None))
        out.append(MacroEvent(eid, 1, at, at, impact, first))
        out.append(MacroEvent(eid, 2, at, at + 3 * 86_400_000, impact, float(np.round(first + rng.normal(0, 0.5), 2)), 1))
    return tuple(out)


def macro_view(events: tuple[MacroEvent, ...], as_of: int) -> dict[str, MacroEvent]:
    """Dernière version de chaque annonce CONNUE à ``as_of`` (jamais une révision future)."""
    view: dict[str, MacroEvent] = {}
    for e in events:
        if e.available_at <= as_of and (e.event_id not in view or e.version > view[e.event_id].version):
            view[e.event_id] = e
    return view
