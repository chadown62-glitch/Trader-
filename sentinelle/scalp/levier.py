"""Open interest des contrats perpétuels BTCUSDT (C028, en observation).

Données : archives journalières publiques « metrics » des contrats à terme USDⓈ-M de Binance (data.binance.vision),
relevés toutes les 5 minutes depuis le 01/09/2020, chacune vérifiée par sa somme de contrôle publiée (data._fetch_verified).
L'archive des liquidations n'est pas publiée sur cette période : les cascades ne sont approchées que par les fortes
baisses d'open interest (désendettement), sans mesure directe des liquidations.
Causalité : l'open interest du jour d est le dernier relevé valide du jour (23:55 UTC en général), connu avant la clôture crypto de d ;
il décide de la position à l'ouverture de d + 1. Quantité en BTC (sum_open_interest), pour ne pas mêler l'effet du prix.
Règle et mesures fixées a priori :
- O1 : variation de l'open interest sur 7 jours dans le tercile haut de son historique connu (au moins 365 jours) alors
  que le BTC a monté sur 7 jours (hausse portée par le levier) : poche crypto en liquidités le lendemain ; comparée à B0
  et B1 sans abstention (outil d'ablation C095, Holm sur les 2) et au même masque décalé d'un an ;
- O2 : jours de désendettement (open interest en baisse d'au moins 10 % sur le jour) : rendement du BTC le lendemain et
  sur les 3 jours suivants contre les autres jours, bilatéral, par blocs de 20 jours, Holm sur les 2.
Écart au protocole préenregistré (f2303eb), décidé après la mesure telle qu'enregistrée (levier-v1.json) : la source publie un
open interest nul, champs voisins vides, à partir de 15:30 UTC le 07/03/2022 et le 10/07/2024 ; ces relevés sont des lacunes et
sont ignorés (version v1b). Les deux résultats sont publiés.
Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.levier
"""

from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import ablation, data, essais, flux, moteur, recherche, regimes, signaux

URL = "https://data.binance.vision/data/futures/um/daily/metrics"
SYMBOL, FIRST_DAY = "BTCUSDT", "2020-09-01"
CHANGE_DAYS, MIN_DAYS, DELEVERAGE = 7, 365, -0.10
AHEAD = (1, 3)


def archive(day: str, cache_dir, client) -> Path:
    name = f"{SYMBOL}-metrics-{day}.zip"
    return data._fetch_verified(f"{URL}/{SYMBOL}/{name}", Path(cache_dir) / "metrics" / SYMBOL / name, client)


def last_reading(path: Path, day: str) -> tuple[int, float] | None:
    """Dernier relevé valide du jour « day » (avant minuit UTC) : (numéro du jour, open interest en BTC) ; None sans relevé.
    Un open interest nul est une lacune de la source (champs voisins vides), jamais une valeur : il est ignoré."""
    with zipfile.ZipFile(path) as z:
        d = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    if d.empty:
        return None
    t = pd.to_datetime(d["create_time"], utc=True)
    start = pd.Timestamp(day, tz="UTC")
    inside = (t >= start) & (t < start + pd.Timedelta(days=1)) & (pd.to_numeric(d["sum_open_interest"], errors="coerce") > 0)
    if not inside.any():
        return None
    i = t.where(inside).idxmax()
    return moteur._day(day), float(d["sum_open_interest"].loc[i])


def series(readings: list[tuple[int, float] | None]) -> pd.Series:
    """Open interest de fin de jour par numéro de jour ; jours absents laissés vides (aucun comblement)."""
    ok = [r for r in readings if r is not None]
    s = pd.Series({d: v for d, v in ok}, dtype=float).sort_index()
    return s.reindex(pd.RangeIndex(int(s.index.min()), int(s.index.max()) + 1))


def leverage_rule(oi: pd.Series, btc: pd.Series) -> pd.Series:
    """O1 : hausse de l'open interest sur 7 jours dans le tercile haut connu à la date, avec un BTC en hausse sur 7 jours."""
    change = np.log(oi).diff(CHANGE_DAYS)
    top = regimes.expanding_tercile(change.dropna(), MIN_DAYS).reindex(oi.index) == 2
    rising = (btc / btc.shift(CHANGE_DAYS) - 1).reindex(oi.index) > 0
    return (top & rising).fillna(False)


def deleveraging(oi: pd.Series) -> pd.Series:
    """Jours où l'open interest baisse d'au moins 10 % (relevé de fin de jour contre celui de la veille)."""
    return oi / oi.shift(1) <= 1 + DELEVERAGE  # rapport comparé directement : une baisse d'exactement 10 % compte


