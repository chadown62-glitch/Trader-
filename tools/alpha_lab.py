"""ALPHA FACTORY v2 — commande du laboratoire de stratégies (hors ligne, exécution finie, aucune promotion).

    python -m tools.alpha_lab candidates                       # les candidats et leurs sources
    python -m tools.alpha_lab universe --archive DOSSIER       # constat sur les données (aucun rendement de stratégie)
    python -m tools.alpha_lab register --archive DOSSIER       # préenregistre le plan (une seule fois)
    python -m tools.alpha_lab verify   [--archive DOSSIER]     # plan, coûts, données et code intacts ?
    python -m tools.alpha_lab run      --archive DOSSIER --output DOSSIER   # exécute le plan enregistré, tel quel
    python -m tools.alpha_lab report   --input DOSSIER         # tableaux lisibles

L'archive est produite à part par ``tools.alpha_lab_fetch`` (téléchargement public borné). Aucune commande
d'ici n'ouvre de connexion, ne démarre de service ni ne modifie le candidat ou le protocole de la PR #48.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from sentinelle.alpha.lab import costs, data, registry  # noqa: E402
from sentinelle.alpha.lab.experiment import FLAGS, REASONS, RETAINED, run_experiment  # noqa: E402
from sentinelle.alpha.lab.simulate import Features  # noqa: E402
from sentinelle.alpha.lab.strategies import CANDIDATES, MIN_AGE, REFERENCES, TOP, Context  # noqa: E402
from sentinelle.alpha.shadow.contract import sha256  # noqa: E402

DIR = ROOT / registry.DIRECTORY


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def other_pairs(panel: data.Panel, feats: Features, start: str) -> list[list[str]]:
    """Les six actifs non majeurs les plus liquides de l'univers daté, la veille du début, par paires."""
    t = panel.index_of(start) - 1
    ctx = Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t])
    names = [panel.base(panel.assets[j]) for j in ctx.universe(min_age=MIN_AGE, top=TOP) if panel.base(panel.assets[j]) not in data.MAJORS][:6]
    return [names[k:k + 2] for k in range(0, len(names) - 1, 2)]


