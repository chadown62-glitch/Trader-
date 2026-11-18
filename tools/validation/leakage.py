"""Utilities for temporal leakage checks; independent from production models."""
from __future__ import annotations

import math


def purged_split(events, test_start, test_end, embargo=0):
    """Return prior, resolved train indices and chronological test indices.

    Each event must contain decision_ts and exit_ts (same numeric unit). A
    training event must be both decided and resolved strictly before the test
    boundary minus embargo. Test labels are returned only by index; callers
    must not use them for fitting, calibration, thresholds or model selection.
    """
    if not (test_start < test_end and embargo >= 0):
        raise ValueError("invalid temporal boundary or embargo")
    train, test = [], []
    cutoff = test_start - embargo
    for i, event in enumerate(events):
        decision, end = event["decision_ts"], event["exit_ts"]
        if not math.isfinite(decision) or (end is not None and not math.isfinite(end)):
            raise ValueError("timestamps must be finite")
        if end is not None and end < decision:
            raise ValueError("exit precedes decision")
        if decision < cutoff and end is not None and end < cutoff:
            train.append(i)
        if test_start <= decision < test_end:
            test.append(i)
    return {"train_indices": train, "test_indices": test, "cutoff": cutoff}


def prefix_mismatches(transform, rows, checkpoints):
    """Check that features for each prefix match those from the full series.

    This detects many look-ahead errors but is not a proof of no leakage:
    external state, revised data and cross-asset alignment need separate checks.
    The transform must return one feature row per input row.
    """
    full = list(transform(list(rows)))
    if len(full) != len(rows):
        raise ValueError("transform must preserve length")
    failures = []
    for end in checkpoints:
        if not isinstance(end, int) or end <= 0 or end > len(rows):
            raise ValueError("invalid checkpoint")
        part = list(transform(list(rows[:end])))
        if len(part) != end:
            raise ValueError("transform must preserve length")
        for index, (a, b) in enumerate(zip(part, full[:end])):
            if a != b:
                failures.append({"prefix_end": end, "index": index})
    return failures
