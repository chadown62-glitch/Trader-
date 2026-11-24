"""Coordinateur de veille : une vue unique de ce que chaque agent ferait aujourd'hui (simulation exclusive).

Agents réunis :
- position de fond crypto (8 cryptos, règles figées) : détentions voulues aujourd'hui, à partir des clôtures d'hier ;
- moteur rapide (20 marchés dont 6 memecoins) : marchés ouverts aujourd'hui (meneuses en tendance) ;
- poche fonds indiciels (G1, G2) : détentions décidées à la dernière fin de mois ;
- agent d'actualités : vetos d'entrée en cours (par actif et pour tout le marché crypto).
Un veto d'actualité retire l'actif des entrées du jour ; il ne vend rien. Aucun ordre n'est passé.

Une passe : python -m sentinelle.scalp.veille
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
import pandas as pd

from . import actifs, core, rapide, reserve
from .flash import Archive

KLINES = "https://data-api.binance.vision/api/v3/klines"


def daily_closes(symbols, client: httpx.Client, days: int = 120) -> pd.DataFrame:
    """Clôtures journalières des jours terminés (le jour en cours est exclu), index : numéro du jour UTC."""
    today = int(time.time() * 1000) // core.DAY_MS
    out = {}
    for s in symbols:
        rows = client.get(KLINES, params={"symbol": s, "interval": "1d", "limit": days}).json()
        out[s] = pd.Series({int(r[0]) // core.DAY_MS: float(r[4]) for r in rows if int(r[0]) // core.DAY_MS < today})
    return pd.DataFrame(out).sort_index()


def snapshot(core_closes: pd.DataFrame, fast_closes: pd.DataFrame, etf_prices: pd.DataFrame, vetoes: dict) -> dict:
    """Décisions du jour, chacune fondée sur les seules données closes ; les vetos retirent des entrées."""
    blocked = set(vetoes.get("assets", {}))
    market_blocked = bool(vetoes.get("market"))
    base = lambda s: s.replace("USDC", "")  # noqa: E731
    today = int(core_closes.index[-1]) + 1  # jour en cours : décidé sur les clôtures jusqu'à hier
    rank_today = core.signals(pd.concat([core_closes, pd.DataFrame(index=[today])]), reserve.CORE_RULES)[0].loc[today]
    keep = rank_today[rank_today <= reserve.CORE_RULES.top].dropna().sort_values()  # entrées voulues, hors état du portefeuille
    opened = rapide.open_markets(pd.concat([fast_closes, pd.DataFrame(index=[today])]), reserve.FAST_RULES).loc[today]
    fast_open = sorted(s for s, ok in opened.items() if ok)
    month_start = pd.Timestamp(pd.to_datetime(today * core.DAY_MS, unit="ms").date()).replace(day=1)
    decisions = {rule: actifs.month_end_signals(etf_prices, rule) for rule in ("G1", "G2")}
    # En vigueur : décision de la dernière fin de mois terminée ; le mois en cours n'est qu'un aperçu provisoire.
    etf = {rule: d[d.index < month_start].iloc[-1] for rule, d in decisions.items()}
    preview = {rule: d.iloc[-1] for rule, d in decisions.items() if d.index[-1] >= month_start}
    return {
        "jour_utc": str(pd.to_datetime(today * core.DAY_MS, unit="ms").date()),
        "fond_crypto": {"meneuses_voulues": list(keep.index),
                        "entrees_bloquees_par_actualite": [s for s in keep.index if base(s) in blocked or market_blocked]},
        "rapide": {"marches_ouverts": fast_open,
                   "bloques_par_actualite": [s for s in fast_open if base(s) in blocked or market_blocked]},
        "fonds": {rule: {t: round(float(w), 4) for t, w in row.items() if w > 0} for rule, row in etf.items()},
        "fonds_decision_du": str(etf["G1"].name.date()),
        "fonds_apercu_si_le_mois_finissait_aujourd_hui": {rule: {t: round(float(w), 4) for t, w in row.items() if w > 0}
                                                         for rule, row in preview.items()},
        "actualites": {"vetos_par_actif": {a: len(v) for a, v in vetoes.get("assets", {}).items()},
                       "veto_marche_crypto": vetoes.get("market", [])[:5]},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Coordinateur de veille : décisions du jour de chaque agent (aucun ordre)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--news-db", default="data/actualites-flash.sqlite")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    frozen, _ = reserve.check_frozen()
    with httpx.Client(timeout=20) as client:
        core_closes = daily_closes(frozen["core"]["universe"], client)
        fast_closes = daily_closes(rapide.UNIVERSE, client)
    etf = pd.DataFrame({t: actifs.download(t, Path(args.cache) / "veille", end_ts=int(time.time())) for t in actifs.UNIVERSE})
    vetoes = Archive(args.news_db).vetoes(int(time.time() * 1000)) if Path(args.news_db).exists() else {"assets": {}, "market": []}
    snap = snapshot(core_closes, fast_closes, etf, vetoes)
    print(json.dumps(snap, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return snap


if __name__ == "__main__":
    main()