def after_deleveraging(oi: pd.Series, btc: pd.Series) -> dict:
    """O2 : rendement du BTC après un jour de désendettement contre les autres jours (lendemain et 3 jours suivants)."""
    lev = deleveraging(oi)
    out = {}
    for h in AHEAD:
        ahead = (np.log(btc).shift(-h) - np.log(btc)).reindex(oi.index)
        ok = ahead.notna() & oi.notna() & oi.shift(1).notna()
        out[f"{h}_jour" + ("s" if h > 1 else "")] = flux.mean_gap(ahead[ok], lev[ok].to_numpy())
    for k, ph in zip(out, essais.holm([v["p_bilaterale"] for v in out.values()])):
        out[k]["p_holm"] = ph
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Open interest des perpétuels BTC (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/levier-v1b.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    days = [d.strftime("%Y-%m-%d") for d in pd.date_range(FIRST_DAY, signaux.LAST)]
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))

        def one(day):
            try:
                return last_reading(archive(day, args.cache, client), day)
            except httpx.HTTPStatusError:
                return None

        with ThreadPoolExecutor(4) as pool:
            readings = list(pool.map(one, days))
    oi = series(readings)
    prices = moteur.etf_prices(args.cache, signaux.LAST)
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    grid, cols = p["c"].index, list(p["c"].columns)
    btc = p["c"]["BTCUSDT"]
    rg = signaux.etf_returns(prices, grid).loc[first:]
    vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in ("F1_momentum", "F6_volume_pression", "F8_momentum_ajuste")]).sum(axis=0) >= 2,
                        index=grid, columns=cols)
    mask = leverage_rule(oi, btc).reindex(grid, fill_value=False)

    def b0(sig):
        return signaux.net(sig, member, p, r, signaux.COST)[0].loc[first:]

    def b1(sig):
        return signaux.mix(signaux.net(sig, member, p, r, signaux.COST, 0.30)[0].loc[first:], rg)

    label = "O1 open interest en forte hausse avec un BTC en hausse"
    rows, controls = [], []
    for name, (sig, run) in {"B0": (fam["F1_momentum"], b0), "B1": (vote, b1)}.items():
        ref = run(sig)
        rows.append(ablation.compare(ref, run(regimes.abstain(sig, mask)), label=f"{label}, {name}",
                                     costs="0,125 %/côté" + (" ; fonds 0,10 %" if name == "B1" else ""), btc_close=btc))
        controls.append(ablation.compare(ref, run(regimes.abstain(sig, regimes.shifted(mask))), label=f"témoin décalé d'un an : {label}, {name}",
                                         costs="identiques", btc_close=btc))
    rows = ablation.adjust(rows)
    o2 = after_deleveraging(oi, btc)
    known = oi.notna()
    out = {"schema": "scalp-levier-v1b", "status": "mesure sur historique déjà consulté ; règles fixées a priori ; aucune intégration",
           "ecart_au_protocole": "relevés d'open interest nuls (lacunes de la source) ignorés ; résultat tel que préenregistré : levier-v1.json",
           "capacites": ["C028"], "source": URL, "symbole": SYMBOL,
           "limites": ["liquidations non publiées en archive : cascades approchées par les baisses d'open interest",
                       f"open interest disponible depuis le {FIRST_DAY} seulement"],
           "couverture": {"jours_connus": int(known.sum()), "jours": int(len(oi)),
                          "premier_jour": str(pd.to_datetime(int(oi.index[0]) * moteur.DAY_MS, unit="ms").date())},
           "parametres": {"variation_jours": CHANGE_DAYS, "historique_minimal_jours": MIN_DAYS, "seuil_desendettement": DELEVERAGE, "horizons_jours": AHEAD},
           "part_abstention": round(float(mask.loc[first:].mean()), 4), "jours_desendettement": int(deleveraging(oi).sum()),
           "ablations": rows, "temoins": controls, "apres_desendettement": o2, "seconds": round(time.time() - t0)}
    for x, c in zip(rows, controls):
        print(f"{x['ablation'][:62]:62} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] Holm {x['p_holm']:.3f} "
              f"| baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} % | témoin {c['ecart_hebdo_moyen_pct']:+.4f}, baisse {c['avec']['max_drawdown_pct']:.1f} %")
    for k, v in o2.items():
        print(f"après désendettement, {k:8} écart {v['ecart_moyen_pct']:+.4f} pt (n {v['jours']}), p {v['p_bilaterale']:.3f}, Holm {v['p_holm']:.3f}")
    print("couverture :", out["couverture"], "| part d'abstention :", out["part_abstention"], "| jours de désendettement :", out["jours_desendettement"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "levier-v1b", "ecart_au_protocole": "relevés nuls ignorés",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"], "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]}
                                               for x in rows},
                                 "apres_desendettement": {k: {"ecart_moyen_pct": v["ecart_moyen_pct"], "p_holm": v["p_holm"]} for k, v in o2.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
