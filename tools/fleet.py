"""Flotte d'agents (mission 57) — rejeu FINI d'une capture memecoin : aucune connexion, aucun ordre, aucun processus permanent.

    python -m tools.fleet replay --capture data/memecoin/capture-….jsonl.gz --output DOSSIER
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

from sentinelle.fleet import agents as A, coordinator as K  # noqa: E402
from sentinelle.memecoin import collect as C, lab as L  # noqa: E402


def events_from_capture(capture: list[dict]) -> tuple[list[A.Event], dict]:
    """Un événement par token contrôlé, daté de son contrôle ; seules les bougies déjà closes à cet instant sont visibles."""
    pools = {r["pool"]: r for r in capture if r["kind"] == "pool"}
    sec = {r["mint"]: r for r in capture if r["kind"] == "security"}
    coll = next((r for r in capture if r["kind"] == "collection"), {})
    events = [A.Event("health", "système", coll.get("finished_ms") or int(time.time() * 1000), {})]
    for r in capture:
        if r["kind"] != "ohlcv" or r["pool"] not in pools:
            continue
        p = pools[r["pool"]]
        s = sec.get(p["mint"])
        if s is None:
            continue
        t = s["observed_ms"]
        g = L.dense([c for c in r["candles"] if c[0] + 60_000 <= t])
        events.append(A.Event("memecoin_candidate", p["pool"], t, {"pool": p, "security": s, "grid": None if g is None else g.tolist(),
                                                                   "observed_ms": p["observed_ms"], "notional_eur": 10.0}))
    ctx = {"portfolio": {"equity_eur": 30.0, "cash_eur": 30.0, "open_positions": 0, "daily_loss_eur": 0.0},
           "sources": coll.get("sources") or {}, "sol_usd": L._sol_usd(capture), "disk_path": str(ROOT)}
    return events, ctx


def markdown(rep: dict, decisions: list[dict]) -> str:
    out = [f"Événements : {rep['events']} · désaccords consignés : {rep['disagreements']}", "", "| Décision | Nombre |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in sorted(rep["decisions"].items(), key=lambda kv: -kv[1])]
    out += ["", "| Agent | Essais | Échecs | Décisif | Sauté | Désactivé | Processeur total (ms) | Mémoire max (Ko) |", "|---|---|---|---|---|---|---|---|"]
    for name, a in rep["agents"].items():
        out.append(f"| {name} | {a['runs']} | {a['failures']} | {a['pivotal']} | {a['skipped']} | "
                   f"{a['disabled_reason'] or ('quarantaine' if a['quarantined'] else '—')} | {a['cpu_ms']:.1f} | {a['peak_kb']:.0f} |")
    props = [d for d in decisions if d["decision"] == K.PROPOSAL]
    out += ["", f"Propositions simulées (à soumettre au moteur de veto indépendant, jamais exécutées) : {len(props)}."]
    return "\n".join(out)


def cmd_replay(a) -> int:
    capture = C.read_capture(Path(a.capture))
    events, ctx = events_from_capture(capture)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    journal = out / "fleet-journal.jsonl"
    if journal.exists():
        print("Un journal existe déjà ici : utilisez un nouveau dossier.", file=sys.stderr)
        return 2
    k = K.Coordinator(journal=journal)
    t0 = time.perf_counter()
    decisions = k.run(events, ctx | {"journal_path": journal})
    rep = k.report() | {"wall_s": round(time.perf_counter() - t0, 2), "capture": Path(a.capture).name}
    (out / "fleet-report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    (out / "fleet-report.md").write_text(markdown(rep, decisions) + "\n", encoding="utf-8")
    print(markdown(rep, decisions))
    return 0


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = cli.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("--capture", required=True)
    r.add_argument("--output", required=True)
    a = cli.parse_args(argv)
    return {"replay": cmd_replay}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
