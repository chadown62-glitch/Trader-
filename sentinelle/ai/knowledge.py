"""Fiche de connaissance d'une crypto, calculée sur tout son historique.

Ce que la fiche contient : volatilité, pires mouvements, lien avec le Bitcoin,
climat de marché, liquidité, régularités selon l'heure ou le jour (avec un
contrôle statistique strict) et résultats passés de chaque stratégie de l'IA
sur cette crypto, frais compris.

Ce qu'elle n'est pas : une promesse. Les mesures faites sur 33 cryptos montrent
que les résultats passés d'une stratégie sur une crypto ne prédisent presque rien
de ses résultats futurs (corrélation 0,03 sur 8 863 cas, et aucun gain mesurable
quand on donne cette information au modèle). La fiche sert à comprendre et à
doser le risque, pas à choisir les trades.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .coins import KNOWLEDGE_DATE, coin_info
from .features import REGIME_LABELS, compute_features
from .labeling import label_candidates, non_overlapping
from .metrics import trade_metrics
from .strategies import STRATEGIES

DAY = 86_400_000
WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _f(x, digits: int = 6):
    """Nombre « propre » pour le JSON (None si indéfini)."""
    if x is None:
        return None
    x = float(x)
    return round(x, digits) if math.isfinite(x) else None


def daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Bougies regroupées par jour (UTC). Les journées incomplètes aux extrémités sont retirées."""
    day = df["ts"] // DAY
    g = df.groupby(day)
    out = pd.DataFrame({
        "close": g["close"].last(), "high": g["high"].max(), "low": g["low"].min(),
        "quote_volume": (df["close"] * df["volume"]).groupby(day).sum(), "bars": g["close"].size(),
    })
    full = out["bars"].max()
    if len(out) > 2:
        if out["bars"].iloc[-1] < full:
            out = out.iloc[:-1]
        if out["bars"].iloc[0] < full:
            out = out.iloc[1:]
    return out


def _drawdown(close: pd.Series) -> dict:
    peak = close.cummax()
    dd = close / peak - 1
    trough = dd.idxmin()
    start = close.loc[:trough].idxmax()
    after = close.loc[trough:]
    rec = after[after >= close.loc[start]]
    return {"depth": _f(dd.min()), "start": int(start) * DAY, "trough": int(trough) * DAY,
            "recovered": int(rec.index[0]) * DAY if len(rec) else None,
            "days_to_trough": int(trough - start)}


def _seasonality(values: pd.Series, keys: pd.Series, labels: dict) -> list[dict]:
    """Rendement moyen par groupe, avec un contrôle strict : effet net (|t| ≥ 3) ET de même signe sur les deux moitiés de l'historique."""
    out = []
    half = len(values) // 2
    for k, grp in values.groupby(keys):
        n = len(grp)
        if n < 30:
            continue
        mean, sd = grp.mean(), grp.std(ddof=1)
        t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
        first = values.iloc[:half][keys.iloc[:half] == k].mean()
        second = values.iloc[half:][keys.iloc[half:] == k].mean()
        stable = bool(np.sign(first) == np.sign(second) == np.sign(mean)) if np.isfinite(first) and np.isfinite(second) else False
        out.append({"key": labels.get(k, str(k)), "mean": _f(mean), "t": _f(t, 2), "n": int(n), "notable": bool(abs(t) >= 3.0 and stable)})
    return out


