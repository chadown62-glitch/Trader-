"""Apprentissage champion / challenger : statistiques prudentes, comparaison appariée, promotions, retour en arrière."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sentinelle.agent.agent import TradingAgent
from sentinelle.ai.bench import make_candidates
from sentinelle.ai.compare import compare_models, decisions, frozen_vs_retrained, realized
from sentinelle.ai.metrics import (MIN_BLOCKS, bonferroni_alpha, clustered_se, comparison_verdict, critical_value, paired_comparison,
                                   proven_positive, trade_metrics)
from sentinelle.ai.model import MetaModel, payoff_table
from sentinelle.ai.trainer import feature_bins, outcome_mix, train_walk_forward
from sentinelle.api.server import create_api
from sentinelle.config import DEFAULT_SETTINGS, apply_update
from sentinelle.market.sources import SyntheticSource
from sentinelle.risk.manager import RiskManager
from sentinelle.services.champion import PSI_ALERT, psi
from sentinelle.services.common import UserError
from sentinelle.services.core import App
from sentinelle.services.models import CHALLENGER_MAX_DAYS, DAY as SECONDS_PER_DAY

H = {"X-Sentinelle": "1"}
DAY = 86_400_000
WEEK = 7 * DAY


# ---------------------------------------------------------------------------
# 1. Incertitude prudente
# ---------------------------------------------------------------------------
def test_clustered_error_is_never_more_optimistic_than_the_usual_one():
    rng = np.random.default_rng(1)
    ts = np.sort(rng.uniform(0, 600 * DAY, 600))
    independent = rng.normal(0.1, 1.5, 600)
    se, blocks = clustered_se(independent, ts)
    naive = independent.std(ddof=1) / np.sqrt(600)
    assert blocks == 20 and se >= naive and se < 2 * naive
    # trades dépendants : chaque mois a son « humeur », commune à tous les trades du mois
    mood = rng.normal(0, 1.0, 21)[(ts // (30 * DAY)).astype(int)]
    dependent = 0.1 + mood + rng.normal(0, 0.5, 600)
    se_dep, _ = clustered_se(dependent, ts)
    assert se_dep > 3 * dependent.std(ddof=1) / np.sqrt(600)  # l'erreur type habituelle serait bien trop optimiste
    assert clustered_se([1.0], [0]) == (None, 1) and clustered_se([1.0, 2.0], [0, DAY])[0] is None  # un seul bloc : rien à dire


def test_proof_needs_enough_time_blocks_and_scales_with_the_number_of_tries():
    rng = np.random.default_rng(3)
    r = rng.normal(0.4, 1.0, 400)
    long = trade_metrics(r, ts=np.sort(rng.uniform(0, 400 * DAY, 400)))
    assert long["blocks"] >= MIN_BLOCKS and long["se_cluster"] is not None and proven_positive(long)
    assert long["ci_low"] < long["mean_r"] < long["ci_high"]
    # même résultat, mais concentré sur trois mois : aucune preuve possible
    short = trade_metrics(r, ts=np.sort(rng.uniform(0, 80 * DAY, 400)))
    assert short["blocks"] < MIN_BLOCKS and not proven_positive(short)
    assert not proven_positive(trade_metrics([])) and not proven_positive({"n": 0})
    # sans dates : test habituel (compatibilité)
    assert proven_positive(trade_metrics(r)) and "se_cluster" not in trade_metrics(r)
    # peu de blocs : seuil de Student, plus exigeant que 1,645 ; plusieurs essais : plus exigeant encore
    assert critical_value(0.05) == pytest.approx(1.645, abs=1e-3)
    assert critical_value(0.05, 10) == pytest.approx(1.833, abs=1e-3) and critical_value(0.05, 10) > critical_value(0.05)
    assert bonferroni_alpha(1) == 0.05 and bonferroni_alpha(5) == pytest.approx(0.01) and bonferroni_alpha(0) == 0.05
    # résultat modeste (moyenne 0,10 R, écart-type 1, 400 trades sur 14 mois) : prouvé à 95 %, plus du tout après vingt essais
    weak = trade_metrics(0.1 + np.where(np.arange(400) % 2 == 0, 1.0, -1.0), ts=np.arange(400) * DAY)
    assert weak["blocks"] == 14 and weak["mean_r"] / weak["se_cluster"] == pytest.approx(2.0, abs=0.01)
    assert proven_positive(weak, 0.05) and not proven_positive(weak, bonferroni_alpha(20))


def test_paired_comparison_verdicts():
    rng = np.random.default_rng(5)
    n = 600
    ts = np.sort(rng.uniform(0, 300 * DAY, n))
    outcome = rng.choice([2.0, -1.0], n, p=[0.4, 0.6])
    take_a = rng.random(n) < 0.5
    good = (outcome > 0) & (rng.random(n) < 0.7) | (outcome < 0) & (rng.random(n) < 0.3)  # B reconnaît mieux les gagnants
    better = paired_comparison(outcome * take_a, outcome * good, ts, block_days=7)
    assert better["n"] == n and better["blocks"] >= 40 and better["n_diff"] > 100
    assert better["diff"] > 0 and better["ci_low"] > 0 and better["p_better"] > 0.99
    assert better["diff"] == pytest.approx(better["mean_b"] - better["mean_a"]) and better["sum_b"] > better["sum_a"]
    assert comparison_verdict(better)[0] == "meilleur" and "mieux" in comparison_verdict(better)[1]
    worse = paired_comparison(outcome * good, outcome * take_a, ts, block_days=7)
    assert worse["ci_high"] < 0 and comparison_verdict(worse)[0] == "moins bon"
    coin = paired_comparison(outcome * take_a, outcome * (rng.random(n) < 0.5), ts, block_days=7)
    assert coin["ci_low"] < 0 < coin["ci_high"] and comparison_verdict(coin)[0] == "non départagés"
    same = paired_comparison(outcome * take_a, outcome * take_a, ts, block_days=7)
    assert same["n_diff"] == 0 and comparison_verdict(same)[0] == "insuffisant"
    # différence réelle mais trop peu de situations, ou trop peu de semaines : pas de conclusion
    few = paired_comparison(outcome[:40] * take_a[:40], outcome[:40] * good[:40], ts[:40], block_days=7)
    assert comparison_verdict(few)[0] == "insuffisant"
    packed = paired_comparison(outcome * take_a, outcome * good, np.sort(rng.uniform(0, 20 * DAY, n)), block_days=7)
    assert packed["blocks"] < MIN_BLOCKS and comparison_verdict(packed)[0] == "insuffisant"
    assert paired_comparison([], [], [])["diff"] is None


# ---------------------------------------------------------------------------
# 2. Entraînement : pas de fuite à la frontière, seuil choisi à part, fiche d'identité
# ---------------------------------------------------------------------------
def test_training_purges_the_boundary_and_separates_calibration_from_threshold():
    cand, _ = make_candidates(3000, "simple", seed=4)
    # des positions qui durent longtemps : certaines, décidées avant la période de contrôle, se dénouent pendant elle
    cand["exit_ts"] = cand["decision_ts"] + 40 * DAY
    res = train_walk_forward(cand, "4h", with_importance=False)
    rep = res.report
    hold_start = rep["period"]["holdout_start"]
    crossing = int(((cand["decision_ts"] < hold_start) & (cand["exit_ts"] >= hold_start)).sum())
    assert crossing > 20 and rep["purged_at_boundary"] == crossing  # écartés de l'apprentissage, de la calibration et du seuil
    assert all(f["end"] <= hold_start for f in rep["folds"])
    sel = rep["selection"]
    assert sel["independent"] is True and sel["n_calibration"] >= 100 and sel["n_threshold"] >= 100
    assert rep["folds"][0]["start"] < sel["split_ts"] < hold_start
    assert rep["alpha"] == 0.05 and rep["holdout"]["model"]["blocks"] >= MIN_BLOCKS
    assert 0 <= rep["holdout"]["brier"] <= rep["holdout"]["brier_reference"] + 0.02  # prévisions au moins aussi bonnes que le taux moyen
    # fiche d'identité du modèle : ce qu'il a vu et comment il a été fabriqué
    meta = res.final_model.meta
    assert meta["train_end"] == int(cand["exit_ts"].max()) and meta["holdout_start"] == hold_start and meta["recipe"] == "mixte"
    assert res.eval_model.meta["train_end"] == hold_start and res.eval_model.meta["n_train"] < meta["n_train"]
    assert set(res.final_model.feature_bins) >= {"rsi14", "atr_pct"} and len(res.final_model.feature_bins["rsi14"]) == 9
    mix = res.final_model.outcome_mix["trend_follow"]
    assert 0 < mix["win_rate"] < 1 and mix["mean_win"] > 0 > mix["mean_loss"] and mix["stop_share_of_losses"] == 1.0
    assert rep["meta"] == meta
    # trop peu d'exemples pour séparer calibration et seuil : c'est signalé, pas caché
    small = train_walk_forward(make_candidates(420, "simple", seed=4)[0], "4h", with_importance=False)
    assert small.report.get("selection", {"independent": False})["independent"] is False


def test_more_tries_demand_more_proof():
    cand, _ = make_candidates(2500, "faible", seed=4)
    assert train_walk_forward(cand, "4h", with_importance=False).ok
    strict = train_walk_forward(cand, "4h", with_importance=False, alpha=bonferroni_alpha(200), rules_fallback=False)
    assert not strict.ok and strict.report["alpha"] == pytest.approx(0.05 / 200)
    assert any("trop incertain" in r for r in strict.reasons)


def test_feature_bins_and_outcome_mix_helpers():
    cand, _ = make_candidates(500, "simple", seed=1)
    bins = feature_bins(cand, ["rsi14", "inconnue"])
    assert list(bins) == ["rsi14"] and bins["rsi14"] == sorted(bins["rsi14"])
    values = cand["rsi14"].to_numpy()
    assert psi(bins["rsi14"], values) == pytest.approx(0.0, abs=0.01)  # même répartition qu'à l'apprentissage
    assert psi(bins["rsi14"], values + 2.0) > PSI_ALERT  # le marché ne ressemble plus à l'apprentissage
    assert psi(bins["rsi14"], values[:10]) is None  # trop peu d'observations pour juger
    mix = outcome_mix(cand)
    assert set(mix) == {"trend_follow", "trend_breakout"} and sum(mix["trend_follow"]["shares"].values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 3. Champion contre challenger sur les mêmes situations
# ---------------------------------------------------------------------------
def quick_model(rows, version, threshold=0.0, seen_until=True):
    m = MetaModel("4h", recipe="prudent").fit(rows)
    m.payoffs = payoff_table(rows)
    m.threshold, m.version = threshold, version
    m.metrics = {"verdict": "validé", "holdout_mean_r": 0.2}
    m.meta = {"train_end": int(rows["exit_ts"].max())} if seen_until else {}
    return m


@pytest.fixture(scope="module")
def shift():
    """Marché artificiel dont la règle s'inverse aux trois cinquièmes : un vieux modèle y devient mauvais."""
    cand, _ = make_candidates(6000, "changement", seed=3, years=6.0)
    t = np.arange(len(cand)) / (len(cand) - 1)
    old = quick_model(cand[t < 0.55], "m-ancien")  # a appris l'ancienne règle
    new = quick_model(cand[(t >= 0.62) & (t < 0.8)], "m-nouveau")  # a appris la nouvelle
    return cand, t, old, new


