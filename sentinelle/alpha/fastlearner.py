"""« Fast Learner » : audit du coût de calcul et courbes d'apprentissage (critère SECONDAIRE, exploratoire).

Ce module ne prouve aucun rendement. Il répond à trois questions séparées :

1. **Coût** : combien de CPU, de temps mural et de mémoire coûte un apprenant, sur la même machine,
   les mêmes observations, les mêmes graines et les mêmes budgets ? Répétitions, médiane, 95e
   centile et intervalle du rapport entre deux apprenants. La mesure de la PR #40 chronométrait
   avec le traceur de mémoire actif, ce qui ralentit inégalement le code : ici le temps et la
   mémoire sont mesurés dans des passes distinctes, et l'écart entre les deux méthodes est publié.
2. **Courbe** : nouvelle observation -> calibration -> avantage net, par climat et par actif.
3. **Regret** : écart au meilleur choix simple connu après coup, par seconde de calcul, et avantage
   par euro de pertes fictives qu'il a fallu subir pour obtenir les observations (« apprendre coûte »).

L'AUC et la calibration sont rapportées à part du résultat en euros : l'une ne vaut pas l'autre.
"""
from __future__ import annotations

import gc
import os
import platform
import time
import tracemalloc
from typing import Callable

import numpy as np

from .bars import Universe
from .dataset import Situations, build_situations, split, time_windows
from .execution import CostModel, PortfolioRules, run_portfolio
from .learners import Cash, Learner, ProjectMixte, RegimeRouter, SingleTeacher, TakeAll, climate, labels
from .stats import auc, brier, ece
from .teachers import TEACHER_NAMES
from .velocity import intents_for, recent_budget, window_indices

FRACTIONS = (0.5, 0.15, 0.15, 0.2)
EMBARGO_BARS = 30
CONTENDERS: dict[str, Callable[[], Learner]] = {"regime_router": RegimeRouter, "project_mixte": ProjectMixte}


def platform_info() -> dict:
    import sklearn

    return {"python": platform.python_version(), "implementation": platform.python_implementation(), "machine": platform.machine(),
            "system": platform.system(), "cpu_count": os.cpu_count(), "numpy": np.__version__, "sklearn": sklearn.__version__}


def _work(learner: Learner, train: Situations, cal: Situations, sel: Situations, ev: Situations):
    learner.fit(train, cal)
    learner.select(sel)
    enter, variant = learner.policy(ev)
    return enter, variant, learner.proba(ev)


def measure(factory: Callable[[], Learner], train: Situations, cal: Situations, sel: Situations, ev: Situations, *, repeats: int) -> tuple[Learner, dict]:
    """Temps (sans traceur) sur ``repeats`` répétitions, puis mémoire dans une passe séparée."""
    cpu, wall = [], []
    learner = factory()
    for _ in range(repeats):
        learner = factory()
        gc.collect()
        c0, w0 = time.process_time(), time.perf_counter()
        _work(learner, train, cal, sel, ev)
        cpu.append(time.process_time() - c0)
        wall.append(time.perf_counter() - w0)
    traced = factory()
    gc.collect()
    tracemalloc.start()
    c0 = time.process_time()
    _work(traced, train, cal, sel, ev)
    traced_cpu = time.process_time() - c0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return learner, {"cpu_s": cpu, "wall_s": wall, "peak_mb": peak / 2**20, "cpu_s_under_memory_tracer": traced_cpu}


def spread(xs: list[float]) -> dict:
    a = np.asarray(xs, float)
    return {"n": int(len(a)), "median": float(np.median(a)), "p95": float(np.quantile(a, 0.95)), "min": float(a.min()), "max": float(a.max())}


def ratio_interval(slow: list[float], fast: list[float], *, seed: int = 9001, reps: int = 2000, confidence: float = 0.95) -> dict:
    """Rapport des médianes (lent / rapide) et intervalle par rééchantillonnage des répétitions."""
    a, b = np.asarray(slow, float), np.asarray(fast, float)
    if not len(a) or not len(b) or np.median(b) <= 0:
        return {"ratio": None, "ci_low": None, "ci_high": None}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(reps):
        den = np.median(rng.choice(b, len(b)))
        if den > 0:
            draws.append(np.median(rng.choice(a, len(a))) / den)
    q = (1 - confidence) / 2
    lo, hi = (np.quantile(draws, [q, 1 - q]) if draws else (np.nan, np.nan))
    return {"ratio": float(np.median(a) / np.median(b)), "ci_low": float(lo), "ci_high": float(hi)}


