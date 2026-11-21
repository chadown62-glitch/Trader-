import importlib.util
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from sentinelle.dmh import memecoin_cloud_runner as runner
from sentinelle.dmh import memecoin_simulator as sim
from tests import test_configurable_capital as frozen
from tests.test_dmh_scanner import MINT, NOW, POOL, WSOL, b58
from tests.test_memecoin_engine import EstablishedFeed

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "memecoin-paper-sim.yml"
THIN = b58(bytes(range(1, 33)))  # pool à 1 600 $ : refusé par le filtre de liquidité, sans aucun devis
DANGER = b58(bytes(range(2, 34)))  # pool établi et revendable, mais signalé « danger » par Rugcheck : veto


def test_cli_ticks_restart_and_domain(tmp_path, monkeypatch):
    db = tmp_path / "session.sqlite"
    output = tmp_path / "report.json"
    calls = []

    def scan(client, database, bus, registry):
        calls.append(1)
        return {"errors": []}

    monkeypatch.setattr(sim, "scan_once", scan)
    args = ["--db-path", str(db), "--report-path", str(output)]
    runner.main(args + ["--ticks", "2", "--initial-capital", "100"])
    first = json.loads(output.read_text(encoding="utf-8"))
    runner.main(args)
    second = json.loads(output.read_text(encoding="utf-8"))
    assert len(calls) == 3 and second["revision"] == first["revision"] + 1
    assert second["initial_capital"] == second["final_equity"] == 100
    assert second["asset_domain"] == sim.DOMAIN and second["win_rate"] is None
    assert second["net_pnl"] == second["total_trades"] == 0 and second["paper_only"]
    assert set(second["strategies"]) == {"sniper", "momentum", "dip_hunter", "whale_tail"}
    assert "Classement par token" in output.with_suffix(".txt").read_text(encoding="utf-8")
    dbh = runner.open_session(db)
    try:
        assert len(sim.load(dbh)[0]["curve"]) == 3
        assert dbh.scalar("SELECT count(*) FROM accounts") == dbh.scalar("SELECT count(*) FROM fills") == 0
    finally:
        dbh.close()
    for n in ("0", str(runner.MAX_TICKS + 1)):
        with pytest.raises(SystemExit):
            runner.main(args + ["--ticks", n])
    assert len(calls) == 3


def test_closed_results_and_stale_rpc(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite"
    output = tmp_path / "report.json"
    db = runner.open_session(path)
    try:
        state, _ = sim.load(db)
        fills = [
            {"mint": "test", "strategy": "momentum", "side": side, "pnl": pnl}
            for realized in (3.0, -1.5, 0.0)
            for side, pnl in (("buy", 0), ("sell", realized))
        ]
        state.update(
            cash=31.5, revision=2, fills=fills, completed=[{"mint": "test", "strategy": "momentum", "return": r} for r in (0.3, -0.15, 0)]
        )
        db.set_meta(sim.KEY, json.dumps(state))
    finally:
        db.close()

    def unavailable(*args, **kwargs):
        raise runner.SourceUnavailable("Solana", "source indisponible")

    monkeypatch.setattr(sim, "scan_once", unavailable)
    result = runner.run(3, path, output)
    assert result["ticks_completed"] == 0 and len(result["source_errors"]) == 1
    assert result["win_rate"] == 1 / 3 and result["closed_trades"] == result["total_trades"] == 3
    assert result["final_equity"] == 31.5 and result["net_pnl"] == 1.5
    assert result["strategies"]["momentum"]["pnl"] == 1.5 and result["token_ranking"] == [("test", 1.5)]
    db = runner.open_session(path)
    try:
        state, _ = sim.load(db)
        state["positions"]["momentum/test"] = {"mark": 2, "basis": 2, "mint": "test", "strategy": "momentum", "pnl": 0}
        db.set_meta(sim.KEY, json.dumps(state))
    finally:
        db.close()
    latest = runner.run(1, path, output)
    assert latest["final_equity"] is None and latest["net_pnl"] is None
    assert latest["stale_marks"] == ["test"]


def test_foreign_db_is_not_adopted(tmp_path):
    path = tmp_path / "cockpit.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE accounts(id INTEGER)")
    with pytest.raises(ValueError, match="dédiée"):
        runner.run(1, path, tmp_path / "report.json")


