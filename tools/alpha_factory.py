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
    a = cli.parse_args(argv)
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
