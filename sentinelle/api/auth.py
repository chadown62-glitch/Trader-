"""Authentification du panel : mot de passe, sessions, protection anti-essais.

- mot de passe haché avec scrypt (jamais stocké en clair) ;
- session dans un cookie HttpOnly + SameSite=Strict ;
- les actions qui modifient quelque chose exigent un en-tête spécifique
  (protection contre les requêtes forgées depuis un autre site) ;
- 5 essais ratés en 15 minutes : blocage de 15 minutes ;
- la création du mot de passe (premier lancement) n'est possible que depuis
  l'ordinateur lui-même.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from ..storage.db import Database

COOKIE = "stn_session"
CSRF_HEADER = "x-sentinelle"
SESSION_DAYS = 7
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Le mot de passe doit contenir au moins 10 caractères"
    if len(password) > 200:
        return "Mot de passe trop long"
    if password.lower() in {"motdepasse", "password12", "azertyuiop", "0123456789", "1234567890"}:
        return "Mot de passe trop courant"
    return None


class Auth:
    def __init__(self, db: Database):
        self.db = db
        self._fails: dict[str, list[float]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.db.get_meta("auth_hash"))

    def set_password(self, password: str) -> None:
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        self.db.set_meta("auth_hash", hash_password(password))
        self.db.execute("DELETE FROM sessions")  # déconnecte toutes les sessions existantes

    def locked(self, ip: str) -> float:
        now = time.time()
        fails = [t for t in self._fails.get(ip, []) if now - t < LOCK_SECONDS]
        self._fails[ip] = fails
        if len(fails) >= MAX_FAILS:
            return LOCK_SECONDS - (now - fails[-MAX_FAILS])
        return 0.0

    def check(self, password: str, ip: str) -> bool:
        if self.locked(ip) > 0:
            return False
        ok = verify_password(password, self.db.get_meta("auth_hash") or "")
        if not ok:
            self._fails.setdefault(ip, []).append(time.time())
        else:
            self._fails.pop(ip, None)
        return ok

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.insert("sessions", {"token_hash": hashlib.sha256(token.encode()).hexdigest(), "created_at": now,
                                    "expires_at": now + SESSION_DAYS * 86400, "last_seen": now})
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        h = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.one("SELECT * FROM sessions WHERE token_hash = ?", (h,))
        now = time.time()
        if not row or row["expires_at"] < now:
            return False
        if now - row["last_seen"] > 60:
            self.db.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                            (now, now + SESSION_DAYS * 86400, h))
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
