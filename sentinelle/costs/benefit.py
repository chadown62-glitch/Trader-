"""Bénéfice net attendu après coûts, avec son incertitude.

Ce module ne lit AUCUN résultat de stratégie : l'avantage brut est une hypothèse fournie par
l'appelant (par exemple +10 pb par aller-retour), jamais un chiffre tiré d'ALPHA FACTORY v2. Il
répond à : « si une stratégie avait cet avantage brut, que resterait-il après les coûts mesurés ? ».

Rappel inscrit dans les résultats : sans avantage brut (0), le résultat net est le coût changé de
signe, quelle que soit la méthode d'exécution. Réduire les coûts ne crée pas d'avantage.
"""
from __future__ import annotations

import numpy as np


def net_edge(gross_mean_bps: float, gross_sd_bps: float, cost_samples_bps, *, trades: int, gross_mean_se_bps: float = 0.0,
             sims: int = 4000, seed: int = 5501) -> dict:
    """Moyenne nette par aller-retour sur ``trades`` opérations : distribution simulée.

    - rendement brut de chaque opération ~ Normale(moyenne vraie, ``gross_sd_bps``) ;
    - moyenne vraie ~ Normale(``gross_mean_bps``, ``gross_mean_se_bps``) : l'avantage lui-même est incertain ;
    - coût de chaque opération tiré dans les coûts MESURÉS (aller-retour, points de base).
    """
    costs = np.asarray([c for c in cost_samples_bps if c is not None and np.isfinite(c)], float)
    if len(costs) == 0 or trades < 1:
        raise ValueError("Aucun coût mesuré ou aucune opération")
    rng = np.random.default_rng(seed)
    true_mean = rng.normal(gross_mean_bps, gross_mean_se_bps, sims) if gross_mean_se_bps > 0 else np.full(sims, gross_mean_bps)
    gross = rng.normal(true_mean[:, None], gross_sd_bps, (sims, trades)) if gross_sd_bps > 0 else np.repeat(true_mean[:, None], trades, axis=1)
    cost = costs[rng.integers(len(costs), size=(sims, trades))]
    net = (gross - cost).mean(axis=1)
    return {"gross_mean_bps": gross_mean_bps, "trades": trades, "mean_cost_bps": float(costs.mean()),
            "net_mean_bps": float(gross_mean_bps - costs.mean()),  # espérance exacte ; la simulation ne sert qu'à la dispersion
            "net_ci_low": float(np.quantile(net, 0.025)), "net_ci_high": float(np.quantile(net, 0.975)),
            "p_net_positive": float((net > 0).mean()), "break_even_gross_bps": float(costs.mean()), "cost_samples": int(len(costs))}


def annual_cost_eur(round_trip_bps: float, trades_per_year: float, position_eur: float) -> float:
    """Coûts annuels, en euros, d'un nombre d'allers-retours d'une taille donnée."""
    return round_trip_bps / 1e4 * position_eur * trades_per_year


def grid(cost_samples_bps, *, gross_edges_bps=(0.0, 10.0, 25.0, 50.0, 100.0), trades_per_year=(12, 52, 250), position_eur: float = 10.0,
         gross_sd_bps: float = 500.0) -> list[dict]:
    """Tableau de lecture : avantage brut hypothétique × fréquence d'opérations, sur une année."""
    rows = []
    mean_cost = float(np.mean([c for c in cost_samples_bps if c is not None and np.isfinite(c)]))
    for g in gross_edges_bps:
        for n in trades_per_year:
            r = net_edge(g, gross_sd_bps, cost_samples_bps, trades=int(n))
            rows.append({**r, "trades_per_year": n, "position_eur": position_eur,
                         "annual_cost_eur": annual_cost_eur(mean_cost, n, position_eur),
                         "annual_net_eur": r["net_mean_bps"] / 1e4 * position_eur * n})
    return rows
