"""Archives d'actualités : inventaire des sources publiques et étude historique hors ligne (décision du 25 novembre 2026).

1. Inventaire : pour chaque source publique candidate, accès depuis l'environnement, profondeur, nature de l'horodatage,
   limites. Aucune collecte prospective.
2. Étude préenregistrée, sur la source la plus directement liée aux vetos : les annonces officielles de retrait de cote de
   Binance (« Binance Will Delist X, Y on AAAA-MM-JJ », catalogue 161, horodatage de publication de la source elle-même).
   Pour chaque jeton retiré qui cotait contre USDT, on entre à l'ouverture de la première minute qui suit la publication
   (rien n'est connu avant) et on mesure le rendement anormal contre le BTC à 1 heure et à 24 heures, ainsi que la dérive
   des 24 heures précédentes (fuite d'information). Le veto existant (flash.py : 24 h sans entrée après un retrait) est
   ainsi chiffré. Bougies d'une minute vérifiées par data.download_day ; la période réservée reste refusée.
Mesure seulement : aucune règle n'est modifiée.

Lancement : python -m sentinelle.scalp.annonces
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

from . import data, essais

CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article"
CATALOGS = {48: "nouvelles cotations", 49: "actualités Binance", 161: "retraits de cote", 157: "maintenance"}
DELIST = re.compile(r"^Binance Will Delist (.+?) on (\d{4}-\d{2}-\d{2})", re.IGNORECASE)
EXCLUDE = re.compile(r"futures|margin|alpha|leveraged|trading pairs?|perpetual|options|loans", re.IGNORECASE)
MINUTE, HOUR, DAY = 60_000, 3_600_000, 86_400_000
BOOT, SEED, PAUSE = 5000, 20261204, 0.25


def retry(fn, *args, tries: int = 4, **kw):
    """Coupures de transport passagères : jusqu'à 4 essais espacés ; les erreurs HTTP (404…) remontent telles quelles."""
    for k in range(tries):
        try:
            return fn(*args, **kw)
        except httpx.TransportError:
            if k == tries - 1:
                raise
            time.sleep(2 ** k)


def tokens_of(title: str) -> tuple[list[str], str] | None:
    """Jetons et date de retrait d'un titre d'annonce de retrait de cote au comptant ; None pour les autres annonces."""
    if EXCLUDE.search(title):
        return None
    m = DELIST.match(title.strip())
    if not m:
        return None
    names = re.split(r",\s*|\s+and\s+|\s*&\s*", m.group(1))
    return [n.strip().upper() for n in names if re.fullmatch(r"[A-Za-z0-9]{2,12}", n.strip())], m.group(2)


def list_catalog(client: httpx.Client, catalog: int, page_size: int = 50) -> list[dict]:
    out, page = [], 1
    while True:
        r = retry(client.get, f"{CMS}/catalog/list/query", params={"catalogId": catalog, "pageNo": page, "pageSize": page_size})
        r.raise_for_status()
        arts = (r.json().get("data") or {}).get("articles") or []
        if not arts:
            return out
        out += [{"code": a["code"], "title": a["title"]} for a in arts]
        page += 1
        time.sleep(PAUSE)


def detail(client: httpx.Client, code: str) -> dict:
    r = retry(client.get, f"{CMS}/detail/query", params={"articleCode": code})
    r.raise_for_status()
    d = r.json()["data"]
    time.sleep(PAUSE)
    return {"publication_ms": int(d["publishDate"]), "derniere_mise_a_jour_ms": int(d["lastUpdateTime"]) if d.get("lastUpdateTime") else None}


def minute_bars(symbol: str, days: list[str], cache, client) -> pd.DataFrame | None:
    parts = []
    for day in days:
        try:
            parts.append(data.read_month(retry(data.download_day, symbol, day, cache, client=client)))
        except httpx.HTTPStatusError:
            return None  # aucune archive publiée ce jour pour cette paire
    full, _ = data.complete_grid(pd.concat(parts, ignore_index=True))
    return full.set_index("t")


