"""Flux publics de stablecoins (C018) et effets calendaires (C029).

C018. Offre en circulation des stablecoins indexés sur le dollar, série journalière publique de DefiLlama
(stablecoins.llama.fi, données ouvertes), archivée avec son empreinte SHA-256 et son heure de téléchargement. Limite
déclarée : la série est reconstituée par la source et peut avoir été révisée depuis ; elle n'est PAS point-in-time et
le résultat est une borne optimiste. Prudence : le relevé daté D (00:00 UTC) n'est utilisé qu'à la clôture du jour D + 1,
soit au moins 24 heures plus tard. Valeurs nulles (couverture absente) ignorées.
- S1 : variation sur 20 jours de l'offre dans le tercile bas de son historique connu (au moins 365 jours) : contraction,
  poche crypto en liquidités le lendemain ;
- corrélation de rang entre la variation de l'offre sur 7 jours connue à la date et le rendement du BTC sur les 7 jours
  suivants, bilatérale, par blocs de 20 jours.
C029. Jours de la semaine et tournant de mois, sur le BTC (clôture d → clôture d + 1, 00:00 UTC) :
- K1 : liquidités le samedi et le dimanche (signaux du vendredi et du samedi neutralisés) ;
- écart du rendement moyen de chaque jour de la semaine et du tournant de mois (dernier jour et trois premiers jours)
  au rendement moyen des autres jours, bilatéral, par blocs de 20 jours, Holm sur les 8.
Les coûts selon l'heure ne sont PAS mesurés (aucun carnet d'ordres historique horodaté).
S1 et K1 sont comparées à B0 et B1 sans abstention (outil d'ablation C095, Holm sur les 4) et à leur témoin décalé d'un
an (regimes.shifted). Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.flux
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import ablation, essais, moteur, recherche, regimes, signaux

LLAMA = "https://stablecoins.llama.fi/stablecoincharts/all"
SUPPLY_WINDOW, LEAD_DAYS, MIN_DAYS, LAG_DAYS = 20, 7, 365, 1
BLOCK = 20
WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def weekday(days) -> np.ndarray:
    """Jour de la semaine (0 = lundi) d'un numéro de jour UTC (le 1er janvier 1970 était un jeudi)."""
    return (np.asarray(days, dtype=np.int64) + 3) % 7


def month_turn(days) -> np.ndarray:
    """Vrai le dernier jour du mois et les trois premiers jours du mois suivant."""
    dates = pd.to_datetime(np.asarray(days, dtype=np.int64) * moteur.DAY_MS, unit="ms")
    return np.asarray(dates.is_month_end | (dates.day <= 3))


def download_supply(cache_dir, client: httpx.Client) -> pd.Series:
    path = Path(cache_dir) / "defillama" / "stablecoins-all.json"
    if not path.exists():
        r = client.get(LLAMA)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        path.with_suffix(".sha256").write_text(
            f"{hashlib.sha256(r.content).hexdigest()}  {path.name}  téléchargé {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n", encoding="utf-8")
    return parse_supply(json.loads(path.read_text(encoding="utf-8")))


