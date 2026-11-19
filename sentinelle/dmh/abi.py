"""Keccak-256 et encodage ABI minimal pour des appels **en lecture seule** (`eth_call`).

Les sélecteurs de fonctions sont calculés à partir de leur signature (keccak-256), jamais recopiés à la main.
Aucune fonction de ce module ne signe quoi que ce soit : il n'y a ici ni clé, ni transaction.
"""
from __future__ import annotations

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _f(a: list[list[int]]) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def _sponge(data: bytes, pad: int) -> bytes:
    rate = 136
    msg = bytearray(data) + bytes([pad])
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            a[x][y] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _f(a)
    return b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))


def keccak256(data: bytes) -> bytes:
    """Keccak-256 d'Ethereum (remplissage 0x01, différent de SHA3-256 normalisé, qui utilise 0x06)."""
    return _sponge(data, 0x01)


def selector(signature: str) -> str:
    """Sélecteur de 4 octets (hexadécimal) d'une signature canonique, ex. « decimals() »."""
    return keccak256(signature.encode()).hex()[:8]


def _word(value: int) -> str:
    if value < 0 or value >= 1 << 256:
        raise ValueError("entier hors de l'intervalle uint256")
    return f"{value:064x}"


def address_word(address: str) -> str:
    a = address.lower()
    if not (a.startswith("0x") and len(a) == 42 and all(ch in "0123456789abcdef" for ch in a[2:])):
        raise ValueError(f"adresse EVM invalide : {address!r}")
    return "0" * 24 + a[2:]


def encode_call(signature: str, args: list[tuple[str, object]]) -> str:
    """Encode un appel à arguments statiques (address, uint*, bool) ; un tuple statique s'encode comme ses champs."""
    words = []
    for kind, value in args:
        if kind == "address":
            words.append(address_word(str(value)))
        elif kind.startswith("uint"):
            words.append(_word(int(value)))  # type: ignore[arg-type]
        elif kind == "bool":
            words.append(_word(1 if value else 0))
        else:
            raise ValueError(f"type ABI non pris en charge : {kind}")
    return "0x" + selector(signature) + "".join(words)


def decode_words(result: str, count: int) -> list[int]:
    """Découpe une réponse `eth_call` en mots de 32 octets ; refuse une réponse vide ou trop courte."""
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ValueError("réponse eth_call malformée")
    body = result[2:]
    if len(body) < 64 * count or len(body) % 64:
        raise ValueError(f"réponse eth_call trop courte ({len(body) // 2} octets pour {count} valeurs)")
    return [int(body[64 * i:64 * i + 64], 16) for i in range(count)]


def decode_string(result: str) -> str:
    """Décode une chaîne ABI dynamique (ou un bytes32 historique, ex. le symbole de MKR)."""
    body = result[2:] if isinstance(result, str) and result.startswith("0x") else ""
    if len(body) == 64:  # bytes32
        return bytes.fromhex(body).rstrip(b"\x00").decode("utf-8", "replace")
    if len(body) < 128:
        raise ValueError("chaîne ABI malformée")
    offset = int(body[:64], 16) * 2
    length = int(body[offset:offset + 64], 16)
    raw = bytes.fromhex(body[offset + 64:offset + 64 + 2 * length])
    if len(raw) != length:
        raise ValueError("chaîne ABI tronquée")
    return raw.decode("utf-8", "replace")
