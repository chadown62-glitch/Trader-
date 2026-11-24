"""Moteur unique : un seul portefeuille pour les cryptos, les memecoins et les actions, marchés au choix (simulation exclusive).

Règles consignées dans research/scalp-v1/trials.jsonl avant tout calcul (« moteur-unique-v1 ») ; aucune n'est nouvelle :
- poche crypto : les cryptos et memecoins choisis, aux règles figées de la position de fond (frozen-system-v1 : élan sur
  14 jours, 3 places, moyenne de 20 jours montante, moyenne de 50 jours, filtre BTC, stop suiveur de 10 %, maintien parmi
  les 5 plus fortes, volatilité visée de 30 % par place). Si le BTC n'est pas choisi, il sert de filtre sans être acheté ;
- poche actions : la règle G2 de Faber sur 8 fonds indiciels (actions, obligations, or, matières premières, immobilier) ;
- portefeuille unique : parts de base crypto 35 %, actions 65 % ; une poche sans position cède sa part aux poches
  investies, pour que le capital ne dorme pas ; sans aucune poche investie, tout reste en liquidités (0 %).
En baisse, aucune vente à découvert (CLAUDE.md) : le moteur sort vers les obligations, l'or ou les liquidités.
Aucun ordre, aucune clé, aucun levier.

Mesure : python -m sentinelle.scalp.moteur
Répartition du jour : python -m sentinelle.scalp.moteur --aujourdhui --capital 1000 --marches crypto,actions
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import actifs, core, rapide, reserve
from .data import BASE_URL, DAILY_URL, _fetch_verified, months, read_month

DAY_MS = core.DAY_MS
MARKETS = ("crypto", "memecoins", "actions")
CRYPTO = [s.replace("USDC", "USDT") for s in rapide.MAJORS]
MEMECOINS = [s.replace("USDC", "USDT") for s in rapide.MEMECOINS]
FILTER = "BTCUSDT"
ACTIONS = list(actifs.UNIVERSE)
SHARES = {"crypto": 0.35, "actions": 0.65}
FEE = 0.00075
COSTS_FILE = Path(__file__).resolve().parents[2] / "research" / "scalp-v1" / "live-costs-2026-11-23.json"
FIRST_MONTH = "2017-08"
WEEKLY_REFERENCE = 1.30  # demande du propriétaire, publiée à titre descriptif ; jamais un paramètre


def _day(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) // DAY_MS


# --- Données : bougies journalières Binance vérifiées, cours ajustés des fonds -------------------------------------------

def _archive(symbol: str, period: str, cache_dir, client) -> Path:
    """Archive journalière (1d) d'un mois « AAAA-MM » ou d'un jour « AAAA-MM-JJ », vérifiée par sa somme de contrôle.
    La période réservée a été consommée par l'évaluation unique (#140) : elle se lit ici comme le reste de l'historique."""
    base = DAILY_URL if len(period) == 10 else BASE_URL
    name = f"{symbol}-1d-{period}.zip"
    sub = "daily" if len(period) == 10 else ""
    return _fetch_verified(f"{base}/{symbol}/1d/{name}", Path(cache_dir) / "1d" / symbol / sub / name, client)


def _published(symbol: str, month: str, client) -> bool:
    return client.head(f"{BASE_URL}/{symbol}/1d/{symbol}-1d-{month}.zip.CHECKSUM").status_code == 200


def first_month(symbol: str, last: str, client) -> str | None:
    """Premier mois publié (la paire entre dans l'univers à sa cotation, jamais avant), par dichotomie."""
    ms = months(FIRST_MONTH, last)
    if not _published(symbol, ms[-1], client):
        return None
    lo, hi = 0, len(ms) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if _published(symbol, ms[mid], client) else (mid + 1, hi)
    return ms[lo]


def daily_bars(symbol: str, last_day: str, cache_dir, client, first: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Ouverture, clôture et plus bas de chaque jour UTC jusqu'à « last_day » inclus (index : numéro du jour).
    Mois terminés : archives mensuelles ; mois en cours : archives journalières. Un trou n'est jamais comblé."""
    end = datetime.strptime(last_day, "%Y-%m-%d")
    current = end.strftime("%Y-%m")
    prev = (end.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    start = first or first_month(symbol, prev, client)
    if start is None:
        return pd.DataFrame(columns=["o", "c", "l"]), {"symbol": symbol, "listed": None}
    periods = [m for m in months(start, prev)] + [(end.replace(day=1) + timedelta(days=i)).strftime("%Y-%m-%d")
                                                   for i in range(end.day)]
    frames = [read_month(_archive(symbol, p, cache_dir, client)) for p in periods]
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.assign(day=raw["t"].astype("int64") // DAY_MS).drop_duplicates("day").set_index("day").sort_index()
    span = np.arange(raw.index[0], raw.index[-1] + 1)
    bars = raw.reindex(span)[["o", "c", "l"]].astype(float)
    return bars, {"symbol": symbol, "listed": start, "files": len(periods), "days": int(bars["c"].notna().sum()),
                  "missing_days": int(bars["c"].isna().sum()), "current_month": current}


def crypto_tables(symbols, last_day: str, cache_dir, client, workers: int = 8):
    with ThreadPoolExecutor(workers) as pool:
        res = dict(zip(symbols, pool.map(lambda s: daily_bars(s, last_day, cache_dir, client), symbols)))
    reports = [r for _, r in res.values()]
    keep = {s: b for s, (b, r) in res.items() if r.get("listed")}
    tables = tuple(pd.DataFrame({s: b[k] for s, b in keep.items()}).sort_index() for k in ("o", "c", "l"))
    return tables, reports


def etf_prices(cache_dir, last_day: str) -> pd.DataFrame:
    prices = pd.DataFrame({t: actifs.download(t, cache_dir, end_ts=int(time.time())) for t in ACTIONS})
    return prices.loc[:last_day]


# --- Poches -------------------------------------------------------------------------------------------------------------

def slippage(symbols) -> float:
    """Glissement par côté le plus élevé mesuré sur le carnet (23 novembre 2026, ordre de 330 USDC) parmi les marchés
    achetables de la poche, jamais sous l'hypothèse figée de 0,01 %."""
    measured = reserve.measured_slippage(COSTS_FILE)
    return max([0.0001] + [measured.get(s.replace("USDT", "USDC"), 0.0001) for s in symbols])


def crypto_universe(selection) -> list[str]:
    return (CRYPTO if "crypto" in selection else []) + (MEMECOINS if "memecoins" in selection else [])


def crypto_rules(buyable) -> core.CoreRules:
    return replace(reserve.CORE_RULES, market_filter=FILTER, slippage_per_side=slippage(buyable))


def _crypto_run(tables, buyable, start_day: int, end_day: int, capital: float = 1000.0):
    """Règles figées de la position de fond ; le BTC, s'il n'est pas achetable, ne sert qu'au filtre de marché
    (ouverture absente : jamais acheté)."""
    opens, closes, lows = tables
    cols = [s for s in dict.fromkeys(list(buyable) + [FILTER]) if s in closes.columns]
    opens = opens[cols].copy()
    if FILTER not in buyable:
        opens[FILTER] = np.nan
    rules = crypto_rules(buyable)
    return core.run(opens, closes[cols], start_day * DAY_MS, (end_day + 1) * DAY_MS, capital, rules, lows=lows[cols]), rules


def crypto_pocket(tables, buyable, start_day: int, end_day: int, capital: float = 1000.0) -> dict:
    """Poche crypto : équité et détention à la fin de chaque jour."""
    res, rules = _crypto_run(tables, buyable, start_day, end_day, capital)
    equity = pd.Series({t // DAY_MS - 1: v for t, v in res["curve"]})
    active = pd.Series({t // DAY_MS - 1: bool(h) for t, h in res["holdings"]})
    return {"equity": equity, "active": active, "cost": FEE + rules.slippage_per_side, "rules": asdict(rules),
            "orders": len(res["trades"])}


def _calendar(s: pd.Series, first: int, last: int, before) -> pd.Series:
    grid = pd.RangeIndex(first, last + 1)
    return s.reindex(s.index.union(grid)).ffill().reindex(grid).fillna(before)


def actions_pocket(prices: pd.DataFrame, start_day: int, end_day: int, capital: float = 1000.0) -> dict:
    """Poche actions (G2) : clôture d'un jour de bourse connue à la fin de ce jour, gardée le week-end."""
    first = pd.to_datetime(start_day * DAY_MS, unit="ms")
    last = pd.to_datetime(end_day * DAY_MS, unit="ms")
    res = actifs.run(prices, "G2", first, last, capital)
    as_day = lambda d: int(pd.Timestamp(d).timestamp()) // 86400  # noqa: E731
    equity = pd.Series({as_day(d): v for d, v in res["curve"]})
    decisions = actifs.month_end_signals(prices.ffill(), "G2")
    executed = {}
    for when, row in decisions.iterrows():
        nxt = prices.index[prices.index > when]
        if len(nxt):
            executed[as_day(nxt[0])] = bool((row.fillna(0.0) > 0).any())
    active = pd.Series(executed, dtype=bool)
    eq = _calendar(equity, start_day, end_day, capital)
    act = _calendar(active.astype(float), start_day, end_day, 0.0).astype(bool)
    return {"equity": eq, "active": act, "cost": actifs.SIDE_COST, "orders": None}


# --- Portefeuille unique ------------------------------------------------------------------------------------------------

def unify(pockets: dict, shares: dict, start_day: int, end_day: int, capital: float = 1000.0,
          transfers: bool = True) -> dict:
    """Un seul portefeuille. Au début du jour d, les poches investies à la clôture de d − 1 se partagent tout le capital au
    prorata de leurs parts de base ; une poche sans position n'en reçoit pas. Sans transferts (« transfers=False ») :
    parts fixées au départ, chaque poche évolue seule. Les transferts paient les frais du côté acheteur, et du côté
    vendeur quand la poche qui cède reste investie (une poche sortie a déjà vendu, frais comptés dans sa propre courbe)."""
    names = list(pockets)
    base = {p: shares[p] / sum(shares[q] for q in names) for p in names}
    grid = pd.RangeIndex(start_day - 1, end_day + 1)
    ret = {p: _calendar(pockets[p]["equity"], start_day - 1, end_day, np.nan).reindex(grid).ffill().bfill()
           .pct_change().fillna(0.0) for p in names}
    act = {p: _calendar(pockets[p]["active"].astype(float), start_day - 1, end_day, 0.0).astype(bool) for p in names}
    book = {p: (capital * base[p] if not transfers else 0.0) for p in names}
    cash = 0.0 if not transfers else capital
    current, rows, n_transfers, paid = None, [], 0, 0.0
    for d in range(start_day, end_day + 1):
        if transfers:
            invested = frozenset(p for p in names if act[p].at[d - 1])
            if invested != current:
                total = cash + sum(book.values())
                weight = sum(base[p] for p in invested)
                target = {p: (total * base[p] / weight if p in invested else 0.0) for p in names}
                cost = sum(abs(target[p] - book[p]) * pockets[p]["cost"] for p in names
                           if target[p] > book[p] or p in invested)
                total -= cost
                book = {p: (total * base[p] / weight if p in invested else 0.0) for p in names}
                cash = 0.0 if invested else total
                current, n_transfers, paid = invested, n_transfers + 1, paid + cost
        for p in names:
            book[p] *= 1.0 + ret[p].at[d]
        rows.append((d, cash + sum(book.values()), sorted(current) if transfers else names))
    equity = pd.Series({d: v for d, v, _ in rows})
    invested_share = float(np.mean([bool(h) for _, _, h in rows]))
    return {"equity": equity, "transfers": n_transfers, "transfer_costs": paid, "days_invested_share": invested_share,
            "base_shares": base}


# --- Statistiques -------------------------------------------------------------------------------------------------------

def _period_returns(equity: pd.Series, keys: pd.Index, start_value: float) -> pd.Series:
    last = equity.groupby(keys).last()
    prev = last.shift(1).fillna(start_value)
    return 100 * (last / prev - 1)


def stats(equity: pd.Series, capital: float = 1000.0) -> dict:
    """Rendement, pire baisse, semaines (lundi → dimanche UTC), mois et années civiles, à partir de l'équité de fin de jour."""
    eq = equity.to_numpy(float)
    days = equity.index.to_numpy()
    dates = pd.to_datetime(days * DAY_MS, unit="ms")
    weeks = _period_returns(equity, pd.Index((days + 3) // 7), capital)  # le 1er janvier 1970 était un jeudi
    monthly = _period_returns(equity, pd.Index(dates.strftime("%Y-%m")), capital)
    yearly = _period_returns(equity, pd.Index(dates.strftime("%Y")), capital)
    years = len(days) / 365.25
    w = weeks.to_numpy()
    flat = np.abs(w) < 1e-9
    return {"return_pct": float(100 * (eq[-1] / capital - 1)),
            "annual_pct": float(100 * ((eq[-1] / capital) ** (1 / years) - 1)),
            "max_drawdown_pct": float(100 * np.max(1 - eq / np.maximum.accumulate(np.r_[capital, eq])[1:])),
            "weeks": int(len(w)), "mean_week_pct": float(w.mean()), "median_week_pct": float(np.median(w)),
            "compound_week_pct": float(100 * ((eq[-1] / capital) ** (1 / len(w)) - 1)),
            "positive_weeks": int((w >= 1e-9).sum()), "negative_weeks": int((w <= -1e-9).sum()), "flat_weeks": int(flat.sum()), "worst_week_pct": float(w.min()), "best_week_pct": float(w.max()),
            "weeks_at_reference": int((w >= WEEKLY_REFERENCE).sum()),
            "positive_months": int((monthly > 0).sum()), "n_months": int(len(monthly)),
            "years": {k: round(float(v), 2) for k, v in yearly.items()}}


def window(equity: pd.Series, first: int, last: int, capital: float = 1000.0) -> pd.Series:
    """Tranche de l'équité, ramenée à 1000 au début de la tranche (fin du jour précédent, ou capital de départ)."""
    before = equity.loc[:first - 1]
    start = float(before.iloc[-1]) if len(before) else capital
    return equity.loc[first:last] / start * 1000.0


def buy_and_hold(closes: pd.Series, first: int, last: int, capital: float = 1000.0) -> pd.Series:
    s = _calendar(closes.dropna(), first - 1, last, np.nan).ffill()
    return s.loc[first:last] / s.at[first - 1] * capital


# --- Mesure -------------------------------------------------------------------------------------------------------------

def selections():
    for n in range(1, len(MARKETS) + 1):
        yield from (tuple(c) for c in itertools.combinations(MARKETS, n))


def engine(selection, tables, prices, start_day, end_day, *, crypto_symbols=None, transfers=True) -> dict:
    pockets = {}
    buyable = crypto_symbols or crypto_universe(selection)
    if buyable:
        pockets["crypto"] = crypto_pocket(tables, buyable, start_day - 120, end_day)
    if "actions" in selection:
        pockets["actions"] = actions_pocket(prices, start_day - 400, end_day)
    out = unify(pockets, SHARES, start_day, end_day, transfers=transfers)
    out["pockets"] = pockets
    return out


def measure(tables, prices, start: str, end: str, dev_end: str) -> dict:
    s, e, de = _day(start), _day(end), _day(dev_end)
    rows, curves = {}, {}

    def add(name, equity, extra=None):
        curves[name] = equity
        rows[name] = {"all": stats(equity), "dev": stats(window(equity, s, de)), "reserved": stats(window(equity, de + 1, e))}
        if extra:
            rows[name].update(extra)

    full = MARKETS
    main = engine(full, tables, prices, s, e)
    add("moteur U1 (crypto + memecoins + actions)", main["equity"],
        {"transfers": main["transfers"], "transfer_costs": main["transfer_costs"],
         "days_invested_share": main["days_invested_share"], "base_shares": main["base_shares"],
         "crypto_slippage_per_side": main["pockets"]["crypto"]["rules"]["slippage_per_side"]})
    fixed = engine(full, tables, prices, s, e, transfers=False)
    add("U0 parts fixes 35/65 sans transfert", fixed["equity"])
    for sel in selections():
        if sel == full:
            continue
        r = engine(sel, tables, prices, s, e)
        add("U1 " + " + ".join(sel), r["equity"], {"transfers": r["transfers"]})
    pocket = main["pockets"]["crypto"]
    add("poche crypto seule (crypto + memecoins)", window(pocket["equity"], s, e), {"orders": pocket["orders"]})
    add("poche actions seule (G2)", window(main["pockets"]["actions"]["equity"], s, e))
    robust = engine(full, tables, prices, s, e, crypto_symbols=["BTCUSDT", "ETHUSDT"])
    add("robustesse : U1 avec BTC et ETH seuls", robust["equity"])
    add("robustesse : poche BTC et ETH seule", window(robust["pockets"]["crypto"]["equity"], s, e))
    add("CASH", pd.Series(1000.0, index=pd.RangeIndex(s, e + 1)))
    add("BTC (achat-conservation)", buy_and_hold(tables[1]["BTCUSDT"], s, e))
    spy = pd.Series({int(d.timestamp()) // 86400: v for d, v in prices["SPY"].dropna().items()})
    add("SPY (achat-conservation)", buy_and_hold(spy, s, e))
    first = pd.to_datetime((s - 1) * DAY_MS, unit="ms")
    bench = actifs.benchmark(prices.dropna(subset=["SPY", "IEF"]), {"SPY": 0.6, "IEF": 0.4}, first, pd.to_datetime(e * DAY_MS, unit="ms"))
    b = pd.Series({int(pd.Timestamp(d).timestamp()) // 86400: v for d, v in bench["curve"]})
    add("60/40 SPY/IEF (rééquilibré chaque mois)", window(_calendar(b, s - 1, e, 1000.0), s, e))
    weekly = (_period_returns(main["equity"], pd.Index((main["equity"].index.to_numpy() + 3) // 7), 1000.0))
    week_rows = [{"semaine_du": str(pd.to_datetime((int(k) * 7 - 3) * DAY_MS, unit="ms").date()), "pct": round(float(v), 3)}
                 for k, v in weekly.items()]
    return {"rows": rows, "main_weeks": week_rows, "curves": curves}


# --- Répartition du jour ------------------------------------------------------------------------------------------------

def today_plan(selection, tables, prices, capital: float, vetoes: dict) -> dict:
    """Ce que le moteur tiendrait aujourd'hui avec un capital neuf, décidé sur les seules clôtures d'hier."""
    closes = tables[1]
    last = int(closes.index[-1])
    today = last + 1
    plan, invested = {}, {}
    buyable = crypto_universe(selection)
    if buyable:
        # Jour en cours ajouté avec l'ouverture estimée à la dernière clôture : les décisions de l'ouverture du jour
        # (rang et volatilité connus à la clôture d'hier) donnent les détentions voulues aujourd'hui.
        opens, _, lows = (t.reindex(t.index.union([today])) for t in tables)
        opens.loc[today] = closes.loc[last]
        ext = (opens, closes.reindex(opens.index), lows)
        res, rules = _crypto_run(ext, buyable, last - 400, today)
        held = [s for s in res["holdings"][-1][1] if s in buyable]
        vol = core.signals(ext[1], rules)[1].loc[today]
        plan["crypto"] = {s: (1 / rules.top) * min(1.0, rules.vol_target / float(vol[s])) for s in held}
        invested["crypto"] = bool(held)
    if "actions" in selection:
        decisions = actifs.month_end_signals(prices.ffill(), "G2")
        month_start = pd.Timestamp(pd.to_datetime(today * DAY_MS, unit="ms").date()).replace(day=1)
        row = decisions[decisions.index < month_start].iloc[-1]
        plan["actions"] = {t: float(w) for t, w in row.items() if w > 0}
        invested["actions"] = bool(plan["actions"])
    names = [p for p in plan if invested.get(p)]
    weight = sum(SHARES[p] for p in names)
    blocked = set(vetoes.get("assets", {}))
    market = bool(vetoes.get("market"))
    lines = []
    for p in names:
        for asset, w in sorted(plan[p].items()):
            share = SHARES[p] / weight * w
            name = asset.replace("USDT", "")
            veto = p == "crypto" and (name in blocked or market)
            lines.append({"poche": p, "actif": name, "part_pct": round(100 * share, 2),
                          "montant": round(capital * share, 2), "entree_bloquee_par_actualite": veto})
    used = sum(x["part_pct"] for x in lines)
    return {"jour_utc": str(pd.to_datetime(today * DAY_MS, unit="ms").date()), "marches": list(selection),
            "capital": capital, "lignes": lines, "liquidites_pct": round(100 - used, 2),
            "liquidites": round(capital * (1 - used / 100), 2),
            "remarque": "simulation : aucun ordre n'est passé ; une entrée bloquée par l'actualité reste en liquidités"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Moteur unique : un portefeuille, marchés au choix (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--marches", default="crypto,memecoins,actions")
    ap.add_argument("--from", dest="first", default="2018-01-01")
    ap.add_argument("--to", dest="last", default="2026-11-22")
    ap.add_argument("--dev-end", default="2026-08-31")
    ap.add_argument("--aujourdhui", action="store_true", help="répartition du jour au lieu de la mesure historique")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--news-db", default="data/actualites-flash.sqlite")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    selection = tuple(m for m in MARKETS if m in args.marches.split(","))
    if not selection:
        ap.error("--marches : choisir parmi crypto, memecoins, actions")
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        if args.aujourdhui:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            symbols = list(dict.fromkeys(crypto_universe(selection) + [FILTER]))
            for day in (yesterday, (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")):
                try:
                    tables, _ = crypto_tables(symbols, day, args.cache, client)
                    break
                except httpx.HTTPStatusError:
                    continue  # archive de la veille pas encore publiée : la journée précédente sert de base
            from .flash import Archive
            vetoes = Archive(args.news_db).vetoes(int(time.time() * 1000)) if Path(args.news_db).exists() else {}
            fresh = Path(args.cache) / f"jour-{datetime.now(timezone.utc):%Y-%m-%d}"  # cours des fonds du jour, jamais un ancien cache
            plan = today_plan(selection, tables, etf_prices(fresh, day), args.capital, vetoes)
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            return plan
        tables, reports = crypto_tables(list(dict.fromkeys(CRYPTO + MEMECOINS)), args.last, args.cache, client)
    prices = etf_prices(args.cache, args.last)
    result = measure(tables, prices, args.first, args.last, args.dev_end)
    curves = result.pop("curves")
    for name, r in result["rows"].items():
        a = r["all"]
        print(f"{name:44} {a['return_pct']:+8.1f} % (par an {a['annual_pct']:+6.1f} %), pire baisse {a['max_drawdown_pct']:5.1f} %, "
              f"semaine moy. {a['mean_week_pct']:+.2f} %, + {a['positive_weeks']} − {a['negative_weeks']} 0 {a['flat_weeks']}, "
              f"≥ {WEEKLY_REFERENCE} % : {a['weeks_at_reference']}/{a['weeks']}, années {a['years']}")
    report = {"schema": "scalp-moteur-unique-v1", "status": "simulation ; règles préenregistrées ; non validé en direct",
              "period": {"from": args.first, "to": args.last, "dev_end": args.dev_end}, "data": reports,
              "etf_last_close": str(prices.index[-1].date()), **result}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "moteur-unique-v1",
                                 "rows": {n: {k: r["all"][k] for k in ("return_pct", "annual_pct", "max_drawdown_pct",
                                                                       "mean_week_pct", "positive_weeks", "negative_weeks",
                                                                       "weeks_at_reference", "years")}
                                          for n, r in result["rows"].items()}}, ensure_ascii=False, default=float) + "\n")
    return report, curves


if __name__ == "__main__":
    main()
