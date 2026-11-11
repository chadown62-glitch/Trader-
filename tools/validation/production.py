"""Chronological laboratory using Sentinelle's actual labels, fitted models, agent and paper broker."""
import argparse
import csv
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from sentinelle.ai.evaluation import compare_portfolios, portfolio_metrics
from sentinelle.ai.trainer import build_dataset, train_walk_forward
from sentinelle.backtest.engine import run_backtest
from sentinelle.config import DEFAULT_SETTINGS

from .lab import GENERATOR_DEVIATIONS, SCENARIOS, code_revision, make_market

SIZES = (100, 400, 500, 1000, 5000)
ASSET_SEED_OFFSETS = tuple(i * 1009 for i in range(6))

TF = "4h"
BAR_MS = 4 * 3_600_000
START_MS = 1_577_836_800_000
SYMBOL = "LAB/EUR"


def market_frame(scenario, seed, n):
    rows = np.asarray(make_market(scenario, seed, n))
    opening, previous_return = 100., 0.
    wicks = np.random.default_rng(seed + 1)
    data = []
    for i, (x, ret, _, _) in enumerate(rows):
        # x is encoded in this bar's volume. Its payoff is observed in the following bar.
        closing = opening * (1 + previous_return)
        wick = abs(wicks.normal(0, .0018))
        data.append((START_MS+i*BAR_MS, opening, max(opening, closing)*(1+wick),
                     min(opening, closing)*(1-wick), closing, 1000*np.exp(.35*x)))
        opening, previous_return = closing, ret
    return pd.DataFrame(data, columns=["ts","open","high","low","close","volume"]), float(rows[0, 2])


def forecast(model, examples):
    if examples.empty:
        return {"n": 0, "auc": None, "brier": None}
    prob, _ = model.predict(examples)
    y = examples.outcome_r.gt(0).to_numpy()
    return {"n": len(y), "auc": float(roc_auc_score(y, prob)) if len(set(y)) == 2 else None,
            "brier": float(np.mean((prob-y)**2)), "average_probability": float(prob.mean()), "observed_rate": float(y.mean())}


def evaluate(scenario, seed, *, bars=7200, test_bars=600, sizes=SIZES, trial_count=1):
    if not 0 < test_bars < bars or not sizes or min(sizes) < 1:
        raise ValueError("Périodes ou tailles invalides")
    frame, fee = market_frame(scenario, seed, bars)
    boundary = int(frame.ts.iloc[-test_bars])
    end = int(frame.ts.iloc[-1] + BAR_MS)
    history = {SYMBOL: frame}
    for index, offset in enumerate(ASSET_SEED_OFFSETS[1:], 2):
        history[f"LAB{index}/EUR"] = market_frame(scenario, seed + offset, bars)[0]
    cfg = deepcopy(DEFAULT_SETTINGS)
    cfg["general"].update(quote="EUR", watchlist=list(history))
    cfg["ai"].update(timeframe=TF, exploration_risk_factor=1.)
    cfg["paper"]["fee_rate"] = fee
    if scenario == "low_liquidity":
        cfg["paper"]["slippage_bps"] = 35.0
    cfg["ai"]["strategies"] = {key: True for key in cfg["ai"]["strategies"]}
    enabled = [k for k, on in cfg["ai"]["strategies"].items() if on]
    cand, _ = build_dataset(history, TF, enabled, fee, cfg["paper"]["slippage_bps"]/10000)
    training = cand[(cand.decision_ts < boundary) & (cand.exit_ts < boundary) & cand.outcome.notna()]
    test = cand[(cand.decision_ts >= boundary) & (cand.exit_ts < end) & cand.outcome.notna()]
    digest = hashlib.sha256(b"".join((symbol+"\n"+history[symbol].to_csv(index=False, float_format="%.17g")).encode()
                                     for symbol in sorted(history))).hexdigest()
    result = {"scenario": scenario, "seed": seed, "dataset_sha256": digest, "symbols": list(history), "asset_seeds":[seed+k for k in ASSET_SEED_OFFSETS], "generator_parameters": GENERATOR_DEVIATIONS[scenario], "bars": bars,
              "test_bars": test_bars, "test_start_ms": boundary, "test_end_ms": end, "settings": cfg,
              "candidates_available": len(training), "candidates_test": len(test), "strategies": {}, "learning_curve": []}

    def replay(model=None, settings=None, policy=None):
        hist = run_backtest(history, TF, settings or cfg, model, exploration=model is None,
                            start_ts=boundary, initial=30., account_currency="EUR", research_trace=True, reference_policy=policy)
        return portfolio_metrics(hist)

    for key, policy in (("cash","cash"), ("passive","passive"), ("all_signals",None)):
        result["strategies"][key] = replay(policy=policy)
    trend = deepcopy(cfg)
    trend["ai"]["strategies"] = {key: key == "trend_follow" for key in cfg["ai"]["strategies"]}
    result["strategies"]["trend_follow"] = replay(settings=trend)
    # The smallest viable prespecified training size is frozen as Champion. The largest
    # feasible size is the single Challenger; intermediate points cannot select the winner.
    viable = []
    for size in sorted(set(sizes)):
        row = {"requested_candidates": size, "available": len(training), "status": "NON TESTÉ"}
        result["learning_curve"].append(row)
        if size > len(training):
            row["reason"] = "Données d'entraînement insuffisantes"
            continue
        sample = training.tail(size)
        run = train_walk_forward(sample, TF, with_importance=False)
        if run.eval_model is None:
            row["reason"] = "; ".join(run.reasons)
            continue
        model = run.eval_model
        if model.trained_until >= boundary:
            raise ValueError("Le modèle a vu la période finale")
        training_hash = hashlib.sha256(sample.to_csv(index=False, float_format="%.17g").encode()).hexdigest()
        fingerprint = hashlib.sha256((training_hash + model.chosen + str(model.threshold) + str(model.calib)).encode()).hexdigest()[:16]
        version = f"{scenario}-{seed}-{size}-{fingerprint}"
        model.version = version
        fit_cut = run.report["period"]["calibration_start"]
        fitted_rows = sample[(sample.decision_ts < fit_cut) & (sample.exit_ts < fit_cut)]
        row.update(fit_forecast=forecast(model, fitted_rows), recipe=model.chosen, payoffs=model.payoffs, status="ÉVALUÉ", version=version, trained_until_ms=model.trained_until, periods=run.report["period"],
                   split_counts=run.report["split_counts"], threshold=model.threshold, calibration=list(model.calib),
                   training_holdout=run.report["summary"], forecast=forecast(model, test), portfolio=replay(model))
        viable.append(row)
    result["comparison"] = {"verdict":"TESTS INCOMPLETS"}
    if len(viable) >= 2:
        champion, challenger = viable[0], viable[-1]
        paired = compare_portfolios(challenger["portfolio"], champion["portfolio"], seed=seed, trials=trial_count)
        result["comparison"] = {**paired, "champion":champion["version"], "challenger":challenger["version"]}
    if viable:
        final = viable[-1]
        result["evidence"] = {"fit_auc":final["fit_forecast"]["auc"], "unseen_auc":final["forecast"]["auc"],
                              "unseen_brier":final["forecast"]["brier"], "net_return":final["portfolio"]["net_return"],
                              "net_verdict":result["comparison"]["verdict"]}
    return result