def cmd_candidates(a) -> int:
    print(json.dumps({"candidates": [s.describe() for s in CANDIDATES], "references": [s.describe() for s in REFERENCES]},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_universe(a) -> int:
    panel = data.load_archive(a.archive)
    feats = Features(panel)
    sizes = {}
    for date in a.dates:
        t = panel.index_of(date)
        ctx = Context(panel.view(t), feats.age[t], feats.medvol[t], feats.vol90[t])
        cols = ctx.universe(min_age=MIN_AGE, top=TOP)
        sizes[date] = {"eligible_top": [panel.assets[j] for j in cols], "listed": int(np.isfinite(panel.close[t]).sum())}
    print(json.dumps({"provenance": panel.provenance, "quality": data.quality(panel), "universe": sizes}, ensure_ascii=False, indent=1))
    return 0


def cmd_register(a) -> int:
    out = Path(a.dir)
    if (out / "preregistration.json").exists():
        print("Un plan est déjà enregistré : il est immuable (un changement exige un nouveau plan).", file=sys.stderr)
        return 2
    hyp = costs.load(out / registry.COST_FILE)
    panel = data.load_archive(a.archive)
    feats = Features(panel)
    for key in ("start", "split", "end"):
        panel.index_of(registry.WINDOW[key])  # lève si la date manque dans l'archive
    now = _now_ms()
    plan = registry.build_plan(root=ROOT, data=panel.provenance, hyp=hyp, other_pairs=other_pairs(panel, feats, registry.WINDOW["start"]),
                               registered_at=datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(), registered_at_ms=now)
    digest = registry.write_plan(out, plan)
    registry.record(out, "plan_registered", {"plan_id": plan["plan_id"], "plan_sha256": digest, "candidates": len(plan["candidates"]),
                                             "cost_hypotheses_sha256": hyp.sha256, "archive_manifest_sha256": panel.provenance["archive_manifest_sha256"]}, now)
    print(json.dumps({"plan_sha256": digest, "candidates": len(plan["candidates"]), "window": plan["window"],
                      "other_pairs": plan["generalization"]["other_pairs"]}, ensure_ascii=False, indent=1))
    return 0


def _load(directory: Path, archive: Path | None):
    hyp = costs.load(directory / registry.COST_FILE)
    panel = data.load_archive(archive) if archive else None
    plan, digest, problems = registry.load_plan(directory, root=ROOT, hyp=hyp, data=panel.provenance if panel else None)
    return plan, digest, problems, hyp, panel


def cmd_verify(a) -> int:
    if not (Path(a.dir) / "preregistration.json").exists():
        print(json.dumps({"status": "NOT_REGISTERED", "intact": False,
                          "note": "aucun plan enregistré : préparation, aucune exécution sur données réelles"}, ensure_ascii=False, indent=1))
        return 1
    plan, digest, problems, hyp, _ = _load(Path(a.dir), a.archive)
    print(json.dumps({"plan_id": plan["plan_id"], "plan_sha256": digest, "status": plan["status"], "candidates": len(plan["candidates"]),
                      "journal": [ln["kind"] for ln in registry.journal(Path(a.dir))], "problems": problems, "intact": not problems},
                     ensure_ascii=False, indent=1))
    return 1 if problems else 0


def cmd_run(a) -> int:
    directory, out = Path(a.dir), Path(a.output)
    if not (directory / "preregistration.json").exists():
        print("Exécution refusée : aucun plan enregistré (préenregistrement obligatoire avant toute exécution).", file=sys.stderr)
        return 1
    plan, digest, problems, hyp, panel = _load(directory, a.archive)
    if problems:
        print("Exécution refusée : " + " ; ".join(problems), file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    if (out / "results.json").exists():
        print("Des résultats existent déjà dans ce dossier : utilisez un nouveau dossier.", file=sys.stderr)
        return 2
    t0 = time.time()
    report = run_experiment(panel, plan, hyp)
    daily = report.pop("_daily")
    report["plan_sha256"], report["duration_s"] = digest, round(time.time() - t0, 1)
    report["data"] = panel.provenance
    _dump(out / "results.json", report)
    (out / "daily.json.gz").write_bytes(gzip.compress(json.dumps(daily, sort_keys=True).encode("utf-8"), mtime=0))
    registry.record(directory, "experiment_run", {"plan_id": plan["plan_id"], "plan_sha256": digest, "results_sha256": sha256(report),
                                                  "retained": report["summary"]["retained"], "conclusion": report["summary"]["conclusion"]}, _now_ms())
    print(json.dumps({"duration_s": report["duration_s"], **report["summary"]}, ensure_ascii=False, indent=1))
    return 0


def _f(x, digits=2, signed=True, pct=False):
    if x is None:
        return "—"
    if pct:
        x *= 100
    return ((f"{x:+.{digits}f}" if signed else f"{x:.{digits}f}") + (" %" if pct else "")).replace(".", ",")


def markdown(report: dict) -> str:
    """Tableaux tirés de results.json (aucune valeur saisie à la main)."""
    w = report["window"]
    out = [f"Plan `{report['plan_id']}` · fenêtre {w['start']} → {w['end']} ({w['days']} jours ; P1 {w['p1_days']} j, P2 {w['p2_days']} j) · "
           f"coûts « {report['costs']['primary']} » (tension : « {report['costs']['stress']} ») · {report['tests']['candidates']} candidats", "",
           "#### Résultats nets (30 unités fictives, scénario principal)", "",
           "| Candidat | Famille | Résultat net | Par an | Baisse max. | Sharpe | Exposition | Ordres | Brut | Coûts payés | Tension | P1 | P2 | p corrigée (absolu) | p corrigée (relatif) | Verdict |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, c in report["candidates"].items():
        n, k = c["net"], c["costs"]
        out.append(f"| {name} | {c['family']} | {_f(n['net_pnl'])} | {_f(n['cagr'], 1, pct=True)} | {_f(n['max_drawdown'], 0, False, True)} | {_f(n['sharpe'])} | "
                   f"{_f(n['mean_exposure'], 0, False, True)} | {n['trades']} | {_f(k['gross_pnl'])} | {_f(-(k['fees'] + k['spread_slippage']))} | {_f(k['stressed_pnl'])} | "
                   f"{_f(c['periods']['P1']['return'], 0, pct=True)} | {_f(c['periods']['P2']['return'], 0, pct=True)} | {_f(c['absolute']['p_adjusted'], 3, False)} | "
                   f"{_f(c['relative']['p_adjusted'], 3, False)} | {'**retenu**' if c['verdict'] == RETAINED else 'non retenu'} |")
    out += ["", "#### Références simples (mêmes coûts, mêmes jours)", "", "| Référence | Résultat net | Par an | Baisse max. | Sharpe | P1 | P2 |", "|---|---|---|---|---|---|---|"]
    for name, r in report["references"].items():
        out.append(f"| {name} | {_f(r['net_pnl'])} | {_f(r['cagr'], 1, pct=True)} | {_f(r['max_drawdown'], 0, False, True)} | {_f(r['sharpe'])} | "
                   f"{_f(r['periods']['P1']['return'], 0, pct=True)} | {_f(r['periods']['P2']['return'], 0, pct=True)} |")
    out += ["", "#### Pourquoi ils échouent", "", "| Candidat | Motifs | Signalements | Écart quotidien à la détention de même exposition [IC 95 %] | Part du 1er actif dans les gains | Années pour prouver ce Sharpe |",
            "|---|---|---|---|---|---|"]
    for name, c in report["candidates"].items():
        rel = c["relative"]["interval"]
        out.append(f"| {name} | {' ; '.join(REASONS[r] for r in c['reasons']) or '—'} | {' ; '.join(FLAGS[f] for f in c['flags']) or '—'} | "
                   f"{_f(rel['mean'], 3, pct=True)} [{_f(rel['ci_low'], 3, pct=True)} ; {_f(rel['ci_high'], 3, pct=True)}] | "
                   f"{_f(c['concentration']['top_share_of_gains'], 0, False, True)} ({c['concentration']['top_asset']}) | "
                   f"{_f(c['years_to_prove_at_observed_sharpe'], 0, False)} |")
    g = report["generalization"]
    out += ["", f"#### Généralisation : univers privé de {', '.join(g['exclude_already_studied'])}", "",
            "| Candidat | Résultat net | Sharpe | Baisse max. | Écart à la détention de même exposition [IC 95 %] |", "|---|---|---|---|---|"]
    for name, v in g["broad"].items():
        rel = v["relative"]
        out.append(f"| {name} | {_f(v['net_pnl'])} | {_f(v['sharpe'])} | {_f(v['max_drawdown'], 0, False, True)} | "
                   f"{_f(rel['mean'], 3, pct=True)} [{_f(rel['ci_low'], 3, pct=True)} ; {_f(rel['ci_high'], 3, pct=True)}] |")
    out += ["", "#### Généralisation des règles « BTC et ETH » à d'autres paires", "", "| Candidat | Paire | Résultat net | Sharpe | Écart quotidien à la détention | Détention simple de la paire |",
            "|---|---|---|---|---|---|"]
    for name, rows in g["other_pairs"].items():
        for pair, v in rows.items():
            out.append(f"| {name} | {pair} | {_f(v['net_pnl'])} | {_f(v['sharpe'])} | {_f(v['relative_mean_daily'], 3, pct=True)} | {_f(v['hold_total_return'], 0, pct=True)} |")
    out += ["", f"**Conclusion du banc : {report['summary']['conclusion']}.**"]
    return "\n".join(out)


def cmd_report(a) -> int:
    print(markdown(json.loads((Path(a.input) / "results.json").read_text(encoding="utf-8"))))
    return 0


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    sub.add_parser("candidates")
    u = sub.add_parser("universe")
    u.add_argument("--archive", type=Path, required=True)
    u.add_argument("--dates", nargs="+", default=[registry.WINDOW["start"], registry.WINDOW["split"], registry.WINDOW["end"]])
    r = sub.add_parser("register")
    r.add_argument("--archive", type=Path, required=True)
    r.add_argument("--dir", type=Path, default=DIR)
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, default=DIR)
    v.add_argument("--archive", type=Path)
    x = sub.add_parser("run")
    x.add_argument("--archive", type=Path, required=True)
    x.add_argument("--output", type=Path, required=True)
    x.add_argument("--dir", type=Path, default=DIR)
    p = sub.add_parser("report")
    p.add_argument("--input", type=Path, required=True)
    a = cli.parse_args(argv)
    return {"candidates": cmd_candidates, "universe": cmd_universe, "register": cmd_register, "verify": cmd_verify, "run": cmd_run,
            "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
