"""OMEGA C — RADAR, ATLAS : fiche de couverture, dépendances, séries, coûts publiés."""

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import atlas, moteur


def test_a_crypto_row_reports_coverage_gaps_delisting_and_renames():
    d0 = moteur._day("2024-01-01")
    idx = pd.RangeIndex(d0, d0 + 400)
    c = pd.Series(10.0, index=idx)
    c.iloc[100:110] = np.nan  # dix jours sans cotation
    c.iloc[300:] = np.nan  # retirée après 300 jours
    bars = pd.DataFrame({"c": c, "v": 1000.0})
    member = pd.Series([True] * 200 + [False] * 200, index=idx)
    row = atlas.crypto_row("MATICUSDT", bars, {"cut_at": None}, member, d0 + 399)
    assert row["premier_jour"] == "2024-01-01" and row["jours_cotes"] == 290 and row["jours_sans_cotation"] == 110
    assert row["retiree"] and row["volume_median_usdt_derniere_annee"] == 10000.0 and row["part_des_jours_dans_l_univers"] == 0.5
    assert row["renommage"] == "MATICUSDT → POLUSDT"
    assert atlas.crypto_row("XUSDT", pd.DataFrame(), {}, None, d0) == {"symbole": "XUSDT", "cotee": False}


def test_dependence_sees_a_dominant_common_factor():
    rng = np.random.default_rng(50)
    d0 = moteur._day("2021-01-01")
    idx = pd.RangeIndex(d0, d0 + 7 * 60)
    common = np.cumsum(rng.normal(0, 0.03, len(idx)))
    together = pd.DataFrame({f"S{k}": 100 * np.exp(common + np.cumsum(rng.normal(0, 0.005, len(idx)))) for k in range(5)}, index=idx)
    apart = pd.DataFrame({f"S{k}": 100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(idx)))) for k in range(5)}, index=idx)
    member = pd.DataFrame(True, index=idx, columns=together.columns)
    hi, lo = atlas.dependence(together, member), atlas.dependence(apart, member)
    assert hi[2021]["part_premiere_composante"] > 0.8 and hi[2021]["correlation_moyenne"] > 0.7
    assert lo[2021]["part_premiere_composante"] < 0.5 and abs(lo[2021]["correlation_moyenne"]) < 0.3


