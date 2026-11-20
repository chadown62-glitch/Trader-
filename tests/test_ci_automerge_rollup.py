"""CI : décision du job « Request squash merge » sur le `statusCheckRollup` de la PR.

Le filtre jq `ROLLUP_VERDICT` est lu dans `.github/workflows/ci-automerge.yml` et exécuté
sur des rollups au format de `gh pr view --json statusCheckRollup`. Un passage remplacé
(annulé par le groupe de concurrence) ne bloque plus la fusion ; un vrai échec, si.
Hors ligne : aucun appel à GitHub. Ignoré si `jq` est absent du poste.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-automerge.yml"
OWNER_REPO = "https://github.com/chadown62-glitch/Trader-"
WF = "Sentinelle CI"
OLD, NEW = 44791318763, 44794552600  # passage annulé puis passage vert de la PR #76 (20/11/2026)
FAILED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STALE", "STARTUP_FAILURE"}

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq absent (présent sur les runners GitHub)")


def rollup_filter() -> str:
    """Bloc littéral YAML `ROLLUP_VERDICT: |`, sans dépendre de PyYAML."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if re.fullmatch(r"\s*ROLLUP_VERDICT: \|", line))
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    width = min(len(line) - len(line.lstrip()) for line in body if line.strip())
    return "\n".join(line[width:] for line in body).strip() + "\n"


def check(name, run, job, conclusion, status="COMPLETED", workflow=WF, minute=0):
    return {"__typename": "CheckRun", "name": name, "workflowName": workflow, "status": status,
            "conclusion": conclusion, "startedAt": f"2026-11-20T18:{minute:02d}:00Z",
            "completedAt": f"2026-11-20T18:{minute + 1:02d}:00Z" if status == "COMPLETED" else "0001-01-01T00:00:00Z",
            "detailsUrl": f"{OWNER_REPO}/actions/runs/{run}/job/{job}"}


def run_checks(run, first_job, conclusions, minute=0):
    names = ["pytest (windows-latest)", "pytest (ubuntu-latest)", "Vue cockpit (Chromium)"]
    return [check(name, run, first_job + i, c, minute=minute) for i, (name, c) in enumerate(zip(names, conclusions))]


def merge_job(run, job, conclusion="", status="IN_PROGRESS"):
    return check("Request squash merge", run, job, conclusion, status=status, minute=30)


def verdict(tmp_path, rollup, run=NEW):
    program = tmp_path / "rollup.jq"
    program.write_text(rollup_filter(), encoding="utf-8")
    done = subprocess.run(["jq", "-r", "--arg", "run", f"/actions/runs/{run}/", "-f", str(program)],
                          input=json.dumps({"statusCheckRollup": rollup}), capture_output=True, text=True,
                          encoding="utf-8", check=True)
    return done.stdout.strip()


@needs_jq
def test_pr76_cancelled_superseded_run_no_longer_blocks_the_merge(tmp_path):
    cancelled = run_checks(OLD, 136480000000, ["CANCELLED"] * 3) + [merge_job(OLD, 136480000010, "CANCELLED", "COMPLETED")]
    green = run_checks(NEW, 136490561870, ["SUCCESS"] * 3, minute=10) + [merge_job(NEW, 136490600000)]
    assert any(c["conclusion"] in FAILED for c in cancelled)  # l'ancien filtre s'arrêtait ici
    assert verdict(tmp_path, cancelled + green) == "success"
    assert verdict(tmp_path, green + cancelled) == "success"  # l'ordre du rollup est indifférent


@needs_jq
def test_real_failure_in_the_latest_run_still_blocks(tmp_path):
    cancelled = run_checks(OLD, 136480000000, ["CANCELLED"] * 3)
    red = run_checks(NEW, 136490561870, ["FAILURE", "SUCCESS", "SUCCESS"], minute=10) + [merge_job(NEW, 136490600000)]
    assert verdict(tmp_path, cancelled + red) == "failure"
    older_green = run_checks(OLD, 136480000000, ["SUCCESS"] * 3)
    assert verdict(tmp_path, older_green + red) == "failure"  # un ancien succès ne masque pas l'échec récent


