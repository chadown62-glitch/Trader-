"""Coffre des secrets (clés API de l'exchange, jeton Telegram...).

Stockés uniquement sur votre ordinateur (data/secrets.json, exclu de Git).
Le panel ne renvoie jamais une clé en clair : seulement si elle est définie
et ses 4 derniers caractères.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ALLOWED = {
    "exchange_api_key": "Clé API de l'exchange",
    "exchange_secret": "Secret API de l'exchange",
    "exchange_password": "Mot de passe API (certains exchanges)",
    "telegram_token": "Jeton du bot Telegram",
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
        with self._lock:
            v = self._read().get(name)
        return v or None

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
        return {k: {"label": label, "set": bool(data.get(k)), "hint": ("…" + data[k][-4:]) if data.get(k) and len(data[k]) > 8 else None}
                for k, label in ALLOWED.items()}
