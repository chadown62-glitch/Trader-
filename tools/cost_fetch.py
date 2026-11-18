"""COST OPTIMIZER — collecte publique BORNÉE des preuves de coûts (lecture seule, aucun compte, aucune clé).

    python -m tools.cost_fetch filters --root DOSSIER --symbols BTCEUR BTCUSDT ...
    python -m tools.cost_fetch fees    --root DOSSIER
    python -m tools.cost_fetch probe   --root DOSSIER --symbols ... --rounds 12 --pause 20
    python -m tools.cost_fetch samples --root DOSSIER --plan PLAN.json [--max-requests 8000]

Uniquement des requêtes GET publiques, vers une liste fermée de chemins. Chaque réponse brute est
écrite telle quelle (fichier immuable nommé par son empreinte SHA-256) et inscrite dans un index avec
son heure de réception. La collecte s'arrête au budget fixé ; une relance reprend sans refaire ce qui
est déjà archivé. Cet outil n'est importé par aucun module de calcul : le moteur lit l'archive hors ligne.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from pathlib import Path

API = "https://data-api.binance.vision/api/v3"
ALLOWED = frozenset({"exchangeInfo", "depth", "aggTrades", "time"})  # interface publique (filtres, sondes)
FEE_PAGES = ("https://www.binance.com/en/fee/schedule",)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Archive:
    """Dossier en ajout seul : ``raw/<empreinte>`` + ``index.jsonl``."""

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)

    def put(self, kind: str, meta: dict, raw: bytes, received_ms: int, status: int) -> str:
        sha = digest(raw)
        path = self.root / "raw" / sha
        if not path.exists():
            path.write_bytes(raw)
        line = {"kind": kind, "received_ms": received_ms, "status": status, "sha256": sha, "bytes": len(raw), **meta}
        with (self.root / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n")
        return sha

    def index(self) -> list[dict]:
        p = self.root / "index.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


class Client:
    def __init__(self, get, *, budget: int, pause: float = 0.05, clock=time.time):
        self._get, self.budget, self.pause, self.clock, self.calls = get, budget, pause, clock, 0

    def api(self, path: str, params: dict) -> tuple[bytes, int, int]:
        if path not in ALLOWED:
            raise ValueError("Chemin public non autorisé")
        if self.calls >= self.budget:
            raise RuntimeError("Budget de requêtes épuisé : relancez pour reprendre")
        self.calls += 1
        raw, status = self._get(f"{API}/{path}", params)
        received = int(self.clock() * 1000)
        if self.pause:
            time.sleep(self.pause)
        return raw, status, received


def fetch_filters(archive: Archive, client: Client, symbols: list[str]) -> str:
    raw, status, received = client.api("exchangeInfo", {"symbols": json.dumps(sorted(symbols), separators=(",", ":"))})
    if status != 200:
        raise RuntimeError(f"exchangeInfo : HTTP {status}")
    return archive.put("exchangeInfo", {"symbols": sorted(symbols)}, raw, received, status)


def fetch_fee_pages(archive: Archive, get, clock=time.time) -> list[dict]:
    out = []
    for url in FEE_PAGES:
        try:
            raw, status = get(url, {})
        except Exception as exc:  # noqa: BLE001 - page inaccessible : on le consigne, on n'invente rien
            out.append({"url": url, "status": None, "error": type(exc).__name__})
            continue
        sha = archive.put("fee_page", {"url": url}, raw, int(clock() * 1000), status)
        out.append({"url": url, "status": status, "sha256": sha})
    return out


def probe(archive: Archive, client: Client, symbols: list[str], *, rounds: int, pause: float) -> int:
    """Carnet (100 niveaux) puis transactions de la dernière minute, pour chaque paire, ``rounds`` fois."""
    n = 0
    for r in range(rounds):
        for sym in symbols:
            raw, status, received = client.api("depth", {"symbol": sym, "limit": 100})
            archive.put("depth", {"symbol": sym, "round": r}, raw, received, status)
            end = received
            raw2, status2, received2 = client.api("aggTrades", {"symbol": sym, "startTime": end - 120_000, "endTime": end, "limit": 1000})
            archive.put("aggTrades_probe", {"symbol": sym, "round": r, "start_ms": end - 120_000, "end_ms": end}, raw2, received2, status2)
            n += 2
        if r + 1 < rounds:
            time.sleep(pause)
    return n


def fetch_window(client: Client, symbol: str, start: int, end: int) -> tuple[list[dict], list[dict]]:
    """Toutes les transactions agrégées de ``[start, end)``, page par page ; renvoie (lignes, pages)."""
    rows, pages, cursor, from_id = [], [], start, None
    while True:
        params = {"symbol": symbol, "limit": 1000}
        if from_id is None:
            params.update(startTime=cursor, endTime=end - 1)
        else:
            params["fromId"] = from_id
        raw, status, received = client.api("aggTrades", params)
        pages.append({"params": params, "status": status, "sha256": digest(raw), "received_ms": received})
        if status != 200:
            return rows, pages
        batch = json.loads(raw)
        if not isinstance(batch, list):
            return rows, pages
        batch = [b for b in batch if int(b["T"]) < end]
        rows += batch
        if len(batch) < 1000:
            return rows, pages
        last = batch[-1]
        if from_id is None and int(last["T"]) == cursor:
            from_id = int(last["a"]) + 1  # 1000 transactions dans la même milliseconde : pagination par identifiant
        elif from_id is not None:
            from_id = int(last["a"]) + 1
        else:
            cursor = int(last["T"])


def fetch_samples(root: Path, client: Client, plan: dict) -> dict:
    """Une archive par échantillon du plan (ordre du plan, déjà mélangé) ; reprise sans doublon."""
    out_dir = Path(root) / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    for s in plan["samples"]:
        path = out_dir / f"{s['id']}.json.gz"
        if path.exists():
            done += 1
            continue
        rows, pages = fetch_window(client, s["symbol"], s["start_ms"], s["end_ms"])
        ok = all(p["status"] == 200 for p in pages)
        body = {"sample": s, "rows": rows, "pages": pages, "complete": ok}
        payload = gzip.compress(json.dumps(body, sort_keys=True).encode("utf-8"), mtime=0)
        path.write_bytes(payload)
        done += ok
        failed += not ok
    return {"samples": len(plan["samples"]), "archived": done, "failed": failed, "requests": client.calls}


BULK = "https://data.binance.vision/data/spot/daily/aggTrades"


def to_ms(ts: int) -> int:
    """Les fichiers récents de l'archive sont horodatés en microsecondes, les anciens en millisecondes."""
    if ts > 10**14:
        return ts // 1000
    return ts