def _portfolio(universe: Universe, part: Situations, enter: np.ndarray, variant: np.ndarray, window, cost, rules) -> dict:
    res = run_portfolio(universe, intents_for(part, enter, variant), start=window[0], end=window[1], rules=rules, cost=cost)
    by_asset: dict[str, float] = {}
    for t in res.trades:
        by_asset[t["symbol"]] = by_asset.get(t["symbol"], 0.0) + t["pnl"]
    known = np.isfinite(part.net_r["teacher"])
    c = climate(part)
    by_climate = {}
    for g in range(6):
        m = enter & known & (c == g)
        by_climate[str(g)] = {"entries": int(m.sum()), "mean_net_r": float(part.net_r["teacher"][m].mean()) if m.any() else None}
    return {"net_pnl_eur": res.net_pnl, "fills": len(res.trades), "false_signals": sum(1 for t in res.trades if t["pnl"] <= 0),
            "abstentions": int((~enter & ~part.vetoed).sum()), "vetoed": int(part.vetoed.sum()), "max_drawdown": res.max_drawdown(),
            "fees_eur": res.fees, "by_asset": by_asset, "by_climate": by_climate}


def _predictive(learner: Learner, ev: Situations) -> dict:
    ok, y = labels(ev)
    m = ok & ~ev.vetoed
    if not m.any():
        return {}
    p, ref = learner.proba(ev)[m], learner.reference_proba(ev)[m]
    b, b_ref = brier(y[m], p), brier(y[m], ref)
    return {"n": int(m.sum()), "auc": auc(y[m], p), "auc_teacher_identity": auc(y[m], ref), "brier": b, "ece": ece(y[m], p),
            "brier_skill_vs_teacher_rate": (1 - b / b_ref) if b_ref else None}


def _optimism(learner: Learner, ev: Situations, enter: np.ndarray) -> dict:
    """Score sur la fenêtre de sélection (où le seuil a été choisi) contre score sur l'évaluation : l'écart mesure
    l'optimisme de la sélection (« malédiction du vainqueur »), en R net moyen par entrée."""
    r = ev.net_r["teacher"][enter & np.isfinite(ev.net_r["teacher"])]
    sel = getattr(learner, "selection_score", None)
    sel = None if sel is None or not np.isfinite(sel) else float(sel)
    out = float(r.mean()) if len(r) else None
    return {"selection_mean_net_r": sel, "evaluation_mean_net_r": out,
            "optimism_r": (sel - out) if (sel is not None and out is not None) else None}


def tuition(universe: Universe, train_budget: Situations, train_window, cost, rules) -> float | None:
    """Pertes fictives subies en suivant TOUTES les entrées pendant la période qui a produit ces observations."""
    if not len(train_budget):
        return None
    close = universe.ts + universe.bars[universe.symbols[0]].tf_ms
    start = int(np.searchsorted(close, int(train_budget.decided_at.min())))
    enter = ~train_budget.vetoed
    res = run_portfolio(universe, intents_for(train_budget, enter, np.array(["teacher"] * len(train_budget), dtype=object)),
                        start=max(start, train_window[0]), end=train_window[1], rules=rules, cost=cost)
    return float(-res.net_pnl)


