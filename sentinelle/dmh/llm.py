"""Assistant de stratégie : IA 100 % LOCALE (Ollama sur cet ordinateur), réponse traitée comme une donnée.

- l'adresse doit être locale (127.0.0.1, localhost, ::1) : aucune IA distante n'est jamais appelée ;
- la réponse doit respecter un schéma strict ; un champ inconnu, un mode réel, une clé, une modification des
  limites ou des plafonds de risque, un ordre à exécuter : REFUS de toute la réponse (pas de tri silencieux) ;
- le résultat est un BROUILLON de stratégie : il ne déclenche rien. L'utilisateur le relit, demande une
  prévisualisation, puis confirme ; le moteur de sécurité garde son veto.
"""
from __future__ import annotations

import ipaddress
import json
import math
from urllib.parse import urlparse

from .guard import FORBIDDEN_FIELDS, scan_secrets
from .security import SecurityRefusal

ALLOWED = {"asset": str, "side": str, "capital_eur": (int, float), "objective_bps": (int, float), "max_loss_eur": (int, float),
           "horizon": str, "entry_conditions": str, "exit_conditions": str, "explanation": str}
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "asset": {"type": "string"}, "side": {"type": "string", "enum": ["buy"]}, "capital_eur": {"type": "number"},
    "objective_bps": {"type": "number"}, "max_loss_eur": {"type": "number"}, "horizon": {"type": "string"},
    "entry_conditions": {"type": "string"}, "exit_conditions": {"type": "string"}, "explanation": {"type": "string"}},
    "required": ["asset", "side", "capital_eur", "objective_bps", "horizon", "entry_conditions", "exit_conditions", "explanation"]}
SYSTEM = ("Tu aides à préparer une stratégie d'achat FICTIVE (simulation, argent fictif). Réponds uniquement "
          "en JSON selon le schéma. N'invente aucun prix, aucun rendement passé. L'objectif (objective_bps) est une "
          "hypothèse que l'utilisateur devra valider ; dis-le dans l'explication. Tu ne peux ni exécuter d'ordre, ni changer "
          "les limites de risque, ni passer en argent réel.")


def is_loopback_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_draft(obj, *, assets: set[str], max_capital: float) -> dict:
    if not isinstance(obj, dict):
        raise SecurityRefusal("réponse de l'IA illisible : refusée")
    lowered = {str(k).lower(): k for k in obj}
    bad = sorted(k for k in lowered if k in FORBIDDEN_FIELDS or k in ("mode", "limits", "limites", "absolute", "plafonds", "execute",
                                                                     "order", "orders", "confirm"))
    if bad:
        raise SecurityRefusal(f"réponse de l'IA refusée : champ interdit « {bad[0]} » (ordre réel, exécution ou limites)")
    extra = sorted(set(obj) - set(ALLOWED))
    if extra:
        raise SecurityRefusal(f"réponse de l'IA refusée : champ inattendu « {extra[0]} »")
    if scan_secrets(obj):
        raise SecurityRefusal("réponse de l'IA refusée : elle contient ce qui ressemble à un secret")
    out = {}
    for k, t in ALLOWED.items():
        if k not in obj:
            if k in SCHEMA["required"]:
                raise SecurityRefusal(f"réponse de l'IA incomplète : « {k} » manquant")
            continue
        v = obj[k]
        if not isinstance(v, t) or isinstance(v, bool):
            raise SecurityRefusal(f"réponse de l'IA refusée : « {k} » mal typé")
        if isinstance(v, (int, float)) and not math.isfinite(v):
            raise SecurityRefusal(f"réponse de l'IA refusée : « {k} » non fini")
        out[k] = v.strip()[:600] if isinstance(v, str) else float(v)
    if out["side"] != "buy":
        raise SecurityRefusal("réponse de l'IA refusée : seuls des achats fictifs (sans vente à découvert) sont possibles")
    if out["asset"].upper() not in assets:
        raise SecurityRefusal(f"réponse de l'IA refusée : actif « {out['asset']} » non pris en charge")
    out["asset"] = out["asset"].upper()
    if not 0 < out["capital_eur"] <= max_capital:
        raise SecurityRefusal(f"réponse de l'IA refusée : capital {out['capital_eur']} hors de ]0 ; {max_capital}]")
    if not -10_000 < out["objective_bps"] < 10_000:
        raise SecurityRefusal("réponse de l'IA refusée : objectif irréaliste")
    out["objective_is_hypothesis"] = True
    return out


def draft_strategy(message: str, *, url: str, model: str, assets: set[str], max_capital: float, initial_capital: float = 30.0,
                   current_balance: float | None = None, transport=None, timeout: float = 60.0) -> dict:
    if not is_loopback_url(url):
        raise SecurityRefusal("IA distante refusée : le cerveau de l'IA doit rester sur cet ordinateur")
    if scan_secrets({"message": message}):
        raise SecurityRefusal("message refusé : il contient ce qui ressemble à un secret")
    import httpx

    balance = max_capital if current_balance is None else current_balance
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0
           for x in (initial_capital, balance, max_capital)) or initial_capital <= 0:
        raise SecurityRefusal("contexte de capital fictif invalide")
    context = (f" Capital initial : {initial_capital:.2f} EUR fictifs. Solde actuel disponible, réservations déduites : "
               f"{balance:.2f} EUR fictifs. Budget maximal de cette proposition : {max_capital:.2f} EUR.")
    payload = {"model": model, "stream": False, "format": SCHEMA, "options": {"temperature": 0},
               "messages": [{"role": "system", "content": SYSTEM + context},
                            {"role": "user", "content": f"<demande>\n{message[:2000]}\n</demande>\nActifs possibles : {', '.join(sorted(assets))}."}]}
    try:
        with httpx.Client(timeout=timeout, transport=transport, trust_env=False) as c:
            r = c.post(f"{url.rstrip('/')}/api/chat", json=payload)
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "")
    except (httpx.HTTPError, ValueError) as exc:
        return {"available": False, "error": f"IA locale indisponible : {exc.__class__.__name__}", "draft": None}
    try:
        obj = json.loads(content)
    except ValueError:
        raise SecurityRefusal("réponse de l'IA illisible (JSON attendu) : refusée") from None
    return {"available": True, "draft": validate_draft(obj, assets=assets, max_capital=max_capital), "model": model, "source": "ia-locale"}
