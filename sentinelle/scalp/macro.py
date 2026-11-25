"""Contexte macroéconomique et propagation actions → crypto (C014, C015, C016, C017).

Séries publiques journalières de Yahoo Finance, archivées avec leur empreinte SHA-256 (actifs.download) : rendement de
l'emprunt d'État américain à 10 ans (^TNX), indice du dollar (DX-Y.NYB), volatilité implicite du S&P 500 (^VIX),
Nasdaq-100 (QQQ), semi-conducteurs (SMH), valeurs liées aux cryptos (MSTR, COIN).
Causalité : la clôture américaine de la séance d (au plus tard 22:00 UTC) précède la clôture crypto du jour d (00:00 UTC
de d + 1), qui décide de la position prise à l'ouverture de d + 1. Les jours sans séance reprennent la dernière clôture
connue. Chaque série est traitée sur ses propres séances ; ses terciles portent sur son historique connu à la date
(depuis 2000, au moins 250 séances).
Règles d'abstention fixées a priori (vrai : poche crypto en liquidités le lendemain) :
- M1 (C014) : variation du rendement à 10 ans sur 20 séances dans le tercile haut (hausse rapide des taux) ;
- M2 (C015) : rendement de l'indice du dollar sur 20 séances dans le tercile haut ;
- M3 (C016) : niveau du VIX dans le tercile haut ;
- M4 (C016) : variation du VIX sur 5 séances dans le tercile haut (stress en hausse) ;
- M5 (C017) : séance du Nasdaq-100 en baisse d'au moins 2 % (seul le jour crypto suivant est concerné).
Chaque règle est comparée à B0 et B1 sans abstention (outil d'ablation C095, Holm sur les 10) et à son témoin décalé d'un
an (regimes.shifted). Mesure complémentaire de C017 : corrélation de rang entre le rendement de la séance d et celui du BTC
le jour crypto d + 1, bilatérale, par blocs de 20 jours, Holm sur les 4 séries.
Mesure seulement : rien n'entre dans une décision sans approbation humaine.

Lancement : python -m sentinelle.scalp.macro
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

from . import ablation, actifs, essais, moteur, recherche, regimes, signaux

SERIES = {"taux_10_ans": "^TNX", "dollar": "DX-Y.NYB", "vix": "^VIX", "nasdaq_100": "QQQ", "semi_conducteurs": "SMH",
          "microstrategy": "MSTR", "coinbase": "COIN"}
LEADERS = {"nasdaq_100": None, "semi_conducteurs": None, "microstrategy": "2020-08-11", "coinbase": "2021-04-14"}  # lien crypto daté
CHANGE_SESSIONS, VIX_SESSIONS, MIN_SESSIONS = 20, 5, 250
DROP = -0.02
LEAD_BLOCK = 20


def day_numbers(index: pd.DatetimeIndex) -> np.ndarray:
    """Date de séance → numéro du jour UTC du panneau crypto."""
    return np.asarray((index.normalize() - pd.Timestamp(0)) // pd.Timedelta(milliseconds=moteur.DAY_MS), dtype=np.int64)


def session_rules(s: dict[str, pd.Series]) -> dict[str, tuple[pd.Series, bool]]:
    """Règles par séance, chacune sur les séances de sa propre série ; une valeur inconnue ne déclenche rien.
    Valeur : (règle indexée par date de séance, l'état persiste-t-il les jours sans séance)."""
    tnx, dxy, vix, qqq = s["taux_10_ans"], s["dollar"], s["vix"], s["nasdaq_100"]

    def top(x: pd.Series) -> pd.Series:
        x = x.dropna()
        return regimes.expanding_tercile(x, MIN_SESSIONS) == 2

    return {"M1 taux à 10 ans en hausse rapide (tercile haut sur 20 séances)": (top(tnx - tnx.shift(CHANGE_SESSIONS)), True),
            "M2 dollar en hausse (tercile haut sur 20 séances)": (top(np.log(dxy / dxy.shift(CHANGE_SESSIONS))), True),
            "M3 VIX élevé (tercile haut)": (top(vix), True),
            "M4 VIX en hausse (tercile haut sur 5 séances)": (top(np.log(vix / vix.shift(VIX_SESSIONS))), True),
            "M5 Nasdaq-100 en baisse d'au moins 2 % sur la séance": ((qqq.pct_change() <= DROP), False)}


def to_crypto_days(rule: pd.Series, grid: pd.Index, *, carry: bool) -> pd.Series:
    """Règle de séance placée sur les jours crypto : la séance d informe la ligne d (position du lendemain). « carry » :
    l'état persiste les jours sans séance ; sinon seule la ligne de la séance est vraie. Avant la première séance : faux."""
    s = pd.Series(rule.to_numpy(dtype=float), index=day_numbers(rule.index))
    s = s[~s.index.duplicated(keep="last")]
    out = s.reindex(s.index.union(grid)).ffill().reindex(grid) if carry else s.reindex(grid)
    return out.fillna(0.0) > 0.5


def lead_lag(leader: pd.Series, btc: pd.Series, start: str | None = None) -> dict:
    """Corrélation de rang entre le rendement de la séance d et celui du BTC le jour crypto d + 1 (clôture de d →
    clôture de d + 1, c'est-à-dire 00:00 UTC de d + 1 → 00:00 UTC de d + 2, après la clôture américaine) ; p bilatérale
    par blocs circulaires de LEAD_BLOCK jours. btc : clôtures indexées par numéro de jour."""
    r = leader.pct_change().dropna()
    if start:
        r = r[r.index >= pd.Timestamp(start)]
    nxt = np.log(btc).diff().shift(-1)  # ligne d : rendement du jour d + 1
    x, y = r.to_numpy(), nxt.reindex(day_numbers(r.index)).to_numpy()
    ok = ~np.isnan(x) & ~np.isnan(y)
    x, y = x[ok], y[ok]
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    zx, zy = (rx - rx.mean()) / rx.std(), (ry - ry.mean()) / ry.std()
    u = zx * zy  # moyenne : corrélation de rang
    p = min(1.0, 2 * min(essais.bootstrap_pvalue(u, block=LEAD_BLOCK), essais.bootstrap_pvalue(-u, block=LEAD_BLOCK)))
    return {"n": int(len(x)), "spearman": float(np.corrcoef(rx, ry)[0, 1]), "p_bilaterale": float(p)}


def load_series(cache, last_day: str, client: httpx.Client) -> dict[str, pd.Series]:
    end = int(time.time())
    return {k: actifs.download(t, cache, end_ts=end, client=client).loc[:last_day] for k, t in SERIES.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Contexte macroéconomique et propagation actions → crypto (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/macro-contexte-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Sentinelle simulation)"}) as client:
        series = load_series(args.cache, signaux.LAST, client)
    prices = moteur.etf_prices(args.cache, signaux.LAST)
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    grid, cols = p["c"].index, list(p["c"].columns)
    btc = p["c"]["BTCUSDT"]
    rg = signaux.etf_returns(prices, grid).loc[first:]
    vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in ("F1_momentum", "F6_volume_pression", "F8_momentum_ajuste")]).sum(axis=0) >= 2,
                        index=grid, columns=cols)
    masks = {name: to_crypto_days(rule, grid, carry=carry) for name, (rule, carry) in session_rules(series).items()}

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
    btc_span = btc.loc[first:]
    leads = {k: lead_lag(series[k], btc_span, start) for k, start in LEADERS.items()}
    for k, ph in zip(leads, essais.holm([v["p_bilaterale"] for v in leads.values()])):
        leads[k]["p_holm"] = ph
    out = {"schema": "scalp-macro-contexte-v1", "status": "mesure sur historique déjà consulté ; règles fixées a priori ; aucune intégration",
           "capacites": ["C014", "C015", "C016", "C017"], "series": SERIES, "source": actifs.URL,
           "parametres": {"seances_variation": CHANGE_SESSIONS, "seances_vix": VIX_SESSIONS, "seances_minimales_terciles": MIN_SESSIONS,
                          "baisse_nasdaq": DROP, "bloc_correlation_jours": LEAD_BLOCK, "debut_lien_crypto": LEADERS},
           "part_abstention": {k: round(float(m.loc[first:].mean()), 4) for k, m in masks.items()},
           "ablations": rows, "temoins": controls, "correlations_decalees": leads, "seconds": round(time.time() - t0)}
    for x, c in zip(rows, controls):
        print(f"{x['ablation'][:62]:62} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] Holm {x['p_holm']:.3f} "
              f"| baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} % | témoin {c['ecart_hebdo_moyen_pct']:+.4f}, baisse {c['avec']['max_drawdown_pct']:.1f} %")
    for k, v in leads.items():
        print(f"séance {k:16} → BTC le lendemain : rang {v['spearman']:+.4f} (n {v['n']}), p {v['p_bilaterale']:.3f}, Holm {v['p_holm']:.3f}")
    print("part d'abstention :", out["part_abstention"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "macro-contexte-v1",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"], "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]}
                                               for x in rows},
                                 "correlations_decalees": {k: {"spearman": v["spearman"], "n": v["n"], "p_holm": v["p_holm"]} for k, v in leads.items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