def test_devcontainer_private_start():
    cfg = json.loads((Path(__file__).resolve().parents[1] / ".devcontainer/devcontainer.json").read_text(encoding="utf-8"))
    assert "3.12" in cfg["image"] and "requirements.txt" in cfg["postCreateCommand"]
    assert cfg["forwardPorts"] == [8000]
    assert cfg["portsAttributes"]["8000"]["visibility"] == "private"
    assert cfg["portsAttributes"]["8000"]["onAutoForward"] == "openBrowser"
    assert "python -m sentinelle --remote --port 8000 --no-browser" in cfg["postStartCommand"][-1]


@pytest.mark.parametrize(("base", "head", "expected"), frozen.FROZEN)
def test_frozen_file_manifests(base, head, expected):
    frozen.test_frozen_scientific_files(base, head, expected)


class ThreePools(EstablishedFeed):
    """Trois pools en tendance : un retenu, un trop mince (< 20 000 $), un refusé par le veto anti-arnaque."""

    def transport(self, method, url, headers, body, timeout):
        parts = urlsplit(url)
        if parts.hostname == "api.geckoterminal.com":
            def item(mint, pool, reserve):
                return {"attributes": {**self.pool, "address": pool, "reserve_in_usd": reserve},
                        "relationships": {"base_token": {"data": {"id": "solana_" + mint}},
                                          "quote_token": {"data": {"id": "solana_" + WSOL}}, "dex": {"data": {"id": "pumpswap"}}}}
            rows = [item(MINT, POOL, "50000"), item(THIN, b58(bytes(range(3, 35))), "1600"), item(DANGER, b58(bytes(range(4, 36))), "80000")]
            return 200, {}, json.dumps({"data": rows}).encode("utf-8")
        if parts.hostname == "api.rugcheck.xyz" and DANGER in parts.path:
            return 200, {}, json.dumps({**self.rug, "risks": [{"level": "danger", "name": "LP Unlocked"}]}).encode("utf-8")
        return super().transport(method, url, headers, body, timeout)


def test_m58_pipeline_filters_vetoes_consults_the_fleet_and_publishes_markdown(tmp_path, monkeypatch):
    feed = ThreePools()
    monkeypatch.setattr(runner, "PublicClient", lambda **_: feed.client)
    real_tick, ticks = sim.tick, []

    def tick(hub, db, bus):
        feed.client.clock = lambda n=len(ticks): NOW + 60 * n  # each cycle observes strictly later than the previous one
        if len(ticks) == 1:  # second cycle : volume et pression acheteuse accélèrent sur le pool retenu (décision)
            feed.pair.update(volume={"m5": 2000}, txns={"m5": {"buys": 12, "sells": 3}})
        if len(ticks) == 2:  # troisième cycle : une cotation ultérieure et distincte exécute l'entrée décidée (contrat causal)
            feed.pair["priceUsd"] = "1.001"
        ticks.append(1)
        return real_tick(hub, db, bus)

    monkeypatch.setattr(sim, "tick", tick)
    monkeypatch.setattr(runner, "build_analytics_report", None)
    output = tmp_path / "report.json"
    report = runner.run(3, tmp_path / "session.sqlite", output, initial_capital=30)
    m58 = report["pipeline"]
    assert report["asset_domain"] == m58["asset_domain"] == "memecoin" and report["paper_only"] and report["ticks_completed"] == 3
    assert m58["thresholds"]["min_liquidity_usd"] == 20000 and m58["run"]["pools"] == 3 and m58["run"]["accepted"] == 1
    assert m58["run"]["refused_by_stage"]["liquidity"] == 1 and m58["vetoes"]["guardian"] == 1  # filtre puis veto anti-arnaque
    assert m58["fleet"]["decisions"] == {"PROPOSITION_SIMULÉE": 1} and report["strategies"]["momentum"]["trades"] == 1
    assert m58["exit_policy"]["name"] == sim.CALIBRATED_EXITS and m58["exit_policy"]["stop_loss"] == 0.20
    assert m58["exit_policy"]["tiers"] == [3.0, 6.0, 10.0] and m58["exit_policy"]["cover_ratio"] == 1.25
    assert [c["mint"] for c in m58["candidates"]] == [MINT]
    md = output.with_suffix(".md").read_text(encoding="utf-8")
    for fragment in ("### Filtrage M58 : pools établis", "| Refusés · Liquidité sous 20 000 $ | 1 |", "| Refusés · Veto anti-arnaque | 1 |",
                     "| Retenus | 1 |", "Entrée fictive autorisée", "stop −20 %", "x3 / x6 / x10", "aucun ordre réel"):
        assert fragment in md, fragment
    assert "| Vetos anti-arnaque (ce passage) | 1 |" in md and "vetos anti-arnaque : 1" in output.with_suffix(".txt").read_text(encoding="utf-8")
    assert json.loads(output.read_text(encoding="utf-8"))["pipeline"]["run"] == m58["run"]
    db = runner.open_session(tmp_path / "session.sqlite")
    try:
        assert db.scalar("SELECT count(*) FROM accounts") == db.scalar("SELECT count(*) FROM fills") == 0  # aucun compte touché
    finally:
        db.close()


