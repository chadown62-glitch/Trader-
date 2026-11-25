"""Écart entre l'exécution simulée et la cotation accessible (C099).

Le simulateur exécute chaque entrée et chaque sortie de B0 et de B1 au cours d'ouverture du jour (00:00 UTC). Un ordre au
marché réel s'exécute pendant les premières minutes, au prix moyen des échanges. Sur un échantillon tiré d'avance
(graine 0) des entrées et sorties simulées, on lit les archives minute publiées (empreinte vérifiée) et on mesure l'écart
défavorable entre le prix moyen pondéré par les volumes des 1, 5 et 15 premières minutes et l'ouverture simulée : positif
quand l'acheteur paie plus cher, ou quand le vendeur reçoit moins, que dans la simulation. Il est comparé au glissement
supposé (0,05 % par côté). On relève aussi le volume échangé la première minute, à rapprocher de la taille des ordres.
Limites déclarées : aucun carnet d'ordres historique (écart achat-vente NON MESURÉ) ; la période réservée est exclue de
l'échantillon (garde-fou de data.download_day) ; mesure seulement, aucune règle de trading n'est modifiée.

Lancement : python -m sentinelle.scalp.ouverture
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

from . import data, moteur, recherche, signaux

WINDOWS = (1, 5, 15)
SAMPLE = 250  # transactions tirées par stratégie
SEED = 0
ASSUMED_SLIPPAGE = 0.0005  # glissement par côté supposé dans signaux.COST (0,075 % de frais + 0,05 %)


def read_minutes(path) -> pd.DataFrame:
    """Archive minute : heure d'ouverture (ms), ouverture, volume en unités et en USDT."""
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    d = pd.read_csv(io.BytesIO(raw), header=None, usecols=[0, 1, 5, 7])
    if not d.empty and not pd.api.types.is_numeric_dtype(d[0]):  # ligne d'en-tête éventuelle
        d = d.iloc[1:].astype(float)
    d.columns = ["t", "o", "v", "qv"]
    t = d["t"].astype("int64")
    d["t"] = np.where(t > 10**14, t // 1000, t)
    return d.sort_values("t").reset_index(drop=True)


def adverse_gap(minutes: pd.DataFrame, day_open: float, side: int, k: int) -> float | None:
    """Écart défavorable du prix moyen des k premières minutes à l'ouverture simulée (side : +1 achat, −1 vente)."""
    m = minutes.iloc[:k]
    if m["v"].sum() <= 0 or not np.isfinite(day_open) or day_open <= 0:
        return None
    return float(side * (m["qv"].sum() / m["v"].sum() / day_open - 1))


def executions(W: np.ndarray, days, columns) -> list[tuple[int, str, int]]:
    """Entrées (+1) et sorties (−1) simulées à l'ouverture : (jour, paire, sens)."""
    out, prev = [], np.zeros(W.shape[1])
    for i, d in enumerate(days):
        out += [(int(d), columns[j], 1) for j in np.flatnonzero((W[i] > 0) & (prev <= 0))]
        out += [(int(d), columns[j], -1) for j in np.flatnonzero((W[i] <= 0) & (prev > 0))]
        prev = W[i]
    return out


def draw(trades: list, opens: pd.DataFrame, n: int, seed: int) -> list:
    """Tirage fixé d'avance parmi les exécutions à l'ouverture réellement cotées, hors période réservée."""
    lo, hi = data.RESERVED_MS[0] // moteur.DAY_MS, data.RESERVED_MS[1] // moteur.DAY_MS
    eligible = [t for t in trades if not lo <= t[0] <= hi and np.isfinite(opens.at[t[0], t[1]])]
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(eligible), size=min(n, len(eligible)), replace=False)
    return [eligible[i] for i in sorted(pick)]


