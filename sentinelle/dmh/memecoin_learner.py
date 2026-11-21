"""Adaptive, auditable learning from the lots of the memecoin paper session (simulation only).

Two outputs:

* a rolling **efficiency score** per strategy (mean return, profit factor and win rate of its last ``WINDOW`` evaluated
  lots, by opening time) and the **allocation multiplier** derived from it, strictly bounded to ``[0.5, 1.5]``. An
  evaluated lot is a closed lot (its whole realized return, partial exits included) or an open lot that is mature (held
  at least ``MATURITY_S``) or has already sold a slice: its return is then the realized partial sales plus the
  liquidation mark of the remainder. Counting open lots removes the survivorship bias of closed-only learning: stops
  close losers within minutes while winners stay open behind their trailing stop, so closed lots alone are mostly
  losers. The window is an entry cohort (the last lots opened, closed or not), so open survivors are not over-weighted
  either. The paper
  simulator applies the multiplier to the Kelly target of a new fictitious stake; the hard limits (10 % of equity per
  token, the strategy budget, the 25 % cash reserve) never move. Below ``MIN_TRADES`` evaluated lots (or
  ``MIN_CLOSED_FOR_UPSIDE`` closed ones) the multiplier cannot exceed 1.0;
* an **exploration floor** (``exploration_floor``): a strategy whose Kelly fraction fell to zero after a series of stops
  still opens small stakes (0.5 % of equity x its multiplier, never under the 0.05 USD minimum), so it keeps producing
  the data that could rehabilitate it instead of freezing;
* **entry-threshold recommendations** (pool liquidity, 1 h volume, signal confidence). A threshold is suggested only if
  it strictly raises the mean return (``avg_profit_pct``) on the whole history, does so again out of sample in a two-fold
  cross-validation (each half spread over the whole history), and survives a permutation test that replays the whole
  threshold search on shuffled returns (p <= ``ALPHA``). They are suggestions for a human: ``applied`` is always false and
  nothing here changes a filter or a frozen rule.

Returns (fraction of the initial stake) are used rather than USD P&L, so the multiplier never feeds on its own past
stake sizes. No order, no key, no promotion: ``asset_domain="memecoin"``, fictitious funds only.
"""

import json
import math
import random
import sqlite3
import statistics
from contextlib import closing
from pathlib import Path

from . import established_pools
from .memecoin_strategies import NAMES, confidence

ASSET_DOMAIN = "memecoin"
VERSION = "adaptive-v1"
SCORING = "lots-v2"  # closed lots + mature or partially sold open lots
MIN_MULTIPLIER, MAX_MULTIPLIER = 0.5, 1.5
WINDOW = 20  # most recent evaluated lots per strategy in the rolling score
MIN_TRADES = 8  # evaluated lots before a strategy may be sized above 1.0x; also the shrinkage constant
MIN_CLOSED_FOR_UPSIDE = 4  # and at least this many closed lots: latent gains alone never raise a stake
MATURITY_S = 3600  # an open lot without any sale is evaluated once it has been held one hour
PROFIT_SCALE_PCT = 20.0  # a mean return of +20 % maps to tanh(1) on the profit component
SCORE_WEIGHTS = {"profit": 0.5, "profit_factor": 0.3, "win_rate": 0.2}
EXPLORATION_FRACTION = 0.005  # exploration stake: 0.5 % of equity x multiplier
MIN_STAKE_USD = 0.05  # the paper simulator never opens a smaller stake
MIN_RECOMMEND_TRADES = 20  # evaluated lots with a recorded entry before any threshold is considered
MIN_GROUP = 5  # winners and losers needed on each side; a threshold also keeps at least half of the lots
RECOMMEND_WINDOW = 200  # most recent evaluated lots used for the threshold search
PERMUTATIONS = 499  # shuffles of the permutation test (deterministic seed)
PERMUTATION_SEED = 58
ALPHA = 0.01  # a suggested threshold must beat 99 % of the shuffled searches (about 3 % for the three criteria together)
# (feature recorded at entry, current rule, label). The current rules are those of established_pools (M58).
FEATURES = (("liquidity_usd", established_pools.MIN_LIQUIDITY_USD, "liquidité du pool (USD)"),
            ("volume_h1_usd", established_pools.MIN_VOLUME_H1_USD, "volume sur 1 h (USD)"),
            ("confidence", confidence(["sniper"]), "score de confiance du signal"))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clamp(value):
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, value))


def _fmt(value, digits=1):
    return "n/d" if value is None else f"{value:.{digits}f}".replace(".", ",")


def _mine(record):
    return record.get("asset_domain", ASSET_DOMAIN) == ASSET_DOMAIN