def test_the_six_hourly_cloud_runner_can_no_longer_measure_new_entries(tmp_path, monkeypatch):
    """Cadence of the workflow, unchanged: one tick per run (``--ticks 1``), one run every 6 hours. An entry decided in a
    run needs a later valid quote within 10 minutes; the next run comes 6 hours later, so every entry expires unfilled.
    The cloud runner still follows the pipeline, the fleet and the lots already open; it no longer measures new entries."""
    assert "--ticks 1" in WORKFLOW.read_text(encoding="utf-8") and "cron: '0 */6 * * *'" in WORKFLOW.read_text(encoding="utf-8")
    assert sim.ORDER_TTL_S == 600 < 6 * 3600
    feed = ThreePools()
    monkeypatch.setattr(runner, "PublicClient", lambda **_: feed.client)
    monkeypatch.setattr(runner, "build_analytics_report", None)
    db_path, output = tmp_path / "session.sqlite", tmp_path / "report.json"
    reports = []
    for run in range(4):
        feed.client.clock = lambda n=run: NOW + 6 * 3600 * n
        if run:  # from the second run on, the retained pool keeps accelerating: a momentum entry is decided at each run
            feed.pair.update(volume={"m5": 2000 * 2 ** (run - 1)}, txns={"m5": {"buys": 12 + 3 * (run - 1), "sells": 3}})
        feed.pair["priceUsd"] = str(1 + run / 1000)  # every run receives a later, distinct, fresh quote
        reports.append(runner.run(1, db_path, output, initial_capital=30 if run == 0 else None))
    assert [r["ticks_completed"] for r in reports] == [1, 1, 1, 1]
    assert [r["total_trades"] for r in reports] == [0, 0, 0, 0]  # no entry is ever filled
    db = runner.open_session(db_path)
    try:
        state = sim.load(db)[0]
    finally:
        db.close()
    assert [f for f in state["fills"] if f["side"] == "buy"] == [] and state["positions"] == {}
    log = [(r["status"], r["resolved_at"] - r["decided_at"]) for r in state["order_log"]]
    assert log == [("expired", 6 * 3600), ("expired", 6 * 3600)]  # decided at runs 2 and 3, expired at the next run
    assert len(state["orders"]) == 1  # the entry decided at the last run will expire at the next one
    assert [d["decision"] for d in state["fleet"]] == ["PROPOSITION_SIMULÉE"] * 3  # the fleet still accepts entries...
    assert "Entrée fictive autorisée" in output.with_suffix(".md").read_text(encoding="utf-8")  # ...reported, never filled


