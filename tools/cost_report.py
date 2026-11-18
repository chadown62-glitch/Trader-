"""COST OPTIMIZER — tableaux lisibles tirés des mesures et du contrat (aucune valeur saisie à la main)."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

CLASS_LABELS = {"majors_usdt": "BTC et ETH (USDT)", "others_usdt": "autres cryptos (USDT)", "memecoin_usdt": "memecoins (USDT)",
                "eur_direct": "paires en euros", "fx_eurusdt": "conversion EUR/USDT"}


def f(x, digits=2, signed=False, pct=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if pct:
        x *= 100
    s = f"{x:+.{digits}f}" if signed else f"{x:.{digits}f}"
    return s.replace(".", ",") + (" %" if pct else "")


def ci(d, digits=2):
    if not d or d.get("estimate") is None:
        return "—"
    return f"{f(d['estimate'], digits)} [{f(d.get('ci_low'), digits)} ; {f(d.get('ci_high'), digits)}]"


def round_trips(rows: list[dict], variant: str = "market@1000ms", size_index: int = 1) -> dict[str, list[float]]:
    """Coût d'un aller-retour par échantillon : achat puis vente au marché du même échantillon (pb)."""
    by = defaultdict(dict)
    for r in rows:
        if r["quote"] and r["size_index"] == size_index and r["results"].get(variant):
            by[(r["class"], r["id"])][r["side"]] = r["results"][variant]["shortfall_bps"]
    out = defaultdict(list)
    for (cls, _), sides in by.items():
        if sides.get("buy") is not None and sides.get("sell") is not None:
            out[cls].append(sides["buy"] + sides["sell"])
    return out