# -- evaluated lots -----------------------------------------------------------------------------------------------------
def open_lots(state):
    """Open lots worth evaluating: mature (held >= MATURITY_S at their last mark) or already partially sold.

    Return = (realized partial sales + liquidation mark - remaining cost) / initial stake. A lot whose mark is stale is
    skipped: its value is unknown."""
    if not state:
        return []
    stale = set(state.get("stale_marks", []))
    buys = {}
    if any(pos.get("opened_at") is None for pos in state.get("positions", {}).values()):
        for fill in state.get("fills", []):  # lots opened before opening times were recorded: their latest buy
            if fill.get("side") == "buy":
                buys[fill["strategy"] + "/" + fill["mint"]] = fill.get("ts")
    lots = []
    for key, pos in state.get("positions", {}).items():
        if not _mine(pos) or pos.get("mint") in stale or not _finite(pos.get("initial")) or pos["initial"] <= 0 \
                or not _finite(pos.get("mark")) or not _finite(pos.get("basis")):
            continue
        marked = ((state.get("assets", {}).get(pos["mint"]) or {}).get("last") or {}).get("ts")
        opened = pos.get("opened_at")
        if opened is None:
            opened = buys.get(key)
        held = marked - opened if _finite(marked) and _finite(opened) else None
        if pos.get("stage", 0) < 1 and (held is None or held < MATURITY_S):
            continue  # too young to say anything
        value = (pos.get("pnl", 0.0) + pos["mark"] - pos["basis"]) / pos["initial"]
        if _finite(value):
            lots.append({"strategy": pos["strategy"], "mint": pos["mint"], "return": value, "open": True, "held_s": held,
                         "opened_at": opened, "entry": pos.get("entry"), "asset_domain": ASSET_DOMAIN})
    return lots


def evaluated(completed, open_=()):
    """Closed lots and evaluated open lots as one entry cohort, ordered by opening time: the sequence every score and
    threshold reads. Each entry counts once whatever its outcome, so old open survivors leave the rolling window as
    newer entries arrive (closed lots recorded before opening times existed come first, in their closing order)."""
    rows = [r for r in completed if _mine(r) and _finite(r.get("return"))]
    rows += [r for r in open_ if _mine(r) and _finite(r.get("return"))]
    keyed = [((0, 0, i) if not _finite(r.get("opened_at")) else (1, r["opened_at"], i), r) for i, r in enumerate(rows)]
    return [r for _, r in sorted(keyed, key=lambda item: item[0])]


# -- rolling efficiency score --------------------------------------------------------------------------------------------
def score(returns):
    """Efficiency of a strategy on its last ``WINDOW`` evaluated lots, in [-1, 1], with every component kept for audit."""
    recent = returns[-WINDOW:]
    n = len(recent)
    if not n:
        return {"window_trades": 0, "avg_profit_pct": None, "profit_factor": None, "win_rate": None,
                "components": None, "shrink": 0.0, "raw_score": None, "score": 0.0}
    avg = 100 * sum(recent) / n
    gains = sum(r for r in recent if r > 0)
    losses = -sum(r for r in recent if r < 0)
    pf = gains / losses if losses else None
    win = sum(r > 0 for r in recent) / n
    if losses:
        pf_component = (pf - 1) / (pf + 1)  # 0 at break-even, -1 without any gain, toward +1 without losses
    else:
        pf_component = 1.0 if gains else 0.0
    components = {"profit": math.tanh(avg / PROFIT_SCALE_PCT), "profit_factor": pf_component, "win_rate": 2 * win - 1}
    raw = sum(SCORE_WEIGHTS[k] * v for k, v in components.items())
    shrink = n / (n + MIN_TRADES)  # few lots pull the score toward neutral
    return {"window_trades": n, "avg_profit_pct": avg, "profit_factor": pf, "win_rate": win, "components": components,
            "shrink": shrink, "raw_score": raw, "score": raw * shrink}