def test_analytics_are_read_after_close_and_rendered_or_reported_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "scan_once", lambda *a, **k: {"errors": []})
    db_path, output = tmp_path / "session.sqlite", tmp_path / "report.json"
    monkeypatch.setattr(runner, "build_analytics_report", None)
    report = runner.run(1, db_path, output, initial_capital=30)
    assert report["analytics"]["status"] == "unavailable" and report["analytics"]["asset_domain"] == "memecoin"
    md = output.with_suffix(".md").read_text(encoding="utf-8")
    assert "Analytique complète indisponible" in md
    assert "### Apprentissage & multiplicateurs de mise\n\n_Apprentissage indisponible : module memecoin_analytics absent._" in md
    calls = []

    def analytics(path):
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as conn:  # la base est déjà refermée
            calls.append(conn.execute("PRAGMA application_id").fetchone()[0])
        return {"asset_domain": "memecoin", "status": "ok", "initial_capital": 30.0, "equity": 31.5, "net_pnl": 1.5, "net_pnl_pct": 5.0,
                "closed_trades": 4, "win_rate": 0.5, "profit_factor": 2.0, "gross_profit": 3.0, "gross_loss": 1.5,
                "max_drawdown_pct": 4.25, "strategies": {"momentum": {"pnl": 1.5, "trades": 4}}}

    monkeypatch.setattr(runner, "build_analytics_report", analytics)
    report = runner.run(1, db_path, output)
    md = output.with_suffix(".md").read_text(encoding="utf-8")
    assert calls == [runner.APPLICATION_ID] and report["analytics"]["profit_factor"] == 2.0
    for fragment in ("| Profit factor | 2.00 |", "| Drawdown maximal | 4.25 % |", "| Win rate (positions clôturées) | 50.00 % |",
                     "| P&L net | 1.50 $ (5.00 %) |", "| momentum | 1.50 $ | 4 |",
                     "_Apprentissage indisponible : bilan analytique sans section apprentissage._"):
        assert fragment in md, fragment

    def refused(path):
        raise ValueError("Historique des clôtures incomplet")

    monkeypatch.setattr(runner, "build_analytics_report", refused)
    report = runner.run(1, db_path, output)
    md = output.with_suffix(".md").read_text(encoding="utf-8")
    assert report["analytics"]["status"] == "error" and "_Apprentissage indisponible : Historique des clôtures incomplet._" in md


@pytest.mark.skipif(importlib.util.find_spec("sentinelle.dmh.memecoin_analytics") is None,
                    reason="memecoin_analytics arrive avec la PR parallèle #72")
def test_real_analytics_module_feeds_the_report(tmp_path, monkeypatch):
    from sentinelle.dmh import memecoin_analytics

    monkeypatch.setattr(sim, "scan_once", lambda *a, **k: {"errors": []})
    monkeypatch.setattr(runner, "build_analytics_report", memecoin_analytics.build_analytics_report)
    output = tmp_path / "report.json"
    report = runner.run(1, tmp_path / "session.sqlite", output, initial_capital=30)
    assert report["analytics"]["asset_domain"] == "memecoin" and report["analytics"]["status"] == "ok"
    assert {"profit_factor", "max_drawdown_pct", "win_rate", "net_pnl", "strategies"} <= set(report["analytics"])
    assert "| Drawdown maximal | 0.00 % |" in output.with_suffix(".md").read_text(encoding="utf-8")


def learning_section(md):
    section = md.split("### Apprentissage & multiplicateurs de mise\n", 1)[1]
    return section.split("\n### ", 1)[0]


