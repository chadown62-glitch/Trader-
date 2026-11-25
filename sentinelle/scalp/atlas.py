"""OMEGA priorité C — RADAR (univers observé) et ATLAS (inventaire : couverture, qualité, dépendances, coûts, liquidité).

Inventaire descriptif de ce qui est déjà accessible, sans nouvelle source (toute extension attend l'autorisation du
propriétaire) et sans effet sur l'exposition : observer davantage d'actifs n'en fait jamais acheter davantage.
- Cryptos : les 42 candidates (archives journalières vérifiées), cotation et retrait, jours cotés et trous, coupure à une
  recotation, liquidité (volume médian en USDT sur la dernière année cotée), part des jours dans l'univers défini à la date
  (top 10 du volume connu avant le mois, au moins 60 jours d'historique), renommages officiels.
- Dépendances entre cryptos : par année, corrélation moyenne des rendements hebdomadaires des membres et part de variance de la
  première composante principale (un facteur commun dominant limite ce que la diversification et l'apprentissage par actif
  peuvent apporter).
- Fonds (Yahoo, poche actions) et séries macroéconomiques (Yahoo) : historique et trous, depuis le cache.
- Coûts et faisabilité par capital : frais datés (C061, #155), écart de cotation mesuré (C099, #154), minima d'ordre et part
  des entrées faisables à 30, 100, 1 000 et 10 000 € (C062, #150), lus dans les résultats publiés.
Mesure seulement.

Lancement : python -m sentinelle.scalp.atlas
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

from . import actifs, moteur, recherche, signaux

RESULTS = Path(__file__).resolve().parents[2] / "research" / "scalp-v1"


def date(day) -> str | None:
    return None if day is None or not np.isfinite(day) else str(pd.to_datetime(int(day) * moteur.DAY_MS, unit="ms").date())


def crypto_row(symbol: str, bars: pd.DataFrame, report: dict, member: pd.Series | None, last_day: int) -> dict:
    """Couverture, qualité et liquidité d'une paire."""
    if bars.empty:
        return {"symbole": symbol, "cotee": False}
    quoted = bars["c"].notna()
    first, last = int(bars.index[quoted.argmax()]), int(bars.index[::-1][quoted[::-1].argmax()])
    year = bars.loc[max(first, last - 364): last]
    volume = (year["v"] * year["c"]).dropna()
    return {"symbole": symbol, "cotee": True, "premier_jour": date(first), "dernier_jour": date(last),
            "retiree": bool(last < last_day - 30), "jours_cotes": int(quoted.sum()), "jours_sans_cotation": int((~quoted).sum()),
            "coupee_a": report.get("cut_at"), "volume_median_usdt_derniere_annee": float(volume.median()) if len(volume) else None,
            "part_des_jours_dans_l_univers": float(member.mean()) if member is not None else 0.0,
            "renommage": next((f"{k} → {v['nouvelle']}" for k, v in recherche.RENAMES.items() if symbol in (k, v.get("nouvelle"))), None)}


def dependence(c: pd.DataFrame, member: pd.DataFrame) -> dict:
    """Par année : corrélation moyenne des rendements hebdomadaires des membres et part de la première composante."""
    week = c.iloc[::7]
    r = np.log(week / week.shift(1))
    m = member.iloc[::7].reindex(columns=r.columns, fill_value=False)
    years = pd.to_datetime(r.index.to_numpy() * moteur.DAY_MS, unit="ms").year
    out = {}
    for y in sorted(set(years)):
        sel = years == y
        cols = [s for s in r.columns if m[s][sel].mean() >= 0.5]
        block = r.loc[sel, cols].dropna(how="any")
        if len(cols) < 3 or len(block) < 20:
            continue
        corr = block.corr().to_numpy()
        off = corr[~np.eye(len(cols), dtype=bool)]
        eig = np.linalg.eigvalsh(np.cov(((block - block.mean()) / block.std()).to_numpy().T))
        out[int(y)] = {"membres": len(cols), "semaines": int(len(block)), "correlation_moyenne": float(off.mean()),
                       "part_premiere_composante": float(eig[-1] / eig.sum())}
    return out


