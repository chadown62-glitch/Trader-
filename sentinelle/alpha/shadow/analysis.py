"""Analyse préenregistrée : couverture, intégrité, comparaison primaire unique, verdict, puissance.

Une seule hypothèse, une seule comparaison primaire (R1 contre « toutes les entrées »), une seule
lecture à la fin de la fenêtre. Pour parler d'amélioration il faut À LA FOIS :
- borne basse du rendement quotidien net de R1 > 0 (gain absolu : « ne rien faire » ne suffit pas) ;
- borne basse de l'écart quotidien apparié R1 − référence > 0 (gain relatif).
Les deux conditions sont exigées ensemble (test d'intersection-union : pas de correction à
ajouter, un seul essai). Les autres bras sont décrits, jamais utilisés pour conclure.

Pendant la fenêtre, le rapport ne montre que la couverture et la sécurité : les résultats sont
masqués jusqu'à la fin, pour qu'aucune décision ne soit prise « en regardant ».
"""
from __future__ import annotations

import numpy as np

from ...ai.evaluation import block_interval
from .contract import make, sha256
from .ledger import LedgerError, precedence, verify_anchors, verify_chain

DAY_MS = 86_400_000
CLAIM = "simulation fictive ; aucun gain futur promis ; aucune promotion automatique"
DEFAULT_PLAN = {
    "primary_reference": "take_all", "confidence": 0.95, "block_days": 7, "bootstrap_reps": 2000, "bootstrap_seed": 701,
    "min_decision_coverage": 0.95, "min_book_entry_share": 0.90, "min_precedence_share": 0.95, "min_r1_entries": 1,
}


