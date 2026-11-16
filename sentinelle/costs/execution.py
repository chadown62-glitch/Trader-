"""Comparaison des méthodes d'exécution sur un ruban de transactions réel : au marché, à cours limité,
teneur (maker) ou preneur (taker), exécutions partielles et ordres non exécutés.

Une décision est prise à l'instant ``t`` (prix de référence : milieu estimé juste avant ``t``). L'ordre
arrive à ``t + latence``. Méthodes :

- ``market`` : l'ordre prend la liquidité. Un achat est servi par les transactions suivantes déclenchées
  par des acheteurs (elles révèlent le prix vendeur), jusqu'à couvrir la quantité ; s'il n'y en a
  aucune dans le délai, l'exécution n'est pas prouvée (``no_evidence``), jamais supposée ;
- ``join`` : ordre à cours limité au meilleur prix acheteur (achat) — il rejoint la file d'attente ;
- ``improve`` : un pas de prix mieux que le meilleur prix, si l'écart le permet — l'ordre est seul
  en tête de file ; sinon il se comporte comme ``join``.

Règles de remplissage d'un ordre à cours limité d'achat au prix L (vente : symétrique) :

- ``conservative`` : rempli seulement si une transaction déclenchée par un vendeur s'exécute
  STRICTEMENT sous L (le niveau L a donc été entièrement consommé, notre ordre compris) ; en tête de
  file (``improve``), une vente à L ou moins remplit l'ordre à hauteur de la quantité échangée ;
- ``optimistic`` : en plus, toute vente à L remplit l'ordre à hauteur de la quantité échangée,
  comme si personne n'était devant nous dans la file. Elle remplit donc toujours au moins autant que
  la règle prudente.
La vérité est entre les deux : les deux bornes sont toujours rapportées.

Au bout du délai, le reliquat non exécuté est soit annulé (``fallback="cancel"`` : pas de position,
aucun gain compté), soit exécuté au marché (``fallback="market"``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .tape import Quote, Tape, quote_at

POLICIES = ("market", "join", "improve")
RULES = ("conservative", "optimistic")
FALLBACKS = ("cancel", "market")


@dataclass
class Execution:
    policy: str
    side: str
    qty: float
    rule: str = ""
    fallback: str = ""
    fills: list = field(default_factory=list)  # (instant, prix, quantité, teneur ?)
    status: str = "unfilled"  # filled | partial | unfilled | no_evidence
    passive_qty: float = 0.0
    fallback_qty: float = 0.0

    @property
    def filled_qty(self) -> float:
        return float(sum(q for _, _, q, _ in self.fills))

    @property
    def avg_price(self) -> float | None:
        q = self.filled_qty
        return float(sum(p * x for _, p, x, _ in self.fills) / q) if q > 0 else None

    def fees(self, maker_rate: float, taker_rate: float) -> float:
        return float(sum(p * x * (maker_rate if m else taker_rate) for _, p, x, m in self.fills))

    def shortfall_bps(self, mid: float, maker_rate: float, taker_rate: float) -> float | None:
        """Coût de la partie exécutée par rapport au milieu de décision, frais compris (positif = coût)."""
        q = self.filled_qty
        if q <= 0:
            return None
        gross = sum(p * x for _, p, x, _ in self.fills)
        fee = self.fees(maker_rate, taker_rate)
        if self.side == "buy":
            return float(((gross + fee) / q - mid) / mid * 1e4)
        return float((mid - (gross - fee) / q) / mid * 1e4)

    def wait_ms(self, t0: int) -> int | None:
        return int(self.fills[-1][0] - t0) if self.fills else None


def _take(tape: Tape, side: str, qty: float, start: int, end: int) -> list:
    """Liquidité prise au marché : transactions du côté opposé à partir de ``start``, jusqu'à couvrir ``qty``."""
    w = tape.window(start, end)
    idx = np.flatnonzero(w.buyer_initiated if side == "buy" else ~w.buyer_initiated)
    if not len(idx):
        return []
    cum = np.cumsum(w.qty[idx])
    last = int(np.searchsorted(cum, qty - 1e-15))  # première transaction qui complète la quantité
    idx = idx[:last + 1]
    took = np.diff(np.concatenate([[0.0], np.minimum(np.cumsum(w.qty[idx]), qty)]))
    return [(int(w.ts[k]), float(w.price[k]), float(q), False) for k, q in zip(idx, took) if q > 0]


def market(tape: Tape, side: str, qty: float, arrival: int, *, max_wait_ms: int = 60_000) -> Execution:
    ex = Execution("market", side, qty)
    ex.fills = _take(tape, side, qty, arrival, arrival + max_wait_ms)
    ex.fallback_qty = ex.filled_qty
    ex.status = "filled" if ex.filled_qty >= qty - 1e-12 else ("partial" if ex.fills else "no_evidence")
    return ex


def limit_price(policy: str, side: str, quote: Quote, tick: float) -> tuple[float, bool]:
    """Prix de l'ordre et indicateur « seul en tête de file ».

    Si l'estimation est croisée, l'ordre reste du bon côté : un achat n'est jamais placé au-dessus du
    prix vendeur estimé moins un pas (il serait exécuté comme preneur, pas comme teneur)."""
    if policy == "improve":
        if side == "buy" and quote.bid + tick < quote.ask - 1e-12:
            return quote.bid + tick, True
        if side == "sell" and quote.ask - tick > quote.bid + 1e-12:
            return quote.ask - tick, True
    if side == "buy":
        return min(quote.bid, quote.ask - tick), False
    return max(quote.ask, quote.bid + tick), False


