"""Programme multi-actifs, vague 1 : coûts par côté, mesures, poches collectives, jugement à 34 tests et arrêt au 31 août
2026, sur données construites (aucun réseau)."""

import json

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import multi_vague1 as V


def test_each_change_pays_the_side_cost_at_execution():
    net = V.net_returns(np.zeros(5), np.array([[0.0], [1.0], [1.0], [0.0], [0.0]]), 0.001)
    assert net[:, 0].tolist() == pytest.approx([0, 0, -0.001, 0, -0.001])


def test_trade_stats_count_costs_inside_each_trade():
    pos = np.array([0, 1, 1, 0, 0, 1, 0, 0], dtype=float)
    net = np.array([0, 0, 0.02, 0.01, -0.001, 0, -0.03, -0.001])
    s = V.trade_stats(pos, net, 365)
    assert s["transactions"] == 2
    first, second = 1.02 * 1.01 * 0.999 - 1, 0.97 * 0.999 - 1
    assert s["esperance_par_transaction"] == pytest.approx((first + second) / 2)
    assert s["profit_factor"] == pytest.approx(first / -second)


def _returns(rng, days, effect_index, effect):
    """Rendements journaliers indépendants (1,5 % d'écart type) ; avantage de momentum 20 j sur une colonne."""
    sd = 0.015
    r = rng.normal(0, sd, (days, len(V.INSTRUMENTS)))
    if effect_index is not None:
        kappa = effect / (np.sqrt(0.5) * np.sqrt(252))
        for t in range(20, days):
            r[t, effect_index] += kappa * sd * np.sign(r[t - 20:t, effect_index].sum())
    return np.log1p(np.maximum(r, -0.9))


def _closes(rng, days, effect_index=None, effect=0.0):
    r = _returns(rng, days, effect_index, effect)
    out = {}
    for a, name in enumerate(V.INSTRUMENTS):
        freq = "D" if V.INSTRUMENTS[name][0] == "crypto" else "B"
        idx = pd.date_range("2015-01-01", periods=days + 1, freq=freq)
        out[name] = pd.Series(100 * np.exp(np.r_[0.0, np.cumsum(r[:, a])]), index=idx)
    return out


def test_collective_sleeves_hold_zero_on_days_without_quotes():
    prep = V.prepare(_closes(np.random.default_rng(1), 400))
    s = V.collective_series(prep, 0)
    assert s.index.is_monotonic_increasing and (s.index.to_series().diff().dropna() == pd.Timedelta(days=1)).all()
    assert np.isfinite(s).all()


def test_the_program_finds_a_strong_edge_only_where_it_was_put():
    prep = V.prepare(_closes(np.random.default_rng(2), 1500, effect_index=7, effect=3.0))
    verdict = V.judge(prep)
    assert len(verdict["tests"]) == 2 + 4 * len(V.INSTRUMENTS)
    kept = {n for _, n in verdict["retenues"]}
    assert "SP500" in kept and kept <= {"SP500"}
    assert set(V.describe(prep, verdict["retenues"])) == {f"{v}@{n}" for v, n in verdict["retenues"]}
    null = V.judge(V.prepare(_closes(np.random.default_rng(3), 1500)))
    assert null["retenues"] == [] and null["validations_collectives"] == []


