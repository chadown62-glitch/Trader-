"""ALPHA FACTORY — commande de recherche bornée (hors ligne, exécution finie, aucun ordre).

    python -m tools.alpha_factory catalogue
    python -m tools.alpha_factory run --output DOSSIER [--quick] [--seeds 101 202 303]
    python -m tools.alpha_factory real --output DOSSIER --history DOSSIER_CACHE [--symbols BTC/USDT ...]

``run`` : écrit d'abord ``preregistration.json`` (configuration, empreinte, règle de décision),
puis exécute le Learning Velocity Challenge, les ablations du Shadow Council et le Shadow
Tournament sur les marchés synthétiques, et écrit ``report.json`` + ``summary.json``.

``real`` : même protocole sur des historiques publics DÉJÀ archivés (lecture seule, empreintes
SHA-256 consignées). Ces périodes ont déjà été étudiées par le projet : résultat EXPLORATOIRE,
jamais une preuve confirmatoire. Aucun téléchargement n'est fait.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentinelle.alpha.bars import align, load_history_csv  # noqa: E402
from sentinelle.alpha.catalogue import load_catalogue  # noqa: E402
from sentinelle.alpha.execution import CostModel  # noqa: E402
from sentinelle.alpha.experiments import council_ablation, summarize_ablation, tournament  # noqa: E402
from sentinelle.alpha.velocity import ChallengeConfig, run_challenge  # noqa: E402

# Graines officielles jamais exécutées pendant le développement (101 et 202 l'ont été : écartées).
OFFICIAL = ChallengeConfig(seeds=(1001, 2002, 3003), markets=(("noise", 0.0), ("regimes", 1.0), ("regime_shift", 1.0),
                                                            ("hidden_rule", 1.0), ("hidden_rule", 0.5)), n_bars=10000)
QUICK = ChallengeConfig(seeds=(101, 202), markets=(("regimes", 1.0), ("noise", 0.0)), n_bars=3000, budgets=(100, None))


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=float), encoding="utf-8")


def compact(report: dict, ablation: dict, tour: dict, meta: dict) -> dict:
    """Résumé publiable : tableaux agrégés, sans les séries journalières."""
    return {"kind": "alpha-factory-summary", **meta, "preregistration_sha256": report["preregistration"]["config_sha256"],
            "confidence_per_interval": report["confidence_per_interval"], "velocity": report["summary"],
            "ablation": ablation, "tournament": {"verdicts": tour["verdicts"],
                                                 "markets": [{k: v for k, v in m.items()} for m in tour["markets"]]},
            "can_trade": False, "claim": "recherche hors ligne ; aucun gain futur promis ; aucune promotion"}


def run(cfg: ChallengeConfig, output: Path, *, extra=None, label="synthétique") -> dict:
    output.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    report = run_challenge(cfg, output=output, extra_markets=extra)  # préenregistrement écrit en premier
    ablations = []
    for m in report["_markets_raw"]:
        universe = dict(extra or []).get(m["kind"]) if m["seed"] == -1 else None
        ablations.append(council_ablation(m["kind"], m["edge"], m["seed"], cfg, universe=universe,
                                          ledger_path=output / f"ledger-{m['kind']}-{m['seed']}.jsonl",
                                          confidence=report["confidence_per_interval"]))
    ab_summary = summarize_ablation(ablations)
    tour = tournament(report["_markets_raw"], ablations)
    _dump(output / "ablation.json", [{k: (v if k != "results" else {n: {kk: vv for kk, vv in r.items() if not kk.startswith("_")}
                                                                   for n, r in v.items()}) for k, v in a.items()} for a in ablations])
    _dump(output / "tournament.json", tour)
    summary = compact(report, ab_summary, tour, {"data": label, "duration_s": round(time.time() - t0, 1)})
    _dump(output / "summary.json", summary)
    return summary


def _fmt(x, digits=2, signed=True):
    if x is None:
        return "—"
    return (f"{x:+.{digits}f}" if signed else f"{x:.{digits}f}").replace(".", ",")


def markdown(summary: dict, *, budgets=("50", "tout")) -> str:
    """Tableaux lisibles tirés de summary.json (aucune valeur saisie à la main)."""
    out = [f"Données : {summary.get('data')} · préenregistrement `{summary['preregistration_sha256'][:16]}…` · "
           f"confiance par intervalle {_fmt(summary['confidence_per_interval'] * 100, 2, False)} % · durée {summary.get('duration_s')} s", ""]
    for fam, table in summary["velocity"].items():
        out += [f"#### {fam}", "", "| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | "
                "Brier vs taux enseignant | AUC | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in table.values():
            if row["budget"] not in budgets and row["learner"] not in ("take_all",):
                continue
            if row["learner"] == "take_all" and row["budget"] != "tout":
                continue
            out.append(f"| {row['learner']} | {row['budget']} | {_fmt(row['mean_net_pnl_eur'])} | {_fmt(row['mean_delta_vs_take_all_eur'])} | "
                       f"{_fmt(row['mean_trades'], 1, False)} | {_fmt(row['mean_max_drawdown'] * 100, 1, False)} % | "
                       f"{_fmt(row['mean_brier_skill'], 3)} | {_fmt(row['mean_auc'], 3, False)} | {_fmt(row['cpu_s'], 2, False)} | "
                       f"{_fmt(row['peak_mb'], 1, False)} | {_fmt(row['delta_eur_per_100_obs'])} | "
                       f"{row['positive_lower_bounds']}/{row['replications']} | {row['verdict']} |")
        out.append("")
    for fam, table in summary["ablation"].items():
        out += [f"#### Ablation du conseil — {fam}", "", "| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |",
                "|---|---|---|---|---|---|"]
        for name, row in table.items():
            out.append(f"| {name} | {_fmt(row['mean_net_pnl_eur'])} | {_fmt(row['mean_trades'], 1, False)} | {row['vs_take_all']} | "
                       f"{_fmt(row.get('marginal_contribution_eur'))} | {row.get('recommandation', '')} |")
        out.append("")
    out += ["#### Shadow Tournament", "", "| Marché | Graine | Concurrents | Vainqueur (sélection) | P&L évaluation (€) | Malédiction du vainqueur (/jour) | "
            "Corrélation de rang sélection→évaluation |", "|---|---|---|---|---|---|---|"]
    for m in summary["tournament"]["markets"]:
        out.append(f"| {m['kind']}@{m['edge']} | {m['seed']} | {m['candidates']} | {m['winner']} | {_fmt(m['winner_evaluation_pnl_eur'])} | "
                   f"{_fmt(m['winner_curse'] * 100, 3)} % | {_fmt(m['selection_vs_evaluation_rank_correlation'], 2)} |")
    out += ["", "Verdicts du tournoi : " + "; ".join(f"{k} : {v['verdict']} ({v['positive_lower_bounds']}/{v['replications']})"
                                                  for k, v in summary["tournament"]["verdicts"].items())]
    return "\n".join(out)


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalogue")
    r = sub.add_parser("run")
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--quick", action="store_true")
    r.add_argument("--seeds", type=int, nargs="+")
    real = sub.add_parser("real")
    real.add_argument("--output", type=Path, required=True)
    real.add_argument("--history", type=Path, required=True)
    real.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT"])
    real.add_argument("--fx-bps", type=float, default=5.0, help="conversion USDT -> EUR par ordre (hypothèse prudente)")
    rep = sub.add_parser("report")
    rep.add_argument("--input", type=Path, required=True)
    a = cli.parse_args(argv)
    if a.cmd == "report":
        print(markdown(json.loads((a.input / "summary.json").read_text(encoding="utf-8"))))
        return 0
    if a.cmd == "catalogue":
        cat = load_catalogue()
        print(json.dumps({"version": cat.version, "sha256": cat.sha256,
                          "sources": {s.id: {"level": s.level, "used_by": list(s.used_by)} for s in cat.sources.values()}},
                         indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "run":
        cfg = QUICK if a.quick else OFFICIAL
        if a.seeds:
            cfg = ChallengeConfig(**{**asdict(cfg), "seeds": tuple(a.seeds)})
        summary = run(cfg, a.output)
    else:
        bars, digests = [], {}
        for sym in a.symbols:
            bar, digest = load_history_csv(a.history / f"{sym.replace('/', '-')}_4h.csv.gz", sym)
            bars.append(bar)
            digests[sym] = digest
        universe = align(bars)
        groups = {"real-all": universe}
        half = len(a.symbols) // 2
        if half >= 2:  # deux groupes d'actifs disjoints : pseudo-réplications (corrélées, PAS indépendantes)
            groups["real-group-1"] = align(bars[:half])
            groups["real-group-2"] = align(bars[half:])
        cfg = ChallengeConfig(seeds=(), markets=(), budgets=(100, 400, None),
                              cost=asdict(CostModel(fx_bps=a.fx_bps)))
        summary = run(cfg, a.output, extra=list(groups.items()), label="public-exploratoire")
        summary["inputs_sha256"] = digests
        summary["bars"] = universe.length
        summary["period_ms"] = [int(universe.ts[0]), int(universe.ts[-1])]
        _dump(a.output / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("preregistration_sha256", "duration_s", "data")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
