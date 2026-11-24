"""Recherche v1 : améliorer la position de fond sans se tromper soi-même (simulation exclusive).

Protocole consigné dans research/scalp-v1/trials.jsonl avant tout calcul (« recherche-v1 ») :
1. diagnostic : v10 et v13, inchangés, sur la période de mise au point (A, septembre 2024 → août 2026), sur la période
   jamais vue par leurs règles (B, 2018 → août 2024) et sur la période réservée déjà consommée (C) ;
2. grille : 648 réglages de la position de fond (élan, nombre de places, tendance de fond, stop suiveur, volatilité
   visée, univers), chacun seul ou mêlé aux fonds indiciels G2, avec ou sans frein de baisse : 3 888 combinaisons ;
3. sélection hors échantillon : chaque année Y de 2020 à 2026, le réglage est choisi sur les seules années
   précédentes, puis appliqué à Y. La courbe obtenue en enchaînant ces années est le seul résultat présenté comme
   attendu : c'est ce qu'aurait donné, en vrai, la méthode « améliorer sur le passé, puis investir ».
Le meilleur réglage sur la seule période A est aussi mesuré sur B et C : l'écart montre le surapprentissage.
Aucun levier, aucune vente à découvert, aucun objectif de rendement comme critère.

Lancement : python -m sentinelle.scalp.recherche   (contrôle du biais de survie : --survie)
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import actifs, core, moteur
from .data import months, read_month

DAY_MS = core.DAY_MS
UNIVERSES = {"8 cryptos": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT"],
             "BTC + ETH": ["BTCUSDT", "ETHUSDT"]}
BASE = core.CoreRules(market_filter="BTCUSDT", keep_rank=5)
V10 = replace(BASE, trailing_stop=0.10, trend_days=50)
V13 = replace(V10, vol_target=0.3)
GRID = {"lookback_days": [7, 14, 28], "top": [1, 2, 3], "trend_days": [None, 50, 100],
        "trailing_stop": [None, 0.10, 0.15, 0.20], "vol_target": [None, 0.5, 0.3]}
MIXES = [1.0, 0.5, 0.35]
BRAKES = [None, 0.15]
DD_CAP = 25.0
WARMUP, FIRST, LAST = "2017-09-01", "2018-01-01", "2026-11-22"
PERIODS = {"A": ("2024-09-01", "2026-08-31"), "B": ("2018-01-01", "2024-08-31"), "C": ("2026-09-01", "2026-11-22")}
OOS_YEARS = list(range(2020, 2027))


def rules_grid():
    keys = list(GRID)
    for values in itertools.product(*(GRID[k] for k in keys)):
        yield replace(BASE, **dict(zip(keys, values)))


def label(universe: str, rules: core.CoreRules, mix: float, brake) -> str:
    return (f"{universe} | élan {rules.lookback_days} j, {rules.top} place(s), tendance {rules.trend_days or '—'}, "
            f"stop {rules.trailing_stop or '—'}, vol {rules.vol_target or '—'} | crypto {mix:.0%}"
            + (f" | frein {brake:.0%}" if brake else ""))


# --- Courbes ------------------------------------------------------------------------------------------------------------

def core_returns(tables, universe, rules, grid: pd.RangeIndex) -> np.ndarray:
    """Rendements journaliers de la position de fond (fin de jour), sur la grille de jours."""
    cols = [s for s in universe if s in tables[1].columns]
    opens, closes, lows = (t[cols] for t in tables)
    res = core.run(opens, closes, int(grid[0]) * DAY_MS, (int(grid[-1]) + 1) * DAY_MS, 1000.0, rules, lows=lows)
    eq = pd.Series({t // DAY_MS - 1: v for t, v in res["curve"]}).reindex(grid).ffill().fillna(1000.0)
    return eq.pct_change().fillna(0.0).to_numpy()


def year_starts(grid: pd.RangeIndex) -> np.ndarray:
    years = pd.to_datetime(grid.to_numpy() * DAY_MS, unit="ms").year.to_numpy()
    return np.r_[True, years[1:] != years[:-1]]


def mix_returns(rc: np.ndarray, rg: np.ndarray, w: float, starts: np.ndarray, cost_c: float, cost_g: float) -> np.ndarray:
    """Poche crypto (part w) et fonds G2, rééquilibrés au 1er janvier (frais du transfert comptés)."""
    if w >= 1.0:
        return rc.copy()
    out = np.empty_like(rc)
    vc, vg = w, 1 - w
    for i in range(len(rc)):
        before = vc + vg  # le coût du rééquilibrage pèse sur le rendement du jour
        if starts[i] and i > 0:
            moved = abs(vc / before - w) * before
            total = before - moved * (cost_c + cost_g)
            vc, vg = w * total, (1 - w) * total
        vc *= 1 + rc[i]
        vg *= 1 + rg[i]
        out[i] = (vc + vg) / before - 1
    return out


def brake_returns(r: np.ndarray, threshold, cost: float) -> np.ndarray:
    """Frein de baisse : exposition réduite de moitié tant que l'équité sans frein est à plus de « threshold » sous son
    plus haut, constaté à la clôture de la veille ; chaque changement d'exposition paie la moitié d'un aller simple."""
    if not threshold:
        return r.copy()
    eq = np.cumprod(1 + r)
    dd = 1 - eq / np.maximum.accumulate(eq)
    exposure = np.r_[1.0, np.where(dd[:-1] > threshold, 0.5, 1.0)]
    switches = np.r_[0.0, np.abs(np.diff(exposure))]
    return exposure * r - switches * cost


# --- Statistiques vectorisées -------------------------------------------------------------------------------------------

def window_stats(r: np.ndarray, days: np.ndarray) -> dict:
    """Statistiques d'une tranche : rendements journaliers « r » des jours « days » (numéros de jour UTC)."""
    eq = np.cumprod(1 + r)
    week = (days + 3) // 7  # semaines du lundi au dimanche
    last = np.r_[week[1:] != week[:-1], True]
    weekly_eq = eq[last]
    w = 100 * (weekly_eq / np.r_[1.0, weekly_eq[:-1]] - 1)
    years = len(days) / 365.25
    dd = 100 * np.max(1 - eq / np.maximum.accumulate(np.r_[1.0, eq])[1:])
    total = eq[-1]
    return {"return_pct": float(100 * (total - 1)), "annual_pct": float(100 * (total ** (1 / years) - 1)),
            "max_drawdown_pct": float(dd), "mean_week_pct": float(w.mean()), "weeks": int(len(w)),
            "positive_weeks": int((w >= 1e-9).sum()), "negative_weeks": int((w <= -1e-9).sum()),
            "weeks_at_reference": int((w >= moteur.WEEKLY_REFERENCE).sum()), "worst_week_pct": float(w.min())}


