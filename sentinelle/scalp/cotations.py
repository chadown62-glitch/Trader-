"""Nouvelles cotations au comptant Binance (catalogue 48) : sorties causales et asymétrie des gains, sur historique autorisé.

Recherche événementielle demandée le 28 novembre 2026 (hypothèses memecoins, SPEC.md). Pour chaque annonce « Binance Will
List X (SYM) » (étiquette « Seed Tag » comprise), on attend la première minute de cotation de SYMUSDT au comptant, puis on
entre à l'ouverture de la 6e minute (5 minutes de délai : ni l'enchère d'ouverture ni le futur ne sont utilisés). Chaque
sortie est décidée minute par minute avec les seules données déjà connues :
- six sorties à durée fixe (15 min, 1 h, 4 h, 24 h, 48 h, 72 h) ;
- six crochets prise de bénéfice / stop (+10, +25, +50 % ; −10, −20 %), plafonnés à 72 h ; si les deux seuils sont touchés
  dans la même minute, le stop est retenu ; une ouverture au-delà d'un seuil sort à l'ouverture ;
- deux stops suiveurs (−15 %, −30 % depuis la plus haute clôture), plafonnés à 72 h.
Coût par côté : 0,10 % de commission + 0,40 % d'écart et de glissement des premières heures (0,5 %) ; robustesse à 1 %.
Une paire qui cotait déjà avant l'annonce est exclue (ce n'est pas une nouvelle cotation). La période réservée est refusée
par data.guard. Mesure seulement : aucune règle du moteur n'est modifiée, aucun ordre.

Lancement : python -m sentinelle.scalp.cotations
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import annonces, data, essais

LISTING = re.compile(r"^Binance Will List\b", re.IGNORECASE)
SYMBOL = re.compile(r"\(([A-Z0-9]{2,15})\)")
EXCLUDE = re.compile(r"futures|margin|perpetual|options|loans|earn|convert|collateral|bstocks|stock|pre-market|alpha|zero trading fee", re.IGNORECASE)
MINUTE, DAY = 60_000, 86_400_000
ENTRY_DELAY, HORIZON, LOOKAHEAD_DAYS = 5, 72 * 60, 7
SIDE, STRESS = 0.005, 0.01
EXITS = {"T15m": ("temps", 15), "T1h": ("temps", 60), "T4h": ("temps", 240), "T24h": ("temps", 1440), "T48h": ("temps", 2880),
         "T72h": ("temps", 4320), "B10_10": ("crochet", 0.10, 0.10), "B25_10": ("crochet", 0.25, 0.10), "B50_10": ("crochet", 0.50, 0.10),
         "B10_20": ("crochet", 0.10, 0.20), "B25_20": ("crochet", 0.25, 0.20), "B50_20": ("crochet", 0.50, 0.20),
         "S15": ("suiveur", 0.15), "S30": ("suiveur", 0.30)}
CAPITAL, MIN_ORDER_USD, BOOT, SEED, ALPHA = 30.0, 5.0, 5000, 20261205, 0.05


def listing_tokens(title: str) -> list[str] | None:
    """Jetons d'une annonce de nouvelle cotation au comptant ; None pour toute autre annonce."""
    title = title.strip()
    if not LISTING.match(title) or EXCLUDE.search(title):
        return None
    return SYMBOL.findall(title) or None


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def listing_bars(symbol: str, publish_ms: int, cache, client) -> tuple[pd.DataFrame | None, str]:
    """Bougies d'une minute du premier jour de cotation après l'annonce et des 3 jours suivants ; motif d'exclusion sinon."""
    def day_bars(day):
        try:
            return data.read_month(annonces.retry(data.download_day, symbol, day, cache, client=client))
        except httpx.HTTPStatusError:
            return None
    if day_bars(_day(publish_ms - DAY)) is not None:
        return None, "paire déjà cotée avant l'annonce"
    for k in range(LOOKAHEAD_DAYS + 1):
        first = day_bars(_day(publish_ms + k * DAY))
        if first is not None:
            parts = [first] + [b for b in (day_bars(_day(publish_ms + (k + j) * DAY)) for j in range(1, 4)) if b is not None]
            full, _ = data.complete_grid(pd.concat(parts, ignore_index=True))
            return full.set_index("t"), ""
    return None, "aucune cotation USDT au comptant dans les 7 jours"


