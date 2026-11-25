"""Risques extrêmes, plafond par groupe d'actifs corrélés et stops avec écart de cotation (lot 3 : C079, C074, C067).

- C079 scénarios de pertes extrêmes : les 10 pires semaines du BTC (7 jours glissants, sans chevauchement) de 2018 à
  2026, et ce qu'y ont fait B0, B1, le BTC et l'univers à poids égaux ; choc instantané de −30 % sur toute la crypto
  détenue, appliqué au jour de plus forte exposition ; invariant : exposition jamais supérieure au capital (sans levier).
- C074 plafond par groupe corrélé, fixé a priori : chaque jour, parmi les actifs en signal, au plus 3 positions dont la
  corrélation des rendements sur 60 jours (connue la veille) dépasse 0,8 deux à deux ; priorité au meilleur rendement sur
  28 jours. Témoin : le même nombre de positions, choisies par le seul rendement sur 28 jours, sans regard à la corrélation.
- C067 stops : sur les alertes du moteur court terme (exécution minute par minute, campaign.build), part des stops
  exécutés au-delà du niveau prévu (écart de cotation) et perte supplémentaire en R.
Mesure seulement : rien n'entre dans une décision ; Guardian et Risk Commander ne sont pas touchés.

Lancement : python -m sentinelle.scalp.risques --cache data/scalp-cache --alertes <dossier des alertes>
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import ablation, moteur, recherche, signaux
from .trade import STOP

WORST_WEEKS, WEEK_DAYS = 10, 7
SHOCK = -0.30
CORR_WINDOW, CORR_LIMIT, CLUSTER_MAX = 60, 0.8, 3
ALERTS = "alerts-v4-2023-09-2026-08.pkl"


def worst_windows(close: pd.Series, n: int = WORST_WEEKS, days: int = WEEK_DAYS) -> list[tuple[int, int]]:
    """Les n pires variations sur « days » jours, sans chevauchement : (premier jour, dernier jour)."""
    ret = (close / close.shift(days) - 1).dropna().sort_values()
    picked: list[tuple[int, int]] = []
    for end, _ in ret.items():
        start = int(end) - days
        if all(end < a or start > b for a, b in picked):
            picked.append((start, int(end)))
        if len(picked) == n:
            break
    return picked


def window_return(daily: pd.Series, start: int, end: int) -> float:
    """Rendement composé des jours start + 1 … end (variation de l'équité entre les clôtures de start et de end)."""
    seg = daily.loc[start + 1:end]
    return float(np.prod(1 + seg.to_numpy()) - 1)


def cluster_cap(signal: pd.DataFrame, member: pd.DataFrame, c: pd.DataFrame, score: pd.DataFrame,
                limit: float = CORR_LIMIT, cap: int = CLUSTER_MAX, window: int = CORR_WINDOW) -> tuple[pd.DataFrame, pd.Series]:
    """Signal plafonné : par ordre de score décroissant, un actif est gardé sauf s'il est corrélé (> limit, rendements des
    « window » jours jusqu'à la veille) à « cap » actifs déjà gardés. Renvoie aussi le nombre d'actifs gardés par jour."""
    r = np.log(c).diff()
    out = pd.DataFrame(False, index=signal.index, columns=signal.columns)
    kept_n = pd.Series(0, index=signal.index)
    cand = (signal & member).to_numpy()
    sc = score.to_numpy()
    R = r.to_numpy()
    cols = np.arange(signal.shape[1])
    for i in range(len(signal)):
        idx = cols[cand[i]]
        if len(idx) == 0:
            continue
        idx = idx[np.argsort(-np.nan_to_num(sc[i, idx], nan=-np.inf))]
        past = R[max(0, i - window):i][:, idx]  # rendements jusqu'à la veille : la clôture du jour n'entre pas
        valid = np.isfinite(past).sum(axis=0) >= window // 2
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = pd.DataFrame(past).corr(min_periods=window // 2).to_numpy()
        kept: list[int] = []
        for a in range(len(idx)):
            close_to = sum(1 for b in kept if valid[a] and valid[b] and corr[a, b] > limit)
            if close_to < cap:
                kept.append(a)
        out.iloc[i, idx[kept]] = True
        kept_n.iloc[i] = len(kept)
    return out, kept_n


def top_k(signal: pd.DataFrame, member: pd.DataFrame, score: pd.DataFrame, k: pd.Series) -> pd.DataFrame:
    """Témoin : chaque jour, les k meilleurs actifs en signal par le seul score."""
    cand = (signal & member)
    ranked = score.where(cand).rank(axis=1, ascending=False, method="first")
    return ranked.le(k, axis=0) & cand


def stop_gaps(alerts: pd.DataFrame) -> dict:
    """Stops exécutés au-delà du niveau prévu (ouverture de la minute en dessous du stop) et perte supplémentaire en R."""
    s = alerts[alerts["outcome"] == STOP]
    risk = s["entry"] - s["stop"]
    extra_r = ((s["stop"] - s["exit"]) / risk).clip(lower=0.0)
    gapped = extra_r > 1e-9
    return {"stops": int(len(s)), "part_avec_ecart": round(float(gapped.mean()), 4) if len(s) else None,
            "perte_supplementaire_moyenne_r": round(float(extra_r[gapped].mean()), 4) if gapped.any() else 0.0,
            "perte_supplementaire_p95_r": round(float(extra_r[gapped].quantile(0.95)), 4) if gapped.any() else 0.0,
            "pire_perte_supplementaire_r": round(float(extra_r.max()), 4) if len(s) else None,
            "perte_supplementaire_totale_r": round(float(extra_r.sum()), 2), "r_total_des_stops": round(float(s["r"].sum()), 2)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Risques extrêmes, plafond par groupe corrélé et stops (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--alertes", default=None, help="dossier contenant les alertes de campaign.build (alerts-v4-…)")
    ap.add_argument("--out", default="research/scalp-v1/risques-extremes-v1.json")
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
    r28 = p["c"] / p["c"].shift(28) - 1
    b0_ret, b0_w = signaux.net(fam["F1_momentum"], member, p, r, signaux.COST)
    b1_raw, b1_w = signaux.net(vote, member, p, r, signaux.COST, 0.30)
    b0, b1 = b0_ret.loc[first:], signaux.mix(b1_raw.loc[first:], rg)
    ew = signaux.equal_weight_monthly(member, r, signaux.COST).loc[first:]
    btc_daily = (btc / btc.shift(1) - 1).loc[first:].fillna(0.0)
    # C079 : pires semaines du BTC et choc instantané
    scenarios = []
    for a, b in worst_windows(btc.loc[first:]):
        scenarios.append({"du": str(pd.to_datetime(a * moteur.DAY_MS, unit="ms").date()), "au": str(pd.to_datetime(b * moteur.DAY_MS, unit="ms").date()),
                          "btc_pct": 100 * window_return(btc_daily, a, b), "univers_pct": 100 * window_return(ew, a, b),
                          "b0_pct": 100 * window_return(b0, a, b), "b1_pct": 100 * window_return(b1, a, b)})
    keep = grid >= first
    crypto_eq, funds_eq = 0.35 * np.cumprod(1 + b1_raw.loc[first:].to_numpy()), 0.65 * np.cumprod(1 + rg.to_numpy())
    crypto_share = np.r_[0.35, (crypto_eq / (crypto_eq + funds_eq))[:-1]]  # part de la poche crypto à l'ouverture (sans transfert)
    expo = {"B0": b0_w[keep].sum(axis=1), "B1": crypto_share * b1_w[keep].sum(axis=1)}
    shock = {n: {"exposition_max": float(e.max()), "exposition_mediane": float(np.median(e)), "perte_choc_pct_au_pire": float(-100 * SHOCK * e.max()),
                 "invariant_sans_levier": bool(e.max() <= 1.0 + 1e-9)} for n, e in expo.items()}
    # C074 : plafond par groupe corrélé, contre base et contre témoin
    rows, controls = [], []
    for name, sig, vt in (("B0", fam["F1_momentum"], None), ("B1", vote, 0.30)):
        capped, k = cluster_cap(sig, member, p["c"], r28)
        witness = top_k(sig, member, r28, k)

        def run(s, vt=vt, name=name):
            x = signaux.net(s, member, p, r, signaux.COST, vt)[0].loc[first:]
            return signaux.mix(x, rg) if name == "B1" else x
        ref = run(sig)
        rows.append(ablation.compare(ref, run(capped), label=f"C074 plafond de 3 par groupe corrélé (> 0,8), {name}", costs="0,125 %/côté", btc_close=btc))
        controls.append(ablation.compare(ref, run(witness), label=f"témoin : même nombre de positions sans regard à la corrélation, {name}",
                                         costs="0,125 %/côté", btc_close=btc))
        controls[-1]["positions_moyennes_gardees"] = float(k.loc[first:][k.loc[first:] > 0].mean())
    rows = ablation.adjust(rows)
    # C067 : stops et écarts de cotation sur les alertes minute
    gaps = None
    if args.alertes and (Path(args.alertes) / ALERTS).exists():
        alerts, _ = pickle.loads((Path(args.alertes) / ALERTS).read_bytes())
        gaps = stop_gaps(alerts)
    out = {"schema": "scalp-risques-extremes-v1", "status": "mesure sur historique déjà consulté ; règles fixées a priori ; aucune intégration",
           "capacites": ["C079", "C074", "C067"], "pires_semaines_btc": scenarios, "choc_instantane": {"choc": SHOCK, **shock},
           "plafond_groupes_correles": {"ablations": rows, "temoins": controls},
           "stops_ecarts": gaps if gaps is not None else "NON MESURÉ : alertes minute absentes du dossier fourni",
           "liquidite_insuffisante_a_la_sortie": "NON MESURÉ : volume de la minute de sortie non conservé dans les alertes",
           "seconds": round(time.time() - t0)}
    for s in scenarios:
        print(f"{s['du']} → {s['au']} : BTC {s['btc_pct']:+.1f} % | univers {s['univers_pct']:+.1f} % | B0 {s['b0_pct']:+.1f} % | B1 {s['b1_pct']:+.1f} %")
    print("choc :", shock)
    for x, c in zip(rows, controls):
        print(f"{x['ablation'][:52]:52} écart {x['ecart_hebdo_moyen_pct']:+.4f} %/sem Holm {x['p_holm']:.3f} | baisse {x['sans']['max_drawdown_pct']:.1f} → "
              f"{x['avec']['max_drawdown_pct']:.1f} % | témoin {c['ecart_hebdo_moyen_pct']:+.4f}, baisse {c['avec']['max_drawdown_pct']:.1f} %, positions {c['positions_moyennes_gardees']:.2f}")
    print("stops :", gaps)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "risques-extremes-v1",
                                 "plafond": {x["ablation"]: {"ecart_hebdo_moyen_pct": x["ecart_hebdo_moyen_pct"], "p_holm": x["p_holm"],
                                                             "pire_baisse": [x["sans"]["max_drawdown_pct"], x["avec"]["max_drawdown_pct"]]} for x in rows},
                                 "stops": gaps, "choc": shock}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
