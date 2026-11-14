"""Outils communs aux tests ALPHA SHADOW : marchés synthétiques sans écart observé, flux de bougies, carnets."""
import copy

import numpy as np

from sentinelle.alpha.bars import Bars, Universe
from sentinelle.alpha.execution import CostModel, PortfolioRules
from sentinelle.alpha.markets import synthetic_universe
from sentinelle.alpha.shadow import develop
from sentinelle.alpha.shadow.contract import make
from sentinelle.alpha.shadow.engine import SessionConfig, ShadowEngine
from sentinelle.alpha.shadow.ledger import ChainLedger
from sentinelle.alpha.shadow.r1 import artifact_sha256, load_model

TF = 14_400_000
COST, RULES = CostModel(), PortfolioRules()
_CACHE: dict = {}
_WARM: dict = {}


def market(seed: int, kind: str = "regimes", edge: float = 1.0, n_bars: int = 4000) -> dict:
    """Marché synthétique + R1 développé dessus (le moteur ne voit jamais la vérité du marché)."""
    key = (seed, kind, edge, n_bars)
    if key not in _CACHE:
        raw = synthetic_universe(seed, kind=kind, edge=edge, n_bars=n_bars)
        u = Universe({s: Bars(b.symbol, b.ts, b.open, b.high, b.low, b.close, b.volume, None, b.tf_ms, b.provenance)
                      for s, b in raw.bars.items()})
        d = develop.develop(u, cost=COST, rules=RULES, provenance={"synthetic": seed})
        model = load_model(d["artifact"], artifact_sha256(d["artifact"]))
        lo, hi = d["control"]["evaluation"]["window_ms"]
        _CACHE[key] = {"u": u, "d": d, "model": model, "lo": lo, "hi": hi}
    return _CACHE[key]


def config(m: dict, *, mode: str = "synthetic", start: int | None = None, end: int | None = None, **over) -> SessionConfig:
    start = m["lo"] + develop.EMBARGO_BARS * TF if start is None else start
    return SessionConfig("test-protocol", "0" * 64, mode, tuple(m["u"].symbols), start, m["hi"] if end is None else end, **over)


def bars_at(u: Universe, i: int, *, latency: int = 1000, symbols=None) -> list[dict]:
    close = int(u.ts[i]) + TF
    return [make("bar", symbol=s, timeframe_ms=TF, open_at_ms=int(b.ts[i]), close_at_ms=close, open=float(b.open[i]),
                 high=float(b.high[i]), low=float(b.low[i]), close=float(b.close[i]), volume=float(b.volume[i]), source="synthetic",
                 published_at_ms=close, received_at_ms=close + latency, payload_sha256=None)
            for s, b in sorted(u.bars.items()) if symbols is None or s in symbols]


def book_at(u: Universe, sym: str, i: int, *, delay: int = 3000, spread_bps: float = 4.0, qty: float = 1e6, shift: float = 0.0,
            exchange: bool = True, depth_eur: float | None = None) -> dict:
    """Carnet reçu ``delay`` ms après la clôture de la bougie ``i`` ; prix = ouverture suivante (± ``shift``).
    ``depth_eur`` : montant total offert à la vente, réparti sur deux niveaux."""
    close = int(u.ts[i]) + TF
    mid = float(u.bars[sym].open[i + 1]) * (1 + shift)
    half = spread_bps / 2e4
    if depth_eur is not None:
        qty = depth_eur / mid / 2
    return make("book", symbol=sym, source="synthetic", exchange_at_ms=close + delay - 500 if exchange else None,
                received_at_ms=close + delay, time_quality="exchange" if exchange else "receipt_only",
                bids=[[mid * (1 - half), qty]], asks=[[mid * (1 + half), qty], [mid * (1 + 3 * half), qty]], update_id=i)


def warm_index(u: Universe, cfg: SessionConfig) -> int:
    """Indice de la première bougie dont la clôture est dans la fenêtre (les précédentes sont l'historique)."""
    return int(np.searchsorted(u.ts + TF, cfg.start_ms))


def last_index(u: Universe, cfg: SessionConfig) -> int:
    return int(np.searchsorted(u.ts + TF, cfg.end_ms, side="right"))


def start_engine(m: dict, cfg: SessionConfig, *, ledger=None, reference=None) -> tuple[ShadowEngine, int]:
    """Session ouverte et historique d'échauffement déjà admis (copie d'un moteur mis en cache quand c'est possible)."""
    u = m["u"]
    warm = warm_index(u, cfg)
    key = (id(m), cfg.start_ms, cfg.end_ms, cfg.mode, repr(sorted(cfg.cost.items())), repr(sorted(cfg.limits.items())), cfg.not_before_ms)
    if ledger is None and reference is None and key in _WARM:
        return copy.deepcopy(_WARM[key]), warm
    eng = ShadowEngine.start(cfg, m["model"], ledger if ledger is not None else ChainLedger(None), now_ms=int(u.ts[0]), reference=reference)
    eng.step(int(u.ts[warm - 1]) + TF + 2000, bars=[b for i in range(warm) for b in bars_at(u, i)])
    if ledger is None and reference is None:
        _WARM[key] = copy.deepcopy(eng)
    return eng, warm


def run(m: dict, cfg: SessionConfig | None = None, *, ledger=None, upto: int | None = None, books: bool = False, think: int = 2000,
        reference=None, book_kw: dict | None = None) -> ShadowEngine:
    cfg = cfg or config(m)
    u = m["u"]
    eng, warm = start_engine(m, cfg, ledger=ledger, reference=reference)
    last = last_index(u, cfg) if upto is None else upto
    for i in range(warm, last):
        prev_books = [book_at(u, s, i - 1, **(book_kw or {})) for s in u.symbols] if (books and i > warm) else []
        eng.step(int(u.ts[i]) + TF + think, bars=bars_at(u, i), books=prev_books)
    return eng


def kinds(eng: ShadowEngine, kind: str) -> list[dict]:
    return [ln for ln in eng.ledger.lines if ln["kind"] == kind]
