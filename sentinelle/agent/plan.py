"""Plan d'un trade : ce que l'IA écrit AVANT d'entrer.

Raisons du signal, point d'entrée, stop (invalidation), objectif, trois scénarios avec leurs probabilités,
incertitude sur ces probabilités, frais attendus, taille autorisée et événements qui font abandonner le trade.
Le plan est enregistré avec la décision, qu'elle soit prise ou refusée : on peut ensuite le relire et le
confronter à ce qui s'est réellement passé.

Les probabilités ne sont jamais présumées bonnes : leur marge d'erreur vient de la calibration mesurée sur des
situations que le modèle n'avait jamais vues. Sans modèle, aucune probabilité n'est annoncée.
"""

from __future__ import annotations

import math

REGIMES = {"bull": "haussier", "bear": "baissier", "range": "neutre", "neutral": "neutre", "unknown": "inconnu"}
DEFAULT_STOP_SHARE = 0.75  # part des pertes qui sont des stops francs, à défaut de mesure


def probability_margin(model, p: float | None) -> dict:
    """Marge d'erreur de la probabilité annoncée, d'après la calibration mesurée sur des données jamais vues."""
    table = ((getattr(model, "meta", None) or {}).get("calibration") or []) if model is not None else []
    if p is None or not table:
        return {"marge": None, "source": "non mesurée : la probabilité n'a pas encore été vérifiée sur des situations jamais vues"}
    row = min(table, key=lambda b: abs(b["p_mean"] - p))
    n = max(1, int(row["n"]))
    se = math.sqrt(max(row["win_rate"] * (1 - row["win_rate"]), 1e-6) / n)
    margin = min(0.5, abs(row["p_mean"] - row["win_rate"]) + 1.96 * se)
    return {"marge": margin, "annonce": row["p_mean"], "observe": row["win_rate"], "n": n,
            "source": f"calibration mesurée sur {n} situations jamais vues, annoncées autour de {row['p_mean'] * 100:.0f} %"}


def build_scenarios(strategy: str, p_win: float | None, model, cost_r: float, reward_r: float | None) -> list[dict]:
    """Trois scénarios qui se partagent 100 % : haussier (gain), baissier (stop touché), neutre (sortie proche de zéro)."""
    mix = ((getattr(model, "outcome_mix", None) or {}).get(strategy) or {}) if model is not None else {}
    stop_share = mix.get("stop_share_of_losses")
    stop_share = DEFAULT_STOP_SHARE if stop_share is None else float(stop_share)
    win_r = mix.get("mean_win") if mix.get("mean_win") is not None else reward_r
    p_bull = p_win
    p_bear = None if p_win is None else (1 - p_win) * stop_share
    p_flat = None if p_win is None else (1 - p_win) * (1 - stop_share)
    return [
        {"nom": "haussier", "probabilite": p_bull, "resultat_r": win_r,
         "description": "Le prix monte : l'objectif est atteint, ou le stop suiveur protège un gain."},
        {"nom": "baissier", "probabilite": p_bear, "resultat_r": -1.0 - cost_r,
         "description": "Le prix baisse jusqu'au stop : perte limitée au risque prévu, plus les frais."},
        {"nom": "neutre", "probabilite": p_flat, "resultat_r": -cost_r,
         "description": "Le prix ne décide rien : sortie au point mort ou à la durée maximale, on perd surtout les frais."},
    ]


def build_plan(*, symbol: str, strategy, close: float, stop: float, target: float | None, atr_pct: float | None, p_win: float | None,
               expected_r: float | None, threshold: float | None, model, mode: str | None, regime: str, btc_regime: str,
               cost_r: float, horizon_bars: int, timeframe_minutes: int, rd=None, equity: float | None = None, currency: str = "",
               decision: str = "refusé", reason: str = "") -> dict:
    risk_unit = close - stop
    reward_r = ((target - close) / risk_unit) if (target is not None and risk_unit > 0) else None
    reasons = [f"Signal « {strategy.label} » : {strategy.description}",
               f"Climat de cette crypto : {REGIMES.get(regime, regime)} ; climat du marché (Bitcoin) : {REGIMES.get(btc_regime, btc_regime)}."]
    if atr_pct is not None and math.isfinite(atr_pct):
        reasons.append(f"Volatilité : le prix bouge d'environ {atr_pct * 100:.1f} % par bougie.".replace(".", ",", 1))
    if model is not None and p_win is not None:
        tag = "IA à l'essai" if mode == "essai" else "IA"
        reasons.append(f"{tag} : {p_win * 100:.0f} % de chances de gain, espérance {expected_r:+.2f} R"
                       + (f" (seuil pour agir : {threshold:+.2f} R)." if threshold is not None and threshold > -1e6 else "."))
    else:
        reasons.append("Aucun modèle validé : aucune probabilité n'est annoncée (exploration à taille réduite, ou simple observation).")
    plan = {
        "decision": decision,
        "raison_decision": reason,
        "raisons": reasons,
        "entree": {"reference": close, "regle": "Au premier prix disponible après la clôture de la bougie du signal.",
                   "abandon_si": "Ce premier prix est déjà au niveau du stop ou en dessous : l'entrée est abandonnée."},
        "stop": {"prix": stop, "distance_pct": (risk_unit / close * 100) if close > 0 else None,
                 "invalidation": "Sous ce prix, la raison d'acheter ne tient plus : vente immédiate, sans attendre."},
        "objectif": ({"prix": target, "distance_pct": (target - close) / close * 100, "rapport_gain_risque": reward_r}
                     if target is not None else
                     {"prix": None, "rapport_gain_risque": None,
                      "regle": f"Pas d'objectif fixe : un stop suiveur ({strategy.trail_atr:g} fois l'amplitude moyenne sous le plus haut de clôture) laisse courir les gains."}),
        "duree_max": {"bougies": horizon_bars, "jours": horizon_bars * timeframe_minutes / 1440},
        "scenarios": build_scenarios(strategy.name, p_win if model is not None else None, model, cost_r, reward_r),
        "probabilite": {"gain": p_win if model is not None else None, **probability_margin(model, p_win if model is not None else None)},
        "esperance_r": expected_r if model is not None else None,
        "frais": {"en_r": cost_r, "montant": getattr(rd, "costs", None) if rd is not None and getattr(rd, "qty", 0) else None, "monnaie": currency,
                  "detail": "Frais de la plateforme, glissement de prix et conversion, pour l'achat et la vente."},
        "taille": None,
        "abandon": [
            "Le stop est touché (ou le prix ouvre dessous après un trou de cotation).",
            f"La durée maximale est atteinte ({horizon_bars} bougies) sans objectif ni stop.",
            "Un arrêt de sécurité du compte ou un arrêt d'urgence est déclenché.",
        ],
    }
    if rd is not None and getattr(rd, "qty", 0):
        plan["taille"] = {"quantite": rd.qty, "montant": rd.notional, "risque": rd.risk_amount, "monnaie": currency,
                          "risque_pct": (rd.risk_amount / equity * 100) if equity else None, "portee_au_minimum": bool(rd.bumped)}
    return plan