def test_compare_models_only_counts_unseen_situations(shift):
    cand, t, old, new = shift
    late = cand[t >= 0.8]
    seen_until = int(cand.loc[t < 0.8, "exit_ts"].max())
    cmp = compare_models(late, old, new, challenger_seen_until=seen_until)
    assert cmp["champion"] == "m-ancien" and cmp["challenger"] == "m-nouveau" and cmp["champion_knew_share"] == 0.0
    assert cmp["verdict"] == "meilleur" and cmp["clean"]["ci_low"] > 0 and cmp["clean"]["n"] == len(late) - int((late["decision_ts"] <= seen_until).sum())
    assert cmp["clean"]["mean_taken_b"] > cmp["clean"]["mean_taken_a"]
    # dans l'autre sens : un modèle moins bon ne passe pas
    back = compare_models(late, new, old, challenger_seen_until=int(cand.loc[t < 0.55, "exit_ts"].max()))
    assert back["verdict"] == "moins bon" and back["clean"]["ci_high"] < 0
    # le champion a déjà vu ces situations : aucune comparaison équitable, donc aucune décision
    knows = quick_model(cand, "m-qui-sait")
    unfair = compare_models(late, knows, new, challenger_seen_until=seen_until)
    assert unfair["verdict"] == "insuffisant" and unfair["champion_knew_share"] == 1.0 and "déjà vu" in unfair["explanation"]
    assert unfair["indicative"] is not None and unfair["clean"] is None
    # champion sans fiche d'identité (ancienne version du logiciel) : on ne suppose rien
    blind = compare_models(late, quick_model(cand[t < 0.55], "m-sans-fiche", seen_until=False), new, challenger_seen_until=seen_until)
    assert blind["verdict"] == "insuffisant" and blind["champion_knew_share"] is None
    assert compare_models(late.iloc[0:0], old, new)["verdict"] == "insuffisant"
    # la règle « une position à la fois par crypto » s'applique à chacun
    p, er, take = decisions(new, late.sort_values("decision_ts").reset_index(drop=True))
    assert take.sum() <= (er >= new.threshold).sum() and realized(late.sort_values("decision_ts").reset_index(drop=True), take)[~take].sum() == 0


