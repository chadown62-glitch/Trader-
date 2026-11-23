"""Moteur rapide : trades de quelques heures à deux jours sur 20 marchés Binance (dont 6 memecoins), mesurés à la semaine.

Principe d'un trader de court terme discipliné (règles fixées avant mesure, research/scalp-v1/trials.jsonl) :
1. Où trader : seulement les meneuses du jour. Une crypto est « ouverte » le jour d si, sur les clôtures jusqu'à
   d − 1, elle est en tendance (au-dessus de ses moyennes de 20 et 50 jours, la première montante), le BTC aussi,
   et si elle est parmi les 5 plus fortes sur 14 jours (règles de la position de fond, core.signals).
2. Quand entrer : à la clôture d'une bougie d'une heure, sur deux configurations :
   - repli : cours au-dessus de sa moyenne de 50 heures, plus bas récent revenu sur la moyenne de 20 heures, puis
     bougie haussière qui clôture au-dessus, élan (RSI 14) entre 40 et 65 ;
   - cassure : clôture au-dessus du plus haut des 24 heures précédentes, volume au moins 1,5 fois la moyenne,
     moyenne de 20 heures au-dessus de celle de 50.
3. Le plan : stop sous le plus bas des 6 dernières heures (repli) ou à 1,5 ATR (cassure), moins 0,3 ATR ; objectif
   2 R ; stop au point mort à +1 R ; sortie au plus tard après 48 heures. Risque entre 0,85 % et 6 % du prix
   (frais BNB d'au plus 0,2 R).
   Variante « suiveuse » (fixée avant mesure, après l'échec du plan à objectif fixe) : cassures seules, sans objectif,
   stop suiveur à une distance égale au risque initial, sortie au plus tard après 72 heures.
4. L'exécution : à l'ouverture de la minute suivante, gérée minute par minute (trade.manage), puis le pupitre de
   risque prudent (desk.run : 0,5 % du capital risqué par trade, 3 positions, perte du jour, pause).
Simulation exclusive ; aucun ordre réel ; aucune vente à découvert : une semaine sans marché ouvert reste en USDC.

Lancement : python -m sentinelle.scalp.rapide --from 2024-09 --to 2026-08
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import core, desk
from .data import load_bars, month_bounds, months
from .trade import INVALID, manage, trail

HOUR_MS = 3_600_000
WEEK_MS = 7 * core.DAY_MS
MAJORS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "ADAUSDC", "LINKUSDC", "AVAXUSDC", "LTCUSDC",
          "DOTUSDC", "SUIUSDC", "TRXUSDC", "NEARUSDC", "APTUSDC"]
MEMECOINS = ["DOGEUSDC", "PEPEUSDC", "WIFUSDC", "BONKUSDC", "SHIBUSDC", "FLOKIUSDC"]
UNIVERSE = MAJORS + MEMECOINS


@dataclass(frozen=True)
class Rules:
    round_trip: float = 2 * (0.00075 + 0.0001)
    min_risk: float = 0.0085
    max_risk: float = 0.06
    max_minutes: int = 48 * 60
    leaders: int = 5
    regime: core.CoreRules = core.CoreRules(market_filter="BTCUSDC", trend_days=50)
    exit: str = "objectif"  # « objectif » : 2 R et point mort ; « suiveur » : stop suiveur, sans objectif
    setups: tuple = ("repli", "cassure")


SUIVEUSE = Rules(max_minutes=72 * 60, exit="suiveur", setups=("cassure",))


def hourly_bars(minutes: pd.DataFrame) -> pd.DataFrame:
    """Bougies d'une heure ; une heure à laquelle manque une minute est invalide ; « m_next » : minute d'entrée."""
    key = minutes["t"].to_numpy("int64") // HOUR_MS
    g = minutes.assign(key=key, pos=np.arange(len(minutes))).groupby("key", sort=True)
    bars = pd.DataFrame({"t": g["t"].first().to_numpy("int64"), "o": g["o"].first().to_numpy(float),
                         "h": g["h"].max().to_numpy(float), "l": g["l"].min().to_numpy(float),
                         "c": g["c"].last().to_numpy(float), "v": g["v"].sum(min_count=1).to_numpy(float),
                         "complete": (g["o"].count() == 60).to_numpy(), "m_next": g["pos"].max().to_numpy("int64") + 1})
    bars.loc[~bars["complete"], ["o", "h", "l", "c", "v"]] = np.nan
    return bars


def setups(bars: pd.DataFrame) -> pd.DataFrame:
    """Signaux à la clôture de chaque heure, avec le stop du plan ; tout est calculé sur les heures déjà closes."""
    c, o, h, l, v = (bars[k].ffill() for k in ("c", "o", "h", "l", "v"))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    e20, e50 = c.ewm(span=20, adjust=False, min_periods=20).mean(), c.ewm(span=50, adjust=False, min_periods=50).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    vol_ratio = v / v.rolling(24, min_periods=12).mean().shift(1)
    hi24 = h.rolling(24).max().shift(1)
    first = lambda s: s.fillna(False) & ~s.fillna(False).shift(1, fill_value=False)  # noqa: E731
    pullback = (c > e50) & (l.rolling(3).min() <= e20) & (c > e20) & (c > o) & rsi.between(40, 65)
    breakout = (c > hi24) & (vol_ratio >= 1.5) & (e20 > e50)
    out = []
    for name, sig, stop in (("repli", first(pullback), l.rolling(6).min() - 0.3 * atr),
                            ("cassure", first(breakout), c - 1.8 * atr)):
        ok = sig & bars["complete"] & atr.notna()
        a = bars.loc[ok, ["t", "c", "m_next"]].copy()
        a["stop"], a["setup"] = stop[ok], name
        out.append(a)
    a = pd.concat(out).sort_values("t")
    a["decided_at"] = a["t"] + HOUR_MS
    a["risk_pct"] = (a["c"] - a["stop"]) / a["c"]
    return a


def alerts(symbol: str, minutes: pd.DataFrame, rules: Rules = Rules()) -> pd.DataFrame:
    """Alertes d'un marché et issue de leur plan, gérée minute par minute."""
    a = setups(hourly_bars(minutes))
    a = a[(a["risk_pct"] >= rules.min_risk) & (a["risk_pct"] <= rules.max_risk) & a["setup"].isin(rules.setups)].copy()
    o, h, l, c, t = (minutes[k].to_numpy(float) for k in ("o", "h", "l", "c", "t"))
    if rules.exit == "suiveur":
        rows = [trail(o, h, l, c, int(m), float(s), float(close - s), rules.max_minutes, round_trip=rules.round_trip)
                for m, s, close in zip(a["m_next"], a["stop"], a["c"])]
    else:
        rows = [manage(o, h, l, c, int(m), float(s), rules.max_minutes, round_trip=rules.round_trip)
                for m, s in zip(a["m_next"], a["stop"])]
    res = pd.DataFrame(rows, index=a.index)
    if "outcome" not in res:  # aucune alerte sur ce marché
        return a.iloc[0:0].assign(outcome=pd.Series(dtype=object), exit_ms=pd.Series(dtype="int64"), symbol=symbol)
    a = a.join(res.drop(columns=["risk_pct"], errors="ignore"))
    a = a[a["outcome"] != INVALID].copy()
    a["exit_ms"] = t[a["exit_minute"].astype(int)].astype("int64") + 60_000
    a["symbol"] = symbol
    a["cost_r"] = rules.round_trip / a["risk_pct"]
    plan = "stop suiveur à 1 R" if rules.exit == "suiveur" else "objectif 2 R, point mort à +1 R"
    a["reasoning"] = [f"{s} sur {symbol} : risque {r:.2%}, {plan}" for s, r in zip(a["setup"], a["risk_pct"])]
    return a


