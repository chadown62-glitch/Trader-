"""Faisabilité d'exécution avec un petit capital (C062 minima d'ordre et pas de quantité, C098 capital fictif de 30 €).

Pour chaque entrée simulée de B0 et de B1 (2018 → 2026), le montant voulu (part du capital × équité de la poche crypto à
la clôture de la veille) est confronté aux règles de marché publiques de Binance (montant minimal, pas de quantité des
ordres au marché, quantité minimale) et arrondi comme un vrai ordre (costs.inventory.round_trip) : entrée faisable, sous
le minimum (impossible), erreur de taille et reliquat invendable. Capitaux : 30 €, 100 €, 1 000 €, 10 000 €, convertis au
cours EUR/USDT public du jour de la mesure.
Limites déclarées : les règles de marché sont celles d'aujourd'hui (aucun historique public des filtres) ; une paire
retirée de la cote n'a plus de règles (comptée « règles inconnues ») ; les fonds UCITS ne sont pas couverts (NON MESURÉ).
Mesure seulement : aucune règle de trading n'est modifiée.

Lancement : python -m sentinelle.scalp.faisabilite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from ..costs.inventory import round_trip
from ..costs.venues import parse_exchange_info
from . import moteur, recherche, signaux

EXCHANGE_INFO = "https://data-api.binance.vision/api/v3/exchangeInfo"
EUR_USDT = "https://data-api.binance.vision/api/v3/ticker/price"
CAPITALS_EUR = (30, 100, 1_000, 10_000)
CRYPTO_SHARE_B1 = 0.35
FEE = Decimal("0.001")  # commission prélevée dans l'actif reçu (sans BNB) : cas le plus défavorable au reliquat


def entries(W: np.ndarray, days, columns) -> list[tuple[int, str, float]]:
    """Entrées simulées : (jour, paire, part du capital de la poche) au premier jour de chaque position."""
    out, prev = [], np.zeros(W.shape[1])
    for i, d in enumerate(days):
        for j in np.flatnonzero((W[i] > 0) & (prev <= 0)):
            out.append((int(d), columns[j], float(W[i, j])))
        prev = W[i]
    return out


def check_entry(price: float, target: float, rules) -> dict:
    """Une entrée confrontée aux règles de marché : faisable, sous le minimum, ou règles inconnues."""
    if rules is None:
        return {"statut": "règles inconnues"}
    rt = round_trip(Decimal(str(price)), Decimal(str(target)), step=rules.market_step,
                    min_notional=rules.min_notional if rules.min_notional_applies_to_market else Decimal(0),
                    min_qty=rules.min_qty, fee_rate=FEE, raise_to_minimum=False)
    if rt.status != "ok":
        return {"statut": "sous le minimum", "minimum": float(rules.min_notional)}
    return {"statut": "faisable", "erreur_taille": abs(rt.sizing_error), "part_reliquat": rt.dust_share}


def study(trades, pocket_equity: pd.Series, opens: pd.DataFrame, rules: dict, capital_usdt: float) -> dict:
    """Bilan d'un capital : part des entrées faisables, sous le minimum, à règles inconnues ; erreurs et reliquats."""
    rows = []
    for day, sym, w in trades:
        eq_prev = float(pocket_equity.loc[:day - 1].iloc[-1]) if len(pocket_equity.loc[:day - 1]) else 1.0
        target = w * capital_usdt * eq_prev
        price = float(opens.at[day, sym])
        rows.append({"montant": target, **check_entry(price, target, rules.get(sym))})
    n = len(rows)
    ok = [r for r in rows if r["statut"] == "faisable"]
    count = lambda s: sum(r["statut"] == s for r in rows)  # noqa: E731
    return {"entrees": n, "faisables": len(ok), "sous_le_minimum": count("sous le minimum"), "regles_inconnues": count("règles inconnues"),
            "part_faisable": round(len(ok) / n, 4) if n else None,
            "montant_median_usdt": round(float(np.median([r["montant"] for r in rows])), 2) if n else None,
            "erreur_taille_moyenne": round(float(np.mean([r["erreur_taille"] for r in ok])), 5) if ok else None,
            "reliquat_moyen": round(float(np.mean([r["part_reliquat"] for r in ok])), 5) if ok else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Faisabilité d'exécution avec un petit capital (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/faisabilite-petit-capital-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    as_of = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        raw = client.get(EXCHANGE_INFO, params={"permissions": "SPOT"})
        raw.raise_for_status()
        fx = client.get(EUR_USDT, params={"symbol": "EURUSDT"})
        fx.raise_for_status()
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    eur_usdt = float(fx.json()["price"])
    rules = parse_exchange_info(raw.content, as_of=as_of, symbols=set(recherche.CANDIDATES))
    p = signaux.panel(loaded)
    member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
    first = moteur._day(signaux.FIRST)
    grid = p["c"].index
    keep = grid >= first
    cols = list(p["c"].columns)
    vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in ("F1_momentum", "F6_volume_pression", "F8_momentum_ajuste")]).sum(axis=0) >= 2,
                        index=grid, columns=cols)
    strategies = {"B0 momentum simple (1/10 par place, tout le capital)": (signaux.net(fam["F1_momentum"], member, p, r, signaux.COST), 1.0),
                  "B1 vote, volatilité visée 30 % (poche crypto de 35 %)": (signaux.net(vote, member, p, r, signaux.COST, 0.30), CRYPTO_SHARE_B1)}
    out = {"schema": "scalp-faisabilite-petit-capital-v1", "status": "mesure ; règles de marché du jour appliquées à l'historique ; aucune règle de trading modifiée",
           "capacites": ["C062", "C098"], "regles": {"source": EXCHANGE_INFO, "releve_utc": as_of, "sha256": hashlib.sha256(raw.content).hexdigest(),
                                                     "paires": {s: f.to_dict() for s, f in sorted(rules.items())},
                                                     "paires_sans_regles": sorted(set(recherche.CANDIDATES) - set(rules))},
           "eur_usdt": {"cours": eur_usdt, "source": EUR_USDT + "?symbol=EURUSDT", "releve_utc": as_of},
           "fonds_ucits": "NON MESURÉ : aucun prix ni règle d'ordre d'équivalents UCITS dans le dépôt", "resultats": {}}
    for name, ((ret, W), share) in strategies.items():
        eq = pd.Series(np.cumprod(1 + ret.loc[first:].to_numpy()), index=grid[keep])
        trades = entries(W[keep], grid[keep], cols)
        out["resultats"][name] = {f"{k} €": study(trades, eq, p["o"], rules, k * eur_usdt * share) for k in CAPITALS_EUR}
    out["seconds"] = round(time.time() - t0)
    for name, rows in out["resultats"].items():
        print(name)
        for k, s in rows.items():
            print(f"  {k:>9} : {s['entrees']} entrées, faisables {s['part_faisable']:.1%}, sous le minimum {s['sous_le_minimum']}, "
                  f"règles inconnues {s['regles_inconnues']}, montant médian {s['montant_median_usdt']} USDT, reliquat moyen {s['reliquat_moyen']}")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "faisabilite-petit-capital-v1",
                                 "resultats": {n: {k: {m: s[m] for m in ("entrees", "part_faisable", "sous_le_minimum", "regles_inconnues")}
                                                   for k, s in rows.items()} for n, rows in out["resultats"].items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