def test_frozen_model_against_retrained_model(shift):
    cand, t, old, new = shift
    out = frozen_vs_retrained(cand, "4h", n_steps=5, start_frac=0.5)
    assert len(out["steps"]) == 5 and out["steps"][0]["frozen"] == pytest.approx(out["steps"][0]["retrained"])  # première période : même modèle
    assert out["verdict"] == "meilleur", out["explanation"]  # la règle a changé : ré-apprendre fait mieux que rester figé
    assert out["comparison"]["ci_low"] > 0 and out["auc_retrained"] > out["auc_frozen"]
    assert frozen_vs_retrained(cand.iloc[:300], "4h")["verdict"] == "insuffisant"


# ---------------------------------------------------------------------------
# 4. Registre : promotions, rejets, challenger en attente, retour en arrière, version immuable
# ---------------------------------------------------------------------------
def make_app(tmp_path):
    app = App(tmp_path, source=SyntheticSource(["BTC/USDC", "ETH/USDC"], days=5), start=False,
              settings_override={"market": {"source": "synthetic"}, "general": {"watchlist": ["BTC/USDC", "ETH/USDC"]}})
    app._startup()
    return app


@pytest.fixture
def app(tmp_path):
    instance = make_app(tmp_path)
    yield instance
    instance.db.close()


