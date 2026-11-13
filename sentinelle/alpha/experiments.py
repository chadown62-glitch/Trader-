"""Ablations du Shadow Council et Shadow Tournament (hors ligne, mode figé).

Ablation : sur les mêmes jours et les mêmes coûts, on compare le conseil complet, le conseil
privé de chaque agent, chaque agent seul, un vote à la majorité, un modèle unique et les
références simples (toutes les entrées, rien, achat-conservation). La contribution marginale
d'un agent = conseil complet − conseil sans lui ; un agent dont la contribution n'est pas
démontrée est marqué « retirable » au lieu de gonfler le système.

Tournoi : tous les concurrents (apprenants × budgets, variantes du conseil) sont classés sur la
fenêtre de SÉLECTION ; seul le vainqueur est jugé sur l'ÉVALUATION, une fois, sans y apprendre.
On mesure aussi la « malédiction du vainqueur » (score de sélection − score d'évaluation) et si
le classement de sélection prédit celui d'évaluation.
"""
from __future__ import annotations

import numpy as np

from .council import AGENTS, build_council, run_council
from .dataset import build_situations, split, time_windows
from .execution import CostModel, PortfolioRules, run_portfolio
from .learners import ProjectMixte, TakeAll
from .ledger import ShadowLedger
from .markets import synthetic_macro_calendar, synthetic_universe
from .stats import paired_daily_interval, replication_verdict
from .velocity import ChallengeConfig, evaluate_policy, window_indices


