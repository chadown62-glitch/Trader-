"""Reproduce a temporal validation risk in the actual training pipeline."""
from sentinelle.ai import trainer
from sentinelle.ai.bench import make_candidates


def test_payoff_estimates_include_trade_resolved_inside_holdout(monkeypatch):
    candidates, _ = make_candidates(1800, 'simple', seed=19)
    t0, t1 = int(candidates.decision_ts.min()), int(candidates.decision_ts.max())
    hold_start = int(t0 + (t1 - t0) * 0.8)
    index = candidates.index[candidates.decision_ts < hold_start][-1]
    candidates.loc[index, 'symbol'] = 'BOUNDARY/USDC'
    candidates.loc[index, 'exit_ts'] = hold_start + 1
    candidates.loc[index, 'outcome'] = 'target'
    original = trainer.payoff_table
    observed = []
    def capture(data):
        result = original(data)
        boundary = data[data.symbol == 'BOUNDARY/USDC']
        observed.append((boundary.copy(), result))
        return result
    monkeypatch.setattr(trainer, 'payoff_table', capture)
    candidates.loc[index, 'outcome_r'] = 1.0
    trainer.train_walk_forward(candidates, '4h', with_importance=False)
    candidates.loc[index, 'outcome_r'] = 100.0
    trainer.train_walk_forward(candidates, '4h', with_importance=False)
    assert len(observed) == 2
    assert len(observed[0][0]) == len(observed[1][0]) == 1
    assert observed[0][0].iloc[0].decision_ts < hold_start <= observed[0][0].iloc[0].exit_ts
    assert observed[0][1]['_all'] != observed[1][1]['_all']


def test_calibration_and_threshold_selection_reuse_same_oos_labels(monkeypatch):
    import numpy as np
    candidates, _ = make_candidates(1800, 'simple', seed=20)
    observed = {}
    original_calibration = trainer.MetaModel.fit_calibration
    original_threshold = trainer.select_threshold
    def capture_calibration(model, probabilities, labels):
        observed['calibration'] = np.array(labels, copy=True)
        return original_calibration(model, probabilities, labels)
    def capture_threshold(oos, er, forced=None):
        observed['selection'] = (oos['outcome_r'].to_numpy() > 0).astype(int)
        return original_threshold(oos, er, forced)
    monkeypatch.setattr(trainer.MetaModel, 'fit_calibration', capture_calibration)
    monkeypatch.setattr(trainer, 'select_threshold', capture_threshold)
    result = trainer.train_walk_forward(candidates, '4h', with_importance=False)
    assert result.holdout_start is not None
    assert len(observed['calibration']) > 0
    assert np.array_equal(observed['calibration'], observed['selection'])
