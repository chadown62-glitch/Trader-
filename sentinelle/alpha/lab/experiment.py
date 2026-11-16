"""Banc du laboratoire : tous les candidats, les références, la règle de décision et l'autopsie des échecs.

Règle de décision (fixée dans le plan préenregistré, avant toute mesure). Un candidat n'est
« retenu pour un futur protocole prospectif » que s'il passe TOUTES les conditions :

1. gain absolu : rendement quotidien net moyen > 0, probabilité corrigée des essais multiples < 5 %
   (battre l'abstention) ;
2. gain relatif : mieux que la simple détention à exposition égale, probabilité corrigée < 5 %
   (apporter autre chose que du risque de marché) ;
3. tenue dans le temps : résultat net positif ET écart à la référence positif sur chacune des deux
   périodes indépendantes ;
4. tenue aux coûts : résultat net encore positif dans le scénario de coûts dégradé.

Être retenu ne prouve rien : c'est un tri sur des données historiques, donc exploratoire. Un
candidat non retenu reçoit des motifs d'échec mesurés (coûts, absence d'avantage brut, simple
exposition au marché, instabilité, concentration) ; rien n'est inventé quand aucun motif ne ressort.
"""
from __future__ import annotations

import numpy as np

from . import CAN_TRADE, CAPITAL, STATUS, stats
from .costs import CostHypotheses
from .data import Panel
from .simulate import Features, SimResult, simulate
from .strategies import BY_NAME, MIN_AGE, PEG_WINDOW, TOP, Context, Spec
from .data import PEG_VOLATILITY

RETAINED, NOT_RETAINED = "RETENU_POUR_PROTOCOLE_PROSPECTIF", "NON_RETENU"
REASONS = {
    "NO_GROSS_EDGE": "aucun avantage avant coûts : la règle ne gagne pas, même gratuite",
    "COSTS_EXCEED_GROSS_EDGE": "l'avantage brut existe mais les coûts le dépassent",
    "NOT_SIGNIFICANT_ABSOLUTE": "gain absolu non démontré une fois les essais multiples pris en compte",
    "NO_VALUE_BEYOND_MARKET_EXPOSURE": "ne fait pas mieux qu'une simple détention de même exposition",
    "UNSTABLE_ACROSS_PERIODS": "ne tient pas sur les deux périodes indépendantes",
    "FRAGILE_TO_COSTS": "résultat négatif dès que les coûts sont dégradés",
    "UNKNOWN": "aucun motif mesurable",
}
FLAGS = {
    "CONCENTRATED": "plus de la moitié des gains vient d'un seul actif",
    "DRAWDOWN_ABOVE_RISK_LIMIT": "baisse maximale au-dessus de la limite de risque du projet",
    "FEW_TRADES": "moins de 30 ordres : peu d'information",
    "ORDERS_BLOCKED_BY_MINIMUM": "des ordres n'ont pas pu passer sous le minimum de la plateforme",
    "FORCED_EXITS": "ventes imposées par des retraits de la cote",
}


