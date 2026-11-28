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

Version 2 (préenregistrée à part après la revue indépendante du 28 novembre ; la v1 et ses résultats restent archivés) :
- exécution à l'ouverture suivante : l'écart de nuit (clôture → ouverture) revient à la position détenue pendant la nuit,
  la séance (ouverture → clôture) à la position après exécution, les frais sont payés à l'exécution ;
- jugement collectif et tests individuels des variantes collectives sur une coupure calendaire commune (1er janvier 2022) :
  sélection sur les seules données antérieures, test sur les suivantes ; poids fixes de 1/8, liquidités hors fenêtre ;
- mesures descriptives : position portée depuis la veille reprise au début de la fenêtre, mois calendaires ;
- faisabilité : plancher économique existant (dmh.router.economic_minimum, 10 € par achat) ; au-dessus, « indéterminé »
  faute de change daté autorisé et de pas de quantité historiques.

Version 2.1 (contre-revue du 28 novembre sur 5a59795 ; les résultats 2.0 restent archivés dans vague1-v2.json) :
- frais appliqués au montant exécuté à l'ouverture (valeur de la position après l'écart de nuit), et non à la valeur de
  la veille ;
- transactions comptées à partir des exécutions (un achat ouvre une transaction, une vente la ferme) : deux transactions
  adjacentes ne sont plus fusionnées ;
- dates de couverture et empreintes SHA-256 des séries réellement utilisées, pour le rejeu.

Lancement : python -m sentinelle.scalp.multi_vague1
            python -m sentinelle.scalp.multi_vague1 --v2                  (version 2.1 ; --version-v2 2.0 pour le rejeu)
            python -m sentinelle.scalp.multi_vague1 --descriptif   (mesure descriptive a posteriori, sans validation)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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


# -- version 2 : exécution à l'ouverture, coupure collective commune, mesures et faisabilité corrigées ------------------
COMMON_CUT = "2022-01-01"
ECONOMIC_FLOOR_EUR = 10.0  # plancher prudent de dmh.router.economic_minimum
V2_VERSIONS = ("2.0", "2.1")
SOURCES = {"crypto": "archives journalières Binance au comptant (signaux.load_bars)", "fonds": "cours ajustés Yahoo (actifs.download)"}


def load_ohlc(cache_dir, client: httpx.Client) -> dict[str, pd.DataFrame]:
    """Ouvertures et clôtures journalières (cours ajustés pour les fonds), arrêtées au 31 août 2026."""
    end_ts = int(dt.datetime.fromisoformat(LAST_DAY + "T23:59:59+00:00").timestamp())
    out = {}
    for name, (kind, ticker) in INSTRUMENTS.items():
        if kind == "crypto":
            bars, _ = signaux.load_bars(ticker, cache_dir, client)
            frame = pd.DataFrame({"o": bars["o"].to_numpy(), "c": bars["c"].to_numpy()},
                                 index=pd.to_datetime(bars.index.to_numpy() * moteur.DAY_MS, unit="ms"))
        else:
            actifs.download(ticker, cache_dir, end_ts=end_ts)  # met le fichier en cache si besoin
            res = json.loads((Path(cache_dir) / "etf" / f"{ticker}.json").read_text(encoding="utf-8"))["chart"]["result"][0]
            q, adj = res["indicators"]["quote"][0], res["indicators"]["adjclose"][0]["adjclose"]
            raw = pd.DataFrame({"o": q["open"], "c": q["close"], "a": adj}, index=pd.to_datetime(res["timestamp"], unit="s").normalize(), dtype=float)
            raw = raw.dropna()
            raw = raw[~raw.index.duplicated(keep="last")]
            frame = pd.DataFrame({"o": raw["o"] * raw["a"] / raw["c"], "c": raw["a"]})
        out[name] = frame.dropna().loc[:LAST_DAY]
    return out


