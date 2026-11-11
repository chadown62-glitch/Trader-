"""Verrou « 100 % simulation ».

Pendant tout le développement, Sentinelle ne peut ni envoyer, ni modifier, ni
annuler un ordre réel. Ce verrou n'est **pas un réglage** :

- aucun paramètre, aucune variable d'environnement, aucune commande, aucune clé
  API, aucun compte validé et aucun bulletin favorable ne le désactive ;
- le code qui parlait à la partie privée d'un exchange (ordres, annulations,
  soldes) a été retiré du programme : il n'y a plus rien à « réactiver » ;
- ce module ajoute des refus explicites à chaque couche (courtier, client de
  marché, comptes, positions, agents, API, Telegram) pour qu'une erreur future
  dans une couche soit arrêtée par les autres.

Principe de sûreté : en cas de doute ou de panne d'un contrôle, l'opération est
REFUSÉE (« fail-closed »). Journaliser une tentative ne doit jamais empêcher
le refus, et le journal ne contient jamais de secret.
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from .errors import UserError

log = logging.getLogger("sentinelle.securite")

# Constante documentaire. Aucun code ne lit une valeur pour « ouvrir » le verrou :
# les refus ci-dessous ne dépendent d'aucune condition modifiable.
PAPER_ONLY = True

LOCK_MESSAGE = ("Mode simulation uniquement : cette version de Sentinelle ne peut ni envoyer, ni modifier, "
                "ni annuler un ordre réel. Seul l'argent fictif est utilisé.")

# Mots qui signalent une valeur à ne jamais écrire dans un journal
_SENSITIVE = ("key", "secret", "password", "passphrase", "token", "cle", "clé", "mot_de_passe")


class RealTradingForbidden(UserError):
    """Refus de sécurité : opération qui toucherait de l'argent réel."""

    code = "paper_only"

    def __init__(self, operation: str):
        self.operation = operation
        self.message = f"{LOCK_MESSAGE} (opération refusée : {operation})"
        super().__init__(self.message)


def _clean(context: dict[str, Any]) -> dict[str, Any]:
    """Contexte journalisable : valeurs simples, tronquées, sans rien qui ressemble à un secret."""
    out: dict[str, Any] = {}
    for k, v in context.items():
        name = str(k).lower()
        if any(word in name for word in _SENSITIVE):
            continue
        if isinstance(v, bool) or v is None:
            out[str(k)] = v
        elif isinstance(v, (int, float)):
            out[str(k)] = v
        else:
            out[str(k)] = str(v)[:80]
    return out


def forbid(operation: str, *, db=None, **context: Any) -> NoReturn:
    """Journalise une tentative interdite puis lève TOUJOURS `RealTradingForbidden`.

    `context` ne doit contenir que des informations non sensibles (numéro de compte,
    paire…). Par précaution, toute entrée dont le nom évoque un secret est écartée.
    """
    try:
        ctx = _clean(context)
        log.warning("Opération réelle refusée : %s %s", operation, ctx or "")
        if db is not None:
            db.log("warning", "sécurité", f"Tentative refusée (simulation uniquement) : {operation}", ctx or None)
    except Exception:  # un journal en panne ne doit jamais laisser passer l'opération
        pass
    raise RealTradingForbidden(operation)


def is_paper_broker(broker: Any) -> bool:
    """Vrai uniquement pour le courtier simulé exact (ni sous-classe, ni imitation)."""
    try:
        from .execution.paper import PaperBroker

        return type(broker) is PaperBroker and broker.kind == "paper"
    except Exception:
        return False


def is_paper_account(account: Any) -> bool:
    try:
        return account["kind"] == "paper"
    except Exception:
        return False


def is_paper_runtime(rt: Any) -> bool:
    """Un compte actif n'est utilisable que si TOUT est simulé et cohérent."""
    try:
        return (
            is_paper_account(rt.account)
            and is_paper_broker(rt.broker)
            and rt.positions.broker is rt.broker
            and int(rt.broker.account_id) == int(rt.account["id"])
        )
    except Exception:
        return False


def require_paper_broker(broker: Any, operation: str, db=None) -> None:
    if not is_paper_broker(broker):
        forbid(operation, db=db, courtier=type(broker).__name__)


def require_paper_account(account: Any, operation: str, db=None) -> None:
    if not is_paper_account(account):
        kind = aid = None
        try:
            kind, aid = account["kind"], account["id"]
        except Exception:
            pass
        forbid(operation, db=db, compte=aid, type_de_compte=kind)


def require_paper_runtime(rt: Any, operation: str, db=None) -> None:
    if not is_paper_runtime(rt):
        aid = kind = None
        try:
            aid, kind = rt.account["id"], rt.account["kind"]
        except Exception:
            pass
        forbid(operation, db=db, compte=aid, type_de_compte=kind, courtier=type(getattr(rt, "broker", None)).__name__)
