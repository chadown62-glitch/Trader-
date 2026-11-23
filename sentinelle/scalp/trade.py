"""Gestion d'un trade fictif comme un professionnel, sur les bougies d'une minute.

- Entrée à l'ouverture de la minute qui suit la décision ; annulée si cette ouverture est déjà sous le stop.
- Objectif : entrée + 2 fois le risque réel (vente à cours limité, exécutée au prix de l'objectif).
- Protection : dès que le prix atteint +1 R, le stop remonte au point mort, frais compris. Ce déplacement ne vaut
  qu'à partir de la minute suivante (on ignore l'ordre des prix à l'intérieur d'une minute).
- Stop et objectif dans la même minute : le stop est retenu. Ouverture sous le stop : exécution à l'ouverture.
- Durée maximale atteinte : sortie à la clôture. Une minute manquante rend le trade invalide.
Résultat net de frais et de glissement, en pourcentage et en R (multiple du risque).
"""

from __future__ import annotations

import numpy as np

from .watchers import REWARD_R, ROUND_TRIP

STOP, TARGET, BREAKEVEN, TIME, INVALID = "stop", "objectif", "point mort", "durée", "invalide"


def _first(mask: np.ndarray) -> int:
    i = int(np.argmax(mask)) if len(mask) else 0
    return i if len(mask) and mask[i] else 10**9


def manage(o, h, l, c, minute: int, stop: float, max_minutes: int, *, reward_r: float = REWARD_R,
           round_trip: float = ROUND_TRIP) -> dict:
    n = len(o)
    if minute >= n or not np.isfinite(o[minute]):
        return {"outcome": INVALID}
    entry = float(o[minute])
    risk = entry - stop
    if risk <= 0:
        return {"outcome": INVALID}
    end = min(n, minute + max_minutes)
    hh, ll, oo, cc = h[minute:end], l[minute:end], o[minute:end], c[minute:end]
    missing = ~(np.isfinite(hh) & np.isfinite(ll) & np.isfinite(oo) & np.isfinite(cc))
    target, trigger = entry + reward_r * risk, entry + risk
    be = entry * (1 + round_trip)
    first_missing = _first(missing)
    s0, b, t = _first(ll <= stop), _first(hh >= trigger), _first(hh >= target)
    if min(s0, b, t) >= first_missing:
        if first_missing < 10**9:
            return {"outcome": INVALID}
        k = len(cc) - 1
        if end - minute < max_minutes:
            return {"outcome": INVALID}  # historique trop court pour connaître l'issue
        return _result(entry, float(cc[k]), risk, TIME, minute + k, round_trip)
    if s0 <= b:
        k = s0
        return _result(entry, min(stop, float(oo[k])), risk, STOP, minute + k, round_trip)
    if t == b:
        return _result(entry, target, risk, TARGET, minute + t, round_trip)
    after = slice(b + 1, None)
    s1, t1 = _first(ll[after] <= be) + b + 1, _first(hh[after] >= target) + b + 1
    k = min(s1, t1)
    if k >= first_missing:
        if first_missing < 10**9:
            return {"outcome": INVALID}
        if end - minute < max_minutes:
            return {"outcome": INVALID}
        k = len(cc) - 1
        return _result(entry, float(cc[k]), risk, TIME, minute + k, round_trip)
    if s1 <= t1:
        return _result(entry, min(be, float(oo[k])), risk, BREAKEVEN, minute + k, round_trip)
    return _result(entry, target, risk, TARGET, minute + k, round_trip)


def _result(entry, exit_price, risk, outcome, exit_minute, round_trip):
    net = 0.0 if outcome == BREAKEVEN and exit_price == entry * (1 + round_trip) else exit_price / entry - 1 - round_trip
    net = round(net, 12)  # un point mort ne devient jamais un gain par un résidu d'arrondi
    return {"outcome": outcome, "entry": entry, "exit": exit_price, "exit_minute": int(exit_minute),
            "net_return": net, "r": net / (risk / entry), "risk_pct": risk / entry}