def series_row(name: str, s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {"serie": name, "jours": 0}
    gaps = np.diff(s.index.values).astype("timedelta64[D]").astype(int)
    return {"serie": name, "premier_jour": str(s.index[0].date()), "dernier_jour": str(s.index[-1].date()), "jours": int(len(s)),
            "plus_long_trou_jours": int(gaps.max()) if len(gaps) else 0}


def published(name: str) -> dict | None:
    path = RESULTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def costs() -> dict:
    """Coûts et faisabilité lus dans les résultats déjà publiés (aucun nouveau calcul)."""
    frais, ouverture, fais = published("frais-dates-v1.json"), published("ecart-ouverture-v1.json"), published("faisabilite-petit-capital-v1.json")
    out = {}
    if frais:
        out["frais_dates"] = frais.get("bareme")
    if ouverture:
        out["ecart_ouverture"] = {k: v for k, v in ouverture.items() if k not in ("schema", "status", "seconds") and not isinstance(v, list)}
    if fais:
        out["part_des_entrees_faisables"] = {strat: {cap: r["part_faisable"] for cap, r in caps.items()} for strat, caps in fais["resultats"].items()
                                             if isinstance(caps, dict) and all(isinstance(r, dict) and "part_faisable" in r for r in caps.values())}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA C — inventaire de l'univers observable (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/omega-atlas-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
    p = signaux.panel(loaded)
    member = signaux.members(p)
    last_day = moteur._day(signaux.LAST)
    rows = [crypto_row(s, b, r, member[s] if s in member else None, last_day) for s, (b, r) in loaded.items()]
    quoted = [r for r in rows if r["cotee"]]
    memes = set(moteur.MEMECOINS)
    cache = Path(args.cache) / "etf"
    funds = {t: series_row(t, actifs.parse(json.loads((cache / f"{t}.json").read_text(encoding="utf-8"))))
             for t in actifs.UNIVERSE if (cache / f"{t}.json").exists()}
    macro = {f.stem: series_row(f.stem, actifs.parse(json.loads(f.read_text(encoding="utf-8"))))
             for f in sorted(cache.glob("*.json")) if f.stem not in actifs.UNIVERSE}
    out = {"schema": "scalp-omega-atlas-v1", "status": "inventaire descriptif des sources déjà autorisées ; aucune nouvelle source ; aucune exposition ajoutée",
           "omega": ["RADAR", "ATLAS"], "capacites": ["C091", "C092", "C061", "C062", "C098", "C099"],
           "cryptos": {"candidates": len(rows), "cotees": len(quoted), "retirees": sum(r["retiree"] for r in quoted),
                       "coupees_a_une_recotation": sum(bool(r["coupee_a"]) for r in quoted), "memecoins": sorted(m for m in memes if m in loaded),
                       "membres_par_jour_median": float(member.loc[moteur._day(signaux.FIRST):].sum(axis=1).median()), "detail": rows},
           "dependances_hebdomadaires": dependence(p["c"].loc[moteur._day(signaux.FIRST):], member.loc[moteur._day(signaux.FIRST):]),
           "fonds": funds, "series_macro": macro, "couts_et_faisabilite": costs(), "seconds": round(time.time() - t0)}
    c = out["cryptos"]
    print({k: c[k] for k in ("candidates", "cotees", "retirees", "coupees_a_une_recotation", "memecoins", "membres_par_jour_median")})
    for y, d in out["dependances_hebdomadaires"].items():
        print(y, d)
    print("fonds :", {k: (v.get("premier_jour"), v.get("jours")) for k, v in funds.items()})
    print("macro :", {k: (v.get("premier_jour"), v.get("jours")) for k, v in macro.items()})
    print("faisabilité :", json.dumps(out["couts_et_faisabilite"].get("part_des_entrees_faisables"), ensure_ascii=False))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-atlas-v1", "descriptif": True,
                                 "cryptos": {k: c[k] for k in ("candidates", "cotees", "retirees", "coupees_a_une_recotation")},
                                 "dependances": out["dependances_hebdomadaires"]}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