def test_learning_section_on_an_empty_session_shows_neutral_stakes(tmp_path, monkeypatch):
    from sentinelle.dmh import memecoin_analytics

    monkeypatch.setattr(sim, "scan_once", lambda *a, **k: {"errors": []})
    monkeypatch.setattr(runner, "build_analytics_report", memecoin_analytics.build_analytics_report)
    output = tmp_path / "report.json"
    report = runner.run(1, tmp_path / "session.sqlite", output, initial_capital=30)
    assert report["analytics"]["learning"]["status"] == "empty"
    md = output.with_suffix(".md").read_text(encoding="utf-8")
    section = learning_section(md)
    assert md.index("### Stratégies") < md.index("### Apprentissage & multiplicateurs de mise")
    assert ("Apprenant `adaptive-v1` · aucune clôture, toutes les mises restent neutres (1,00x) · 0 clôture(s) · "
            "multiplicateur borné de 0,50x à 1,50x, hausse possible à partir de 8 clôtures par stratégie") in section
    assert "| Stratégie | Multiplicateur | Score | Clôtures | Raison du dimensionnement |\n|---|---|---|---|---|" in section
    for name in ("sniper", "momentum", "dip_hunter", "whale_tail"):
        assert f"| {name} | 1,00x | +0,00 | 0 | aucune clôture, mise neutre 1,00x |" in section
    bullets = [line for line in section.splitlines() if line.startswith("- `")]
    assert [b.split("`")[1] for b in bullets] == ["liquidity_usd", "volume_h1_usd", "confidence"]
    assert all(" · échantillon insuffisant · `applied: false` — " in b and "il en faut 20" in b for b in bullets)
    assert "aucune n'est appliquée automatiquement" in section


def test_learning_section_follows_a_real_closed_lot(tmp_path, monkeypatch):
    from sentinelle.dmh import memecoin_analytics, memecoin_learner

    feed = ThreePools()
    monkeypatch.setattr(runner, "PublicClient", lambda **_: feed.client)
    real_tick, ticks = sim.tick, []

    def tick(hub, db, bus):
        feed.client.clock = lambda n=len(ticks): NOW + 60 * n  # each cycle observes strictly later than the previous one
        if len(ticks) == 1:  # momentum decision on the retained pool
            feed.pair.update(volume={"m5": 2000, "h1": 30000}, txns={"m5": {"buys": 12, "sells": 3}})
        if len(ticks) == 2:  # a later, distinct quote fills the decided entry (causal contract)
            feed.pair["priceUsd"] = "1.001"
        if len(ticks) >= 3:  # price halves twice: the stop is decided, then filled on the next, even lower quote
            feed.pair["priceUsd"] = str(float(feed.pair["priceUsd"]) * 0.5)
        ticks.append(1)
        return real_tick(hub, db, bus)

    monkeypatch.setattr(sim, "tick", tick)
    monkeypatch.setattr(runner, "build_analytics_report", memecoin_analytics.build_analytics_report)
    output = tmp_path / "report.json"
    runner.run(5, tmp_path / "session.sqlite", output, initial_capital=30)
    learned = memecoin_learner.learn(tmp_path / "session.sqlite")["strategies"]["momentum"]
    assert learned["closed_trades"] == 1 and 0.5 <= learned["multiplier"] < 1.0
    section = learning_section(output.with_suffix(".md").read_text(encoding="utf-8"))
    multiplier = f"{learned['multiplier']:.2f}".replace(".", ",")
    score = f"{learned['score']:+.2f}".replace(".", ",")
    assert "· en apprentissage · 1 clôture(s) ·" in section
    assert f"| momentum | {multiplier}x | {score} | 1 | 1 clôtures récentes, profit moyen -" in section
    assert f"-> {multiplier}x (hausse bloquée : 1/8 clôtures) |" in section
    assert "| sniper | 1,00x | +0,00 | 0 | aucune clôture, mise neutre 1,00x |" in section


