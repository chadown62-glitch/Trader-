"""Ancien « portefeuille réel » : désormais inerte (simulation uniquement).

Ce module ne se connecte plus à aucun exchange. Il garde trois rôles :
- décrire l'état du verrou pour le panel (`paper_only_status`) ;
- écarter de la mémoire tout compte actif qui ne serait pas 100 % simulé ;
- conserver les anciens points d'entrée (`_connect_live`, `_poll_live`…) sous forme
  de coquilles vides, pour qu'aucun appel resté ailleurs ne puisse agir.

Les anciens comptes réels, leurs positions, leurs ordres et les anciennes clés API
restent dans les données locales : rien n'est effacé, mais rien n'est utilisé.
"""

from __future__ import annotations

import logging

# Témoin de sécurité : cette fonction n'est PLUS appelée par ce module. Le nom est gardé ici
# pour que les tests de sécurité puissent vérifier qu'aucun client d'exchange n'est créé
# au démarrage, à la reconnexion ou lors d'un changement de réglages.
from ..market.sources import make_ccxt_exchange  # noqa: F401
from ..safety import LOCK_MESSAGE, forbid, is_paper_runtime
from .common import AccountRuntime

log = logging.getLogger("sentinelle.securite")

LEGACY_KEYS = ("exchange_api_key", "exchange_secret", "exchange_password")


class LiveMixin:
    """Méthodes de l'application : état du verrou « simulation uniquement » et anciens comptes réels (inertes)."""

    # ======================================================================
    # État du verrou
    # ======================================================================
    def legacy_accounts(self) -> list[dict]:
        """Anciens comptes non simulés encore présents dans les données (conservés, jamais utilisés)."""
        return self.db.query("SELECT id, name, kind, role, created_at FROM accounts WHERE kind != 'paper' AND archived = 0 ORDER BY id")

    def live_account(self) -> dict | None:
        return self.db.one("SELECT * FROM accounts WHERE kind != 'paper' AND archived = 0 ORDER BY id")

    def paper_only_status(self) -> dict:
        legacy = []
        for acc in self.legacy_accounts():
            legacy.append({
                **acc,
                "open_positions": int(self.db.scalar(
                    "SELECT COUNT(*) FROM positions WHERE account_id = ? AND status IN ('pending', 'open')", (acc["id"],), 0)),
                "open_orders": int(self.db.scalar(
                    "SELECT COUNT(*) FROM orders WHERE account_id = ? AND status IN ('new', 'open', 'partially_filled')", (acc["id"],), 0)),
            })
        return {
            "paper_only": True,
            "message": LOCK_MESSAGE,
            "legacy_accounts": legacy,
            "legacy_keys": self.secrets.has_any(LEGACY_KEYS),
            "legacy_note": ("D'anciens ordres ou positions réels peuvent encore exister sur votre plateforme d'échange : "
                            "Sentinelle ne les voit plus et ne les gère plus. Vérifiez-les directement sur le site de la plateforme."
                            if legacy else None),
        }

    # ======================================================================
    # Comptes actifs : uniquement simulés
    # ======================================================================
    def paper_runtimes(self) -> list[tuple[int, AccountRuntime]]:
        """Comptes actifs vérifiés. Tout compte actif qui n'est pas 100 % simulé est écarté et journalisé."""
        out = []
        for aid, rt in list(self.runtimes.items()):
            if is_paper_runtime(rt):
                out.append((aid, rt))
            else:
                self._evict_runtime(aid, "compte actif non simulé écarté")
        return out

    def _evict_runtime(self, account_id, why: str) -> None:
        rt = self.runtimes.pop(account_id, None)
        self.snapshots.pop(account_id, None)
        kind = broker = None
        try:
            kind = rt.account["kind"]
            broker = type(rt.broker).__name__
        except Exception:
            pass
        try:
            log.warning("Sécurité : %s (compte %s, type %s, courtier %s)", why, account_id, kind, broker)
            self.db.log("warning", "sécurité", f"Simulation uniquement : {why}",
                        {"compte": account_id, "type_de_compte": kind, "courtier": broker})
        except Exception:
            pass

    # ======================================================================
    # Anciens points d'entrée : coquilles vides
    # ======================================================================
    def _connect_live(self) -> dict:
        """Ne se connecte jamais. Renvoie l'état « verrouillé »."""
        self.paper_runtimes()
        self.live_status = {"connected": False, "locked": True, "error": LOCK_MESSAGE}
        return self.live_status

    def reconnect_live(self) -> dict:
        self._connect_live()
        forbid("connexion au portefeuille réel", db=self.db)

    def _poll_live(self) -> None:
        self.paper_runtimes()

    def _sync_live(self) -> None:
        self.paper_runtimes()

    def _reconcile_live(self) -> None:
        self.paper_runtimes()