def test_history_stops_before_the_reserved_period(monkeypatch, tmp_path):
    days = pd.date_range("2024-01-01", "2026-11-22", freq="D")
    long = pd.Series(np.linspace(100, 200, len(days)), index=days)
    bars = pd.DataFrame({"c": long.to_numpy()}, index=(days.asi8 // 10**6 // V.moteur.DAY_MS))
    monkeypatch.setattr(V.signaux, "load_bars", lambda *a, **k: (bars, {}))
    monkeypatch.setattr(V.actifs, "download", lambda *a, **k: long)
    closes = V.load_closes(tmp_path, client=None)
    assert all(s.index[-1] <= pd.Timestamp(V.LAST_DAY) for s in closes.values())


def test_the_descriptive_table_covers_the_sixteen_candidates_and_never_validates():
    closes = _closes(np.random.default_rng(4), 800)
    prep = V.prepare(closes)
    verdict = V.judge(prep)
    out = V.descriptive(prep, closes, verdict)
    assert len(out["candidats_par_instrument"]) == len({(r["variante"], r["instrument"]) for r in verdict["tests"] if r["jugement"] == "par_instrument"})
    assert set(out["faisabilite_huit_poches"]) == {str(c) for c in V.CAPITALS} and "statut" in out
    assert not any(out["faisabilite_huit_poches"]["30"].values())  # 3,75 € par poche : ni le minimum Binance ni une part de fonds


# -- version 2 ---------------------------------------------------------------------------------------------------------
def test_v2_executes_at_the_next_open_the_reviewers_counterexample():
    o, c = np.array([100.0, 100.0, 110.0]), np.array([100.0, 100.0, 110.0])
    pos = np.array([[1.0], [1.0]])  # entrée décidée à la clôture de 100
    net = V.net_returns_open(o, c, pos, 0.001)
    assert net[1, 0] == pytest.approx(-0.001)  # l'écart de nuit 100 → 110 n'appartient pas à la nouvelle position
    assert V.net_returns(np.diff(np.log(c)), pos, 0.001)[1, 0] == pytest.approx(0.099)  # ce que créditait la v1


def test_v2_entry_hold_exit_and_fees_are_attributed_separately():
    o = np.array([100.0, 100.0, 110.0, 121.0, 121.0])
    c = np.array([100.0, 100.0, 110.0, 121.0, 133.1])
    pos = np.array([[1.0], [1.0], [0.0], [0.0]])
    net = V.net_returns_open(o, c, pos, 0.001)[:, 0]
    assert net[1] == pytest.approx(110 / 110 - 1 - 0.001)  # entrée : séance seulement, frais d'achat
    assert net[2] == pytest.approx(121 / 110 - 1)  # maintien : nuit 110 → 121 puis séance 121 → 121
    assert net[3] == pytest.approx(-0.001)  # sortie à l'ouverture : la séance 121 → 133,1 n'est pas gagnée, frais de vente


def test_v2_trade_stats_keep_the_position_carried_into_the_window_and_use_calendar_months():
    pos = np.array([1.0, 1.0, 0.0, 0.0])  # position portée avant la fenêtre, liquidation décidée à la clôture du 1er jour évalué
    net = np.array([0.0, 0.10, -0.001, 0.0])
    dates = pd.to_datetime(["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"])
    s = V.trade_stats_v2(pos, net, dates, 1, 365)
    assert s["transactions"] == 1 and s["esperance_par_transaction"] == pytest.approx(1.10 * 0.999 - 1)
    assert s["part_des_mois_calendaires_positifs"] == pytest.approx(0.5)  # janvier positif, février négatif


def _ohlc(rng, days, shock_after=None):
    closes = _closes(rng, days)
    out = {}
    for name, s in closes.items():
        c = s.copy()
        if shock_after is not None and name == "SP500":
            later = c.index > shock_after
            c[later] = c[later] * np.linspace(0.5, 2.0, later.sum())  # tout le futur du S&P 500 est bouleversé
        out[name] = pd.DataFrame({"o": c.shift(1).fillna(c.iloc[0]), "c": c})
    return out


def test_v2_selections_are_fixed_before_any_evaluated_date():
    base = V.prepare_v2(_ohlc(np.random.default_rng(5), 3000))
    later = max(pd.Timestamp(V.COMMON_CUT), base["SP500"]["dates"][base["SP500"]["cut"]])
    shocked = V.prepare_v2(_ohlc(np.random.default_rng(5), 3000, shock_after=later))
    a, b = V.judge_v2(base)["selection"], V.judge_v2(shocked)["selection"]
    assert a == b  # ni la sélection collective ni aucune sélection par instrument ne lit le futur
    assert len(V.judge_v2(base)["tests"]) == 2 + 4 * len(V.INSTRUMENTS)


def test_v2_feasibility_uses_the_economic_floor_and_says_undetermined_otherwise():
    f = V.feasibility_v2()
    assert f["30"]["huit_poches_equiponderees"]["statut"] == "irréalisable"  # 3,75 € < 10 €, quel que soit le change
    assert f["30"]["portefeuille_entier_une_position"]["statut"] == "indéterminé"
    assert f["100"]["huit_poches_equiponderees"]["statut"] == "indéterminé"


# -- version 2.1 (contre-revue du 28 novembre sur 5a59795) ------------------------------------------------------------
def test_v2_1_charges_the_exit_fee_on_the_amount_sold_at_the_open():
    o = np.array([100.0, 100.0, 100.0, 100.0, 110.0])
    c = np.array([100.0, 100.0, 100.0, 100.0, 110.0])
    pos = np.array([[1.0], [1.0], [0.0], [0.0]])  # détenue la veille, vente à l'ouverture après une nuit de +10 %
    assert V.net_returns_open(o, c, pos, 0.001)[3, 0] == pytest.approx(1.10 * 0.999 - 1)  # 110 vendus, 0,11 de frais : +9,89 %
    assert V.net_returns_open(o, c, pos, 0.001, "2.0")[3, 0] == pytest.approx(0.099)  # ce que calculait la 2.0 (archivée)
    assert V.net_returns_open(o, c, pos, 0.001)[1, 0] == pytest.approx(-0.001)  # achat de 100 à l'ouverture


def test_v2_1_counts_trades_from_executions_not_from_contiguous_exposure():
    pos = np.array([1.0, 0.0, 1.0, 0.0, 0.0])  # deux achats et deux ventes
    net = np.array([0.0, 0.05, -0.08, 0.03, 0.01])
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    assert V.trade_ids(pos).tolist() == [-1, 1, 1, 2, 2]
    s = V.trade_stats_v2(pos, net, dates, 0, 365)
    first, second = 1.05 * 0.92 - 1, 1.03 * 1.01 - 1
    assert s["transactions"] == 2 and s["esperance_par_transaction"] == pytest.approx((first + second) / 2)
    assert s["profit_factor"] == pytest.approx(second / -first)
    assert V.trade_stats_v2(pos, net, dates, 0, 365, "2.0")["transactions"] == 1  # la 2.0 les fusionnait


def test_v2_1_documents_the_series_and_keeps_the_previous_version_results(monkeypatch, tmp_path):
    ohlc = _ohlc(np.random.default_rng(6), 1500)
    fp = V.series_fingerprints(ohlc)
    assert fp["BTC"]["debut"] == ohlc["BTC"].index[0].strftime("%Y-%m-%d") and fp["BTC"]["lignes"] == len(ohlc["BTC"])
    changed = {**ohlc, "BTC": ohlc["BTC"].assign(c=ohlc["BTC"]["c"] * 1.0001)}
    assert V.series_fingerprints(changed)["BTC"]["sha256"] != fp["BTC"]["sha256"] and V.series_fingerprints(ohlc) == fp
    monkeypatch.setattr(V, "load_ohlc", lambda *a, **k: ohlc)
    out = tmp_path / "vague1.json"
    first = V.main(["--v2", "--version-v2", "2.0", "--out", str(out), "--trials", ""])
    V.main(["--v2", "--out", str(out), "--trials", ""])
    saved = json.loads((tmp_path / "vague1-v2.json").read_text(encoding="utf-8"))
    assert saved["version"] == "2.1" and saved["archives"]["2.0"]["tests"] == json.loads(json.dumps(first["tests"]))
