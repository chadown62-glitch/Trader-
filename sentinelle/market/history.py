"""Historique des prix : téléchargement incrémental, cache disque, contrôles.

Les bougies sont stockées en CSV compressé dans data/history/<source>/.
Seules les bougies **clôturées** sont conservées (jamais la bougie en cours).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from ..config import timeframe_ms
from .sources import MarketSource

log = logging.getLogger("sentinelle.history")

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def to_frame(rows: list[list[float]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype="int64" if c == "ts" else "float64") for c in COLUMNS})
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["ts"] = df["ts"].astype("int64")
    for c in COLUMNS[1:]:
        df[c] = df[c].astype("float64")
    return df


def clean_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Trie, dédoublonne et corrige les incohérences. Renvoie (df, rapport)."""
    report = {"duplicates": 0, "fixed_hilo": 0, "dropped_invalid": 0}
    if df.empty:
        return df, report
    n0 = len(df)
    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts").reset_index(drop=True)
    report["duplicates"] = n0 - len(df)
    prices = df[["open", "high", "low", "close"]]
    valid = np.isfinite(prices.to_numpy()).all(axis=1) & (prices.to_numpy() > 0).all(axis=1)
    if (~valid).any():
        report["dropped_invalid"] = int((~valid).sum())
        df = df[valid].reset_index(drop=True)
    hi = df[["open", "close", "high"]].max(axis=1)
    lo = df[["open", "close", "low"]].min(axis=1)
    bad = (hi != df["high"]) | (lo != df["low"])
    if bad.any():
        report["fixed_hilo"] = int(bad.sum())
        df["high"] = hi
        df["low"] = lo
    df["volume"] = df["volume"].fillna(0.0).clip(lower=0.0)
    return df, report


def count_gaps(df: pd.DataFrame, timeframe: str) -> int:
    if len(df) < 2:
        return 0
    step = timeframe_ms(timeframe)
    diffs = np.diff(df["ts"].to_numpy())
    return int((diffs > step).sum())


