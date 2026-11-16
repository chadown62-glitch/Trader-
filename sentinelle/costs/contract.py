"""Contrat de coûts versionné ``cost_contract_v1`` et sa traduction pour ALPHA FACTORY v2.

Le contrat est construit UNIQUEMENT à partir des mesures du plan et des règles écrites dans le plan.
Il est écrit une seule fois (empreinte SHA-256) ; une nouvelle mesure donne un nouveau contrat, avec une
nouvelle version, jamais une retouche. Chaque composante garde son niveau de preuve.

Classes : ``majors_usdt`` (BTC, ETH), ``others_usdt`` (autres cryptos liquides), ``memecoin_usdt``
(memecoins cotés sur la plateforme), ``eur_direct`` (paires en euros), ``fx_eurusdt`` (conversion).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import CONTRACT
from .evidence import Value, weakest
from .markets import CRYPTO_CLASSES, dex_memecoin_hypotheses
from .venues import FeeSchedule

EXPORT_RULE = ("majors = classe majors_usdt ; others = maximum des classes others_usdt et memecoin_usdt ; glissement = maximum "
               "des classes de cryptos en USDT ; conversion par ordre = coût de conversion principal ; minimum d'ordre 5")


class ContractError(ValueError):
    pass


def _est(value, ci, n, method, note="") -> Value:
    return Value(float(value), "bps", "ESTIMATED", "archive publique officielle Binance (transactions agrégées)", method=method,
                 n=int(n), interval=None if ci is None else (float(ci[0]), float(ci[1])), note=note)


def class_values(cls: str, summary: dict, probes: dict, fees: FeeSchedule) -> dict:
    """Composantes d'un ordre pour une classe, scénario principal et scénario de tension, selon les règles du plan."""
    c = summary["classes"].get(cls)
    if not c or c["half_spread_open"]["estimate"] is None or c["half_spread_open"]["ci_high"] is None:
        raise ContractError(f"Mesures insuffisantes pour la classe {cls}")
    hs = c["half_spread_open"]
    correction, note = 0.0, ""
    err = probes.get("estimator_error", {}).get(cls)
    if err and err["mean_error"]["ci_high"] is not None and err["mean_error"]["ci_high"] < 0:
        correction = abs(err["mean_error"]["ci_low"])
        note = f"correction de sous-estimation mesurée par les sondes : +{correction:.2f} pb"
    slip = c["slippage_10eur_1s"]
    hs_primary = hs["ci_high"] + correction
    slip_primary = max(0.0, slip["ci_high"] if slip["ci_high"] is not None else 0.0)
    hs_stress = max(hs_primary, (c["half_spread_all"].get("p90") or 0.0) + correction)
    slip_stress = max(slip_primary, c["slippage_30eur_5s"].get("p90") or 0.0)
    return {
        "fee_taker": fees.taker, "fee_maker": fees.maker,
        "half_spread": _est(hs_primary, (hs["ci_low"], hs["ci_high"]), hs["n"], "borne haute à 95 % de la moyenne, échantillons d'ouverture "
                            "(bootstrap par dates)", note),
        "slippage": _est(slip_primary, (slip["ci_low"], slip["ci_high"]), slip["n"], "max(0, borne haute à 95 % de la moyenne du coût "
                         "au marché à 1 s moins demi-écart et commission), 10 €"),
        "half_spread_stress": _est(hs_stress, None, c["half_spread_all"]["n"], "90e centile du demi-écart, tous échantillons", note),
        "slippage_stress": _est(slip_stress, None, max(1, c["slippage_30eur_5s"].get("n", 1)), "90e centile du coût au marché à 5 s "
                                "moins demi-écart et commission, 30 €"),
        "no_quote_share": c["no_quote_share"],
    }


