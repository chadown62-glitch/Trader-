"""Adaptive, auditable learning from the closed positions of the memecoin paper session (simulation only).

Two outputs, both computed from closed lots and nothing else:

* a rolling **efficiency score** per strategy (mean return, profit factor and win rate of its last ``WINDOW`` closed
  lots) and the **allocation multiplier** derived from it, strictly bounded to ``[0.5, 1.5]``. The paper simulator
  applies it to the Kelly target of a new fictitious stake; the hard limits (10 % of equity per token, the strategy
  budget, the 25 % cash reserve) never move. Below ``MIN_TRADES`` closed lots the score is shrunk toward neutral and the
  multiplier cannot exceed 1.0, so a lucky streak never inflates a stake;
* **entry-threshold recommendations** (pool liquidity, 1 h volume, signal confidence) comparing the entry conditions of
  winning and losing lots. They are suggestions for a human: nothing here changes a filter or a frozen rule.

Returns (fraction of the initial stake) are used rather than USD P&L, so the multiplier never feeds on its own past
stake sizes. No order, no key, no promotion: ``asset_domain="memecoin"``, fictitious funds only.
"""

import json
import math
import sqlite3
import statistics
from contextlib import closing
from pathlib import Path

from . import established_pools
from .memecoin_strategies import NAMES, confidence

ASSET_DOMAIN = "memecoin"
VERSION = "adaptive-v1"
MIN_MULTIPLIER, MAX_MULTIPLIER = 0.5, 1.5
WINDOW = 20  # most recent closed lots per strategy in the rolling score
MIN_TRADES = 8  # closed lots before a strategy may be sized above 1.0x; also the shrinkage constant
PROFIT_SCALE_PCT = 20.0  # a mean return of +20 % maps to tanh(1) on the profit component
SCORE_WEIGHTS = {"profit": 0.5, "profit_factor": 0.3, "win_rate": 0.2}
MIN_RECOMMEND_TRADES = 20  # closed lots with a recorded entry before any threshold is suggested
MIN_GROUP = 5  # winners and losers needed on each side; a suggested threshold also keeps half of the lots
# (feature recorded at entry, current rule, label). The current rules are those of established_pools (M58).
FEATURES = (("liquidity_usd", established_pools.MIN_LIQUIDITY_USD, "liquidité du pool (USD)"),
            ("volume_h1_usd", established_pools.MIN_VOLUME_H1_USD, "volume sur 1 h (USD)"),
            ("confidence", confidence(["sniper"]), "score de confiance du signal"))


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clamp(value):
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, value))


def _returns(completed, strategy):
    rows = [r for r in completed if r.get("strategy") == strategy and r.get("asset_domain", ASSET_DOMAIN) == ASSET_DOMAIN]
    return [r["return"] for r in rows if _finite(r.get("return"))]


def _fmt(value, digits=1):
    return "n/d" if value is None else f"{value:.{digits}f}".replace(".", ",")


def score(returns):
    """Efficiency of a strategy on its last ``WINDOW`` closed lots, in [-1, 1], with every component kept for audit."""
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


def multipliers(completed):
    """Bounded allocation multiplier and its explanation, per strategy."""
    result = {}
    for name in NAMES:
        returns = _returns(completed, name)
        s = score(returns)
        unlocked = len(returns) >= MIN_TRADES
        value = _clamp(1 + s["score"])
        if not unlocked:
            value = min(1.0, value)  # no upside before the minimal sample
        if not returns:
            reason = f"{name} : aucune clôture, mise neutre 1,00x"
        else:
            reason = (f"{name} : {s['window_trades']} clôtures récentes, profit moyen {_fmt(s['avg_profit_pct'])} %, "
                      f"profit factor {_fmt(s['profit_factor'], 2)}, win rate {_fmt(100 * s['win_rate'], 0)} % "
                      f"-> score {_fmt(s['score'], 2)} -> {_fmt(value, 2)}x")
            if not unlocked:
                reason += f" (hausse bloquée : {len(returns)}/{MIN_TRADES} clôtures)"
        result[name] = {**s, "closed_trades": len(returns), "upside_unlocked": unlocked, "multiplier": value, "reason": reason}
    return result


