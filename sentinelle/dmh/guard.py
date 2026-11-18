"""Verrou « argent fictif uniquement » du hub, en refus par défaut (fail-closed).

- `inspect_request` : toute demande qui n'est pas explicitement fictive, ou qui contient un champ lié au réel
  (mode réel, signature, diffusion, portefeuille, clé, levier, vente à découvert, emprunt), est REFUSÉE ;
- `scan_secrets` / `assert_no_secrets` : une configuration qui contient une clé privée, une phrase de
  récupération ou une clé d'API est REFUSÉE ; le message ne reprend jamais la valeur trouvée ;
- `check_runtime` : vérifie, avant chaque opération, que rien n'a ouvert le verrou (constantes, classe exacte
  de l'exécuteur) ; une erreur pendant la vérification vaut refus.

Les refus passent par `sentinelle.safety.forbid` : ils sont journalisés et deviennent un 403 dans l'API.
"""
from __future__ import annotations

import re
from typing import Any, NoReturn

from .. import safety
from . import CAN_BROADCAST, CAN_SIGN, PAPER_ONLY

PAPER_MODE = "paper"

# Champs qui n'ont rien à faire dans une demande fictive : leur seule présence (même vide) est refusée.
FORBIDDEN_FIELDS = frozenset({
    "live", "real", "real_money", "mainnet_order", "broadcast", "sign", "signature", "signed_transaction", "raw_transaction",
    "private_key", "privatekey", "secret_key", "seed", "seed_phrase", "mnemonic", "passphrase", "keystore", "wallet_secret",
    "api_key", "apikey", "api_secret", "secret", "taker", "payer", "wallet", "wallet_address", "spender", "approve",
    "leverage", "margin", "short", "borrow", "loan", "withdraw", "bridge",
})

_HEX64 = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")
_WIF = re.compile(r"^[5KL][1-9A-HJ-NP-Za-km-z]{50,51}$")
_XPRV = re.compile(r"^[xyzt]prv[1-9A-HJ-NP-Za-km-z]{100,112}$")
_MNEMONIC = re.compile(r"^[a-z]{3,8}( [a-z]{3,8}){11,23}$")
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_SENSITIVE_NAME = re.compile(r"(private|secret|seed|mnemonic|passphrase|keystore|api_?key|apikey|password)", re.I)
_HASH_NAME = re.compile(r"(sha256|hash|digest|checksum|tx_?id|request_?id|block)$", re.I)
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58_len(text: str) -> int | None:
    n = 0
    for ch in text:
        i = _B58_ALPHABET.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return len(raw) + len(text) - len(text.lstrip("1"))


def _value_kind(name: str, value: Any) -> str | None:
    """Type de secret reconnu dans une valeur, ou None. Ne renvoie jamais la valeur elle-même."""
    if isinstance(value, list) and len(value) == 64 and all(type(x) is int and 0 <= x <= 255 for x in value):
        return "clé privée Solana (fichier de paire de clés)"
    if not isinstance(value, str):
        return None
    v = value.strip()
    if _PEM.search(v):
        return "clé privée PEM"
    if _MNEMONIC.fullmatch(v) and len(v.split()) in (12, 15, 18, 21, 24):
        return "phrase de récupération"
    if _XPRV.fullmatch(v):
        return "clé privée étendue"
    if _WIF.fullmatch(v):
        return "clé privée au format WIF"
    if _HEX64.fullmatch(v) and not _HASH_NAME.search(name):
        return "clé privée hexadécimale (64 caractères)"
    if 80 <= len(v) <= 90 and _B58.fullmatch(v) and _b58_len(v) == 64:
        return "clé privée Solana (base58)"
    return None


def scan_secrets(obj: Any, path: str = "") -> list[dict]:
    """Liste les emplacements suspects (chemin et nature), sans jamais reproduire la valeur."""
    found: list[dict] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            name = str(k)
            if _SENSITIVE_NAME.search(name) and v not in (None, "", [], {}, False) and not _HASH_NAME.search(name):
                found.append({"path": p, "kind": "champ sensible renseigné"})
                continue
            kind = _value_kind(name, v)
            if kind:
                found.append({"path": p, "kind": kind})
            elif isinstance(v, (dict, list)):
                found += scan_secrets(v, p)
    elif isinstance(obj, list):
        kind = _value_kind(path.rsplit(".", 1)[-1], obj)
        if kind:
            return [{"path": path, "kind": kind}]
        for i, v in enumerate(obj):
            p = f"{path}[{i}]"
            kind = _value_kind(path.rsplit(".", 1)[-1], v)
            if kind:
                found.append({"path": p, "kind": kind})
            elif isinstance(v, (dict, list)):
                found += scan_secrets(v, p)
    else:
        kind = _value_kind(path, obj)
        if kind:
            found.append({"path": path or "(valeur)", "kind": kind})
    return found


def assert_no_secrets(obj: Any, what: str = "configuration", db=None) -> None:
    findings = scan_secrets(obj)
    if findings:
        where = ", ".join(f"{f['path']} ({f['kind']})" for f in findings[:5])
        safety.forbid(f"{what} contenant un secret : {where}", db=db)


def _walk_keys(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield str(k), p, v
            yield from _walk_keys(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{i}]")


def inspect_request(body: Any, operation: str, db=None) -> None:
    """Refuse toute demande qui n'est pas explicitement fictive, ou qui porte un champ lié au réel."""
    if not isinstance(body, dict):
        safety.forbid(f"{operation} : demande illisible", db=db)
    if body.get("mode") != PAPER_MODE:
        safety.forbid(f"{operation} : mode « {str(body.get('mode'))[:20]} » (seul le mode fictif existe)", db=db)
    for key, path, _ in _walk_keys(body):
        if key.lower().replace("-", "_") in FORBIDDEN_FIELDS:
            safety.forbid(f"{operation} : champ interdit « {path} »", db=db)
    assert_no_secrets(body, operation, db=db)


def check_runtime(executor: Any = None) -> None:
    """Contrôle de toutes les couches avant une opération ; toute anomalie (ou erreur) vaut refus."""
    try:
        from .simulate import PaperExecutor
        ok = (safety.PAPER_ONLY is True and PAPER_ONLY is True and CAN_SIGN is False and CAN_BROADCAST is False
              and (executor is None or (type(executor) is PaperExecutor and executor.kind == PAPER_MODE)))
    except Exception:
        ok = False
    if not ok:
        safety.forbid("exécution hors du simulateur fictif du hub")


def refuse(operation: str, db=None, **context: Any) -> NoReturn:
    safety.forbid(operation, db=db, **context)