def benchmark_returns(panel: Panel, feats: Features, start: int, end: int, *, majors: tuple, exclude: frozenset = frozenset()) -> dict:
    """Rendements quotidiens des références de marché, sans frais (avantage laissé aux références).

    ``market`` : moyenne, chaque jour, des actifs de l'univers liquide tel qu'il était connu la VEILLE.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        r = panel.close[1:] / panel.close[:-1] - 1
    r = np.vstack([np.full((1, r.shape[1]), np.nan), r])
    out = {"btc": np.zeros(end - start + 1), "majors": np.zeros(end - start + 1), "market": np.zeros(end - start + 1)}
    for t in range(start, end + 1):
        ctx = Context(panel.view(t - 1), feats.age[t - 1], feats.medvol[t - 1], feats.vol90[t - 1], {}, exclude, majors)
        k = t - start
        cols = {"btc": [ctx.col("BTC")] if ctx.col("BTC") is not None else [],
                "majors": [j for j in (ctx.col(b) for b in majors) if j is not None],
                "market": ctx.universe(min_age=MIN_AGE, top=TOP)}
        for name, js in cols.items():
            vals = r[t, js] if js else np.array([])
            vals = vals[np.isfinite(vals)]
            out[name][k] = float(vals.mean()) if len(vals) else 0.0
    return out


def _sim_summary(res: SimResult) -> dict:
    m = stats.metrics(res.returns, res.equity, res.initial)
    return {**m, "trades": res.trades, "turnover": res.turnover, "fees": res.fees, "spread_slippage": res.spread_slippage,
            "mean_exposure": float(res.exposure.mean()), "blocked": res.blocked, "forced_exits": res.forced_exits}


def run_experiment(panel: Panel, plan: dict, hyp: CostHypotheses, *, features: Features | None = None) -> dict:
    """Exécute le plan tel qu'enregistré. Refuse un candidat absent du plan ou un scénario de coûts inconnu."""
    feats = features or Features(panel)
    start, split, end = panel.index_of(plan["window"]["start"]), panel.index_of(plan["window"]["split"]), panel.index_of(plan["window"]["end"])
    if not start < split <= end:
        raise ValueError("Fenêtres incohérentes")
    names = list(plan["candidates"])
    specs: list[Spec] = []
    for n in names:
        if n not in BY_NAME or BY_NAME[n].family == "reference":
            raise ValueError(f"Candidat non enregistré dans le code : {n}")
        if BY_NAME[n].describe() != plan["candidate_definitions"][n]:
            raise ValueError(f"La définition de {n} a changé depuis l'enregistrement")
        specs.append(BY_NAME[n])
    primary, stress, gross = hyp.scenario(hyp.primary), hyp.scenario(hyp.stress), hyp.scenario(hyp.gross)
    majors = tuple(plan["universe"]["majors"])
    days = panel.days[start:end + 1]
    bounds = [("P1", int(days[0]), int(panel.days[split - 1])), ("P2", int(panel.days[split]), int(days[-1]))]
    p1 = days < panel.days[split]
    bench = benchmark_returns(panel, feats, start, end, majors=majors)
    plan_stats = {"block_days": plan["analysis"]["block_days"], "bootstrap_reps": plan["analysis"]["bootstrap_reps"],
                  "bootstrap_seed": plan["analysis"]["bootstrap_seed"], "confidence": plan["analysis"]["confidence"]}

    def sim(spec: Spec, cost, **kw) -> SimResult:
        return simulate(panel, spec, cost, start=start, end=end, capital=CAPITAL, features=feats, **kw)

    runs = {s.name: sim(s, primary) for s in specs}
    refs = {n: sim(BY_NAME[n], primary) for n in plan["references"]}
    absolute = np.column_stack([runs[n].returns for n in names])
    matched = {n: stats.exposure_matched(runs[n].exposure, bench[BY_NAME[n].benchmark]) for n in names}
    relative = np.column_stack([runs[n].returns - matched[n][0] for n in names])
    fw = plan["analysis"]["familywise"]
    test_abs = stats.familywise(absolute, block=fw["block_days"], reps=fw["reps"], seed=fw["seed"])
    test_rel = stats.familywise(relative, block=fw["block_days"], reps=fw["reps"], seed=fw["seed"])
    alpha, limit = plan["analysis"]["alpha"], plan["analysis"]["risk_limit"]
    candidates = {}
    for k, spec in enumerate(specs):
        res = runs[spec.name]
        net = _sim_summary(res)
        g, st = sim(spec, gross), sim(spec, stress)
        rel = relative[:, k]
        periods = stats.by_period(days, res.returns, bounds)
        rel_periods = {"P1": float(rel[p1].mean()), "P2": float(rel[~p1].mean())}
        conc = stats.concentration(res.by_asset)
        checks = {
            "absolute_gain": bool(net["net_pnl"] > 0 and test_abs["p_adjusted"][k] < alpha),
            "relative_gain": bool(rel.mean() > 0 and test_rel["p_adjusted"][k] < alpha),
            "holds_in_both_periods": bool(periods["P1"]["return"] > 0 and periods["P2"]["return"] > 0 and rel_periods["P1"] > 0 and rel_periods["P2"] > 0),
            "survives_stressed_costs": bool(st.net_pnl > 0),
        }
        reasons = []
        if g.net_pnl <= 0:
            reasons.append("NO_GROSS_EDGE")
        elif net["net_pnl"] <= 0:
            reasons.append("COSTS_EXCEED_GROSS_EDGE")
        if net["net_pnl"] > 0 and not checks["absolute_gain"]:
            reasons.append("NOT_SIGNIFICANT_ABSOLUTE")
        if not checks["relative_gain"]:
            reasons.append("NO_VALUE_BEYOND_MARKET_EXPOSURE")
        if not checks["holds_in_both_periods"]:
            reasons.append("UNSTABLE_ACROSS_PERIODS")
        if net["net_pnl"] > 0 and not checks["survives_stressed_costs"]:
            reasons.append("FRAGILE_TO_COSTS")
        retained = all(checks.values())
        flags = [f for f, on in (("CONCENTRATED", (conc["top_share_of_gains"] or 0) > 0.5),
                                 ("DRAWDOWN_ABOVE_RISK_LIMIT", net["max_drawdown"] > limit), ("FEW_TRADES", res.trades < 30),
                                 ("ORDERS_BLOCKED_BY_MINIMUM", any("minimum" in b for b in res.blocked)),
                                 ("FORCED_EXITS", res.forced_exits > 0)) if on]
        sharpe = net["sharpe"]
        candidates[spec.name] = {
            "family": spec.family, "label": spec.label, "benchmark": spec.benchmark,
            "verdict": RETAINED if retained else NOT_RETAINED,
            "reasons": [] if retained else (reasons or ["UNKNOWN"]), "flags": flags, "checks": checks,
            "net": net,
            "absolute": {"interval": stats.mean_interval(res.returns, plan_stats), "t": test_abs["t"][k], "p_raw": test_abs["p_raw"][k],
                         "p_adjusted": test_abs["p_adjusted"][k]},
            "relative": {"reference": f"{spec.benchmark} à {matched[spec.name][1]:.0%} d'exposition", "exposure": matched[spec.name][1],
                         "interval": stats.mean_interval(rel, plan_stats), "t": test_rel["t"][k], "p_raw": test_rel["p_raw"][k],
                         "p_adjusted": test_rel["p_adjusted"][k], "by_period_mean_daily": rel_periods},
            "periods": periods, "years": stats.by_year(days, res.returns),
            "costs": {"gross_pnl": g.net_pnl, "net_pnl": net["net_pnl"], "stressed_pnl": st.net_pnl, "fees": res.fees,
                      "spread_slippage": res.spread_slippage, "cost_drag": g.net_pnl - net["net_pnl"]},
            "market": stats.alpha_beta(res.returns, bench[spec.benchmark]),
            "concentration": conc,
            "risk": stats.risk_scaled(net, limit),
            "vs_hold_btc": stats.mean_interval(res.returns - refs["hold_btc"].returns, plan_stats) if "hold_btc" in refs else None,
            "years_to_prove_at_observed_sharpe": (7.85 / sharpe ** 2) if sharpe > 0 else None,
        }
    generalization = _generalization(panel, plan, feats, specs, primary, start, end, plan_stats)
    retained = [n for n, c in candidates.items() if c["verdict"] == RETAINED]
    by_family = {}
    for n, c in candidates.items():
        by_family.setdefault(c["family"], []).append(n)
    return {
        "kind": "alpha-lab-experiment-report", "status": STATUS, "can_trade": CAN_TRADE, "plan_id": plan["plan_id"],
        "window": {**plan["window"], "days": int(len(days)), "p1_days": int(p1.sum()), "p2_days": int((~p1).sum())},
        "costs": {"version": hyp.version, "sha256": hyp.sha256, "primary": hyp.primary, "stress": hyp.stress, "status": hyp.status},
        "tests": {"candidates": len(names), "alpha": alpha, "absolute": test_abs, "relative": test_rel, "names": names},
        "candidates": candidates,
        "references": {n: _sim_summary(r) | {"years": stats.by_year(days, r.returns), "periods": stats.by_period(days, r.returns, bounds)}
                       for n, r in refs.items()},
        "benchmarks": {n: {"total_return": float(np.prod(1 + v) - 1), "p1": float(np.prod(1 + v[p1]) - 1), "p2": float(np.prod(1 + v[~p1]) - 1),
                           "years": stats.by_year(days, v)} for n, v in bench.items()},
        "generalization": generalization,
        "summary": {"retained": retained, "candidates": len(names), "by_family": by_family,
                    "positive_net": [n for n, c in candidates.items() if c["net"]["net_pnl"] > 0],
                    "positive_gross": [n for n, c in candidates.items() if c["costs"]["gross_pnl"] > 0],
                    "reason_counts": {r: sum(1 for c in candidates.values() if r in c["reasons"]) for r in REASONS},
                    "conclusion": ("aucun candidat ne passe la règle de décision : aucun avantage net démontré" if not retained else
                                   f"{len(retained)} candidat(s) retenu(s) pour un futur protocole prospectif ; aucune preuve de rentabilité")},
        "_daily": {"days": days.tolist(), "candidates": {n: runs[n].returns.tolist() for n in names},
                   "references": {n: r.returns.tolist() for n, r in refs.items()}, "benchmarks": {n: v.tolist() for n, v in bench.items()},
                   "exposure": {n: runs[n].exposure.tolist() for n in names}},
    }