def build(*, plan: dict, plan_sha256: str, summary: dict, probes: dict, routing: dict, stress: dict, inventory: dict, bar_touch: dict,
          fees: FeeSchedule, filters_sha256: str, filters_as_of: str, coverage: dict, created_at: str, version: str = "1.0.0") -> dict:
    classes, missing = {}, []
    for cls in CRYPTO_CLASSES:
        try:
            classes[cls] = class_values(cls, summary, probes, fees)
        except ContractError:
            missing.append(cls)
    if "fx_eurusdt" not in classes:
        raise ContractError("Conversion EUR/USDT non mesurée : contrat impossible")
    fx = classes["fx_eurusdt"]
    taker = fees.taker.value
    conversion = Value(fx["half_spread"].value + fx["slippage"].value + taker, "bps", weakest([fx["half_spread"].level, fees.taker.level]),
                       "demi-écart et glissement EUR/USDT mesurés + commission preneur", method="somme des composantes du scénario principal",
                       n=fx["half_spread"].n, note="appliquée à chaque ordre d'une paire non cotée en euros, comme le moteur actuel")
    conversion_stress = Value(fx["half_spread_stress"].value + fx["slippage_stress"].value + taker, "bps",
                              weakest([fx["half_spread_stress"].level, fees.taker.level]), "composantes de tension EUR/USDT + commission",
                              method="somme des composantes de tension", n=fx["half_spread_stress"].n,
                              note="tension : conversion à chaque ordre aux 90e centiles")
    confirm = summary.get("confirmatory") or {}
    execution = {"recommended": confirm.get("chosen") if confirm.get("demonstrated") else summary_reference(plan),
                 "passive_improvement_demonstrated": bool(confirm.get("demonstrated")), "confirmatory": confirm,
                 "rule": plan["rules"]["execution_confirmatory"]}
    body = {
        "contract": CONTRACT, "kind": "cost-contract", "schema": 1, "version": version, "created_at": created_at, "status": "PROPOSED_FROZEN",
        "can_trade": False, "plan_id": plan["plan_id"], "plan_sha256": plan_sha256, "fees": {
            "schedule": f"{fees.venue} {fees.tier}", "taker": fees.taker.to_dict(), "maker": fees.maker.to_dict(),
            "discounted_taker": fees.discounted_taker.to_dict() if fees.discounted_taker else None, "fee_asset_rule": fees.fee_asset_rule.to_dict()},
        "filters": {"sha256": filters_sha256, "as_of": filters_as_of, "level": "OBSERVED"},
        "classes": {k: {n: (v.to_dict() if isinstance(v, Value) else v) for n, v in vals.items()} for k, vals in classes.items()},
        "missing_classes": missing,
        "conversion": {"per_order": conversion.to_dict(), "per_order_stress": conversion_stress.to_dict(),
                       "alternative": plan["rules"]["conversion_alternative"]},
        "execution": execution, "routing": routing, "stress_states": stress, "inventory": inventory, "bar_touch_optimism": bar_touch,
        "coverage": coverage, "memecoin_dex": {k: v.to_dict() for k, v in dex_memecoin_hypotheses().items()},
        "not_covered": ["equity", "etf", "forex", "commodity"],
        "rules": plan["rules"], "export_rule": EXPORT_RULE,
        "limits": [
            "grille de frais non archivable ici (page publique inaccessible) : commission traitée comme une hypothèse",
            "pas de carnets historiques : écart et prix estimés à partir des transactions, erreur mesurée par des sondes récentes",
            "file d'attente inconnue : les ordres passifs sont bornés (prudent / optimiste), jamais supposés exécutés",
            "filtres de marché observés au 16/11/2026, appliqués au passé (les pas de quantité ont pu changer)",
        ],
    }
    return body


def summary_reference(plan: dict) -> str:
    return f"market@{plan['latencies_ms'][0]}ms"


def to_alpha_lab(contract: dict) -> dict:
    """Traduction au format ``alpha-lab-cost-hypotheses`` lu par ALPHA FACTORY v2 (proposition, non appliquée ici)."""
    cls = contract["classes"]

    def v(name, key):
        return cls[name][key]["value"] if name in cls else 0.0

    others = [n for n in ("others_usdt", "memecoin_usdt") if n in cls]
    usdt = [n for n in ("majors_usdt", "others_usdt", "memecoin_usdt") if n in cls]
    taker = contract["fees"]["taker"]["value"]
    primary = {"fee_bps": taker, "slippage_bps": round(max(v(n, "slippage") for n in usdt), 4),
               "half_spread_bps": {"majors": round(v("majors_usdt", "half_spread"), 4), "others": round(max(v(n, "half_spread") for n in others), 4)},
               "fx_bps": round(contract["conversion"]["per_order"]["value"], 4), "note": "mesures du plan " + contract["plan_id"]}
    stress = {"fee_bps": taker, "slippage_bps": round(max(v(n, "slippage_stress") for n in usdt), 4),
              "half_spread_bps": {"majors": round(v("majors_usdt", "half_spread_stress"), 4),
                                  "others": round(max(v(n, "half_spread_stress") for n in others), 4)},
              "fx_bps": round(contract["conversion"]["per_order_stress"]["value"], 4), "note": "tension : 90e centiles"}
    return {"kind": "alpha-lab-cost-hypotheses", "schema": 1, "version": f"2.0.0+{contract['version']}",
            "source_of_truth": f"COST OPTIMIZER, {contract['contract']} {contract['version']} (plan {contract['plan_id']})",
            "status": "PROPOSÉ : mesuré et figé par COST OPTIMIZER ; à valider avant l'évaluation unique d'ALPHA FACTORY v2",
            "unit": "points de base (0,01 %) du montant de chaque ordre", "min_notional": 5.0, "majors": ["BTC", "ETH"],
            "scenarios": {"brut": {"fee_bps": 0, "slippage_bps": 0, "half_spread_bps": {"majors": 0, "others": 0}, "fx_bps": 0,
                                   "note": "sans aucun coût : sépare l'avantage brut des coûts, jamais pour conclure"},
                          "mesure": primary, "tension": stress},
            "primary": "mesure", "stress": "tension", "gross": "brut"}


def write(directory: Path, contract: dict, export: dict) -> dict:
    directory = Path(directory)
    path = directory / "cost-contract-v1.json"
    if path.exists():
        raise ContractError("Un contrat existe déjà : il est immuable (nouvelle version = nouveau fichier)")
    out = {}
    for name, body in (("cost-contract-v1.json", contract), ("alpha-lab-cost-hypotheses-proposal.json", export)):
        payload = (json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        (directory / name).write_bytes(payload)
        out[name] = hashlib.sha256(payload).hexdigest()
    (directory / "cost-contract-v1.sha256").write_text("".join(f"{h}  {n}\n" for n, h in out.items()), encoding="utf-8")
    return out
