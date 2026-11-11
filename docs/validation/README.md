# Synthetic validation laboratory

This is an independent, standard-library-only research scaffold. **It does not
train or evaluate the Sentinelle production model**, does not connect to an
exchange, and cannot approve real-money trading. It tests whether the protocol
can recover a deliberately planted signal, identify noise and include costs.

## Run

From the repository root, with Python 3.11–3.13:

```sh
python -m unittest discover -s tests -p test_validation_lab.py -v
python -m tools.validation.lab --output /tmp/trader-lab --scenario noise --scenario trend --seed 17
```

Omit scenario and seed to run all 15 scenarios with seeds 17, 29 and 43. The
CLI writes JSON, CSV and Markdown reports. JSON contains full generator
metadata, seed, row indices, source hash, model coefficients, predictions
summaries, baselines and paired comparisons. No historical or live data are
downloaded. Keep output outside the repository unless intentionally archived.

## Protocol

- Each row contains one visible, dimensionless feature followed by a return
  generated for the next non-overlapping synthetic period. These are not candles.
- Nested training prefixes have 100, 500, 1,000 and 5,000 rows. Each is fitted
  independently; rows 6,000–6,999 are the same chronological holdout. The
  1,000-row gap after the largest prefix is intentionally unused.
- A fixed logistic reference model predicts the sign of a future return. A
  position opens if the probability is at least 0.55. This threshold, all model
  settings, seeds, sample sizes and generator settings are predeclared.
- Synthetic long-only positions last one period and use 20% of a €30 portfolio;
  proportional costs include spread/slippage assumptions on each side. Each
  trade ends before the next. Twenty-four synthetic periods represent a day
  for reporting only. There is no order book, liquidity constraint or margin.
- Baselines are no position, always invested, and an illustrative rule using
  the feature's sign. The latter is **not** a real trend-following strategy.
  Comparisons across training sizes use the identical rows and cost assumptions.
- Output includes calibration bins, Brier score, AUC, accuracy, trade count,
  refusals, return, drawdown, costs, daily observations and paired moving-block
  bootstrap intervals. A missing AUC or mean trade is `null`, never zero.

## Scenarios

| Name | Generator intent |
|---|---|
| noise | No predictive relationship. |
| trend | Positive relation between feature sign and next return. |
| mean_reversion | Negative relation; a stylised reversal signal. |
| alternating | Alternating predictive and uninformative blocks. |
| high_volatility | Larger random shocks. |
| downtrend | Negative drift with a weak predictive relationship. |
| regime_break | The planted sign reverses at observation 6,000. |
| signal_disappears | The planted relationship disappears at 6,000. |
| signal_inverts | The planted relationship reverses more strongly at 6,000. |
| spurious_correlation | Training correlation disappears in the holdout. |
| delayed_data | Returns depend on the prior feature, not the observed one. |
| increasing_noise | Noise increases through time. |
| high_cost | Two-sided costs exceed the small gross signal. |
| low_liquidity | Larger illustrative cost and volatility; no order-book model. |
| stay_flat | Negative drift and no signal; flat is optimal in expectation. |

Signal disappearance and spurious correlation currently share a generator;
their interpretation differs, and a richer independent confounder is future work.

## Statistical boundaries

The same holdout across training sizes makes comparisons paired, **but repeated
selection based on it invalidates its role as a final unseen evaluation**. Use
separate, untouched final periods and pre-register selection rules before any
claim. Overlapping trials, correlated assets and time dependence invalidate
independent-trade confidence assumptions. The 95% moving-block interval is a
conditional estimate with a fixed five-day block, not a guarantee, and may be
unreliable under regime change. Repeat across seeds, blocks and market periods.

Comparisons are descriptive: `automatic_promotion` is always false. Results
must never enable real trading. Historic results in `docs/RESULTATS.md` are not
reproduced here: source datasets, their exact versions and settings must first
be made available and verified. The current lab does not validate portfolio
migration, EUR conversion, execution parity, actual market data, or Claude's
paper-only lock. Those require separate integration tests after the respective
production interfaces exist.

The test suite itself imports no network or brokerage libraries. For tests of
the actual application and security suite, install dependencies separately and
run in an OS/container environment with outbound network disabled and no
credentials. Socket monkeypatches alone do not prove isolation.
