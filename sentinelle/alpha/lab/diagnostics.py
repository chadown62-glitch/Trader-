"""ALPHA FACTORY v2 — analyses DESCRIPTIVES de M55 sur la fenêtre déjà consultée (2020-01-01 → 2026-11-12).

Rien ici ne confirme ni ne modifie quoi que ce soit : le candidat reste figé, la règle de décision est celle du plan,
et ces chiffres ne pourront jamais servir de « seconde confirmation ». Ils décrivent OÙ et COMMENT l'avantage
historique de ``mom_ts_28`` s'est formé (régimes, qualité des entrées et sorties, taille compatible avec le risque,
coûts, délai, liquidité), et pourquoi les autres candidats ont échoué sur un compte de 30 €.
"""
from __future__ import annotations

import numpy as np

from . import CAPITAL
from .costs import CostHypotheses
from .data import Panel
from .experiment import benchmark_returns
from .simulate import Features, is_decision_day, simulate
from .strategies import BY_NAME, Context

CANDIDATE = "mom_ts_28"
MAJORS = ("BTC", "ETH")
HUB_MIN_ORDER = 10.0  # minimum d'ordre par défaut du hub (mission 52)
SCOPE = ("DESCRIPTIF, sur la fenêtre déjà consultée par l'évaluation unique : n'est ni une confirmation ni une "
         "nouvelle évaluation ; le candidat et le protocole prospectif restent figés")


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.nan_to_num(x))
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n - 1:] - np.concatenate([[0.0], c[:-n]])) / n
    return out


def regimes(panel: Panel, ret: np.ndarray, expo: np.ndarray, bench: np.ndarray, start: int, end: int) -> dict:
    """Jour t classé avec l'information de la veille : BTC au-dessus de sa moyenne 200 jours ; volatilité 30 jours en tiers."""
    j = panel.col("BTC")
    close = panel.close[:, j]
    sma = _sma(close, 200)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.concatenate([[np.nan], close[1:] / close[:-1] - 1])
    vol = np.array([np.nanstd(r[max(0, t - 29):t + 1], ddof=1) if t >= 30 else np.nan for t in range(len(close))])
    idx = np.arange(start, end + 1) - 1  # information de la veille
    trend = np.where(close[idx] > sma[idx], "haussier", "baissier")
    v = vol[idx]
    lo, hi = np.nanquantile(v, [1 / 3, 2 / 3])
    level = np.where(v <= lo, "calme", np.where(v <= hi, "moyenne", "agitée"))
    rel = ret - expo.mean() * bench

    def row(mask):
        n = int(mask.sum())
        return {"days": n, "candidate_mean_daily": float(ret[mask].mean()), "majors_mean_daily": float(bench[mask].mean()),
                "relative_mean_daily": float(rel[mask].mean()), "exposure": float(expo[mask].mean()),
                "candidate_compound": float(np.prod(1 + ret[mask]) - 1), "majors_compound": float(np.prod(1 + bench[mask]) - 1)}

    out = {"trend": {k: row(trend == k) for k in ("haussier", "baissier")},
           "volatility": {k: row(level == k) for k in ("calme", "moyenne", "agitée")},
           "volatility_thresholds_daily": [float(lo), float(hi)],
           "rule": "tendance : clôture BTC de la veille au-dessus de sa moyenne 200 jours ; volatilité : écart type des rendements "
                   "quotidiens BTC sur 30 jours (veille), coupée en tiers sur la fenêtre (seuils descriptifs, pas une règle)"}
    return out


