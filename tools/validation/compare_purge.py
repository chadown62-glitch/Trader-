"""Measure two trainer revisions on the identical predeclared cohort, in separate processes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.checkout.resolve()))
    from sklearn.metrics import roc_auc_score
    from sentinelle.ai.bench import make_candidates
    from sentinelle.ai.labeling import non_overlapping
    from sentinelle.ai.trainer import train_walk_forward

    results = []
    for seed in (19, 20):
        rows, _ = make_candidates(1800, "simple", seed=seed)
        result = train_walk_forward(rows, "4h", with_importance=False)
        cohort = rows[(rows.decision_ts >= result.holdout_start) & (rows.exit_ts < rows.decision_ts.max())]
        probabilities, edge = result.eval_model.predict(cohort)
        y = cohort.outcome_r.gt(0)
        take = non_overlapping(cohort, edge >= result.eval_model.threshold)
        signature = cohort[["decision_ts", "symbol", "outcome_r"]].to_csv(index=False)
        results.append({"seed":seed, "decisions":len(cohort), "trades":int(take.sum()),
                        "mean_r":float(cohort.loc[take, "outcome_r"].mean()),
                        "auc":float(roc_auc_score(y, probabilities)), "brier":float(((y-probabilities)**2).mean()),
                        "cohort_sha256":hashlib.sha256(signature.encode()).hexdigest()})
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
