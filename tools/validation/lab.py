"""Deterministic synthetic research benchmark; no production broker or network.

This deliberately small reference model is not TRADER AI. It verifies a research
protocol and supplies controls until a production-model adapter is reviewed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SCENARIOS = (
    "noise", "trend", "mean_reversion", "alternating", "high_volatility",
    "downtrend", "regime_break", "signal_disappears", "signal_inverts",
    "spurious_correlation", "delayed_data", "increasing_noise", "high_cost",
    "low_liquidity", "stay_flat",
)
SIZES = (100, 500, 1000, 5000)
INITIAL = 30.0
GENERATOR_DEVIATIONS = {
    "noise": {"coefficient": 0.0}, "trend": {}, "mean_reversion": {"coefficient": -0.003},
    "alternating": {"coefficient_blocks": [0.003, 0.0], "block_periods": 500},
    "high_volatility": {"sigma": 0.015}, "downtrend": {"drift": -0.004},
    "regime_break": {"coefficient_after_6000": -0.003},
    "signal_disappears": {"coefficient_after_6000": 0.0},
    "signal_inverts": {"coefficient_after_6000": -0.005},
    "spurious_correlation": {"coefficient_after_6000": 0.0},
    "delayed_data": {"return_feature_lag": 1},
    "increasing_noise": {"sigma_start": 0.002, "sigma_end": 0.010},
    "high_cost": {"cost_per_side": 0.004},
    "low_liquidity": {"cost_per_side": 0.006, "sigma": 0.008},
    "stay_flat": {"drift": -0.005, "coefficient": 0.0, "cost_per_side": 0.001},
}


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def make_market(name, seed, n=7000):
    """(visible signal, subsequent simple return, per-side cost, trading allowed).

    Observations are synthetic and each represents one non-overlapping period.
    x is known before y. The regime transition is at 6000, fixed independently
    of test outcome. Artificial data have no claim to real-market properties.
    """
    if name not in SCENARIOS or n < 2:
        raise ValueError("unknown scenario or insufficient observations")
    rng = random.Random(seed)
    rows, prev_x = [], 0.0
    for t in range(n):
        x = rng.gauss(0, 1)
        sign = 1 if x >= 0 else -1
        coef, drift, sigma, cost = 0.003, 0.0, 0.004, 0.0005
        allowed = True
        if name == "noise": coef = 0.0
        elif name == "mean_reversion": coef = -0.003
        elif name == "alternating": coef = 0.003 if (t // 500) % 2 == 0 else 0.0
        elif name == "high_volatility": sigma = 0.015
        elif name == "downtrend": drift = -0.004
        elif name == "regime_break": coef = 0.003 if t < 6000 else -0.003
        elif name == "signal_disappears": coef = 0.003 if t < 6000 else 0.0
        elif name == "signal_inverts": coef = 0.003 if t < 6000 else -0.005
        elif name == "spurious_correlation": coef = 0.003 if t < 6000 else 0.0
        elif name == "delayed_data": sign = 1 if prev_x >= 0 else -1
        elif name == "increasing_noise": sigma = 0.002 + 0.008 * t / max(1, n - 1)
        elif name == "high_cost": cost = 0.004
        elif name == "low_liquidity": cost = 0.006; sigma = 0.008
        elif name == "stay_flat": drift, coef, cost = -0.005, 0.0, 0.001
        y = max(-0.95, min(0.95, drift + coef * sign + rng.gauss(0, sigma)))
        rows.append((x, y, cost, allowed))
        prev_x = x
    return rows


def fit_logistic(rows, epochs=30, learning_rate=0.3, l2=0.01):
    """Fit a tiny binary model only on prior examples; fixed hyperparameters."""
    if not rows:
        raise ValueError("empty training set")
    intercept, weight = 0.0, 0.0
    for _ in range(epochs):
        gi, gw = 0.0, 0.0
        for x, y, _, _ in rows:
            d = sigmoid(intercept + weight * x) - (1 if y > 0 else 0)
            gi += d
            gw += d * x
        intercept -= learning_rate * gi / len(rows)
        weight -= learning_rate * (gw / len(rows) + l2 * weight)
    return intercept, weight


def auc_score(scores, labels):
    """Mann-Whitney AUC with average ranks for ties; None for one class."""
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    pairs = sorted(zip(scores, labels), key=lambda pair: pair[0])
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        rank_sum += avg_rank * sum(label for _, label in pairs[i:j])
        i = j
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def score(rows, probabilities, rule, allocation=0.2, periods_per_day=24):
    if len(rows) != len(probabilities) or not 0 < allocation <= 1:
        raise ValueError("invalid inputs")
    equity, peak, dd = INITIAL, INITIAL, 0.0
    net_trades, daily = [], []
    day_start, fees, refused = equity, 0.0, 0
    labels = [int(y > 0) for _, y, _, _ in rows]
    for i, ((x, y, side_cost, allowed), p) in enumerate(zip(rows, probabilities)):
        take = allowed and ((rule == "model" and p >= 0.55) or rule == "long" or (rule == "signal_rule" and x > 0))
        if take:
            # One non-overlapping position per period; cost applies on both sides.
            net = (1 - side_cost) * (1 + y) * (1 - side_cost) - 1
            allocation_value = equity * allocation
            fees += allocation_value * (side_cost + (1 - side_cost) * (1 + y) * side_cost)
            equity *= 1 + allocation * net
            net_trades.append(net)
        else:
            refused += 1
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
        if (i + 1) % periods_per_day == 0 or i == len(rows) - 1:
            daily.append(equity / day_start - 1)
            day_start = equity
    n = len(rows)
    if not n:
        raise ValueError("empty evaluation set")
    bins = []
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        idx = [i for i, p in enumerate(probabilities) if lo <= p < lo + 0.2 or (lo == 0.8 and p == 1)]
        bins.append({"lower": lo, "n": len(idx),
                     "predicted": statistics.fmean(probabilities[i] for i in idx) if idx else None,
                     "observed": statistics.fmean(labels[i] for i in idx) if idx else None})
    return {
        "n_periods": n, "n_trades": len(net_trades), "refused": refused,
        "accuracy": sum((p >= 0.5) == bool(y) for p, y in zip(probabilities, labels)) / n,
        "brier": sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / n, "calibration_bins": bins,
        "auc": auc_score(probabilities, labels),
        "mean_net_trade": statistics.fmean(net_trades) if net_trades else None,
        "return_pct": (equity / INITIAL - 1) * 100, "final_equity_eur": equity,
        "daily_returns": daily, "mean_daily_return": statistics.fmean(daily),
        "max_drawdown_pct": dd * 100, "fees_and_slippage_eur": fees,
    }


def block_bootstrap_mean_ci(values, seed, block=5, reps=1000, confidence=0.95):
    """Moving-block bootstrap, preserves local dependence, conditional on sample."""
    if not values or block < 1 or block > len(values) or reps < 20 or not 0 < confidence < 1:
        raise ValueError("invalid bootstrap parameters")
    rng, n, means = random.Random(seed), len(values), []
    for _ in range(reps):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n - block + 1)
            sample.extend(values[start:start + block])
        means.append(statistics.fmean(sample[:n]))
    means.sort()
    alpha = (1 - confidence) / 2
    return [means[int(alpha * (reps - 1))], means[int((1 - alpha) * (reps - 1))]]


def compare_models(rows, champion, challenger, seed):
    """Paired comparison on precisely the same synthetic holdout and costs.

    This is descriptive and cannot promote a model. A final untouched period,
    multiple-testing correction and market-specific controls remain necessary.
    """
    if len(rows) < 120:
        raise ValueError("at least 120 periods are required")
    def metrics(model):
        predictions = [sigmoid(model[0] + model[1] * x) for x, *_ in rows]
        return score(rows, predictions, "model")
    old, new = metrics(champion), metrics(challenger)
    paired = [a - b for a, b in zip(new["daily_returns"], old["daily_returns"])]
    return {"champion": old, "challenger": new,
            "daily_return_delta_ci_95pct": block_bootstrap_mean_ci(paired, seed),
            "verdict": "TESTS INCOMPLETS", "automatic_promotion": False}


def evaluate_scenario(name, seed, sizes=SIZES, train_pool=6000, holdout_size=1000):
    if not sizes or min(sizes) < 1 or max(sizes) > train_pool or holdout_size < 120:
        raise ValueError("invalid train or holdout sizes")
    rows = make_market(name, seed, train_pool + holdout_size)
    holdout = rows[train_pool:]
    baselines = {}
    for baseline in ("flat", "long", "signal_rule"):
        baselines[baseline] = score(holdout, [0.5] * len(holdout), baseline)
    results, previous = [], None
    for size in sizes:
        model = fit_logistic(rows[:size])
        probabilities = [sigmoid(model[0] + model[1] * row[0]) for row in holdout]
        metrics = score(holdout, probabilities, "model")
        differences = [a - b for a, b in zip(metrics["daily_returns"], baselines["flat"]["daily_returns"])]
        metrics["daily_return_difference_vs_flat_95pct_block_ci"] = block_bootstrap_mean_ci(differences, seed + size)
        comparison = compare_models(holdout, previous, model, seed + size + 1) if previous is not None else None
        results.append({"train_size": size, "model": {"intercept": model[0], "weight": model[1]},
                        "metrics": metrics, "comparison_to_previous_size": comparison})
        previous = model
    return {"scenario": name, "seed": seed, "train_pool": train_pool,
            "holdout": [train_pool, train_pool + holdout_size],
            "generator": {"regime_transition_index": 6000, "steps_per_day": 24,
                          "defaults": {"coefficient": 0.003, "drift": 0.0, "sigma": 0.004,
                                       "cost_per_side": 0.0005, "return_clip": [-0.95, 0.95]},
                          "deviations": GENERATOR_DEVIATIONS[name],
                          "cost_model": "per-side proportional cost includes simulated slippage"},
            "baselines": baselines, "learning_curve": results,
            "verdict": "AVANTAGE NET NON DÉMONTRÉ"}


def run_experiment(seeds=(17, 29, 43), scenarios=SCENARIOS, sizes=SIZES):
    return {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
            "code_version": code_revision(), "data": "synthetic; never live",
            "lab_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "reference_model": "one-feature logistic regression; not the production model",
            "initial_eur": INITIAL, "seeds": list(seeds), "train_sizes": list(sizes),
            "notes": ["Fixed holdout reused across sizes; never use it for final strategy selection.",
                      "Costs and market structure are stylised, not observed quotes.",
                      "Overlapping model trials and multiple scenarios invalidate naive 95% claims.",
                      "Bootstrap assumes local dependence; regime breaks and short samples limit inference.",
                      "The signal_rule baseline is a synthetic feature rule, not a real trend-following strategy.",
                      "No automatic promotion or live trading is implemented."],
            "results": [evaluate_scenario(name, seed, sizes) for name in scenarios for seed in seeds]}


def code_revision():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def write_reports(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    (directory / "report.json").write_text(json_text, encoding="utf-8")
    with (directory / "learning_curve.csv").open("w", encoding="utf-8", newline="") as f:
        columns = ["scenario", "seed", "train_size", "brier", "auc", "n_trades", "return_pct", "max_drawdown_pct"]
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for result in report["results"]:
            for point in result["learning_curve"]:
                m = point["metrics"]
                w.writerow({"scenario": result["scenario"], "seed": result["seed"], "train_size": point["train_size"],
                            **{k: m[k] for k in columns[3:]}})
    lines = ["# Synthetic learning benchmark", "", f"Code: {report['code_version']}",
             f"JSON SHA-256: {hashlib.sha256(json_text.encode()).hexdigest()}", "",
             "These numbers evaluate a reference model, not TRADER AI. No claim of live-market profitability.", "",
             "| Scenario | Seed | Train | Brier | AUC | Trades | Return % | Drawdown % |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for result in report["results"]:
        for point in result["learning_curve"]:
            m = point["metrics"]
            auc = f"{m['auc']:.3f}" if m["auc"] is not None else "n/a"
            lines.append(f"| {result['scenario']} | {result['seed']} | {point['train_size']} | {m['brier']:.3f} | {auc} | {m['n_trades']} | {m['return_pct']:.2f} | {m['max_drawdown_pct']:.2f} |")
    lines += ["", "## Limitations", *[f"- {n}" for n in report["notes"]], "", "Verdict: AVANTAGE NET NON DÉMONTRÉ"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument("--scenario", choices=SCENARIOS, action="append")
    parser.add_argument("--seed", type=int, action="append")
    args = parser.parse_args()
    report = run_experiment(tuple(args.seed or (17, 29, 43)), tuple(args.scenario or SCENARIOS))
    write_reports(report, args.output)
    print(f"{len(report['results'])} synthetic scenarios saved to {args.output}")


if __name__ == "__main__":
    main()