def net_returns_open(o: np.ndarray, c: np.ndarray, pos: np.ndarray, side: float, version: str = "2.1") -> np.ndarray:
    """Rendements nets (jours × variantes) quand la position décidée à la clôture s'exécute à l'ouverture suivante.
    Indice j : du jour j au jour j + 1 des prix ; pos[j] est décidée à la clôture du jour j + 1.
    2.1 : les frais portent sur le montant exécuté à l'ouverture ; 2.0 (archivée) : sur la valeur de la veille."""
    night = (o[1:] / c[:-1] - 1)[:, None]
    day = (c[1:] / o[1:] - 1)[:, None]
    prev = np.vstack([np.zeros((2, pos.shape[1])), pos])[:len(pos)]  # pos[j − 2], tenue pendant la nuit du jour j + 1
    new = np.vstack([np.zeros((1, pos.shape[1])), pos])[:len(pos)]   # pos[j − 1], exécutée à l'ouverture du jour j + 1
    if version == "2.0":
        return (1 + prev * night) * (1 + new * day) - 1 - side * np.abs(new - prev)
    at_open = 1 + prev * night                             # richesse à l'ouverture, avant exécution
    traded = np.abs(new * at_open - prev * (1 + night))    # montant acheté ou vendu à l'ouverture
    return (at_open - side * traded) * (1 + new * day) - 1


def exposure(pos: np.ndarray) -> np.ndarray:
    prev = np.r_[0.0, 0.0, pos][:len(pos)]
    new = np.r_[0.0, pos][:len(pos)]
    return (prev > 0) | (new > 0)


def trade_ids(pos: np.ndarray) -> np.ndarray:
    """Numéro de la transaction à laquelle appartient chaque jour de net (−1 hors position), à partir des exécutions :
    un achat à l'ouverture ouvre une transaction ; le jour de la vente (nuit et frais) appartient encore à la précédente."""
    prev = np.r_[0.0, 0.0, pos][:len(pos)]
    new = np.r_[0.0, pos][:len(pos)]
    ids = np.cumsum((prev <= 0) & (new > 0))
    return np.where((prev > 0) | (new > 0), ids, -1)


def trade_stats_v2(pos: np.ndarray, net: np.ndarray, dates: pd.DatetimeIndex, start: int, cal: int, version: str = "2.1") -> dict:
    """Mesures sur net[start:] ; une position portée depuis la veille de la fenêtre y est reprise ; mois calendaires.
    2.1 : transactions comptées à partir des exécutions ; 2.0 (archivée) : jours d'exposition consécutifs fusionnés."""
    x = net[start:]
    wealth = np.cumprod(1 + x)
    peak = np.maximum.accumulate(np.r_[1.0, wealth])[1:]
    trades, cur = [], None
    if version == "2.0":
        for value, held in zip(x, exposure(pos)[start:]):
            if held:
                cur = (1 + value) * (cur if cur is not None else 1.0)
            elif cur is not None:
                trades.append(cur - 1)
                cur = None
        if cur is not None:
            trades.append(cur - 1)
    else:
        ids = trade_ids(pos)[start:]
        trades = [float(np.prod(1 + x[ids == k]) - 1) for k in np.unique(ids[ids >= 0])]
    t = np.array(trades)
    gains, losses = t[t > 0].sum(), -t[t < 0].sum()
    months = pd.Series(x, index=dates[start:]).groupby(dates[start:].to_period("M")).apply(lambda v: np.prod(1 + v) - 1)
    return {"rendement_geometrique_annuel": float(wealth[-1] ** (cal / len(x)) - 1), "drawdown_max": float((1 - wealth / peak).max()),
            "sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(cal)) if x.std(ddof=1) > 0 else 0.0, "transactions": int(len(t)),
            "esperance_par_transaction": float(t.mean()) if len(t) else None,
            "profit_factor": float(gains / losses) if losses > 0 else None, "part_des_mois_calendaires_positifs": float((months > 0).mean())}


