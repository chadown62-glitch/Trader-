"""Coffre des secrets (jeton Telegram, identifiants de lecture Telegram...).

Stockés uniquement sur votre ordinateur (data/secrets.json, exclu de Git).
Le panel ne renvoie jamais une clé en clair : seulement si elle est définie
et ses 4 derniers caractères.

Simulation uniquement : les anciennes clés API d'exchange éventuellement
présentes dans le fichier y restent (rien n'est effacé automatiquement), mais
`get()` ne les renvoie JAMAIS : aucune partie du programme ne peut les lire.
Vous pouvez les supprimer depuis les réglages.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# Anciennes clés d'exchange : conservées si elles existent, jamais lues ni utilisées.
LEGACY_IGNORED = {
    "exchange_api_key": "Ancienne clé API de l'exchange (ignorée)",
    "exchange_secret": "Ancien secret API de l'exchange (ignoré)",
    "exchange_password": "Ancien mot de passe API (ignoré)",
}

ALLOWED = {
    **LEGACY_IGNORED,
    "telegram_token": "Jeton du bot Telegram",
    "telegram_api_id": "api_id (my.telegram.org)",
    "telegram_api_hash": "api_hash (my.telegram.org)",
    "telegram_phone": "Numéro de téléphone Telegram (format +33…)",
}


class SecretStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def get(self, name: str) -> str | None:
        if name in LEGACY_IGNORED:
            return None  # simulation uniquement : une clé d'exchange ne sort jamais du fichier
        with self._lock:
            v = self._read().get(name)
        return v or None

    def has_any(self, names) -> bool:
        """Indique si l'un de ces secrets est présent, sans jamais en renvoyer la valeur."""
        with self._lock:
            data = self._read()
        return any(bool(data.get(n)) for n in names)

    def set(self, values: dict[str, str | None]) -> None:
        with self._lock:
            data = self._read()
            for k, v in values.items():
                if k not in ALLOWED:
                    raise ValueError(f"Secret inconnu : {k}")
                if v is None or v == "":
                    data.pop(k, None)
                else:
                    if len(v) > 500 or any(ch in v for ch in "\r\n"):
                        raise ValueError(f"Valeur invalide pour {ALLOWED[k]}")
                    data[k] = v.strip()
            self._write(data)

    def status(self) -> dict:
        with self._lock:
            data = self._read()
        out = {}
        for k, label in ALLOWED.items():
            ignored = k in LEGACY_IGNORED
            hint = ("…" + data[k][-4:]) if data.get(k) and len(data[k]) > 8 and not ignored else None
            out[k] = {"label": label, "set": bool(data.get(k)), "hint": hint, "ignored": ignored}
        return out
