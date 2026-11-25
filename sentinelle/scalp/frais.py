"""Frais datés (C061) : barème Binance au comptant à la date de chaque exécution, au lieu d'un frais constant.

Barème officiel, lu le 24/11/2026 sur binance.com, pour un utilisateur ordinaire (VIP 0) des paires USDT :
- frais standard de 0,10 % à l'achat comme à la vente pendant toute la période ;
- remise en BNB de 50 % jusqu'au 20/07/2018 04:00 UTC, puis de 25 %. Elle a été prolongée le 25/06/2021, puis le
  30/06/2022 « jusqu'à nouvel ordre ». Aucune annonce de 2019 à 2021 n'a été trouvée : le barème décroissant du livre
  blanc (12,5 %, puis 6,75 %) est donc testé comme borne pessimiste NON ATTESTÉE ;
- BTC/USDT sans frais du 08/07/2022 14:00 au 22/03/2023 00:00 UTC, sans remise BNB.
Les exécutions ont lieu à l'ouverture (00:00 UTC) : un changement en cours de journée s'applique au jour suivant.

Les positions du simulateur (signaux.simulate) ne dépendent pas des frais ; seuls les rendements nets en dépendent. On
rejoue donc les mêmes positions avec un coût par jour et par actif. À frais constants, le résultat est identique à
signaux.simulate (contrôlé par un test). Mesure seulement : aucune règle de trading n'est modifiée.

Lancement : python -m sentinelle.scalp.frais
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

SLIPPAGE = signaux.COST - 0.00075  # glissement supposé, inchangé (0,05 % par côté)
BASE_FEE = 0.001
B = "https://www.binance.com/en/support/"
SOURCES = {
    "remise BNB 50 % → 25 % le 20/07/2018 04:00 UTC ; frais VIP 0 de 0,10 %": B + "articles/360007480472",
    "25 % prolongée jusqu'au 13/07/2022 (publié le 25/06/2021)": B + "announcement/detail/2af5831727164545b8a2c665fae8bc04",
    "25 % prolongée jusqu'à nouvel ordre (publié le 30/06/2022)": B + "announcement/detail/844f0bae047d4be4903303cb743734e1",
    "BTC/USDT sans frais à partir du 08/07/2022 14:00 UTC, sans remise BNB": B + "announcement/detail/10435147c55d4a40b64fcbf43cb46329",
    "fin des frais nuls sur BTC/USDT le 22/03/2023 00:00 UTC": B + "announcement/detail/be13a645cca643d28eab5b9b34f2dc36",
    "barème actuel : 0,100 %, 0,07500 % avec BNB": "https://www.binance.com/en/fee/schedule",
}
BNB_50_UNTIL = "2018-07-20"  # dernier jour d'exécution (00:00 UTC) à 50 % de remise
BTC_ZERO = ("2022-07-09", "2023-03-21")  # jours d'exécution sans frais sur BTCUSDT
WHITEPAPER = (("2019-07-21", "2020-07-20", 0.125), ("2020-07-21", "2021-06-25", 0.0675))  # borne pessimiste non attestée


def fee_table(days, columns, variant: str) -> pd.DataFrame:
    """Frais par jour d'exécution et par paire. Variantes : « constant » (0,075 %), « annonces » (barème officiel),
    « livre blanc » (annonces + remise décroissante 2019-2021, non attestée), « sans BNB » (0,10 %, BTC sans frais)."""
    idx = pd.Index(days)
    dates = pd.to_datetime(idx.to_numpy() * moteur.DAY_MS, unit="ms")
    if variant == "constant":
        return pd.DataFrame(BASE_FEE * 0.75, index=idx, columns=columns)
    if variant == "sans BNB":
        fee = pd.Series(BASE_FEE, index=idx)
    else:
        fee = pd.Series(np.where(dates <= pd.Timestamp(BNB_50_UNTIL), BASE_FEE * 0.5, BASE_FEE * 0.75), index=idx)
        if variant == "livre blanc":
            for start, end, discount in WHITEPAPER:
                fee[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))] = BASE_FEE * (1 - discount)
        elif variant != "annonces":
            raise ValueError(f"variante inconnue : {variant}")
    table = pd.DataFrame(np.repeat(fee.to_numpy()[:, None], len(columns), axis=1), index=idx, columns=columns)
    if "BTCUSDT" in table:
        table.loc[(dates >= pd.Timestamp(BTC_ZERO[0])) & (dates <= pd.Timestamp(BTC_ZERO[1])), "BTCUSDT"] = 0.0
    return table


def replay(W: np.ndarray, r: pd.DataFrame, cost: pd.DataFrame) -> pd.Series:
    """Rendements nets des positions W (signaux.simulate) avec un coût par jour et par actif (frais + glissement)."""
    R, C = r.fillna(0.0).to_numpy(), cost.to_numpy()
    out, held = np.zeros(len(W)), np.zeros(W.shape[1])
    for i in range(len(W)):
        w = W[i]
        gross = float(w @ R[i])
        out[i] = gross - float(C[i] @ np.abs(w - held))
        total = 1.0 + gross
        held = w * (1 + R[i]) / total if total > 0 else np.zeros_like(held)
    return pd.Series(out, index=r.index)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Frais datés (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/frais-dates-v1.json")
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
    nets = {"B0": signaux.net(fam["F1_momentum"], member, p, r, signaux.COST), "B1": signaux.net(vote, member, p, r, signaux.COST, 0.30)}
    reference = {"B0": nets["B0"][0].loc[first:], "B1": signaux.mix(nets["B1"][0].loc[first:], rg)}
    series = {}
    for name, (_, w) in nets.items():
        for variant in ("constant", "annonces", "livre blanc", "sans BNB"):
            net = replay(w, r, fee_table(grid, cols, variant) + SLIPPAGE).loc[first:]
            series[(name, variant)] = signaux.mix(net, rg) if name == "B1" else net
    identical = all(np.allclose(series[(n, "constant")].to_numpy(), reference[n].to_numpy(), rtol=0, atol=1e-12) for n in nets)
    rows = ablation.adjust([ablation.compare(series[(n, "constant")], series[(n, v)], label=f"C061 frais « {v} » contre constants, {n}",
                                             costs=f"frais {v} + glissement 0,05 %/côté" + (" ; fonds 0,10 %" if n == "B1" else ""), btc_close=btc)
                            for n in nets for v in ("annonces", "livre blanc", "sans BNB")])
    out = {"schema": "scalp-frais-dates-v1", "status": "mesure sur historique déjà consulté ; protocole préenregistré ; aucune règle modifiée",
           "capacites": ["C061"], "sources": SOURCES, "bareme": {"frais_vip0": BASE_FEE, "remise_bnb_50_jusqu_au": BNB_50_UNTIL,
                                                                  "btcusdt_sans_frais": BTC_ZERO, "livre_blanc_non_atteste": WHITEPAPER},
           "non_couvert": "niveaux VIP supérieurs, paires non USDT, frais des fonds (0,10 % inchangé)", "ablations": rows,
           "verifications": {"constant_identique_au_simulateur": bool(identical)},
           "seconds": round(time.time() - t0)}
    for x in rows:
        print(f"{x['ablation']:52} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem IC [{x['ic95_pct'][0]:+.4f} ; {x['ic95_pct'][1]:+.4f}] Holm {x['p_holm']:.3f} "
              f"| {x['sans']['geometric_week_pct']:.4f} → {x['avec']['geometric_week_pct']:.4f} %/sem, baisse {x['sans']['max_drawdown_pct']:.1f} → {x['avec']['max_drawdown_pct']:.1f} %")
    print("constant identique au simulateur :", out["verifications"]["constant_identique_au_simulateur"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "frais-dates-v1",
                                 "ablations": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "ic95_pct": x["ic95_pct"],
                                                               "p_holm": x["p_holm"]} for x in rows}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