BASE_ROWS = make_candidates(500, "simple", seed=1)[0]


def result(version, ok=True, trial=False, threshold=0.0):
    m = quick_model(BASE_ROWS, version, threshold)
    m.metrics = {"verdict": "validé" if ok else "non validé", "trial": trial, "holdout_mean_r": 0.2}
    return SimpleNamespace(final_model=m, ok=ok, trial=trial, reasons=["test"])


def cmp(verdict):
    return {"verdict": verdict, "explanation": f"comparaison : {verdict}", "champion": "x", "clean": None, "indicative": None}


def events(app, version=None):
    rows = app.models.history("4h", 200)
    return [e["event"] for e in reversed(rows) if version is None or e["version"] == version]


def test_first_validated_model_becomes_champion_and_unvalidated_never_does(app):
    store = app.models
    assert store.register(result("m-rejet", ok=False), {})["status"] == "rejected"
    with pytest.raises(ValueError):
        store.promote("m-rejet")
    assert store.register(result("m-essai", ok=False, trial=True), {})["status"] == "trial"
    with pytest.raises(ValueError):
        store.promote("m-essai")
    assert store.champion("4h") is None and store.trial("4h").version == "m-essai"
    row = store.register(result("m-1"), {})
    assert row["status"] == "champion" and row["promoted_at"] and len(row["sha256"]) == 64 and row["params"]["train_end"]
    assert store.champion("4h").version == "m-1" and store.trial("4h") is None
    assert events(app, "m-1") == ["enregistré", "promu"] and events(app, "m-essai") == ["enregistré", "à l'essai", "remplacé"]
    assert "Premier modèle validé" in next(e for e in store.history("4h") if e["event"] == "promu")["reason"]


def test_a_validated_model_only_replaces_the_champion_when_proven_better(app):
    store = app.models
    store.register(result("m-1"), {})
    # pas de comparaison concluante : il attend comme challenger, le champion reste en place
    assert store.register(result("m-2"), {}, cmp("insuffisant"))["status"] == "challenger"
    assert store.champion("4h").version == "m-1" and store.challenger("4h").version == "m-2"
    assert [m.version for m in store.shadows("4h")] == ["m-1", "m-2"]
    assert events(app, "m-2") == ["enregistré", "à l'épreuve"]
    # un challenger est déjà à l'épreuve : le suivant n'est pas retenu (on laisse le temps de conclure)
    assert store.register(result("m-3"), {}, cmp("non départagés"))["status"] == "retired"
    assert store.challenger("4h").version == "m-2" and events(app, "m-3") == ["enregistré", "non retenu"]
    # nettement moins bon que le champion : rejeté, même s'il est validé seul
    assert store.register(result("m-4"), {}, cmp("moins bon"))["status"] == "rejected"
    assert store.champion("4h").version == "m-1" and "moins bon" in next(e for e in store.history("4h") if e["version"] == "m-4" and e["event"] == "rejeté")["reason"]
    # prouvé meilleur : il devient champion, l'ancien est conservé et reste suivi
    row = store.register(result("m-5"), {"x": 1}, cmp("meilleur"))
    assert row["status"] == "champion" and store.champion("4h").version == "m-5" and store.get("m-1")["status"] == "retired"
    assert events(app, "m-1")[-1] == "rétrogradé" and events(app, "m-5") == ["enregistré", "promu"]
    promoted = next(e for e in store.history("4h") if e["version"] == "m-5" and e["event"] == "promu")
    assert promoted["data"]["previous"] == "m-1" and promoted["data"]["comparison"]["verdict"] == "meilleur" and promoted["data"]["source"] == "historique"
    assert store.monitoring("4h")["previous"] == "m-1" and store.previous("4h").version == "m-1"
    assert [m.version for m in store.shadows("4h")] == ["m-5", "m-2", "m-1"]
    assert store.report("m-5")["comparison"]["verdict"] == "meilleur" and store.report("m-5")["x"] == 1
    # l'IA trade avec le champion ; le challenger ne trade jamais
    assert all(a["model"] == "m-5" for a in app.agents_status())
    # un challenger resté trop longtemps sans être départagé cède sa place
    app.db.execute("UPDATE models SET created_at = created_at - ? WHERE version = 'm-2'", ((CHALLENGER_MAX_DAYS + 1) * SECONDS_PER_DAY,))
    assert store.register(result("m-6"), {}, cmp("insuffisant"))["status"] == "challenger"
    assert store.get("m-2")["status"] == "retired" and events(app, "m-2")[-1] == "remplacé"


