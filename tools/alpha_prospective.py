"""ALPHA FACTORY v2 — observation prospective de mom_ts_28 (mission 55). Commandes à exécution finie, aucun service.

    python -m tools.alpha_prospective register --archive DOSSIER        # une seule fois, avant le 22/11/2026
    python -m tools.alpha_prospective verify                            # protocole, coûts et code intacts ?
    python -m tools.alpha_prospective fetch   --root DOSSIER            # BTCUSDT et ETHUSDT publics, jusqu'à hier (≤ 10 requêtes)
    python -m tools.alpha_prospective observe --root DOSSIER            # inscrit les décisions au journal, affiche l'état
    python -m tools.alpha_prospective status  --root DOSSIER            # état seul, sans rien écrire
    python -m tools.alpha_prospective diagnose --archive DOSSIER --output DOSSIER   # analyses descriptives (fenêtre consultée)

Aucune commande n'ouvre de compte, ne signe ni n'envoie d'ordre : lecture publique seulement (``fetch``).
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from sentinelle.alpha.lab import costs, data, diagnostics, prospective as P, registry  # noqa: E402
from tools import alpha_lab_fetch  # noqa: E402

LAB = ROOT / registry.DIRECTORY
DIR = ROOT / P.DIRECTORY
RESULTS = ROOT / "docs" / "validation" / "mesures" / "alpha-factory-v2"
MANIFEST = LAB / "archive" / "manifest.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _day_ms(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=True))


def history_basis(daily_path: Path) -> dict:
    """Écart quotidien de mom_ts_28 face à BTC+ETH à exposition égale, sur l'historique publié (pour dimensionner seulement)."""
    d = json.loads(gzip.decompress(daily_path.read_bytes()))
    c, e, b = (np.array(x) for x in (d["candidates"][P.CANDIDATE], d["exposure"][P.CANDIDATE], d["benchmarks"]["majors"]))
    rel = c - e.mean() * b
    p2 = np.array(d["days"]) >= int(np.datetime64(registry.WINDOW["split"], "D").astype(int))
    return {"mu_all": float(rel.mean()), "sigma_all": float(rel.std(ddof=1)), "mu_p2": float(rel[p2].mean()), "sigma_p2": float(rel[p2].std(ddof=1))}


def cmd_register(a) -> int:
    out = Path(a.dir)
    now = a.now_ms or _now_ms()
    if now >= _day_ms(P.FIRST_DECISION):
        print("Enregistrement refusé : il doit précéder la première décision (22/11/2026).", file=sys.stderr)
        return 2
    if (out / "protocol.json").exists():
        print("Un protocole est déjà enregistré : il est immuable.", file=sys.stderr)
        return 2
    hyp = costs.load(LAB / registry.COST_FILE)
    plan, plan_sha, problems = registry.load_plan(LAB, root=ROOT, hyp=hyp)
    if problems:
        print("Enregistrement refusé : " + " ; ".join(problems), file=sys.stderr)
        return 1
    results_sha = hashlib.sha256((RESULTS / "results.json").read_bytes()).hexdigest()
    rules, meta = data.load_rules(a.archive)
    if meta["exchange_info_sha256"] != plan["execution"]["rules"]["exchange_info_sha256"]:
        print("Enregistrement refusé : catalogue différent de celui de l'évaluation unique.", file=sys.stderr)
        return 1
    proto = P.build_protocol(root=ROOT, plan=plan, plan_sha256=plan_sha, results_sha256=results_sha, hyp=hyp, rules=rules,
                             history=history_basis(RESULTS / "daily.json.gz"),
                             registered_at=datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(), registered_at_ms=now)
    digest = P.write_protocol(out, proto)
    P.record(out, "protocol_registered", {"protocol_id": P.PROTOCOL_ID, "protocol_sha256": digest, "plan_sha256": plan_sha,
                                          "results_sha256": results_sha}, now)
    _print({"protocol_sha256": digest, "first_decision": P.FIRST_DECISION, "boundaries_z": proto["analysis"]["boundaries_z"],
            "power": proto["power"]})
    return 0


def _load(directory: Path):
    hyp = costs.load(LAB / registry.COST_FILE)
    proto, digest, problems = P.load_protocol(directory, root=ROOT, hyp=hyp)
    return proto, digest, problems, hyp


def cmd_verify(a) -> int:
    if not (Path(a.dir) / "protocol.json").exists():
        _print({"status": "NOT_REGISTERED", "intact": False})
        return 1
    proto, digest, problems, _ = _load(Path(a.dir))
    _print({"protocol_id": proto["protocol_id"], "protocol_sha256": digest, "journal": [x["kind"] for x in P.journal(Path(a.dir))],
            "problems": problems, "intact": not problems})
    return 1 if problems else 0