def prepare_v2(ohlc: dict[str, pd.DataFrame], version: str = "2.1") -> dict:
    out = {}
    for name, f in ohlc.items():
        kind = INSTRUMENTS[name][0]
        o, c = f["o"].to_numpy(float), f["c"].to_numpy(float)
        labels, pos = mirror.positions(np.diff(np.log(c))[:, None])
        pos = pos[:, :, 0]
        dates = f.index[1:]
        out[name] = {"dates": dates, "o": o, "c": c, "pos": pos, "labels": labels, "kind": kind, "cal": CALENDAR[kind], "version": version,
                     "net": net_returns_open(o, c, pos, SIDE_COST[kind], version), "net2": net_returns_open(o, c, pos, 2 * SIDE_COST[kind], version),
                     "cut": int(len(pos) * TRAIN_SHARE), "common": int(np.searchsorted(dates, pd.Timestamp(COMMON_CUT)))}
    return out


def collective_series_v2(prep: dict, j: int) -> pd.Series:
    """Poches de poids fixe 1/8 du 1er janvier 2022 au 31 août 2026 ; rendement nul (liquidités) hors cotation ou après un
    retrait (XMR) — les poids ne sont jamais redistribués."""
    span = pd.date_range(COMMON_CUT, LAST_DAY, freq="D")
    total = pd.Series(0.0, index=span)
    for x in prep.values():
        sleeve = pd.Series(x["net"][x["common"]:, j], index=x["dates"][x["common"]:])
        total = total.add(sleeve.reindex(span).fillna(0.0), fill_value=0.0)
    return total / len(prep)


def judge_v2(prep: dict) -> dict:
    names = list(prep)
    own = {n: mirror.annual_sharpe(x["net"][:x["cut"]]) for n, x in prep.items()}
    common = {n: mirror.annual_sharpe(x["net"][:x["common"]]) for n, x in prep.items()}
    coll = [int(j) for j in np.argsort(-np.mean([common[n] for n in names], axis=0))[:TOP]]
    tests = [("collectif", j, None) for j in coll] + [("individuel", j, n) for j in coll for n in names]
    tests += [("par_instrument", int(j), n) for n in names for j in np.argsort(-own[n])[:TOP]]

    def sample(kind, j, n):
        if n is None:
            return collective_series_v2(prep, j).to_numpy()
        x = prep[n]
        return x["net"][x["common"]:, j] if kind == "individuel" else x["net"][x["cut"]:, j]
    pvals = [mirror.newey_west_p(sample(k, j, n)) for k, j, n in tests]
    adjusted = essais.holm(pvals)
    labels = prep[names[0]]["labels"]
    rows = [{"jugement": k, "variante": labels[j], "instrument": n, "p": p, "p_holm": h, "valide": bool(h < ALPHA)}
            for (k, j, n), p, h in zip(tests, pvals, adjusted)]
    coll_ok = {r["variante"] for r in rows if r["jugement"] == "collectif" and r["valide"]}
    retained = {(r["variante"], r["instrument"], r["jugement"]) for r in rows if r["valide"] and (r["jugement"] == "par_instrument" or
                                                                                            (r["jugement"] == "individuel" and r["variante"] in coll_ok))}
    return {"tests": rows, "validations_collectives": sorted(coll_ok), "retenues": sorted(retained),
            "selection": {"collective": [labels[j] for j in coll], "par_instrument": {n: [labels[int(j)] for j in np.argsort(-own[n])[:TOP]] for n in names}}}


def describe_v2(prep: dict, verdict: dict) -> dict:
    out = {}
    for r in verdict["tests"]:
        if r["jugement"] != "par_instrument":
            continue
        x = prep[r["instrument"]]
        j = x["labels"].index(r["variante"])
        hold = x["c"][1:] / x["c"][:-1] - 1  # détention simple, clôture à clôture, alignée sur net
        out[f"{r['variante']}@{r['instrument']}"] = {
            **trade_stats_v2(x["pos"][:, j], x["net"][:, j], x["dates"], x["cut"], x["cal"], x["version"]),
            "couts_doubles_positif": bool(x["net2"][x["cut"]:, j].mean() > 0),
            "tiers_positifs": int(sum(t.mean() > 0 for t in np.array_split(x["net"][x["cut"]:, j], 3))),
            "detention_simple": trade_stats_v2(np.ones(len(hold)), hold, x["dates"], x["cut"], x["cal"], x["version"]), "p_holm": r["p_holm"]}
    return out