def reports(results, directory, *, revision=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = {"schema_version":1, "date_utc":datetime.now(timezone.utc).isoformat(), "code_version":revision or code_revision(),
              "source":"OHLCV synthétiques déterministes", "capital_eur":30, "timeframe":TF, "results":results,
              "limits":["Le générateur plante un signal dans le volume. Les prix sont entièrement synthétiques.",
                        "Exécution OHLC à l'ouverture suivante avec glissement modélisé. Aucun carnet historique.",
                        "La référence passive respecte les plafonds d'exposition initiaux et conserve sa position.",
                        "Les intervalles sont conditionnels au scénario et à la longueur de bloc. Les jours sans transaction sont inclus.",
                        "Une comparaison synthétique ne promeut aucun modèle dans l'application."]}
    (directory/"production.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    columns = ["scenario","seed","strategy","size","status","auc","brier","capital_eur","net_return","n_trades","fees_eur","max_drawdown"]
    with (directory/"production.csv").open("w", newline="", encoding="utf-8") as stream:
        out = csv.DictWriter(stream,fieldnames=columns,extrasaction="ignore")
        out.writeheader()
        for result in results:
            common = {"scenario":result["scenario"],"seed":result["seed"]}
            for name,metrics in result["strategies"].items():
                out.writerow({**common,"strategy":name,"status":"ÉVALUÉ",**metrics})
            for point in result["learning_curve"]:
                out.writerow({**common,"strategy":"Sentinelle","size":point["requested_candidates"],"status":point["status"],
                              **point.get("forecast",{}),**point.get("portfolio",{})})
    lines = ["# Validation de Sentinelle en simulation", "", f"Version `{report['code_version']}`. Capital initial de 30 €.", "",
             "| Scénario | Seed | Candidats | AUC | Brier | Trades | Capital final |","|---|---:|---:|---:|---:|---:|---:|"]
    for result in results:
        for p in result["learning_curve"]:
            metrics = p.get("portfolio",{})
            def value(key):
                v = p.get("forecast",{}).get(key)
                return f"{v:.3f}" if v is not None else "—"
            cash = f"{metrics['capital_eur']:.3f} €" if metrics else "—"
            lines.append(f"| {result['scenario']} | {result['seed']} | {p['requested_candidates']} | {value('auc')} | {value('brier')} | {metrics.get('n_trades','—')} | {cash} |")
    lines.extend(["","## Comparaisons appariées",""]+[f"- {r['scenario']} ({r['seed']}) — {r['comparison']['verdict']}" for r in results])
    lines.extend(["","## Limites",""]+[f"- {t}" for t in report["limits"]])
    (directory/"production.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios",nargs="+",choices=SCENARIOS,default=["noise","trend","signal_inverts"])
    parser.add_argument("--seeds",type=int,nargs="+",default=[19])
    parser.add_argument("--sizes",type=int,nargs="+",default=list(SIZES))
    parser.add_argument("--bars",type=int,default=7200)
    parser.add_argument("--test-bars",type=int,default=600)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    total=len(args.scenarios)*len(args.seeds)
    results=[evaluate(name, seed, bars=args.bars, test_bars=args.test_bars, sizes=args.sizes, trial_count=total)
             for name in args.scenarios for seed in args.seeds]
    reports(results,args.output)


if __name__ == "__main__":
    main()
