"""ALPHA SHADOW — commande de recherche bornée pour le protocole RegimeRouter-R1 (mission #41).

Hors ligne, exécution finie, aucun ordre, aucun service. Les commandes :

    python -m tools.alpha_shadow contract                      # formats du contrat alpha_shadow_contract_v1
    python -m tools.alpha_shadow freeze   --archive DOSSIER    # développement + gel de R1 (une seule fois)
    python -m tools.alpha_shadow register --not-before DATE    # puissance, durée, manifeste immuable
    python -m tools.alpha_shadow verify                        # manifeste, artefacts et code intacts ?
    python -m tools.alpha_shadow replay   --archive DOSSIER --output DOSSIER   # rejeu EXPLORATOIRE du moteur
    python -m tools.alpha_shadow report   --ledger FICHIER [--anchors FICHIER]
    python -m tools.alpha_shadow anchor   --ledger FICHIER     # ligne à publier pour dater le registre
    python -m tools.alpha_shadow bench    --output DOSSIER     # coût de calcul et courbes d'apprentissage (exploratoire)

``freeze`` et ``replay`` lisent des archives publiques DÉJÀ téléchargées (``tools.collect_public``) ; ils ne
téléchargent rien. Aucune commande ne démarre l'étude prospective : elle exige un accord distinct.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from sentinelle.alpha.bars import Bars, Universe, align  # noqa: E402
from sentinelle.alpha.execution import CostModel, PortfolioRules  # noqa: E402
from sentinelle.alpha.shadow import analysis, protocol  # noqa: E402
from sentinelle.alpha.shadow.contract import json_schema, make, sha256  # noqa: E402
from sentinelle.alpha.shadow.engine import Limits, ShadowEngine, load_lines  # noqa: E402
from sentinelle.alpha.shadow.ledger import ChainLedger  # noqa: E402
from sentinelle.alpha.shadow.r1 import load_model  # noqa: E402

DIR = ROOT / protocol.DIRECTORY
SYMBOLS = ("BTC/EUR", "ETH/EUR", "SOL/EUR")
HORIZONS = (90, 180, 270, 365)
EFFECTS = (0.0, 0.02, 0.05, 0.10, 0.20)
HORIZON_RULE = {"target_yearly_effect": 0.05, "target_power": 0.80,
                "text": "plus petite durée parmi 90, 180, 270 et 365 jours dont la puissance simulée atteint 80 % pour un "
                        "avantage vrai de +5 % par an ; à défaut 365 jours, puissance déclarée"}


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def load_universe(archive: Path, symbols=SYMBOLS) -> tuple[Universe, dict]:
    """Archives publiques immuables (vérifiées à chaque lecture) -> bougies alignées + provenance."""
    from tools.validation.archive import load_archive

    bars, prov = [], {}
    for sym in symbols:
        frame, manifest = load_archive(Path(archive) / sym.replace("/", "-"))
        bars.append(Bars(sym, frame.ts.to_numpy(np.int64), *(frame[c].to_numpy(float) for c in ("open", "high", "low", "close", "volume")),
                         None, 14_400_000, f"public:{manifest['source']}:sha256={manifest['dataset_sha256']}"))
        prov[sym] = {"dataset_sha256": manifest["dataset_sha256"], "source": manifest["source"], "bars": manifest["quality"]["bars"],
                     "start_ms": manifest["request"]["start_ms"], "end_ms": manifest["request"]["end_ms"],
                     "observed_at_ms": manifest["request"]["observed_at_ms"]}
    return align(bars), prov


def cmd_freeze(a) -> int:
    from sentinelle.alpha.shadow import develop

    out = Path(a.out)
    if (out / "r1-model.json").exists():
        print("Un artefact R1 existe déjà : le gel ne se refait pas (nouveau protocole requis).", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    universe, prov = load_universe(a.archive)
    cost, rules = CostModel(), PortfolioRules()
    d = develop.develop(universe, cost=cost, rules=rules, provenance=prov)
    artifact = d["artifact"]
    digest = sha256(artifact)
    _dump(out / "r1-model.json", artifact)
    ref_sha = None
    if d["reference_bytes"]:
        (out / "reference-project-mixte.joblib").write_bytes(d["reference_bytes"])
        ref_sha = protocol.binary_sha(out / "reference-project-mixte.joblib")
    control = d["control"]
    sel, ev = control["selection"]["arms"]["r1"], control["evaluation"]["arms"]["r1"]
    report = {"kind": "alpha-shadow-r1-development", "status": "EXPLORATORY : périodes déjà étudiées, aucune valeur de preuve",
              "model_sha256": digest, "reference_project_mixte_sha256": ref_sha, "data": prov,
              "windows": d["windows"].to_dict(), "generated_at_ms": _now_ms(),
              "selection_to_control": {"selection_mean_net_r": sel["mean_net_r_taken"], "control_mean_net_r": ev["mean_net_r_taken"],
                                       "selection_net_pnl_eur": sel["net_pnl_eur"], "control_net_pnl_eur": ev["net_pnl_eur"],
                                       "note": "la marge a été choisie sur la sélection : son score y est optimiste par construction"},
              "control": {name: {"window_ms": c["window_ms"], "bars": c["bars"],
                                 "arms": {arm: {k: ({str(d_): v for d_, v in val.items()} if k in ("daily", "entries_by_day") else val)
                                                for k, val in row.items()} for arm, row in c["arms"].items()}}
                          for name, c in control.items()}}
    _dump(out / "development-report.json", report)
    protocol.journal_append(out / "trials.jsonl", "development_freeze",
                            {"model_sha256": digest, "reference_project_mixte_sha256": ref_sha,
                             "development_report_sha256": protocol.file_sha(out / "development-report.json"),
                             "preselection": artifact["training"]["preselection"], "data": prov,
                             "note": "gel unique de RegimeRouter-R1 ; mêmes hyperparamètres que la PR #40"}, _now_ms())
    print(json.dumps({"model_sha256": digest, "margin_r": artifact["margin_r"], "reference_teacher": artifact["reference_teacher"],
                      "preselection": artifact["training"]["preselection"],
                      "control_r1_net_pnl_eur": ev["net_pnl_eur"], "control_take_all_net_pnl_eur": control["evaluation"]["arms"]["take_all"]["net_pnl_eur"]},
                     ensure_ascii=False, indent=1))
    return 0


def choose_horizon(power: dict) -> tuple[int, float]:
    key = f"{HORIZON_RULE['target_yearly_effect']:.2f}"
    for h in HORIZONS:
        if power["power"][str(h)][key] >= HORIZON_RULE["target_power"]:
            return h, power["power"][str(h)][key]
    return HORIZONS[-1], power["power"][str(HORIZONS[-1])][key]


def cmd_register(a) -> int:
    out = Path(a.dir)
    artifact = json.loads((out / "r1-model.json").read_text(encoding="utf-8"))
    digest = sha256(artifact)
    load_model(artifact, digest)
    dev = json.loads((out / "development-report.json").read_text(encoding="utf-8"))
    if dev["model_sha256"] != digest:
        print("Le rapport de développement ne correspond pas à l'artefact.", file=sys.stderr)
        return 2
    ev = dev["control"]["evaluation"]["arms"]
    r1 = {int(k): v for k, v in ev["r1"]["daily"].items()}
    ref = {int(k): v for k, v in ev["take_all"]["daily"].items()}
    power = analysis.power_table(r1, ref, horizons=HORIZONS, yearly_effects=EFFECTS, plan={})
    horizon, achieved = choose_horizon(power)
    floor = analysis.activity_floor({int(k): v for k, v in ev["r1"]["entries_by_day"].items()}, sorted(r1), horizon)
    not_before = int(datetime.strptime(a.not_before, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    now = _now_ms()
    if not_before <= now:
        print("--not-before doit être dans le futur : la fenêtre ne peut pas précéder l'enregistrement.", file=sys.stderr)
        return 2
    summary = {**power, "horizon_rule": HORIZON_RULE, "chosen_horizon_days": horizon, "power_at_target": achieved,
               "activity": floor, "min_r1_entries_rule": "5e centile de l'activité de R1 au contrôle de développement, sur la durée choisie"}
    manifest = protocol.build_manifest(root=ROOT, model_sha256=digest, reference_sha256=dev["reference_project_mixte_sha256"],
                                       development_sha256=protocol.file_sha(out / "development-report.json"), registered_at_ms=now,
                                       registered_at=_iso(now), not_before_ms=not_before, horizon_days=horizon,
                                       min_r1_entries=max(1, floor["p05"]), symbols=artifact["training"]["symbols"],
                                       cost=CostModel(**artifact["training"]["cost"]), rules=PortfolioRules(**artifact["training"]["rules"]),
                                       limits=Limits(), power=summary, data=dev["data"])
    m_sha = protocol.write_manifest(out, manifest)
    protocol.journal_append(out / "trials.jsonl", "protocol_registered",
                            {"protocol_id": manifest["protocol_id"], "manifest_sha256": m_sha, "model_sha256": digest, "trials": 1,
                             "horizon_days": horizon, "not_before_ms": not_before, "status": manifest["status"]}, now)
    print(json.dumps({"manifest_sha256": m_sha, "horizon_days": horizon, "power_at_target": achieved, "min_r1_entries": max(1, floor["p05"]),
                      "not_before": _iso(not_before)}, ensure_ascii=False, indent=1))
    return 0


def load_protocol(directory: Path, *, check_code: bool = True):
    manifest, m_sha, problems = protocol.load_manifest(directory, root=ROOT if check_code else None)
    artifact = json.loads((Path(directory) / manifest["candidate"]["model_file"]).read_text(encoding="utf-8"))
    model = load_model(artifact, manifest["candidate"]["model_sha256"])
    return manifest, m_sha, problems, model


def load_reference(directory: Path, manifest: dict):
    """Référence descriptive ; absente ou illisible -> bras déclaré indisponible (jamais simulé à l'aveugle)."""
    digest = manifest["arms"]["project_mixte_sha256"]
    if digest is None:
        return None
    try:
        from sentinelle.alpha.shadow import reference

        return reference.load(Path(directory) / manifest["arms"]["project_mixte_file"], digest)
    except Exception as exc:  # noqa: BLE001 - bibliothèque absente, version incompatible : bras indisponible
        print(f"Référence « modèle du projet » indisponible : {exc}", file=sys.stderr)
        return None


def cmd_verify(a) -> int:
    manifest, m_sha, problems, model = load_protocol(Path(a.dir))
    journal = protocol.read_journal(Path(a.dir) / "trials.jsonl")
    print(json.dumps({"protocol_id": manifest["protocol_id"], "manifest_sha256": m_sha, "model_sha256": model.sha256,
                      "status": manifest["status"], "horizon_days": manifest["window"]["horizon_days"],
                      "not_before": _iso(manifest["window"]["not_before_ms"]), "trials_in_journal": [ln["kind"] for ln in journal],
                      "problems": problems, "intact": not problems}, ensure_ascii=False, indent=1))
    return 1 if problems else 0


def stream(universe: Universe, start_index: int, end_index: int, *, latency_ms: int = 1000):
    """Bougies d'un historique, une clôture à la fois (pour un rejeu : l'horloge est celle des données)."""
    tf = 14_400_000
    for i in range(start_index, end_index):
        close = int(universe.ts[i]) + tf
        yield close, [make("bar", symbol=s, timeframe_ms=tf, open_at_ms=int(b.ts[i]), close_at_ms=close, open=float(b.open[i]),
                           high=float(b.high[i]), low=float(b.low[i]), close=float(b.close[i]), volume=float(b.volume[i]),
                           source=b.provenance[:200], published_at_ms=close, received_at_ms=close + latency_ms, payload_sha256=None)
                      for s, b in sorted(universe.bars.items())]


def replay(universe: Universe, manifest: dict, m_sha: str, model, *, start_ms: int, days: int, ledger_path: Path | None,
           reference=None, mode: str = "replay_exploratory") -> ShadowEngine:
    cfg = protocol.session_config(manifest, m_sha, start_ms=start_ms, mode=mode, horizon_days=days)
    tf = cfg.tf_ms
    closes = universe.ts + tf
    first = int(np.searchsorted(closes, cfg.start_ms))
    last = int(np.searchsorted(closes, cfg.end_ms, side="right"))
    warm = max(0, first - 1)
    history = [bar for _, bars in stream(universe, 0, warm) for bar in bars]
    t0 = int(closes[warm - 1]) + 2000 if warm else int(universe.ts[0])
    engine = ShadowEngine.start(cfg, model, ChainLedger(ledger_path, fsync=False), now_ms=t0, reference=reference)
    if history:
        engine.step(t0, bars=history)
    for close, bars in stream(universe, warm, last):
        engine.step(close + 2000, bars=bars)
    return engine


def cmd_replay(a) -> int:
    manifest, m_sha, problems, model = load_protocol(Path(a.dir), check_code=False)
    universe, prov = load_universe(a.archive)
    dev = json.loads((Path(a.dir) / "development-report.json").read_text(encoding="utf-8"))
    lo, hi = dev["control"]["evaluation"]["window_ms"]
    start = int(datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) if a.start else lo
    days = a.days or (hi - start) // 86_400_000
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "ledger.jsonl"
    if ledger.exists():
        print("Un registre existe déjà dans ce dossier : utilisez un nouveau dossier.", file=sys.stderr)
        return 2
    t0 = time.time()
    engine = replay(universe, manifest, m_sha, model, start_ms=start, days=days, ledger_path=ledger,
                    reference=load_reference(Path(a.dir), manifest))
    report = analysis.build_report(engine.ledger.lines, manifest_sha256=m_sha, model_sha256=model.sha256, plan=manifest["analysis"],
                                   now_ms=engine.now, explored=True)
    report["inputs"] = prov
    _dump(out / "report.json", report)
    print(json.dumps({"verdict": report["verdict"], "lines": len(engine.ledger.lines), "duration_s": round(time.time() - t0, 1),
                      "arms": {k: round(v["net_pnl_eur"], 4) for k, v in report["arms"].items()}}, ensure_ascii=False, indent=1))
    return 0


def cmd_report(a) -> int:
    manifest, m_sha, problems, model = load_protocol(Path(a.dir))
    lines = load_lines(Path(a.ledger))
    anchors = json.loads(Path(a.anchors).read_text(encoding="utf-8")) if a.anchors else []
    report = analysis.build_report(lines, manifest_sha256=m_sha, model_sha256=model.sha256, plan=manifest["analysis"],
                                   now_ms=a.now_ms or _now_ms(), anchors=anchors)
    if problems:
        report["verdict"], report["reasons"] = "FAILED_INTEGRITY", problems + report["reasons"]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def cmd_anchor(a) -> int:
    lines = load_lines(Path(a.ledger))
    print(json.dumps({"contract": "alpha_shadow_anchor_v1", "ledger_id": lines[0]["sha"], "seq": lines[-1]["seq"],
                      "head_sha256": lines[-1]["sha"], "created_at_ms": _now_ms()}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    sub.add_parser("contract")
    f = sub.add_parser("freeze")
    f.add_argument("--archive", type=Path, required=True)
    f.add_argument("--out", type=Path, default=DIR)
    r = sub.add_parser("register")
    r.add_argument("--dir", type=Path, default=DIR)
    r.add_argument("--not-before", required=True, help="date UTC la plus précoce du début de fenêtre (AAAA-MM-JJ, future)")
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, default=DIR)
    p = sub.add_parser("replay")
    p.add_argument("--dir", type=Path, default=DIR)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--start", help="début UTC (AAAA-MM-JJ) ; par défaut la fenêtre de contrôle du développement")
    p.add_argument("--days", type=int)
    rep = sub.add_parser("report")
    rep.add_argument("--dir", type=Path, default=DIR)
    rep.add_argument("--ledger", type=Path, required=True)
    rep.add_argument("--anchors", type=Path)
    rep.add_argument("--now-ms", type=int)
    an = sub.add_parser("anchor")
    an.add_argument("--ledger", type=Path, required=True)
    b = sub.add_parser("bench")
    b.add_argument("--output", type=Path, required=True)
    b.add_argument("--archive", type=Path)
    b.add_argument("--repeats", type=int, default=7)
    b.add_argument("--quick", action="store_true")
    a = cli.parse_args(argv)
    if a.cmd == "contract":
        print(json.dumps(json_schema(), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "bench":
        from tools.alpha_bench import run_bench

        return run_bench(a)
    return {"freeze": cmd_freeze, "register": cmd_register, "verify": cmd_verify, "replay": cmd_replay, "report": cmd_report,
            "anchor": cmd_anchor}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