def passive(tape: Tape, side: str, qty: float, quote: Quote, post: int, timeout_ms: int, *, policy: str, tick: float,
            rule: str, fallback: str, max_wait_ms: int = 60_000) -> Execution:
    if policy not in ("join", "improve") or rule not in RULES or fallback not in FALLBACKS:
        raise ValueError("Méthode, règle ou repli inconnu")
    price, front = limit_price(policy, side, quote, tick)
    ex = Execution(policy, side, qty, rule, fallback)
    w = tape.window(post + 1, post + timeout_ms + 1)
    hit = ~w.buyer_initiated if side == "buy" else w.buyer_initiated  # qui peut exécuter notre ordre
    px = w.price
    through = hit & ((px < price - 1e-12) if side == "buy" else (px > price + 1e-12))
    touch = hit & ((px <= price + 1e-12) if side == "buy" else (px >= price - 1e-12))
    partial_ok = touch if (rule == "optimistic" or front) else np.zeros(len(w), bool)
    first_through = int(np.argmax(through)) if through.any() else len(w)  # les deux règles : un niveau traversé est entièrement consommé
    idx = np.flatnonzero(partial_ok[:first_through])
    left = qty
    if len(idx):
        cum = np.cumsum(w.qty[idx])
        stop = int(np.searchsorted(cum, qty - 1e-15))
        idx = idx[:stop + 1]
        got = np.diff(np.concatenate([[0.0], np.minimum(np.cumsum(w.qty[idx]), qty)]))
        for k, g in zip(idx, got):
            if g > 0:
                ex.fills.append((int(w.ts[k]), price, float(g), True))
                left -= float(g)
    if left > 1e-15 and first_through < len(w):
        ex.fills.append((int(w.ts[first_through]), price, left, True))  # le niveau entier a été consommé : notre ordre aussi
        left = 0.0
    ex.passive_qty = ex.filled_qty
    if left > 1e-15 and fallback == "market":
        rest = _take(tape, side, left, post + timeout_ms, post + timeout_ms + max_wait_ms)
        ex.fills += rest
        ex.fallback_qty = float(sum(q for _, _, q, _ in rest))
    done = ex.filled_qty
    ex.status = "filled" if done >= qty - 1e-12 else ("partial" if done > 0 else ("no_evidence" if fallback == "market" else "unfilled"))
    return ex


@dataclass(frozen=True)
class SampleResult:
    """Toutes les méthodes sur un même échantillon (comparaison appariée)."""
    symbol: str
    t: int
    side: str
    qty: float
    notional: float
    quote: Quote | None
    results: dict  # nom de variante -> dict de mesures

    def to_dict(self) -> dict:
        q = None if self.quote is None else {"bid": self.quote.bid, "ask": self.quote.ask, "mid": self.quote.mid,
                                             "half_spread_bps": self.quote.half_spread_bps, "crossed": self.quote.crossed,
                                             "bid_age_ms": self.quote.bid_age_ms, "ask_age_ms": self.quote.ask_age_ms}
        return {"symbol": self.symbol, "t": self.t, "side": self.side, "qty": self.qty, "notional": self.notional, "quote": q,
                "results": self.results}


def variant_name(policy: str, timeout_ms: int | None = None, rule: str | None = None, fallback: str | None = None, latency_ms: int | None = None) -> str:
    if policy == "market":
        return f"market@{latency_ms}ms"
    return f"{policy}/{timeout_ms // 1000}s/{rule}/{fallback}"


def evaluate_sample(tape: Tape, t: int, side: str, notional: float, *, tick: float, maker_rate: float, taker_rate: float,
                    latencies_ms: tuple[int, ...], timeouts_ms: tuple[int, ...], lookback_ms: int = 60_000) -> SampleResult:
    """Toutes les variantes pour un ordre de ``notional`` (devise de cotation) décidé à ``t``."""
    quote = quote_at(tape, t, lookback_ms=lookback_ms)
    if quote is None:
        return SampleResult(tape.symbol, t, side, 0.0, notional, None, {})
    qty = notional / quote.mid
    mid = quote.mid
    out = {}
    for lat in latencies_ms:
        ex = market(tape, side, qty, t + lat)
        out[variant_name("market", latency_ms=lat)] = _measure(ex, mid, maker_rate, taker_rate, t)
    base_post = t + latencies_ms[0]
    for timeout in timeouts_ms:
        later = quote_at(tape, base_post + timeout, lookback_ms=lookback_ms)
        drift = None if later is None else (later.mid - mid) / mid * 1e4 * (1 if side == "buy" else -1)
        for policy in ("join", "improve"):
            for rule in RULES:
                for fb in FALLBACKS:
                    ex = passive(tape, side, qty, quote, base_post, timeout, policy=policy, tick=tick, rule=rule, fallback=fb)
                    m = _measure(ex, mid, maker_rate, taker_rate, t)
                    m["adverse_drift_bps"] = drift  # mouvement du milieu pendant l'attente, du point de vue de l'ordre
                    out[variant_name(policy, timeout, rule, fb)] = m
    return SampleResult(tape.symbol, t, side, qty, notional, quote, out)


def _measure(ex: Execution, mid: float, maker_rate: float, taker_rate: float, t: int) -> dict:
    return {"status": ex.status, "fill_ratio": ex.filled_qty / ex.qty if ex.qty else 0.0, "passive_ratio": ex.passive_qty / ex.qty if ex.qty else 0.0,
            "fallback_ratio": ex.fallback_qty / ex.qty if ex.qty else 0.0, "shortfall_bps": ex.shortfall_bps(mid, maker_rate, taker_rate),
            "wait_ms": ex.wait_ms(t), "maker_fills": sum(1 for f in ex.fills if f[3]), "taker_fills": sum(1 for f in ex.fills if not f[3])}