def window_returns(bars: pd.DataFrame, publish_ms: int) -> dict | None:
    """Entrée à l'ouverture de la première minute après la publication ; sorties à la clôture 1 h et 24 h plus tard."""
    t0 = (publish_ms // MINUTE + 1) * MINUTE
    def at(t, col):
        v = bars[col].get(t)
        return float(v) if v is not None and np.isfinite(v) else None
    entry, h1, d1, pre = at(t0, "o"), at(t0 + HOUR - MINUTE, "c"), at(t0 + DAY - MINUTE, "c"), at(t0 - DAY, "o")
    if entry is None or h1 is None or d1 is None:
        return None
    return {"r_1h": np.log(h1 / entry), "r_24h": np.log(d1 / entry), "r_avant_24h": np.log(entry / pre) if pre else None}


def event_study(events: list[dict], cache, client) -> list[dict]:
    rows = []
    for ev in events:
        t0 = (ev["publication_ms"] // MINUTE + 1) * MINUTE
        days = sorted({datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for t in (t0 - DAY, t0, t0 + DAY)})
        try:
            data.guard(t0 - DAY, t0 + DAY)
        except data.ReservedPeriodError:
            rows += [{**ev, "jeton": tk, "exclu": "période réservée"} for tk in ev["jetons"]]
            continue
        btc = minute_bars("BTCUSDT", days, cache, client)
        b = window_returns(btc, ev["publication_ms"]) if btc is not None else None
        for tk in ev["jetons"]:
            bars = minute_bars(f"{tk}USDT", days, cache, client)
            r = window_returns(bars, ev["publication_ms"]) if bars is not None else None
            if r is None or b is None:
                rows.append({**ev, "jeton": tk, "exclu": "pas de paire USDT cotée sur la fenêtre"})
                continue
            rows.append({**ev, "jeton": tk, "exclu": None, **{f"{k}_brut": v for k, v in r.items()},
                         "anormal_1h": r["r_1h"] - b["r_1h"], "anormal_24h": r["r_24h"] - b["r_24h"],
                         "anormal_avant_24h": (r["r_avant_24h"] - b["r_avant_24h"]) if r["r_avant_24h"] is not None and b["r_avant_24h"] is not None else None})
    return rows


def cluster_bootstrap_mean(frame: pd.DataFrame, col: str, draws: int = BOOT, seed: int = SEED) -> dict:
    """Moyenne et p unilatérale (moyenne < 0) par rééchantillonnage des annonces (les jetons d'une annonce ne sont pas indépendants)."""
    groups = [g[col].to_numpy() for _, g in frame.groupby("code")]
    rng = np.random.default_rng(seed)
    means = np.array([np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]).mean() for _ in range(draws)])
    return {"moyenne": float(frame[col].mean()), "mediane": float(frame[col].median()), "ic95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p_unilaterale": float((means >= 0).mean()), "evenements": int(len(frame)), "annonces": len(groups)}


def probe(client: httpx.Client, url: str, **kw) -> dict:
    try:
        r = client.get(url, timeout=20, **kw)
        return {"statut_http": r.status_code, "octets": len(r.content)}
    except httpx.HTTPError as e:
        return {"statut_http": None, "erreur": type(e).__name__}