def test_versions_are_immutable(app, tmp_path):
    store = app.models
    store.register(result("m-1"), {})
    with pytest.raises(ValueError):  # une version enregistrée n'est jamais réécrite
        store.register(result("m-1"), {})
    assert store.champion("4h").version == "m-1"
    path = tmp_path / "models" / "m-1.joblib"
    path.write_bytes(path.read_bytes() + b"modification")
    store.invalidate()
    assert store.champion("4h") is None  # fichier modifié : le modèle est refusé, l'IA n'a plus de champion
    assert "empreinte invalide" in events(app, "m-1")
    assert app.db.scalar("SELECT COUNT(*) FROM logs WHERE category = 'models' AND message LIKE '%fichier modifié%'") >= 1


def test_rollback_restores_the_previous_champion(app):
    store = app.models
    store.register(result("m-1"), {})
    with pytest.raises(UserError):  # rien à quoi revenir
        app.rollback_model(confirm=True)
    store.register(result("m-2"), {}, cmp("meilleur"))
    with pytest.raises(UserError):  # confirmation obligatoire
        app.rollback_model()
    assert app.rollback_model(confirm=True) == {"champion": "m-1"}
    assert store.champion("4h").version == "m-1" and store.get("m-2")["status"] == "retired"
    assert events(app, "m-2")[-1] == "rétrogradé" and events(app, "m-1")[-1] == "rétabli"
    assert store.monitoring("4h") is None
    with pytest.raises(UserError):
        app.rollback_model(confirm=True)
    # promotion décidée par l'utilisateur : possible pour un modèle validé, et tracée comme telle
    store.promote("m-2")
    assert store.champion("4h").version == "m-2" and events(app, "m-2")[-1] == "promu manuellement"


# ---------------------------------------------------------------------------
# 5. Prédictions à blanc, promotion et retour en arrière sur des situations vécues
# ---------------------------------------------------------------------------
def test_shadow_predictions_are_recorded_before_the_outcome(app):
    store = app.models
    store.register(result("m-1", threshold=0.0), {})
    store.register(result("m-2", threshold=5.0), {}, cmp("insuffisant"))  # challenger très exigeant : ne prendrait rien
    # à une même clôture, l'IA examine au plus une situation par stratégie
    first = {name: BASE_ROWS[BASE_ROWS["strategy"] == name].iloc[0].to_dict() for name in ("trend_follow", "trend_breakout")}
    rows = list(first.values())
    assert app.record_shadow_predictions("BTC/USDC", "4h", 1_700_000_000_000, rows) == 4
    assert app.record_shadow_predictions("BTC/USDC", "4h", 1_700_000_000_000, rows) == 0  # une prédiction ne se réécrit pas
    saved = app.db.query("SELECT * FROM shadow_predictions ORDER BY id")
    assert {s["model_version"] for s in saved} == {"m-1", "m-2"} and all(0 <= s["p_win"] <= 1 for s in saved)
    assert all(s["take"] == 0 for s in saved if s["model_version"] == "m-2")
    assert [s["take"] for s in saved if s["model_version"] == "m-1"] == [int(s["expected_r"] >= 0) for s in saved if s["model_version"] == "m-1"]
    assert app.record_shadow_predictions("BTC/USDC", "1h", 1, rows) == 0  # aucun modèle suivi sur cette unité de temps


def lived(app, start_week, weeks, per_week, decide, outcomes=(2.0, -1.0, -1.0)):
    """Situations vécues en direct et dénouées : `decide(version, gagnante)` dit si chaque modèle la prenait."""
    k = app.db.scalar("SELECT COUNT(*) FROM candidates") or 0
    for w in range(start_week, start_week + weeks):
        for j in range(per_week):
            ts = int(w * WEEK + j * 3_600_000 * 8)
            r = outcomes[k % len(outcomes)]
            app.db.execute("INSERT INTO candidates(symbol, timeframe, ts, strategy, source, decision, account_id, outcome, outcome_r) "
                           "VALUES ('BTC/USDC', '4h', ?, 'trend_follow', 'live', 'refusé', 1, ?, ?)", (ts, "target" if r > 0 else "stop", r))
            for version in decide.__defaults__[0]:
                app.db.execute("INSERT INTO shadow_predictions(symbol, timeframe, ts, strategy, model_version, p_win, expected_r, take, created_at) "
                               "VALUES ('BTC/USDC', '4h', ?, 'trend_follow', ?, 0.5, 0.1, ?, 0)", (ts, version, int(decide(version, r > 0))))
            k += 1