def series_fingerprints(ohlc: dict[str, pd.DataFrame]) -> dict:
    """Couverture et empreinte SHA-256 des séries réellement utilisées (date, ouverture, clôture), pour le rejeu."""
    out = {}
    for name, f in ohlc.items():
        text = f[["o", "c"]].to_csv(float_format="%.17g", date_format="%Y-%m-%d", lineterminator="\n")
        out[name] = {"source": SOURCES[INSTRUMENTS[name][0]], "symbole": INSTRUMENTS[name][1], "debut": f.index[0].strftime("%Y-%m-%d"),
                     "fin": f.index[-1].strftime("%Y-%m-%d"), "lignes": int(len(f)), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return out


def feasibility_v2() -> dict:
    """Plancher économique existant (10 € par achat, dmh.router.economic_minimum) : une poche en dessous est irréalisable
    quel que soit le change ; au-dessus, la preuve exige un change daté et des pas de quantité qui ne sont pas disponibles."""
    out = {}
    for capital in CAPITALS:
        rows = {}
        for label, sleeve in (("huit_poches_equiponderees", capital / len(INSTRUMENTS)), ("portefeuille_entier_une_position", float(capital))):
            rows[label] = {"poche_eur": sleeve, "statut": "irréalisable" if sleeve < ECONOMIC_FLOOR_EUR else "indéterminé",
                           "motif": "sous le plancher économique de 10 € par achat" if sleeve < ECONOMIC_FLOOR_EUR
                           else "change daté, pas de quantité et revente au bid non démontrés"}
        out[str(capital)] = rows
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Programme multi-actifs — vague 1 (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/multi-v1/vague1.json")
    ap.add_argument("--trials", default="research/multi-v1/trials.jsonl")
    ap.add_argument("--descriptif", action="store_true", help="mesure descriptive a posteriori des candidats (sans validation)")
    ap.add_argument("--v2", action="store_true", help="version 2 corrigée (exécution à l'ouverture, coupure collective commune)")
    ap.add_argument("--version-v2", default="2.1", choices=V2_VERSIONS, help="2.1 par défaut ; 2.0 pour le rejeu des résultats archivés")
    args = ap.parse_args(argv)
    t0 = time.time()
    if args.v2:
        with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
            ohlc = load_ohlc(args.cache, client)
        version = args.version_v2
        prep = prepare_v2(ohlc, version)
        verdict = judge_v2(prep)
        out = {"schema": "multi-vague1-v2", "version": version,
               "status": "corrige la v1 (archivée) ; historique déjà consulté ; simulation exclusive ; aucune promotion",
               "coupure_collective": COMMON_CUT, "couts_par_cote": SIDE_COST, "series": series_fingerprints(ohlc), **verdict,
               "descriptif_candidats": describe_v2(prep, verdict), "faisabilite": feasibility_v2(), "seconds": round(time.time() - t0)}
        target = Path(args.out.replace("vague1", "vague1-v2"))
        previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        archives = previous.pop("archives", {})
        if previous and previous.get("version", "2.0") != version:
            archives[previous.get("version", "2.0")] = previous  # résultats d'une autre version conservés tels quels
        target.write_text(json.dumps({**out, "archives": archives}, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({k: out[k] for k in ("version", "validations_collectives", "retenues", "selection")}, ensure_ascii=False))
        if args.trials:
            with open(args.trials, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     "engine": "multi-vague1-v2" if version == "2.0" else f"multi-vague1-v{version}", "kind": "result",
                                     "validations_collectives": out["validations_collectives"], "retenues": out["retenues"],
                                     "tests_les_plus_proches": sorted(out["tests"], key=lambda r: r["p_holm"])[:5]}, ensure_ascii=False) + "\n")
        return out
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