def episodes(panel: Panel, start: int, end: int, cost) -> dict:
    """Épisodes de détention par actif, rejoués depuis les décisions hebdomadaires (mêmes jours que le simulateur)."""
    spec = BY_NAME[CANDIDATE]
    feats = Features(panel)
    weights = []
    for t in range(start - 1, end):
        if t == start - 1 or is_decision_day(spec, panel, t):
            ctx = Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t], {}, frozenset(), MAJORS)
            weights.append((t, spec.fn(ctx, spec.params)))
    rows = []
    for base in MAJORS:
        j = panel.col(base)
        o, c = panel.open[:, j], panel.close[:, j]
        held, entry = False, None
        for t, w in weights:
            on = w.get(base, 0.0) > 0
            if on and not held:
                held, entry = True, t + 1
            elif not on and held:
                held = False
                rows.append(_episode(base, o, c, entry, t + 1, cost, closed=True))
        if held:
            rows.append(_episode(base, o, c, entry, end, cost, closed=False))
    g = np.array([r["gross"] for r in rows])
    net = np.array([r["net_approx"] for r in rows])
    wins, losses = net[net > 0], net[net <= 0]
    after7 = np.array([r["after_exit_7d"] for r in rows if r["after_exit_7d"] is not None])
    after28 = np.array([r["after_exit_28d"] for r in rows if r["after_exit_28d"] is not None])
    return {
        "count": len(rows), "by_asset": {b: sum(1 for r in rows if r["asset"] == b) for b in MAJORS},
        "win_rate": float((net > 0).mean()), "mean_net": float(net.mean()), "median_net": float(np.median(net)),
        "mean_gross": float(g.mean()), "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None,
        "profit_factor_episodes": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
        "mean_days": float(np.mean([r["days"] for r in rows])), "median_days": float(np.median([r["days"] for r in rows])),
        "mean_adverse_excursion": float(np.mean([r["mae"] for r in rows])), "mean_favourable_excursion": float(np.mean([r["mfe"] for r in rows])),
        "share_of_mfe_kept": float(g.sum() / sum(r["mfe"] for r in rows)) if sum(r["mfe"] for r in rows) > 0 else None,
        "after_exit_7d_mean": float(after7.mean()) if len(after7) else None,
        "after_exit_28d_mean": float(after28.mean()) if len(after28) else None,
        "after_exit_28d_share_lower": float((after28 < 0).mean()) if len(after28) else None,
        "best": max(rows, key=lambda r: r["net_approx"]), "worst": min(rows, key=lambda r: r["net_approx"]),
        "top5_share_of_gains": float(np.sort(wins)[::-1][:5].sum() / wins.sum()) if len(wins) else None,
        "rule": "entrée à l'ouverture qui suit la décision, sortie à l'ouverture qui suit la décision de sortie ; net approché = brut "
                "− un aller-retour du scénario principal (hors reliquats) ; « après la sortie » = variation du prix après être sorti "
                "(négative : la sortie a évité une baisse)",
    }


def _episode(base, o, c, e, x, cost, *, closed: bool) -> dict:
    px_in, px_out = float(o[e]), float(o[x] if closed else c[x])
    path = c[e:x] / px_in - 1 if x > e else np.array([px_out / px_in - 1])
    gross = px_out / px_in - 1
    rt = 2 * (cost.worse(base) + cost.fee)
    after = lambda n: float(c[x + n - 1] / o[x] - 1) if closed and x + n - 1 < len(c) and np.isfinite(c[x + n - 1]) else None  # noqa: E731
    return {"asset": base, "entry_day": int(e), "exit_day": int(x), "days": int(x - e), "gross": float(gross), "net_approx": float(gross - rt),
            "mae": float(min(0.0, np.nanmin(path))), "mfe": float(max(0.0, np.nanmax(path))), "after_exit_7d": after(7),
            "after_exit_28d": after(28), "closed": closed}


def sizing(ret: np.ndarray, dd_full: float) -> dict:
    """Taille réduite pour tenir une limite de baisse ; faisabilité avec le minimum d'ordre du hub sur 30 €."""
    rows = {}
    for limit in (0.10, 0.20, 0.30):
        s = min(1.0, limit / dd_full)
        eq = np.cumprod(1 + s * ret)
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
        dd = float(np.max(1 - np.concatenate([[1.0], eq]) / peak))
        years = len(ret) / 365
        order = s * CAPITAL / len(MAJORS)
        rows[f"{int(limit * 100)}%"] = {"scale": s, "cagr_approx": float(eq[-1] ** (1 / years) - 1), "max_drawdown_approx": dd,
                                        "order_per_asset_on_30": order, "feasible_with_hub_minimum": bool(order >= HUB_MIN_ORDER),
                                        "capital_needed_for_hub_minimum": float(HUB_MIN_ORDER * len(MAJORS) / s)}
    return {"rows": rows, "rule": "rendements quotidiens nets multipliés par la taille (approximation : coûts proportionnels, reliquats "
                                  "et minimums non recalculés) ; ordre par actif quand les deux sont détenus"}


