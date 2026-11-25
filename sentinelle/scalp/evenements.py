"""Études de réactions aux événements macroéconomiques programmés (lot 2 : C007, avec C001 côté marché et C006 borné).

Événements : communiqués de politique monétaire de la Fed (FOMC, 14:00 heure de New York) et publications de l'indice des
prix américain (CPI, 8:30 heure de New York), dates lues sur les calendriers officiels (federalreserve.gov, bls.gov).
Heure convertie en UTC avec les changements d'heure. Données : bougies d'une minute du BTCUSDT (archives publiques à
empreinte vérifiée) ; la période réservée n'est pas chargée (data.guard).
Mesures à partir de l'heure officielle de publication (disponibilité effective, aucune anticipation) :
- variation absolue sur 5, 30 et 60 minutes, comparée au même instant sept jours plus tôt (témoin apparié) ;
- variation signée sur 60 minutes (son sens n'est pas prévisible sans la surprise contre le consensus : C006 bloquée,
  aucun consensus public horodaté) ;
- minute du plus grand écart dans l'heure (délai de réaction du marché, volet « marché » de C001).
Intervalles par rééchantillonnage des événements, Holm sur les tests principaux. Mesure seulement.

Lancement : python -m sentinelle.scalp.evenements
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

from . import data, essais

NY = ZoneInfo("America/New_York")
SYMBOL = "BTCUSDT"
WINDOWS = (5, 30, 60)
CONTROL_DAYS = 7
DRAWS, SEED = 2000, 0
SOURCES = {"FOMC": ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                     "https://www.federalreserve.gov/monetarypolicy/fomchistorical2018.htm",
                     "https://www.federalreserve.gov/monetarypolicy/fomchistorical2019.htm",
                     "https://www.federalreserve.gov/monetarypolicy/fomchistorical2020.htm"],
           "CPI": ["https://www.bls.gov/bls/news-release/cpi.htm"]}
# Communiqués des réunions programmées (dernier jour de la réunion), lus le 24/11/2026. Les réunions et annonces non
# programmées (mars 2020…) sont exclues : leur heure n'est pas celle du calendrier.
FOMC = [
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19", "2019-01-30",
    "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11", "2020-01-29", "2020-04-29",
    "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16", "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15", "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27",
    "2022-09-21", "2022-11-02", "2022-12-14", "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20",
    "2023-11-01", "2023-12-13", "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07",
    "2024-12-18", "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
]
FOMC_NOT_VERBATIM = {"2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
                     "2022-11-02", "2022-12-14"}  # lues dans une synthèse de la page, contrôle de sensibilité sans elles
# Publications du CPI (code de date des liens d'archive du BLS) ; celle d'octobre 2025 n'a pas eu lieu (interruption budgétaire).
CPI = [
    "2018-02-14", "2018-03-13", "2018-04-11", "2018-05-10", "2018-06-12", "2018-07-12", "2018-08-10", "2018-09-13", "2018-10-11",
    "2018-11-14", "2018-12-12", "2019-01-11", "2019-02-13", "2019-03-12", "2019-04-10", "2019-05-10", "2019-06-12", "2019-07-11",
    "2019-08-13", "2019-09-12", "2019-10-10", "2019-11-13", "2019-12-11", "2020-01-14", "2020-02-13", "2020-03-11", "2020-04-10",
    "2020-05-12", "2020-06-10", "2020-07-14", "2020-08-12", "2020-09-11", "2020-10-13", "2020-11-12", "2020-12-10", "2021-01-13",
    "2021-02-10", "2021-03-10", "2021-04-13", "2021-05-12", "2021-06-10", "2021-07-13", "2021-08-11", "2021-09-14", "2021-10-13",
    "2021-11-10", "2021-12-10", "2022-01-12", "2022-02-10", "2022-03-10", "2022-04-12", "2022-05-11", "2022-06-10", "2022-07-13",
    "2022-08-10", "2022-09-13", "2022-10-13", "2022-11-10", "2022-12-13", "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12",
    "2023-05-10", "2023-06-13", "2023-07-12", "2023-08-10", "2023-09-13", "2023-10-12", "2023-11-14", "2023-12-12", "2024-01-11",
    "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14", "2024-09-11", "2024-10-10",
    "2024-11-13", "2024-12-11", "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13", "2025-06-11", "2025-07-15",
    "2025-08-12", "2025-09-11", "2025-10-24", "2025-12-18", "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12",
    "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-10",
]
LOCAL_TIME = {"FOMC": "14:00", "CPI": "08:30"}


def release_utc(day: str, local: str) -> pd.Timestamp:
    """Heure de publication (heure de New York) convertie en UTC, changements d'heure compris."""
    t = datetime.strptime(f"{day} {local}", "%Y-%m-%d %H:%M").replace(tzinfo=NY)
    return pd.Timestamp(t).tz_convert("UTC")