def test_series_row_measures_history_and_the_longest_gap():
    s = pd.Series([1.0, 2.0, 3.0, np.nan, 4.0], index=pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-06", "2024-01-15"]))
    row = atlas.series_row("X", s)
    assert row == {"serie": "X", "premier_jour": "2024-01-01", "dernier_jour": "2024-01-15", "jours": 4, "plus_long_trou_jours": 10}


def test_costs_are_read_from_the_published_results():
    out = atlas.costs()
    assert "frais_dates" in out and "part_des_entrees_faisables" in out
    caps = next(iter(out["part_des_entrees_faisables"].values()))
    assert set(caps) == {"30 €", "100 €", "1000 €", "10000 €"} and all(0 <= v <= 1 for v in caps.values())


def _panel(rng, weeks, n, down_extra):
    d0 = moteur._day("2019-01-06")
    idx = pd.RangeIndex(d0, d0 + 7 * weeks)
    btc_w = rng.normal(0, 0.06, weeks)
    common = np.where(btc_w < np.quantile(btc_w, 0.1), down_extra, 0.0)  # un facteur commun ne joue qu'en crise
    rets = {f"S{k}": 0.5 * btc_w + common * rng.normal(0, 1, weeks).mean() + rng.normal(0, 0.04, weeks) for k in range(n)}
    shock = rng.normal(0, 1, weeks)
    for k in range(n):
        rets[f"S{k}"] = rets[f"S{k}"] + common * shock
    daily = {s: np.repeat(np.exp(np.cumsum(np.r_[0.0, r[1:]])), 7) for s, r in {"BTCUSDT": btc_w, **rets}.items()}
    c = pd.DataFrame(daily, index=idx)
    return c, pd.DataFrame(True, index=idx, columns=c.columns)


def test_crisis_dependence_sees_a_factor_that_only_acts_in_btc_crashes():
    rng = np.random.default_rng(51)
    c, member = _panel(rng, 400, 6, down_extra=0.08)
    out = atlas.crisis_dependence(c, member, reps=300)
    assert out["semaines"]["crise"] == out["semaines"]["hausse"] == 40
    assert out["correlation_moyenne"]["crise"] > out["correlation_moyenne"]["hausse"] + 0.2
    assert out["h1_crise_moins_hausse"]["significatif_5pct"] and out["part_des_membres_en_baisse"]["crise"] >= 0.75 > out["part_des_membres_en_baisse"]["hausse"]
    calm, member = _panel(np.random.default_rng(52), 400, 6, down_extra=0.0)
    quiet = atlas.crisis_dependence(calm, member, reps=300)
    assert abs(quiet["h1_crise_moins_hausse"]["ecart"]) < 0.25  # symétrie des deux déciles sans effet de crise


def test_correlation_stability_compares_consecutive_years_on_common_pairs():
    rng = np.random.default_rng(53)
    d0 = moteur._day("2021-01-01")
    idx = pd.RangeIndex(d0, d0 + 7 * 160)
    loads = np.linspace(0.2, 1.5, 6)  # structure fixe : chaque actif a sa propre exposition au facteur
    f = rng.normal(0, 0.03, len(idx))
    c = pd.DataFrame({f"S{k}": 100 * np.exp(np.cumsum(loads[k] * f + rng.normal(0, 0.01, len(idx)))) for k in range(6)}, index=idx)
    out = atlas.correlation_stability(c, pd.DataFrame(True, index=idx, columns=c.columns))
    assert set(out) >= {"2021→2022", "2022→2023"} and all(v["paires"] == 15 for v in out.values())
    assert np.mean([v["rang_spearman"] for v in out.values()]) > 0.5

def test_published_output_and_journal_aliases_are_refused_before_loading(tmp_path, monkeypatch):
    results = tmp_path / "research" / "scalp-v1"
    results.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(atlas.rejeu, "RESULTS", results)
    archive = results / "omega-atlas-v1.json"
    archive.write_bytes(b'{"published": true}')
    alias = tmp_path / "other-name.jsonl"
    os.link(archive, alias)
    new = tmp_path / "fresh.json"
    monkeypatch.setattr(atlas.httpx, "Client", lambda **kwargs: pytest.fail("accès avant validation"))
    for path in (archive, alias):
        with pytest.raises(FileExistsError):
            atlas.main(["--out", str(path)])
        with pytest.raises(ValueError, match="archive"):
            atlas.main(["--out", str(new), "--trials", str(path)])
    with pytest.raises(ValueError, match="distinctes"):
        atlas.main(["--out", str(new), "--trials", str(new)])
    assert archive.read_bytes() == b'{"published": true}' and not new.exists()


def test_replay_identifies_inputs_dependency_and_never_replaces_an_existing_result(tmp_path):
    manifest = {"symbol": "BTCUSDT", "files": [["2020-01", "a" * 64]]}
    sources = atlas.rejeu.provenance("atlas", "historical-source", {"BTCUSDT": (None, manifest)}, ["signaux"])
    assert sources["archives_crypto"]["BTCUSDT"] == manifest
    assert sources["code_sha256"]["signaux"] == hashlib.sha256(Path(atlas.signaux.__file__).read_bytes()).hexdigest()
    original = atlas.rejeu.RESULTS / sources["archive_v1"]["nom"]
    assert sources["archive_v1"]["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    output = tmp_path / "new.json"
    digest = atlas.rejeu.write_result(output, {"provenance": sources})
    assert json.loads(output.read_bytes())["provenance"] == sources
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        atlas.rejeu.write_result(output, {"second": True})
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest


def test_costs_fingerprint_is_taken_from_the_bytes_actually_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas, "RESULTS", tmp_path)
    source = tmp_path / "frais-dates-v1.json"
    source.write_bytes(b'{"bareme":{"fee":1}}')
    hashes = {}
    assert atlas.costs(hashes)["frais_dates"] == {"fee": 1}
    assert hashes[source.name] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert hashes["ecart-ouverture-v1.json"] is None
