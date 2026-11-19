"""COST OPTIMIZER — commande hors ligne (exécution finie, aucun ordre, aucune connexion).

    python -m tools.cost_optimizer audit
    python -m tools.cost_optimizer register --archive ARCHIVE_V2          # plan de mesure, une seule fois, avant la collecte
    python -m tools.cost_optimizer verify
    python -m tools.cost_optimizer measure  --data DOSSIER --archive ARCHIVE_V2 --out DOSSIER
    python -m tools.cost_optimizer contract --measures DOSSIER            # contrat cost_contract_v1, une seule fois
    python -m tools.cost_optimizer report   --measures DOSSIER

La collecte publique est faite à part par ``tools.cost_fetch`` (bornée). ``ARCHIVE_V2`` est l'archive
journalière d'ALPHA FACTORY v2 (liquidité datée, clôtures) : aucun résultat de stratégie n'y figure.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentinelle.alpha.shadow.contract import sha256  # noqa: E402
from sentinelle.alpha.shadow.protocol import journal_append, read_journal  # noqa: E402
from sentinelle.costs import audit, benefit, contract, measure, plan, venues  # noqa: E402

DIR = ROOT / "research" / "cost-optimizer-v1"


def _now() -> tuple[int, str]:
    ms = int(time.time() * 1000)
    return ms, datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str) + "\n", encoding="utf-8")


def cmd_audit(a) -> int:
    print(json.dumps(audit.findings(), indent=1, ensure_ascii=False))
    return 0


def cmd_register(a) -> int:
    d = Path(a.dir)
    if (d / "measurement-plan.json").exists():
        print("Un plan de mesure est déjà enregistré : il est immuable.", file=sys.stderr)
        return 2
    ms, iso = _now()
    p = plan.build(a.archive, registered_at=iso, root=ROOT)
    digest = plan.write(d, p)
    journal_append(d / "journal.jsonl", "measurement_plan_registered", {"plan_id": p["plan_id"], "plan_sha256": digest,
                                                                      "samples": len(p["samples"]), "files": len(p["files"])}, ms)
    print(json.dumps({"plan_sha256": digest, "samples": len(p["samples"]), "files": len(p["files"])}, ensure_ascii=False))
    return 0


def cmd_verify(a) -> int:
    d = Path(a.dir)
    try:
        p, digest = plan.load(d)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"intact": False, "problem": str(exc)}, ensure_ascii=False))
        return 1
    drift = plan.code_drift(p, ROOT)
    print(json.dumps({"intact": True, "plan_id": p["plan_id"], "plan_sha256": digest, "journal": [x["kind"] for x in read_journal(d / "journal.jsonl")],
                      "code_changed_since_registration": drift, "contract": (d / "cost-contract-v1.json").exists()}, ensure_ascii=False, indent=1))
    return 0


def load_filters(data: Path, archive: Path) -> tuple[dict, str, str]:
    """Filtres observés : catalogue complet de l'archive v2 (14/11/2026), complété et remplacé par la collecte du jour."""
    filters = venues.parse_exchange_info((Path(archive) / "exchangeInfo.json").read_bytes(), as_of="2026-11-14")
    idx = [json.loads(x) for x in (Path(data) / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    latest = [r for r in idx if r["kind"] == "exchangeInfo" and r["status"] == 200]
    sha, as_of = sha256((Path(archive) / "exchangeInfo.json").read_bytes()), "2026-11-14"
    if latest:
        r = latest[-1]
        as_of = datetime.fromtimestamp(r["received_ms"] / 1000, timezone.utc).strftime("%Y-%m-%d")
        filters.update(venues.parse_exchange_info((Path(data) / "raw" / r["sha256"]).read_bytes(), as_of=as_of))
        sha = r["sha256"]
    return filters, sha, as_of


def load_fees(data: Path) -> venues.FeeSchedule:
    idx = [json.loads(x) for x in (Path(data) / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    pages = [r for r in idx if r["kind"] == "fee_page" and r["status"] == 200 and r["bytes"] > 0]
    if pages:
        r = pages[-1]
        return venues.binance_spot_vip0(page_sha256=r["sha256"], as_of=datetime.fromtimestamp(r["received_ms"] / 1000, timezone.utc).strftime("%Y-%m-%d"))
    return venues.binance_spot_vip0()


def cmd_measure(a) -> int:
    p, digest = plan.load(Path(a.dir))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "measures.json").exists():
        print("Des mesures existent déjà dans ce dossier : utilisez un nouveau dossier.", file=sys.stderr)
        return 2
    t0 = time.time()
    filters, filters_sha, filters_as_of = load_filters(a.data, a.archive)
    fees = load_fees(a.data)
    tapes, status = measure.load_samples(a.data, p)
    rows = measure.evaluate(p, tapes, filters, fees)
    summary = measure.summarize(p, rows, fees)
    result = {"kind": "cost-measures", "plan_id": p["plan_id"], "plan_sha256": digest, "collection": status,
              "fees": {"taker": fees.taker.to_dict(), "maker": fees.maker.to_dict()}, "filters": {"sha256": filters_sha, "as_of": filters_as_of},
              "coverage": measure.quote_coverage(rows), "summary": summary, "probes": measure.probes(a.data, filters),
              "stress": measure.stress_states(p, rows, a.archive), "routing": measure.routing(p, rows),
              "bar_touch": measure.bar_touch_optimism(p, rows, a.archive), "inventory": measure.inventory_effects(rows, filters, fees),
              "rows": len(rows), "duration_s": round(time.time() - t0, 1)}
    _dump(out / "measures.json", result)
    (out / "rows.jsonl.gz").write_bytes(gzip.compress("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode("utf-8"), mtime=0))
    ms, _ = _now()
    journal_append(Path(a.dir) / "journal.jsonl", "measured", {"plan_sha256": digest, "measures_sha256": sha256(result), "rows": len(rows),
                                                              "collection": status}, ms)
    print(json.dumps({"rows": len(rows), "collection": status, "duration_s": result["duration_s"],
                      "confirmatory": (summary.get("confirmatory") or {}).get("demonstrated")}, ensure_ascii=False))
    return 0


def cmd_contract(a) -> int:
    d = Path(a.dir)
    p, digest = plan.load(d)
    m = json.loads((Path(a.measures) / "measures.json").read_text(encoding="utf-8"))
    if m["plan_sha256"] != digest:
        print("Les mesures ne viennent pas du plan enregistré.", file=sys.stderr)
        return 1
    fees = venues.binance_spot_vip0() if m["fees"]["taker"]["level"] == "HYPOTHETICAL" else venues.binance_spot_vip0(
        page_sha256=m["fees"]["taker"]["sha256"], as_of=m["fees"]["taker"]["as_of"])
    ms, iso = _now()
    c = contract.build(plan=p, plan_sha256=digest, summary=m["summary"], probes=m["probes"], routing=m["routing"], stress=m["stress"],
                       inventory=m["inventory"], bar_touch=m["bar_touch"], fees=fees, filters_sha256=m["filters"]["sha256"],
                       filters_as_of=m["filters"]["as_of"], coverage=m["coverage"], created_at=iso)
    c["measures_sha256"] = sha256(m)
    export = contract.to_alpha_lab(c)
    hashes = contract.write(d, c, export)
    journal_append(d / "journal.jsonl", "contract_frozen", {"plan_sha256": digest, **hashes}, ms)
    print(json.dumps(hashes, ensure_ascii=False, indent=1))
    return 0


def cmd_report(a) -> int:
    from tools.cost_report import markdown

    m = json.loads((Path(a.measures) / "measures.json").read_text(encoding="utf-8"))
    c_path = Path(a.dir) / "cost-contract-v1.json"
    c = json.loads(c_path.read_text(encoding="utf-8")) if c_path.exists() else None
    rows = [json.loads(x) for x in gzip.decompress((Path(a.measures) / "rows.jsonl.gz").read_bytes()).decode("utf-8").splitlines()]
    print(markdown(m, c, rows, benefit))
    return 0


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    sub.add_parser("audit")
    for name in ("register", "verify", "measure", "contract", "report"):
        p = sub.add_parser(name)
        p.add_argument("--dir", type=Path, default=DIR)
        if name in ("register", "measure"):
            p.add_argument("--archive", type=Path, required=True)
        if name == "measure":
            p.add_argument("--data", type=Path, required=True)
            p.add_argument("--out", type=Path, required=True)
        if name in ("contract", "report"):
            p.add_argument("--measures", type=Path, required=True)
    a = cli.parse_args(argv)
    return {"audit": cmd_audit, "register": cmd_register, "verify": cmd_verify, "measure": cmd_measure, "contract": cmd_contract,
            "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