def extract_windows(zip_bytes: bytes, windows: list[tuple[str, int, int]]) -> dict[str, list]:
    """Lignes ``[instant_ms, prix, quantité, acheteur_teneur, identifiant]`` de chaque fenêtre ``[début, fin)``."""
    import io
    import zipfile

    out = {w[0]: [] for w in windows}
    if not windows:
        return out
    last_end = max(w[2] for w in windows)
    first_start = min(w[1] for w in windows)
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    with z.open(z.namelist()[0]) as fh:
        for raw in fh:
            f = raw.split(b",")
            if not f[0].isdigit():
                continue  # en-tête éventuel
            ts = to_ms(int(f[5]))
            if ts < first_start:
                continue
            if ts >= last_end:
                break
            for wid, lo, hi in windows:
                if lo <= ts < hi:
                    out[wid].append([ts, f[1].decode(), f[2].decode(), f[6].strip().lower() == b"true", int(f[0])])
    return out


def fetch_bulk(root: Path, plan: dict, get, *, max_files: int, max_bytes: int) -> dict:
    """Fichiers journaliers officiels : téléchargés, vérifiés par leur somme publiée, fenêtres extraites, fichier supprimé."""
    out_dir = Path(root) / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = Archive(root)
    by_file: dict[tuple[str, str], list[dict]] = {}
    for s in plan["samples"]:
        by_file.setdefault((s["symbol"], s["day"]), []).append(s)
    files = bytes_ = done = failed = 0
    order = []  # l'ordre du plan (déjà mélangé) : une collecte interrompue reste représentative
    for s in plan["samples"]:
        key = (s["symbol"], s["day"])
        if key not in order:
            order.append(key)
    for symbol, day in order:
        group = by_file[(symbol, day)]
        if all((out_dir / f"{s['id']}.json.gz").exists() for s in group):
            done += 1
            continue
        if files >= max_files or bytes_ >= max_bytes:
            break
        url = f"{BULK}/{symbol}/{symbol}-aggTrades-{day}.zip"
        files += 1
        try:
            payload, status = get(url, {})
            check, cstatus = get(url + ".CHECKSUM", {})
        except RuntimeError as exc:
            archive.put("bulk_error", {"url": url, "error": str(exc)[:200]}, b"", int(time.time() * 1000), 0)
            failed += 1
            continue
        sha = digest(payload)
        official = check.decode("utf-8", "replace").split()[0] if cstatus == 200 and check else None
        ok = status == 200 and official == sha
        bytes_ += len(payload)
        rows = extract_windows(payload, [(s["id"], s["start_ms"], s["end_ms"]) for s in group]) if ok else {s["id"]: [] for s in group}
        for s in group:
            body = {"sample": s, "source": url, "zip_sha256": sha, "official_sha256": official, "checksum_ok": ok, "http_status": status,
                    "rows": rows[s["id"]], "columns": ["ts_ms", "price", "qty", "buyer_is_maker", "agg_id"]}
            (out_dir / f"{s['id']}.json.gz").write_bytes(gzip.compress(json.dumps(body, sort_keys=True).encode("utf-8"), mtime=0))
        done += ok
        failed += not ok
    return {"files_planned": len(order), "files_done": done, "files_failed": failed, "downloaded_mb": round(bytes_ / 1e6, 1)}


