"""MEMECOIN ALPHA ENGINE (mission 56) — commandes à exécution finie ; aucune transaction, aucun service permanent.

    python -m tools.memecoin collect --out DOSSIER [--pages 5] [--security 60] [--budget 900]   # collecte publique bornée
    python -m tools.memecoin follow  --capture FICHIER --out DOSSIER                          # seconde lecture (issue)
    python -m tools.memecoin lab     --capture FICHIER… [--follow FICHIER…] --output DOSSIER  # veto, sélection, coûts, stress
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentinelle.dmh.net import PublicClient  # noqa: E402
from sentinelle.memecoin import collect as C  # noqa: E402


def cmd_collect(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client = PublicClient(budget=a.budget, timeout=15.0)
    records = C.collect(client, pages=a.pages, max_security=a.security)
    path = out / f"capture-{int(time.time())}.jsonl.gz"
    digest = C.write_capture(path, records)
    summary = records[-1]
    print(json.dumps({"capture": str(path), "sha256": digest, "pools": summary["pools"], "requests": summary["requests"],
                      "sources": summary["sources"]}, ensure_ascii=False, indent=1))
    return 0


def cmd_follow(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    prev = C.read_capture(Path(a.capture))
    client = PublicClient(budget=a.budget, timeout=15.0)
    records = C.follow(client, prev)
    path = out / (Path(a.capture).name.replace("capture-", "follow-").split(".")[0] + f"-{int(time.time())}.jsonl.gz")
    digest = C.write_capture(path, records)
    print(json.dumps({"follow": str(path), "sha256": digest, "pools": sum(1 for r in records if r["kind"] == "ohlcv")}, ensure_ascii=False))
    return 0


def cmd_lab(a) -> int:
    from sentinelle.memecoin import lab

    capture = [r for path in a.capture for r in C.read_capture(Path(path))]  # plusieurs collectes : enregistrements réunis
    follow = [r for path in (a.follow or []) for r in C.read_capture(Path(path))] or None
    rep = lab.run(capture, follow)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "lab.json").write_text(json.dumps(rep, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (out / "lab.md").write_text(lab.markdown(rep) + "\n", encoding="utf-8")
    print(lab.markdown(rep))
    return 0


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    c.add_argument("--pages", type=int, default=5)
    c.add_argument("--security", type=int, default=60)
    c.add_argument("--budget", type=int, default=900)
    f = sub.add_parser("follow")
    f.add_argument("--capture", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--budget", type=int, default=300)
    lb = sub.add_parser("lab")
    lb.add_argument("--capture", required=True, nargs="+")
    lb.add_argument("--follow", nargs="*")
    lb.add_argument("--output", required=True)
    a = cli.parse_args(argv)
    return {"collect": cmd_collect, "follow": cmd_follow, "lab": cmd_lab}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