def per_year(r: np.ndarray, days: np.ndarray) -> dict:
    years = pd.to_datetime(days * DAY_MS, unit="ms").year.to_numpy()
    return {int(y): round(float(100 * (np.prod(1 + r[years == y]) - 1)), 2) for y in np.unique(years)}


def choose(stats_rows: list[dict], rule: str) -> int:
    """R1 : plus forte semaine moyenne parmi les pires baisses ≤ 25 % (sinon la plus faible baisse) ; R2 : rendement
    annuel / pire baisse (au moins 5 %)."""
    if rule == "R1":
        ok = [i for i, s in enumerate(stats_rows) if s["max_drawdown_pct"] <= DD_CAP]
        if not ok:
            return int(np.argmin([s["max_drawdown_pct"] for s in stats_rows]))
        return max(ok, key=lambda i: stats_rows[i]["mean_week_pct"])
    return int(np.argmax([s["annual_pct"] / max(5.0, s["max_drawdown_pct"]) for s in stats_rows]))


def walk_forward(R: np.ndarray, days: np.ndarray, rule: str, window: str, switch_cost: np.ndarray) -> dict:
    """Pour chaque année Y : réglage choisi sur les années précédentes (toutes depuis 2018, ou les deux dernières),
    appliqué à Y. Changer de réglage au 1er janvier paie un aller-retour complet."""
    years = pd.to_datetime(days * DAY_MS, unit="ms").year.to_numpy()
    out = np.zeros(int(np.isin(years, OOS_YEARS).sum()))
    chosen, pos, prev = {}, 0, None
    for y in OOS_YEARS:
        train = (years < y) & (years >= (2018 if window == "toutes" else y - 2))
        rows = [window_stats(R[k][train], days[train]) for k in range(len(R))]
        k = choose(rows, rule)
        seg = R[k][years == y].copy()
        if prev is not None and k != prev:
            seg[0] -= switch_cost[k]
        out[pos:pos + len(seg)] = seg
        pos += len(seg)
        chosen[y] = {"index": k, "train": rows[k]}
        prev = k
    oos_days = days[np.isin(years, OOS_YEARS)]
    return {"returns": out, "days": oos_days, "chosen": chosen}