def summarize(rows: list[dict]) -> dict:
    out = {"executions": len(rows), "minute_absente": sum(r.get("absente", False) for r in rows)}
    ok = [r for r in rows if not r.get("absente")]
    out["premiere_minute_egale_a_l_ouverture"] = round(float(np.mean([r["ouverture_identique"] for r in ok])), 4) if ok else None
    for k in WINDOWS:
        g = np.array([r[f"ecart_{k}min"] for r in ok if r[f"ecart_{k}min"] is not None]) * 1e4
        out[f"{k} min"] = {"n": int(len(g)), "moyenne_pb": round(float(g.mean()), 2), "mediane_pb": round(float(np.median(g)), 2),
                           "p90_pb": round(float(np.percentile(g, 90)), 2), "p95_pb": round(float(np.percentile(g, 95)), 2),
                           "part_au_dela_du_glissement_suppose": round(float((g > ASSUMED_SLIPPAGE * 1e4).mean()), 4)} if len(g) else None
    q = [r["volume_premiere_minute_usdt"] for r in ok]
    out["volume_premiere_minute_usdt_median"] = round(float(np.median(q)), 0) if q else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Écart entre exécution simulée et cotation accessible (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/ecart-ouverture-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            loaded = dict(zip(recherche.CANDIDATES, pool.map(lambda s: signaux.load_bars(s, args.cache, client), recherche.CANDIDATES)))
        p = signaux.panel(loaded)
        member, r, fam = signaux.members(p), signaux.asset_returns(p), signaux.families(p)
        first = moteur._day(signaux.FIRST)
        grid = p["c"].index
        keep = grid >= first
        cols = list(p["c"].columns)
        vote = pd.DataFrame(np.stack([fam[n].to_numpy() for n in ("F1_momentum", "F6_volume_pression", "F8_momentum_ajuste")]).sum(axis=0) >= 2,
                            index=grid, columns=cols)
        weights = {"B0 momentum simple": signaux.net(fam["F1_momentum"], member, p, r, signaux.COST)[1],
                   "B1 vote, volatilité visée 30 %": signaux.net(vote, member, p, r, signaux.COST, 0.30)[1]}
        samples = {name: draw(executions(W[keep], grid[keep], cols), p["o"], SAMPLE, SEED) for name, W in weights.items()}
        needed = sorted({(d, s) for rows in samples.values() for d, s, _ in rows})
        minute_dir = Path(args.cache) / "1m-ouverture"

        def fetch(key):
            d, s = key
            day = str(pd.to_datetime(d * moteur.DAY_MS, unit="ms").date())
            try:
                return key, read_minutes(data.download_day(s, day, minute_dir, client=client))
            except httpx.HTTPStatusError:
                return key, None

        with ThreadPoolExecutor(8) as pool:
            minutes = dict(pool.map(fetch, needed))
    out = {"schema": "scalp-ecart-ouverture-v1", "status": "mesure sur historique déjà consulté ; échantillon tiré d'avance ; aucune règle modifiée",
           "capacites": ["C099"], "methode": {"fenetres_minutes": list(WINDOWS), "echantillon_par_strategie": SAMPLE, "graine": SEED,
                                              "glissement_suppose_par_cote": ASSUMED_SLIPPAGE, "periode_reservee_exclue": [data.RESERVED_FROM, data.RESERVED_TO]},
           "ecart_achat_vente": "NON MESURÉ : aucun carnet d'ordres historique public", "resultats": {}}
    for name, rows in samples.items():
        measured = []
        for d, s, side in rows:
            m = minutes.get((d, s))
            day_open = float(p["o"].at[d, s])
            if m is None or m.empty:
                measured.append({"absente": True})
                continue
            row = {"ouverture_identique": bool(np.isclose(m["o"].iloc[0], day_open, rtol=1e-9)),
                   "volume_premiere_minute_usdt": float(m["qv"].iloc[0])}
            row.update({f"ecart_{k}min": adverse_gap(m, day_open, side, k) for k in WINDOWS})
            measured.append(row)
        out["resultats"][name] = {"tout": summarize(measured),
                                  "entrees": summarize([x for x, (_, _, sd) in zip(measured, rows) if sd == 1]),
                                  "sorties": summarize([x for x, (_, _, sd) in zip(measured, rows) if sd == -1])}
    out["seconds"] = round(time.time() - t0)
    for name, res in out["resultats"].items():
        t = res["tout"]
        print(name, {k: t[k] for k in ("executions", "minute_absente", "premiere_minute_egale_a_l_ouverture", "volume_premiere_minute_usdt_median")})
        for k in WINDOWS:
            print(f"  {k:>2} min : {t[f'{k} min']}")
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "ecart-ouverture-v1",
                                 "resultats": {n: {f"{k} min": res["tout"][f"{k} min"] for k in WINDOWS} for n, res in out["resultats"].items()}},
                                ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
