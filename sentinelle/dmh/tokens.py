"""Identité des actifs : un token est (réseau, adresse du contrat ou du mint), JAMAIS son seul symbole.

- deux réseaux peuvent porter un token de même symbole : ce sont deux identités distinctes ;
- un symbole qui ressemble à celui d'un token vérifié (casse, caractères cyrilliques ou grecs, espaces,
  chiffres à la place de lettres) mais qui pointe vers une autre adresse est traité comme une IMITATION ;
- un token absent du registre vérifié est INCONNU, donc jamais sûr par défaut : aucune opération ;
- les versions « enveloppées » d'un actif d'un autre réseau (ex. BTC enveloppé) ne sont pas l'actif lui-même :
  elles portent un risque de pont ou de dépositaire et ne sont pas routées dans cette version.

Le registre vérifié (`research/market-hub-v1/token-registry.json`) est produit par `tools/dmh_tokens.py` à partir
de sources indépendantes (lecture sur la chaîne, catalogues publics) ; son empreinte est contrôlée au chargement.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

NETWORKS = {"ethereum": {"kind": "evm", "chain_id": 1, "native": "ETH"},
            "base": {"kind": "evm", "chain_id": 8453, "native": "ETH"},
            "solana": {"kind": "solana", "chain_id": None, "native": "SOL"}}
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_EVM = re.compile(r"^0x[0-9a-f]{40}$")
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "research" / "market-hub-v1" / "token-registry.json"


class TokenError(ValueError):
    pass


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        i = B58.find(ch)
        if i < 0:
            raise TokenError("caractère base58 invalide")
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + raw


@dataclass(frozen=True, order=True)
class TokenId:
    network: str
    address: str

    def __post_init__(self):
        net = NETWORKS.get(self.network)
        if net is None:
            raise TokenError(f"réseau inconnu : {self.network!r}")
        if net["kind"] == "evm":
            a = str(self.address).lower()
            if not _EVM.fullmatch(a) or a == "0x" + "0" * 40:
                raise TokenError(f"adresse EVM invalide sur {self.network}")
            object.__setattr__(self, "address", a)
        else:
            a = str(self.address)
            if not 32 <= len(a) <= 44 or len(b58decode(a)) != 32:
                raise TokenError("mint Solana invalide (32 octets en base58 attendus)")

    @property
    def key(self) -> str:
        return f"{self.network}:{self.address}"

    @classmethod
    def parse(cls, key: str) -> "TokenId":
        network, _, address = str(key).partition(":")
        return cls(network, address)


_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h",
    "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "А": "a", "В": "b", "Е": "e", "К": "k", "М": "m", "Н": "h", "О": "o",
    "Р": "p", "С": "c", "Т": "t", "Х": "x", "У": "y", "І": "i", "Ѕ": "s", "Ј": "j",
    "α": "a", "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "ν": "v", "ι": "i", "κ": "k", "Α": "a", "Β": "b", "Ε": "e", "Ζ": "z",
    "Η": "h", "Ι": "i", "Κ": "k", "Μ": "m", "Ν": "n", "Ο": "o", "Ρ": "p", "Τ": "t", "Υ": "y", "Χ": "x",
    "0": "o", "1": "l", "5": "s", "$": "s", "€": "e",
})


def skeleton(symbol: str) -> str:
    """Forme canonique d'un symbole pour repérer les imitations (« USDС » cyrillique, « U S D C », « usdc.e »…)."""
    s = unicodedata.normalize("NFKC", str(symbol)).translate(_CONFUSABLES).lower()
    s = "".join(ch for ch in s if ch.isalnum())
    return s.replace("i", "l")


@dataclass(frozen=True)
class TokenInfo:
    id: TokenId
    symbol: str
    decimals: int
    asset: str  # actif logique (BTC, ETH, SOL, USDC…) ; un actif enveloppé garde son propre nom
    status: str  # VERIFIED | UNVERIFIED | BLOCKED
    sources: tuple[dict, ...] = field(default=(), compare=False)
    notes: str = ""
    accepted_flags: tuple[str, ...] = ()  # pouvoirs connus et acceptés d'un émetteur régulé (affichés, jamais cachés)

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"