def test_learning_section_lists_a_suggested_threshold_never_applied(tmp_path, monkeypatch):
    from sentinelle.dmh import memecoin_analytics, memecoin_learner

    history = [{"strategy": "momentum", "mint": "L", "return": -0.2, "entry": {"liquidity_usd": x, "volume_h1_usd": 8_000, "confidence": 0.55}}
               for x in range(21_000, 31_000, 1_000)]
    history += [{"strategy": "momentum", "mint": "L", "return": -0.1, "entry": {"liquidity_usd": x, "volume_h1_usd": 8_000, "confidence": 0.55}}
                for x in (80_000, 90_000)]
    history += [{"strategy": "momentum", "mint": "W", "return": 0.3, "entry": {"liquidity_usd": x, "volume_h1_usd": 8_000, "confidence": 0.55}}
                for x in range(35_000, 85_000, 5_000)]

    def with_history(path):
        return {**memecoin_analytics.build_analytics_report(path), "learning": memecoin_learner.learn_from_state({"completed": history})}

    monkeypatch.setattr(sim, "scan_once", lambda *a, **k: {"errors": []})
    monkeypatch.setattr(runner, "build_analytics_report", with_history)
    output = tmp_path / "report.json"
    runner.run(1, tmp_path / "session.sqlite", output, initial_capital=30)
    section = learning_section(output.with_suffix(".md").read_text(encoding="utf-8"))
    assert "· actif sur les quatre stratégies ·" not in section and "· en apprentissage · 22 clôture(s) ·" in section
    momentum = memecoin_learner.multipliers(history)["momentum"]  # last 20 lots: mean +6 %, upside unlocked
    assert momentum["upside_unlocked"] and 1.0 < momentum["multiplier"] <= 1.5
    assert (f"| momentum | {momentum['multiplier']:.2f}x | {momentum['score']:+.2f} | 22 | ".replace(".", ",")) in section
    assert "| whale_tail | 1,00x | +0,00 | 0 |" in section
    assert ("- `liquidity_usd` · seuil suggéré 35 000.00 $ (règle actuelle 20 000.00 $) · `applied: false` — "
            "liquidité du pool (USD) : médiane 57500,00 pour les gagnantes") in section
    assert "- `volume_h1_usd` · aucun seuil plus strict utile · `applied: false` — " in section
    assert "- `confidence` · aucun seuil plus strict utile · `applied: false` — " in section
    assert "applied: true" not in section


def test_paper_sim_workflow_is_well_formed_and_publishes_the_markdown_summary():
    text = WORKFLOW.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "\t" not in text and text.endswith("\n")
    assert all((len(ln) - len(ln.lstrip(" "))) % 2 == 0 for ln in lines if ln.strip())  # indentation YAML de 2 espaces
    assert [ln.split(":")[0] for ln in lines if re.match(r"^[a-z]", ln)] == ["name", "on", "permissions", "concurrency", "jobs"]
    assert "  contents: read" in lines and "write" not in text.split("jobs:")[0]  # aucun droit d'écriture
    assert "  workflow_dispatch:" in lines and "    - cron: '0 */6 * * *'" in lines
    steps = [ln.strip()[2:] for ln in lines if re.match(r"^      - (name|uses): ", ln)]
    run_step = steps.index("name: Run one bounded paper tick (M58 pipeline)")
    publish = steps.index("name: Publish the analytical report to the job summary")
    assert steps.index("name: Restore the previous session") < run_step < publish < steps.index("uses: actions/upload-artifact@v4")
    assert steps[-1] == "uses: actions/cache/save@v4" and "        uses: actions/cache/restore@v4" in lines
    block = text.split("name: Publish the analytical report to the job summary")[1].split("- uses:")[0]
    assert "if: always()" in block and 'cat cloud/report.md >> "$GITHUB_STEP_SUMMARY"' in block
    assert "python -m sentinelle.dmh.memecoin_cloud_runner --ticks 1 --db-path cloud/session.sqlite --report-path cloud/report.json" in text
    for artifact in ("cloud/report.json", "cloud/report.txt", "cloud/report.md", "cloud/session.sqlite"):
        assert f"            {artifact}" in lines, artifact
    if importlib.util.find_spec("yaml"):  # analyse complète quand un lecteur YAML est installé
        import yaml

        doc = yaml.safe_load(text)
        assert doc[True]["schedule"] == [{"cron": "0 */6 * * *"}] and "workflow_dispatch" in doc[True]
