"""Développement et gel de RegimeRouter-R1 (hors ligne, une seule fois, avant toute observation future).

Chaîne imposée par le protocole :
  apprentissage (issues mûres)  ->  calibration indépendante (issues mûres, fenêtre disjointe,
  ajoutées à la table rétrécie)  ->  présélection UNIQUE de la marge sur la fenêtre de sélection
  ->  gel de l'artefact.
La dernière fenêtre du développement (« contrôle ») ne règle rien : elle sert à mesurer
l'activité attendue pour l'analyse de puissance et l'écart sélection -> contrôle. Tout ce qui
est mesuré ici porte sur des périodes déjà étudiées : résultat EXPLORATOIRE.

Ce module importe les apprenants (donc scikit-learn) : il n'est jamais chargé par le moteur.
"""
from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np

from ..bars import Universe
from ..dataset import build_situations, split, time_windows
from ..execution import CostModel, PortfolioRules, run_portfolio
from ..experiments import buy_and_hold
from ..learners import Cash, ProjectMixte, RegimeRouter, SingleTeacher, TakeAll, labels
from ..teachers import TEACHER_NAMES
from ..velocity import intents_for, window_indices
from . import reference
from .r1 import HYPERPARAMETERS, build_artifact, margin_text

FRACTIONS = (0.5, 0.15, 0.15, 0.2)  # apprentissage, calibration, sélection, contrôle (comme la PR #40)
EMBARGO_BARS = 30


def reference_teacher(*parts) -> str:
    """Enseignant de référence, fixé par règle : meilleur R net moyen sur les issues mûres non refusées."""
    best, pick = -math.inf, TEACHER_NAMES[0]
    for t in TEACHER_NAMES:
        r = np.concatenate([p.net_r["teacher"][(p.teacher == t) & ~p.vetoed & np.isfinite(p.net_r["teacher"])] for p in parts])
        if len(r) and float(r.mean()) > best:
            best, pick = float(r.mean()), t
    return pick


def develop(universe: Universe, *, cost: CostModel, rules: PortfolioRules, provenance: dict) -> dict:
    assert RegimeRouter.prior == HYPERPARAMETERS["prior"]
    assert [margin_text(m) for m in RegimeRouter.margins] == HYPERPARAMETERS["margins"]
    s = build_situations(universe, cost, variants=("teacher",))
    w = time_windows(universe, FRACTIONS, embargo_bars=EMBARGO_BARS)
    masks, purged = split(s, w)
    parts = {k: s.take(v) for k, v in masks.items()}
    router = RegimeRouter()
    router.fit(parts["train"], parts["calibration"])
    router.select(parts["selection"])
    ref = reference_teacher(parts["train"], parts["calibration"])
    project = ProjectMixte()
    project.fit(parts["train"], parts["calibration"])
    project.select(parts["selection"])
    sel = parts["selection"]
    taken_sel = (~sel.vetoed) & (router.score(sel) > router.margin) & labels(sel)[0]
    training = {
        "data": provenance, "bars": universe.length, "symbols": universe.symbols,
        "period_ms": [int(universe.ts[0]), int(universe.ts[-1]) + universe.bars[universe.symbols[0]].tf_ms],
        "fractions": list(FRACTIONS), "embargo_bars": EMBARGO_BARS, "windows": w.to_dict(),
        "cost": asdict(cost), "rules": asdict(rules),
        "situations": {k: int(v.sum()) for k, v in masks.items()} | {"purged": int(purged.sum())},
        "preselection": {"window": "selection", "margin_r": margin_text(router.margin), "mean_net_r": router.selection_score,
                         "entries": int(taken_sel.sum()), "tries": len(RegimeRouter.margins)},
        "reference_teacher_rule": "meilleur R net moyen des issues mûres non refusées, apprentissage + calibration",
        "status": "développement sur périodes déjà étudiées : exploratoire",
    }
    artifact = build_artifact(router.table, router.margin, ref, training)
    arms = {"r1": router, "take_all": TakeAll(), f"teacher:{ref}": SingleTeacher(ref), "project_mixte": project, "cash": Cash()}
    control = {}
    for split_name in ("selection", "evaluation"):
        part, win = parts[split_name], window_indices(universe, w.bounds[split_name])
        rows = {}
        for name, learner in arms.items():
            if name in ("take_all", "cash") or name.startswith("teacher:"):
                learner.fit(parts["train"], parts["calibration"])
            enter, variant = learner.policy(part)
            res = run_portfolio(universe, intents_for(part, enter, variant), start=win[0], end=win[1], rules=rules, cost=cost)
            taken = part.net_r["teacher"][enter & np.isfinite(part.net_r["teacher"])]
            entries: dict[int, int] = {}
            for t in res.trades:
                day = t["entry_ts"] // 86_400_000
                entries[day] = entries.get(day, 0) + 1
            rows[name] = {"net_pnl_eur": res.net_pnl, "trades": len(res.trades), "max_drawdown": res.max_drawdown(),
                          "fees_eur": res.fees, "mean_net_r_taken": float(taken.mean()) if len(taken) else None,
                          "n_taken": int(len(taken)), "refused": res.refused, "daily": res.daily_returns(),
                          "entries_by_day": entries}
        hold = buy_and_hold(universe, win, cost, rules.capital)
        rows["buy_and_hold"] = {"net_pnl_eur": hold["net_pnl_eur"], "trades": len(universe.symbols), "daily": hold["_daily"]}
        control[split_name] = {"window_ms": list(w.bounds[split_name]), "bars": [int(win[0]), int(win[1])], "arms": rows}
    return {"artifact": artifact, "router": router, "project": project, "parts": parts, "windows": w, "control": control,
            "situations": s, "reference_bytes": reference.dump(project) if project.members else None}
