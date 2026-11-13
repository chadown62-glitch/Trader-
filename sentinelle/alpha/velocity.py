"""Learning Velocity Challenge : qui apprend le plus, par observation et par minute de calcul ?

Protocole (préenregistré : la configuration et son empreinte sont écrites AVANT toute évaluation) :
1. marchés déterministes (graines fixées), fenêtres apprentissage / calibration / sélection /
   évaluation disjointes, embargo et purge des issues tardives ;
2. budgets d'observations égaux pour tous : les ``n`` situations mûres les plus récentes ;
3. chaque concurrent apprend sur l'apprentissage, se calibre sur la calibration, règle son unique
   seuil sur la sélection, puis est évalué UNE fois sur l'évaluation, sans y apprendre (mode figé) ;
4. mesures prédictives (AUC, Brier, log-loss, calibration) ET, séparément, financières sur
   30 € fictifs (frais, demi-écart, glissement) : P&L net, trades, baisse maximale, rotation,
   résultat des abstentions, par climat et par actif ; coût CPU et mémoire ;
5. comparaison appariée jour par jour à la référence « toutes les entrées des enseignants »,
   intervalle par blocs de 7 jours, correction de Bonferroni sur le nombre d'essais ;
6. verdict « amélioration répliquée hors échantillon » seulement si la borne basse est positive
   sur au moins 2/3 des marchés indépendants ; sinon « non démontré » (résultat attendu et rapporté).
"""
from __future__ import annotations

import hashlib
import json
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import CAN_TRADE
from .bars import Universe
from .dataset import Situations, build_situations, split, time_windows
from .execution import CostModel, PortfolioRules, run_portfolio
from .learners import Learner, TakeAll, default_learners, labels
from .markets import REGIMES, synthetic_universe
from .stats import bonferroni, paired_daily_interval, predictive, replication_verdict


@dataclass(frozen=True)
class ChallengeConfig:
    seeds: tuple[int, ...] = (11, 23, 37)
    markets: tuple[tuple[str, float], ...] = (("regimes", 1.0), ("regimes", 0.5), ("regime_shift", 1.0), ("noise", 0.0))
    n_bars: int = 6000
    budgets: tuple[int | None, ...] = (50, 100, 200, 400, None)
    embargo_bars: int = 30
    confidence: float = 0.95
    fractions: tuple[float, ...] = (0.5, 0.15, 0.15, 0.2)
    cost: dict = field(default_factory=lambda: asdict(CostModel()))
    rules: dict = field(default_factory=lambda: asdict(PortfolioRules()))

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def window_indices(universe: Universe, bounds: tuple[int, int]) -> tuple[int, int]:
    close = universe.ts + universe.bars[universe.symbols[0]].tf_ms
    return int(np.searchsorted(close, bounds[0])), int(np.searchsorted(close, bounds[1], side="right"))


def recent_budget(train: Situations, n: int | None) -> Situations:
    ok, _ = labels(train)
    idx = np.flatnonzero(ok)
    if n is not None and len(idx) > n:
        idx = idx[np.argsort(train.decided_at[idx], kind="mergesort")][-n:]
    m = np.zeros(len(train), dtype=bool)
    m[idx] = True
    return train.take(m)


def intents_for(s: Situations, enter: np.ndarray, variant: np.ndarray) -> list:
    out = []
    for k in np.flatnonzero(enter):
        d = s.decisions[k]
        out.append((s.symbol[k], int(s.index[k]), d.plan, s.variant_outcomes[variant[k]][k]))
    return out


def evaluate_policy(universe: Universe, s_eval: Situations, enter: np.ndarray, variant: np.ndarray,
                    window: tuple[int, int], cost: CostModel, rules: PortfolioRules) -> dict:
    res = run_portfolio(universe, intents_for(s_eval, enter, variant), start=window[0], end=window[1], rules=rules, cost=cost)
    taken_r = np.array([s_eval.net_r[variant[k]][k] for k in np.flatnonzero(enter)])
    taken_r = taken_r[np.isfinite(taken_r)]
    skipped = (~enter) & (~s_eval.vetoed) & np.isfinite(s_eval.net_r["teacher"])
    regimes = {}
    for g in np.unique(s_eval.regime):
        m = enter & (s_eval.regime == g)
        rr = np.array([s_eval.net_r[variant[k]][k] for k in np.flatnonzero(m)])
        rr = rr[np.isfinite(rr)]
        regimes[REGIMES[g] if 0 <= g < len(REGIMES) else "inconnu"] = {"n": int(len(rr)), "mean_net_r": float(rr.mean()) if len(rr) else None}
    assets: dict[str, float] = {}
    for t in res.trades:
        assets[t["symbol"]] = assets.get(t["symbol"], 0.0) + t["pnl"]
    return {
        "net_pnl_eur": res.net_pnl, "final_eur": res.final, "trades": len(res.trades), "refused": res.refused,
        "fees_eur": res.fees, "max_drawdown": res.max_drawdown(), "turnover": res.turnover,
        "mean_net_r_taken": float(taken_r.mean()) if len(taken_r) else None, "n_taken": int(len(taken_r)),
        "abstention": {"n_skipped": int(skipped.sum()),
                       "mean_net_r_of_skipped": float(np.nanmean(s_eval.net_r["teacher"][skipped])) if skipped.any() else None,
                       "note": "issue contrefactuelle des entrées écartées ; négative = s'abstenir a évité des pertes"},
        "by_regime": regimes, "by_asset": assets, "_daily": res.daily_returns(),
    }


