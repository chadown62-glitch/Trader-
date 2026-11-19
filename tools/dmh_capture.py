"""DIRECT MARKET HUB — capture publique BORNÉE puis validation hors ligne (scénarios identiques et reproductibles).

capture  : lit une fois les carnets, règles, devis et frais de réseau nécessaires aux scénarios (actifs × tailles),
           tels que le routeur les demande, et les archive (empreinte SHA-256). Aucun service n'est démarré.
validate : rejoue la capture (aucun réseau) : décision du hub, exécution simulée sur les carnets capturés, et
           comparaison avec le moteur existant sur les mêmes scénarios. Écrit validation.json et tableaux.md.

Usage : python -m tools.dmh_capture capture --out DOSSIER   puis   python -m tools.dmh_capture validate --capture FICHIER
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinelle.dmh import assumptions as A  # noqa: E402
from sentinelle.dmh.ledger import Ledger, StandaloneCash  # noqa: E402
from sentinelle.dmh.net import PublicClient  # noqa: E402
from sentinelle.dmh.router import Intent, build_routes, choose, economic_minimum  # noqa: E402
from sentinelle.dmh.security import DEFAULT_LIMITS, Portfolio, Verdict, check_financial, check_route, effective  # noqa: E402
from sentinelle.dmh.simulate import PaperExecutor  # noqa: E402
from sentinelle.dmh.tokens import load_registry  # noqa: E402
from sentinelle.dmh.view import LiveView, ReplayView  # noqa: E402
from sentinelle.storage.db import Database  # noqa: E402

try:  # module absent sous Windows : temps processeur sans mémoire du processus
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None


def _usage() -> tuple[float, float | None]:
    if resource is None:
        return time.process_time(), None
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime, r.ru_maxrss / 1024

SCENARIOS = [("BTC", ()), ("ETH", ("base", "ethereum")), ("SOL", ("solana",))]
SIZES = (5.0, 10.0, 30.0)
EDGE_BPS = 100.0  # avantage brut HYPOTHÉTIQUE servant seulement à classer les routes (jamais un résultat de stratégie)
# Moteur existant (réglages actuels) : commission 10 pb + glissement 5 pb par ordre, + 5 pb de conversion sur une
# paire cotée en USDC depuis le portefeuille en euros (cas de l'application) ; aucun écart compté sans carnet.
EXISTING = {"eur_pair_order_bps": 15.0, "usdc_pair_order_bps": 20.0, "touch_fill": 1.0}
# COST OPTIMIZER (#50), mesures publiées : coût réel d'un ordre au marché de 10 € depuis des euros, par classe.
MEASURED_50 = {"eur_direct_order_bps": 11.41, "majors_usdt_order_bps": 21.88, "others_usdt_order_bps": 23.69}


def capture(out: Path) -> Path:
    client = PublicClient(budget=1500)
    # une seule version de chaque donnée par scénario (pas de relecture en cours de décision) : rejeu exact
    view = LiveView(client, ttl_ms={"book": 600_000, "dex": 600_000, "gas": 600_000, "solana": 600_000})
    reg = load_registry()
    t0 = int(time.time() * 1000)
    timings, per = [], {}
    for asset, nets in SCENARIOS:
        for n in SIZES:
            # une capture complète par scénario : tout ce que le routeur lit pour CETTE décision, rien de réutilisé
            view._cache.clear()
            rec: dict = {}
            view.recorder = rec
            start = int(time.time() * 1000)
            s = time.perf_counter()
            routes = build_routes(view, reg, Intent(asset, n, EDGE_BPS, nets, True))
            choose(routes, EDGE_BPS)
            timings.append({"asset": asset, "notional_eur": n, "seconds": time.perf_counter() - s, "routes": len(routes)})
            per[f"{asset}|{n:g}"] = {"captured_ms": start, "finished_ms": int(time.time() * 1000), "data": rec}
            print(f"{asset} {n:g} € : {len(routes)} routes en {timings[-1]['seconds']:.1f} s", flush=True)
    doc = {"kind": "dmh-capture", "captured_ms": t0, "finished_ms": int(time.time() * 1000), "scenarios": [list(x) for x in SCENARIOS],
           "sizes": list(SIZES), "edge_bps": EDGE_BPS, "assumptions": {"version": A.VERSION, "sha256": A.digest()},
           "registry_sha256": reg.meta.get("sha256"), "timings": timings, "network": client.health(), "per_scenario": per}
    out.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str).encode()
    path = out / f"capture-{t0}.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    (out / f"capture-{t0}.json.gz.sha256").write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n", encoding="utf-8")
    return path


def _simulate(view, reg, route_dict, n) -> dict:
    """Exécute la route retenue (entrée puis sortie) sur les carnets capturés, dans un portefeuille fictif isolé."""
    db = Database(":memory:")
    led = Ledger(db, 1, clock=lambda: view.now_ms() / 1000)
    led.cash = StandaloneCash(led)
    lim = effective(dict(DEFAULT_LIMITS)) | {"max_book_age_ms": 10**12, "max_quote_age_ms": 10**12}
    ex = PaperExecutor(view, led, limits=lim)
    first = route_dict["entry"][0]
    if first["asset_in"] != "EUR":
        return {"simulated": False, "reason": "route financée depuis un solde sur la chaîne"}
    reserve = sum(x["qty_in"] for x in route_dict["entry"] if x["asset_in"] == "EUR")
    led.open_operation("in", origin="validation", side="buy", asset="X", intent={}, route={}, verdict={}, config_hash="v",
                       evidence_mode=view.mode, reserve=("EUR", "cash", reserve))
    out_in = ex.run("in", route_dict, route_dict, which="entry")
    led.settle("in", out_in.state if out_in.state != "OPEN" else "NOT_FILLED", out_in.fills, {})
    if out_in.state not in ("FILLED", "PARTIAL"):
        return {"simulated": True, "entry_state": out_in.state, "notes": out_in.notes}
    held = [h for h in led.holdings() if h["location"] != "cash"]
    target = max(held, key=lambda h: h["cost_eur"])
    led.open_operation("out", origin="validation", side="sell", asset="X", intent={}, route={}, verdict={}, config_hash="v",
                       evidence_mode=view.mode, reserve=(target["asset"], target["location"], target["qty"]))
    out_out = ex.run("out", route_dict, route_dict, which="exit")
    led.settle("out", out_out.state, out_out.fills, {})
    left = sum(h["cost_eur"] for h in led.holdings())
    final_cash = led.cash.total()
    return {"simulated": True, "entry_state": out_in.state, "exit_state": out_out.state, "spent_eur": reserve,
            "final_cash_eur": final_cash, "leftover_cost_eur": left, "round_trip_cost_eur": 30.0 - final_cash - left,
            "round_trip_cost_bps": (30.0 - final_cash - left) / reserve * 1e4, "reconciled": led.reconcile()["ok"],
            "notes": out_in.notes + out_out.notes}


def _user_path(view, reg, asset, n, nets, edge) -> dict:
    """PARCOURS UTILISATEUR : limites par défaut, portefeuille neuf de 30 €, même règle de choix que le hub."""
    lim = effective(dict(DEFAULT_LIMITS)) | {"max_book_age_ms": 10**12, "max_quote_age_ms": 10**12}  # données d'archive
    routes = build_routes(view, reg, Intent(asset, n, edge, tuple(nets), True))
    c = choose(routes, edge, max_cost_bps=lim["max_round_trip_cost_bps"], max_immobilized_pct=lim["max_immobilized_pct"])
    v = Verdict()
    chosen = c.route.to_dict(edge) if c.route else None
    pf = Portfolio(30.0, 30.0, {}, 30.0, 30.0, 30.0)
    if chosen:
        check_financial(v, lim, pf, side="buy", asset=asset, notional=n, route_totals=chosen["totals"], cash_needed=n)
        check_route(v, lim, chosen, view.now_ms(), reg, {})
    else:
        check_financial(v, lim, pf, side="buy", asset=asset, notional=n, route_totals=None, cash_needed=n)
    outcome = "AUTORISÉE" if chosen and v.allowed else ("REFUS DE SÉCURITÉ" if chosen else "ABSTENTION")
    return {"limits": "par défaut (position ≤ 10 €, minimum 10 €, réserve 6 €, immobilisation ≤ 1 %, coût ≤ 150 pb)",
            "outcome": outcome, "route": chosen["label"] if chosen else None,
            "cost_bps": chosen["totals"]["cost_bps"] if chosen else None,
            "immobilized_pct": chosen["totals"]["immobilized_pct"] if chosen else None,
            "reasons": [r.message for r in v.reasons if r.veto] + c.reasons, "fee_dependent": c.fee_dependent}


def validate(capture_path: Path, out: Path) -> dict:
    raw = capture_path.read_bytes()
    cap = json.loads(gzip.decompress(raw))
    reg = load_registry()
    rows = []
    tracemalloc.start()
    cpu0, _ = _usage()
    t0 = time.perf_counter()
    decisions = 0
    for asset, nets in cap["scenarios"]:
        for n in cap["sizes"]:
            view = ReplayView(cap["per_scenario"][f"{asset}|{n:g}"])
            s = time.perf_counter()
            routes = build_routes(view, reg, Intent(asset, n, cap["edge_bps"], tuple(nets), True))
            # 1) MESURE DE COÛT : portefeuille de mesure isolé, AUCUNE limite de l'utilisateur (montant, réserve,
            #    immobilisation, plafond de coût) ; seule la règle de choix robuste s'applique
            c = choose(routes, cap["edge_bps"])
            decisions += 1
            dt = time.perf_counter() - s
            chosen = c.route.to_dict(cap["edge_bps"]) if c.route else None
            v = Verdict()
            if chosen:
                check_route(v, effective(dict(DEFAULT_LIMITS)) | {"max_book_age_ms": 10**12, "max_quote_age_ms": 10**12},
                            chosen, view.now_ms(), reg, {})
            sim = _simulate(view, reg, chosen, n) if chosen else {"simulated": False}
            existing_rt = 2 * EXISTING["usdc_pair_order_bps"]
            best_by_kind = {}
            for r in routes:  # toutes les routes CHIFFRÉES, y compris celles écartées pour leur coût (comparaison complète)
                if not r.entry or r.exit is None or any(x.startswith("route impossible") for x in r.reasons):
                    continue
                t = r.totals()
                k = r.kind
                if k not in best_by_kind or t["cost_bps"] < best_by_kind[k]["cost_bps"]:
                    best_by_kind[k] = {"label": r.label, "cost_bps": t["cost_bps"], "cost_low_bps": t["cost_low_bps"],
                                       "cost_high_bps": t["cost_high_bps"], "fill": t["entry_fill_probability"], "dust_eur": t["dust_eur"],
                                       "latency_ms": t["latency_ms"], "finality_ms": t["finality_ms"], "fees_paid_eur": t["fees_paid_eur"],
                                       "immobilized_eur": t["immobilized_eur"], "immobilized_pct": t["immobilized_pct"],
                                       "adverse_bps": t["scenarios_bps"]["defavorable"]}
            impossible = [{"label": r.label, "reason": r.reasons[0]} for r in routes if r.reasons][:12]
            user = _user_path(view, reg, asset, n, nets, cap["edge_bps"])
            minimums = []
            for venue in view.venues():
                pair = f"{asset}/EUR"
                if view.supports(venue, pair):
                    try:
                        b, rules = view.book(venue, pair), view.rules(venue, pair)
                    except Exception:
                        continue
                    minimums.append(economic_minimum(rules, b.mid, view.fees(venue)["taker"].value / 1e4,
                                                     max_immobilized_pct=DEFAULT_LIMITS["max_immobilized_pct"]))
            rows.append({"asset": asset, "notional_eur": n, "measure": "coût hors limites de l'utilisateur (portefeuille de mesure isolé)",
                         "user_path": user, "economic_minimums": minimums, "decision": c.decision, "decision_seconds": dt,
                         "chosen": None if not chosen else {"label": chosen["label"], "kind": chosen["kind"], **chosen["totals"]},
                         "security": v.to_dict(), "simulated": sim, "best_by_kind": best_by_kind, "impossible": impossible,
                         "sensitivity": c.sensitivity, "existing_engine_rt_bps": existing_rt,
                         "existing_engine_rt_eur": existing_rt / 1e4 * n,
                         "saving_vs_existing_estimated_bps": None if not chosen else existing_rt - chosen["totals"]["cost_bps"],
                         "saving_vs_existing_simulated_bps": None if not sim.get("round_trip_cost_bps") else existing_rt - sim["round_trip_cost_bps"]})
    wall = time.perf_counter() - t0
    cpu1, rss = _usage()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    net = cap["network"]["hosts"]
    avail = {h: {"calls": x["calls"], "errors": x["errors"], "availability": (1 - x["errors"] / x["calls"]) if x["calls"] else None,
                 "latency_ms_median": x["latency_ms_median"]} for h, x in net.items()}
    doc = {"kind": "dmh-validation", "capture": capture_path.name, "capture_sha256": hashlib.sha256(raw).hexdigest(),
           "captured_ms": cap["captured_ms"], "edge_bps_hypothesis": cap["edge_bps"], "existing_engine": EXISTING,
           "cost_optimizer_measured": MEASURED_50, "assumptions": cap["assumptions"], "rows": rows,
           "performance": {"decisions": decisions, "replay_wall_seconds": wall, "cpu_seconds": cpu1 - cpu0,
                           "peak_python_mb": peak / 1e6, "max_rss_mb": rss,
                           "live_decision_seconds": cap["timings"]},
           "data_availability": avail,
           "security_vetoes": sum(len(r["security"]["vetoes"]) for r in rows)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "tableaux.md").write_text(tables(doc), encoding="utf-8")
    return doc


def _f(x, d=1):
    return "—" if x is None else f"{x:.{d}f}".replace(".", ",")


def tables(doc: dict) -> str:
    out = [f"Capture `{doc['capture']}` (empreinte `{doc['capture_sha256'][:12]}…`), avantage brut HYPOTHÉTIQUE {doc['edge_bps_hypothesis']:.0f} pb "
           "(sert seulement à classer les routes).", "",
           "#### A. Parcours utilisateur : limites par défaut appliquées, portefeuille neuf de 30 €", "",
           "| Actif | Montant | Issue | Route | Coût (pb) | Immobilisé | Motifs |", "|---|---|---|---|---|---|---|"]
    for r in doc["rows"]:
        u = r["user_path"]
        out.append(f"| {r['asset']} | {r['notional_eur']:g} € | **{u['outcome']}** | {u['route'] or '—'} | {_f(u['cost_bps'])} | "
                   f"{_f(u['immobilized_pct'], 2)} % | {' ; '.join(u['reasons'])[:220] or '—'} |")
    out += ["", "#### B. Mesure de coût : portefeuille de mesure isolé, limites de l'utilisateur NON appliquées", "",
            "Ces chiffres comparent des routes ; ils ne disent pas qu'une opération de ce montant serait autorisée (voir A).", "",
           "| Actif | Montant | Décision | Route retenue | Coût estimé (pb) [bas ; haut] | Coût simulé sur les carnets (pb) | Moteur existant (pb) | Écart estimé | Écart simulé |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in doc["rows"]:
        c = r["chosen"] or {}
        sim = r["simulated"]
        out.append(f"| {r['asset']} | {r['notional_eur']:g} € | {r['decision']} | {c.get('label', '—')} | {_f(c.get('cost_bps'))} "
                   f"[{_f(c.get('cost_low_bps'))} ; {_f(c.get('cost_high_bps'))}] | {_f(sim.get('round_trip_cost_bps'))} | "
                   f"{_f(r['existing_engine_rt_bps'])} | {_f(r['saving_vs_existing_estimated_bps'])} | {_f(r['saving_vs_existing_simulated_bps'])} |")
    out += ["", "#### C. Meilleure route de chaque type (pb aller-retour ; frais payés et capital immobilisé séparés)", "",
            "| Actif | Montant | Type | Route | Coût économique | Frais payés (€) | Immobilisé (€ ; %) | Défavorable | Remplissage | Latence (s) | Finalité (s) |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in doc["rows"]:
        for k, b in sorted(r["best_by_kind"].items(), key=lambda kv: kv[1]["cost_bps"]):
            out.append(f"| {r['asset']} | {r['notional_eur']:g} € | {k} | {b['label']} | {_f(b['cost_bps'])} | {_f(b['fees_paid_eur'], 4)} | "
                       f"{_f(b['immobilized_eur'], 3)} ; {_f(b['immobilized_pct'], 2)} % | {_f(b['adverse_bps'])} | {b['fill']:.0%} | "
                       f"{_f(b['latency_ms'] / 1000, 1)} | {_f(b['finality_ms'] / 1000, 0)} |")
    out += ["", "#### D. Seuil économique d'achat par plateforme et par actif (règle de la mission 52)", "",
            "Minimum = le plus grand de : plancher prudent de 10 € ; minimum de la plateforme + un pas d'arrondi, commission comprise ; "
            "montant pour lequel un reliquat d'un pas reste ≤ 1 % du montant.", "",
            "| Actif | Plateforme | Pas | Valeur d'un pas (€) | Minimum plateforme (€) | Minimum reliquat (€) | **Seuil économique (€)** | Contrainte |",
            "|---|---|---|---|---|---|---|---|"]
    seen = set()
    for r in doc["rows"]:
        for m in r["economic_minimums"]:
            key = (r["asset"], m["venue"])
            if key in seen:
                continue
            seen.add(key)
            out.append(f"| {r['asset']} | {m['venue']} | {m['step']:g} | {_f(m['step_value_eur'], 4)} | {_f(m['venue_min_eur'], 2)} | "
                       f"{_f(m['dust_min_eur'], 2)} | **{_f(m['economic_min_eur'], 2)}** | {m['binding']} |")
    p = doc["performance"]
    out += ["", "#### Ressources", "", f"- {p['decisions']} décisions rejouées en {_f(p['replay_wall_seconds'], 2)} s, CPU {_f(p['cpu_seconds'], 2)} s, "
            f"mémoire Python maximale {_f(p['peak_python_mb'], 1)} Mo, mémoire du processus {_f(p['max_rss_mb'], 0)} Mo ;",
            "- décision en direct (réseau compris) : " + ", ".join(f"{t['asset']} {t['notional_eur']:g} € {t['seconds']:.1f} s" for t in p["live_decision_seconds"]),
            "", "#### Disponibilité des sources pendant la capture", "", "| Source | Appels | Erreurs | Disponibilité | Latence médiane (ms) |", "|---|---|---|---|---|"]
    for h, a in sorted(doc["data_availability"].items()):
        pct = "—" if a["availability"] is None else f"{a['availability']:.0%}"
        out.append(f"| {h} | {a['calls']} | {a['errors']} | {pct} | {_f(a['latency_ms_median'], 0)} |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    logging.disable(logging.CRITICAL)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--out", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--capture", required=True)
    v.add_argument("--out", default=str(ROOT / "docs" / "validation" / "mesures" / "market-hub-v1"))
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        print(capture(Path(a.out)))
    else:
        doc = validate(Path(a.capture), Path(a.out))
        print(json.dumps({"decisions": doc["performance"]["decisions"], "vetoes": doc["security_vetoes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
