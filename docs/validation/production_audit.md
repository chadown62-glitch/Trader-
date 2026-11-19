# Static quantitative audit of Sentinelle at 5fa6b365

This is a code review, not a reproduced historical experiment. No source code
of the production engine was modified. Verify against the exact commit before
applying any finding to a later revision.

## 1. Boundary leakage risk in threshold selection

In `sentinelle/ai/trainer.py` lines 183–186, `dev` is selected by
`decision_ts < hold_start`, without requiring `exit_ts < hold_start`.
Lines 219–224 compute `payoff_table(dev)` and then select a threshold using the
resulting expected returns. Therefore, **if a development trade is opened
before the boundary but closes after it**, its future outcome can affect
threshold selection for the holdout. This is a code-path finding; the number
of affected rows and resulting performance distortion have not been measured.
The final evaluation model itself is fitted on the purged `eval_train` at
line 232, but that does not undo a threshold already selected with boundary-
crossing outcomes. Purge labels used for every learned quantity before the
boundary and add an explicit test with an event crossing it.

## 2. Calibration and threshold estimates reuse OOS labels

The calibration fit and threshold selection both use the same out-of-fold
predictions and outcomes (`trainer.py` lines 214–228). These predictions were
out-of-sample for the fitted classifiers, but calibration metrics on those same
labels are in-sample with respect to the calibration step and threshold search.
`payoff_table(dev)` also includes outcomes later than the early folds. Treat
these reported metrics as selection metrics, not independent forward results;
evaluate the entire fitted pipeline on untouched data, with nested temporal
splits and any needed purge/embargo. Repeated research on a holdout also
requires a new final test period and control of multiple testing.

## 3. Dependence and confidence

`sentinelle/ai/metrics.py` resamples individual R values with replacement for
its reported confidence interval and uses `mean - 1.645 * standard_error` in
`proven_positive`. The former assumes individual resampling is appropriate;
the latter is a one-sided normal approximation, not proof of a 95% probability
of future profit. Financial observations may overlap or be correlated across
assets and time. Report assumptions and use time-blocked / clustered sensitivity
analysis. The independent block bootstrap utility in this PR is illustrative;
it has not yet been wired into the production evaluation.

## 4. Unequal risk size in portfolio baselines

`sentinelle/services/training.py` lines 89–93 compares an evaluated model with
`exploration=True` when the model is absent. In `sentinelle/agent/agent.py`
lines 297–312, model and exploration decisions may use different risk factors:
exploration uses `ai.exploration_risk_factor`, whose default is 0.5, while a
validated model uses 1.0. Under those conditions, portfolio returns and drawdown
are not directly attributable solely to the model's trade selection. Reproduce
with identical sizing, costs, period and execution rules; report both versions.

## 5. Historical claims not reproduced

`docs/RESULTATS.md` documents an out-of-period 2018–2022 result and subsequent
studies. Exact input datasets and reproducible experiment artifacts were not
provided in this review. Treat those figures as project-reported, not
independently reproduced. Separately verify the asset universe as of each date,
historical source and revisions, fees, selected strategies, and any repeated
use of that period. An earlier period not used initially may cease to be
independent if later used to choose strategies.

## Next verification steps

1. Construct an integration test in which a development candidate closes
   inside the holdout, perturb only that outcome, and assert that all
   pre-holdout model parameters, calibration and threshold are unchanged.
2. Log all experiment IDs, data hashes, splits, effective risk factors, costs,
   seeds and code versions; reserve an untouched final temporal evaluation.
3. Compare paired daily portfolio results with equal sizing, then repeat with
   several block lengths, instruments and independently chosen periods.
4. Execute in an isolated environment with no credentials or private exchange
   access. Do not treat this static review or the synthetic laboratory as
   evidence of profitability, or as authorization to trade real money.