def test_challenger_is_promoted_on_lived_situations_then_watched(app):
    store = app.models
    store.register(result("m-1"), {})
    store.register(result("m-2"), {}, cmp("insuffisant"))
    app.db.execute("UPDATE models SET created_at = 0, promoted_at = 0")
    week0 = 2900  # semaines depuis 1970 : dates récentes

    def smart(version, winner, versions=("m-1", "m-2")):  # le challenger évite les perdantes, le champion prend tout
        return True if version == "m-1" else winner

    # trois semaines seulement : beaucoup de situations, mais pas assez de recul → aucune décision
    lived(app, week0, 3, 12, smart)
    out = app.review_models()
    assert out["action"] is None and out["challenger"]["verdict"] == "insuffisant" and store.champion("4h").version == "m-1"
    assert out["challenger"]["n"] == 36 and out["challenger"]["n_diff"] == 24 and out["challenger"]["blocks"] == 3
    # cinq semaines de plus : la différence est nette et durable → promotion
    lived(app, week0 + 3, 5, 12, smart)
    out = app.review_models()
    assert out["action"] == "promotion" and out["challenger"]["verdict"] == "meilleur" and out["challenger"]["ci_low"] > 0
    assert store.champion("4h").version == "m-2" and store.challenger("4h") is None and store.get("m-1")["status"] == "retired"
    promoted = next(e for e in store.history("4h") if e["version"] == "m-2" and e["event"] == "promu")
    assert promoted["data"]["source"] == "direct" and promoted["data"]["previous"] == "m-1"
    assert app.review_models()["action"] is None  # rien d'autre à décider pour l'instant

    # après la promotion, le nouveau champion se met à faire moins bien que l'ancien : retour automatique
    since = store.monitoring("4h")["since"]
    start = int(since * 1000 // WEEK) + 1

    def reversed_(version, winner, versions=("m-1", "m-2")):  # l'ancien évite maintenant les perdantes, le nouveau prend tout
        return winner if version == "m-1" else True

    lived(app, start, 8, 12, reversed_)
    out = app.review_models()
    assert out["action"] == "retour en arrière" and out["monitoring"]["verdict"] == "moins bon"
    assert store.champion("4h").version == "m-1" and store.get("m-2")["status"] == "retired"
    last = store.history("4h")[0]
    assert last["event"] == "rétabli" and last["data"]["automatic"] is True and "Retour automatique" in last["reason"]
    assert app.db.scalar("SELECT COUNT(*) FROM logs WHERE message LIKE 'Retour en arrière%'") == 1


def test_a_worse_challenger_is_rejected_on_lived_situations(app):
    store = app.models
    store.register(result("m-1"), {})
    store.register(result("m-2"), {}, cmp("insuffisant"))
    app.db.execute("UPDATE models SET created_at = 0, promoted_at = 0")

    def clumsy(version, winner, versions=("m-1", "m-2")):  # le challenger ne garde que les perdantes
        return True if version == "m-1" else not winner

    lived(app, 2900, 8, 12, clumsy)
    out = app.review_models()
    assert out["action"] == "rejet" and store.champion("4h").version == "m-1" and store.get("m-2")["status"] == "rejected"
    assert store.challenger("4h") is None and events(app, "m-2")[-1] == "rejeté"


def test_agent_records_shadow_predictions_before_deciding(db, paper, pm, prices, clock):
    from .test_risk_agent_backtest import _feat_with_signal

    settings = apply_update(DEFAULT_SETTINGS, {"ai": {"regime_filter": False}, "risk": {"min_reward_to_fees": 1.0}})
    settings["ai"]["strategies"] = {k: k == "trend_breakout" for k in settings["ai"]["strategies"]}
    df, feat, idx = _feat_with_signal()
    sub = feat.iloc[: idx[-1] + 1].reset_index(drop=True)
    prices["ETH/USDC"] = float(sub["close"].iloc[-1])
    calls = []
    agent = TradingAgent("ia", "training", db, pm, RiskManager(db, paper.account_id, lambda: settings, clock), lambda: settings, lambda: None,
                         timeframe="1h", clock=clock, shadow_fn=lambda *a: calls.append(a) or 1)
    agent.running = True
    agent.on_bar("ETH/USDC", sub)
    assert len(calls) == 1
    symbol, tf, bar_ts, rows = calls[0]
    assert (symbol, tf, bar_ts) == ("ETH/USDC", "1h", int(sub["ts"].iloc[-1])) and rows[0]["strategy"] == "trend_breakout"
    assert db.one("SELECT outcome FROM candidates")["outcome"] is None  # enregistré avant toute issue
    # une panne de cet enregistrement ne bloque pas l'IA
    broken = TradingAgent("ia2", "training", db, pm, RiskManager(db, paper.account_id, lambda: settings, clock), lambda: settings, lambda: None,
                          timeframe="1h", clock=clock, shadow_fn=lambda *a: 1 / 0)
    broken.running = True
    broken.on_bar("ETH/USDC", sub)
    assert broken.stats["decisions"] == 1


# ---------------------------------------------------------------------------
# 6. Journal des expériences et exigence de preuve
# ---------------------------------------------------------------------------
def test_experiment_log_counts_distinct_configurations(app):
    base = app.training_config(app.settings, "4h")
    assert app.training_alpha(app.settings, "4h") == (0.05, 1)
    app.log_experiment("train", base, {"verdict": "non validé"})
    app.log_experiment("train", base, {"verdict": "non validé"})  # ré-entraînement identique : ne compte pas comme un nouvel essai
    assert app.training_alpha(app.settings, "4h") == (0.05, 1)
    for recipe in ("prudent", "riche", "lineaire"):
        app.log_experiment("train", {**base, "recipe": recipe}, {"verdict": "validé" if recipe == "riche" else "non validé"})
    alpha, n = app.training_alpha(app.settings, "4h")
    assert n == 4 and alpha == pytest.approx(0.05 / 4)
    other = apply_update(app.settings, {"ai": {"recipe": "comite"}})  # une configuration jamais essayée compte tout de suite
    assert app.training_alpha(other, "4h") == (pytest.approx(0.05 / 5), 5)
    app.log_experiment("bench", {"sizes": [1500]}, {"score": 0.9})
    exp = app.experiments()
    assert exp["totals"] == {"train": 5, "bench": 1} and exp["distinct_training_configs"] == 4 and exp["validated"] == 1
    assert exp["confidence_pct"] == pytest.approx(98.75) and "98,75" in exp["note"] and exp["rows"][0]["kind"] == "bench"
    assert exp["rows"][1]["params"]["recipe"] == "lineaire" and exp["rows"][1]["result"]["verdict"] == "non validé"
    assert app.config_hash(base) == app.config_hash(dict(reversed(list(base.items())))) != app.config_hash({**base, "timeframe": "1h"})


def test_training_registers_experiment_and_compares_with_the_champion(tmp_path):
    """Chaîne complète : un deuxième entraînement validé ne détrône pas le champion sans preuve qu'il est meilleur."""
    from .helpers import HOUR, make_series
    from .test_learning import HistorySource

    def drift(ts):
        return np.where((ts // HOUR) % 24 < 12, 0.003, -0.003)

    series = {s: make_series(30_000, seed=10 + i, start=p, drift_fn=drift) for i, (s, p) in enumerate((("BTC/USDC", 30_000), ("ETH/USDC", 2_000)))}
    app = App(tmp_path, source=HistorySource(series), start=False, settings_override={
        "general": {"watchlist": list(series)}, "market": {"source": "synthetic"},
        "ai": {"timeframe": "1h", "strategies": {k: True for k in ("trend_breakout", "pullback", "mean_reversion", "momentum", "trend_follow")}}})
    try:
        first = app._train(lambda x, m: None, app.settings, "1h", 1250, False)
        assert first["ok"] and first["status"] == "champion" and first["comparison"] is None
        assert first["report"]["experiments"] == {"distinct_configs": 1, "alpha": 0.05, "confidence_pct": 95.0}
        second = app._train(lambda x, m: None, app.settings, "1h", 1250, True)
        # le champion a déjà vu la période de contrôle du second : pas de comparaison équitable → challenger, pas champion
        assert second["ok"] and second["status"] == "challenger" and second["comparison"]["verdict"] == "insuffisant"
        assert second["comparison"]["champion_knew_share"] == 1.0 and any("Face au champion" in r for r in second["reasons"])
        assert app.models.champion("1h").version == first["version"] and app.models.challenger("1h").version == second["version"]
        # les deux simulations de référence utilisent la même taille de risque
        rep = second["report"]
        assert rep["backtest_model"]["initial"] == rep["backtest_baseline"]["initial"]
        exp = app.experiments()
        assert exp["trainings"] == 2 and exp["distinct_training_configs"] == 1 and exp["validated"] == 2
        assert exp["rows"][0]["result"]["status"] == "challenger" and exp["rows"][0]["result"]["comparison"]["verdict"] == "insuffisant"
        # une troisième tentative avec une autre recette : l'exigence de preuve monte
        other = apply_update(app.settings, {"ai": {"recipe": "prudent"}})
        third = app._train(lambda x, m: None, other, "1h", 1250, False)
        assert third["report"]["experiments"]["distinct_configs"] == 2 and third["report"]["alpha"] == pytest.approx(0.025)
        assert any("2 configurations différentes" in r for r in third["reasons"])
        status = app.learning_status()
        assert status["champion"]["version"] == first["version"] and status["challenger"]["version"] == second["version"]
        assert status["state"].startswith("approuvé") and status["acting"] == first["version"] and len(status["rules"]) == 6
        assert status["comparison"]["verdict"] == "insuffisant" and status["comparison"]["historical"]["verdict"] == "insuffisant"
        assert status["champion"]["sha256"] and status["champion"]["params"]["recipe"] == "mixte"
        assert status["health"]["version"] == first["version"] and status["health"]["live"]["n"] == 0 and status["health"]["notes"]
        assert status["experiments"]["trainings"] == 3 and [e["event"] for e in status["history"]][-2:] == ["promu", "enregistré"]
        json.dumps(status, allow_nan=False, default=str)
    finally:
        app.db.close()


def test_champion_api(app):
    app.models.register(result("m-1"), {})
    app.models.register(result("m-2"), {}, cmp("meilleur"))
    with TestClient(create_api(app)) as c:
        assert c.post("/api/auth/setup", json={"password": "faux-mot-de-passe-de-test"}, headers=H).status_code == 200
        st = c.get("/api/ai/champion").json()
        assert st["champion"]["version"] == "m-2" and st["monitoring"]["previous"] == "m-1" and st["history"][0]["label"]
        assert c.post("/api/ai/rollback", json={}, headers=H).status_code == 400
        assert c.post("/api/ai/rollback", json={"confirm": True}, headers=H).json() == {"champion": "m-1"}
        assert c.get("/api/ai/champion").json()["champion"]["version"] == "m-1"
        assert c.get("/api/ai/experiments").json()["trainings"] == 0
        models = {m["version"]: m["status"] for m in c.get("/api/ai/models").json()}
        assert models == {"m-1": "champion", "m-2": "retired"}


def test_learning_proof_job_runs_on_history_and_is_logged(tmp_path):
    """La mesure « modèle figé contre ré-entraîné » tourne dans l'application, est conservée et inscrite au journal des expériences."""
    import time as _time

    from .helpers import HOUR, make_series
    from .test_learning import HistorySource

    # le motif s'inverse aux deux tiers : un modèle figé avant l'inversion devient mauvais
    def drift(ts):
        k = np.arange(len(ts))
        sign = np.where(k < 0.66 * len(ts), 1.0, -1.0)
        return sign * np.where((ts // HOUR) % 24 < 12, 0.003, -0.003)

    series = {s: make_series(16_000, seed=40 + i, start=p, drift_fn=drift) for i, (s, p) in enumerate((("BTC/USDC", 30_000), ("ETH/USDC", 2_000)))}
    app = App(tmp_path, source=HistorySource(series), start=False, settings_override={
        "general": {"watchlist": list(series)}, "market": {"source": "synthetic"},
        "ai": {"timeframe": "1h", "strategies": {k: True for k in ("trend_breakout", "pullback", "mean_reversion", "momentum", "trend_follow")}}})
    try:
        app.start_learning_proof(days=720, n_steps=4)
        with pytest.raises(UserError):
            app.start_learning_proof(days=720)  # une seule mesure à la fois
        deadline = _time.time() + 600
        while app.jobs.running("proof") and _time.time() < deadline:
            _time.sleep(0.2)
        job = next(j for j in app.jobs.list() if j["kind"] == "proof")
        assert job["status"] == "done", job.get("error")
        rep = app.learning_proof()
        assert rep["timeframe"] == "1h" and rep["symbols"] == ["BTC/USDC", "ETH/USDC"] and len(rep["steps"]) == 4
        assert rep["verdict"] in ("meilleur", "non départagés", "moins bon", "insuffisant") and rep["comparison"]["n"] > 0
        assert rep["steps"][0]["frozen"] == pytest.approx(rep["steps"][0]["retrained"])
        exp = app.experiments()
        assert exp["totals"].get("proof") == 1 and exp["rows"][0]["result"]["verdict"] == rep["verdict"]
        assert app.learning_status()["proof"]["verdict"] == rep["verdict"]
        json.dumps(rep, allow_nan=False)
    finally:
        app.db.close()
