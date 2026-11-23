"""Bougies d'une minute publiques de Binance, vérifiées et datées sans biais.

- Source : archives mensuelles de data.binance.vision, avec leur somme de contrôle SHA-256 publiée.
- Horodatages : millisecondes ou microsecondes (archives récentes), normalisés en millisecondes.
- Trous : jamais comblés. La grille complète porte des valeurs absentes là où Binance n'a rien publié.
- Période réservée : son chargement est refusé hors évaluation unique (SPEC.md, protocole préenregistré).
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
RESERVED_FROM = "2026-09-01T00:00:00Z"
RESERVED_TO = "2026-11-22T23:59:59Z"
MINUTE_MS = 60_000
MAX_BYTES = 200_000_000
COLUMNS = ["t", "o", "h", "l", "c", "v", "taker_buy"]


class ReservedPeriodError(PermissionError):
    """Lecture de la période réservée à l'évaluation unique, hors évaluation."""


class DataIntegrityError(ValueError):
    """Archive dont l'empreinte ne correspond pas à la somme de contrôle publiée."""


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


RESERVED_MS = (_ms(RESERVED_FROM), _ms(RESERVED_TO) + 999)


def month_bounds(month: str) -> tuple[int, int]:
    """Premier et dernier instant (ms) du mois « AAAA-MM », en UTC."""
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    nxt = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    return int(start.timestamp() * 1000), int(nxt.timestamp() * 1000) - 1


def months(first: str, last: str) -> list[str]:
    out, cur = [], datetime.strptime(first, "%Y-%m")
    end = datetime.strptime(last, "%Y-%m")
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = cur.replace(year=cur.year + (cur.month == 12), month=cur.month % 12 + 1)
    return out


def guard(start_ms: int, end_ms: int, *, evaluation: bool = False) -> None:
    """Refuse tout intervalle qui touche la période réservée, sauf pour l'évaluation unique."""
    if not evaluation and start_ms <= RESERVED_MS[1] and end_ms >= RESERVED_MS[0]:
        raise ReservedPeriodError(
            f"Période réservée à l'évaluation unique ({RESERVED_FROM} → {RESERVED_TO}) : chargement refusé")


def download_month(symbol: str, month: str, cache_dir, *, client: httpx.Client | None = None,
                   evaluation: bool = False) -> Path:
    """Télécharge une archive mensuelle et vérifie sa somme de contrôle publiée avant de la conserver."""
    guard(*month_bounds(month), evaluation=evaluation)
    name = f"{symbol}-1m-{month}.zip"
    path = Path(cache_dir) / symbol / name
    sidecar = path.with_name(name + ".CHECKSUM")
    if path.exists() and sidecar.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sidecar.read_text(encoding="utf-8").split()[0]:
            raise DataIntegrityError(f"{name} : archive du cache modifiée depuis sa vérification")
        return path
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        url = f"{BASE_URL}/{symbol}/1m/{name}"
        published = client.get(url + ".CHECKSUM")
        published.raise_for_status()
        expected = published.text.split()[0].strip().lower()
        archive = client.get(url)
        archive.raise_for_status()
    finally:
        if own:
            client.close()
    body = archive.content
    if len(body) > MAX_BYTES:
        raise DataIntegrityError(f"{name} : archive trop volumineuse")
    if hashlib.sha256(body).hexdigest() != expected:
        raise DataIntegrityError(f"{name} : empreinte différente de la somme de contrôle publiée")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    sidecar.write_text(published.text, encoding="utf-8")
    return path


def read_month(path) -> pd.DataFrame:
    """Lit une archive : temps d'ouverture (ms), OHLC, volume et volume acheteur au marché."""
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    d = pd.read_csv(io.BytesIO(raw), header=None, usecols=[0, 1, 2, 3, 4, 5, 9])
    if not d.empty and not np.issubdtype(d[0].dtype, np.number):  # ligne d'en-tête éventuelle
        d = d.iloc[1:].astype(float)
    d.columns = COLUMNS
    t = d["t"].astype("int64")
    d["t"] = np.where(t > 10**14, t // 1000, t)  # microsecondes -> millisecondes
    return d


def complete_grid(d: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Grille minute par minute ; les minutes sans bougie restent absentes (NaN). Renvoie aussi le nombre de trous."""
    d = d.drop_duplicates("t").sort_values("t")
    grid = np.arange(d["t"].iloc[0], d["t"].iloc[-1] + MINUTE_MS, MINUTE_MS, dtype="int64")
    full = d.set_index("t").reindex(grid)
    gaps = int((np.diff(d["t"].to_numpy()) != MINUTE_MS).sum())
    return full.rename_axis("t").reset_index(), gaps


def load_bars(symbol: str, first: str, last: str, cache_dir, *, client: httpx.Client | None = None,
              evaluation: bool = False) -> tuple[pd.DataFrame, dict]:
    """Bougies de « first » à « last » (mois inclus) sur une grille complète, avec leur rapport d'intégrité."""
    files, parts = [], []
    for month in months(first, last):
        path = download_month(symbol, month, cache_dir, client=client, evaluation=evaluation)
        files.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        parts.append(read_month(path))
    raw = pd.concat(parts, ignore_index=True)
    duplicates = int(raw.duplicated("t").sum())
    full, gaps = complete_grid(raw)
    guard(int(full["t"].iloc[0]), int(full["t"].iloc[-1]), evaluation=evaluation)
    report = {"symbol": symbol, "from": first, "to": last, "bars": int(full["o"].notna().sum()),
              "missing_minutes": int(full["o"].isna().sum()), "gaps": gaps, "duplicates_removed": duplicates,
              "files": files}
    return full, report
