"""ALPHA FACTORY v2 — téléchargement borné des bougies journalières publiques (paires USDT au comptant).

Outil explicite, à exécution finie, sans compte ni clé : uniquement des lectures publiques
(``exchangeInfo`` et ``klines``). Il n'est importé ni par le laboratoire ni par les tests : la
recherche lit ensuite l'archive hors ligne, en lecture seule, avec vérification des empreintes.

    python -m tools.alpha_lab_fetch --root DOSSIER --end 2026-11-13 [--max-requests 6000]

Pourquoi toutes les paires, y compris celles retirées de la cote : un univers construit avec les
seules cryptos encore vivantes aujourd'hui embellit le passé (biais du survivant, déjà signalé dans
docs/RESULTATS.md). Le catalogue public liste aussi les paires retirées ; leur historique reste servi.

Reprise : un actif déjà archivé n'est jamais retéléchargé. Chaque réponse brute est hachée
(SHA-256) dans le manifeste ; seules les colonnes utiles sont conservées, en CSV compressé déterministe.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://data-api.binance.vision/api/v3"
DAY_MS = 86_400_000
COLUMNS = "ts,open,high,low,close,volume,quote_volume,trades"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode_rows(rows: list[list]) -> bytes:
    """CSV compressé, octet pour octet reproductible (aucune date dans l'en-tête gzip)."""
    text = COLUMNS + "\n" + "".join(",".join(str(x) for x in r) + "\n" for r in rows)
    return gzip.compress(text.encode("utf-8"), compresslevel=6, mtime=0)


def normalize(batch: list, end_ms: int) -> list[list]:
    """Garde les bougies closes avant ``end_ms`` ; refuse les réponses mal formées."""
    out = []
    for r in batch:
        ts = int(r[0])
        if ts % DAY_MS or ts + DAY_MS > end_ms:
            continue
        out.append([ts, r[1], r[2], r[3], r[4], r[5], r[7], int(r[8])])
    return out


def fetch_symbol(get, symbol: str, end_ms: int, *, budget: list[int]) -> tuple[list[list], list[dict]]:
    rows, pages, cursor = [], [], 0
    while cursor < end_ms:
        if budget[0] <= 0:
            raise RuntimeError("Budget de requêtes épuisé : relancez pour reprendre")
        budget[0] -= 1
        raw = get("klines", {"symbol": symbol, "interval": "1d", "startTime": cursor, "endTime": end_ms - 1, "limit": 1000})
        batch = json.loads(raw)
        if not isinstance(batch, list):
            raise ValueError(f"Réponse inattendue pour {symbol}")
        pages.append({"start_ms": cursor, "sha256": digest(raw), "bars": len(batch)})
        if not batch:
            break
        part = normalize(batch, end_ms)
        if part and rows and part[0][0] <= rows[-1][0]:
            raise ValueError(f"Pagination non croissante pour {symbol}")
        rows += part
        cursor = int(batch[-1][0]) + DAY_MS
        if len(batch) < 1000:
            break
    return rows, pages


def run(root: Path, end_ms: int, get, *, max_requests: int, pause: float = 0.0, only: list[str] | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / "klines").mkdir(exist_ok=True)
    info_path = root / "exchangeInfo.json"
    if not info_path.exists():
        info_path.write_bytes(get("exchangeInfo", {}))
    info = json.loads(info_path.read_bytes())
    symbols = sorted((s["symbol"], s["baseAsset"], s["status"]) for s in info["symbols"] if s["quoteAsset"] == "USDT")
    if only:
        symbols = [s for s in symbols if s[0] in only]
    state_path = root / "progress.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    budget = [max_requests]
    for symbol, base, status in symbols:
        if symbol in state:
            continue
        rows, pages = fetch_symbol(get, symbol, end_ms, budget=budget)
        payload = encode_rows(rows)
        (root / "klines" / f"{symbol}.csv.gz").write_bytes(payload)
        state[symbol] = {"base": base, "status": status, "bars": len(rows), "first_ms": rows[0][0] if rows else None,
                         "last_ms": rows[-1][0] if rows else None, "sha256": digest(payload), "pages": pages}
        state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        if pause:
            time.sleep(pause)
    manifest = {"kind": "alpha-lab-daily-archive", "schema": 1, "endpoint": ENDPOINT, "quote": "USDT", "interval": "1d",
                "end_ms": end_ms, "exchange_info_sha256": digest(info_path.read_bytes()), "columns": COLUMNS.split(","),
                "symbols": {s: state[s] for s, _, _ in symbols if s in state}, "complete": all(s in state for s, _, _ in symbols)}
    payload = (json.dumps(manifest, sort_keys=True, indent=1) + "\n").encode("utf-8")
    (root / "manifest.json").write_bytes(payload)
    (root / "manifest.sha256").write_text(digest(payload) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    cli.add_argument("--root", type=Path, required=True)
    cli.add_argument("--end", required=True, help="date UTC exclue (AAAA-MM-JJ), déjà passée")
    cli.add_argument("--max-requests", type=int, default=6000)
    cli.add_argument("--pause", type=float, default=0.05)
    a = cli.parse_args(argv)
    end_ms = int(datetime.strptime(a.end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    if end_ms > int(time.time() * 1000):
        print("--end doit être passée : aucune bougie non close n'est archivée.", file=sys.stderr)
        return 2
    import requests

    session = [requests.Session()]

    def get(path: str, params: dict) -> bytes:
        """Au plus 4 tentatives espacées (coupure passagère du réseau), puis arrêt : la reprise est toujours possible."""
        last = "inatteignable"
        for attempt in range(4):
            try:
                response = session[0].get(f"{ENDPOINT}/{path}", params=params, timeout=30)
            except requests.RequestException as exc:
                last = type(exc).__name__
                session[0] = requests.Session()
            else:
                if response.status_code == 200:
                    return response.content
                last = f"HTTP {response.status_code}"
                if response.status_code in (418, 429):  # limite de débit : on s'arrête, on ne force jamais
                    break
            time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"{last} sur {path} : arrêt, reprise possible plus tard")

    manifest = run(a.root, end_ms, get, max_requests=a.max_requests, pause=a.pause)
    print(json.dumps({"symbols": len(manifest["symbols"]), "complete": manifest["complete"],
                      "bars": sum(v["bars"] for v in manifest["symbols"].values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