def daily_returns(points: list[tuple[int, float]], initial: float) -> dict[int, float]:
    """Rendement par jour UTC (une valeur à minuit clôt la journée précédente), comme ``execution.PortfolioResult``."""
    closes: dict[int, float] = {}
    for t, v in points:
        closes[(t - 1) // DAY_MS] = v
    out, last = {}, initial
    for d in sorted(closes):
        out[d] = closes[d] / last - 1 if last > 0 else 0.0
        last = closes[d]
    return out


def interval(x: np.ndarray, plan: dict) -> dict:
    ci = block_interval(x, block=plan["block_days"], seed=plan["bootstrap_seed"], reps=plan["bootstrap_reps"],
                        confidence=plan["confidence"]) if len(x) else None
    return {"days": int(len(x)), "mean": float(x.mean()) if len(x) else None,
            "ci_low": None if ci is None else float(ci[0]), "ci_high": None if ci is None else float(ci[1])}


def primary_test(candidate: dict[int, float], reference: dict[int, float], plan: dict) -> dict:
    days = sorted(set(candidate) & set(reference))
    x = np.array([candidate[d] for d in days])
    y = np.array([reference[d] for d in days])
    absolute, relative = interval(x, plan), interval(x - y, plan)
    lows = (absolute["ci_low"], relative["ci_low"])
    highs = (absolute["ci_high"], relative["ci_high"])
    if None in lows:
        outcome = "undetermined"
    elif lows[0] > 0 and lows[1] > 0:
        outcome = "supported"
    elif highs[0] < 0 or highs[1] < 0:
        outcome = "regression"
    else:
        outcome = "not_demonstrated"
    return {"absolute": absolute, "relative": relative, "outcome": outcome}


def read_ledger(lines: list[dict]) -> dict:
    """Extrait du registre ce dont l'analyse a besoin (lignes des pas clos uniquement)."""
    report = verify_chain(lines)
    valid = report["valid"]
    session = next((ln for ln in valid if ln["kind"] == "session"), None)
    if session is None:
        raise LedgerError("Registre sans session")
    cfg = session["body"]["config"]
    arms = session["body"]["arms"]
    equity = {a: [] for a in arms}
    decisions, outcomes, rejections, admissions = [], [], {}, {"DECIDABLE": 0, "HISTORY": 0, "PENDING": 0}
    aborted_at, final = None, False
    for ln in valid:
        b = ln["body"]
        if ln["kind"] == "settlement":
            for a, v in b["arms"].items():
                equity[a].append((b["close_at_ms"], v["equity_eur"]))
            aborted_at, final = b["r1_aborted_at_ms"], final or b["final"]
        elif ln["kind"] == "decision":
            decisions.append(ln)
        elif ln["kind"] == "outcome":
            outcomes.append(b)
        elif ln["kind"] == "rejection":
            rejections[b["code"]] = rejections.get(b["code"], 0) + 1
        elif ln["kind"] == "admission":
            admissions[b["status"]] += 1
    return {"session": session, "config": cfg, "arms": arms, "equity": equity, "decisions": decisions, "outcomes": outcomes,
            "rejections": rejections, "admissions": admissions, "aborted_at": aborted_at, "final": final,
            "void_lines": len(report["void"]), "recoveries": sum(1 for ln in valid if ln["kind"] == "recovery"),
            "uncommitted": len(report["uncommitted"])}


def coverage(data: dict, now_ms: int) -> dict:
    cfg = data["config"]
    tf, start, end = cfg["tf_ms"], cfg["start_ms"], cfg["end_ms"]
    horizon = min(end, (now_ms // tf) * tf)
    slots = max(0, (horizon - start) // tf + 1) if horizon >= start else 0
    expected = slots * len(cfg["symbols"])
    by_action: dict[str, int] = {}
    refusals: dict[str, int] = {}
    seen = set()
    for ln in data["decisions"]:
        b = ln["body"]
        seen.add((b["symbol"], b["as_of_ms"]))
        by_action[b["action"]] = by_action.get(b["action"], 0) + 1
        if b["action"] == "REFUSED":
            refusals[b["reason"]] = refusals.get(b["reason"], 0) + 1
    decided = sum(v for k, v in by_action.items() if k != "REFUSED")
    settled = len(data["equity"]["r1"])
    return {"expected_decisions": expected, "decided_on_time": decided, "refused": refusals, "missing": max(0, expected - len(seen)),
            "decision_coverage": (decided / expected) if expected else None, "actions": by_action,
            "expected_settlements": slots, "settled": settled, "settlement_coverage": (settled / slots) if slots else None,
            "rejected_observations": data["rejections"], "admissions": data["admissions"]}


def arm_table(data: dict, initial: float) -> dict:
    out = {}
    for arm in data["arms"]:
        pts = data["equity"][arm]
        mine = [o for o in data["outcomes"] if o["arm"] == arm]
        filled = [o for o in mine if o["status"] == "filled"]
        latent = [o for o in mine if o["status"] == "latent"]
        eq = np.array([initial] + [v for _, v in pts])
        book = sum(1 for o in filled + latent if o["entry"]["evidence"] == "book")
        out[arm] = {
            "net_pnl_eur": float(eq[-1] - initial), "final_eur": float(eq[-1]),
            "max_drawdown": float(np.max(1 - eq / np.maximum.accumulate(eq))) if len(eq) > 1 else 0.0,
            "closed_trades": len(filled), "open_at_end": len(latent), "wins": sum(1 for o in filled if o["net_eur"] > 0),
            "false_signals": sum(1 for o in filled if o["net_eur"] <= 0),
            "abandoned": sum(1 for o in mine if o["status"] == "abandoned"),
            "refused_by_portfolio": sum(1 for o in mine if o["status"] == "refused_by_portfolio"),
            "fees_eur": float(sum(o["entry"]["fee_eur"] + (o["exit"]["fee_eur"] if o["exit"] else 0.0) for o in filled + latent)),
            "net_eur_closed": float(sum(o["net_eur"] for o in filled)), "latent_eur": float(sum(o["net_eur"] for o in latent)),
            "mean_net_r": float(np.mean([o["net_r"] for o in filled])) if filled else None,
            "entries": len(filled) + len(latent), "book_entries": book,
            "book_entry_share": (book / (len(filled) + len(latent))) if (filled or latent) else None,
            "partial_fills": sum(1 for o in filled + latent if o["entry"]["partial"]),
        }
    return out


def build_report(lines: list[dict], *, manifest_sha256: str, model_sha256: str, plan: dict, now_ms: int,
                 anchors: list[dict] | None = None, explored: bool = False) -> dict:
    """Rapport du contrat. ``explored`` : la période a déjà été étudiée (verdict plafonné à EXPLORATORY)."""
    plan = {**DEFAULT_PLAN, **plan}
    reasons: list[str] = []
    integrity: dict = {"chain": "ok", "anchors": [], "anchored": False}
    try:
        data = read_ledger(lines)
    except (LedgerError, KeyError, TypeError) as exc:
        return make("report", protocol_id="inconnu", manifest_sha256=manifest_sha256, model_sha256=model_sha256, mode="synthetic",
                    generated_at_ms=now_ms, verdict="FAILED_INTEGRITY", reasons=[f"registre invalide : {exc}"[:390]],
                    r1_status="not_evaluated", window={"start_ms": now_ms, "end_ms": now_ms + 1, "complete": False, "days_observed": 0},
                    integrity={"chain": "broken"}, coverage={}, primary={}, arms={}, secondary={}, claim=CLAIM)
    cfg, body = data["config"], data["session"]["body"]
    initial = cfg["rules"]["capital"]
    if body["manifest_sha256"] != manifest_sha256 or body["model_sha256"] != model_sha256:
        reasons.append("la session n'utilise pas le manifeste ou le modèle préenregistrés")
    problems = verify_anchors(lines, anchors or [])
    integrity["anchors"], integrity["anchor_problems"] = problems[:20], len(problems)
    reasons += problems[:5] + ([f"… et {len(problems) - 5} autres écarts entre le registre et ses ancres"] if len(problems) > 5 else [])
    prec = precedence(lines, anchors or [])
    dated = sorted((a for a in (anchors or []) if isinstance(a.get("published_at_ms"), int)), key=lambda a: a["seq"])
    integrity.update(precedence=prec, anchored=bool(dated), void_lines=data["void_lines"], recoveries=data["recoveries"],
                     session_anchored_before_start=bool(dated) and dated[0]["published_at_ms"] <= cfg["start_ms"],
                     ledger_id=data["session"]["sha"], state="chaîne vérifiée ; au-delà de la dernière ancre, rien n'est prouvé")
    cov = coverage(data, now_ms)
    arms = arm_table(data, initial)
    daily = {a: daily_returns(pts, initial) for a, pts in data["equity"].items()}
    complete = data["final"]
    window = {"start_ms": cfg["start_ms"], "end_ms": cfg["end_ms"], "complete": complete, "days_observed": len(daily["r1"])}
    test = primary_test(daily["r1"], daily[plan["primary_reference"]], plan)
    descriptive = {a: {"vs_r1_paired": interval(np.array([daily["r1"][d] - daily[a][d] for d in sorted(set(daily["r1"]) & set(daily[a]))]), plan)}
                   for a in data["arms"] if a not in ("r1", plan["primary_reference"])}
    primary = {"hypothesis": "R1 améliore le résultat net du portefeuille fictif : gain absolu > 0 ET supérieur à la référence primaire",
               "reference": plan["primary_reference"], "confidence": plan["confidence"], "trials": 1, **test}
    secondary = {"descriptive_only": descriptive, "note": "bras descriptifs : jamais utilisés pour conclure"}
    mode = cfg["mode"]
    r1_status = "not_evaluated"
    if reasons:
        verdict = "FAILED_INTEGRITY"
    elif mode != "prospective" or explored:
        verdict = "EXPLORATORY"
        reasons.append("période déjà étudiée ou données simulées : aucune valeur de preuve")
    elif not data["equity"]["r1"]:
        verdict = "NOT_STARTED"
    elif data["aborted_at"] is not None:
        verdict, r1_status = "REJECTED", "rejected"
        reasons.append("critère d'arrêt atteint : baisse maximale du candidat au-delà de la limite de risque")
    elif not complete:
        verdict = "IN_PROGRESS"
        primary = {"masked": True, "reference": plan["primary_reference"],
                   "note": "résultats masqués jusqu'à la fin de la fenêtre préenregistrée"}
        secondary = {"masked": True}
        arms = {a: {k: v for k, v in row.items() if k in ("max_drawdown", "entries", "book_entry_share", "open_at_end")} for a, row in arms.items()}
    else:
        gaps = []
        if (cov["decision_coverage"] or 0) < plan["min_decision_coverage"]:
            gaps.append(f"couverture des décisions {cov['decision_coverage']:.1%} sous le minimum {plan['min_decision_coverage']:.0%}")
        if data["rejections"].get("CONFLICTING_REVISION"):
            gaps.append("bougies révisées après coup : vérification indépendante de l'historique requise")
        for a in ("r1", plan["primary_reference"]):
            share = arms[a]["book_entry_share"]
            if arms[a]["entries"] and (share or 0) < plan["min_book_entry_share"]:
                gaps.append(f"entrées de {a} simulées sans carnet horodaté ({(share or 0):.0%}) : approximation non confirmatoire")
        if arms["r1"]["entries"] < plan["min_r1_entries"]:
            gaps.append(f"activité de R1 insuffisante ({arms['r1']['entries']} entrées, minimum {plan['min_r1_entries']})")
        if not integrity["session_anchored_before_start"]:
            gaps.append("session non ancrée avant le début de la fenêtre")
        if prec["decisions_with_outcome"] and (prec["share"] or 0) < plan["min_precedence_share"]:
            gaps.append(f"antériorité prouvée pour {(prec['share'] or 0):.0%} des décisions seulement")
        if test["outcome"] == "undetermined":
            gaps.append("trop peu de jours pour un intervalle")
        if gaps:
            verdict, reasons = "INSUFFICIENT_EVIDENCE", reasons + gaps
        elif test["outcome"] == "regression":
            verdict, r1_status = "REJECTED", "rejected"
            reasons.append("régression démontrée (borne haute négative)")
        elif test["outcome"] == "supported":
            verdict, r1_status = "PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY", "retained_as_candidate_for_a_new_paper_protocol"
            reasons.append("gain absolu et relatif positifs ; décision humaine et nouveau protocole requis avant tout changement")
        else:
            verdict, r1_status = "INCONCLUSIVE", "rejected"
            reasons.append("amélioration non démontrée : R1 n'est pas retenu (règle fixée avant l'expérience)")
    return make("report", protocol_id=cfg["protocol_id"], manifest_sha256=manifest_sha256, model_sha256=model_sha256, mode=mode,
                generated_at_ms=now_ms, verdict=verdict, reasons=reasons, r1_status=r1_status, window=window, integrity=integrity,
                coverage=cov, primary=primary, arms=arms, secondary=secondary, claim=CLAIM)


# ---------------------------------------------------------------------------
# Puissance : que peut conclure la fenêtre, compte tenu de l'activité attendue ?
# ---------------------------------------------------------------------------


def _resample(n_source: int, length: int, block: int, rng: np.random.Generator) -> np.ndarray:
    starts = rng.integers(n_source, size=(length + block - 1) // block)
    return ((starts[:, None] + np.arange(block)) % n_source).ravel()[:length]


def power_table(candidate: dict[int, float], reference: dict[int, float], *, horizons: tuple[int, ...],
                yearly_effects: tuple[float, ...], plan: dict, sims: int = 300, seed: int = 4141, inner_reps: int = 400) -> dict:
    """Probabilité de conclure « amélioration » si l'avantage vrai de R1 valait ``effet`` par an.

    Les deux séries du contrôle de développement sont centrées (moyenne nulle), rééchantillonnées
    par blocs de 7 jours sur la durée voulue, puis l'effet est ajouté à R1. La ligne « 0 » mesure
    le taux de faux positifs de la règle. Les séries sources sont exploratoires : la puissance est
    une estimation d'ordre de grandeur, pas une garantie.
    """
    plan = {**DEFAULT_PLAN, **plan, "bootstrap_reps": inner_reps}
    days = sorted(set(candidate) & set(reference))
    x = np.array([candidate[d] for d in days])
    y = np.array([reference[d] for d in days])
    x0, y0 = x - x.mean(), y - y.mean()
    rng = np.random.default_rng(seed)
    table = {}
    for h in horizons:
        row = {}
        draws = [_resample(len(days), h, plan["block_days"], rng) for _ in range(sims)]
        for eff in yearly_effects:
            delta = eff / 365.0
            hits = 0
            for idx in draws:
                test = primary_test(dict(enumerate(x0[idx] + delta)), dict(enumerate(y0[idx])), plan)
                hits += test["outcome"] == "supported"
            row[f"{eff:.2f}"] = hits / sims
        table[str(h)] = row
    return {"source_days": len(days), "source_daily_std_candidate": float(x.std(ddof=1)), "source_daily_std_difference": float((x - y).std(ddof=1)),
            "sims": sims, "inner_reps": inner_reps, "seed": seed, "yearly_effects": list(yearly_effects), "horizons_days": list(horizons),
            "power": table, "method": "bootstrap circulaire par blocs de 7 jours des séries centrées du contrôle de développement"}


def activity_floor(entries_by_day: dict[int, int], days: list[int], horizon: int, *, block: int = 7, sims: int = 2000, seed: int = 4242) -> dict:
    """Nombre minimal d'entrées de R1 attendu sur ``horizon`` jours (5e centile de l'activité du développement)."""
    counts = np.array([entries_by_day.get(d, 0) for d in days], dtype=float)
    rng = np.random.default_rng(seed)
    totals = np.array([counts[_resample(len(days), horizon, block, rng)].sum() for _ in range(sims)])
    return {"entries_per_day": float(counts.mean()), "expected_entries": float(counts.mean() * horizon),
            "p05": int(np.floor(np.quantile(totals, 0.05))), "p50": int(np.median(totals)), "source_days": len(days)}


def report_sha(report: dict) -> str:
    return sha256(report)


def verify_against_history(lines: list[dict], reference_bars: dict[str, dict[int, str]]) -> dict:
    """Compare les bougies du registre à un historique public obtenu INDÉPENDAMMENT.

    ``reference_bars`` : actif -> {ouverture_ms -> empreinte de marché} recalculée depuis une autre
    source (``engine.market_sha``). Un registre réécrit avec de faux prix reste cohérent avec
    lui-même ; il ne l'est plus avec l'historique public. C'est le contrôle qui rend une
    réécriture complète détectable sans faire confiance à celui qui tient le fichier.
    """
    checked, mismatched, unknown = 0, [], []
    for ln in verify_chain(lines)["valid"]:
        if ln["kind"] != "admission" or ln["body"]["status"] == "PENDING":
            continue
        bar = ln["body"]["bar"]
        ref = reference_bars.get(bar["symbol"], {}).get(bar["open_at_ms"])
        if ref is None:
            unknown.append([bar["symbol"], bar["open_at_ms"]])
        else:
            checked += 1
            if ref != ln["body"]["bar_sha256"]:
                mismatched.append([bar["symbol"], bar["open_at_ms"]])
    return {"checked": checked, "mismatched": mismatched, "unknown": unknown, "consistent": not mismatched}
