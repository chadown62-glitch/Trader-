"""OMEGA — REPLAY : bibliothèque de ruptures synthétiques ; reproduction des trois expériences du registre OMEGA V3.

Le registre `docs/scalp/omega-registre-v3.md` (copie conforme, 25 novembre 2026) publie trois expériences synthétiques :
- HIVE / BORDER (graine 20261125) : estimateurs individuel, collectif et hybride d'un paramètre latent artificiel ;
- CORTEX / ECHO (graine 20261126) : moyennes de 1, 2, 4 et 8 prévisions à erreurs corrélées et biais communs ;
- PULSE / QUARANTINE (graine 20261127) : alertes sur les entrées, sur les erreurs révélées avec retard, et leur union.

Les deux dernières sont reproduites avec le code publié dans le registre, à l'identique. Pour la première, le registre décrit
le modèle mais pas l'ordre des tirages : un ordre naturel est fixé a priori (ci-dessous), et l'accord est jugé
statistiquement (écart au plus de 3 erreurs types de Monte-Carlo), sans recherche d'un ordre qui retrouverait les chiffres.
Les valeurs attendues en théorie sont publiées à côté.

Aucune donnée de marché, aucun rendement, aucune règle de placement : contrôles du laboratoire seulement.

Lancement : python -m sentinelle.scalp.replay
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

# Valeurs publiées par le registre V3 (copiées à l'identique).
V3_HIVE = {"stable": (0.125195, 0.159070, 0.071322), "heterogeneite_accrue": (0.764807, 0.798175, 0.710705),
           "rupture_individuelle": (0.349323, 0.163622, 0.196640), "rupture_collective": (2.115646, 2.149521, 2.061772),
           "familles_peu_coherentes": (0.125121, 1.409114, 0.324619)}
V3_CORTEX = {(0.0, 0.0): (1.000976, 0.500145, 0.249582, 0.125486), (0.5, 0.0): (1.002152, 0.752430, 0.625527, 0.563989),
             (0.9, 0.0): (1.000930, 0.951754, 0.926611, 0.914363), (0.9, 0.5): (1.252992, 1.202156, 1.177640, 1.165453)}
# (entrée, erreur, union, union avant rupture, délai médian de l'union) ; pourcentages.
V3_PULSE = {"stable": (0.50, 0.75, 1.25, 1.85, 23.0), "input_only": (100.00, 1.00, 100.00, 2.10, 13.0),
            "hidden_error": (0.50, 99.95, 99.95, 2.20, 19.0), "both": (100.00, 99.95, 100.00, 1.90, 13.0),
            "hidden_bias": (0.45, 98.65, 98.65, 2.70, 28.0)}

HIVE_SEED, CORTEX_SEED, PULSE_SEED = 20261125, 20261126, 20261127
HIVE_SCENARIOS = ("stable", "heterogeneite_accrue", "rupture_individuelle", "rupture_collective", "familles_peu_coherentes")
HYBRID_WEIGHT = 0.55  # part de l'estimation individuelle, fixée par le registre V3


def hive_border(reps: int = 1000, families: int = 20, assets: int = 50, seed: int = HIVE_SEED) -> dict:
    """Erreur quadratique moyenne des trois estimateurs sur le paramètre latent, par scénario.

    Ordre des tirages fixé a priori : un seul générateur ; scénarios dans l'ordre du registre ; pour chacun, effets de
    famille (reps × familles), effets individuels (reps × familles × actifs), bruit d'estimation, puis tirages propres
    au scénario (bruit futur, actifs inversés). Paramètre latent = famille + individuel ; estimation individuelle =
    latent + N(0, 1/8) ; collective = moyenne de la famille ; hybride = 0,55 individuelle + 0,45 collective.
    """
    rng = np.random.default_rng(seed)
    out = {}
    for name in HIVE_SCENARIOS:
        sd_f, sd_a = (0.15, 1.2) if name == "familles_peu_coherentes" else (0.7, 0.4)
        f = rng.normal(0, sd_f, (reps, families, 1))
        a = rng.normal(0, sd_a, (reps, families, assets))
        x = f + a + rng.normal(0, np.sqrt(1 / 8), (reps, families, assets))
        target = f + a
        if name == "heterogeneite_accrue":
            target = target + rng.normal(0, 0.8, (reps, families, assets))
        elif name == "rupture_individuelle":
            target = f + np.where(rng.random((reps, families, assets)) < 0.35, -a, a)
        elif name == "rupture_collective":
            target = -f + a
        c = np.broadcast_to(x.mean(axis=2, keepdims=True), x.shape)
        h = HYBRID_WEIGHT * x + (1 - HYBRID_WEIGHT) * c
        row = {}
        for label, est in (("individuel", x), ("collectif", c), ("hybride", h)):
            per_rep = ((est - target) ** 2).mean(axis=(1, 2))
            row[label] = {"eqm": float(per_rep.mean()), "erreur_type": float(per_rep.std(ddof=1) / np.sqrt(reps))}
        out[name] = row
    return out


def hive_theory(assets: int = 50) -> dict:
    """Erreurs quadratiques attendues, calcul exact (hors bruit de Monte-Carlo).

    Avec q = w + (1 − w)/n, une part p d'actifs dont l'effet individuel s'inverse (s = −1) :
    - individuel : e + 4·p·va ;
    - collectif : va·(1 + (4p − 1)/n) + e/n ;
    - hybride : va·[(1 − p)(1 − q)² + p(1 + q)² + (n − 1)((1 − w)/n)²] + e·[q² + (n − 1)((1 − w)/n)²].
    Bruit futur N(0, 0,8²) : + 0,64 partout. Effet de famille inversé : + 4·vf partout.
    """
    n, e, w = assets, 1 / 8, HYBRID_WEIGHT
    q, r = w + (1 - w) / n, (n - 1) * ((1 - w) / n) ** 2
    out = {}
    for name in HIVE_SCENARIOS:
        vf, va = (0.15**2, 1.2**2) if name == "familles_peu_coherentes" else (0.7**2, 0.4**2)
        p = 0.35 if name == "rupture_individuelle" else 0.0
        extra = 0.64 if name == "heterogeneite_accrue" else 4 * vf if name == "rupture_collective" else 0.0
        out[name] = {"individuel": e + 4 * p * va + extra,
                     "collectif": va * (1 + (4 * p - 1) / n) + e / n + extra,
                     "hybride": va * ((1 - p) * (1 - q) ** 2 + p * (1 + q) ** 2 + r) + e * (q**2 + r) + extra}
    return out


def cortex_echo(n: int = 250_000, models: int = 8, seed: int = CORTEX_SEED) -> dict:
    """Code du registre V3 : e_i = b + √ρ·Z_commun + √(1−ρ)·Z_i ; EQM de la moyenne des k premières prévisions."""
    rng = np.random.default_rng(seed)
    out = {}
    for rho, b in V3_CORTEX:
        z = rng.normal(size=(n, 1))
        u = rng.normal(size=(n, models))
        e = b + np.sqrt(rho) * z + np.sqrt(1 - rho) * u
        out[f"rho={rho},b={b}"] = {f"eqm_{k}": float(np.mean(e[:, :k].mean(axis=1) ** 2)) for k in (1, 2, 4, 8)}
    return out


def cortex_theory(rho: float, b: float, k: int) -> float:
    return b**2 + rho + (1 - rho) / k


def pulse_quarantine(n: int = 2000, length: int = 512, change: int = 256, window: int = 32, delay: int = 8,
                     seed: int = PULSE_SEED) -> dict:
    """Code du registre V3 : alertes sur les entrées, sur les erreurs révélées avec retard, et leur union."""
    rng = np.random.default_rng(seed)
    scenarios = [("stable", 0, 0, 1), ("input_only", 1.5, 0, 1), ("hidden_error", 0, 0, 2), ("both", 1.5, 0, 2), ("hidden_bias", 0, 1.25, 1)]

    def rolling(a):
        c = np.concatenate([np.zeros((len(a), 1)), np.cumsum(a, axis=1)], axis=1)
        return (c[:, window:] - c[:, :-window]) / window

    results = {}
    for name, dx, bias, scale in scenarios:
        x = rng.normal(size=(n, length))
        e = rng.normal(size=(n, length))
        x[:, change:] += dx
        e[:, change:] = e[:, change:] * scale + bias
        xa = np.zeros((n, length), bool)
        ea = xa.copy()
        xa[:, window - 1:] = np.abs(rolling(x)) > 0.65
        ea[:, window - 1 + delay:] = rolling(e**2)[:, :-delay] > 2
        result = {}
        for label, a in (("input", xa), ("residual", ea), ("union", xa | ea)):
            pre = a[:, 128:change].any(axis=1)
            early = a[:, change:change + 64]
            detected = early.any(axis=1)
            d = np.argmax(early[detected], axis=1)
            result[label] = {"pre_alert_pct": round(100 * pre.mean(), 2), "post64_alert_pct": round(100 * detected.mean(), 2),
                             "median_delay": float(np.median(d)) if len(d) else None}
        results[name] = result
    return results


def compare(hive: dict, cortex: dict, pulse: dict) -> dict:
    """Écarts aux valeurs publiées par le registre V3."""
    h = {}
    for name, published in V3_HIVE.items():
        h[name] = {}
        for label, v3 in zip(("individuel", "collectif", "hybride"), published):
            r = hive[name][label]
            h[name][label] = {"v3": v3, "reproduit": round(r["eqm"], 6), "ecart_en_erreurs_types": round((r["eqm"] - v3) / r["erreur_type"], 2)}
    c = {}
    for (rho, b), published in V3_CORTEX.items():
        r = cortex[f"rho={rho},b={b}"]
        c[f"rho={rho},b={b}"] = {"identique_a_1e-6": all(abs(r[f"eqm_{k}"] - v) < 1e-6 for k, v in zip((1, 2, 4, 8), published)),
                                 "ecart_max": max(abs(r[f"eqm_{k}"] - v) for k, v in zip((1, 2, 4, 8), published)),
                                 "theorie": {f"eqm_{k}": cortex_theory(rho, b, k) for k in (1, 2, 4, 8)}}
    p = {}
    for name, (inp, res, uni, pre, delay) in V3_PULSE.items():
        r = pulse[name]
        mine = (r["input"]["post64_alert_pct"], r["residual"]["post64_alert_pct"], r["union"]["post64_alert_pct"],
                r["union"]["pre_alert_pct"], r["union"]["median_delay"])
        p[name] = {"v3": [inp, res, uni, pre, delay], "reproduit": list(mine), "identique": list(mine) == [inp, res, uni, pre, delay]}
    return {"hive_border": h, "cortex_echo": c, "pulse_quarantine": p,
            "hive_dans_3_erreurs_types": all(abs(v["ecart_en_erreurs_types"]) <= 3 for s in h.values() for v in s.values()),
            "cortex_identique": all(v["identique_a_1e-6"] for v in c.values()),
            "pulse_identique": all(v["identique"] for v in p.values())}


def main(argv=None):
    ap = argparse.ArgumentParser(description="OMEGA REPLAY — reproduction des expériences synthétiques du registre V3 (simulation exclusive)")
    ap.add_argument("--out", default="research/scalp-v1/omega-replay-v1.json")
    ap.add_argument("--trials", default="research/scalp-v1/trials.jsonl")
    args = ap.parse_args(argv)
    t0 = time.time()
    hive, cortex, pulse = hive_border(), cortex_echo(), pulse_quarantine()
    cmp = compare(hive, cortex, pulse)
    out = {"schema": "scalp-omega-replay-v1", "status": "reproduction des expériences synthétiques du registre OMEGA V3 ; aucune donnée de marché",
           "omega": ["REPLAY", "HIVE", "BORDER", "CORTEX", "ECHO", "PULSE", "QUARANTINE"], "capacites": ["C079", "C084", "C095"],
           "registre": "docs/scalp/omega-registre-v3.md", "graines": {"hive": HIVE_SEED, "cortex": CORTEX_SEED, "pulse": PULSE_SEED},
           "hive_border": hive, "hive_theorie": hive_theory(), "cortex_echo": cortex, "pulse_quarantine": pulse, "comparaison": cmp,
           "seconds": round(time.time() - t0)}
    print({k: cmp[k] for k in ("hive_dans_3_erreurs_types", "cortex_identique", "pulse_identique")})
    for name, row in cmp["hive_border"].items():
        print(name, {k: (v["v3"], v["reproduit"], v["ecart_en_erreurs_types"]) for k, v in row.items()})
    for name, row in cmp["pulse_quarantine"].items():
        print(name, row["v3"], row["reproduit"])
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.trials:
        with open(args.trials, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": "omega-replay-v1",
                                 "accord": {k: cmp[k] for k in ("hive_dans_3_erreurs_types", "cortex_identique", "pulse_identique")},
                                 "hive_ecarts_en_erreurs_types": {n: {k: v["ecart_en_erreurs_types"] for k, v in r.items()} for n, r in cmp["hive_border"].items()}},
                                ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    main()