def run(panel: Panel, rules: dict, hyp: CostHypotheses, results: dict) -> dict:
    w = results["window"]
    start, end = panel.index_of(w["start"]), panel.index_of(w["end"])
    spec = BY_NAME[CANDIDATE]
    cost = hyp.scenario(hyp.primary)
    feats = Features(panel)
    res = simulate(panel, spec, cost, start=start, end=end, capital=CAPITAL, features=feats, rules=rules)
    published = results["candidates"][CANDIDATE]["net"]["net_pnl"]
    if abs(res.net_pnl - published) > 1e-6:
        raise ValueError("Le rejeu ne reproduit pas le résultat publié : diagnostic refusé")
    bench = benchmark_returns(panel, feats, start, end, majors=MAJORS)["majors"]
    cand = results["candidates"][CANDIDATE]
    sens = results["sensitivity"]
    vol = {b: float(np.nanmedian(panel.qvol[start:end + 1, panel.col(b)])) for b in MAJORS}
    failures = {}
    for n, c in results["candidates"].items():
        if n == CANDIDATE:
            continue
        failures[n] = {"net": c["net"]["net_pnl"], "gross": c["costs"]["gross_pnl"], "fees_and_spread": c["costs"]["fees"] + c["costs"]["spread_slippage"],
                       "dust_lost": c["net"].get("dust_lost", 0.0), "trades": c["net"]["trades"],
                       "trades_at_minimum_10": sens["variants"]["minimum_10"][n]["trades"], "reasons": c["reasons"]}
    return {
        "kind": "alpha-lab-m55-diagnostic", "scope": SCOPE, "candidate": CANDIDATE, "window": {"start": w["start"], "end": w["end"]},
        "replay_matches_published": True, "net_pnl": res.net_pnl,
        "regimes": regimes(panel, res.returns, res.exposure, bench, start, end),
        "episodes": episodes(panel, start, end, cost),
        "sizing": sizing(res.returns, cand["net"]["max_drawdown"]),
        "costs_latency_liquidity": {
            "sensitivity": {v: sens["variants"][v][CANDIDATE]["net_pnl"] for v in sens["variants"]},
            "break_even_fee": sens["break_even_fee"][CANDIDATE],
            "median_daily_quote_volume": vol, "typical_order": CAPITAL / len(MAJORS),
            "order_share_of_daily_volume": {b: (CAPITAL / len(MAJORS)) / v for b, v in vol.items()},
            "note": "impact de marché négligeable pour des ordres de cette taille ; le délai intrajournalier n'est pas mesurable avec des "
                    "bougies journalières (seul le retard d'un jour l'est)",
        },
        "failures": failures,
        "failure_summary": {
            "dust_exceeds_fees": sorted(n for n, f in failures.items() if f["dust_lost"] > f["fees_and_spread"]),
            "cannot_trade_at_minimum_10": sorted(n for n, f in failures.items() if f["trades_at_minimum_10"] == 0),
            "no_gross_edge": sorted(n for n, f in failures.items() if f["gross"] <= 0),
        },
    }


def _p(x, d=1):
    return "—" if x is None else f"{x * 100:+.{d}f} %".replace(".", ",")


def _n(x, d=2):
    return "—" if x is None else f"{x:.{d}f}".replace(".", ",")


