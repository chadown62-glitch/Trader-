"""COST OPTIMIZER — plan de mesure enregistré, journal, mesures publiées et contrat figé (cohérence et immuabilité)."""
import json
import shutil
from pathlib import Path

import pytest

from sentinelle.alpha.lab import costs as labcosts
from sentinelle.alpha.shadow.contract import sha256
from sentinelle.alpha.shadow.protocol import read_journal
from sentinelle.costs import plan
from tools import cost_optimizer

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "research" / "cost-optimizer-v1"
MEASURES = ROOT / "docs" / "validation" / "mesures" / "cost-optimizer-v1"


def amended_files() -> set[str]:
    return {f for ln in read_journal(DIR / "journal.jsonl") if ln["kind"] == "code_amendment" for f in ln["body"]["files"]}


def test_measurement_plan_is_intact_registered_first_and_its_code_unchanged_or_declared():
    p, digest = plan.load(DIR)
    journal = read_journal(DIR / "journal.jsonl")
    assert journal[0]["kind"] == "measurement_plan_registered" and journal[0]["body"]["plan_sha256"] == digest
    drift = set(plan.code_drift(p, ROOT))
    assert drift <= amended_files(), f"code modifié sans déclaration au journal : {sorted(drift - amended_files())}"
    for ln in journal:
        if ln["kind"] == "code_amendment":
            assert ln["body"]["reason"] and ln["body"]["effect"]  # toute correction est motivée et son effet décrit


def test_plan_structure_follows_its_own_rules():
    p, _ = plan.load(DIR)
    assert p["plan_id"] == plan.PLAN_ID and p["seed"] == plan.SEED and p["window"] == list(plan.WINDOW) and p["can_trade"] is False
    assert len(p["samples"]) == 1252 and len(p["files"]) == 623 and p["drawn"] == {"others_usdt": 150, "memecoin_usdt": 80}
    classes = {s["class"] for s in p["samples"]}
    assert classes == {"majors_usdt", "others_usdt", "memecoin_usdt", "eur_direct", "fx_eurusdt"}
    for s in p["samples"]:
        assert s["end_ms"] - s["start_ms"] == plan.BEFORE_MS + plan.AFTER_MS and s["start_ms"] < s["t_ms"] < s["end_ms"]
        assert s["split"] == ("estimation" if s["day"] < plan.SPLIT else "validation") and len(s["sizes_quote"]) == 3
        assert plan.WINDOW[0] <= s["day"] <= plan.WINDOW[1]
        if s["kind"] == "open":
            assert s["t_ms"] % plan.DAY_MS == plan.OPEN_OFFSET_MS
    assert {"half_spread_primary", "slippage_primary", "execution_confirmatory", "export_alpha_lab", "estimator_correction"} <= set(p["rules"])
    assert "aucun résultat de stratégie" in p["independence"]
    text = json.dumps(p)
    assert "alpha-factory-v2/results" not in text and "mesures/alpha-factory-v2" not in text  # aucun résultat d'ALPHA FACTORY v2


def test_plan_cannot_be_registered_twice(tmp_path, capsys):
    assert cost_optimizer.main(["register", "--archive", str(tmp_path)]) == 2
    assert "immuable" in capsys.readouterr().err
    with pytest.raises(FileExistsError):
        plan.write(DIR, {})
    work = tmp_path / "copy"
    shutil.copytree(DIR, work)
    raw = (work / "measurement-plan.json").read_text(encoding="utf-8")
    (work / "measurement-plan.json").write_text(raw.replace('"seed": 20261116', '"seed": 1'), encoding="utf-8")
    with pytest.raises(ValueError, match="empreinte"):
        plan.load(work)


def test_cli_verify_reports_the_registered_plan(capsys):
    assert cost_optimizer.main(["verify"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["intact"] is True and out["journal"][0] == "measurement_plan_registered"


def test_published_measures_come_from_the_registered_plan_without_retouch():
    p, digest = plan.load(DIR)
    m = json.loads((MEASURES / "measures.json").read_text(encoding="utf-8"))
    measured = [ln for ln in read_journal(DIR / "journal.jsonl") if ln["kind"] == "measured"]
    assert measured and m["plan_sha256"] == digest and sha256(m) == measured[-1]["body"]["measures_sha256"]
    assert m["collection"]["planned"] == len(p["samples"]) and m["collection"]["checksum_failed"] == 0


def test_frozen_contract_is_intact_and_its_export_is_readable_by_alpha_factory_v2():
    lines = (DIR / "cost-contract-v1.sha256").read_text(encoding="utf-8").split("\n")
    hashes = {ln.split("  ")[1]: ln.split("  ")[0] for ln in lines if ln}
    for name, h in hashes.items():
        assert sha256((DIR / name).read_bytes()) == h, name
    c = json.loads((DIR / "cost-contract-v1.json").read_text(encoding="utf-8"))
    _, digest = plan.load(DIR)
    m = json.loads((MEASURES / "measures.json").read_text(encoding="utf-8"))
    assert c["plan_sha256"] == digest and c["measures_sha256"] == sha256(m) and c["can_trade"] is False and c["status"] == "PROPOSED_FROZEN"
    frozen = [ln for ln in read_journal(DIR / "journal.jsonl") if ln["kind"] == "contract_frozen"]
    assert frozen and frozen[-1]["body"]["cost-contract-v1.json"] == hashes["cost-contract-v1.json"]
    hyp = labcosts.load(DIR / "alpha-lab-cost-hypotheses-proposal.json")
    assert hyp.primary == "mesure" and "PROPOSÉ" in hyp.status
    assert hyp.scenario(hyp.stress).round_trip("X") >= hyp.scenario(hyp.primary).round_trip("X")
    for v in c["classes"].values():
        assert v["half_spread"]["level"] == "ESTIMATED" and v["fee_taker"]["level"] in ("HYPOTHETICAL", "DECLARED")