def open_markets(closes: pd.DataFrame, rules: Rules = Rules()) -> pd.DataFrame:
    """Marchés ouverts au trading chaque jour : meneuses en tendance, marché porteur (connu à 00:00 UTC)."""
    rank, _ = core.signals(closes, rules.regime)
    return (rank <= rules.leaders).fillna(False)


def gate(cands: pd.DataFrame, opened: pd.DataFrame) -> np.ndarray:
    day = (cands["decided_at"].to_numpy("int64") - 1) // core.DAY_MS  # jour de la bougie qui a donné le signal
    return np.array([bool(opened.at[d, s]) if d in opened.index and s in opened.columns else False
                     for d, s in zip(day, cands["symbol"])])


def periods(curve, capital, start, end, step_ms) -> list[dict]:
    """Rendement de chaque période (semaine depuis lundi 00:00 UTC, ou mois) à partir de l'équité réalisée."""
    out, t = [], start
    eq = sorted(curve)
    def at(x):  # noqa: E306
        pts = [v for ts, v in eq if ts <= x]
        return pts[-1] if pts else capital
    while t < end:
        u = min(end, t + step_ms) if step_ms else end
        out.append({"from": int(t), "pct": 100 * (at(u) / at(t) - 1)})
        t = u
    return out


def weekly_report(result: dict, start: int, end: int) -> dict:
    monday = start - ((start // core.DAY_MS + 3) % 7) * core.DAY_MS  # le 1er janvier 1970 était un jeudi
    weeks = periods(result["curve"], result["capital"], monday, end, WEEK_MS)
    trades_by_week = pd.Series([(t["exit_ms"] - monday) // WEEK_MS for t in result["trades"]]).value_counts()
    for i, w in enumerate(weeks):
        w["trades"] = int(trades_by_week.get(i, 0))
    pct = np.array([w["pct"] for w in weeks])
    active = np.array([w["trades"] > 0 for w in weeks])
    month_rows = []
    for m in months(pd.to_datetime(start, unit="ms").strftime("%Y-%m"), pd.to_datetime(end - 1, unit="ms").strftime("%Y-%m")):
        a, b = month_bounds(m)
        month_rows.append({"month": m, "pct": periods(result["curve"], result["capital"], a, b + 1, None)[0]["pct"]})
    mp = np.array([r["pct"] for r in month_rows])
    return {"weeks": len(weeks), "positive_weeks": int((pct > 0).sum()), "negative_weeks": int((pct < 0).sum()),
            "flat_weeks": int((pct == 0).sum()), "weeks_with_trades": int(active.sum()),
            "mean_week_pct": float(pct.mean()), "median_week_pct": float(np.median(pct)),
            "mean_active_week_pct": float(pct[active].mean()) if active.any() else None,
            "best_week_pct": float(pct.max()), "worst_week_pct": float(pct.min()),
            "trades_per_week": float(len(result["trades"]) / max(1, len(weeks))),
            "months": month_rows, "positive_months": int((mp > 0).sum()), "n_months": len(mp), "week_rows": weeks}


def build(cache_dir, first: str, last: str, universe=UNIVERSE, rules: Rules = Rules()):
    from .campaign import _listed_from
    cands, opens, closes = [], {}, {}
    for s in universe:
        start = _listed_from(s, first, last, cache_dir, None)
        if start is None:
            continue
        minutes, _ = load_bars(s, start, last, cache_dir)
        o, c, _l = core.daily_table({s: minutes})
        opens[s], closes[s] = o[s], c[s]
        cands.append(alerts(s, minutes, rules))
        del minutes
    return pd.concat(cands, ignore_index=True), pd.DataFrame(opens), pd.DataFrame(closes)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Moteur rapide : essai à la semaine (simulation exclusive)")
    ap.add_argument("--from", dest="first_month", default="2024-09")
    ap.add_argument("--to", dest="last_month", default="2026-08")
    ap.add_argument("--data-from", default="2024-01")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--no-gate", action="store_true", help="diagnostic : sans filtre des meneuses en tendance")
    ap.add_argument("--memecoins-only", action="store_true")
    ap.add_argument("--suiveuse", action="store_true", help="variante cassures seules avec stop suiveur")
    ap.add_argument("--out", default=None)
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    rules = SUIVEUSE if args.suiveuse else Rules()
    store = Path(args.cache) / f"rapide-{rules.exit}-{args.data_from}-{args.last_month}.pkl"
    if store.exists():
        cands, opens, closes = pd.read_pickle(store)
    else:
        cands, opens, closes = build(args.cache, args.data_from, args.last_month, rules=rules)
        pd.to_pickle((cands, opens, closes), store)
    if args.memecoins_only:
        cands = cands[cands["symbol"].isin(MEMECOINS)]
    start, end = month_bounds(args.first_month)[0], month_bounds(args.last_month)[1] + 1
    take = np.ones(len(cands), bool) if args.no_gate else gate(cands, open_markets(closes, rules))
    result = desk.run(cands, take, start, end)
    weekly = weekly_report(result, start, end)
    m = result["metrics"]
    print(f"{args.first_month} → {args.last_month} : {m['return_pct']:+.2f} % ({m['trades']} trades, {weekly['trades_per_week']:.1f} par semaine, "
          f"gagnants {m['win_rate'] or 0:.0%}, baisse max {m['max_drawdown_pct']:.2f} %)")
    print(f"  semaines : {weekly['positive_weeks']} positives, {weekly['negative_weeks']} négatives, {weekly['flat_weeks']} sans trade "
          f"sur {weekly['weeks']} ; moyenne {weekly['mean_week_pct']:+.2f} %, médiane {weekly['median_week_pct']:+.2f} %, "
          f"pire {weekly['worst_week_pct']:+.2f} %, meilleure {weekly['best_week_pct']:+.2f} %")
    print(f"  mois positifs : {weekly['positive_months']}/{weekly['n_months']} ; par configuration : "
          + ", ".join(f"{k} {v['trades']} trades, {v['wins']} gagnants, {v['pnl']:+.1f}" for k, v in m["by_setup"].items()))
    report = {"schema": "scalp-rapide-v1", "rules": asdict(rules), "gate": not args.no_gate, "memecoins_only": args.memecoins_only,
              "from": args.first_month, "to": args.last_month, "universe": sorted(cands["symbol"].unique().tolist()),
              "metrics": m, "refusals": result["refusals"], "weekly": weekly, "note": args.note}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "rapide",
                                 "note": args.note, "gate": not args.no_gate, "memecoins_only": args.memecoins_only,
                                 "from": args.first_month, "to": args.last_month, "return_pct": m["return_pct"],
                                 "trades": m["trades"], "win_rate": m["win_rate"], "max_drawdown_pct": m["max_drawdown_pct"],
                                 **{k: weekly[k] for k in ("positive_weeks", "negative_weeks", "flat_weeks", "weeks",
                                                           "mean_week_pct", "worst_week_pct", "positive_months", "n_months")}},
                                ensure_ascii=False, default=float) + "\n")
    return report


if __name__ == "__main__":
    main()
