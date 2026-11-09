"""Preuves de bon fonctionnement de l'apprentissage.

1. Sur un marché purement aléatoire (aucun avantage possible), l'IA ne doit
   PAS prétendre avoir trouvé une stratégie gagnante.
2. Sur un marché où un vrai motif existe, l'IA doit le découvrir et en tirer
   un résultat positif sur des données qu'elle n'a jamais vues.
"""

import numpy as np

from sentinelle.ai.trainer import build_dataset, train_walk_forward

from .helpers import HOUR, make_series

SYMBOLS = [("BTC/USDC", 30_000), ("ETH/USDC", 2_000), ("SOL/USDC", 50)]


def _drift(mu, split=12):
    def fn(ts):
        hours = (ts // HOUR) % 24
        return np.where(hours < split, mu, -mu * split / (24 - split))

    return fn


def test_random_market_is_not_validated():
    hist = {s: make_series(17_000, seed=1 + i, start=p) for i, (s, p) in enumerate(SYMBOLS)}
    cand, _ = build_dataset(hist, "1h")
    res = train_walk_forward(cand, "1h")
    assert res.verdict == "non validé"
    assert not res.ok
    hold = res.report["holdout"]
    # sans avantage réel, prendre les signaux coûte de l'argent (frais)
    assert hold["baseline"]["mean_r"] < 0
    assert hold["model"]["n"] == 0 or hold["model"]["mean_r"] < 0.1


def test_planted_pattern_is_learned_out_of_sample():
    hist = {s: make_series(17_000, seed=10 + i, start=p, drift_fn=_drift(0.003)) for i, (s, p) in enumerate(SYMBOLS)}
    cand, _ = build_dataset(hist, "1h")
    res = train_walk_forward(cand, "1h")
    hold = res.report["holdout"]
    assert res.ok, res.reasons
    assert hold["model"]["n"] >= 30
    assert hold["model"]["mean_r"] > 0.1
    assert hold["model"]["mean_r"] > hold["baseline"]["mean_r"] + 0.3
    assert hold["auc"] > 0.65
    # l'IA a bien identifié la vraie cause (l'heure de la journée)
    top = [x["feature"] for x in res.report["importance"][:3]]
    assert "hour_cos" in top or "hour_sin" in top
    # le modèle final est utilisable
    p, er = res.final_model.predict(cand.tail(50))
    assert ((p >= 0) & (p <= 1)).all() and np.isfinite(er).all()
