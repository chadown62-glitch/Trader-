"""Programme multi-actifs — vague 1 : la grille MIRROR (45 variantes) sur l'historique réel des huit instruments prioritaires.

Cadrage : SPEC.md, « Extension du Sprint 5 — programme multi-actifs et cerveau central V4 ». Simulation exclusive, positions
longues ou nulles décidées à la clôture et exécutées le lendemain, aucune lecture du futur. Historique déjà consulté par
d'autres études : ce n'est pas une validation aveugle.

Instruments : BTC, ETH, SOL, XMR (archives journalières Binance au comptant, XMR jusqu'à son retrait du 20/02/2024) ;
or (GLD), argent (SLV, autorisé le 27 novembre 2026), Nasdaq-100 (QQQ), S&P 500 (SPY) (cours ajustés Yahoo). Arrêt au
31 août 2026 : la période réservée n'est jamais lue.

Coûts par côté, conventions existantes du dépôt : cryptos 0,075 % + 0,05 % de glissement (moteur.FEE), fonds 0,10 %
(actifs.SIDE_COST) ; robustesse à coûts doublés.

Jugement (standard du cadrage, calibré par MIRROR v4 et v5), 34 tests de Newey-West unilatéraux corrigés ensemble par Holm :
- par instrument : deux meilleures variantes sur les 70 % premiers de son historique, test sur les 30 % suivants (16) ;
- collectif : deux meilleures variantes par Sharpe moyen d'apprentissage, test du portefeuille équipondéré de leurs
  poches hors échantillon (2) ;
- individuel : ces deux variantes collectives, instrument par instrument (16).
Une stratégie ne compte pour un instrument que si elle y est validée individuellement ; la contribution marginale n'est que
descriptive (mesurée sur la fenêtre de validation, elle ne filtre pas les faux selon MIRROR v4).

Lancement : python -m sentinelle.scalp.multi_vague1
            python -m sentinelle.scalp.multi_vague1 --descriptif   (mesure descriptive a posteriori, sans validation)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import actifs, essais, mirror, moteur, signaux

INSTRUMENTS = {"BTC": ("crypto", "BTCUSDT"), "ETH": ("crypto", "ETHUSDT"), "SOL": ("crypto", "SOLUSDT"), "XMR": ("crypto", "XMRUSDT"),
               "OR": ("fonds", "GLD"), "ARGENT": ("fonds", "SLV"), "NASDAQ100": ("fonds", "QQQ"), "SP500": ("fonds", "SPY")}
CALENDAR = {"crypto": 365, "fonds": 252}
SIDE_COST = {"crypto": moteur.FEE + 0.0005, "fonds": actifs.SIDE_COST}
LAST_DAY = "2026-08-31"
TRAIN_SHARE, TOP, ALPHA = mirror.TRAIN_SHARE, mirror.TOP, mirror.ALPHA
CAPITALS = (30, 100, 1_000, 10_000)
MIN_ORDER_USD = {"crypto": 5.0}  # minimum d'ordre au comptant Binance ; fonds : une part entière


def load_closes(cache_dir, client: httpx.Client) -> dict[str, pd.Series]:
    """Clôtures journalières (index : dates), arrêtées au 31 août 2026 ; jamais au-delà."""
    end_ts = int(dt.datetime.fromisoformat(LAST_DAY + "T23:59:59+00:00").timestamp())
    out = {}
    for name, (kind, ticker) in INSTRUMENTS.items():
        if kind == "crypto":
            bars, _ = signaux.load_bars(ticker, cache_dir, client)
            s = pd.Series(bars["c"].to_numpy(), index=pd.to_datetime(bars.index.to_numpy() * moteur.DAY_MS, unit="ms"))
        else:
            s = actifs.download(ticker, cache_dir, end_ts=end_ts)
        out[name] = s.dropna().loc[:LAST_DAY]
    return out


def net_returns(r: np.ndarray, pos: np.ndarray, side: float) -> np.ndarray:
    """Rendements nets (jours × variantes) : position de t appliquée au rendement de t + 1, chaque changement payé à
    l'exécution, au coût par côté de l'instrument."""
    gross = np.zeros_like(pos)
    gross[1:] = pos[:-1] * (np.exp(r[1:]) - 1)[:, None]
    turns = np.abs(np.diff(np.vstack([np.zeros((1, pos.shape[1])), pos]), axis=0))
    gross[1:] -= turns[:-1] * side
    return gross


def trade_stats(pos: np.ndarray, net: np.ndarray, cal: int) -> dict:
    """Mesures d'une variante sur une fenêtre : rendement géométrique annuel, drawdown, transactions, espérance,
    profit factor, part des mois positifs."""
    wealth = np.cumprod(1 + net)
    peak = np.maximum.accumulate(np.r_[1.0, wealth])[1:]
    held = np.r_[0.0, pos[:-1]] > 0  # jours où la position de la veille porte le rendement
    trades, cur = [], None
    for x, h in zip(net, held):
        if h:
            cur = (1 + x) * (cur if cur is not None else 1.0)
        elif cur is not None:
            trades.append(cur * (1 + x) - 1)  # le coût de sortie est payé le jour suivant la dernière position
            cur = None
    if cur is not None:
        trades.append(cur - 1)
    t = np.array(trades)
    gains, losses = t[t > 0].sum(), -t[t < 0].sum()
    months = np.array([np.prod(1 + c) - 1 for c in np.array_split(net, max(1, round(len(net) / (cal / 12))))])
    return {"rendement_geometrique_annuel": float(wealth[-1] ** (cal / len(net)) - 1), "drawdown_max": float((1 - wealth / peak).max()),
            "sharpe": float(net.mean() / net.std(ddof=1) * np.sqrt(cal)) if net.std(ddof=1) > 0 else 0.0,
            "transactions": int(len(t)), "esperance_par_transaction": float(t.mean()) if len(t) else None,
            "profit_factor": float(gains / losses) if losses > 0 else None, "part_des_mois_positifs": float((months > 0).mean())}


def prepare(closes: dict[str, pd.Series]) -> dict:
    """Positions et rendements nets des 45 variantes pour chaque instrument, sur son propre historique."""
    out = {}
    for name, s in closes.items():
        kind = INSTRUMENTS[name][0]
        r = np.diff(np.log(s.to_numpy()))
        labels, pos = mirror.positions(r[:, None])
        pos = pos[:, :, 0]
        out[name] = {"dates": s.index[1:], "r": r, "pos": pos, "labels": labels, "kind": kind, "cal": CALENDAR[kind],
                     "net": net_returns(r, pos, SIDE_COST[kind]), "net2": net_returns(r, pos, 2 * SIDE_COST[kind]),
                     "cut": int(len(r) * TRAIN_SHARE)}
    return out


def collective_series(prep: dict, j: int) -> pd.Series:
    """Portefeuille équipondéré des poches hors échantillon d'une variante : chaque poche compte du début à la fin de sa
    propre fenêtre (rendement nul les jours sans cotation, par exemple les fins de semaine pour les fonds)."""
    cols = {}
    for name, x in prep.items():
        sleeve = pd.Series(x["net"][x["cut"]:, j], index=x["dates"][x["cut"]:])
        span = pd.date_range(sleeve.index[0], sleeve.index[-1], freq="D")
        cols[name] = sleeve.reindex(span).fillna(0.0)
    return pd.DataFrame(cols).mean(axis=1, skipna=True)


def judge(prep: dict) -> dict:
    names = list(prep)
    is_sharpe = {n: mirror.annual_sharpe(x["net"][:x["cut"]]) for n, x in prep.items()}
    coll = [int(j) for j in np.argsort(-np.mean([is_sharpe[n] for n in names], axis=0))[:TOP]]
    tests = [("collectif", j, None) for j in coll] + [("individuel", j, n) for j in coll for n in names]
    tests += [("par_instrument", int(j), n) for n in names for j in np.argsort(-is_sharpe[n])[:TOP]]
    pvals = [mirror.newey_west_p(collective_series(prep, j).to_numpy() if n is None else prep[n]["net"][prep[n]["cut"]:, j]) for _, j, n in tests]
    adjusted = essais.holm(pvals)
    labels = prep[names[0]]["labels"]
    rows = [{"jugement": k, "variante": labels[j], "instrument": n, "p": p, "p_holm": h, "valide": bool(h < ALPHA)}
            for (k, j, n), p, h in zip(tests, pvals, adjusted)]
    coll_ok = {r["variante"] for r in rows if r["jugement"] == "collectif" and r["valide"]}
    retained = {(r["variante"], r["instrument"]) for r in rows if r["valide"] and (r["jugement"] == "par_instrument" or
                                                                              (r["jugement"] == "individuel" and r["variante"] in coll_ok))}
    return {"tests": rows, "validations_collectives": sorted(coll_ok), "retenues": sorted(retained)}


def describe(prep: dict, retained) -> dict:
    """Mesures hors échantillon de chaque couple retenu, références simples et faisabilité aux quatre capitaux."""
    out = {}
    for variant, name in retained:
        x = prep[name]
        j = x["labels"].index(variant)
        oos, oos2 = x["net"][x["cut"]:, j], x["net2"][x["cut"]:, j]
        thirds = np.array_split(oos, 3)
        hold = np.exp(x["r"][x["cut"]:]) - 1
        out[f"{variant}@{name}"] = {**trade_stats(x["pos"][x["cut"]:, j], oos, x["cal"]),
                                    "couts_doubles_positif": bool(oos2.mean() > 0), "tiers_positifs": int(sum(t.mean() > 0 for t in thirds)),
                                    "detention_simple": trade_stats(np.ones(len(hold)), hold, x["cal"])}
    return out


def feasibility(closes: dict[str, pd.Series], retained) -> dict:
    """Un ordre par poche au plus petit capital : minimum Binance pour les cryptos, une part entière pour les fonds (prix en
    dollars au dernier jour ; conversion en euros non appliquée ici)."""
    n = max(1, len(retained))
    out = {}
    for capital in CAPITALS:
        sleeve = capital / n
        out[str(capital)] = {name: bool(sleeve >= (MIN_ORDER_USD["crypto"] if INSTRUMENTS[name][0] == "crypto" else float(closes[name].iloc[-1])))
                             for _, name in retained}
    return out


def descriptive(prep: dict, closes: dict[str, pd.Series], verdict: dict) -> dict:
    """Mesure déclarée a posteriori (registre, 28 novembre) : candidats par instrument, hors échantillon et après coûts,
    face à la détention simple ; faisabilité d'un portefeuille des huit poches. Aucune valeur de validation."""
    cands = [(r["variante"], r["instrument"]) for r in verdict["tests"] if r["jugement"] == "par_instrument"]
    return {"statut": "descriptif a posteriori ; ne modifie pas le verdict préenregistré", "candidats_par_instrument": describe(prep, cands),
            "p_holm": {f"{r['variante']}@{r['instrument']}": r["p_holm"] for r in verdict["tests"] if r["jugement"] == "par_instrument"},
            "faisabilite_huit_poches": feasibility(closes, [(None, n) for n in INSTRUMENTS])}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Programme multi-actifs — vague 1 (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/multi-v1/vague1.json")
    ap.add_argument("--trials", default="research/multi-v1/trials.jsonl")
    ap.add_argument("--descriptif", action="store_true", help="mesure descriptive a posteriori des candidats (sans validation)")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        closes = load_closes(args.cache, client)
    prep = prepare(closes)
    verdict = judge(prep)
    if args.descriptif:
        out = descriptive(prep, closes, verdict)
        target = Path(args.out).with_name(Path(args.out).stem + "-descriptif.json")
        target.write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(out["faisabilite_huit_poches"], ensure_ascii=False))
        return out
    out = {"schema": "multi-vague1", "status": "historique déjà consulté ; simulation exclusive ; aucune promotion",
           "couverture": {n: {"debut": str(s.index[0].date()), "fin": str(s.index[-1].date()), "jours": int(len(s)),
                              "annees_hors_echantillon": round((len(s) - 1 - prep[n]["cut"]) / prep[n]["cal"], 2)} for n, s in closes.items()},
           "couts_par_cote": SIDE_COST, **verdict, "mesures_hors_echantillon": describe(prep, verdict["retenues"]),
           "faisabilite_capitaux": feasibility(closes, verdict["retenues"]), "seconds": round(time.time() - t0)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("validations_collectives", "retenues")}, ensure_ascii=False))
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "multi-vague1", "kind": "result",
                                 "validations_collectives": out["validations_collectives"], "retenues": out["retenues"],
                                 "tests_valides": [r for r in out["tests"] if r["valide"]]}, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
