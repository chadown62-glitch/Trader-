"""Amplitude des ranges (C036) et épuisement de mouvement (C039).

Calculs causaux : ligne d = données jusqu'à la clôture de d, position prise à l'ouverture de d + 1. Paramètres fixés a
priori, univers à la date (signaux.members), historique 2018-2026 déjà consulté.
C036. L'amplitude du range sur 20 jours ((plus haut − plus bas) / clôture) prédit-elle l'ampleur du mouvement des
5 jours suivants (|log(c[d+5] / c[d])|) mieux que la volatilité réalisée sur 20 jours, déjà utilisée (C024, C076) ?
Chaque jour, corrélation de rang entre les membres (au moins 10) ; moyenne sur les jours ; écart amplitude − volatilité ;
p bilatérale par blocs circulaires de 20 jours (horizons de 5 jours chevauchants).
C039. Capitulation d'un membre : rendement sur 5 jours ≤ −3 écarts types (volatilité journalière sur 20 jours × √5) et
volume du jour ≥ 2 fois sa médiane sur 20 jours ; position à l'ouverture suivante, tenue 5 jours (signal actif 5 lignes).
Comparée à l'outil d'ablation C095 (0,125 %/côté), Holm sur les 2 :
- E1 : entrées ajoutées à B0 (B0 ou capitulation) contre B0 ;
- E2 : capitulation seule contre liquidités.
Témoin : le même signal décalé d'un an pour chaque actif.
Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.epuisement
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import ablation, essais, moteur, recherche, signaux, techniques

RANGE_DAYS, VOL_DAYS, AHEAD, MIN_MEMBERS, BLOCK = 20, 20, 5, 10, 20
DROP_DAYS, DROP_Z, VOLUME_X, HOLD = 5, -3.0, 2.0, 5
SHIFT_DAYS = 365


def predictors(p: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Amplitude du range sur 20 jours, volatilité réalisée sur 20 jours (connues à la clôture de d) et ampleur des
    5 jours suivants (cible, jamais utilisée comme entrée)."""
    h, lo, c = p["h"], p["l"], p["c"]
    amplitude = (h.rolling(RANGE_DAYS, min_periods=RANGE_DAYS).max() - lo.rolling(RANGE_DAYS, min_periods=RANGE_DAYS).min()) / c
    vol = np.log(c).diff().rolling(VOL_DAYS, min_periods=VOL_DAYS).std()
    ahead = (np.log(c).shift(-AHEAD) - np.log(c)).abs()
    return amplitude, vol, ahead


def daily_rank_ic(x: pd.DataFrame, y: pd.DataFrame, member: pd.DataFrame) -> pd.Series:
    """Corrélation de rang transversale de chaque jour entre x et y sur les membres valides (au moins MIN_MEMBERS)."""
    ok = member & x.notna() & y.notna()
    rx, ry = x.where(ok).rank(axis=1), y.where(ok).rank(axis=1)
    n = ok.sum(axis=1)
    cx, cy = rx.sub(rx.mean(axis=1), axis=0), ry.sub(ry.mean(axis=1), axis=0)
    ic = (cx * cy).sum(axis=1) / np.sqrt((cx ** 2).sum(axis=1) * (cy ** 2).sum(axis=1))
    return ic.where(n >= MIN_MEMBERS).dropna()


def two_sided(x: np.ndarray) -> float:
    return min(1.0, 2 * min(essais.bootstrap_pvalue(x, block=BLOCK), essais.bootstrap_pvalue(-x, block=BLOCK)))


def amplitude_study(p: dict, member: pd.DataFrame, first: int) -> dict:
    amplitude, vol, ahead = predictors(p)
    ia = daily_rank_ic(amplitude, ahead, member).loc[first:]
    iv = daily_rank_ic(vol, ahead, member).loc[first:]
    both = pd.concat([ia, iv], axis=1, keys=["a", "v"]).dropna()
    diff = (both["a"] - both["v"]).to_numpy()
    return {"jours": int(len(both)), "correlation_amplitude": float(both["a"].mean()), "p_amplitude": two_sided(both["a"].to_numpy()),
            "correlation_volatilite": float(both["v"].mean()), "ecart_amplitude_moins_volatilite": float(diff.mean()),
            "p_ecart": two_sided(diff)}