def control_time(t: pd.Timestamp, events: set[str]) -> pd.Timestamp:
    """Même heure, sept jours plus tôt ; reculer d'une semaine de plus si ce jour est lui-même un événement."""
    c = t - pd.Timedelta(days=CONTROL_DAYS)
    while str(c.date()) in events:
        c -= pd.Timedelta(days=CONTROL_DAYS)
    return c


def reaction(closes: pd.Series, t_ms: int) -> dict | None:
    """Réaction après l'instant t (ms) : variations sur 5, 30, 60 minutes et minute du plus grand écart. Aucune donnée
    antérieure à t n'est comparée à une donnée postérieure autrement que par le cours de référence de la minute t."""
    ref_t = t_ms - 60_000  # clôture de la minute qui précède la publication : dernier cours connu avant l'annonce
    if ref_t not in closes.index:
        return None
    ref = closes.at[ref_t]
    out = {}
    path = []
    for k in range(1, max(WINDOWS) + 1):
        tk = t_ms + (k - 1) * 60_000
        if tk not in closes.index:
            return None
        path.append(np.log(closes.at[tk] / ref))
    path = np.array(path)
    for k in WINDOWS:
        out[f"abs_{k}"] = float(abs(path[k - 1]))
        out[f"signe_{k}"] = float(path[k - 1])
    out["minute_du_pic"] = int(np.argmax(np.abs(path)) + 1)
    return out


def paired_test(event: np.ndarray, control: np.ndarray, *, two_sided: bool = False) -> dict:
    """Écart moyen événement − témoin, intervalle à 95 % et p-valeur par rééchantillonnage des paires."""
    diff = event - control
    rng = np.random.default_rng(SEED)
    means = diff[rng.integers(0, len(diff), (DRAWS, len(diff)))].mean(axis=1)
    centred = means - diff.mean()
    p = float((np.abs(centred) >= abs(diff.mean())).mean()) if two_sided else float((centred >= diff.mean()).mean())
    return {"n": int(len(diff)), "ecart_moyen": float(diff.mean()), "ic95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p": p, "mediane_evenement": float(np.median(event)), "mediane_temoin": float(np.median(control))}


def signed_test(x: np.ndarray) -> dict:
    """Moyenne de la variation signée, bilatéral (aucun sens attendu sans la surprise)."""
    return paired_test(x, np.zeros_like(x), two_sided=True)


def events(last_day: str = "2026-11-22") -> list[dict]:
    """Événements programmés convertis en UTC ; ceux dont le jour ou le témoin tombe dans la période réservée sont exclus."""
    lo, hi = data.RESERVED_FROM[:10], data.RESERVED_TO[:10]
    out = []
    for kind, days in (("FOMC", FOMC), ("CPI", CPI)):
        dates = set(days)
        for d in days:
            t = release_utc(d, LOCAL_TIME[kind])
            c = control_time(t, dates)
            if str(t.date()) > last_day or any(lo <= str(x.date()) <= hi for x in (t, c)):
                continue
            out.append({"type": kind, "jour": d, "utc": t, "temoin_utc": c, "verbatim": not (kind == "FOMC" and d in FOMC_NOT_VERBATIM)})
    return out


def day_closes(day: str, cache_dir, client) -> pd.Series | None:
    """Clôtures minute du BTCUSDT pour un jour UTC (archive journalière à empreinte vérifiée), indexées par l'heure d'ouverture."""
    try:
        m = data.read_month(data.download_day(SYMBOL, day, cache_dir, client=client))
    except (httpx.HTTPStatusError, data.ReservedPeriodError):
        return None
    return pd.Series(m["c"].to_numpy(float), index=m["t"].astype("int64").to_numpy())


