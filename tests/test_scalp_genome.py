"""OMEGA A — GENOME : profils par actif, fiabilité, stabilité entre moitiés, erreur par famille."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import genome, moteur, recherche


def test_families_are_fixed_a_priori_and_cover_each_candidate_once():
    assert sorted(genome.FAMILY) == sorted(recherche.CANDIDATES)
    assert sum(len(v.split()) for v in genome.FAMILIES.values()) == len(recherche.CANDIDATES) == 42
    assert genome.FAMILY["PEPEUSDT"] == "memecoins" and genome.FAMILY["BTCUSDT"] == "paiement"


def test_profile_measures_volatility_beta_momentum_and_drift():
    rng = np.random.default_rng(60)
    btc = pd.Series(rng.normal(0, 0.05, 400))
    r = 2 * btc + rng.normal(0, 0.01, 400) + 0.003
    r28 = pd.Series(rng.normal(0, 1, 400))
    nxt = 0.5 * r28 + rng.normal(0, 0.1, 400)
    p = genome.profile(r, r28, nxt, btc)
    assert p["beta"] == pytest.approx(2, abs=0.05) and p["momentum"] > 0.9
    assert p["volatilite"] == pytest.approx(r.std(), rel=1e-9) and p["derive"] == pytest.approx(r.mean())
    assert np.isnan(genome.profile(r, r28, nxt, None)["beta"])  # le BTC n'a pas de bêta envers lui-même


def _prices(rng, vols, weeks=300, drift=0.0):
    d0 = moteur._day("2018-01-07")
    idx = pd.RangeIndex(d0, d0 + 7 * weeks)
    btc = rng.normal(0, 0.05, weeks)
    cols = {"BTCUSDT": btc}
    for k, v in enumerate(vols):
        cols[f"A{k}USDT"] = 0.8 * btc + rng.normal(drift, v, weeks)
    return pd.DataFrame({s: np.repeat(100 * np.exp(np.cumsum(r)), 7) for s, r in cols.items()}, index=idx)


def test_stability_and_reliability_separate_a_persistent_profile_from_noise():
    rng = np.random.default_rng(61)
    c = _prices(rng, np.linspace(0.02, 0.15, 12))  # volatilité propre et persistante ; dérive et momentum nuls
    out = genome.study(c, boot=60, perms=500)
    assert out["effectif"]["actifs_scindes_en_deux_moities"] == 13
    assert out["stabilite"]["volatilite"]["stable_holm_5pct"] and out["stabilite"]["volatilite"]["rang_spearman"] > 0.8
    assert not out["stabilite"]["momentum"]["stable_holm_5pct"]  # aucun profil de momentum n'a été construit
    assert out["fiabilite"]["volatilite"]["fiabilite"] > 0.9 > out["fiabilite"]["momentum"]["fiabilite"]
    assert out["erreur_par_famille"]["volatilite"]["meilleure"] == "propre"


def test_family_error_prefers_the_family_mean_when_own_estimates_are_noise():
    idx = [f"{c}USDT" for c in ("ETH", "ADA", "SOL", "DOT", "DOGE", "SHIB", "PEPE")]
    rng = np.random.default_rng(62)
    truth = pd.Series({a: (0.2 if genome.FAMILY[a] == "memecoins" else 0.05) for a in idx})
    first = pd.DataFrame({k: truth + rng.normal(0, 0.05, len(idx)) for k in genome.PROFILES})
    second = pd.DataFrame({k: truth + rng.normal(0, 0.05, len(idx)) for k in genome.PROFILES})
    out = genome.family_error(first, second)
    for k in genome.PROFILES:
        assert out[k]["eqm_famille"] < out[k]["eqm_generale"]  # la famille porte l'information, l'estimation propre est bruitée
