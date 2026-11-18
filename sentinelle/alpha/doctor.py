"""« Docteur des erreurs » : où le système se trompe-t-il, et à quel prix ? (issues mûres seulement)

Catégories, toutes mesurées en R net, frais compris :
- ``fausse_entrée`` : entrée prise, issue négative ;
- ``refus_injustifié`` : entrée écartée (abstention ou politique) alors que son issue contrefactuelle
  était positive ; c'est un CONTREFACTUEL, jamais un gain manqué « réel » ;
- ``sortie_trop_précoce`` : une autre variante de sortie, pour la même entrée, aurait fait au moins
  0,5 R de mieux ;
- ``tué_par_les_coûts`` : mouvement favorable avant coûts (gross_r > 0) mais résultat net ≤ 0.

Le diagnostic n'entraîne rien : il produit des pistes à tester dans un FUTUR protocole préenregistré.
"""
from __future__ import annotations

import numpy as np

from .dataset import Situations
from .learners import climate
from .teachers import EXIT_VARIANTS


def diagnose(s: Situations, enter: np.ndarray, *, as_of: int | None = None, top: int = 5) -> dict:
    """``as_of`` : seules les issues disponibles à cet instant comptent (par défaut : toutes les mûres)."""
    r = s.net_r["teacher"]
    known = np.isfinite(r) & (True if as_of is None else s.available_at <= as_of)
    c = climate(s)
    cats: dict[str, list[int]] = {"fausse_entrée": [], "refus_injustifié": [], "sortie_trop_précoce": [], "tué_par_les_coûts": []}
    for k in np.flatnonzero(known):
        if enter[k] and r[k] < 0:
            cats["fausse_entrée"].append(k)
        if not enter[k] and r[k] > 0:
            cats["refus_injustifié"].append(k)
        if enter[k]:
            alts = [s.net_r[v][k] for v in EXIT_VARIANTS[1:] if np.isfinite(s.net_r[v][k])]
            if alts and max(alts) - r[k] >= 0.5:
                cats["sortie_trop_précoce"].append(k)
            o = s.outcomes[k]
            if o.gross_r is not None and o.gross_r > 0 and r[k] <= 0:
                cats["tué_par_les_coûts"].append(k)
    out = {"observations": int(known.sum()), "entries": int((enter & known).sum()), "categories": {}}
    for name, ks in cats.items():
        ks = np.array(ks, dtype=int)
        cells: dict[str, list[float]] = {}
        for k in ks:
            cells.setdefault(f"{s.teacher[k]}|climat {int(c[k])}", []).append(float(r[k]))
        worst = sorted(cells.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]
        pool = (~enter & known) if name == "refus_injustifié" else (enter & known)
        out["categories"][name] = {
            "n": int(len(ks)), "mean_net_r": float(np.mean(r[ks])) if len(ks) else None,
            "share": float(len(ks) / max(1, int(pool.sum()))),  # part des refus (ou des entrées) concernés
            "top_cells": [{"cell": cell, "n": len(v), "mean_net_r": float(np.mean(v))} for cell, v in worst],
        }
    return out