def markdown(m: dict, c: dict | None, rows: list[dict], benefit) -> str:
    s = m["summary"]
    out = [f"Plan `{m['plan_id']}` · empreinte `{m['plan_sha256'][:12]}…` · collecte : {m['collection']} · {m['rows']} mesures", ""]
    out += ["#### Coût d'un ordre au marché de 10 €, par classe (points de base du montant)", "",
            "| Classe | Échantillons (avec prix) | Sans liquidité visible | Demi-écart à l'ouverture [IC 95 %] | Demi-écart en journée (médiane) | "
            "Coût total à 1 s [IC 95 %] | Glissement au-delà de l'écart [IC 95 %] | p90 à 1 s | p99 à 1 s | Coût médian à 30 s |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for cls, v in s["classes"].items():
        d = v["market_shortfall_10eur_1s_describe"]
        out.append(f"| {CLASS_LABELS.get(cls, cls)} | {v['samples']} ({v['with_quote']}) | {f(v['no_quote_share'], 1, pct=True)} | {ci(v['half_spread_open'])} | "
                   f"{f(v['by_kind']['intraday'].get('median'))} | {ci(v['market_shortfall_10eur_1s'])} | {ci(v['slippage_10eur_1s'])} | "
                   f"{f(d.get('p90'))} | {f(d.get('p99'))} | {f(v['by_latency']['30000ms'].get('median'))} |")
    rt = round_trips(rows)
    out += ["", "#### Aller-retour au marché de 10 € (achat puis vente, commission comprise)", "",
            "| Classe | Allers-retours mesurés | Moyenne | Médiane | p90 | p99 |", "|---|---|---|---|---|---|"]
    for cls, xs in sorted(rt.items()):
        x = np.array(xs)
        out.append(f"| {CLASS_LABELS.get(cls, cls)} | {len(x)} | {f(x.mean())} | {f(np.median(x))} | {f(np.quantile(x, 0.9))} | {f(np.quantile(x, 0.99))} |")
    p = m["probes"]
    out += ["", "#### Carnets observés (sondes du 16/11/2026) et erreur de l'estimateur", "",
            "| Paire | Sondes | Demi-écart observé (médiane) | Montant au meilleur prix (médiane) | Impact 30 € | Impact 100 € |", "|---|---|---|---|---|---|"]
    for sym, b in sorted(p["books"].items()):
        out.append(f"| {sym} | {b['probes']} | {f(b['half_spread_bps'].get('median'), 3)} | {f(b['depth_quote_top'].get('median'), 0)} | "
                   f"{f(b['impact_bps']['30.0'].get('median'), 3)} | {f(b['impact_bps']['100.0'].get('median'), 3)} |")
    out += ["", "| Classe | Comparaisons | Erreur moyenne (estimé − observé) [IC 95 %] | Part sous-estimée |", "|---|---|---|---|"]
    for cls, e in sorted(p["estimator_error"].items()):
        out.append(f"| {CLASS_LABELS.get(cls, cls)} | {e['n']} | {ci(e['mean_error'], 3)} | {f(e['underestimates_share'], 0, pct=True)} |")
    out += ["", "#### Méthodes d'exécution (ordre de 10 €, toutes classes, achats et ventes)", "",
            "| Méthode | Part exécutée | dont passive | Coût moyen | Coût médian | Gain vs marché à 1 s [IC 95 %] | Mouvement pendant l'attente (médiane) |",
            "|---|---|---|---|---|---|---|"]
    for name in sorted(s["policies"], key=lambda n: (not n.startswith("market"), n)):
        v = s["policies"][name]
        out.append(f"| {name} | {f(v['fill_ratio'], 0, pct=True)} | {f(v['passive_ratio'], 0, pct=True)} | {f(v['shortfall'].get('mean'))} | "
                   f"{f(v['shortfall'].get('median'))} | {ci(v['vs_market'])} | {f(v['adverse_drift'].get('median'))} |")
    k = s.get("confirmatory") or {}
    if k:
        out += ["", f"**Comparaison confirmatoire** : variante retenue sur la moitié d'estimation : `{k['chosen']}` ; sur la moitié de validation, "
                f"gain moyen face au marché {ci(k['validation_market_minus_chosen'])} pb (règle prudente), borne optimiste "
                f"{ci(k['validation_optimistic_bound'])} pb. Amélioration démontrée : **{'oui' if k['demonstrated'] else 'non'}**."]
    out += ["", "#### Optimisme du remplissage « au toucher » (moteur actuel sur bougies)", "",
            "| Variante | Échantillons | Bougie du jour touchée | Exécuté selon le ruban |", "|---|---|---|---|"]
    for name, v in sorted(m["bar_touch"].items()):
        out.append(f"| {name} | {v['n']} | {f(v['bar_touch_rate'], 0, pct=True)} | {f(v['tape_fill_rate'], 0, pct=True)} |")
    out += ["", "#### Routage : paire en euros, ou conversion EUR → USDT puis paire en USDT (achat de 10 €)", "",
            "| Actif | Paires mesurées | Direct (moyenne) | Via USDT (moyenne) | Écart via − direct [IC 95 %] |", "|---|---|---|---|---|"]
    for base, v in m["routing"].items():
        out.append(f"| {base} | {v['pairs']} | {f(v['direct'].get('mean'))} | {f(v['via_usdt'].get('mean'))} | {ci(v['via_minus_direct'])} |")
    out += ["", "#### Situations défavorables (ordre de 10 € au marché, états connus la veille)", "",
            "| État | Seuil | Coût médian (état) | p90 (état) | Coût médian (reste) | Demi-écart médian (état / reste) |", "|---|---|---|---|---|---|"]
    for name, v in m["stress"].items():
        if name == "after_10pct_day":
            out.append(f"| lendemain d'un mouvement de plus de 10 % | {v['samples']} éch. | {f(v['shortfall'].get('median'))} | {f(v['shortfall'].get('p90'))} | — | "
                       f"{f(v['half_spread'].get('median'))} / — |")
        else:
            out.append(f"| {name} (quart supérieur) | {f(v['threshold'], 4)} | {f(v['high'].get('median'))} | {f(v['high'].get('p90'))} | "
                       f"{f(v['rest'].get('median'))} | {f(v['high_half_spread'].get('median'))} / {f(v['rest_half_spread'].get('median'))} |")
    out += ["", "#### Reliquats et tailles réelles (aller-retour de 10 €, frais prélevés dans l'actif reçu, filtres observés)", "",
            "| Classe | Échantillons | Reliquat médian (part de la position) | p90 | Écart de taille médian |", "|---|---|---|---|---|"]
    for cls, v in sorted(m["inventory"].items()):
        out.append(f"| {CLASS_LABELS.get(cls, cls)} | {v['n']} | {f(v['dust_share'].get('median'), 1, pct=True)} | {f(v['dust_share'].get('p90'), 1, pct=True)} | "
                   f"{f(v['sizing_error'].get('median'), 1, signed=True, pct=True)} |")
    if c:
        out += ["", "#### Contrat de coûts proposé (un ordre, points de base)", "",
                "| Classe | Commission | Demi-écart (principal) | Glissement (principal) | Demi-écart (tension) | Glissement (tension) | Aller-retour preneur (principal) |",
                "|---|---|---|---|---|---|---|"]
        for cls, v in c["classes"].items():
            out.append(f"| {CLASS_LABELS.get(cls, cls)} | {f(v['fee_taker']['value'])} ({v['fee_taker']['level']}) | {f(v['half_spread']['value'])} | "
                       f"{f(v['slippage']['value'])} | {f(v['half_spread_stress']['value'])} | {f(v['slippage_stress']['value'])} | "
                       f"{f(v['round_trip_taker_bps'] if 'round_trip_taker_bps' in v else 2 * (v['fee_taker']['value'] + v['half_spread']['value'] + v['slippage']['value']))} |")
        out += ["", f"Conversion par ordre : {f(c['conversion']['per_order']['value'])} pb (tension : {f(c['conversion']['per_order_stress']['value'])} pb). "
                f"Méthode recommandée : `{c['execution']['recommended']}`."]
    out += ["", "#### Bénéfice net d'un avantage brut HYPOTHÉTIQUE, après les coûts mesurés (aller-retour au marché, 10 €, un an)", "",
            "| Classe | Avantage brut par aller-retour | Opérations par an | Net moyen par opération [IC 95 %] | Probabilité net > 0 | Coûts annuels (€) | Net annuel (€) |",
            "|---|---|---|---|---|---|---|"]
    for cls in ("majors_usdt", "others_usdt", "memecoin_usdt", "eur_direct"):
        if cls not in rt:
            continue
        for row in benefit.grid(rt[cls], gross_edges_bps=(0.0, 25.0, 50.0, 100.0), trades_per_year=(12, 52), position_eur=10.0):
            out.append(f"| {CLASS_LABELS[cls]} | {f(row['gross_mean_bps'], 0)} pb | {row['trades_per_year']} | {f(row['net_mean_bps'], 1)} "
                       f"[{f(row['net_ci_low'], 1)} ; {f(row['net_ci_high'], 1)}] | {f(row['p_net_positive'], 0, pct=True)} | {f(row['annual_cost_eur'])} | "
                       f"{f(row['annual_net_eur'], 2, signed=True)} |")
    return "\n".join(out)