def capitulation(p: dict) -> pd.DataFrame:
    """Évènement de capitulation à la clôture de d (rendement sur 5 jours ≤ −3 écarts types, volume ≥ 2 × médiane)."""
    c, v = p["c"], p["v"]
    lr = np.log(c).diff()
    sigma = lr.rolling(VOL_DAYS, min_periods=VOL_DAYS).std().shift(DROP_DAYS)  # volatilité connue avant la chute
    z = (np.log(c) - np.log(c.shift(DROP_DAYS))) / (sigma * np.sqrt(DROP_DAYS))
    spike = v >= VOLUME_X * v.rolling(VOL_DAYS, min_periods=VOL_DAYS).median()
    return (z <= DROP_Z) & spike


def hold(event: pd.DataFrame) -> pd.DataFrame:
    """Signal actif sur la ligne de l'évènement et les HOLD − 1 suivantes : HOLD jours de position."""
    return techniques.recent(event.fillna(False), HOLD)


def shifted_frame(mask: pd.DataFrame, days: int = SHIFT_DAYS) -> pd.DataFrame:
    return pd.DataFrame(np.roll(mask.to_numpy(), days, axis=0), index=mask.index, columns=mask.columns)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Amplitude des ranges et épuisement de mouvement (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/epuisement-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    btc = p["c"]["BTCUSDT"]
    amp = amplitude_study(p, member, first)
    event = capitulation(p) & member
    sig = hold(event)
    base = fam["F1_momentum"]

    def run(s):
        return signaux.net(s, member, p, r, signaux.COST)[0].loc[first:]

    ref, cash = run(base), run(pd.DataFrame(False, index=base.index, columns=base.columns))
    rows = ablation.adjust([ablation.compare(ref, run(base | sig), label="E1 C039 capitulation ajoutée à B0", costs="0,125 %/côté", btc_close=btc),
                            ablation.compare(cash, run(sig), label="E2 C039 capitulation seule contre liquidités", costs="0,125 %/côté", btc_close=btc)])
    ctl = shifted_frame(sig)
    controls = [ablation.compare(ref, run(base | ctl), label="témoin décalé d'un an : E1", costs="identiques", btc_close=btc),
                ablation.compare(cash, run(ctl), label="témoin décalé d'un an : E2", costs="identiques", btc_close=btc)]
    span = event.loc[first:]
    out = {"schema": "scalp-epuisement-v1", "status": "mesure sur historique déjà consulté ; paramètres fixés a priori ; aucune intégration",
           "capacites": ["C036", "C039"],
           "parametres": {"amplitude_jours": RANGE_DAYS, "volatilite_jours": VOL_DAYS, "horizon_jours": AHEAD, "membres_minimum": MIN_MEMBERS,
                          "bloc_jours": BLOCK, "chute_jours": DROP_DAYS, "chute_ecarts_types": DROP_Z, "volume_multiple": VOLUME_X,
                          "tenue_jours": HOLD, "decalage_temoin_jours": SHIFT_DAYS},
           "amplitude": amp, "capitulations": {"evenements": int(span.sum().sum()), "jours_avec_evenement": int(span.any(axis=1).sum())},
           "ablations": rows, "temoins": controls, "seconds": round(time.time() - t0)}
    print(f"C036 rang amplitude → ampleur à 5 jours {amp['correlation_amplitude']:+.4f} (p {amp['p_amplitude']:.3f}) ; volatilité {amp['correlation_volatilite']:+.4f} ; "
          f"écart {amp['ecart_amplitude_moins_volatilite']:+.4f} (p {amp['p_ecart']:.3f}, {amp['jours']} jours)")
    print("capitulations :", out["capitulations"])
    for x, c in zip(rows, controls):
        print(f"{x['ablation'][:50]:50} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] Holm {x['p_holm']:.3f} "
              f"| baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} % | témoin {c['ecart_hebdo_moyen_pct']:+.4f}")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "epuisement-v1", "amplitude": amp,
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"], "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]}
                                               for x in rows}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