class TokenRegistry:
    def __init__(self, tokens: list[TokenInfo], meta: dict | None = None):
        self.meta = meta or {}
        self._by_id: dict[TokenId, TokenInfo] = {}
        for t in tokens:
            if t.id in self._by_id:
                raise TokenError(f"identité en double dans le registre : {t.id.key}")
            if not 0 <= t.decimals <= 36:
                raise TokenError(f"décimales invalides pour {t.id.key}")
            self._by_id[t.id] = t
        seen: dict[tuple[str, str], TokenId] = {}
        for t in tokens:
            if t.verified:
                k = (t.id.network, skeleton(t.symbol))
                if k in seen:
                    raise TokenError(f"deux tokens vérifiés au symbole indiscernable sur {t.id.network} : {t.symbol}")
                seen[k] = t.id

    def __len__(self) -> int:
        return len(self._by_id)

    def all(self) -> list[TokenInfo]:
        return sorted(self._by_id.values(), key=lambda t: (t.id.network, t.symbol))

    def get(self, token: TokenId) -> TokenInfo | None:
        return self._by_id.get(token)

    def require(self, token: TokenId) -> TokenInfo:
        info = self._by_id.get(token)
        if info is None:
            raise TokenError(f"token inconnu ({token.key}) : jamais considéré comme sûr")
        if not info.verified:
            raise TokenError(f"token non vérifié ({info.symbol} sur {token.network}) : statut {info.status}")
        return info

    def resolve(self, network: str, symbol: str) -> TokenInfo:
        """Un symbole n'est accepté que s'il désigne exactement un token vérifié sur ce réseau précis."""
        if network not in NETWORKS:
            raise TokenError(f"réseau inconnu : {network!r}")
        sk = skeleton(symbol)
        matches = [t for t in self._by_id.values() if t.id.network == network and skeleton(t.symbol) == sk]
        verified = [t for t in matches if t.verified]
        if len(verified) != 1:
            raise TokenError(f"« {symbol} » ne désigne aucun token vérifié unique sur {network} : préciser l'adresse")
        if verified[0].symbol != symbol and symbol.upper() != verified[0].symbol.upper():
            raise TokenError(f"« {symbol} » imite le symbole de {verified[0].symbol} sur {network}")
        return verified[0]

    def impersonation(self, token: TokenId, symbol: str) -> TokenInfo | None:
        """Token vérifié que ce couple (adresse, symbole) imiterait : même apparence, autre adresse, même réseau."""
        sk = skeleton(symbol)
        for t in self._by_id.values():
            if t.verified and t.id.network == token.network and skeleton(t.symbol) == sk and t.id != token:
                return t
        return None

    def by_asset(self, asset: str, network: str) -> TokenInfo | None:
        hits = [t for t in self._by_id.values() if t.verified and t.asset == asset and t.id.network == network]
        return hits[0] if len(hits) == 1 else None


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_registry(path: Path = REGISTRY_PATH) -> TokenRegistry:
    """Charge le registre vérifié ; une empreinte absente ou différente le rend inutilisable (aucun token sûr)."""
    path = Path(path)
    digest_file = path.with_suffix(".json.sha256")
    if not path.exists() or not digest_file.exists():
        return TokenRegistry([], {"status": "ABSENT", "path": str(path)})
    expected = digest_file.read_text(encoding="utf-8").split()[0]
    if file_sha256(path) != expected:
        raise TokenError("registre des tokens modifié sans nouvelle empreinte : refusé")
    raw = json.loads(path.read_text(encoding="utf-8"))
    tokens = [TokenInfo(TokenId(t["network"], t["address"]), t["symbol"], int(t["decimals"]), t["asset"], t["status"],
                        tuple(t.get("sources", ())), t.get("notes", ""), tuple(t.get("accepted_flags", ()))) for t in raw["tokens"]]
    return TokenRegistry(tokens, {k: v for k, v in raw.items() if k != "tokens"} | {"sha256": expected})