def markdown(r: dict) -> str:
    out = [f"> {r['scope']}.", "", "#### Régimes de marché (information de la veille)", "",
           "| Régime | Jours | mom_ts_28 (cumul) | BTC+ETH (cumul) | Écart quotidien moyen | Exposition |", "|---|---|---|---|---|---|"]
    for group in ("trend", "volatility"):
        for k, v in r["regimes"][group].items():
            out.append(f"| {k} | {v['days']} | {_p(v['candidate_compound'], 0)} | {_p(v['majors_compound'], 0)} | "
                       f"{_p(v['relative_mean_daily'], 3)} | {_p(v['exposure'], 0)} |")
    e = r["episodes"]
    out += ["", "#### Qualité des entrées et des sorties", "",
            "| Mesure | Valeur |", "|---|---|",
            f"| Épisodes (BTC / ETH) | {e['count']} ({e['by_asset']['BTC']} / {e['by_asset']['ETH']}) |",
            f"| Part gagnante | {_p(e['win_rate'], 0)} |",
            f"| Gain net moyen / médian par épisode | {_p(e['mean_net'])} / {_p(e['median_net'])} |",
            f"| Rapport gain moyen / perte moyenne | {_n(e['payoff_ratio'])} |",
            f"| Facteur de profit (épisodes) | {_n(e['profit_factor_episodes'])} |",
            f"| Durée moyenne / médiane | {_n(e['mean_days'], 0)} / {_n(e['median_days'], 0)} jours |",
            f"| Pire baisse moyenne pendant un épisode | {_p(e['mean_adverse_excursion'])} |",
            f"| Meilleure hausse moyenne pendant un épisode | {_p(e['mean_favourable_excursion'])} |",
            f"| Gains bruts conservés / meilleures hausses atteintes (somme des épisodes) | {_p(e['share_of_mfe_kept'], 0)} |",
            f"| Variation moyenne 7 / 28 jours après la sortie | {_p(e['after_exit_7d_mean'])} / {_p(e['after_exit_28d_mean'])} |",
            f"| Sorties suivies d'une baisse à 28 jours | {_p(e['after_exit_28d_share_lower'], 0)} |",
            f"| Part des 5 meilleurs épisodes dans les gains | {_p(e['top5_share_of_gains'], 0)} |",
            "", "#### Taille compatible avec la limite de risque", "",
            "| Limite de baisse | Taille | Par an (approché) | Baisse max. (approchée) | Ordre par actif sur 30 € | Possible avec le minimum de 10 € du hub | Capital nécessaire |",
            "|---|---|---|---|---|---|---|"]
    for k, v in r["sizing"]["rows"].items():
        out.append(f"| {k} | {_p(v['scale'], 0)} | {_p(v['cagr_approx'], 0)} | {_p(v['max_drawdown_approx'], 0)} | {_n(v['order_per_asset_on_30'])} € | "
                   f"{'oui' if v['feasible_with_hub_minimum'] else 'non'} | {_n(v['capital_needed_for_hub_minimum'], 0)} € |")
    c = r["costs_latency_liquidity"]
    out += ["", "#### Coûts, délai, liquidité (résultat net sur 30 unités)", "", "| Variante | Résultat net |", "|---|---|"]
    out += [f"| {k} | {_n(v)} |" for k, v in c["sensitivity"].items()]
    be = c["break_even_fee"]
    out += ["", f"Commission d'équilibre : {be['note'] if be['bps'] is None else _n(be['bps'], 1) + ' pb'}. "
            f"Volume quotidien médian : BTC {_n(c['median_daily_quote_volume']['BTC'] / 1e9, 1)} milliard(s), ETH "
            f"{_n(c['median_daily_quote_volume']['ETH'] / 1e9, 1)} milliard(s) d'USDT ; un ordre de {c['typical_order']:.0f} en représente environ "
            f"{c['order_share_of_daily_volume']['BTC']:.0e}. {c['note'][0].upper() + c['note'][1:]}.",
            "", "#### Pourquoi les 14 autres échouent sur 30 €", "",
            "| Candidat | Net | Brut | Frais et écart | Reliquats perdus | Ordres | Ordres avec minimum 10 |", "|---|---|---|---|---|---|---|"]
    for n, f in r["failures"].items():
        out.append(f"| {n} | {_n(f['net'])} | {_n(f['gross'])} | {_n(-f['fees_and_spread'])} | {_n(-f['dust_lost'])} | {f['trades']} | {f['trades_at_minimum_10']} |")
    s = r["failure_summary"]
    out += ["", f"Reliquats perdus supérieurs aux frais : {', '.join(s['dust_exceeds_fees']) or 'aucun'}.",
            f"Aucun ordre possible avec un minimum de 10 € : {', '.join(s['cannot_trade_at_minimum_10']) or 'aucun'}.",
            f"Aucun avantage même sans coûts : {', '.join(s['no_gross_edge']) or 'aucun'}."]
    return "\n".join(out)
