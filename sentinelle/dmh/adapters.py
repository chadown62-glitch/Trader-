"""Adaptateurs vers les contrats multi-marchés de la PR #47 (`sentinelle/architecture/contracts.py`, autre lignée).

La PR #47 n'est pas dans la lignée de ce hub : rien n'est importé ni fusionné. Ces fonctions produisent des
dictionnaires au MÊME schéma et appliquent les mêmes règles (identité ≠ symbole ; un memecoin exige son réseau ET
son identifiant unique ; heures UTC en millisecondes ; une information reçue après la décision est refusée), pour
qu'une intégration ultérieure, après revue, se fasse sans conversion ambiguë.
"""
from __future__ import annotations

from .assumptions import MEMECOINS
from .observe import Book
from .tokens import TokenInfo

_UTC_MS_MIN, _UTC_MS_MAX = 631_152_000_000, 4_102_444_800_000


def asset_ref_onchain(info: TokenInfo) -> dict:
    family = "memecoin" if info.asset in MEMECOINS else "crypto"
    ref = {"family": family, "symbol": info.symbol, "venue": f"dex:{info.id.network}", "network": info.id.network,
           "instrument_id": info.id.address}
    _check(ref)
    return ref


def asset_ref_cex(venue: str, asset: str) -> dict:
    family = "memecoin" if asset in MEMECOINS else "crypto"
    ref = {"family": family, "symbol": asset, "venue": venue, "network": None, "instrument_id": None}
    if family == "memecoin":
        raise ValueError("C00 : un memecoin exige son réseau et son identifiant unique (contrat ou mint) — utiliser asset_ref_onchain")
    _check(ref)
    return ref


def evidence_from_book(book: Book, field: str = "mid") -> dict:
    value = {"mid": book.mid, "bid": book.bid, "ask": book.ask, "half_spread_bps": book.half_spread_bps}[field]
    published = book.source_ms if book.source_ms is not None else book.received_ms
    ev = {"source": f"{book.venue}:{book.pair}", "field": field, "value": float(value), "event_at_ms": int(published),
          "published_at_ms": int(min(published, book.received_ms)), "received_at_ms": int(book.received_ms), "kind": "observed"}
    for k in ("event_at_ms", "published_at_ms", "received_at_ms"):
        if not _UTC_MS_MIN <= ev[k] <= _UTC_MS_MAX:
            raise ValueError(f"C00 : {k} doit être en millisecondes UTC")
    return ev


def snapshot(asset_ref: dict, decision_at_ms: int, evidence: list[dict]) -> dict:
    for e in evidence:
        if e["received_at_ms"] > decision_at_ms:
            raise ValueError("C00 : information reçue après la décision (information future)")
    return {"asset": asset_ref, "decision_at_ms": int(decision_at_ms), "observations": list(evidence)}


def _check(ref: dict) -> None:
    for k in ("symbol", "venue"):
        if not isinstance(ref[k], str) or not ref[k] or len(ref[k]) > 80:
            raise ValueError(f"C00 : {k} invalide")
    if ref["family"] == "memecoin" and not (ref["network"] and ref["instrument_id"]):
        raise ValueError("C00 : un memecoin exige son réseau ET son identifiant unique")
