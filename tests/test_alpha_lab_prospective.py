"""Mission 55 — observation prospective de mom_ts_28 : plan séquentiel, journal, refus (données SYNTHÉTIQUES ; aucun réseau)."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.lab import costs, diagnostics, prospective as P, registry
from tools import alpha_lab_fetch as F
from tools import alpha_prospective as AP

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / registry.DIRECTORY
HYP = costs.load(LAB / registry.COST_FILE)
RULES = {"BTC": {"symbol": "BTCUSDT", "step": 1e-5, "min_notional": 5.0}, "ETH": {"symbol": "ETHUSDT", "step": 1e-4, "min_notional": 5.0}}
DAY = 86_400_000
D0 = int(np.datetime64("2026-01-01", "D").astype(int))
HISTORY = {"mu_all": 0.001, "sigma_all": 0.016, "mu_p2": 0.0005, "sigma_p2": 0.012}


def ms(date: str) -> int:
    return int(np.datetime64(date, "D").astype(int)) * DAY


def series(until: str, *, seed=1, drift=0.0, crash_on: str | None = None) -> dict:
    n = int(np.datetime64(until, "D").astype(int)) - D0 + 1
    rng = np.random.default_rng(seed)
    out = {}
    for k, (sym, px) in enumerate((("BTCUSDT", 60_000.0), ("ETHUSDT", 3_000.0))):
        r = rng.normal(drift, 0.02, n)
        if crash_on:
            i = int(np.datetime64(crash_on, "D").astype(int)) - D0
            r[i:i + 3] = -0.45
        out[sym] = px * np.cumprod(1 + r)
    return out


def klines_get(prices: dict):
    def get(path, params):
        c = prices[params["symbol"]]
        rows = []
        for i, px in enumerate(c):
            ts = (D0 + i) * DAY
            if params["startTime"] <= ts <= params["endTime"]:
                o = c[i - 1] if i else px
                rows.append([ts, f"{o:.8f}", f"{max(o, px) * 1.001:.8f}", f"{min(o, px) * 0.999:.8f}", f"{px:.8f}", "1.0", ts + DAY - 1,
                             "1000000000.0", 10, "0", "0", "0"])
        return json.dumps(rows[:1000]).encode()
    return get


@pytest.fixture(scope="module")
def protocol_template():
    plan, _, _ = registry.load_plan(LAB)
    return plan


def world(tmp_path, monkeypatch, plan, prices, *, end: str):
    obs = tmp_path / "obs"
    AP.fetch(obs, ms(end) + DAY, klines_get(prices))
    manifest = {"end_ms": ms(P.CONFIRMATORY_END) + DAY, "symbols": {}}
    for sym in P.SYMBOLS.values():
        rows = [r for r in P._rows((obs / "klines" / f"{sym}.csv.gz").read_bytes()) if int(r[0]) + DAY <= manifest["end_ms"]]
        manifest["symbols"][sym] = {"sha256": hashlib.sha256(F.encode_rows(rows)).hexdigest()}
    mpath = tmp_path / "manifest.json"
    mpath.write_bytes(json.dumps(manifest).encode())
    monkeypatch.setattr(AP, "MANIFEST", mpath)
    plan = json.loads(json.dumps(plan))
    plan["data"]["archive_manifest_sha256"] = hashlib.sha256(mpath.read_bytes()).hexdigest()
    proto = P.build_protocol(root=ROOT, plan=plan, plan_sha256="a" * 64, results_sha256="b" * 64, hyp=HYP, rules=RULES, history=HISTORY,
                             registered_at="test", registered_at_ms=1)
    pdir = tmp_path / "protocol"
    P.write_protocol(pdir, proto)
    return obs, pdir, proto


def test_the_sequential_boundaries_spend_five_percent_and_allow_an_early_stop_only_for_a_large_lead():
    b = P.boundaries()
    assert len(b) == P.MAX_LOOKS and all(x > y for x, y in zip(b, b[1:]))
    assert b[0] > 5 and 1.6 < b[-1] < 1.8  # O'Brien-Fleming : très exigeant au début, proche de 1,645 à la fin
    null = P.operating_characteristics(b, 0.0, 0.016)
    assert abs(null[-1] - 0.05) < 0.006  # risque total d'une fausse confirmation ≈ 5 %
    strong = P.operating_characteristics(b, 0.001, 0.016)
    assert strong == sorted(strong) and strong[-1] > 0.85


def test_block_z_is_a_mean_over_its_bootstrap_standard_error():
    x = np.random.default_rng(3).normal(0.001, 0.01, 2000)
    z = P.block_z(x)
    assert 2 < z < 7 and np.isnan(P.block_z(x[:5]))


def test_registration_must_precede_the_first_decision(tmp_path, capsys):
    late = str(ms(P.FIRST_DECISION))
    assert AP.main(["register", "--archive", str(tmp_path), "--dir", str(tmp_path / "p"), "--now-ms", late]) == 2
    assert "précéder la première décision" in capsys.readouterr().err and not (tmp_path / "p").exists()


def test_nothing_is_evaluated_before_the_start(tmp_path, monkeypatch, protocol_template):
    obs, pdir, _ = world(tmp_path, monkeypatch, protocol_template, series("2026-11-20"), end="2026-11-20")
    code, out = AP.observe(pdir, obs, write=True)
    assert code == 0 and out["status"] == "PAS_COMMENCÉ" and out["new_decisions"] == []
    assert [x["kind"] for x in P.journal(pdir)] == []  # rien à inscrire


def test_decisions_are_weekly_recorded_once_and_a_contradiction_is_refused(tmp_path, monkeypatch, protocol_template):
    obs, pdir, _ = world(tmp_path, monkeypatch, protocol_template, series("2027-01-31", seed=4), end="2027-01-31")
    code, out = AP.observe(pdir, obs, write=True)
    days = [d for d, _ in out["new_decisions"]]
    assert code == 0 and days[0] == P.FIRST_DECISION and len(days) == 11
    assert all((np.datetime64(d, "D").astype(int) + 3) % 7 == 6 for d in days)  # tous des dimanches
    assert out["status"] == "EN_COURS" and out["looks"] == [] and out["next_look_on"] == "2027-11-22"
    assert "sans valeur de décision" in out["descriptive"]["note"]
    code, again = AP.observe(pdir, obs, write=True)
    assert code == 0 and again["new_decisions"] == [] and [x["kind"] for x in P.journal(pdir)] == ["observation"]
    day = out["new_decisions"][0][0]
    P.record(pdir, "observation", {"decisions": [[day, {"BTC": 0.123}]]}, 5)  # une inscription contradictoire (falsifiée)
    code, refused = AP.observe(pdir, obs, write=True)
    assert code == 1 and "décision différente" in refused["refused"][0]


def test_a_revised_history_is_refused(tmp_path, monkeypatch, protocol_template):
    prices = series("2026-12-31", seed=5)
    obs, pdir, _ = world(tmp_path, monkeypatch, protocol_template, prices, end="2026-12-31")
    prices["BTCUSDT"][100] *= 1.01  # une bougie de l'historique déjà consulté change
    AP.fetch(obs, ms("2026-12-31") + DAY, klines_get(prices))
    code, out = AP.observe(pdir, obs, write=True)
    assert code == 1 and "ne reproduit pas l'archive" in out["refused"][0] and P.journal(pdir) == []


def test_annual_looks_and_the_harm_stop(tmp_path, monkeypatch, protocol_template):
    obs, pdir, proto = world(tmp_path, monkeypatch, protocol_template, series("2028-01-15", seed=6, drift=0.002), end="2028-01-15")
    _, out = AP.observe(pdir, obs, write=False)
    assert len(out["looks"]) == 1 and out["looks"][0]["boundary"] == proto["analysis"]["boundaries_z"][0]
    assert out["status"] in ("EN_COURS", "CONFIRMÉ_PROSPECTIVEMENT") and P.journal(pdir) == []  # « status » n'écrit rien
    obs2, pdir2, _ = world(tmp_path / "crash", monkeypatch, protocol_template,
                           series("2027-06-01", seed=6, drift=0.004, crash_on="2027-03-02"), end="2027-06-01")
    _, crash = AP.observe(pdir2, obs2, write=False)
    assert crash["status"] == "ARRÊT_RISQUE" and crash["descriptive"]["max_drawdown"] > P.HARM_DRAWDOWN


def test_the_protocol_is_write_once_and_detects_changes(tmp_path, monkeypatch, protocol_template):
    _, pdir, proto = world(tmp_path, monkeypatch, protocol_template, series("2026-11-20"), end="2026-11-20")
    with pytest.raises(P.ProspectiveError, match="immuable"):
        P.write_protocol(pdir, proto)
    assert P.load_protocol(pdir, root=ROOT, hyp=HYP)[2] == []
    text = (pdir / "protocol.json").read_text(encoding="utf-8").replace('"max_looks": 9', '"max_looks": 3')
    (pdir / "protocol.json").write_text(text, encoding="utf-8")
    assert "le protocole ne correspond plus à son empreinte" in P.load_protocol(pdir)[2]
    assert proto["can_trade"] is False and proto["promotion_allowed"] is False and proto["no_permanent_service"] is True


def test_registered_protocol_state():
    d = ROOT / P.DIRECTORY
    if not (d / "protocol.json").exists():
        pytest.skip("protocole non encore enregistré")
    proto, digest, problems = P.load_protocol(d, root=ROOT, hyp=HYP)
    assert problems == [] and proto["registered_at_ms"] < ms(P.FIRST_DECISION)
    assert proto["source"]["plan_sha256"] == registry.load_plan(LAB)[1]
    res = ROOT / "docs" / "validation" / "mesures" / "alpha-factory-v2" / "results.json"
    assert proto["source"]["results_sha256"] == hashlib.sha256(res.read_bytes()).hexdigest()
    kinds = [x["kind"] for x in P.journal(d)]
    assert kinds[0] == "protocol_registered" and set(kinds) <= {"protocol_registered", "observation"}
    assert proto["analysis"]["boundaries_z"] == P.boundaries()


def test_published_diagnostic_is_reproduced_when_the_archive_is_available():
    archive = ROOT / "data" / "alpha-lab" / "archive-20261113"
    out = ROOT / "docs" / "validation" / "mesures" / "alpha-factory-v2-prospective"
    if not archive.exists() or not (out / "diagnostic.json").exists():
        pytest.skip("archive locale absente (elle se retélécharge : voir docs/coordination/development-campaign.md)")
    from sentinelle.alpha.lab import data
    rep = diagnostics.run(data.load_archive(archive), data.load_rules(archive)[0], HYP,
                          json.loads((ROOT / "docs" / "validation" / "mesures" / "alpha-factory-v2" / "results.json").read_text(encoding="utf-8")))
    assert json.loads(json.dumps(rep)) == json.loads((out / "diagnostic.json").read_text(encoding="utf-8"))
    assert (out / "diagnostic.md").read_text(encoding="utf-8") == diagnostics.markdown(rep) + "\n"