def run_learner(learner: Learner, train: Situations, cal: Situations, sel: Situations, ev: Situations) -> tuple[dict, np.ndarray, np.ndarray]:
    tracemalloc.start()
    t0 = time.process_time()
    learner.fit(train, cal)
    learner.select(sel)
    enter, variant = learner.policy(ev)
    p = learner.proba(ev)
    cpu = time.process_time() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ok, y = labels(ev)
    m = ok & ~ev.vetoed
    pred = {}
    if m.any():
        pred = predictive(y[m], p[m], getattr(learner, "base_rate", 0.5))
        ref = learner.reference_proba(ev)[m]
        from .stats import brier
        b_ref = brier(y[m], ref)
        pred["brier_teacher_rate"] = b_ref
        pred["brier_skill_vs_teacher_rate"] = (1 - pred["brier"] / b_ref) if b_ref else None
        from .stats import auc
        pred["auc_teacher_rate"] = auc(y[m], ref)  # ce qu'obtient la seule identité de l'enseignant
    return {"cpu_s": cpu, "peak_mb": peak / 2**20, "predictive": pred, "params": learner.describe()}, enter, variant


def market_run(kind: str, edge: float, seed: int, cfg: ChallengeConfig, learners_factory=default_learners,
               universe: Universe | None = None) -> dict:
    cost, rules = CostModel(**cfg.cost), PortfolioRules(**cfg.rules)
    u = universe if universe is not None else synthetic_universe(seed, kind=kind, edge=edge, n_bars=cfg.n_bars)
    s = build_situations(u, cost)
    w = time_windows(u, cfg.fractions, embargo_bars=cfg.embargo_bars)
    masks, purged = split(s, w)
    parts = {k: s.take(v) for k, v in masks.items()}
    ev_window = window_indices(u, w.bounds["evaluation"])
    sel_window = window_indices(u, w.bounds["selection"])
    base = TakeAll()
    base.fit(parts["train"], parts["calibration"])
    b_enter, b_var = base.policy(parts["evaluation"])
    baseline = evaluate_policy(u, parts["evaluation"], b_enter, b_var, ev_window, cost, rules)
    bs_enter, bs_var = base.policy(parts["selection"])
    baseline_sel = evaluate_policy(u, parts["selection"], bs_enter, bs_var, sel_window, cost, rules)
    runs = []
    for n in cfg.budgets:
        train = recent_budget(parts["train"], n)
        for learner in learners_factory():
            info, enter, variant = run_learner(learner, train, parts["calibration"], parts["selection"], parts["evaluation"])
            fin = evaluate_policy(u, parts["evaluation"], enter, variant, ev_window, cost, rules)
            sel_enter, sel_var = learner.policy(parts["selection"])
            fin_sel = evaluate_policy(u, parts["selection"], sel_enter, sel_var, sel_window, cost, rules)
            runs.append({"learner": learner.name, "budget": n if n is not None else int(labels(parts["train"])[0].sum()),
                         "budget_label": "tout" if n is None else str(n), "observations": int(len(train)), **info,
                         "financial": {k: v for k, v in fin.items() if not k.startswith("_")},
                         "_daily": fin["_daily"], "_daily_selection": fin_sel["_daily"],
                         "selection_net_pnl_eur": fin_sel["net_pnl_eur"]})
    return {"kind": kind, "edge": edge, "seed": seed, "universe": {"symbols": u.symbols, "bars": u.length,
                                                                     "provenance": sorted({b.provenance for b in u.bars.values()})},
            "situations": int(len(s)), "splits": {k: int(v.sum()) for k, v in masks.items()}, "purged": int(purged.sum()),
            "windows": w.to_dict(), "baseline": {k: v for k, v in baseline.items() if not k.startswith("_")},
            "_baseline_daily": baseline["_daily"], "_baseline_daily_selection": baseline_sel["_daily"], "runs": runs}


def preregister(cfg: ChallengeConfig, output: Path | None, learners: list[str]) -> dict:
    """Écrit le plan d'expérience AVANT toute évaluation (anti p-hacking) et le renvoie."""
    trials = len(learners) * len(cfg.budgets)
    plan = {"kind": "alpha-factory-velocity-preregistration", "config": asdict(cfg), "config_sha256": cfg.digest(),
            "learners": learners, "trials_per_market": trials, "registered_at": time.time(),
            "primary_comparison": "rendement quotidien apparié vs take_all, fenêtre d'évaluation, mode figé",
            "decision_rule": "borne basse > 0 (Bonferroni sur les essais) sur >= 2/3 des marchés indépendants d'une famille",
            "can_trade": CAN_TRADE}
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        path = output / "preregistration.json"
        if path.exists() and json.loads(path.read_text())["config_sha256"] != plan["config_sha256"]:
            raise ValueError("Un autre plan est déjà enregistré dans ce dossier : utilisez un nouveau dossier")
        if not path.exists():
            path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    return plan


