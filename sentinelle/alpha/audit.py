"""Audit de causalité par troncature (équipe rouge automatique).

Une grandeur calculée à l'indice ``i`` est causale si elle ne change pas quand on retire
toutes les bougies postérieures à ``i``. On la recalcule donc sur ``bars.prefix(i + 1)`` et on
compare. Une colonne qui « regarde » le futur (même d'une seule bougie) est détectée.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .bars import Bars


def prefix_violations(fn: Callable[[Bars], np.ndarray], bars: Bars, checkpoints, *, atol: float = 1e-9) -> list[int]:
    """Indices où ``fn(bars)[i]`` diffère de ``fn(bars.prefix(i + 1))[i]`` (NaN égal à NaN)."""
    full = np.asarray(fn(bars), dtype=float)
    bad = []
    for i in checkpoints:
        part = np.asarray(fn(bars.prefix(i + 1)), dtype=float)
        a, b = full[i], part[i] if i < len(part) else np.nan
        same = (np.isnan(a) and np.isnan(b)) or (np.isfinite(a) and np.isfinite(b) and abs(a - b) <= atol * max(1.0, abs(a)))
        if not same:
            bad.append(int(i))
    return bad