def multipliers(completed, open_=()):
    """Bounded allocation multiplier and its explanation, per strategy (closed lots, then evaluated open lots)."""
    rows = evaluated(completed, open_)
    result = {}
    for name in NAMES:
        mine = [r for r in rows if r.get("strategy") == name]
        returns = [r["return"] for r in mine]
        closed_count = sum(1 for r in mine if not r.get("open"))
        open_count = len(mine) - closed_count
        s = score(returns)
        window_open = sum(1 for r in mine[-WINDOW:] if r.get("open"))
        unlocked = len(returns) >= MIN_TRADES and closed_count >= MIN_CLOSED_FOR_UPSIDE
        value = _clamp(1 + s["score"])
        if not unlocked:
            value = min(1.0, value)  # no upside before the minimal sample
        if not returns:
            reason = f"{name} : aucune clôture, mise neutre 1,00x"
        else:
            counted = f"{s['window_trades'] - window_open} clôtures récentes"
            if window_open:
                counted += f" et {window_open} positions ouvertes évaluées (ventes partielles + valeur de liquidation)"
            reason = (f"{name} : {counted}, profit moyen {_fmt(s['avg_profit_pct'])} %, "
                      f"profit factor {_fmt(s['profit_factor'], 2)}, win rate {_fmt(100 * s['win_rate'], 0)} % "
                      f"-> score {_fmt(s['score'], 2)} -> {_fmt(value, 2)}x")
            if not unlocked:
                if open_count:
                    reason += (f" (hausse bloquée : {len(returns)}/{MIN_TRADES} positions évaluées, "
                               f"{closed_count}/{MIN_CLOSED_FOR_UPSIDE} clôtures)")
                else:
                    reason += f" (hausse bloquée : {len(returns)}/{MIN_TRADES} clôtures)"
        result[name] = {**s, "closed_trades": closed_count, "open_lots": open_count, "evaluated_lots": len(returns),
                        "upside_unlocked": unlocked, "multiplier": value, "reason": reason}
    return result


def multipliers_for_state(state):
    """Multipliers of a live paper session: its closed lots and its evaluated open lots."""
    return multipliers((state or {}).get("completed", []), open_lots(state))


def exploration_floor(equity, multiplier):
    """Smallest stake a signalled strategy still gets, so a run of stops never freezes it at zero."""
    return max(MIN_STAKE_USD, EXPLORATION_FRACTION * max(0.0, equity) * multiplier)


