"""Situations d'apprentissage : décisions des enseignants + issues datées + découpage chronologique purgé.

Une situation = un enseignant qui propose (ou refuse, pour cause d'abstention) une entrée à la
clôture d'une bougie. On y attache :
- les caractéristiques connues à cet instant (``bars.feature_row``) ;
- l'issue du plan exécuté selon le contrat, disponible seulement à ``available_at`` ;
- les issues contrefactuelles de variantes de sortie (même entrée, autre sortie) : elles
  apprennent à mieux SORTIR, et pour les entrées refusées elles apprennent si le refus était juste.
  Ce sont des contrefactuels : jamais des gains réalisés.

Les jeux d'apprentissage / calibration / sélection / évaluation sont disjoints dans le temps,
avec embargo et purge : une situation dont l'issue n'est connue qu'après la fin de sa fenêtre en
est retirée (et comptée dans ``purged``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .bars import FEATURES, Universe, feature_row, indicators
from .execution import CostModel, Outcome, simulate_trade
from .teachers import EXIT_VARIANTS, TEACHER_NAMES, TEACHERS, abstention_mask, exit_signals

SPLITS = ("train", "calibration", "selection", "evaluation")


@dataclass
class Situations:
    symbol: np.ndarray
    teacher: np.ndarray
    index: np.ndarray
    decided_at: np.ndarray  # ms
    available_at: np.ndarray  # ms (issue de la variante « teacher ») ; inf si non résolue
    vetoed: np.ndarray  # bool : l'enseignant d'abstention a refusé l'entrée
    X: np.ndarray
    net_r: dict[str, np.ndarray]  # par variante de sortie ; NaN si non résolue
    net_return: dict[str, np.ndarray]
    avail: dict[str, np.ndarray]
    regime: np.ndarray  # vérité du marché synthétique (rapport seulement) ; -1 si inconnue
    decisions: list = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    variant_outcomes: dict[str, list[Outcome]] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.index)

    def take(self, mask: np.ndarray) -> "Situations":
        idx = np.flatnonzero(mask)
        return Situations(self.symbol[idx], self.teacher[idx], self.index[idx], self.decided_at[idx], self.available_at[idx],
                          self.vetoed[idx], self.X[idx], {k: v[idx] for k, v in self.net_r.items()},
                          {k: v[idx] for k, v in self.net_return.items()}, {k: v[idx] for k, v in self.avail.items()},
                          self.regime[idx], [self.decisions[i] for i in idx], [self.outcomes[i] for i in idx],
                          {k: [v[i] for i in idx] for k, v in self.variant_outcomes.items()})

    def teacher_onehot(self) -> np.ndarray:
        return np.stack([(self.teacher == t).astype(float) for t in TEACHER_NAMES], axis=1)

    def design(self) -> np.ndarray:
        """Matrice d'entrée des modèles : caractéristiques as_of + identité de l'enseignant."""
        return np.concatenate([self.X, self.teacher_onehot()], axis=1)


def build_situations(universe: Universe, cost: CostModel = CostModel(), *, variants: tuple[str, ...] = EXIT_VARIANTS) -> Situations:
    rows = []
    for sym in universe.symbols:
        b = universe.bars[sym]
        ind = indicators(b, cost.default_spread_bps)
        veto, veto_reasons = abstention_mask(b, ind)
        exits = exit_signals(b, ind)
        regimes = universe.truth.get("regimes")
        for t in TEACHERS:
            for d in t.decisions(b, ind, veto, veto_reasons):
                outs = {}
                for v in variants:
                    plan = t.plan(b, ind, d.index, v)
                    sig = exits.get(plan.exit_rule) if plan.exit_rule else None
                    outs[v] = simulate_trade(b, ind, d.index, plan, cost, sig)
                rows.append((sym, t.name, d, feature_row(b, ind, d.index), outs,
                             int(regimes[d.index]) if regimes is not None else -1))
    rows.sort(key=lambda r: (r[2].decided_at, r[0], r[1]))
    n = len(rows)

    def arr(f, dtype=float):
        return np.array([f(r) for r in rows], dtype=dtype) if n else np.zeros(0, dtype=dtype)

    def res(o: Outcome, attr: str):
        v = getattr(o, attr)
        return np.nan if (not o.resolved or v is None) else float(v)

    net_r = {v: arr(lambda r, v=v: res(r[4][v], "net_r")) for v in variants}
    net_ret = {v: arr(lambda r, v=v: res(r[4][v], "net_return")) for v in variants}
    avail = {v: arr(lambda r, v=v: float(r[4][v].available_at) if r[4][v].resolved else np.inf) for v in variants}
    return Situations(
        symbol=arr(lambda r: r[0], object), teacher=arr(lambda r: r[1], object), index=arr(lambda r: r[2].index, int),
        decided_at=arr(lambda r: r[2].decided_at, np.int64), available_at=avail["teacher"].copy(),
        vetoed=arr(lambda r: r[2].action == "skip", bool),
        X=np.array([r[3] for r in rows]) if n else np.zeros((0, len(FEATURES))),
        net_r=net_r, net_return=net_ret, avail=avail, regime=arr(lambda r: r[5], int),
        decisions=[r[2] for r in rows], outcomes=[r[4]["teacher"] for r in rows],
        variant_outcomes={v: [r[4][v] for r in rows] for v in variants},
    )


@dataclass(frozen=True)
class Windows:
    bounds: dict[str, tuple[int, int]]  # [début, fin] en ms (décisions)
    embargo_ms: int

    def to_dict(self) -> dict:
        return {"bounds": {k: list(v) for k, v in self.bounds.items()}, "embargo_ms": self.embargo_ms}


def time_windows(universe: Universe, fractions=(0.5, 0.15, 0.15, 0.2), *, embargo_bars: int = 30) -> Windows:
    """Fenêtres calendaires fixées par la grille de temps, pas par les données (déterministe)."""
    ts = universe.ts
    tf = universe.bars[universe.symbols[0]].tf_ms
    n = len(ts)
    cuts = np.round(np.cumsum((0,) + tuple(fractions)) * n).astype(int)
    bounds = {}
    for k, name in enumerate(SPLITS):
        lo, hi = cuts[k], min(cuts[k + 1], n) - 1
        bounds[name] = (int(ts[lo]) + tf, int(ts[hi]) + tf)
    return Windows(bounds, embargo_bars * tf)


def split(s: Situations, w: Windows) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Masques disjoints ; une issue doit être connue avant la fin de sa fenêtre, sinon purge."""
    masks = {}
    used = np.zeros(len(s), dtype=bool)
    for k, name in enumerate(SPLITS):
        lo, hi = w.bounds[name]
        if k > 0:
            lo = lo + w.embargo_ms
        m = (s.decided_at >= lo) & (s.decided_at <= hi) & (s.available_at <= hi)
        masks[name] = m & ~used
        used |= masks[name]
    return masks, ~used