@needs_jq
def test_latest_cancelled_check_without_replacement_still_blocks(tmp_path):
    rollup = run_checks(NEW, 136490561870, ["SUCCESS", "SUCCESS", "SUCCESS"], minute=10) + [merge_job(NEW, 136490600000)]
    rollup.append(check("lint", 1, 5, "CANCELLED", workflow="Autre flux"))
    assert verdict(tmp_path, rollup) == "failure"


@needs_jq
def test_newer_unfinished_check_is_awaited(tmp_path):
    rollup = run_checks(OLD, 136480000000, ["CANCELLED"] * 3) + [merge_job(NEW, 136490600000)]
    rollup += run_checks(NEW, 136490561870, ["SUCCESS", "SUCCESS"], minute=10)
    rollup.append(check("Vue cockpit (Chromium)", NEW, 136490561872, "", status="IN_PROGRESS", minute=10))
    assert verdict(tmp_path, rollup) == "pending"


@needs_jq
def test_this_merge_job_is_ignored_but_something_else_must_report(tmp_path):
    assert verdict(tmp_path, [merge_job(NEW, 136490600000)]) == "pending"
    assert verdict(tmp_path, []) == "pending"
    skipped_draft_run = [merge_job(OLD, 136480000010, "SKIPPED", "COMPLETED")]
    green = run_checks(NEW, 136490561870, ["SUCCESS"] * 3, minute=10) + [merge_job(NEW, 136490600000)]
    assert verdict(tmp_path, skipped_draft_run + green) == "success"


@needs_jq
def test_same_job_name_in_another_workflow_is_a_separate_check(tmp_path):
    green = run_checks(NEW, 136490561870, ["SUCCESS"] * 3, minute=10) + [merge_job(NEW, 136490600000)]
    other = check("pytest (ubuntu-latest)", 1, 999999999999, "FAILURE", workflow="Autre flux")
    assert verdict(tmp_path, green + [other]) == "failure"


@needs_jq
def test_legacy_statuses_and_external_checks_keep_their_latest_entry(tmp_path):
    green = run_checks(NEW, 136490561870, ["SUCCESS"] * 3, minute=10) + [merge_job(NEW, 136490600000)]
    status = {"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR",
              "targetUrl": "https://ci.example.invalid/1", "startedAt": "2026-11-20T18:05:00Z"}
    assert verdict(tmp_path, green + [status]) == "failure"
    assert verdict(tmp_path, green + [dict(status, state="PENDING")]) == "pending"
    # Contrôle externe sans identifiant de job : l'entrée inachevée la plus récente l'emporte.
    done = dict(check("scan", 0, 0, "SUCCESS", workflow=""), detailsUrl="https://scan.example.invalid/1")
    queued = dict(done, status="QUEUED", conclusion="", startedAt="0001-01-01T00:00:00Z",
                  completedAt="0001-01-01T00:00:00Z")
    assert verdict(tmp_path, green + [done]) == "success"
    assert verdict(tmp_path, green + [done, queued]) == "pending"


def test_merge_job_keeps_its_existing_guards():
    text = WORKFLOW.read_text(encoding="utf-8")
    merge = text[text.index("\n  merge:"):]
    for guard in ("5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18", "18b31bb901f656d6126b370f47c510775f135c57",
                  "--match-head-commit \"$HEAD_SHA\"", "github.event.pull_request.number != 54",
                  "github.event.pull_request.number != 55", '"$ROLLUP_VERDICT"'):
        assert guard in merge, guard
    assert "actions/checkout" not in merge and "pip install" not in merge and "pytest" not in merge
    assert "edited" in text.split("jobs:")[0]  # le reciblage d'une PR empilée relance la CI
