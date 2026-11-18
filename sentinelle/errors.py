"""Erreurs communes à toute l'application (module sans dépendance)."""

from __future__ import annotations


class UserError(Exception):
    """Erreur à afficher telle quelle à l'utilisateur."""