# --- Contrôle du biais de survie (recherche-v1b) ------------------------------------------------------------------------

CANDIDATES = [c + "USDT" for c in (
    "BTC ETH BNB XRP ADA SOL DOGE LINK DOT LTC BCH EOS XLM TRX ETC NEO IOTA XMR LUNA FTT MATIC AVAX ATOM UNI FIL SHIB NEAR "
    "ICP VET THETA ALGO XTZ WAVES ZEC DASH ONT QTUM OMG SUI APT PEPE TON").split()]
TOP_N = 8
PIT_SLIPPAGE = 0.000339  # le plus élevé mesuré parmi les 8 cryptos ; aucune mesure n'existe pour les cryptos disparues
BREAK = 20.0  # bond d'un jour au-delà de ×20 : changement d'actif sous le même nom (série coupée)


def published_range(symbol: str, last: str, client) -> tuple[str, str] | None:
    """Premier et dernier mois publiés (cryptos retirées de la cote comprises), par sondage tous les 6 mois puis
    dichotomie de chaque côté. Un mois manquant au milieu reste un trou."""
    ms = months(moteur.FIRST_MONTH, last)
    probe = [i for i in range(len(ms) - 1, -1, -6)] + [0]
    hit = next((i for i in probe if moteur._published(symbol, ms[i], client)), None)
    if hit is None:
        return None
    lo, hi = 0, hit
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if moteur._published(symbol, ms[mid], client) else (mid + 1, hi)
    first = lo
    lo, hi = hit, len(ms) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if moteur._published(symbol, ms[mid], client) else (lo, mid - 1)
    return ms[first], ms[lo]


def cut_at_break(bars: pd.DataFrame) -> tuple[pd.DataFrame, int | None]:
    """Coupe la série au premier bond de plus de ×20 en un jour (ouverture ou clôture contre la clôture de la veille) :
    au-delà, ce n'est plus le même actif (par exemple LUNA, recotée sous le même nom après son effondrement). Une chute,
    même brutale, est un fait de marché : elle est gardée."""
    prev = bars["c"].ffill().shift(1)  # dernière clôture connue : une recotation suit souvent des jours sans cotation
    jump = np.maximum(bars["o"], bars["c"]) / prev
    bad = bars.index[jump > BREAK]
    if len(bad) == 0:
        return bars, None
    return bars.loc[:bad[0] - 1], int(bad[0])