def parse_supply(payload: list[dict]) -> pd.Series:
    """Offre en circulation des stablecoins indexés sur le dollar, par numéro de jour UTC du relevé."""
    days = [int(x["date"]) * 1000 // moteur.DAY_MS for x in payload]
    values = [float((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or 0.0) for x in payload]
    s = pd.Series(values, index=days, dtype=float)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s > 0]


def usable(supply: pd.Series, grid: pd.Index) -> pd.Series:
    """Offre connue à la clôture de chaque jour du panneau : relevé du jour d − LAG_DAYS (au moins 24 h de délai),
    sans comblement au-delà d'un jour manquant."""
    full = supply.reindex(pd.RangeIndex(int(supply.index.min()), int(supply.index.max()) + 1 + LAG_DAYS)).ffill(limit=1)
    return full.shift(LAG_DAYS).reindex(grid)


def supply_rule(known: pd.Series) -> pd.Series:
    """S1 : contraction de l'offre sur 20 jours (tercile bas de l'historique connu) ; une valeur inconnue ne déclenche rien."""
    change = np.log(known).diff(SUPPLY_WINDOW)
    t = regimes.expanding_tercile(change.dropna(), MIN_DAYS).reindex(known.index)
    return (t == 0).fillna(False)


def weekend_rule(grid: pd.Index) -> pd.Series:
    """K1 : signal neutralisé le vendredi et le samedi, donc liquidités aux ouvertures du samedi et du dimanche."""
    return pd.Series(np.isin(weekday(grid), (4, 5)), index=grid)


def rank_test(x: np.ndarray, y: np.ndarray) -> dict:
    """Corrélation de rang, p bilatérale par blocs circulaires de BLOCK jours."""
    ok = ~np.isnan(x) & ~np.isnan(y)
    rx, ry = pd.Series(x[ok]).rank().to_numpy(), pd.Series(y[ok]).rank().to_numpy()
    u = (rx - rx.mean()) / rx.std() * (ry - ry.mean()) / ry.std()
    p = min(1.0, 2 * min(essais.bootstrap_pvalue(u, block=BLOCK), essais.bootstrap_pvalue(-u, block=BLOCK)))
    return {"n": int(ok.sum()), "spearman": float(np.corrcoef(rx, ry)[0, 1]), "p_bilaterale": float(p)}


def supply_lead(known: pd.Series, btc: pd.Series) -> dict:
    """Variation de l'offre sur 7 jours connue à la ligne d contre rendement du BTC de la clôture d à la clôture d + 7."""
    change = np.log(known).diff(LEAD_DAYS)
    ahead = np.log(btc).shift(-LEAD_DAYS) - np.log(btc)
    return rank_test(change.to_numpy(), ahead.reindex(change.index).to_numpy())


def mean_gap(r: pd.Series, mask: np.ndarray) -> dict:
    """Rendement moyen des jours marqués moins celui des autres jours ; p bilatérale par blocs (série des écarts
    centrés sur chaque groupe)."""
    x, m = r.to_numpy(), np.asarray(mask, dtype=bool)
    ok = ~np.isnan(x)
    x, m = x[ok], m[ok]
    gap = x[m].mean() - x[~m].mean()
    # contribution de chaque jour à l'écart des moyennes : moyenne = écart observé
    u = np.where(m, x / m.mean(), -x / (1 - m.mean()))
    p = min(1.0, 2 * min(essais.bootstrap_pvalue(u, block=BLOCK), essais.bootstrap_pvalue(-u, block=BLOCK)))
    return {"jours": int(m.sum()), "ecart_moyen_pct": float(100 * gap), "p_bilaterale": float(p)}


def calendar(btc: pd.Series) -> dict:
    r = np.log(btc).diff().dropna()  # ligne d : rendement du jour d (00:00 UTC de d → 00:00 UTC de d + 1)
    out = {WEEKDAYS[w]: mean_gap(r, weekday(r.index) == w) for w in range(7)}
    out["tournant de mois"] = mean_gap(r, month_turn(r.index))
    for k, ph in zip(out, essais.holm([v["p_bilaterale"] for v in out.values()])):
        out[k]["p_holm"] = ph
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flux de stablecoins et effets calendaires (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/flux-calendrier-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
        supply = download_supply(args.cache, client)
    prices = moteur.etf_prices(args.cache, signaux.LAST)
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    grid, cols = p["c"].index, list(p["c"].columns)
    btc = p["c"]["BTCUSDT"]
    rg = signaux.etf_returns(prices, grid).loc[first:]
    vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in ("F1_momentum", "F6_volume_pression", "F8_momentum_ajuste")]).sum(axis=0) >= 2,
                        index=grid, columns=cols)
    known = usable(supply.loc[: moteur._day(signaux.LAST)], grid)
    masks = {"S1 contraction de l'offre de stablecoins sur 20 jours (tercile bas)": supply_rule(known),
             "K1 liquidités le samedi et le dimanche": weekend_rule(grid)}

    def b0(sig):
        return signaux.net(sig, member, p, r, signaux.COST)[0].loc[first:]

    def b1(sig):
        return signaux.mix(signaux.net(sig, member, p, r, signaux.COST, 0.30)[0].loc[first:], rg)

    rows, controls = [], []
    for name, (sig, run) in {"B0": (fam["F1_momentum"], b0), "B1": (vote, b1)}.items():
        ref = run(sig)
        for rule, mask in masks.items():
            rows.append(ablation.compare(ref, run(regimes.abstain(sig, mask)), label=f"{rule}, {name}",
                                         costs="0,125 %/côté" + (" ; fonds 0,10 %" if name == "B1" else ""), btc_close=btc))
            controls.append(ablation.compare(ref, run(regimes.abstain(sig, regimes.shifted(mask))), label=f"témoin décalé d'un an : {rule}, {name}",
                                             costs="identiques", btc_close=btc))
    rows = ablation.adjust(rows)
    span = btc.loc[first:]
    lead = supply_lead(known.loc[first:], span)
    cal = calendar(span)
    cover = known.loc[first:]
    out = {"schema": "scalp-flux-calendrier-v1", "status": "mesure sur historique déjà consulté ; règles fixées a priori ; aucune intégration",
           "capacites": ["C018", "C029"], "source_stablecoins": LLAMA,
           "limites": ["offre de stablecoins reconstituée par la source, non point-in-time : borne optimiste",
                       "coûts selon l'heure non mesurés (aucun carnet historique horodaté)"],
           "parametres": {"fenetre_offre_jours": SUPPLY_WINDOW, "horizon_correlation_jours": LEAD_DAYS, "historique_minimal_jours": MIN_DAYS,
                          "delai_releve_jours": LAG_DAYS, "bloc_jours": BLOCK},
           "couverture_offre": {"jours_connus": int(cover.notna().sum()), "jours": int(len(cover)),
                                "premier_jour_connu": str(pd.to_datetime(int(cover.first_valid_index()) * moteur.DAY_MS, unit="ms").date())},
           "part_abstention": {k: round(float(m.loc[first:].mean()), 4) for k, m in masks.items()},
           "ablations": rows, "temoins": controls, "offre_contre_btc_7_jours": lead, "calendrier_btc": cal, "seconds": round(time.time() - t0)}
    for x, c in zip(rows, controls):
        print(f"{x['ablation'][:62]:62} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] Holm {x['p_holm']:.3f} "
              f"| baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} % | témoin {c['ecart_hebdo_moyen_pct']:+.4f}, baisse {c['avec']['max_drawdown_pct']:.1f} %")
    print(f"offre sur 7 jours → BTC sur 7 jours : rang {lead['spearman']:+.4f} (n {lead['n']}), p {lead['p_bilaterale']:.3f}")
    for k, v in cal.items():
        print(f"{k:18} écart {v['ecart_moyen_pct']:+.4f} %/jour (n {v['jours']}), p {v['p_bilaterale']:.3f}, Holm {v['p_holm']:.3f}")
    print("part d'abstention :", out["part_abstention"], "| couverture :", out["couverture_offre"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "flux-calendrier-v1",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"], "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]}
                                               for x in rows},
                                 "offre_contre_btc_7_jours": lead,
                                 "calendrier_btc": {k: {"ecart_moyen_pct": v["ecart_moyen_pct"], "p_holm": v["p_holm"]} for k, v in cal.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