def exit_return(bars: pd.DataFrame, entry_t: int, rule: tuple, side: float) -> float | None:
    """Rendement net d'une position ouverte à l'ouverture de entry_t, selon une règle de sortie causale."""
    window = bars.loc[entry_t: entry_t + (HORIZON - 1) * MINUTE]
    if window.empty or not np.isfinite(window["o"].iloc[0]):
        return None
    entry = float(window["o"].iloc[0])
    o, h, lo, c = (window[k].to_numpy(float) for k in ("o", "h", "l", "c"))
    exit_price = None
    if rule[0] == "temps":
        closes = c[: rule[1]]
        closes = closes[np.isfinite(closes)]
        exit_price = float(closes[-1]) if len(closes) else None
    elif rule[0] == "crochet":
        tp, sl = entry * (1 + rule[1]), entry * (1 - rule[2])
        for i in range(len(c)):
            if not np.isfinite(c[i]):
                continue
            if o[i] <= sl or o[i] >= tp:
                exit_price = float(o[i])  # ouverture au-delà d'un seuil : sortie à l'ouverture
                break
            if lo[i] <= sl:
                exit_price = sl  # stop retenu en premier si les deux seuils sont touchés dans la minute
                break
            if h[i] >= tp:
                exit_price = tp
                break
        if exit_price is None:
            finite = c[np.isfinite(c)]
            exit_price = float(finite[-1]) if len(finite) else None
    else:
        peak = -np.inf
        for i in range(len(c)):
            if not np.isfinite(c[i]):
                continue
            peak = max(peak, c[i])
            if c[i] <= peak * (1 - rule[1]):
                exit_price = float(c[i])
                break
        if exit_price is None:
            finite = c[np.isfinite(c)]
            exit_price = float(finite[-1]) if len(finite) else None
    if exit_price is None:
        return None
    return exit_price * (1 - side) / (entry * (1 + side)) - 1


def holding_end(bars: pd.DataFrame, entry_t: int, rule: tuple) -> int:
    """Fin de détention (pour le portefeuille séquentiel) : borne prudente à l'horizon de la règle."""
    return entry_t + (rule[1] if rule[0] == "temps" else HORIZON) * MINUTE


def study(events: list[dict], cache, client) -> list[dict]:
    rows = []
    for ev in events:
        for tk in ev["jetons"]:
            row = {"code": ev["code"], "titre": ev["titre"], "publication_ms": ev["publication_ms"], "jeton": tk}
            try:
                data.guard(ev["publication_ms"] - DAY, ev["publication_ms"] + (LOOKAHEAD_DAYS + 4) * DAY)
            except data.ReservedPeriodError:
                rows.append({**row, "exclu": "période réservée"})
                continue
            bars, why = listing_bars(f"{tk}USDT", ev["publication_ms"], cache, client)
            if bars is None:
                rows.append({**row, "exclu": why})
                continue
            start = int(bars.index[0])
            entry_t = start + ENTRY_DELAY * MINUTE
            rets = {k: exit_return(bars, entry_t, r, SIDE) for k, r in EXITS.items()}
            if any(v is None for v in rets.values()):
                rows.append({**row, "exclu": "bougies insuffisantes après l'entrée"})
                continue
            rows.append({**row, "exclu": None, "debut_cotation_ms": start, "entree_ms": entry_t,
                         "delai_annonce_cotation_h": round((start - ev["publication_ms"]) / 3_600_000, 2), "rendements": rets,
                         "rendements_stress": {k: exit_return(bars, entry_t, r, STRESS) for k, r in EXITS.items()}})
    return rows


def bootstrap_positive(frame: pd.DataFrame, col: str, draws: int = BOOT, seed: int = SEED) -> float:
    """p unilatérale de « moyenne > 0 » par rééchantillonnage des annonces."""
    groups = [g[col].to_numpy() for _, g in frame.groupby("code")]
    rng = np.random.default_rng(seed)
    means = np.array([np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]).mean() for _ in range(draws)])
    return float((means <= 0).mean())


def describe(x: np.ndarray) -> dict:
    wins, losses = x[x > 0], x[x <= 0]
    return {"moyenne": float(x.mean()), "mediane": float(np.median(x)), "part_gagnante": float((x > 0).mean()),
            "gain_moyen": float(wins.mean()) if len(wins) else None, "perte_moyenne": float(losses.mean()) if len(losses) else None,
            "asymetrie_gain_perte": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None,
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None,
            "coefficient_d_asymetrie": float(pd.Series(x).skew()), "pire": float(x.min()), "meilleur": float(x.max())}


