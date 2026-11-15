"""ALPHA FACTORY v2 — banc complet : témoins négatif et positif, règle de décision, préenregistrement, isolation."""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from sentinelle.alpha.lab import CAN_TRADE, LEVERAGE, LONG_ONLY, STATUS, costs, registry
from sentinelle.alpha.lab.experiment import FLAGS, NOT_RETAINED, REASONS, RETAINED, benchmark_returns, run_experiment
from sentinelle.alpha.lab.simulate import Features
from sentinelle.alpha.lab.strategies import CANDIDATES, REFERENCES
from sentinelle.alpha.lab.synthetic import synthetic_panel
from sentinelle.alpha.shadow import protocol
from tests.test_alpha_lab import END_MS, fake_exchange
from tools import alpha_lab, alpha_lab_fetch

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / registry.DIRECTORY
MEASURES = ROOT / "docs" / "validation" / "mesures" / "alpha-factory-v2"
HYP = costs.load(DIR / registry.COST_FILE)
START = 430


def plan_for(panel, *, reps: int = 300) -> dict:
    plan = registry.build_plan(root=ROOT, data=panel.provenance, hyp=HYP, other_pairs=[["A03", "A04"], ["A07", "A08"]],
                               registered_at="test", registered_at_ms=1)
    n = len(panel)
    plan["window"] = {"start": panel.date(START), "split": panel.date(START + (n - START) // 2), "end": panel.date(n - 1)}
    plan["analysis"]["familywise"]["reps"] = reps
    plan["analysis"]["bootstrap_reps"] = 300
    plan["generalization"]["exclude_already_studied"] = ["BTC", "ETH", "A09", "A10"]
    return plan


@pytest.fixture(scope="module")
def noise():
    out = []
    for seed in (3, 4):
        panel = synthetic_panel(seed, kind="noise", days=1500)
        out.append(run_experiment(panel, plan_for(panel), HYP))
    return out


@pytest.fixture(scope="module")
def momentum():
    panel = synthetic_panel(5, kind="momentum", days=1500, edge=1.5)
    return panel, run_experiment(panel, plan_for(panel), HYP)


# ---------------------------------------------------------------------------
# Témoins : rien sur le bruit, quelque chose quand un effet existe
# ---------------------------------------------------------------------------


def test_p0_nothing_is_retained_on_markets_without_any_edge(noise):
    for rep in noise:
        assert rep["summary"]["retained"] == [] and "aucun avantage net démontré" in rep["summary"]["conclusion"]
        assert all(c["verdict"] == NOT_RETAINED and c["reasons"] for c in rep["candidates"].values())
        assert rep["references"]["cash"]["net_pnl"] == 0.0 and rep["status"] == "EXPLORATORY" and rep["can_trade"] is False
    lucky = [c for c in noise[0]["candidates"].values() if c["net"]["net_pnl"] > 0]
    assert lucky and all("NOT_SIGNIFICANT_ABSOLUTE" in c["reasons"] for c in lucky)  # un gain dû au hasard ne suffit pas


def test_the_lab_can_say_yes_when_a_real_momentum_effect_is_hidden(momentum):
    _, rep = momentum
    best = rep["candidates"]["mom_xs_28_top3"]
    assert best["verdict"] == RETAINED and all(best["checks"].values()) and best["reasons"] == []
    assert best["absolute"]["p_adjusted"] < 0.05 and best["relative"]["p_adjusted"] < 0.05 and best["costs"]["stressed_pnl"] > 0
    assert best["periods"]["P1"]["return"] > 0 and best["periods"]["P2"]["return"] > 0
    assert "mom_xs_28_top3" in rep["summary"]["retained"] and "aucune preuve de rentabilité" in rep["summary"]["conclusion"]
    for name in ("trend_sma_200", "crypto_weekday_btc", "meanrev_xs_7_bottom3"):  # aucun effet caché pour ces règles : non retenues
        assert rep["candidates"][name]["verdict"] == NOT_RETAINED, name
    assert "mom_xs_28_top3" in rep["generalization"]["broad_positive"]  # l'effet tient aussi hors des actifs « déjà étudiés »


def test_every_candidate_gets_measured_reasons_and_the_same_tests(noise, momentum):
    for rep in noise + [momentum[1]]:
        assert list(rep["candidates"]) == [s.name for s in CANDIDATES] == rep["tests"]["names"] and rep["tests"]["candidates"] == 15
        assert rep["tests"]["absolute"]["tests"] == rep["tests"]["relative"]["tests"] == 15
        for name, c in rep["candidates"].items():
            assert set(c["reasons"]) <= set(REASONS) and set(c["flags"]) <= set(FLAGS) and "UNKNOWN" not in c["reasons"], name
            assert (c["verdict"] == RETAINED) == all(c["checks"].values()) == (c["reasons"] == [])
            assert 0 <= c["relative"]["exposure"] <= 1 + 1e-9 and c["net"]["max_drawdown"] >= 0
            assert set(c["periods"]) == {"P1", "P2"} and c["costs"]["fees"] >= 0
            gross_bad = c["costs"]["gross_pnl"] <= 0
            assert ("NO_GROSS_EDGE" in c["reasons"]) == gross_bad, name
            if c["net"]["net_pnl"] <= 0 and not gross_bad:
                assert "COSTS_EXCEED_GROSS_EDGE" in c["reasons"], name
            if c["net"]["max_drawdown"] > 0.10:
                assert "DRAWDOWN_ABOVE_RISK_LIMIT" in c["flags"] and c["risk"]["scale"] < 1


def test_report_separates_candidates_references_and_market_benchmarks(noise):
    rep = noise[0]
    assert set(rep["references"]) == {s.name for s in REFERENCES} and not set(rep["references"]) & set(rep["candidates"])
    assert set(rep["benchmarks"]) == {"btc", "majors", "market"}
    g = rep["generalization"]
    assert set(g["broad"]) == {s.name for s in CANDIDATES if s.benchmark == "market"}
    assert set(g["other_pairs"]) == {s.name for s in CANDIDATES if s.benchmark == "majors"}
    assert all(set(rows) == {"A03+A04", "A07+A08"} for rows in g["other_pairs"].values())
    assert rep["window"]["p1_days"] + rep["window"]["p2_days"] == rep["window"]["days"] == 1500 - START


def test_market_benchmark_uses_yesterdays_universe_only():
    panel = synthetic_panel(3, kind="noise", days=700)
    feats = Features(panel)
    full = benchmark_returns(panel, feats, 300, 600, majors=("BTC", "ETH"))
    short = panel.view(450)
    part = benchmark_returns(short, Features(short), 300, 450, majors=("BTC", "ETH"))
    for name in ("btc", "majors", "market"):
        assert np.array_equal(full[name][:151], part[name])  # ajouter le futur ne change pas le passé
    j = panel.col("BTC")
    assert full["btc"][0] == pytest.approx(panel.close[300, j] / panel.close[299, j] - 1)


def test_the_experiment_is_deterministic(noise):
    panel = synthetic_panel(3, kind="noise", days=1500)
    again = run_experiment(panel, plan_for(panel), HYP)
    assert json.dumps(again, sort_keys=True) == json.dumps(noise[0], sort_keys=True)


def test_the_plan_is_enforced_not_a_suggestion(momentum):
    panel, _ = momentum
    plan = plan_for(panel)
    for change in (lambda p: p["candidates"].append("mom_xs_99"), lambda p: p["candidates"].append("hold_btc"),
                   lambda p: p["candidate_definitions"]["mom_xs_28_top3"]["params"].update(lookback=14),
                   lambda p: p["window"].update(split=p["window"]["start"])):
        other = json.loads(json.dumps(plan))
        change(other)
        with pytest.raises((ValueError, KeyError)):
            run_experiment(panel, other, HYP)
    with pytest.raises(costs.CostError):
        run_experiment(panel, plan, costs.CostHypotheses("x", "0", HYP.scenarios, "gratuit", "tendu", "brut", "t", "t"))


# ---------------------------------------------------------------------------
# Préenregistrement et journal
# ---------------------------------------------------------------------------


def test_plan_is_write_once_and_detects_any_change(tmp_path):
    panel = synthetic_panel(3, kind="noise", days=600)
    work = tmp_path / "lab"
    work.mkdir()
    shutil.copy(DIR / registry.COST_FILE, work / registry.COST_FILE)
    hyp = costs.load(work / registry.COST_FILE)
    plan = registry.build_plan(root=ROOT, data={"archive_manifest_sha256": "a" * 64}, hyp=hyp, other_pairs=[["X", "Y"]], registered_at="t", registered_at_ms=5)
    assert plan["candidates"] == [s.name for s in CANDIDATES] and plan["trials"]["parameter_sets_per_candidate"] == 1
    assert plan["can_trade"] is False and plan["promotion_allowed"] is False and plan["long_only"] is True and plan["leverage"] == 1.0
    digest = registry.write_plan(work, plan)
    with pytest.raises(registry.RegistryError, match="immuable"):
        registry.write_plan(work, plan)
    assert registry.load_plan(work, root=ROOT, hyp=hyp, data={"archive_manifest_sha256": "a" * 64}) == (plan, digest, [])
    assert registry.load_plan(work, data={"archive_manifest_sha256": "b" * 64})[2] == ["l'archive de données n'est pas celle du plan"]
    cost_file = work / registry.COST_FILE
    cost_file.write_text(cost_file.read_text(encoding="utf-8").replace('"fee_bps": 10, "slippage_bps": 5, "half_spread_bps": {"majors": 2, "others": 8}',
                                                                       '"fee_bps": 1, "slippage_bps": 5, "half_spread_bps": {"majors": 2, "others": 8}'), encoding="utf-8")
    assert registry.load_plan(work, hyp=costs.load(cost_file))[2] == ["les hypothèses de coûts ont changé depuis l'enregistrement"]
    text = (work / "preregistration.json").read_text(encoding="utf-8")
    (work / "preregistration.json").write_text(text.replace('"alpha": 0.05', '"alpha": 0.5'), encoding="utf-8")
    assert registry.load_plan(work)[2] == ["le plan ne correspond plus à son empreinte"]
    root = tmp_path / "repo"
    for name in registry.IMPLEMENTATION:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, root / name)
    (work / "preregistration.json").write_text(text, encoding="utf-8")
    assert registry.load_plan(work, root=root)[2] == []
    target = root / "sentinelle/alpha/lab/strategies.py"
    target.write_text(target.read_text(encoding="utf-8").replace('{"lookback": 28, "n": 3}', '{"lookback": 21, "n": 3}'), encoding="utf-8")
    assert registry.load_plan(work, root=root)[2] == ["code modifié depuis l'enregistrement : sentinelle/alpha/lab/strategies.py"]
    registry.record(work, "plan_registered", {"plan_sha256": digest}, 10)
    registry.record(work, "experiment_run", {"retained": []}, 20)
    assert [ln["kind"] for ln in registry.journal(work)] == ["plan_registered", "experiment_run"] and len(panel) == 600


def test_registered_plan_and_published_results_are_intact_and_consistent():
    plan, digest, problems = registry.load_plan(DIR, root=ROOT, hyp=HYP)
    assert problems == [], problems  # le code du laboratoire est celui qui a été préenregistré
    assert plan["plan_id"] == registry.PLAN_ID and plan["status"] == "EXPLORATORY" and plan["window"] == registry.WINDOW
    assert plan["candidates"] == [s.name for s in CANDIDATES] and plan["candidate_definitions"] == {s.name: s.describe() for s in CANDIDATES}
    assert plan["trials"]["candidates"] == 15 and plan["costs"]["primary"] == "prudent" and plan["analysis"]["risk_limit"] == 0.10
    assert plan["generalization"]["exclude_already_studied"] == list(registry.ALREADY_STUDIED) and len(plan["generalization"]["other_pairs"]) == 3
    journal = registry.journal(DIR)
    assert [ln["kind"] for ln in journal][:2] == ["plan_registered", "experiment_run"] and journal[0]["body"]["plan_sha256"] == digest
    assert journal[0]["at_ms"] < journal[1]["at_ms"]  # le plan a été enregistré avant l'exécution
    results = json.loads((MEASURES / "results.json").read_text(encoding="utf-8"))
    from sentinelle.alpha.shadow.contract import sha256
    assert sha256(results) == journal[1]["body"]["results_sha256"]  # les résultats publiés sont ceux de l'exécution, sans retouche
    assert results["plan_sha256"] == digest and results["data"]["archive_manifest_sha256"] == plan["data"]["archive_manifest_sha256"]
    assert list(results["candidates"]) == plan["candidates"] and results["status"] == "EXPLORATORY" and results["can_trade"] is False
    assert results["summary"]["retained"] == journal[1]["body"]["retained"]
    assert all((c["verdict"] == RETAINED) == all(c["checks"].values()) for c in results["candidates"].values())


def test_the_frozen_r1_candidate_and_protocol_of_pr48_are_untouched():
    manifest, _, problems = protocol.load_manifest(ROOT / protocol.DIRECTORY, root=ROOT)
    assert problems == [] and manifest["status"] == "PREREGISTERED_NOT_STARTED"
    assert manifest["candidate"]["model_sha256"] == "33c765de35987e5a4ddfeb5ce8ee1ece65126878cc02ec80a00a135148123911"
    assert [ln["kind"] for ln in protocol.read_journal(ROOT / protocol.DIRECTORY / "trials.jsonl")] == ["development_freeze", "protocol_registered"]


def test_lab_declares_itself_exploratory_long_only_and_unlevered():
    assert CAN_TRADE is False and LONG_ONLY is True and LEVERAGE == 1.0 and STATUS == "EXPLORATORY"


# ---------------------------------------------------------------------------
# Commande
# ---------------------------------------------------------------------------


def test_cli_lists_candidates_verifies_and_reports(capsys):
    assert alpha_lab.main(["candidates"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["candidates"]) == 15 and len(out["references"]) == 4 and all(c["source"] for c in out["candidates"])
    assert alpha_lab.main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["intact"] is True
    assert alpha_lab.main(["report", "--input", str(MEASURES)]) == 0
    text = capsys.readouterr().out
    assert "Conclusion du banc" in text and "mom_xs_28_top3" in text and "Références simples" in text


def test_cli_refuses_to_register_twice_or_to_run_on_other_data(tmp_path, capsys):
    assert alpha_lab.main(["register", "--archive", str(tmp_path)]) == 2
    assert "immuable" in capsys.readouterr().err
    alpha_lab_fetch.run(tmp_path / "archive", END_MS, fake_exchange([]), max_requests=50)
    assert alpha_lab.main(["run", "--archive", str(tmp_path / "archive"), "--output", str(tmp_path / "out")]) == 1
    assert "archive de données n'est pas celle du plan" in capsys.readouterr().err and not (tmp_path / "out").exists()
    assert alpha_lab.main(["universe", "--archive", str(tmp_path / "archive"), "--dates", "2018-02-05"]) == 0
    assert json.loads(capsys.readouterr().out)["quality"]["columns"] == 3


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

FORBIDDEN = ("sentinelle.execution", "sentinelle.services", "sentinelle.api", "sentinelle.notify", "sentinelle.market", "sentinelle.storage",
             "sentinelle.news", "sentinelle.agent", "sentinelle.risk", "sentinelle.backtest", "sentinelle.preflight", "tools.supervise",
             "tools.observe", "tools.public_capture", "tools.collect_public", "tools.alpha_lab_fetch", "ccxt", "httpx", "requests", "socket",
             "urllib", "aiohttp", "subprocess", "telethon", "sqlite3", "asyncio", "threading", "multiprocessing", "sklearn", "joblib", "pandas")
LAB_FILES = sorted((ROOT / "sentinelle" / "alpha" / "lab").glob("*.py")) + [ROOT / "tools" / "alpha_lab.py"]


def _imports(path: Path) -> list[str]:
    names = []
    package = {3: "sentinelle.", 2: "sentinelle.alpha.", 1: "sentinelle.alpha.lab."}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = (package[node.level] if node.level else "") + (node.module or "")
            names += [base] + [f"{base.rstrip('.')}.{a.name}" for a in node.names]
    return names


@pytest.mark.parametrize("path", LAB_FILES, ids=lambda p: p.name)
def test_lab_modules_cannot_reach_a_broker_the_network_or_the_application(path):
    for name in _imports(path):
        assert not any(name == f or name.startswith(f + ".") for f in FORBIDDEN), f"{path.name} importe {name}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    idents = {n.attr if isinstance(n, ast.Attribute) else n.id for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
    assert not idents & {"place_order", "create_order", "submit_order", "cancel_order", "fetch_balance", "withdraw", "sleep"}, path.name


def test_the_download_tool_is_separate_public_and_never_touches_the_application():
    names = _imports(ROOT / "tools" / "alpha_lab_fetch.py")
    assert not any(n.startswith(("sentinelle", "tools.", "ccxt")) for n in names)  # aucun accès au courtier ni au portefeuille
    source = (ROOT / "tools" / "alpha_lab_fetch.py").read_text(encoding="utf-8")
    assert "data-api.binance.vision" in source and "apiKey" not in source and "secret" not in source.lower()
    assert set(n.split(".")[0] for n in names) <= {"__future__", "argparse", "gzip", "hashlib", "json", "sys", "time", "datetime", "pathlib", "requests"}


def test_running_the_lab_loads_no_broker_network_or_learning_module():
    code = ("import sys, sentinelle.alpha.lab.experiment, sentinelle.alpha.lab.registry, tools.alpha_lab;"
            "bad=sorted(m for m in sys.modules if m.split('.')[0] in ('sklearn','pandas','joblib','ccxt','httpx','requests','sqlite3','fastapi')"
            " or m.startswith(('sentinelle.execution.paper','sentinelle.execution.positions','sentinelle.execution.live','sentinelle.services',"
            "'sentinelle.api','sentinelle.market','sentinelle.storage','sentinelle.ai.model','tools.alpha_lab_fetch')));print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
