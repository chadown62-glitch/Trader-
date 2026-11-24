"""Régimes de marché et abstention (lot 2 : C021, C024, C026, C030).

Classificateurs causaux, calculés à la clôture du jour d et utilisés à l'ouverture de d + 1, sans paramètre ajusté sur
les données :
- C021 tendance/range : rendement d'efficacité de Kaufman du BTC sur 30 jours (|variation nette| / somme des variations
  absolues) ; « tendance » au-delà de 0,3, sinon « range » ; sens donné par la variation nette ;
- C024 volatilité : volatilité réalisée du BTC sur 20 jours, rangée en terciles de son propre historique connu à la date
  (fenêtre croissante, au moins 365 jours) ;
- C026 corrélation : corrélation moyenne sur 30 jours des membres de l'univers avec le BTC, rangée de la même façon.
Règles d'abstention (C030) fixées avant tout calcul : la poche crypto reste en liquidités quand la règle est vraie. Chaque
règle est comparée à la stratégie sans abstention (outil d'ablation C095, Holm sur le lot) et à un témoin : le même masque
décalé d'un an, qui garde la part et la durée des abstentions mais rompt leur calendrier.
Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.regimes
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

from . import ablation, moteur, recherche, signaux

ER_WINDOW, ER_TREND = 30, 0.3
VOL_WINDOW, CORR_WINDOW, MIN_HISTORY_DAYS = 20, 30, 365
SHIFT_DAYS = 365  # décalage du témoin


def efficiency_ratio(close: pd.Series, n: int = ER_WINDOW) -> pd.Series:
    """|c_t − c_{t−n}| / Σ |c_i − c_{i−1}| sur n jours : 1 = mouvement rectiligne, 0 = aller-retour."""
    path = close.diff().abs().rolling(n).sum()
    return (close - close.shift(n)).abs() / path.replace(0.0, np.nan)


def expanding_tercile(x: pd.Series, min_history: int = MIN_HISTORY_DAYS) -> pd.Series:
    """Tercile (0 bas, 1 moyen, 2 haut) de chaque valeur parmi les valeurs connues jusqu'à ce jour inclus ; NaN avant
    « min_history » observations. Aucune valeur future n'entre dans le seuil."""
    values = x.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    seen: list[float] = []
    for i, v in enumerate(values):
        if np.isnan(v):
            continue
        seen.append(v)
        if len(seen) >= min_history:
            q1, q2 = np.quantile(seen, [1 / 3, 2 / 3])
            out[i] = 0.0 if v <= q1 else (1.0 if v <= q2 else 2.0)
    return pd.Series(out, index=x.index)


def btc_correlation(c: pd.DataFrame, member: pd.DataFrame, n: int = CORR_WINDOW) -> pd.Series:
    """Corrélation moyenne, sur n jours, des rendements des membres de l'univers (hors BTC) avec ceux du BTC."""
    r = np.log(c).diff()
    btc = r["BTCUSDT"]
    corr = r.drop(columns="BTCUSDT").rolling(n, min_periods=n).corr(btc)
    others = member.drop(columns="BTCUSDT", errors="ignore").reindex(columns=corr.columns, fill_value=False)
    return corr.where(others).mean(axis=1)


def regimes(c: pd.DataFrame, member: pd.DataFrame) -> pd.DataFrame:
    """Régimes du jour (connus à la clôture) : tendance, sens, tercile de volatilité, tercile de corrélation."""
    btc = c["BTCUSDT"]
    er = efficiency_ratio(btc)
    vol = np.log(btc).diff().rolling(VOL_WINDOW).std() * np.sqrt(365)
    corr = btc_correlation(c, member)
    return pd.DataFrame({"efficacite": er, "tendance": er > ER_TREND, "hausse": btc > btc.shift(ER_WINDOW),
                         "volatilite": vol, "tercile_volatilite": expanding_tercile(vol),
                         "correlation": corr, "tercile_correlation": expanding_tercile(corr)})


