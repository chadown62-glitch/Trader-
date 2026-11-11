"""Ancien courtier réel : RETIRÉ. Ce module ne contient plus aucun code d'exécution.

Pendant tout le développement, Sentinelle fonctionne exclusivement en argent
fictif (voir `sentinelle/safety.py` et `docs/SECURITE.md`). Le code qui envoyait,
suivait ou annulait des ordres chez un exchange a été supprimé de ce fichier :
il n'existe plus aucun appel à la partie privée de CCXT dans le programme.

La classe `LiveBroker` est conservée uniquement comme **butée de sécurité** :
- elle ne peut pas être construite ;
- si un objet de cette classe existait malgré tout (créé sans passer par le
  constructeur, ou conservé en mémoire par une ancienne version), chacune de ses
  méthodes refuse d'agir, sans jamais toucher à l'objet « exchange » qu'il porte.

Il n'y a ici aucune condition, aucun réglage et aucune variable d'environnement :
le refus est inconditionnel.
"""

from __future__ import annotations

from ..safety import forbid


class LiveBroker:
    """Butée de sécurité : toute utilisation est refusée (simulation uniquement)."""

    kind = "live"

    def __init__(self, *args, **kwargs):
        forbid("création d'un courtier réel")

    # -- lecture du compte chez l'exchange ---------------------------------------
    def fetch_balance(self, *args, **kwargs):
        forbid("lecture du solde réel")

    def balances(self, *args, **kwargs):
        forbid("lecture du solde réel")

    def available(self, *args, **kwargs):
        forbid("lecture du solde réel")

    def equity(self, *args, **kwargs):
        forbid("lecture du solde réel")

    # -- ordres --------------------------------------------------------------------
    def place_order(self, *args, **kwargs):
        forbid("envoi d'un ordre réel")

    def cancel_order(self, *args, **kwargs):
        forbid("annulation d'un ordre réel")

    def open_orders(self, *args, **kwargs):
        forbid("lecture des ordres réels")

    def get_order(self, *args, **kwargs):
        forbid("lecture d'un ordre réel")

    # -- suivi -----------------------------------------------------------------------
    def sync(self, *args, **kwargs):
        forbid("synchronisation avec l'exchange")

    def process_bar(self, *args, **kwargs):
        forbid("déclenchement d'un objectif réel")

    def reconcile(self, *args, **kwargs):
        forbid("réconciliation avec l'exchange")

    # -- anciens détails internes : refusés eux aussi ------------------------------
    def _call(self, *args, **kwargs):
        forbid("appel privé à l'exchange")

    def _find_by_client_id(self, *args, **kwargs):
        forbid("appel privé à l'exchange")

    def _apply(self, *args, **kwargs):
        forbid("mise à jour d'un ordre réel")

    def _invalidate(self, *args, **kwargs):
        forbid("lecture du solde réel")