def _generalization(panel, plan, feats, specs, cost, start, end, plan_stats) -> dict:
    """La règle tient-elle ailleurs ? Univers privé des cryptos déjà étudiées par le projet ; autres paires pour les règles « majeurs »."""
    g = plan["generalization"]
    exclude = frozenset(g["exclude_already_studied"])
    out = {"exclude_already_studied": sorted(exclude), "broad": {}, "other_pairs": {}}
    bench_ex = benchmark_returns(panel, feats, start, end, majors=tuple(plan["universe"]["majors"]), exclude=exclude)
    for spec in specs:
        if spec.benchmark == "market":
            res = simulate(panel, spec, cost, start=start, end=end, features=feats, exclude=exclude)
            ref, e = stats.exposure_matched(res.exposure, bench_ex["market"])
            m = stats.metrics(res.returns, res.equity, res.initial)
            out["broad"][spec.name] = {"net_pnl": m["net_pnl"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"], "trades": res.trades,
                                       "relative": stats.mean_interval(res.returns - ref, plan_stats), "exposure": e}
        elif spec.benchmark == "majors":
            rows = {}
            for pair in g["other_pairs"]:
                pair = tuple(pair)
                res = simulate(panel, spec, cost, start=start, end=end, features=feats, majors=pair)
                b = benchmark_returns(panel, feats, start, end, majors=pair)["majors"]
                ref, e = stats.exposure_matched(res.exposure, b)
                m = stats.metrics(res.returns, res.equity, res.initial)
                rows["+".join(pair)] = {"net_pnl": m["net_pnl"], "sharpe": m["sharpe"], "max_drawdown": m["max_drawdown"], "trades": res.trades,
                                        "relative_mean_daily": float((res.returns - ref).mean()), "exposure": e,
                                        "hold_total_return": float(np.prod(1 + b) - 1)}
            out["other_pairs"][spec.name] = rows
    out["broad_positive"] = sorted(n for n, v in out["broad"].items() if v["net_pnl"] > 0 and (v["relative"]["mean"] or 0) > 0)
    out["other_pairs_positive_share"] = {n: float(np.mean([v["net_pnl"] > 0 and v["relative_mean_daily"] > 0 for v in rows.values()]))
                                         for n, rows in out["other_pairs"].items()}
    return out


def universe_rule() -> dict:
    return {"min_listed_days": MIN_AGE, "top_by_median_quote_volume": TOP, "volume_window_days": 30,
            "peg_filter": {"window_days": PEG_WINDOW, "min_daily_volatility": PEG_VOLATILITY}}