def rules(g: pd.DataFrame) -> dict[str, pd.Series]:
    """Règles d'abstention fixées a priori (vrai = poche crypto en liquidités le lendemain). Régime inconnu : pas d'abstention."""
    known = g["efficacite"].notna()
    return {"A1 BTC en range": known & ~g["tendance"],
            "A2 BTC en tendance baissière": known & g["tendance"] & ~g["hausse"],
            "A3 volatilité dans le tercile haut": g["tercile_volatilite"] == 2,
            "A4 corrélation haute et BTC en baisse sur 30 jours": (g["tercile_correlation"] == 2) & known & ~g["hausse"]}


def shifted(mask: pd.Series, days: int = SHIFT_DAYS) -> pd.Series:
    """Témoin : le même masque décalé circulairement de « days » jours (même part, mêmes durées, autre calendrier)."""
    return pd.Series(np.roll(mask.to_numpy(), days), index=mask.index)


def abstain(signal: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """Signal neutralisé les jours où la règle est vraie (à la clôture) : la position du lendemain est fermée."""
    return signal & ~mask.reindex(signal.index, fill_value=False).to_numpy()[:, None]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Régimes de marché et abstention (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/regimes-abstention-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    prices = moteur.etf_prices(args.cache, signaux.LAST)
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    grid, cols = p["c"].index, list(p["c"].columns)
    btc = p["c"]["BTCUSDT"]
    rg = signaux.etf_returns(prices, grid).loc[first:]
    vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in ("F1_momentum", "F6_volume_pression", "F8_momentum_ajuste")]).sum(axis=0) >= 2,
                        index=grid, columns=cols)
    g = regimes(p["c"], member)
    masks = rules(g)

    def b0(sig):
        return signaux.net(sig, member, p, r, signaux.COST)[0].loc[first:]

    def b1(sig):
        return signaux.mix(signaux.net(sig, member, p, r, signaux.COST, 0.30)[0].loc[first:], rg)

    base = {"B0": (fam["F1_momentum"], b0), "B1": (vote, b1)}
    rows, controls = [], []
    for name, (sig, run) in base.items():
        ref = run(sig)
        for rule, mask in masks.items():
            rows.append(ablation.compare(ref, run(abstain(sig, mask)), label=f"C030 {rule}, {name}",
                                         costs="0,125 %/côté" + (" ; fonds 0,10 %" if name == "B1" else ""), btc_close=btc))
            controls.append(ablation.compare(ref, run(abstain(sig, shifted(mask))), label=f"témoin décalé d'un an : {rule}, {name}",
                                             costs="identiques", btc_close=btc))
    rows = ablation.adjust(rows)
    span = g.loc[first:]
    out = {"schema": "scalp-regimes-abstention-v1", "status": "mesure sur historique déjà consulté ; règles fixées a priori ; aucune intégration",
           "capacites": ["C021", "C024", "C026", "C030"],
           "parametres": {"efficacite_fenetre": ER_WINDOW, "seuil_tendance": ER_TREND, "volatilite_fenetre": VOL_WINDOW,
                          "correlation_fenetre": CORR_WINDOW, "historique_minimal_terciles": MIN_HISTORY_DAYS, "decalage_temoin_jours": SHIFT_DAYS},
           "repartition": {"jours": int(len(span)), "tendance": round(float(span["tendance"].mean()), 4),
                           "tercile_volatilite": {str(int(k)): int(v) for k, v in span["tercile_volatilite"].value_counts().sort_index().items()},
                           "tercile_correlation": {str(int(k)): int(v) for k, v in span["tercile_correlation"].value_counts().sort_index().items()},
                           "part_abstention": {k: round(float(m.loc[first:].mean()), 4) for k, m in masks.items()}},
           "ablations": rows, "temoins": controls, "seconds": round(time.time() - t0)}
    for x, c in zip(rows, controls):
        print(f"{x['ablation'][:58]:58} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] Holm {x['p_holm']:.3f} "
              f"| baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} % | témoin {c['ecart_hebdo_moyen_pct']:+.4f}, baisse {c['avec']['max_drawdown_pct']:.1f} %")
    print("répartition :", out["repartition"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "regimes-abstention-v1",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"], "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]}
                                               for x in rows}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
