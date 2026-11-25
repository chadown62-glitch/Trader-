"""Spécialistes par régime (C085) et retrait des caractéristiques redondantes (C086) : règles fixées a priori."""

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import apprentissage, moteur


def test_dedup_drops_the_later_of_two_redundant_features_and_never_the_setup_flags():
    rng = np.random.default_rng(9)
    a = rng.normal(size=400)
    X = pd.DataFrame({"a": a, "b": 2 * a + rng.normal(0, 0.01, 400), "c": rng.normal(size=400), "vide": np.nan,
                      "is_x": (a > 0).astype(float)})
    assert apprentissage.dedup_columns(X) == ["b"]
    assert apprentissage.dedup_columns(X[["b", "a", "c"]]) == ["a"]  # l'ordre fixé décide laquelle est gardée
    cands = pd.DataFrame({"b": [1.0, 2.0], "c": [3.0, 4.0]})
    out = apprentissage.masked(cands, ["b"])
    assert out["b"].isna().all() and out["c"].tolist() == [3.0, 4.0] and cands["b"].tolist() == [1.0, 2.0]
    assert apprentissage.masked(cands, []) is cands


def test_the_regime_of_a_decision_is_read_at_the_previous_close():
    days = pd.RangeIndex(1000, 1080)
    close = pd.Series(np.r_[100.0 + np.arange(40), 140.0 + np.where(np.arange(40) % 2 == 0, 0.0, 5.0)], index=days)
    ms = lambda d, minutes=5: d * moteur.DAY_MS + minutes * 60_000  # noqa: E731
    reg = apprentissage.trend_of_previous_day(close, np.array([ms(1010), ms(1040), ms(1079)]))
    assert np.isnan(reg[0])  # moins de 30 jours d'historique la veille : inconnu
    assert reg[1] == 1.0  # veille (jour 1039) : 30 jours de hausse rectiligne
    assert reg[2] == 0.0  # veille : aller-retour
    later = close.copy()
    later.loc[1040:] = 1.0  # le jour de la décision et la suite ne comptent pas
    assert apprentissage.trend_of_previous_day(later, np.array([ms(1040)]))[0] == 1.0


class _Stub:
    """Modèle factice : probabilité constante égale à la part de gains de son apprentissage, prêt dès 1 alerte."""
    fitted = []

    def __init__(self, extra=()):
        self.ready = False

    def fit(self, train):
        _Stub.fitted.append(len(train))
        self.rate, self.base_rate, self.ready = float((train["r"] > 0).mean()), float((train["r"] > 0).mean()), len(train) > 0
        return self

    def judge(self, cands):
        return pd.DataFrame({"p": np.full(len(cands), self.rate), "take": False}, index=cands.index)


def test_a_small_specialist_and_unknown_regimes_fall_back_to_the_single_model(monkeypatch):
    monkeypatch.setattr(apprentissage, "PrincipalAI", _Stub)
    monkeypatch.setattr(apprentissage, "dedup_columns", lambda X: [])
    monkeypatch.setattr(apprentissage, "matrix", lambda cands, extra: pd.DataFrame(index=cands.index))
    n_trend = apprentissage.MIN_TRAIN
    train = pd.DataFrame({"r": np.r_[np.ones(n_trend), -np.ones(50)]})
    reg_train = np.r_[np.ones(n_trend), np.zeros(50)]
    test = pd.DataFrame({"r": [1.0, -1.0, 1.0]})
    res = apprentissage.judge_month(train, test, (), reg_train, np.array([1.0, 0.0, np.nan]))
    ref = n_trend / (n_trend + 50)
    assert res["p_spec"] == pytest.approx([1.0, ref, ref])  # tendance : spécialiste ; range (50 alertes) et inconnu : modèle unique
    assert res["fallback"] == ["range"] and res["p_ref"] == pytest.approx([ref] * 3)


def test_paired_brier_gap_is_negative_when_a_version_is_closer_to_the_outcomes():
    rng = np.random.default_rng(10)
    n = 1200
    y = rng.integers(0, 2, n)
    frame = pd.DataFrame({"month": np.repeat([f"2025-{k:02d}" for k in range(1, 13)], n // 12), "y": y,
                          "p_ref": np.full(n, 0.5), "p_good": np.where(y == 1, 0.7, 0.3), "p_same": np.full(n, 0.5)})
    good = apprentissage.paired(frame, "p_good")
    assert good["ecart_brier"] == pytest.approx(0.09 - 0.25) and good["p_unilaterale"] < 0.01 and good["ic95"][1] < 0
    same = apprentissage.paired(frame, "p_same")
    assert same["ecart_brier"] == 0.0 and same["p_unilaterale"] == 1.0
