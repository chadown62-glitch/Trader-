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

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

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


def totp(secret_b32: str, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    """Code à usage unique (RFC 6238), compatible Google Authenticator, Authy, etc."""
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = int((for_time if for_time is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


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

    # -- double authentification (facultative) ---------------------------------
    @property
    def totp_enabled(self) -> bool:
        return bool(self.db.get_meta("totp_secret"))

    def totp_setup(self) -> dict:
        secret = base64.b32encode(os.urandom(20)).decode().rstrip("=")
        self.db.set_meta("totp_pending", secret)
        uri = f"otpauth://totp/{quote('Sentinelle:panel')}?secret={secret}&issuer=Sentinelle"
        return {"secret": secret, "uri": uri}

    def totp_check(self, code: str, secret: str | None = None) -> bool:
        secret = secret or self.db.get_meta("totp_secret")
        code = (code or "").strip().replace(" ", "")
        if not secret or not code.isdigit():
            return False
        now = time.time()
        for drift in (-30, 0, 30):  # tolérance d'horloge : ±30 secondes
            step = int((now + drift) // 30)
            if hmac.compare_digest(totp(secret, now + drift), code):
                last = int(self.db.get_meta("totp_last_step", "0") or 0)
                if step <= last:
                    return False  # code déjà utilisé
                self.db.set_meta("totp_last_step", str(step))
                return True
        return False

    def totp_enable(self, code: str) -> bool:
        pending = self.db.get_meta("totp_pending")
        if not pending or not self.totp_check(code, pending):
            return False
        self.db.set_meta("totp_secret", pending)
        self.db.execute("DELETE FROM meta WHERE key = 'totp_pending'")
        return True

    def totp_disable(self) -> None:
        self.db.execute("DELETE FROM meta WHERE key IN ('totp_secret', 'totp_pending')")

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.insert("sessions", {"token_hash": hashlib.sha256(token.encode()).hexdigest(), "created_at": now,
                                    "expires_at": now + SESSION_DAYS * 86400, "last_seen": now})
        return token

    def valid(self, token: str | None, *, refresh: bool = True) -> bool:
        if not token:
            return False
        h = hashlib.sha256(token.encode()).hexdigest()
        if refresh:
            row = self.db.one("SELECT * FROM sessions WHERE token_hash = ?", (h,))
        else:
            # The preflight GET must not renew sessions or change the installation's WAL index.
            import tempfile
            import sqlite3
            from pathlib import Path
            from ..preflight import _read_only
            try:
                with tempfile.TemporaryDirectory(prefix='sentinelle-auth-read-') as scratch:
                    conn=_read_only(self.db.path,Path(scratch))
                    try:row=conn.execute("SELECT * FROM sessions WHERE token_hash = ?",(h,)).fetchone()
                    finally:conn.close()
            except (OSError,ValueError,sqlite3.Error):return False
        now = time.time()
        if not row or row["expires_at"] < now:
            return False
        if refresh and now - row["last_seen"] > 60:
            self.db.execute("UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
                            (now, now + SESSION_DAYS * 86400, h))
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