def run_challenge(cfg: ChallengeConfig = ChallengeConfig(), *, output: Path | None = None,
                  learners_factory=default_learners, extra_markets: list[tuple[str, Universe]] | None = None) -> dict:
    names = [lr.name for lr in learners_factory()]
    plan = preregister(cfg, output, names)
    trials = plan["trials_per_market"]
    conf = bonferroni(cfg.confidence, trials)
    markets = [market_run(kind, edge, seed, cfg, learners_factory) for kind, edge in cfg.markets for seed in cfg.seeds]
    for label, universe in extra_markets or []:
        markets.append(market_run(label, float("nan"), -1, cfg, learners_factory, universe=universe))
    # comparaisons appariées : chaque concurrent vs take_all, sur les mêmes jours
    for m in markets:
        for r in m["runs"]:
            r["vs_take_all"] = paired_daily_interval(r["_daily"], m["_baseline_daily"], confidence=conf)
            r["absolute"] = paired_daily_interval(r["_daily"], {d: 0.0 for d in r["_daily"]}, confidence=conf)
    summary = summarize(markets, cfg)
    report = {"kind": "alpha-factory-velocity-report", "preregistration": plan, "confidence_per_interval": conf,
              "markets": [{k: v for k, v in m.items() if not k.startswith("_")} | {"runs": [{k: v for k, v in r.items() if not k.startswith("_")} for r in m["runs"]]}
                          for m in markets],
              "summary": summary, "can_trade": CAN_TRADE,
              "claim": "recherche hors ligne ; aucun gain futur n'est promis ; aucune promotion"}
    report["_markets_raw"] = markets
    if output is not None:
        (output / "report.json").write_text(json.dumps({k: v for k, v in report.items() if not k.startswith("_")},
                                                       indent=2, ensure_ascii=False, default=float))
    return report


def summarize(markets: list[dict], cfg: ChallengeConfig) -> dict:
    families: dict[str, list[dict]] = {}
    for m in markets:
        families.setdefault(f"{m['kind']}@{m['edge']}", []).append(m)
    out = {}
    for fam, ms in families.items():
        table = {}
        keys = sorted({(r["learner"], r["budget_label"]) for m in ms for r in m["runs"]}, key=lambda k: (k[0], _bud(k[1])))
        for learner, budget in keys:
            rows = [r for m in ms for r in m["runs"] if r["learner"] == learner and r["budget_label"] == budget]
            base = [m["baseline"]["net_pnl_eur"] for m in ms for r in m["runs"] if r["learner"] == learner and r["budget_label"] == budget]
            lows = [r["vs_take_all"]["ci_low"] for r in rows]
            pred = [r["predictive"] for r in rows if r["predictive"]]
            delta = [r["financial"]["net_pnl_eur"] - b for r, b in zip(rows, base)]
            cpu_min = np.mean([r["cpu_s"] for r in rows]) / 60
            mb = np.mean([r["peak_mb"] for r in rows])
            obs = np.mean([max(1, r["observations"]) for r in rows])
            table[f"{learner}|{budget}"] = {
                "learner": learner, "budget": budget, "markets": len(rows),
                "mean_net_pnl_eur": float(np.mean([r["financial"]["net_pnl_eur"] for r in rows])),
                "mean_delta_vs_take_all_eur": float(np.mean(delta)),
                "mean_trades": float(np.mean([r["financial"]["trades"] for r in rows])),
                "mean_max_drawdown": float(np.mean([r["financial"]["max_drawdown"] for r in rows])),
                "mean_brier_skill": _mean([p.get("brier_skill_vs_teacher_rate") for p in pred]),
                "mean_auc": _mean([p.get("auc") for p in pred]),
                "mean_auc_teacher_rate": _mean([p.get("auc_teacher_rate") for p in pred]),
                "mean_logloss": _mean([p.get("logloss") for p in pred]),
                "mean_ece": _mean([p.get("ece") for p in pred]),
                "cpu_s": float(np.mean([r["cpu_s"] for r in rows])), "peak_mb": float(mb),
                "delta_eur_per_100_obs": float(np.mean(delta) / obs * 100),
                "delta_eur_per_cpu_minute": float(np.mean(delta) / cpu_min) if cpu_min > 0 else None,
                "delta_eur_per_mb": float(np.mean(delta) / mb) if mb > 0 else None,
                **replication_verdict(lows),
            }
        out[fam] = table
    return out


def _bud(label: str) -> float:
    return float("inf") if label == "tout" else float(label)


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    return float(np.mean(xs)) if xs else None