# -- entry-threshold recommendations ----------------------------------------------------------------------------------
def _best_threshold(xs, rets, current):
    """Threshold (strictly above the current rule) that maximises the mean return of the kept lots, keeping at least
    half of them and excluding at least one; ``None`` unless it strictly raises the mean."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    return _scan([xs[i] for i in order], [rets[i] for i in order], current)


def _scan(sx, sr, current):
    """``_best_threshold`` on values already sorted by the feature (``sr`` aligned on ``sx``)."""
    n = len(sx)
    suffix = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + sr[i]
    base = suffix[0] / n
    need = max(MIN_GROUP, math.ceil(n / 2))
    best = None
    for i in range(1, n):
        if sx[i] == sx[i - 1] or sx[i] <= current:
            continue  # a threshold sits at the first lot of a distinct value
        kept = n - i
        if kept < need:
            break
        mean = suffix[i] / kept
        if mean > base + 1e-12 and (best is None or mean > best[1] + 1e-12):
            best = (sx[i], mean)
    return best, base


def _improvement(sx, sr, current):
    best, base = _scan(sx, sr, current)
    return 0.0 if best is None else best[1] - base


def _recommend(rows, feature, current, label):
    data = [(r["entry"][feature], r["return"]) for r in rows
            if isinstance(r.get("entry"), dict) and _finite(r["entry"].get(feature))][-RECOMMEND_WINDOW:]
    xs, rets = [x for x, _ in data], [r for _, r in data]
    winners = [x for x, r in data if r > 0]
    losers = [x for x, r in data if r <= 0]
    out = {"feature": feature, "label": label, "current_threshold": current, "samples": len(data), "winners": len(winners),
           "losers": len(losers), "winners_median": statistics.median(winners) if winners else None,
           "losers_median": statistics.median(losers) if losers else None, "suggested_threshold": None,
           "kept_trades": None, "excluded_winners": None, "excluded_losers": None,
           "avg_profit_pct_before": 100 * statistics.fmean(rets) if data else None, "avg_profit_pct_after": None,
           "cross_validation": [], "p_value": None, "rejection": None, "applied": False}
    if len(data) < MIN_RECOMMEND_TRADES or len(winners) < MIN_GROUP or len(losers) < MIN_GROUP:
        return {**out, "status": "insufficient_sample",
                "explanation": f"{label} : {len(data)} positions évaluées avec conditions d'entrée enregistrées, "
                               f"{len(winners)} gagnantes et {len(losers)} perdantes ; il en faut {MIN_RECOMMEND_TRADES}, "
                               f"dont {MIN_GROUP} de chaque côté."}
    medians = (f"médiane {_fmt(out['winners_median'], 2)} pour les gagnantes contre {_fmt(out['losers_median'], 2)} "
               f"pour les perdantes")
    best, base = _best_threshold(xs, rets, current)
    if best is None:
        return {**out, "status": "no_improvement", "rejection": "no_threshold",
                "explanation": f"{label} : {medians} ; aucun seuil au-dessus de la règle actuelle n'aurait amélioré l'historique."}
    threshold, after = best
    # Out of sample: two interleaved halves, each spread over the whole history; fit on one, check on the other.
    folds = []
    for k in (0, 1):
        train = [(x, r) for i, (x, r) in enumerate(data) if i % 2 == k]
        test = [(x, r) for i, (x, r) in enumerate(data) if i % 2 != k]
        fit, _ = _best_threshold([x for x, _ in train], [r for _, r in train], current)
        kept = [r for x, r in test if fit is not None and x >= fit[0]]
        before_test = statistics.fmean(r for _, r in test)
        after_test = statistics.fmean(kept) if kept else None
        folds.append({"train_threshold": None if fit is None else fit[0], "test_trades": len(test), "test_kept": len(kept),
                      "test_avg_profit_pct_before": 100 * before_test,
                      "test_avg_profit_pct_after": None if after_test is None else 100 * after_test,
                      "confirmed": after_test is not None and len(kept) < len(test) and after_test > before_test + 1e-12})
    # Noise: replay the whole threshold search on shuffled returns (deterministic).
    observed = after - base
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    sx = [xs[i] for i in order]
    rng = random.Random(PERMUTATION_SEED)
    shuffled, beaten = [rets[i] for i in order], 0
    for _ in range(PERMUTATIONS):
        rng.shuffle(shuffled)
        beaten += _improvement(sx, shuffled, current) >= observed - 1e-12
    p_value = (1 + beaten) / (1 + PERMUTATIONS)
    excluded_winners = sum(1 for x, r in data if x < threshold and r > 0)
    excluded_losers = sum(1 for x, r in data if x < threshold and r <= 0)
    fitted = {**out, "kept_trades": sum(1 for x in xs if x >= threshold), "excluded_winners": excluded_winners,
              "excluded_losers": excluded_losers, "avg_profit_pct_after": 100 * after, "cross_validation": folds,
              "p_value": p_value}
    if not all(f["confirmed"] for f in folds):
        return {**fitted, "status": "no_improvement", "rejection": "cross_validation",
                "explanation": f"{label} : {medians} ; le meilleur seuil ({_fmt(threshold, 2)}) n'améliore pas le profit moyen "
                               "hors échantillon (validation croisée en deux moitiés) : rejeté."}
    if p_value > ALPHA:
        return {**fitted, "status": "no_improvement", "rejection": "noise",
                "explanation": f"{label} : {medians} ; le gain du meilleur seuil ({_fmt(threshold, 2)}) reste compatible avec "
                               f"le hasard (test de permutation : p = {_fmt(p_value, 2)} > {_fmt(ALPHA, 2)}) : rejeté."}
    return {**fitted, "status": "suggested", "suggested_threshold": threshold,
            "explanation": f"{label} : {medians}. Un seuil d'entrée à {_fmt(threshold, 2)} aurait écarté {excluded_losers} perdantes et "
                           f"{excluded_winners} gagnantes ; profit moyen {_fmt(out['avg_profit_pct_before'])} % -> {_fmt(100 * after)} %, "
                           f"amélioration confirmée hors échantillon (validation croisée en deux moitiés) et non due au hasard "
                           f"(p = {_fmt(p_value, 3)}). À confirmer en simulation avant toute adoption humaine."}


def learn_from_state(state):
    """Learning report of a paper session snapshot (``None`` or a session without any lot gives a neutral report)."""
    completed = [] if state is None else state.get("completed", [])
    open_ = open_lots(state)
    strategies = multipliers(completed, open_)
    rows = evaluated(completed, open_)
    closed = sum(s["closed_trades"] for s in strategies.values())
    status = "empty" if not rows else "active" if all(s["upside_unlocked"] for s in strategies.values()) else "learning"
    return {"asset_domain": ASSET_DOMAIN, "paper_only": True, "version": VERSION, "scoring": SCORING, "status": status,
            "closed_trades": closed, "open_lots_evaluated": len(open_), "bounds": {"min": MIN_MULTIPLIER, "max": MAX_MULTIPLIER},
            "window": WINDOW, "min_trades": MIN_TRADES, "maturity_s": MATURITY_S, "score_weights": SCORE_WEIGHTS,
            "exploration": {"fraction_of_equity": EXPLORATION_FRACTION, "min_stake_usd": MIN_STAKE_USD},
            "strategies": strategies, "recommendations": [_recommend(rows, *feature) for feature in FEATURES],
            "applies_to": "mise cible de Kelly des nouvelles positions fictives ; plafonds de risque et filtres inchangés"}


def learn(db_path):
    """Read the paper session in read-only mode and learn from its lots; a missing or empty base is neutral."""
    from . import memecoin_simulator as sim  # late import: the simulator itself imports this module

    path = Path(db_path)
    state = None
    if path.exists():
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                row = conn.execute("SELECT value FROM meta WHERE key=?", (sim.KEY,)).fetchone()
                state = json.loads(row[0]) if row else None
    return learn_from_state(state)