def buy_and_hold(universe, window: tuple[int, int], cost: CostModel, capital: float = 30.0) -> dict:
    """Achat à parts égales à la première ouverture, conservation jusqu'à la fin (référence passive)."""
    start, end = window
    k = len(universe.symbols)
    qty = {s: (capital / k) / (1 + cost.fee) / float(universe.bars[s].open[start]) for s in universe.symbols}
    tf = universe.bars[universe.symbols[0]].tf_ms
    eq = [(int(universe.ts[j]) + tf, sum(q * float(universe.bars[s].close[j]) * (1 - cost.fee) for s, q in qty.items()))
          for j in range(start, end)]
    closes: dict[int, float] = {}
    for t, v in eq:
        closes[(t - 1) // 86_400_000] = v
    daily, last = {}, capital
    for d in sorted(closes):
        daily[d] = closes[d] / last - 1
        last = closes[d]
    return {"net_pnl_eur": eq[-1][1] - capital if eq else 0.0, "_daily": daily}


def prepare(kind: str, edge: float, seed: int, cfg: ChallengeConfig, universe=None):
    cost, rules = CostModel(**cfg.cost), PortfolioRules(**cfg.rules)
    u = universe if universe is not None else synthetic_universe(seed, kind=kind, edge=edge, n_bars=cfg.n_bars)
    s = build_situations(u, cost)
    w = time_windows(u, cfg.fractions, embargo_bars=cfg.embargo_bars)
    masks, _ = split(s, w)
    parts = {k: s.take(v) for k, v in masks.items()}
    return u, parts, w, cost, rules


def council_ablation(kind: str, edge: float, seed: int, cfg: ChallengeConfig, *, ledger_path=None, universe=None,
                     confidence: float = 0.95) -> dict:
    u, parts, w, cost, rules = prepare(kind, edge, seed, cfg, universe)
    ev = parts["evaluation"]
    window = window_indices(u, w.bounds["evaluation"])
    sel_window = window_indices(u, w.bounds["selection"])
    macro = synthetic_macro_calendar(u, seed) if universe is None else ()
    model = ProjectMixte()
    model.fit(parts["train"], parts["calibration"])
    variants = {"council_full": dict(enabled=frozenset(AGENTS))}
    for a in AGENTS:
        variants[f"without:{a}"] = dict(enabled=frozenset(AGENTS) - {a})
        variants[f"only:{a}"] = dict(enabled=frozenset({a}))
    variants["vote"] = dict(enabled=frozenset(AGENTS), mode="vote")
    results: dict[str, dict] = {}
    decisions_full = None
    for name, opts in variants.items():
        for split_name, s_part, win in (("evaluation", ev, window), ("selection", parts["selection"], sel_window)):
            council = build_council(parts["train"], parts["calibration"], cost, rules, eval_situations=s_part, model=model, **opts)
            ledger = ShadowLedger(ledger_path) if (ledger_path and name == "council_full" and split_name == "evaluation") else None
            intents, decisions = run_council(u, council, s_part, win, cost=cost, macro_events=macro, ledger=ledger, rules=rules)
            res = run_portfolio(u, intents, start=win[0], end=win[1], rules=rules, cost=cost)
            key = name if split_name == "evaluation" else name + "@selection"
            results[key] = {"net_pnl_eur": res.net_pnl, "trades": len(res.trades), "max_drawdown": res.max_drawdown(),
                            "abstentions": sum(1 for d in decisions if d.action != "ENTER"),
                            "entries_proposed": sum(1 for d in decisions if d.action == "ENTER"), "_daily": res.daily_returns()}
            if ledger is not None:
                results[key]["ledger_records"] = len(ledger.records)
                results[key]["ledger_verified"] = ledger.verify()
            if name == "council_full" and split_name == "evaluation":
                decisions_full = decisions
    take = TakeAll()
    take.fit(parts["train"], parts["calibration"])
    for split_name, s_part, win in (("evaluation", ev, window), ("selection", parts["selection"], sel_window)):
        e, v = take.policy(s_part)
        base = evaluate_policy(u, s_part, e, v, win, cost, rules)
        model.select(parts["selection"])
        me, mv = model.policy(s_part)
        single = evaluate_policy(u, s_part, me, mv, win, cost, rules)
        suffix = "" if split_name == "evaluation" else "@selection"
        results["take_all" + suffix] = {"net_pnl_eur": base["net_pnl_eur"], "trades": base["trades"], "max_drawdown": base["max_drawdown"], "_daily": base["_daily"]}
        results["single_model" + suffix] = {"net_pnl_eur": single["net_pnl_eur"], "trades": single["trades"],
                                            "max_drawdown": single["max_drawdown"], "_daily": single["_daily"]}
        results["cash" + suffix] = {"net_pnl_eur": 0.0, "trades": 0, "max_drawdown": 0.0, "_daily": {d: 0.0 for d in base["_daily"]}}
        bh = buy_and_hold(u, win, cost)
        results["buy_and_hold" + suffix] = {"net_pnl_eur": bh["net_pnl_eur"], "trades": len(u.symbols), "max_drawdown": None, "_daily": bh["_daily"]}
    ref = results["take_all"]["_daily"]
    full = results["council_full"]["_daily"]
    for name, r in results.items():
        if name.endswith("@selection"):
            continue
        r["vs_take_all"] = paired_daily_interval(r["_daily"], ref, confidence=confidence)
        r["vs_council_full"] = paired_daily_interval(full, r["_daily"], confidence=confidence) if name.startswith("without:") else None
    examples = [d.to_record() for d in (decisions_full or []) if d.disagreements or d.vetoes][:5]
    return {"kind": kind, "edge": edge, "seed": seed, "results": results, "examples": examples,
            "macro_events": len(macro), "evaluation_situations": len(ev)}


def summarize_ablation(markets: list[dict]) -> dict:
    families: dict[str, list[dict]] = {}
    for m in markets:
        families.setdefault(f"{m['kind']}@{m['edge']}", []).append(m)
    out = {}
    for fam, ms in families.items():
        names = [n for n in ms[0]["results"] if not n.endswith("@selection")]
        table = {}
        for n in names:
            rows = [m["results"][n] for m in ms]
            entry = {"mean_net_pnl_eur": float(np.mean([r["net_pnl_eur"] for r in rows])),
                     "mean_trades": float(np.mean([r["trades"] for r in rows])),
                     "vs_take_all": replication_verdict([r["vs_take_all"]["ci_low"] for r in rows])["verdict"]}
            if n.startswith("without:"):
                lows = [r["vs_council_full"]["ci_low"] for r in rows]
                entry["marginal_contribution_eur"] = float(np.mean([m["results"]["council_full"]["net_pnl_eur"] - m["results"][n]["net_pnl_eur"] for m in ms]))
                v = replication_verdict(lows)
                entry["agent_utile"] = v["verdict"]
                entry["recommandation"] = "garder" if v["verdict"].startswith("amélioration") else "retirable (contribution non démontrée)"
            table[n] = entry
        out[fam] = table
    return out


def tournament(velocity_markets: list[dict], ablations: list[dict]) -> dict:
    """Classement sur la SÉLECTION, jugement du seul vainqueur sur l'ÉVALUATION."""
    out = []
    for vm in velocity_markets:
        ab = next((a for a in ablations if (a["kind"], a["seed"]) == (vm["kind"], vm["seed"])), None)
        cands = []
        for r in vm["runs"]:
            sel = r["_daily_selection"]
            cands.append({"name": f"{r['learner']}|{r['budget_label']}", "selection": float(np.mean(list(sel.values()))) if sel else 0.0,
                          "_eval_daily": r["_daily"], "evaluation_pnl": r["financial"]["net_pnl_eur"]})
        if ab is not None:
            for n in ("council_full", "vote"):
                sel = ab["results"][n + "@selection"]["_daily"]
                cands.append({"name": n, "selection": float(np.mean(list(sel.values()))) if sel else 0.0,
                              "_eval_daily": ab["results"][n]["_daily"], "evaluation_pnl": ab["results"][n]["net_pnl_eur"]})
        cands.sort(key=lambda c: (-c["selection"], c["name"]))
        win = cands[0]
        ev_means = [float(np.mean(list(c["_eval_daily"].values()))) if c["_eval_daily"] else 0.0 for c in cands]
        sel_means = [c["selection"] for c in cands]
        rank_corr = _spearman(sel_means, ev_means)
        paired = paired_daily_interval(win["_eval_daily"], vm["_baseline_daily"])
        out.append({"kind": vm["kind"], "edge": vm["edge"], "seed": vm["seed"], "candidates": len(cands), "winner": win["name"],
                    "winner_selection_mean_daily": win["selection"], "winner_evaluation_mean_daily": ev_means[0],
                    "winner_curse": win["selection"] - ev_means[0], "winner_evaluation_pnl_eur": win["evaluation_pnl"],
                    "winner_vs_take_all": paired, "selection_vs_evaluation_rank_correlation": rank_corr})
    fams: dict[str, list[dict]] = {}
    for t in out:
        fams.setdefault(f"{t['kind']}@{t['edge']}", []).append(t)
    verdicts = {f: replication_verdict([t["winner_vs_take_all"]["ci_low"] for t in ts]) for f, ts in fams.items()}
    return {"markets": out, "verdicts": verdicts}


def _spearman(a, b) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])