class HistoryStore:
    def __init__(self, root: Path, source: MarketSource):
        self.root = Path(root)
        self.source = source
        self._lock = threading.RLock()  # plusieurs threads peuvent mettre à jour l'historique

    def path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "-")
        return self.root / self.source.name / f"{safe}_{timeframe}.csv.gz"

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        p = self.path(symbol, timeframe)
        if not p.exists():
            return to_frame([])
        try:
            df = pd.read_csv(p, compression="gzip")
            df["ts"] = df["ts"].astype("int64")
            return clean_frame(df[COLUMNS])[0]
        except Exception:  # fichier corrompu : on repart de zéro
            log.exception("Cache d'historique illisible, il sera reconstruit : %s", p)
            return to_frame([])

    def save(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        p = self.path(symbol, timeframe)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        df.to_csv(tmp, index=False, compression="gzip")
        tmp.replace(p)  # écriture atomique

    def _fetch_range(self, symbol, timeframe, start_ms, end_ms, progress=None) -> list[list[float]]:
        step = timeframe_ms(timeframe)
        out: list[list[float]] = []
        cursor = start_ms
        guard = 0
        while cursor < end_ms and guard < 10_000:
            guard += 1
            batch = self.source.fetch_ohlcv(symbol, timeframe, since=int(cursor), limit=1000)
            if not batch:
                break
            batch = [r for r in batch if r[0] >= cursor]
            if not batch:
                break
            out.extend(batch)
            nxt = int(batch[-1][0]) + step
            if nxt <= cursor:
                break
            cursor = nxt
            if progress:
                progress(min(1.0, (cursor - start_ms) / max(1, end_ms - start_ms)))
            if len(batch) < 2 and cursor >= end_ms - step:
                break
        return out

    def update(
        self,
        symbol: str,
        timeframe: str,
        days: int,
        progress: Callable[[float], None] | None = None,
    ) -> pd.DataFrame:
        """Complète le cache pour couvrir `days` jours jusqu'à maintenant."""
        with self._lock:
            return self._update(symbol, timeframe, days, progress)

    def _update(self, symbol, timeframe, days, progress) -> pd.DataFrame:
        step = timeframe_ms(timeframe)
        now = self.source.now_ms()
        start = now - days * 86_400_000
        start -= start % step
        df = self.load(symbol, timeframe)
        new_rows: list[list[float]] = []
        earliest = self._earliest(symbol, timeframe)
        if df.empty:
            new_rows += self._fetch_range(symbol, timeframe, start, now, progress)
            if new_rows:
                self._set_earliest(symbol, timeframe, start, int(new_rows[0][0]))
        else:
            first, last = int(df["ts"].iloc[0]), int(df["ts"].iloc[-1])
            if first > start + step and not (earliest and earliest["first"] <= first and earliest["asked"] <= start):
                older = self._fetch_range(symbol, timeframe, start, first, None)
                new_rows += older
                self._set_earliest(symbol, timeframe, start, int(older[0][0]) if older else first)
            new_rows += self._fetch_range(symbol, timeframe, last + step, now, progress)
        if new_rows:
            df = pd.concat([df, to_frame(new_rows)], ignore_index=True)
        df, report = clean_frame(df)
        # on ne garde que les bougies clôturées
        df = df[df["ts"] + step <= now].reset_index(drop=True)
        if new_rows:
            self.save(symbol, timeframe, df)
        if any(report.values()):
            log.info("Historique %s %s nettoyé : %s", symbol, timeframe, report)
        cut = df[df["ts"] >= start].reset_index(drop=True)
        return cut

    # -- mémoire de la première cotation disponible ------------------------------
    def _meta_path(self) -> Path:
        return self.root / self.source.name / "premieres-cotations.json"

    def _earliest(self, symbol: str, timeframe: str) -> dict | None:
        import json

        p = self._meta_path()
        try:
            return json.loads(p.read_text(encoding="utf-8")).get(f"{symbol}|{timeframe}") if p.exists() else None
        except (OSError, ValueError):
            return None

    def _set_earliest(self, symbol: str, timeframe: str, asked: int, first: int) -> None:
        import json

        p = self._meta_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            data = {}
        prev = data.get(f"{symbol}|{timeframe}") or {}
        data[f"{symbol}|{timeframe}"] = {"asked": min(asked, prev.get("asked", asked)), "first": first}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")

    def info(self, symbol: str, timeframe: str) -> dict:
        df = self.load(symbol, timeframe)
        if df.empty:
            return {"symbol": symbol, "timeframe": timeframe, "bars": 0}
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars": len(df),
            "first": int(df["ts"].iloc[0]),
            "last": int(df["ts"].iloc[-1]),
            "gaps": count_gaps(df, timeframe),
            "updated": time.time(),
        }


def splice(older: pd.DataFrame, newer: pd.DataFrame) -> pd.DataFrame:
    """Raccorde un historique plus ancien (ex. BTC/USDT) devant un historique récent (ex. BTC/USDC).

    Seule la partie antérieure au début de `newer` est conservée ; les prix sont
    ajustés par le rapport observé au point de raccordement (écart USDT/USDC ≈ 0).
    """
    if older is None or older.empty:
        return newer
    if newer is None or newer.empty:
        return older
    first = int(newer["ts"].iloc[0])
    before = older[older["ts"] < first].copy()
    if before.empty:
        return newer
    overlap = older[older["ts"] == first]
    ratio = float(newer["close"].iloc[0] / overlap["close"].iloc[0]) if len(overlap) else 1.0
    if not (0.9 < ratio < 1.1):
        ratio = 1.0
    for col in ("open", "high", "low", "close"):
        before[col] = before[col] * ratio
    return pd.concat([before, newer], ignore_index=True)