def candidate_bars(symbol: str, last_day: str, cache_dir, client) -> tuple[pd.DataFrame, dict]:
    prev = (pd.Timestamp(last_day).replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")
    span = published_range(symbol, prev, client)
    if span is None:
        return pd.DataFrame(columns=["o", "c", "l", "q"]), {"symbol": symbol, "listed": None}
    first, last = span
    current = last == prev
    periods = months(first, last)
    if current:
        stop = pd.Timestamp(last_day)
        periods += [(stop.replace(day=1) + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(stop.day)]
    frames, missing = [], []
    for p in periods:
        try:
            frames.append(read_month(moteur._archive(symbol, p, cache_dir, client)))
        except httpx.HTTPStatusError:
            missing.append(p)
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.assign(day=raw["t"].astype("int64") // DAY_MS).drop_duplicates("day").set_index("day").sort_index()
    bars = raw.reindex(np.arange(raw.index[0], raw.index[-1] + 1))[["o", "c", "l", "v"]].astype(float)
    bars["q"] = bars["v"] * bars["c"]
    bars, cut = cut_at_break(bars.drop(columns="v"))
    return bars, {"symbol": symbol, "listed": first, "last_month": last, "still_listed": current,
                  "missing_periods": missing, "cut_at": str(pd.to_datetime(cut * DAY_MS, unit="ms").date()) if cut else None}


def point_in_time_members(quote: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    """Chaque mois : les n paires au plus fort volume échangé moyen des 30 jours précédant le 1er du mois (connu à la
    date), parmi les paires cotées. L'appartenance vaut pour tous les jours du mois."""
    avg = quote.rolling(30, min_periods=20).mean()
    dates = pd.to_datetime(quote.index.to_numpy() * DAY_MS, unit="ms")
    month = pd.Index(dates.strftime("%Y-%m"))
    first_of_month = np.r_[True, month[1:] != month[:-1]]
    members = pd.DataFrame(False, index=quote.index, columns=quote.columns)
    rows = np.flatnonzero(first_of_month)
    for k, start in enumerate(rows):
        end = rows[k + 1] if k + 1 < len(rows) else len(quote)
        if start == 0:
            continue
        known = avg.iloc[start - 1].dropna()  # volume connu à la clôture de la veille du 1er du mois
        top = known.sort_values(ascending=False).index[:n]
        members.iloc[start:end, [members.columns.get_loc(s) for s in top]] = True
    return members


def survie(cache_dir, client, configs, R, days, switch, keep_series: dict | None = None) -> dict:
    """a) sélection limitée aux réglages BTC + ETH ; b) univers des 8 cryptos les plus échangées à la date.
    « keep_series » (facultatif) reçoit les séries calculées, pour les contrôles statistiques (essais multiples)."""
    out = {"a_btc_eth": {}, "b_univers_a_la_date": {}}
    sub = [k for k, c in enumerate(configs) if c["universe"] == "BTC + ETH"]
    for rule in ("R1", "R2"):
        for window in ("toutes", "deux dernières"):
            res = walk_forward(R[sub], days, rule, window, switch[sub])
            s = window_stats(res["returns"], res["days"]) | {"years": per_year(res["returns"], res["days"])}
            out["a_btc_eth"][f"{rule}, fenêtre {window}"] = s
    with ThreadPoolExecutor(8) as pool:
        loaded = dict(zip(CANDIDATES, pool.map(lambda s: candidate_bars(s, LAST, cache_dir, client), CANDIDATES)))
    out["donnees"] = [r for _, r in loaded.values()]
    bars = {s: b for s, (b, r) in loaded.items() if r.get("listed") and len(b)}
    tables = tuple(pd.DataFrame({s: b[k] for s, b in bars.items()}).sort_index() for k in ("o", "c", "l"))
    quote = pd.DataFrame({s: b["q"] for s, b in bars.items()}).sort_index()
    members = point_in_time_members(quote)
    grid = pd.RangeIndex(moteur._day(WARMUP), moteur._day(LAST) + 1)
    starts = year_starts(grid)
    keep = grid >= moteur._day(FIRST)
    out["univers_par_mois"] = {str(pd.to_datetime(int(d) * DAY_MS, unit="ms").date()): [s.replace("USDT", "") for s in row[row].index]
                               for d, row in members.loc[grid[0]:].iterrows()
                               if pd.to_datetime(int(d) * DAY_MS, unit="ms").day == 1 and pd.to_datetime(int(d) * DAY_MS, unit="ms").month in (1, 7)}
    prices = None
    series, pit_configs, costs = [], [], []
    cost_c = moteur.FEE + PIT_SLIPPAGE
    for rules in rules_grid():
        rules = replace(rules, slippage_per_side=PIT_SLIPPAGE)
        res = core.run(*tables[:2], int(grid[0]) * DAY_MS, (int(grid[-1]) + 1) * DAY_MS, 1000.0, rules, lows=tables[2],
                       members=members)
        eq = pd.Series({t // DAY_MS - 1: v for t, v in res["curve"]}).reindex(grid).ffill().fillna(1000.0)
        rc = eq.pct_change().fillna(0.0).to_numpy()
        if prices is None:
            prices = moteur.etf_prices(cache_dir, LAST)
            rg = moteur.actions_pocket(prices, int(grid[0]) - 400, int(grid[-1]))["equity"].reindex(grid).ffill()
            rg = rg.pct_change().fillna(0.0).to_numpy()
        for w in MIXES:
            rm = mix_returns(rc, rg, w, starts, cost_c, actifs.SIDE_COST)
            side = w * cost_c + (1 - w) * actifs.SIDE_COST
            for b in BRAKES:
                pit_configs.append({"universe": "8 plus échangées à la date", "rules": rules, "mix": w, "brake": b})
                series.append(brake_returns(rm, b, 0.5 * side)[keep])
                costs.append(2 * side)
    P = np.vstack(series)
    pdays = grid.to_numpy()[keep]
    for rule in ("R1", "R2"):
        for window in ("toutes", "deux dernières"):
            res = walk_forward(P, pdays, rule, window, np.array(costs))
            s = window_stats(res["returns"], res["days"]) | {"years": per_year(res["returns"], res["days"])}
            s["choisis"] = {y: label(pit_configs[v["index"]]["universe"], pit_configs[v["index"]]["rules"],
                                     pit_configs[v["index"]]["mix"], pit_configs[v["index"]]["brake"]) for y, v in res["chosen"].items()}
            out["b_univers_a_la_date"][f"{rule}, fenêtre {window}"] = s
            if keep_series is not None:
                keep_series.setdefault("oos", {})[f"{rule}, fenêtre {window}"] = res
    if keep_series is not None:
        keep_series.update(P=P, days=pdays, configs=pit_configs)
    v10 = next(k for k, c in enumerate(pit_configs) if c["mix"] == 1.0 and c["brake"] is None
               and asdict(c["rules"]) == asdict(replace(V10, slippage_per_side=PIT_SLIPPAGE)))
    oos = pdays >= moteur._day("2020-01-01")
    out["b_v10_inchange"] = window_stats(P[v10][oos], pdays[oos]) | {"years": per_year(P[v10][oos], pdays[oos])}
    out["b_v10_inchange_B"] = window_stats(P[v10][mask(pdays, "B")], pdays[mask(pdays, "B")])
    return out


# --- Programme ----------------------------------------------------------------------------------------------------------

def build(tables, prices, progress=None):
    grid = pd.RangeIndex(moteur._day(WARMUP), moteur._day(LAST) + 1)
    starts = year_starts(grid)
    rg = moteur.actions_pocket(prices, int(grid[0]) - 400, int(grid[-1]))["equity"].reindex(grid).ffill()
    rg = rg.pct_change().fillna(0.0).to_numpy()
    configs, series, costs = [], [], []
    for uname, universe in UNIVERSES.items():
        slip = moteur.slippage(universe)
        cost_c = moteur.FEE + slip
        for n, rules in enumerate(rules_grid()):
            rules = replace(rules, slippage_per_side=slip)
            rc = core_returns(tables, universe, rules, grid)
            for w in MIXES:
                rm = mix_returns(rc, rg, w, starts, cost_c, actifs.SIDE_COST)
                side = w * cost_c + (1 - w) * actifs.SIDE_COST
                for b in BRAKES:
                    configs.append({"universe": uname, "rules": rules, "mix": w, "brake": b})
                    series.append(brake_returns(rm, b, 0.5 * side))
                    costs.append(2 * side)
            if progress:
                progress(uname, n)
    keep = grid >= moteur._day(FIRST)
    days = grid.to_numpy()[keep]
    R = np.vstack(series)[:, keep]
    return configs, R, days, np.array(costs), rg[keep]


def mask(days, period):
    a, b = (moteur._day(x) for x in PERIODS[period])
    return (days >= a) & (days <= b)


def diagnostic(tables, days_grid) -> dict:
    out = {}
    universe = UNIVERSES["8 cryptos"]
    for name, rules in (("v10", V10), ("v13", V13)):
        for costs, slip in (("coûts d'origine (0,01 %)", 0.0001), ("coûts mesurés", moteur.slippage(universe))):
            r = core_returns(tables, universe, replace(rules, slippage_per_side=slip), days_grid)
            days = days_grid.to_numpy()
            keep = days >= moteur._day(FIRST)
            r, d = r[keep], days[keep]
            out[f"{name}, {costs}"] = {p: window_stats(r[mask(d, p)], d[mask(d, p)]) for p in PERIODS} | {"years": per_year(r, d)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recherche v1 : grille et sélection hors échantillon (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/recherche-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    ap.add_argument("--survie", action="store_true", help="contrôle du biais de survie (recherche-v1b)")
    args = ap.parse_args(argv)
    t0 = time.time()
    symbols = list(dict.fromkeys(UNIVERSES["8 cryptos"] + UNIVERSES["BTC + ETH"]))
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        tables, reports = moteur.crypto_tables(symbols, LAST, args.cache, client)
        prices = moteur.etf_prices(args.cache, LAST)
        if args.survie:
            configs, R, days, switch, _ = build(tables, prices)
            result = survie(args.cache, client, configs, R, days, switch)
            for part in ("a_btc_eth", "b_univers_a_la_date"):
                for k, v in result[part].items():
                    print(f"{part} {k:24} : {v['mean_week_pct']:+.2f} %/sem, {v['annual_pct']:+.1f} %/an, baisse {v['max_drawdown_pct']:.1f} %, "
                          f"semaines + {v['positive_weeks']}/{v['weeks']}, {v['years']}")
            print("v10 inchangé, univers à la date, 2020 → 2026 :", {m: round(result["b_v10_inchange"][m], 2) for m in ("mean_week_pct", "annual_pct", "max_drawdown_pct")})
            result |= {"schema": "scalp-recherche-v1b", "status": "simulation ; protocole préenregistré (1b24030)", "seconds": round(time.time() - t0)}
            out = Path(args.out)  # le contrôle s'ajoute au rapport de recherche-v1, sous sa propre clé
            report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
            report["controle_biais_de_survie"] = result
            out.write_text(json.dumps(report, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
            if args.trials:
                with open(args.trials, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "recherche-v1b",
                                         **{part: {k: {m: v[m] for m in ("mean_week_pct", "annual_pct", "max_drawdown_pct", "positive_weeks", "weeks")}
                                                   for k, v in result[part].items()} for part in ("a_btc_eth", "b_univers_a_la_date")},
                                         "v10_univers_a_la_date": {m: result["b_v10_inchange"][m] for m in ("mean_week_pct", "annual_pct", "max_drawdown_pct")}},
                                        ensure_ascii=False, default=float) + "\n")
            return result
    grid = pd.RangeIndex(moteur._day(WARMUP), moteur._day(LAST) + 1)
    diag = diagnostic(tables, grid)
    for name, d in diag.items():
        print(f"{name:34} A {d['A']['mean_week_pct']:+.2f} %/sem (baisse {d['A']['max_drawdown_pct']:.0f} %) | "
              f"B {d['B']['mean_week_pct']:+.2f} %/sem (baisse {d['B']['max_drawdown_pct']:.0f} %) | C {d['C']['return_pct']:+.1f} % | {d['years']}")
    configs, R, days, switch, rg = build(tables, prices, progress=lambda u, n: n % 108 == 107 and print(f"  {u} : {n + 1}/324", flush=True))
    rows = []
    for k in range(len(configs)):
        s = {p: window_stats(R[k][mask(days, p)], days[mask(days, p)]) for p in PERIODS}
        s["all"] = window_stats(R[k], days)
        rows.append(s)
    best_a = choose([r["A"] for r in rows], "R1")
    by_a = sorted(range(len(rows)), key=lambda k: -rows[k]["A"]["mean_week_pct"])
    wf = {}
    for rule in ("R1", "R2"):
        for window in ("toutes", "deux dernières"):
            res = walk_forward(R, days, rule, window, switch)
            stats = window_stats(res["returns"], res["days"])
            stats["years"] = per_year(res["returns"], res["days"])
            wf[f"{rule}, fenêtre {window}"] = {
                "oos_2020_2026": stats,
                "choisis": {y: {"reglage": label(configs[v['index']]['universe'], configs[v['index']]['rules'],
                                                 configs[v['index']]['mix'], configs[v['index']]['brake']),
                                "semaine_moyenne_entrainement": round(v["train"]["mean_week_pct"], 3),
                                "pire_baisse_entrainement": round(v["train"]["max_drawdown_pct"], 1)}
                            for y, v in res["chosen"].items()}}
            print(f"hors échantillon {rule}, {window:15} : {stats['mean_week_pct']:+.2f} %/sem, {stats['annual_pct']:+.1f} %/an, "
                  f"baisse {stats['max_drawdown_pct']:.1f} %, semaines + {stats['positive_weeks']}/{stats['weeks']}, {stats['years']}")
    oos_days = days[days >= moteur._day("2020-01-01")]
    refs = {"v10 (coûts mesurés)": None, "v13 (coûts mesurés)": None}
    for name, rules in (("v10 (coûts mesurés)", V10), ("v13 (coûts mesurés)", V13)):
        k = next(i for i, c in enumerate(configs) if c["universe"] == "8 cryptos" and c["mix"] == 1.0 and c["brake"] is None
                 and asdict(c["rules"]) == asdict(replace(rules, slippage_per_side=moteur.slippage(UNIVERSES["8 cryptos"]))))
        r = R[k][days >= moteur._day("2020-01-01")]
        refs[name] = window_stats(r, oos_days) | {"years": per_year(r, oos_days)}
    g = rg[days >= moteur._day("2020-01-01")]
    refs["fonds G2 seuls"] = window_stats(g, oos_days) | {"years": per_year(g, oos_days)}
    compact = [[label(c["universe"], c["rules"], c["mix"], c["brake"])] +
               [round(rows[k][p][m], 3) for p in ("A", "B", "C", "all") for m in ("mean_week_pct", "max_drawdown_pct")]
               for k, c in enumerate(configs)]
    report = {
        "schema": "scalp-recherche-v1", "status": "simulation ; protocole préenregistré (caf3421) ; non validé en direct",
        "periods": PERIODS, "data": reports, "diagnostic": diag,
        "surapprentissage": {"meilleur_sur_A_regle_R1": {"reglage": compact[best_a][0], **{p: rows[best_a][p] for p in PERIODS}},
                             "dix_meilleurs_sur_A": [{"reglage": compact[k][0], "A": rows[k]["A"]["mean_week_pct"],
                                                      "A_baisse": rows[k]["A"]["max_drawdown_pct"], "B": rows[k]["B"]["mean_week_pct"],
                                                      "B_baisse": rows[k]["B"]["max_drawdown_pct"], "C_pct": rows[k]["C"]["return_pct"]}
                                                     for k in by_a[:10]]},
        "hors_echantillon": wf, "references_2020_2026": refs,
        "grille": {"colonnes": ["réglage"] + [f"{p} {m}" for p in ("A", "B", "C", "tout") for m in ("semaine moyenne %", "pire baisse %")],
                   "lignes": compact},
        "seconds": round(time.time() - t0),
    }
    print(f"meilleur sur A (R1) : {compact[best_a][0]} → A {rows[best_a]['A']['mean_week_pct']:+.2f} %/sem, "
          f"B {rows[best_a]['B']['mean_week_pct']:+.2f} %/sem (baisse {rows[best_a]['B']['max_drawdown_pct']:.0f} %), C {rows[best_a]['C']['return_pct']:+.1f} %")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "recherche-v1",
                                 "combinations": len(configs),
                                 "hors_echantillon": {k: {m: v["oos_2020_2026"][m] for m in ("mean_week_pct", "annual_pct", "max_drawdown_pct", "positive_weeks", "weeks")}
                                                      for k, v in wf.items()},
                                 "meilleur_sur_A": {"reglage": compact[best_a][0], "A": rows[best_a]["A"]["mean_week_pct"],
                                                    "B": rows[best_a]["B"]["mean_week_pct"], "C": rows[best_a]["C"]["return_pct"]}},
                                ensure_ascii=False, default=float) + "\n")
    return report


if __name__ == "__main__":
    main()
