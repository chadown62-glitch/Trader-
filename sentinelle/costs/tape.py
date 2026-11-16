"""Ruban de transactions publiques et estimation du meilleur prix acheteur / vendeur à un instant donné.

Binance publie, pour chaque transaction agrégée, son prix, sa quantité, son instant et le côté de
l'initiateur (``m`` : l'acheteur était le teneur de marché, donc la transaction a été déclenchée par
un VENDEUR au prix acheteur). Les carnets historiques n'étant pas publiés, le meilleur prix vendeur
(ask) et le meilleur prix acheteur (bid) à l'instant ``t`` sont ESTIMÉS par :

    ask(t) ≈ prix de la dernière transaction déclenchée par un acheteur avant t
    bid(t) ≈ prix de la dernière transaction déclenchée par un vendeur avant t

dans une fenêtre de ``lookback`` millisecondes. L'estimation est refusée (None) s'il manque un côté.
Quand le prix a bougé entre les deux transactions, l'estimation peut être « croisée » (prix vendeur
estimé sous le prix acheteur estimé) : elle est GARDÉE et signalée, car l'écart brut reste sans biais
en moyenne (le sens du mouvement entre les deux transactions est aléatoire), alors qu'écarter ces cas
retirerait justement les moments agités. Son erreur est mesurée contre des carnets observés (sondes) :
c'est ce qui autorise à l'employer, pas une supposition.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Tape:
    symbol: str
    ts: np.ndarray  # millisecondes UTC, non décroissant
    price: np.ndarray
    qty: np.ndarray
    buyer_initiated: np.ndarray  # True : transaction au prix vendeur (un acheteur a pris la liquidité)
    sha256: str = ""
    source: str = "binance aggTrades (public)"

    def __post_init__(self):
        n = len(self.ts)
        if not (len(self.price) == len(self.qty) == len(self.buyer_initiated) == n):
            raise ValueError("Ruban incohérent")
        if n and (np.any(np.diff(self.ts) < 0) or np.any(self.price <= 0) or np.any(self.qty <= 0)
                  or not np.isfinite(self.price).all() or not np.isfinite(self.qty).all()):
            raise ValueError("Ruban non chronologique ou valeurs invalides")

    def __len__(self) -> int:
        return len(self.ts)

    def window(self, lo: int, hi: int) -> "Tape":
        """Transactions d'instant dans ``[lo, hi)``."""
        a, b = np.searchsorted(self.ts, lo, side="left"), np.searchsorted(self.ts, hi, side="left")
        return Tape(self.symbol, self.ts[a:b], self.price[a:b], self.qty[a:b], self.buyer_initiated[a:b], self.sha256, self.source)

    def before(self, t: int) -> "Tape":
        """Tout ce qui est strictement antérieur à ``t`` : ce qu'un observateur pouvait connaître."""
        b = np.searchsorted(self.ts, t, side="left")
        return Tape(self.symbol, self.ts[:b], self.price[:b], self.qty[:b], self.buyer_initiated[:b], self.sha256, self.source)


def from_binance(rows: list[dict], symbol: str, *, raw: bytes | None = None) -> Tape:
    """Transactions agrégées de Binance (``a``, ``p``, ``q``, ``T``, ``m``) ; doublons d'identifiant retirés."""
    seen, keep = set(), []
    for r in rows:
        if r["a"] in seen:
            continue
        seen.add(r["a"])
        keep.append(r)
    keep.sort(key=lambda r: (int(r["T"]), int(r["a"])))
    digest = hashlib.sha256(raw).hexdigest() if raw is not None else hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()
    return Tape(symbol, np.array([int(r["T"]) for r in keep], dtype=np.int64), np.array([float(r["p"]) for r in keep]),
                np.array([float(r["q"]) for r in keep]), np.array([not bool(r["m"]) for r in keep]), digest)


@dataclass(frozen=True)
class Quote:
    t: int
    bid: float
    ask: float
    bid_age_ms: int
    ask_age_ms: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def half_spread_bps(self) -> float:
        """Demi-écart brut estimé (peut être négatif si l'estimation est croisée)."""
        return (self.ask - self.bid) / 2 / self.mid * 1e4

    @property
    def crossed(self) -> bool:
        return self.ask <= self.bid


def quote_at(tape: Tape, t: int, *, lookback_ms: int = 60_000) -> Quote | None:
    """Meilleurs prix estimés juste avant ``t`` ; None si l'information manque (aucune supposition)."""
    w = tape.window(t - lookback_ms, t)
    if not len(w):
        return None
    buys, sells = np.flatnonzero(w.buyer_initiated), np.flatnonzero(~w.buyer_initiated)
    if not len(buys) or not len(sells):
        return None
    ia, ib = buys[-1], sells[-1]
    ask, bid = float(w.price[ia]), float(w.price[ib])
    return Quote(t, bid, ask, int(t - w.ts[ib]), int(t - w.ts[ia]))


def mid_path(tape: Tape, times: list[int], *, lookback_ms: int = 60_000) -> list[float | None]:
    out = []
    for t in times:
        q = quote_at(tape, t, lookback_ms=lookback_ms)
        out.append(None if q is None else q.mid)
    return out