def portfolio(rows: list[dict], rule_name: str, capital: float = CAPITAL) -> dict:
    """Une position à la fois, tout le capital fictif engagé, dans l'ordre des entrées ; une annonce survenant pendant une
    détention est ignorée. Taux hebdomadaire géométrique sur la durée couverte."""
    kept = sorted((r for r in rows if r["exclu"] is None), key=lambda r: r["entree_ms"])
    wealth, free_at, trades, curve = capital, -1, 0, [capital]
    for r in kept:
        if r["entree_ms"] < free_at or wealth < MIN_ORDER_USD:
            continue
        wealth *= 1 + r["rendements"][rule_name]
        free_at = holding_end(None, r["entree_ms"], EXITS[rule_name])
        trades += 1
        curve.append(wealth)
    span_weeks = (kept[-1]["entree_ms"] - kept[0]["entree_ms"]) / (7 * DAY) if len(kept) > 1 else 0
    peak = np.maximum.accumulate(curve)
    return {"capital_initial": capital, "capital_final": float(wealth), "transactions": trades, "semaines": round(span_weeks, 1),
            "taux_hebdomadaire_geometrique": float((wealth / capital) ** (1 / span_weeks) - 1) if span_weeks > 0 and wealth > 0 else None,
            "drawdown_max": float((1 - np.array(curve) / peak).max())}


def summarize(rows: list[dict]) -> dict:
    kept = [r for r in rows if r["exclu"] is None]
    frame = pd.DataFrame([{"code": r["code"], **{k: v for k, v in r["rendements"].items()}} for r in kept])
    stress = pd.DataFrame([r["rendements_stress"] for r in kept])
    pvals = [bootstrap_positive(frame, k) for k in EXITS]
    holm = essais.holm(pvals)
    years = pd.Series([_day(r["entree_ms"])[:4] for r in kept])
    out = {"evenements_mesures": len(kept), "annonces": int(frame["code"].nunique()),
           "exclusions": pd.Series([r["exclu"] for r in rows if r["exclu"]]).value_counts().to_dict(), "regles": {}}
    for (name, rule), p, h in zip(EXITS.items(), pvals, holm):
        x = frame[name].to_numpy(float)
        out["regles"][name] = {"regle": list(rule), **describe(x), "p_unilaterale": p, "p_holm": h, "positive_holm_5pct": bool(h < ALPHA),
                               "moyenne_couts_doubles": float(stress[name].mean()),
                               "moyenne_par_annee": {y: float(x[(years == y).to_numpy()].mean()) for y in sorted(years.unique())},
                               "portefeuille_30_eur": portfolio(kept, name)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Nouvelles cotations Binance : sorties causales et asymétrie (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/cotations-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    ap.add_argument("--last", default="2026-08-31", help="dernière date de publication retenue")
    args = ap.parse_args(argv)
    t0 = time.time()
    last_ms = int(datetime.strptime(args.last, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) + DAY - 1
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        catalog = annonces.list_catalog(client, 48)
        events = []
        for a in catalog:
            toks = listing_tokens(a["title"])
            if toks:
                d = annonces.detail(client, a["code"])
                if d["publication_ms"] <= last_ms:
                    events.append({"code": a["code"], "titre": a["title"], "publication_ms": d["publication_ms"], "jetons": toks})
        rows = study(events, args.cache, client)
    res = summarize(rows)
    out = {"schema": "scalp-cotations-v1", "status": "historique autorisé ; simulation exclusive ; aucune règle modifiée ; préenregistré",
           "catalogue": {"articles": len(catalog), "annonces_de_cotation_au_comptant": len(events)},
           "parametres": {"delai_d_entree_min": ENTRY_DELAY, "horizon_max_h": HORIZON // 60, "cout_par_cote": SIDE, "cout_stress_par_cote": STRESS,
                          "capital_fictif": CAPITAL, "derniere_publication": args.last},
           **res, "evenements": rows, "seconds": round(time.time() - t0)}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: {m: v[m] for m in ("moyenne", "mediane", "part_gagnante", "p_holm")} for k, v in res["regles"].items()}, ensure_ascii=False))
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "cotations-v1", "kind": "result",
                                 "evenements_mesures": res["evenements_mesures"], "exclusions": res["exclusions"],
                                 "regles": {k: {m: v[m] for m in ("moyenne", "mediane", "part_gagnante", "p_holm", "moyenne_couts_doubles")}
                                            for k, v in res["regles"].items()}}, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