def compute_profile(symbol: str, df: pd.DataFrame, timeframe: str, btc: pd.DataFrame | None = None,
                    fee_rate: float = 0.001, slip: float = 0.0005) -> dict:
    """Fiche complète d'une crypto. `df` : bougies clôturées ; `btc` : bougies du Bitcoin (None pour le Bitcoin lui-même)."""
    base, quote = symbol.split("/")
    df = df.reset_index(drop=True)
    d = daily_frame(df)
    close = d["close"]
    r = close.pct_change().dropna()
    lr = np.log(close).diff().dropna()
    years = len(d) / 365.0
    prof: dict = {"symbol": symbol, "base": base, "quote": quote, "timeframe": timeframe, "info": coin_info(base),
                  "knowledge_date": KNOWLEDGE_DATE}
    prof["history"] = {"first": int(df["ts"].iloc[0]), "last": int(df["ts"].iloc[-1]), "years": _f(years, 2), "bars": int(len(df)), "days": int(len(d))}
    if len(d) < 60:
        prof["insufficient"] = True
        return prof

    # -- prix et rendements -----------------------------------------------------------
    ath_day = close.idxmax()
    total = close.iloc[-1] / close.iloc[0] - 1
    by_year = []
    yr = pd.to_datetime(d.index.to_numpy() * DAY, unit="ms").year
    last_of_year = close.groupby(yr).last()
    prev = close.iloc[0]
    for y, v in last_of_year.items():
        by_year.append({"year": int(y), "return": _f(v / prev - 1, 4)})
        prev = v
    prof["price"] = {"last": _f(close.iloc[-1], 8), "ath": _f(close.max(), 8), "ath_ts": int(ath_day) * DAY,
                     "from_ath": _f(close.iloc[-1] / close.max() - 1, 4), "total_return": _f(total, 4),
                     "cagr": _f((1 + total) ** (1 / years) - 1, 4) if years >= 1 and total > -1 else None, "by_year": by_year}

    # -- volatilité --------------------------------------------------------------------
    vol30 = lr.rolling(30, min_periods=30).std()
    rank = float((vol30.dropna() <= vol30.iloc[-1]).mean()) if vol30.notna().sum() > 60 else None
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    prof["volatility"] = {"annual": _f(lr.std() * math.sqrt(365), 4), "recent_90d": _f(lr.iloc[-90:].std() * math.sqrt(365), 4),
                          "typical_day": _f(r.abs().median(), 4), "typical_candle": _f((tr / df["close"]).median(), 4),
                          "rank_now": _f(rank, 3)}

    # -- mouvements extrêmes ----------------------------------------------------------
    week = close.pct_change(7).dropna()
    prof["extremes"] = {
        "worst_day": {"return": _f(r.min(), 4), "ts": int(r.idxmin()) * DAY}, "best_day": {"return": _f(r.max(), 4), "ts": int(r.idxmax()) * DAY},
        "worst_week": {"return": _f(week.min(), 4), "ts": int(week.idxmin()) * DAY} if len(week) else None,
        "days_down_10": int((r <= -0.10).sum()), "days_up_10": int((r >= 0.10).sum()),
        "down_10_per_year": _f((r <= -0.10).sum() / years, 2) if years > 0 else None,
        "max_drawdown": _drawdown(close),
    }

    # -- lien avec le Bitcoin ---------------------------------------------------------
    if btc is not None and len(btc):
        b = daily_frame(btc.reset_index(drop=True))["close"]
        both = pd.concat([np.log(close).diff().rename("c"), np.log(b).diff().rename("b")], axis=1).dropna()
        if len(both) >= 90:
            last = both.iloc[-365:]
            var = last["b"].var()
            crash = both[both["b"] <= math.log(0.95)]
            rally = both[both["b"] >= math.log(1.05)]
            prof["btc"] = {"corr_all": _f(both["c"].corr(both["b"]), 3), "corr_1y": _f(last["c"].corr(last["b"]), 3),
                           "beta_1y": _f(last["c"].cov(last["b"]) / var, 3) if var > 0 else None,
                           "when_btc_drops_5": {"mean": _f(np.expm1(crash["c"]).mean(), 4), "n": int(len(crash))} if len(crash) >= 5 else None,
                           "when_btc_rises_5": {"mean": _f(np.expm1(rally["c"]).mean(), 4), "n": int(len(rally))} if len(rally) >= 5 else None}

    # -- climat de marché et caractère ---------------------------------------------
    feat = compute_features(df, timeframe, btc=btc)
    reg = feat["regime"]
    known = reg[reg != "unknown"]
    cur = str(reg.iloc[-1])
    run = int((reg.iloc[::-1] == cur).cumprod().sum())
    bar_days = (df["ts"].iloc[-1] - df["ts"].iloc[-2]) / DAY if len(df) > 1 else 1.0
    c_bar = df["close"]
    eff = ((c_bar - c_bar.shift(60)).abs() / c_bar.diff().abs().rolling(60, min_periods=60).sum().replace(0, np.nan)).median()
    ac = lr.autocorr(1) if len(lr) > 60 else None
    prof["regime"] = {"current": cur, "current_label": REGIME_LABELS.get(cur, cur), "days_in_current": _f(run * bar_days, 1),
                      "share": {k: _f((known == k).mean(), 3) for k in ("bull", "neutral", "bear")} if len(known) else {}}
    prof["character"] = {"trend_efficiency": _f(eff, 3), "daily_autocorr": _f(ac, 3),
                         "autocorr_significant": bool(ac is not None and abs(ac) * math.sqrt(len(lr)) >= 3.0)}

    # -- régularités selon le jour et l'heure (contrôle strict) -------------------
    weekday = pd.Series(((d.index.to_numpy() + 3) % 7)[1:], index=r.index)  # 1er janvier 1970 = jeudi
    bar_r = df["close"].pct_change()
    hours = (df["ts"] // 3_600_000) % 24
    valid = bar_r.notna()
    prof["seasonality"] = {
        "weekday": _seasonality(r, weekday, dict(enumerate(WEEKDAYS))),
        "hour_utc": _seasonality(bar_r[valid].reset_index(drop=True), hours[valid].reset_index(drop=True), {h: f"{int(h):02d} h" for h in range(24)}),
    }
    prof["seasonality"]["notable"] = [
        f"{'le ' if part == 'weekday' else 'bougie de '}{x['key']}{'' if part == 'weekday' else ' (heure universelle)'} : {_pct(x['mean'], 2)} en moyenne"
        for part in ("weekday", "hour_utc") for x in prof["seasonality"][part] if x["notable"]]

    # -- liquidité ---------------------------------------------------------------------
    qv = d["quote_volume"]
    prof["liquidity"] = {"daily_volume_30d": _f(qv.iloc[-30:].mean(), 0), "daily_volume_median": _f(qv.median(), 0)}

    # -- résultats passés des stratégies de l'IA sur cette crypto (frais compris) --
    cand = label_candidates(feat, symbol, timeframe, None, fee_rate, slip)
    cand = cand[cand["trend_200d"].notna()] if len(cand) else cand
    two_years = int(df["ts"].iloc[-1]) - 730 * DAY
    strat = {}
    for name, st in STRATEGIES.items():
        sub = cand[cand["strategy"] == name].sort_values("decision_ts").reset_index(drop=True)
        take = non_overlapping(sub) if len(sub) else np.zeros(0, dtype=bool)
        trades = sub[take] if len(sub) else sub
        m = trade_metrics(trades["outcome_r"].to_numpy()) if len(trades) else trade_metrics([])
        recent = trades[trades["decision_ts"] >= two_years] if len(trades) else trades
        mr = trade_metrics(recent["outcome_r"].to_numpy()) if len(recent) else trade_metrics([])
        hold = ((trades["exit_ts"] - trades["decision_ts"]) / DAY).mean() if len(trades) else None
        strat[name] = {"label": st.label, "n": m["n"], "win_rate": _f(m["win_rate"], 3), "mean_r": _f(m["mean_r"], 3),
                       "ci_low": _f(m["ci_low"], 3), "ci_high": _f(m["ci_high"], 3), "sum_r": _f(m["sum_r"], 1),
                       "hold_days": _f(hold, 1), "recent_n": mr["n"], "recent_mean_r": _f(mr["mean_r"], 3),
                       "proven": bool(m["n"] >= 30 and m["ci_low"] is not None and m["ci_low"] > 0)}
    prof["strategies"] = strat
    prof["reading"] = reading(prof)
    return prof


def fr(x: float, digits: int = 1, sign: bool = False) -> str:
    """Nombre au format français (virgule décimale, vrai signe moins)."""
    txt = f"{x:+.{digits}f}" if sign else f"{x:.{digits}f}"
    return txt.replace(".", ",").replace("-", "−")


def _pct(x, digits: int = 1) -> str:
    return "—" if x is None else fr(x * 100, digits, sign=True) + " %"


def _pct_abs(x, digits: int = 1) -> str:
    return "—" if x is None else fr(abs(x) * 100, digits) + " %"


def _date(ts) -> str:
    return "—" if ts is None else pd.to_datetime(ts, unit="ms").strftime("%d/%m/%Y")


def reading(prof: dict) -> list[str]:
    """Lecture en phrases simples des chiffres de la fiche."""
    out = []
    v, e = prof["volatility"], prof["extremes"]
    out.append(f"Un jour normal, le prix bouge d'environ {_pct_abs(v['typical_day'])} ; sur un an, sa volatilité est de {_pct_abs(v['annual'], 0)}.")
    if v.get("rank_now") is not None:
        lvl = "calme" if v["rank_now"] < 0.33 else "agitée" if v["rank_now"] > 0.67 else "ordinaire"
        out.append(f"En ce moment, la période est {lvl} pour cette crypto (plus agitée que {fr(v['rank_now'] * 100, 0)} % de son historique).")
    dd = e["max_drawdown"]
    end = f", effacée le {_date(dd['recovered'])}." if dd["recovered"] else ", pas encore effacée."
    out.append(f"Pire journée : {_pct(e['worst_day']['return'])} le {_date(e['worst_day']['ts'])}. "
               f"Plus forte baisse de son histoire : {_pct(dd['depth'], 0)} entre le {_date(dd['start'])} et le {_date(dd['trough'])}{end}")
    if e.get("down_10_per_year") is not None:
        out.append(f"Journées à −10 % ou pire : {e['days_down_10']} en {fr(prof['history']['years'])} ans, soit environ {fr(e['down_10_per_year'])} par an.")
    b = prof.get("btc")
    if b and b.get("corr_1y") is not None:
        link = "très liée au" if b["corr_1y"] >= 0.75 else "assez liée au" if b["corr_1y"] >= 0.5 else "peu liée au"
        txt = f"Elle est {link} Bitcoin (corrélation {fr(b['corr_1y'], 2)} sur un an)"
        if b.get("beta_1y") is not None:
            amp = "plus amples" if b["beta_1y"] > 1.1 else "moins amples" if b["beta_1y"] < 0.9 else "d'ampleur comparable"
            txt += f" et ses mouvements sont {amp} (× {fr(b['beta_1y'], 2)})"
        out.append(txt + ".")
        if b.get("when_btc_drops_5"):
            out.append(f"Les jours où le Bitcoin perd 5 % ou plus, elle fait en moyenne {_pct(b['when_btc_drops_5']['mean'])} "
                       f"({b['when_btc_drops_5']['n']} journées observées).")
    reg = prof["regime"]
    if reg.get("share"):
        out.append(f"Climat actuel : {reg['current_label']} depuis {fr(reg['days_in_current'], 0)} jours. Sur tout l'historique : "
                   f"haussier {fr(reg['share']['bull'] * 100, 0)} % du temps, baissier {fr(reg['share']['bear'] * 100, 0)} %.")
    sea = prof["seasonality"]
    out.append("Aucune régularité fiable selon le jour ou l'heure." if not sea["notable"]
               else "Régularité statistiquement nette, mais trop petite pour couvrir les frais à elle seule : " + " ; ".join(sea["notable"]) + ".")
    ranked = sorted((s for s in prof["strategies"].values() if s["n"] >= 20), key=lambda s: -s["mean_r"])
    for s in ranked:
        verdict = "avantage net" if s["proven"] else "résultat négatif" if s["ci_high"] < 0 else "rien de prouvé"
        out.append(f"{s['label']} : {fr(s['mean_r'], 2, sign=True)} R par trade sur {s['n']} trades "
                   f"(entre {fr(s['ci_low'], 2, sign=True)} et {fr(s['ci_high'], 2, sign=True)} R) : {verdict}.")
    return out


def brief(prof: dict) -> dict:
    """Résumé d'une fiche pour la liste du panel."""
    if prof.get("insufficient"):
        return {"symbol": prof["symbol"], "name": prof["info"]["name"], "kind": prof["info"]["kind"], "insufficient": True, "years": prof["history"]["years"]}
    return {
        "symbol": prof["symbol"], "name": prof["info"]["name"], "kind": prof["info"]["kind"], "years": prof["history"]["years"],
        "typical_day": prof["volatility"]["typical_day"], "vol_annual": prof["volatility"]["annual"], "vol_rank": prof["volatility"]["rank_now"],
        "max_drawdown": prof["extremes"]["max_drawdown"]["depth"], "from_ath": prof["price"]["from_ath"],
        "corr_btc": (prof.get("btc") or {}).get("corr_1y"), "beta_btc": (prof.get("btc") or {}).get("beta_1y"),
        "regime": prof["regime"]["current"], "regime_label": prof["regime"]["current_label"],
        "daily_volume": prof["liquidity"]["daily_volume_30d"], "updated": prof.get("updated"),
        "trend": {k: {"mean_r": prof["strategies"][k]["mean_r"], "n": prof["strategies"][k]["n"], "proven": prof["strategies"][k]["proven"]}
                  for k in ("trend_follow", "trend_breakout") if k in prof["strategies"]},
    }