def bench_market(label: str, universe: Universe, *, budgets: tuple, repeats: int, cost: CostModel = CostModel(),
                 rules: PortfolioRules = PortfolioRules()) -> dict:
    s = build_situations(universe, cost, variants=("teacher",))
    w = time_windows(universe, FRACTIONS, embargo_bars=EMBARGO_BARS)
    masks, purged = split(s, w)
    parts = {k: s.take(v) for k, v in masks.items()}
    ev, ev_win = parts["evaluation"], window_indices(universe, w.bounds["evaluation"])
    train_win = window_indices(universe, w.bounds["train"])
    fixed = {}
    for ref in [Cash(), TakeAll()] + [SingleTeacher(t) for t in TEACHER_NAMES]:
        ref.fit(parts["train"], parts["calibration"])
        enter, variant = ref.policy(ev)
        fixed[ref.name] = _portfolio(universe, ev, enter, variant, ev_win, cost, rules)
    best_fixed = max(fixed, key=lambda k: fixed[k]["net_pnl_eur"])
    rows = []
    for n in budgets:
        train = recent_budget(parts["train"], n)
        cost_of_data = tuition(universe, train, train_win, cost, rules)
        cell = {"budget": "tout" if n is None else n, "observations": int(len(train)), "tuition_eur": cost_of_data, "learners": {}}
        timings = {}
        for name, factory in CONTENDERS.items():
            learner, timing = measure(factory, train, parts["calibration"], parts["selection"], ev, repeats=repeats)
            enter, variant = learner.policy(ev)
            fin = _portfolio(universe, ev, enter, variant, ev_win, cost, rules)
            delta = fin["net_pnl_eur"] - fixed["take_all"]["net_pnl_eur"]
            regret = fixed[best_fixed]["net_pnl_eur"] - fin["net_pnl_eur"]
            cpu_med = float(np.median(timing["cpu_s"]))
            timings[name] = timing
            cell["learners"][name] = {
                "cpu_s": spread(timing["cpu_s"]), "wall_s": spread(timing["wall_s"]), "peak_mb": timing["peak_mb"],
                "cpu_s_under_memory_tracer": timing["cpu_s_under_memory_tracer"],
                "tracer_slowdown": (timing["cpu_s_under_memory_tracer"] / cpu_med) if cpu_med > 0 else None,
                "predictive": _predictive(learner, ev), "financial": fin, "params": learner.describe(),
                "selection_to_evaluation": _optimism(learner, ev, enter),
                "delta_vs_take_all_eur": delta, "regret_vs_best_fixed_eur": regret,
                "regret_eur_per_cpu_s": (regret / cpu_med) if cpu_med > 1e-4 else None,
                "advantage_per_tuition_eur": (delta / cost_of_data) if cost_of_data and cost_of_data > 0.01 else None,
            }
        slow, fast = timings["project_mixte"], timings["regime_router"]
        cell["ratio_project_over_router"] = {
            "cpu": ratio_interval(slow["cpu_s"], fast["cpu_s"]), "wall": ratio_interval(slow["wall_s"], fast["wall_s"]),
            "peak_mb": (slow["peak_mb"] / fast["peak_mb"]) if fast["peak_mb"] > 0 else None,
            "cpu_as_measured_in_pr40": (slow["cpu_s_under_memory_tracer"] / fast["cpu_s_under_memory_tracer"])
            if fast["cpu_s_under_memory_tracer"] > 0 else None,
        }
        rows.append(cell)
    return {"label": label, "bars": universe.length, "symbols": universe.symbols,
            "situations": {k: int(v.sum()) for k, v in masks.items()} | {"purged": int(purged.sum())},
            "fixed": fixed, "best_fixed_in_hindsight": best_fixed, "budgets": rows}


def summarize(markets: list[dict]) -> dict:
    """Rapports de coût sur tous les marchés et budgets (médiane et étendue) ; rappel du statut exploratoire."""
    out = {}
    for key in ("cpu", "wall"):
        ratios = [c["ratio_project_over_router"][key]["ratio"] for m in markets for c in m["budgets"]
                  if c["ratio_project_over_router"][key]["ratio"] is not None]
        out[key] = {"cells": len(ratios), "median_ratio": float(np.median(ratios)) if ratios else None,
                    "min_ratio": float(np.min(ratios)) if ratios else None, "max_ratio": float(np.max(ratios)) if ratios else None}
    pr40 = [c["ratio_project_over_router"]["cpu_as_measured_in_pr40"] for m in markets for c in m["budgets"]
            if c["ratio_project_over_router"]["cpu_as_measured_in_pr40"] is not None]
    mem = [c["ratio_project_over_router"]["peak_mb"] for m in markets for c in m["budgets"] if c["ratio_project_over_router"]["peak_mb"] is not None]
    slow = [c["learners"][n]["tracer_slowdown"] for m in markets for c in m["budgets"] for n in CONTENDERS if c["learners"][n]["tracer_slowdown"]]
    out["cpu_with_memory_tracer_as_in_pr40"] = {"median_ratio": float(np.median(pr40)) if pr40 else None}
    out["peak_memory"] = {"median_ratio": float(np.median(mem)) if mem else None}
    out["tracer_slowdown_by_learner"] = {n: float(np.median([c["learners"][n]["tracer_slowdown"] for m in markets for c in m["budgets"]
                                                          if c["learners"][n]["tracer_slowdown"]])) for n in CONTENDERS} if slow else {}
    out["selection_optimism_r"] = {}
    for n in CONTENDERS:
        gaps = [c["learners"][n]["selection_to_evaluation"]["optimism_r"] for m in markets for c in m["budgets"]
                if c["learners"][n]["selection_to_evaluation"]["optimism_r"] is not None]
        out["selection_optimism_r"][n] = {"cells": len(gaps), "median": float(np.median(gaps)) if gaps else None,
                                          "share_positive": float(np.mean([g > 0 for g in gaps])) if gaps else None}
    wins = {n: sum(1 for m in markets for c in m["budgets"] if c["learners"][n]["delta_vs_take_all_eur"] > 0) for n in CONTENDERS}
    out["cells_with_positive_delta_vs_take_all"] = wins
    out["status"] = "EXPLORATORY : coût de calcul mesuré ; aucun avantage de rendement n'en découle"
    return out