def _requests_get():
    import requests

    session = [requests.Session()]

    def get(url: str, params: dict) -> tuple[bytes, int]:
        last = None
        for attempt in range(4):
            try:
                r = session[0].get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                last = exc
                session[0] = requests.Session()
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code in (418, 429):  # limite de débit : on s'arrête, on ne force jamais
                raise RuntimeError(f"HTTP {r.status_code} : limite de débit, reprise plus tard")
            return r.content, r.status_code
        raise RuntimeError(f"Réseau indisponible : {type(last).__name__}")
    return get


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    for name in ("filters", "fees", "probe", "samples", "bulk"):
        p = sub.add_parser(name)
        p.add_argument("--root", type=Path, required=True)
        p.add_argument("--max-requests", type=int, default=8000)
        p.add_argument("--pause", type=float, default=0.05)
        if name in ("filters", "probe"):
            p.add_argument("--symbols", nargs="+", required=True)
        if name == "probe":
            p.add_argument("--rounds", type=int, default=12)
            p.add_argument("--round-pause", type=float, default=20.0)
        if name in ("samples", "bulk"):
            p.add_argument("--plan", type=Path, required=True)
        if name == "bulk":
            p.add_argument("--max-files", type=int, default=2000)
            p.add_argument("--max-gb", type=float, default=8.0)
    a = cli.parse_args(argv)
    get = _requests_get()
    archive = Archive(a.root)
    client = Client(get, budget=a.max_requests, pause=a.pause)
    if a.cmd == "filters":
        print(json.dumps({"exchangeInfo_sha256": fetch_filters(archive, client, a.symbols)}))
    elif a.cmd == "fees":
        print(json.dumps(fetch_fee_pages(archive, get), ensure_ascii=False))
    elif a.cmd == "probe":
        print(json.dumps({"requests": probe(archive, client, a.symbols, rounds=a.rounds, pause=a.round_pause)}))
    elif a.cmd == "bulk":
        plan = json.loads(a.plan.read_text(encoding="utf-8"))
        print(json.dumps(fetch_bulk(a.root, plan, get, max_files=a.max_files, max_bytes=int(a.max_gb * 1e9))))
    else:
        plan = json.loads(a.plan.read_text(encoding="utf-8"))
        print(json.dumps(fetch_samples(a.root, client, plan)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