def inventory(client: httpx.Client, catalogs: dict) -> list[dict]:
    hn = probe(client, "https://hn.algolia.com/api/v1/search", params={"query": "bitcoin", "tags": "story", "numericFilters": "created_at_i<1293840000", "hitsPerPage": 1})
    return [
        {"source": "Annonces Binance (CMS public)", "acces": "accessible", "catalogues": catalogs,
         "horodatage": "publishDate de la source (ms) et lastUpdateTime (corrections)", "limites": "titres et corps ; profondeur par catalogue ci-contre"},
        {"source": "GDELT 2.0, fichiers bruts (data.gdeltproject.org)", **probe(client, "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"),
         "premier_fichier_2015_02_18": probe(client, "https://data.gdeltproject.org/gdeltv2/20150218230000.export.CSV.zip")["statut_http"],
         "horodatage": "DATEADDED, toutes les 15 minutes depuis le 2015-02-18 (date de traitement par GDELT)", "limites": "volumineux ; thèmes et ton, pas de texte intégral"},
        {"source": "GDELT DOC 2.0 (api.gdeltproject.org)", **probe(client, "https://api.gdeltproject.org/api/v2/doc/doc", params={"query": "bitcoin", "mode": "timelinevol", "format": "json"}),
         "horodatage": "date de publication repérée par GDELT", "limites": "limitation de débit (429) ; archive depuis 2017"},
        {"source": "Hacker News (Algolia)", **hn, "horodatage": "created_at de la publication", "limites": "audience technique ; pas une source d'actualité financière"},
        {"source": "Wayback Machine (CDX)", **probe(client, "https://web.archive.org/cdx/search/cdx", params={"url": "cointelegraph.com/rss", "limit": 1}),
         "horodatage": "date de capture (borne supérieure de la disponibilité)", "limites": "accès depuis l'environnement à vérifier"},
        {"source": "Flux RSS des médias crypto (CoinDesk, Cointelegraph…)", **probe(client, "https://cointelegraph.com/rss"),
         "horodatage": "pubDate", "limites": "derniers articles seulement : aucune profondeur historique sans collecte prospective (non autorisée)"},
        {"source": "Réserve fédérale et BLS (calendriers officiels)", **probe(client, "https://www.federalreserve.gov/feeds/press_all.xml"),
         "horodatage": "heure officielle de publication", "limites": "déjà utilisés (C007, #158)"},
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Archives d'actualités : inventaire et retraits de cote Binance (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/annonces-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        cat = client.get(f"{CMS}/list/query", params={"type": 1, "pageNo": 1, "pageSize": 1}).json()["data"]["catalogs"]
        totals = {c["catalogId"]: c.get("total") for c in cat}
        delist = list_catalog(client, 161)
        oldest = detail(client, delist[-1]["code"]) if delist else {}
        catalogs = {CATALOGS[k]: {"articles": totals.get(k)} for k in CATALOGS}
        catalogs["retraits de cote"]["plus_ancienne_publication"] = (datetime.fromtimestamp(oldest["publication_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                                                                       if oldest else None)
        inv = inventory(client, catalogs)
        events = []
        for a in delist:
            parsed = tokens_of(a["title"])
            if parsed and parsed[0]:
                events.append({**a, "jetons": parsed[0], "retrait_le": parsed[1], **detail(client, a["code"])})
        rows = event_study(events, Path(args.cache), client)
    frame = pd.DataFrame(rows)
    kept = frame[frame["exclu"].isna()]
    tests = [{"test": "H1 rendement anormal moyen à 24 h après l'annonce < 0", **cluster_bootstrap_mean(kept, "anormal_24h")},
             {"test": "H2 rendement anormal moyen à 1 h après l'annonce < 0", **cluster_bootstrap_mean(kept, "anormal_1h")}]
    for t, ph in zip(tests, essais.holm([t["p_unilaterale"] for t in tests])):
        t["p_holm"], t["significatif_holm_5pct"] = float(ph), bool(ph < 0.05)
    pre = kept.dropna(subset=["anormal_avant_24h"])
    revised = sum(1 for e in events if e["derniere_mise_a_jour_ms"] and e["derniere_mise_a_jour_ms"] - e["publication_ms"] > HOUR)
    out = {"schema": "scalp-annonces-v1", "status": "inventaire et étude historique hors ligne ; aucune collecte prospective ; aucune règle modifiée",
           "capacites": ["C001", "C003", "C008"], "inventaire": inv,
           "retraits": {"annonces_du_catalogue": len(delist), "annonces_de_retrait_au_comptant": len(events),
                        "jetons": int(len(frame)), "jetons_mesures": int(len(kept)), "exclusions": frame["exclu"].value_counts().to_dict() if len(frame) else {},
                        "annonces_corrigees_plus_d_une_heure_apres": revised,
                        "derive_avant_annonce_24h": {"moyenne": float(pre["anormal_avant_24h"].mean()) if len(pre) else None, "evenements": int(len(pre))},
                        "part_sous_moins_10pct_a_24h": float((kept["anormal_24h"] < np.log(0.9)).mean()) if len(kept) else None,
                        "par_annee": {str(y): {"evenements": int(len(g)), "anormal_24h_moyen": float(g["anormal_24h"].mean())}
                                      for y, g in kept.groupby(pd.to_datetime(kept["publication_ms"], unit="ms").dt.year)} if len(kept) else {}},
           "tests": tests, "evenements": rows, "seconds": round(time.time() - t0)}
    print(json.dumps({k: v for k, v in out["retraits"].items()}, ensure_ascii=False, default=float))
    for t in tests:
        print(t)
    for s in inv:
        print(s["source"], s.get("statut_http"), s.get("erreur"))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "annonces-v1", "retraits": out["retraits"],
                                 "tests": {t["test"]: {"moyenne": t["moyenne"], "p_holm": t["p_holm"]} for t in tests}}, ensure_ascii=False, default=float) + "\n")
    return out


if __name__ == "__main__":
    main()
