"""Trades fictifs à triple barrière sur bougies d'une minute, nets de frais, et mesure de référence.

Règles (research/scalp-v1/protocol.json) :
- décision à la clôture de la bougie i, entrée à l'ouverture de la bougie i+1 ;
- objectif (vente à cours limité, exécutée au prix de l'objectif) et stop surveillés dès la bougie d'entrée ;
- stop et objectif touchés dans la même bougie : le stop est retenu (on ne sait pas lequel est venu d'abord) ;
- ouverture sous le stop : exécution à l'ouverture, plus basse que le stop ;
- sinon sortie à la clôture de la dernière bougie de la durée maximale ;
- une minute manquante pendant le trade le rend invalide : il est écarté, jamais deviné.

Lancement : python -m sentinelle.scalp.barriers --out research/scalp-v1/baseline.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

TARGET, STOP, TIME, INVALID = 1, -1, 0, -2


@dataclass(frozen=True)
class Costs:
    fee_per_side: float = 0.001
    slippage_per_side: float = 0.0001

    @property
    def round_trip(self) -> float:
        return 2 * (self.fee_per_side + self.slippage_per_side)


@dataclass(frozen=True)
class Barrier:
    target_pct: float
    stop_pct: float
    max_minutes: int


def breakeven_win_rate(barrier: Barrier, costs: Costs) -> float:
    """Part de trades gagnants nécessaire pour ne rien perdre, si chaque trade finit à l'objectif ou au stop."""
    t, s = barrier.target_pct / 100, barrier.stop_pct / 100
    return (s + costs.round_trip) / (t + s)


def simulate(o, h, l, c, decisions, barrier: Barrier, costs: Costs) -> dict:
    """Simule un trade par indice de décision. Renvoie issue, rendement brut et net, et bougie de sortie."""
    o, h, l, c = (np.asarray(x, dtype=float) for x in (o, h, l, c))
    i = np.asarray(decisions, dtype=int)
    n, H = len(o), int(barrier.max_minutes)
    i = i[(i >= 0) & (i + H < n)]
    entry = o[i + 1]
    up = entry * (1 + barrier.target_pct / 100)
    dn = entry * (1 - barrier.stop_pct / 100)
    outcome = np.full(len(i), TIME)
    gross = np.full(len(i), np.nan)
    exit_at = i + H
    live = np.isfinite(entry)
    outcome[~live] = INVALID
    for k in range(1, H + 1):
        j = i + k
        missing = live & ~(np.isfinite(h[j]) & np.isfinite(l[j]) & np.isfinite(o[j]))
        outcome[missing], exit_at[missing] = INVALID, j[missing]
        live &= ~missing
        stop = live & (l[j] <= dn)
        gross[stop] = np.minimum(dn[stop], o[j][stop]) / entry[stop] - 1
        outcome[stop], exit_at[stop] = STOP, j[stop]
        target = live & ~stop & (h[j] >= up)
        gross[target] = barrier.target_pct / 100
        outcome[target], exit_at[target] = TARGET, j[target]
        live &= ~(stop | target)
    last = i + H
    timed = live & np.isfinite(c[last])
    gross[timed] = c[last][timed] / entry[timed] - 1
    outcome[live & ~timed] = INVALID
    net = gross - costs.round_trip
    return {"decision": i, "outcome": outcome, "gross": gross, "net": net, "exit": exit_at}


def _bootstrap_mean(values: np.ndarray, groups: np.ndarray, reps: int = 1000, seed: int = 0) -> list[float]:
    """Intervalle à 95 % de la moyenne, en rééchantillonnant des journées entières (dépendance temporelle)."""
    keys, inv = np.unique(groups, return_inverse=True)
    sums, counts = np.bincount(inv, values), np.bincount(inv)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(reps, len(keys)))
    means = sums[draws].sum(1) / counts[draws].sum(1)
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def summarize(result: dict, barrier: Barrier, costs: Costs, t_ms=None) -> dict:
    ok = result["outcome"] != INVALID
    out, net = result["outcome"][ok], result["net"][ok]
    row = {"barrier": asdict(barrier), "trades": int(ok.sum()), "invalid": int((~ok).sum()),
           "target_share": float(np.mean(out == TARGET)), "stop_share": float(np.mean(out == STOP)),
           "time_share": float(np.mean(out == TIME)), "mean_net": float(net.mean()),
           "net_positive_share": float(np.mean(net > 0)), "breakeven_win_rate": breakeven_win_rate(barrier, costs)}
    if t_ms is not None:
        days = np.asarray(t_ms)[result["decision"][ok]] // 86_400_000
        row["mean_net_ci95_daily_blocks"] = _bootstrap_mean(net, days)
    return row


def baseline(bars, barriers, costs: Costs, stride: int = 5) -> dict:
    """Entrées au hasard (toutes les « stride » minutes) : ce qu'un signal doit battre, frais compris."""
    o, h, l, c, t = (bars[k].to_numpy(float) for k in ("o", "h", "l", "c", "t"))
    decisions = np.arange(0, len(o), stride)
    rows = [summarize(simulate(o, h, l, c, decisions, b, costs), b, costs, t) for b in barriers]
    starts = np.arange(0, len(o) - 61, 60)
    valid = np.array([np.isfinite(h[s + 1:s + 61]).all() and np.isfinite(o[s + 1]) for s in starts], bool)
    peak = np.array([np.nanmax(h[s + 1:s + 61]) / o[s + 1] - 1 if v else np.nan for s, v in zip(starts, valid)])
    peak = peak[valid]
    return {"grid": rows, "hours": int(len(peak)), "hour_peak_ge_1pct": float(np.mean(peak >= 0.01)),
            "hour_peak_ge_2pct": float(np.mean(peak >= 0.02))}


def main(argv=None) -> dict:
    from .data import load_bars

    ap = argparse.ArgumentParser(description="Mesure de référence du trading court (simulation exclusive)")
    ap.add_argument("--symbols", default="BTCUSDC,ETHUSDC,SOLUSDC")
    ap.add_argument("--first", default="2025-09")
    ap.add_argument("--last", default="2026-08")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--protocol", default="research/scalp-v1/protocol.json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"))
    barriers = [Barrier(**b) for b in protocol["baseline_grid"]]
    cost = protocol["costs"]
    variants = {"standard": Costs(cost["fee_per_side"], cost["slippage_per_side"]),
                "bnb": Costs(cost["fee_per_side_bnb"], cost["slippage_per_side"])}
    report = {"schema": "scalp-baseline-v1", "protocol": args.protocol, "rules": protocol["execution_rules"],
              "costs": {k: {**asdict(v), "round_trip": v.round_trip} for k, v in variants.items()},
              "entries": "une décision toutes les 5 minutes, sans aucun signal", "markets": {}}
    for symbol in args.symbols.split(","):
        bars, integrity = load_bars(symbol, args.first, args.last, args.cache)
        report["markets"][symbol] = {"integrity": integrity,
                                     **{k: baseline(bars, barriers, v) for k, v in variants.items()}}
        for row in report["markets"][symbol]["standard"]["grid"]:
            b = row["barrier"]
            print(f"{symbol} +{b['target_pct']}%/-{b['stop_pct']}%/{b['max_minutes']} min : gagnés {row['target_share']:.1%}, "
                  f"net moyen {row['mean_net']:+.3%}, seuil {row['breakeven_win_rate']:.1%}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
