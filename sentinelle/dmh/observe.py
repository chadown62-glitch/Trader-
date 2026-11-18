"""Observations de marché horodatées et leur contrôle qualité.

- chaque observation porte l'heure UTC de réception (ms), l'heure de la source si elle en donne une, sa source
  et l'empreinte SHA-256 de la réponse brute ;
- un carnet incohérent (prix ou quantités non finis ou négatifs, côtés non triés, niveaux en double, meilleur
  achat ≥ meilleure vente, heure de source dans le futur) est REJETÉ, jamais corrigé ;
- une observation trop ancienne n'est pas utilisable ; aucun prix manquant n'est reconstruit ;
- les doublons et les retours en arrière d'une même série sont détectés ;
- plusieurs sources indépendantes sont comparées : une source qui s'écarte du consensus est écartée, et un prix
  de réseau décentralisé trop éloigné des plateformes centralisées bloque la route (token ou prix manipulé).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

CLOCK_SKEW_MS = 2_000  # tolérance entre l'horloge locale et celle des sources
MAX_AGE_MS = {"book": 5_000, "dex_quote": 15_000, "gas": 30_000, "fx": 10_000, "token_risk": 3_600_000}


class InvalidData(ValueError):
    pass


def _levels(raw, side: str) -> tuple[tuple[float, float], ...]:
    out = []
    for lvl in raw or ():
        try:
            p, q = float(lvl[0]), float(lvl[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise InvalidData(f"niveau de carnet illisible ({side})") from exc
        if not (math.isfinite(p) and math.isfinite(q)) or p <= 0 or q < 0:
            raise InvalidData(f"prix ou quantité invalide ({side})")
        if q > 0:
            out.append((p, q))
    if not out:
        raise InvalidData(f"carnet vide côté {side}")
    prices = [p for p, _ in out]
    if len(set(prices)) != len(prices):
        raise InvalidData(f"niveaux en double ({side})")
    ordered = sorted(prices, reverse=(side == "bids"))
    if prices != ordered:
        raise InvalidData(f"côté {side} non trié")
    return tuple(out)


@dataclass(frozen=True)
class Book:
    venue: str
    pair: str  # paire canonique « BASE/QUOTE »
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    received_ms: int
    source_ms: int | None
    sha256: str
    latency_ms: float = 0.0
    evidence: dict = field(default_factory=dict, compare=False)

    @classmethod
    def build(cls, venue, pair, bids, asks, *, received_ms, source_ms=None, sha256="", latency_ms=0.0, evidence=None) -> "Book":
        b, a = _levels(bids, "bids"), _levels(asks, "asks")
        if b[0][0] >= a[0][0]:
            raise InvalidData(f"carnet croisé sur {venue} {pair} (achat {b[0][0]} ≥ vente {a[0][0]})")
        if source_ms is not None and source_ms > received_ms + CLOCK_SKEW_MS:
            raise InvalidData(f"heure de source dans le futur ({venue}) : horloges désynchronisées")
        return cls(venue, pair, b, a, int(received_ms), None if source_ms is None else int(source_ms), sha256, latency_ms, evidence or {})

    @property
    def bid(self) -> float:
        return self.bids[0][0]

    @property
    def ask(self) -> float:
        return self.asks[0][0]

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def half_spread_bps(self) -> float:
        return (self.ask - self.bid) / 2 / self.mid * 1e4

    def depth_quote(self, side: str, levels: int = 10) -> float:
        rows = self.asks if side == "buy" else self.bids
        return sum(p * q for p, q in rows[:levels])

    def age_ms(self, now_ms: int) -> int:
        return max(now_ms - self.received_ms, now_ms - (self.source_ms if self.source_ms is not None else self.received_ms))

    def fresh(self, now_ms: int, max_age_ms: int = MAX_AGE_MS["book"]) -> bool:
        return -CLOCK_SKEW_MS <= now_ms - self.received_ms and self.age_ms(now_ms) <= max_age_ms

    def to_dict(self, levels: int = 10) -> dict:
        return {"venue": self.venue, "pair": self.pair, "bids": [list(x) for x in self.bids[:levels]],
                "asks": [list(x) for x in self.asks[:levels]], "received_ms": self.received_ms, "source_ms": self.source_ms,
                "sha256": self.sha256, "latency_ms": round(self.latency_ms, 1), "mid": self.mid,
                "half_spread_bps": round(self.half_spread_bps, 4)}


class SeriesGuard:
    """Doublons, retours en arrière et trous d'une série d'observations (par source et instrument)."""

    def __init__(self, expected_every_ms: int | None = None, gap_factor: float = 3.0):
        self.expected, self.factor = expected_every_ms, gap_factor
        self.last: dict[tuple, tuple[int, str]] = {}
        self.events: list[dict] = []

    def check(self, key: tuple, ts_ms: int, sha256: str) -> str:
        prev = self.last.get(key)
        verdict = "ok"
        if prev is not None:
            if sha256 and sha256 == prev[1] and ts_ms == prev[0]:
                verdict = "doublon"
            elif ts_ms < prev[0]:
                verdict = "retour_en_arriere"
            elif self.expected and ts_ms - prev[0] > self.expected * self.factor:
                verdict = "trou"
        if verdict != "retour_en_arriere":
            self.last[key] = (ts_ms, sha256)
        if verdict != "ok":
            self.events.append({"key": list(key), "ts_ms": ts_ms, "verdict": verdict})
        return verdict


@dataclass(frozen=True)
class Consensus:
    pair: str
    median_mid: float | None
    sources: dict[str, float]
    deviations_bps: dict[str, float]
    outliers: tuple[str, ...]
    usable: tuple[str, ...]
    status: str  # OK | SOURCE_UNIQUE | INCOHERENT | INDISPONIBLE

    def to_dict(self) -> dict:
        return {"pair": self.pair, "median_mid": self.median_mid, "sources": self.sources,
                "deviations_bps": {k: round(v, 2) for k, v in self.deviations_bps.items()}, "outliers": list(self.outliers),
                "usable": list(self.usable), "status": self.status}


def consensus(pair: str, books: list[Book], now_ms: int, *, max_dev_bps: float = 50.0, max_age_ms: int = MAX_AGE_MS["book"]) -> Consensus:
    fresh = {b.venue: b.mid for b in books if b.pair == pair and b.fresh(now_ms, max_age_ms)}
    if not fresh:
        return Consensus(pair, None, {}, {}, (), (), "INDISPONIBLE")
    med = statistics.median(fresh.values())
    dev = {v: (m / med - 1) * 1e4 for v, m in fresh.items()}
    outliers = tuple(sorted(v for v, d in dev.items() if abs(d) > max_dev_bps))
    usable = tuple(sorted(v for v in fresh if v not in outliers))
    if len(fresh) == 1:
        status = "SOURCE_UNIQUE"
    elif len(fresh) == 2 and outliers:
        status, usable = "INCOHERENT", ()  # deux sources qui se contredisent : impossible de savoir laquelle croire
    elif len(usable) < 2:
        status = "INCOHERENT"
    else:
        status = "OK"
    return Consensus(pair, med, fresh, dev, outliers, usable, status)


def dex_vs_reference(dex_price: float, reference: float | None, max_dev_bps: float = 150.0) -> tuple[bool, float | None]:
    """(acceptable, écart en pb) d'un prix obtenu sur la chaîne face au consensus des plateformes centralisées."""
    if reference is None or not (dex_price > 0 and math.isfinite(dex_price)):
        return False, None
    d = (dex_price / reference - 1) * 1e4
    return abs(d) <= max_dev_bps, d