def _recommend(completed, feature, current, label):
    data = [(r["entry"][feature], r["return"]) for r in completed
            if isinstance(r.get("entry"), dict) and _finite(r["entry"].get(feature)) and _finite(r.get("return"))
            and r.get("asset_domain", ASSET_DOMAIN) == ASSET_DOMAIN]
    winners = [x for x, ret in data if ret > 0]
    losers = [x for x, ret in data if ret <= 0]
    out = {"feature": feature, "label": label, "current_threshold": current, "samples": len(data), "winners": len(winners),
           "losers": len(losers), "winners_median": statistics.median(winners) if winners else None,
           "losers_median": statistics.median(losers) if losers else None, "suggested_threshold": None,
           "kept_trades": None, "excluded_winners": None, "excluded_losers": None,
           "avg_profit_pct_before": 100 * statistics.fmean(r for _, r in data) if data else None,
           "avg_profit_pct_after": None, "applied": False}
    if len(data) < MIN_RECOMMEND_TRADES or len(winners) < MIN_GROUP or len(losers) < MIN_GROUP:
        return {**out, "status": "insufficient_sample",
                "explanation": f"{label} : {len(data)} clôtures avec conditions d'entrée enregistrées, {len(winners)} gagnantes "
                               f"et {len(losers)} perdantes ; il en faut {MIN_RECOMMEND_TRADES}, dont {MIN_GROUP} de chaque côté."}
    best, best_total = None, sum(r for _, r in data)
    for threshold in sorted({x for x, _ in data}):
        kept = [r for x, r in data if x >= threshold]
        if len(kept) < max(MIN_GROUP, len(data) / 2):
            break  # never fit a threshold on a small tail of the history
        if sum(kept) > best_total + 1e-12 and threshold > current:
            best, best_total = threshold, sum(kept)
    medians = (f"médiane {_fmt(out['winners_median'], 2)} pour les gagnantes contre {_fmt(out['losers_median'], 2)} "
               f"pour les perdantes")
    if best is None:
        return {**out, "status": "no_improvement",
                "explanation": f"{label} : {medians} ; aucun seuil au-dessus de la règle actuelle n'aurait amélioré l'historique."}
    kept = [(x, r) for x, r in data if x >= best]
    after = 100 * statistics.fmean(r for _, r in kept)
    excluded_winners = sum(1 for x, r in data if x < best and r > 0)
    excluded_losers = sum(1 for x, r in data if x < best and r <= 0)
    return {**out, "status": "suggested", "suggested_threshold": best, "kept_trades": len(kept),
            "excluded_winners": excluded_winners, "excluded_losers": excluded_losers, "avg_profit_pct_after": after,
            "explanation": f"{label} : {medians}. Un seuil d'entrée à {_fmt(best, 2)} aurait écarté {excluded_losers} perdantes et "
                           f"{excluded_winners} gagnantes ; profit moyen {_fmt(out['avg_profit_pct_before'])} % -> {_fmt(after)} %. "
                           "Mesure sur l'historique déjà vu : à confirmer en simulation avant toute adoption humaine."}


def learn_from_state(state):
    """Learning report of a paper session snapshot (``None`` or a session without closed lots gives a neutral report)."""
    completed = [] if state is None else state.get("completed", [])
    strategies = multipliers(completed)
    closed = sum(s["closed_trades"] for s in strategies.values())
    status = "empty" if not closed else "active" if all(s["upside_unlocked"] for s in strategies.values()) else "learning"
    return {"asset_domain": ASSET_DOMAIN, "paper_only": True, "version": VERSION, "status": status, "closed_trades": closed,
            "bounds": {"min": MIN_MULTIPLIER, "max": MAX_MULTIPLIER}, "window": WINDOW, "min_trades": MIN_TRADES,
            "score_weights": SCORE_WEIGHTS, "strategies": strategies,
            "recommendations": [_recommend(completed, *feature) for feature in FEATURES],
            "applies_to": "mise cible de Kelly des nouvelles positions fictives ; plafonds de risque et filtres inchangés"}


def learn(db_path):
    """Read the paper session in read-only mode and learn from its closed lots; a missing or empty base is neutral."""
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