def measure(ev: dict, cache_dir, client) -> dict:
    out = dict(ev)
    for key, t in (("evenement", ev["utc"]), ("temoin", ev["temoin_utc"])):
        closes = day_closes(str(t.date()), cache_dir, client)
        out[key] = reaction(closes, int(t.value // 1_000_000)) if closes is not None else None
    return out


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["evenement"] and r["temoin"]]
    ev = {k: np.array([r["evenement"][k] for r in ok]) for k in ok[0]["evenement"]} if ok else {}
    ct = {k: np.array([r["temoin"][k] for r in ok]) for k in ok[0]["temoin"]} if ok else {}
    out = {"evenements": len(rows), "mesures": len(ok)}
    if not ok:
        return out
    for k in WINDOWS:
        out[f"absolu_{k}_min"] = paired_test(ev[f"abs_{k}"], ct[f"abs_{k}"])
    out["signe_60_min"] = signed_test(ev["signe_60"])
    out["minute_du_pic_mediane"] = {"evenement": float(np.median(ev["minute_du_pic"])), "temoin": float(np.median(ct["minute_du_pic"]))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Réactions du BTC aux publications FOMC et CPI (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache")
    ap.add_argument("--out", default="research/scalp-v1/evenements-macro-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    evs = events()
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"}) as client:
        with ThreadPoolExecutor(8) as pool:
            rows = list(pool.map(lambda e: measure(e, Path(args.cache) / "1m-evenements", client), evs))
    res = {kind: summarize([r for r in rows if r["type"] == kind]) for kind in ("FOMC", "CPI")}
    res["FOMC dates relues mot à mot seulement"] = summarize([r for r in rows if r["type"] == "FOMC" and r["verbatim"]])
    primary = [(kind, "absolu_30_min") for kind in ("FOMC", "CPI")] + [(kind, "signe_60_min") for kind in ("FOMC", "CPI")]
    for (kind, key), ph in zip(primary, essais.holm([res[kind][key]["p"] for kind, key in primary])):
        res[kind][key]["p_holm"], res[kind][key]["significatif_holm_5pct"] = ph, bool(ph < 0.05)
    out = {"schema": "scalp-evenements-macro-v1", "status": "mesure sur historique ; heures officielles de publication ; aucune intégration",
           "capacites": ["C007", "C001", "C006"], "sources": SOURCES,
           "heures": {"FOMC": "14:00 heure de New York (« For release at 2:00 p.m. » sur les communiqués)",
                      "CPI": "8:30 heure de New York (« embargoed until 8:30 a.m. (ET) » sur les publications)"},
           "surprise_contre_consensus_C006": "NON MESURÉE : aucun consensus antérieur public et horodaté trouvé ; le sens de la réaction n'est donc pas prévisible ici",
           "resultats": res, "seconds": round(time.time() - t0)}
    for kind, r in res.items():
        print(kind, {k: r[k] for k in ("evenements", "mesures")})
        for key in ("absolu_5_min", "absolu_30_min", "absolu_60_min", "signe_60_min"):
            if key in r:
                x = r[key]
                print(f"   {key:15} évén. médiane {100 * x['mediane_evenement']:.3f} % | témoin {100 * x['mediane_temoin']:.3f} % | écart moyen "
                      f"{100 * x['ecart_moyen']:+.3f} % IC [{100 * x['ic95'][0]:+.3f} ; {100 * x['ic95'][1]:+.3f}] p {x['p']:.4f}"
                      + (f" Holm {x['p_holm']:.4f}" if "p_holm" in x else ""))
        if "minute_du_pic_mediane" in r:
            print("   minute du pic (médiane)", r["minute_du_pic_mediane"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "evenements-macro-v1",
                                 "resultats": {f"{k} {key}": {"ecart_moyen": res[k][key]["ecart_moyen"], "p_holm": res[k][key]["p_holm"]} for k, key in primary}},
                                ensure_ascii=False, default=str) + "\n")
    return out


if __name__ == "__main__":
    main()