def fetch(root: Path, end_ms: int, get, *, max_requests: int = 10) -> dict:
    """Télécharge BTCUSDT et ETHUSDT (bougies closes avant ``end_ms``) ; écrit un instantané haché."""
    (root / "klines").mkdir(parents=True, exist_ok=True)
    budget, files, pages = [max_requests], {}, {}
    for symbol in P.SYMBOLS.values():
        rows, pg = alpha_lab_fetch.fetch_symbol(get, symbol, end_ms, budget=budget)
        payload = alpha_lab_fetch.encode_rows(rows)
        (root / "klines" / f"{symbol}.csv.gz").write_bytes(payload)
        files[symbol], pages[symbol] = hashlib.sha256(payload).hexdigest(), pg
    snap = {"kind": "alpha-prospective-snapshot", "end_ms": end_ms, "fetched_at_ms": _now_ms(), "files": files, "pages": pages,
            "endpoint": alpha_lab_fetch.ENDPOINT}
    (root / "snapshot.json").write_text(json.dumps(snap, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return snap


def cmd_fetch(a) -> int:
    import requests

    end_ms = _day_ms(datetime.now(timezone.utc).strftime("%Y-%m-%d"))  # minuit UTC : seules les bougies closes

    def get(path: str, params: dict) -> bytes:
        r = requests.get(f"{alpha_lab_fetch.ENDPOINT}/{path}", params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} sur {path} : arrêt, reprise possible plus tard")
        return r.content

    snap = fetch(Path(a.root), end_ms, get)
    _print({"files": snap["files"], "end": datetime.fromtimestamp(end_ms / 1000, timezone.utc).date().isoformat()})
    return 0


def observe(directory: Path, root: Path, *, write: bool, now_ms: int | None = None) -> tuple[int, dict]:
    proto, digest, problems, hyp = _load(directory)
    if problems:
        return 1, {"refused": problems}
    manifest = json.loads(MANIFEST.read_bytes())
    if hashlib.sha256(MANIFEST.read_bytes()).hexdigest() != proto["data"]["archive_manifest_sha256"]:
        return 1, {"refused": ["manifeste de l'archive différent de celui du protocole"]}
    bad = P.check_history(root, manifest, alpha_lab_fetch.encode_rows)
    if bad:
        return 1, {"refused": bad}
    panel, snap_sha = P.load_observation(root)
    decisions = P.weekly_decisions(panel)
    known = {}
    for line in P.journal(directory):
        for day, w in (line["body"].get("decisions") or []):
            known[day] = w
    clash = [day for day, w in decisions if day in known and known[day] != w]
    if clash:
        return 1, {"refused": [f"décision différente de celle déjà inscrite : {d}" for d in clash]}
    new = [[d, w] for d, w in decisions if d not in known]
    now = now_ms or _now_ms()
    if write and new:
        late = [d for d, _ in new if now > _day_ms(d) + 2 * P.DAY_MS]
        P.record(directory, "observation", {"protocol_sha256": digest, "snapshot_sha256": snap_sha, "last_day": panel.date(P.last_complete_day(panel)),
                                            "decisions": new, "recorded_late": late,
                                            "note": "règle déterministe et figée : une inscription tardive ne change pas la décision"}, now)
    state = P.evaluate(panel, proto, hyp)
    return 0, {"protocol_sha256": digest, "new_decisions": new, **state}


def cmd_observe(a) -> int:
    code, out = observe(Path(a.dir), Path(a.root), write=True)
    _print(out)
    return code


def cmd_status(a) -> int:
    code, out = observe(Path(a.dir), Path(a.root), write=False)
    _print(out)
    return code


def cmd_diagnose(a) -> int:
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    rep = diagnostics.run(data.load_archive(a.archive), data.load_rules(a.archive)[0], costs.load(LAB / registry.COST_FILE),
                          json.loads((RESULTS / "results.json").read_text(encoding="utf-8")))
    (out / "diagnostic.json").write_text(json.dumps(rep, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (out / "diagnostic.md").write_text(diagnostics.markdown(rep) + "\n", encoding="utf-8")
    print(diagnostics.markdown(rep))
    return 0


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--archive", type=Path, required=True)
    r.add_argument("--dir", type=Path, default=DIR)
    r.add_argument("--now-ms", type=int, default=None, help=argparse.SUPPRESS)
    v = sub.add_parser("verify")
    v.add_argument("--dir", type=Path, default=DIR)
    f = sub.add_parser("fetch")
    f.add_argument("--root", type=Path, required=True)
    for name in ("observe", "status"):
        o = sub.add_parser(name)
        o.add_argument("--root", type=Path, required=True)
        o.add_argument("--dir", type=Path, default=DIR)
    d = sub.add_parser("diagnose")
    d.add_argument("--archive", type=Path, required=True)
    d.add_argument("--output", type=Path, required=True)
    a = cli.parse_args(argv)
    return {"register": cmd_register, "verify": cmd_verify, "fetch": cmd_fetch, "observe": cmd_observe, "status": cmd_status,
            "diagnose": cmd_diagnose}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
