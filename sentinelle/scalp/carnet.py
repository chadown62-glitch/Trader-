"""C042 — archives publiques de carnets d'ordres (décision du propriétaire du 25 novembre 2026, simulation exclusive).

Source : data.binance.vision, « bookDepth » des contrats perpétuels USDⓈ-M (aucune archive équivalente au comptant).
Chaque fichier journalier donne, toutes les 30 secondes environ, la profondeur cumulée (quantité et montant) à ±1, 2, 3, 4
et 5 % du prix ; le niveau ±0,2 % n'apparaît qu'entre décembre 2025 et mars 2026. Les archives commencent le 2023-01-01.

1. Intégrité : chaque archive est vérifiée par sa somme de contrôle SHA-256 publiée ; l'empreinte est conservée.
2. Couverture : jours publiés ou absents, instantanés par jour (2 880 attendus), plus long trou, doublons, niveaux manquants.
3. Disponibilité dans le temps : date de publication (en-tête Last-Modified) de chaque archive, délai après la fin du jour ;
   alignement horaire du carnet sur les bougies d'une minute des mêmes contrats (prix implicite contre clôture, décalages
   de −8 h à +8 h).
4. Étude préenregistrée : le déséquilibre du carnet I_k = (profondeur acheteuse − vendeuse) / somme, à ±k %, connu à
   l'instant t, est-il lié au rendement de t à t + h (5 min, 60 min, 1 jour) ? BTC et ETH, k = 1 et 5 % (présents sur
   toute la fenêtre) ; ±0,2 % descriptif seulement. Corrélation de rang par jour, moyenne sur les jours (inférence par
   jour) ; pour 1 jour, corrélation sur les jours et permutation ; Holm sur les 12 tests.
Fenêtre : 2023-01-01 → 2026-08-31. La période réservée (2026-09-01 → 2026-11-22) reste refusée par `data.guard`, comme pour
toutes les données à la minute. Aucune règle de trading, aucun ordre, aucun levier : mesure seulement.

Lancement : python -m sentinelle.scalp.carnet
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from . import data, essais

BOOK_URL = "https://data.binance.vision/data/futures/um/daily/bookDepth"
KLINE_URL = "https://data.binance.vision/data/futures/um/monthly/klines"
FIRST_DAY, LAST_DAY = "2023-01-01", "2026-08-31"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
LEVELS = (0.2, 1.0, 5.0)
TESTED = (1.0, 5.0)  # niveaux présents sur toute la fenêtre ; ±0,2 % est descriptif
HORIZONS_MIN = (5, 60)
STEP_S, STALE_S, DAY_S = 300, 60, 86_400
EXPECTED_SNAPSHOTS = 2880
PERMUTATIONS, SEED = 2000, 20261130
SHIFTS_H = (-8, -1, 0, 1, 8)


class IntegrityError(RuntimeError):
    pass


def day_ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def days(first: str = FIRST_DAY, last: str = LAST_DAY) -> list[str]:
    d0, d1 = datetime.strptime(first, "%Y-%m-%d"), datetime.strptime(last, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]


def fetch(client: httpx.Client, url: str) -> tuple[bytes | None, dict]:
    """Archive et somme de contrôle publiée ; None si l'archive n'existe pas (404). Toute discordance est une erreur."""
    published = client.get(url + ".CHECKSUM")
    if published.status_code == 404:
        return None, {"publiee": False}
    published.raise_for_status()
    archive = client.get(url)
    archive.raise_for_status()
    expected = published.text.split()[0].strip().lower()
    got = hashlib.sha256(archive.content).hexdigest()
    if got != expected:
        raise IntegrityError(f"{url} : empreinte {got} différente de la somme publiée {expected}")
    modified = archive.headers.get("last-modified")
    return archive.content, {"publiee": True, "sha256": got, "octets": len(archive.content),
                             "publication_utc": parsedate_to_datetime(modified).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if modified else None}


def read_zip_csv(body: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))


def parse_book(frame: pd.DataFrame) -> pd.DataFrame:
    """Colonnes : ts (secondes UTC), pct, depth, notional."""
    ts = pd.to_datetime(frame["timestamp"], utc=True)
    return pd.DataFrame({"ts": (ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1),
                         "pct": frame["percentage"].astype(float).round(2), "depth": frame["depth"].astype(float),
                         "notional": frame["notional"].astype(float)})


def wide(book: pd.DataFrame) -> pd.DataFrame:
    """Un instantané par ligne : déséquilibre par niveau, prix implicite (montant / quantité des tranches à ±1 %)."""
    depth = book.pivot_table(index="ts", columns="pct", values="depth", aggfunc="last")
    note = book.pivot_table(index="ts", columns="pct", values="notional", aggfunc="last")
    out = pd.DataFrame(index=depth.index)
    for k in LEVELS:
        b, a = depth.get(-k), depth.get(k)
        out[f"i{k:g}"] = (b - a) / (b + a) if b is not None and a is not None else np.nan
    out["mid"] = (note[-1.0] / depth[-1.0] + note[1.0] / depth[1.0]) / 2 if all(x in depth for x in (-1.0, 1.0)) else np.nan
    out["niveaux"] = depth.notna().sum(axis=1)
    return out


def quality(book: pd.DataFrame, snaps: pd.DataFrame) -> dict:
    ts = snaps.index.to_numpy()
    gaps = np.diff(ts) if len(ts) > 1 else np.array([0])
    fmt = int(book["pct"].nunique())
    return {"lignes": int(len(book)), "instantanes": int(len(snaps)), "instantanes_attendus": EXPECTED_SNAPSHOTS,
            "niveaux_du_format": fmt, "plus_long_trou_s": int(gaps.max()), "doublons": int(book.duplicated(["ts", "pct"]).sum()),
            "instantanes_incomplets": int((snaps["niveaux"] < fmt).sum())}


def on_grid(snaps: pd.DataFrame, start_s: int, length_s: int = DAY_S, step_s: int = STEP_S, stale_s: int = STALE_S) -> pd.DataFrame:
    """Dernier instantané connu à chaque instant de la grille (au plus 60 s d'ancienneté), jamais un instantané postérieur."""
    grid = np.arange(start_s, start_s + length_s, step_s)
    ts = snaps.index.to_numpy()
    pos = np.searchsorted(ts, grid, side="right") - 1
    ok = (pos >= 0) & (grid - ts[np.clip(pos, 0, None)] <= stale_s)
    vals = snaps.drop(columns="niveaux").to_numpy(dtype=float)[np.clip(pos, 0, None)]
    vals[~ok] = np.nan
    return pd.DataFrame(vals, index=pd.Index(grid, name="t"), columns=[c for c in snaps.columns if c != "niveaux"])


def parse_klines(frame: pd.DataFrame) -> pd.Series:
    """Clôture de chaque bougie d'une minute, indexée par la fin de la bougie (secondes UTC)."""
    if not str(frame.columns[0]).startswith("open_time"):
        frame = pd.DataFrame(np.vstack([frame.columns.to_numpy(), frame.to_numpy()]), columns=range(frame.shape[1]))
        frame = frame.rename(columns={0: "open_time", 4: "close"})
    t = frame["open_time"].astype("int64")
    t = np.where(t > 10**14, t // 1000, t)  # microsecondes éventuelles → millisecondes
    return pd.Series(frame["close"].astype(float).to_numpy(), index=pd.Index(t // 1000 + 60, name="t")).sort_index()


def book_day(client: httpx.Client, symbol: str, day: str, cache: Path) -> tuple[pd.DataFrame | None, dict]:
    start = day_ms(day)
    data.guard(start, start + DAY_S * 1000 - 1)  # période réservée refusée, comme pour toute donnée à la minute
    path = cache / symbol / f"{day}.csv"
    meta_path = path.with_suffix(".json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta.get("publiee"):
            return None, meta  # absence déjà constatée
        if path.exists():
            return pd.read_csv(path, index_col="t"), meta
    body, meta = fetch(client, f"{BOOK_URL}/{symbol}/{symbol}-bookDepth-{day}.zip")
    meta["jour"] = day
    if body is None:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return None, meta
    book = parse_book(read_zip_csv(body))
    snaps = wide(book)
    meta["qualite"] = quality(book, snaps)
    grid = on_grid(snaps, start // 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    grid.to_csv(path)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return grid, meta


def kline_month(client: httpx.Client, symbol: str, month: str, cache: Path) -> tuple[pd.Series, dict]:
    path = cache / symbol / f"klines-{month}.csv"
    meta_path = path.with_suffix(".json")
    if path.exists() and meta_path.exists():
        return pd.read_csv(path, index_col="t")["close"], json.loads(meta_path.read_text(encoding="utf-8"))
    body, meta = fetch(client, f"{KLINE_URL}/{symbol}/1m/{symbol}-1m-{month}.zip")
    if body is None:
        raise IntegrityError(f"bougies {symbol} {month} absentes")
    close = parse_klines(read_zip_csv(body))
    path.parent.mkdir(parents=True, exist_ok=True)
    close.rename("close").to_csv(path)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return close, meta


def alignment(grid: pd.DataFrame, close: pd.Series) -> dict:
    """Écart médian (pb) entre le prix implicite du carnet et la clôture de la minute, pour plusieurs décalages horaires."""
    out = {}
    for h in SHIFTS_H:
        c = close.reindex(grid.index + 3600 * h)
        ok = grid["mid"].notna().to_numpy() & c.notna().to_numpy()
        out[f"{h:+d} h"] = float(np.median(np.abs(np.log(grid["mid"].to_numpy()[ok] / c.to_numpy()[ok]))) * 1e4) if ok.any() else None
    return out


def _p_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def intraday_test(grid: pd.DataFrame, close: pd.Series, level: float, horizon_min: int) -> dict:
    """Corrélation de rang par jour entre I_k(t) et le rendement de t à t + h ; moyenne et erreur type sur les jours."""
    p0 = close.reindex(grid.index).to_numpy()
    p1 = close.reindex(grid.index + horizon_min * 60).to_numpy()
    frame = pd.DataFrame({"i": grid[f"i{level:g}"].to_numpy(), "r": np.log(p1 / p0), "jour": grid.index // DAY_S})
    frame = frame.dropna()
    rho = frame.groupby("jour").apply(lambda d: d["i"].rank().corr(d["r"].rank()) if len(d) >= 50 else np.nan, include_groups=False).dropna()
    n = len(rho)
    se = float(rho.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    z = float(rho.mean() / se) if n > 1 and se > 0 else float("nan")
    q = frame["i"].quantile([0.1, 0.9])
    top, bottom = frame.loc[frame["i"] >= q[0.9], "r"], frame.loc[frame["i"] <= q[0.1], "r"]
    return {"jours": int(n), "observations": int(len(frame)), "rang_moyen": float(rho.mean()), "erreur_type": se, "z": z,
            "p_bilaterale": _p_two_sided(z) if np.isfinite(z) else float("nan"),
            "rendement_moyen_pb": {"decile_superieur": float(top.mean() * 1e4), "decile_inferieur": float(bottom.mean() * 1e4),
                                   "ensemble": float(frame["r"].mean() * 1e4)}}


def daily_test(grid: pd.DataFrame, close: pd.Series, level: float, perms: int, rng: np.random.Generator) -> dict:
    """I_k au dernier instant de la grille de chaque jour (23:55 UTC) contre le rendement des 24 heures suivantes."""
    starts = grid.index[grid.index % DAY_S == DAY_S - STEP_S]
    i = grid.loc[starts, f"i{level:g}"].to_numpy()
    r = np.log(close.reindex(starts + DAY_S).to_numpy() / close.reindex(starts).to_numpy())
    ok = np.isfinite(i) & np.isfinite(r)
    a, b = pd.Series(i[ok]).rank().to_numpy(), pd.Series(r[ok]).rank().to_numpy()
    rho = float(np.corrcoef(a, b)[0, 1])
    null = np.array([np.corrcoef(a, rng.permutation(b))[0, 1] for _ in range(perms)])
    return {"jours": int(ok.sum()), "rang": rho, "p_bilaterale": float((1 + np.sum(np.abs(null) >= abs(rho))) / (1 + perms))}


def _rank_corr(a, b) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    return float(pd.Series(a[ok]).rank().corr(pd.Series(b[ok]).rank())) if ok.sum() > 10 else float("nan")


def robustness(grid: pd.DataFrame, close: pd.Series, level: float, horizon_min: int) -> dict:
    """Contrôles a posteriori (non confirmatoires) : rendement décalé de 5 min, placebo sur le passé, sans recouvrement,
    corrélation groupée."""
    i = grid[f"i{level:g}"].to_numpy()
    t, h = grid.index.to_numpy(), horizon_min * 60
    price = lambda x: close.reindex(x).to_numpy()  # noqa: E731
    future, shifted, past = np.log(price(t + h) / price(t)), np.log(price(t + 300 + h) / price(t + 300)), np.log(price(t) / price(t - h))
    step = max(1, h // STEP_S)
    days = t // DAY_S

    def per_day(x, sel=slice(None)):
        frame = pd.DataFrame({"i": i[sel], "r": x[sel], "j": days[sel]}).dropna()
        rho = frame.groupby("j").apply(lambda d: d["i"].rank().corr(d["r"].rank()) if len(d) >= max(10, 50 // step) else np.nan,
                                       include_groups=False).dropna()
        return {"rang_moyen": float(rho.mean()), "z": float(rho.mean() / (rho.std(ddof=1) / math.sqrt(len(rho)))) if len(rho) > 1 else float("nan"), "jours": int(len(rho))}

    def partial_per_day():
        frame = pd.DataFrame({"i": i, "f": future, "p": past, "j": days}).dropna()

        def one(d):
            if len(d) < 50:
                return np.nan
            ri, rf, rp = (d[c].rank().to_numpy() for c in ("i", "f", "p"))
            res = lambda y: y - np.polyval(np.polyfit(rp, y, 1), rp)  # noqa: E731
            return float(np.corrcoef(res(ri), res(rf))[0, 1])

        rho = frame.groupby("j").apply(one, include_groups=False).dropna()
        return {"rang_moyen": float(rho.mean()), "z": float(rho.mean() / (rho.std(ddof=1) / math.sqrt(len(rho)))), "jours": int(len(rho))}

    reversal_future = pd.DataFrame({"i": -past, "r": future, "j": days}).dropna()
    rho_rev = reversal_future.groupby("j").apply(lambda d: d["i"].rank().corr(d["r"].rank()) if len(d) >= 50 else np.nan, include_groups=False).dropna()
    return {"futur": per_day(future), "decale_de_5_min": per_day(shifted), "placebo_passe": per_day(past),
            "sans_recouvrement": per_day(future, slice(None, None, step)), "groupe_toutes_observations": _rank_corr(i, future),
            "retour_pur": {"rang_moyen": float(rho_rev.mean()), "z": float(rho_rev.mean() / (rho_rev.std(ddof=1) / math.sqrt(len(rho_rev)))), "jours": int(len(rho_rev))},
            "apport_propre_a_rendement_passe_donne": partial_per_day()}


def daily_terciles(grid: pd.DataFrame, close: pd.Series, level: float) -> dict:
    starts = grid.index[grid.index % DAY_S == DAY_S - STEP_S]
    i = grid.loc[starts, f"i{level:g}"].to_numpy()
    r = np.log(close.reindex(starts + DAY_S).to_numpy() / close.reindex(starts).to_numpy())
    frame = pd.DataFrame({"i": i, "r": r}).dropna()
    frame["tercile"] = pd.qcut(frame["i"], 3, labels=["bas", "milieu", "haut"])
    return {str(k): {"jours": int(len(g)), "rendement_moyen_pb": float(g["r"].mean() * 1e4)} for k, g in frame.groupby("tercile", observed=True)}


def robustness_report(grids: dict, closes: dict) -> dict:
    out = {"statut": "contrôles a posteriori déclarés avant calcul (trials.jsonl), non confirmatoires"}
    for s in grids:
        for k in TESTED:
            for h in HORIZONS_MIN:
                out[f"{s} ±{k:g} % {h} min"] = robustness(grids[s], closes[s], k, h)
        out[f"{s} ±5 % 1 jour, terciles"] = daily_terciles(grids[s], closes[s], 5.0)
    return out


def coverage(metas: list[dict]) -> dict:
    published = [m for m in metas if m.get("publiee")]
    q = [m["qualite"] for m in published]
    delays = []
    for m in published:
        if m.get("publication_utc"):
            end = datetime.strptime(m["jour"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            pub = datetime.strptime(m["publication_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            delays.append((pub - end).total_seconds() / 3600)
    d = np.array(delays) if delays else np.array([np.nan])
    snaps = np.array([x["instantanes"] for x in q]) if q else np.array([0])
    return {"jours_demandes": len(metas), "jours_publies": len(published), "jours_absents": [m["jour"] for m in metas if not m.get("publiee")],
            "empreintes_verifiees": len(published), "octets": int(sum(m["octets"] for m in published)),
            "instantanes_par_jour": {"mediane": float(np.median(snaps)), "minimum": int(snaps.min()),
                                     "jours_sous_95pct": int((snaps < 0.95 * EXPECTED_SNAPSHOTS).sum())},
            "plus_long_trou_s": int(max((x["plus_long_trou_s"] for x in q), default=0)),
            "jours_avec_doublons": int(sum(x["doublons"] > 0 for x in q)),
            "instantanes_incomplets": int(sum(x["instantanes_incomplets"] for x in q)),
            "premier_jour_a_12_niveaux": next((m["jour"] for m in published if m["qualite"]["niveaux_du_format"] >= 12), None),
            "delai_de_publication_h": {"mediane": float(np.nanmedian(d)), "p95": float(np.nanpercentile(d, 95)), "maximum": float(np.nanmax(d)),
                                       "publiees_plus_de_7_jours_apres": int(np.sum(d > 168))}}


def study(grids: dict, closes: dict, perms: int = PERMUTATIONS, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    tests, names = {}, []
    for s in grids:
        for k in TESTED:
            for h in HORIZONS_MIN:
                tests[f"{s} ±{k:g} % {h} min"] = intraday_test(grids[s], closes[s], k, h)
                names.append(f"{s} ±{k:g} % {h} min")
            tests[f"{s} ±{k:g} % 1 jour"] = daily_test(grids[s], closes[s], k, perms, rng)
            names.append(f"{s} ±{k:g} % 1 jour")
    adj = essais.holm([tests[n]["p_bilaterale"] for n in names])
    for n, p in zip(names, adj):
        tests[n]["p_holm"] = float(p)
        tests[n]["significatif_holm_5pct"] = bool(p < 0.05)
    for s in grids:  # ±0,2 % : descriptif (niveau absent avant fin 2025), hors correction
        for h in HORIZONS_MIN:
            if grids[s]["i0.2"].notna().sum() > 0:
                tests[f"{s} ±0.2 % {h} min (descriptif)"] = intraday_test(grids[s], closes[s], 0.2, h)
    return tests


def main(argv=None):
    ap = argparse.ArgumentParser(description="C042 — archives publiques de carnets d'ordres (simulation exclusive)")
    ap.add_argument("--cache", default="data/scalp-cache/carnet")
    ap.add_argument("--first", default=FIRST_DAY)
    ap.add_argument("--last", default=LAST_DAY)
    ap.add_argument("--out", default="research/scalp-v1/carnet-c042-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    cache = Path(args.cache)
    wanted = days(args.first, args.last)
    months = sorted({d[:7] for d in wanted})
    grids, closes, report = {}, {}, {}
    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "sentinelle-simulation"},
                      limits=httpx.Limits(max_connections=16)) as client:
        for s in SYMBOLS:
            with ThreadPoolExecutor(16) as pool:
                books = list(pool.map(partial(book_day, client, s, cache=cache), wanted))
                klines = list(pool.map(partial(kline_month, client, s, cache=cache), months))
            grids[s] = pd.concat([g for g, _ in books if g is not None]).sort_index()
            closes[s] = pd.concat([c for c, _ in klines]).sort_index()
            closes[s] = closes[s][~closes[s].index.duplicated()]
            report[s] = {"carnet": coverage([m for _, m in books]), "bougies": {"mois": len(months), "empreintes_verifiees": len(klines),
                                                                              "publication_utc": {m: k["publication_utc"] for m, (_, k) in zip(months, klines)}},
                         "alignement_pb": alignment(grids[s], closes[s]),
                         "provenance": {"carnet": {m["jour"]: m.get("sha256") for _, m in books}, "bougies": {m: k["sha256"] for m, (_, k) in zip(months, klines)}}}
    tests = study(grids, closes)
    checks = robustness_report(grids, closes)
    out = {"schema": "scalp-carnet-c042-v1", "status": "archives publiques vérifiées ; mesure sur historique jamais étudié par le projet ; aucune règle de trading",
           "capacites": ["C042"], "source": {"carnet": BOOK_URL, "bougies": KLINE_URL, "marche": "contrats perpétuels USDⓈ-M (aucune archive au comptant)"},
           "fenetre": {"de": args.first, "a": args.last, "periode_reservee_exclue": [data.RESERVED_FROM, data.RESERVED_TO]},
           "niveaux_pct": list(LEVELS), "niveaux_testes_pct": list(TESTED), "horizons_min": list(HORIZONS_MIN) + [1440], "inventaire": report, "tests": tests, "controles_a_posteriori": checks, "seconds": round(time.time() - t0)}
    for s in SYMBOLS:
        c = report[s]["carnet"]
        print(s, {k: c[k] for k in ("jours_demandes", "jours_publies", "empreintes_verifiees", "instantanes_par_jour", "delai_de_publication_h")},
              "alignement :", report[s]["alignement_pb"])
    for name, r in tests.items():
        print(name, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "carnet-c042-v1",
                                 "couverture": {s: {k: report[s]["carnet"][k] for k in ("jours_publies", "empreintes_verifiees")} for s in SYMBOLS},
                                 "tests": {n: {k: r.get(k) for k in ("p_holm", "significatif_holm_5pct")} | {"rang": r.get("rang_moyen", r.get("rang"))} for n, r in tests.items()}},
                                ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
