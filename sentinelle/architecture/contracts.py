"""Shared multi-market *research* contracts, not orders or trade authorization.

All timestamps are explicit UTC milliseconds. Information may be used in a
snapshot only if it had been received no later than the decision time.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum


class MarketFamily(str, Enum):
    CRYPTO = "crypto"
    MEMECOIN = "memecoin"
    EQUITY = "equity"
    FOREX = "forex"
    ETF = "etf"
    COMMODITY = "commodity"


class EvidenceKind(str, Enum):
    OBSERVED = "observed"
    SCHEDULED_EVENT = "scheduled_event"


class OpportunityStatus(str, Enum):
    CANDIDATE = "candidate"
    WATCH = "watch"
    REJECT = "reject"


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$")
# Explicit timestamp unit check catches seconds accidentally passed as millis.
_UTC_MS_MIN = 631_152_000_000  # 1990-01-01 UTC
_UTC_MS_MAX = 4_102_444_800_000  # 2100-01-01 UTC


def _timestamp(name: str, value: int) -> None:
    if type(value) is not int or not (_UTC_MS_MIN <= value <= _UTC_MS_MAX):
        raise ValueError(f"{name} must be UTC milliseconds from 1990 through 2100")


def _nonblank(name: str, value: str, max_len: int = 256) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ValueError(f"{name} must be nonempty text (max {max_len})")


def _finite(name: str, value: float) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not a boolean")


@dataclass(frozen=True, slots=True)
class AssetRef:
    """An instrument identity; ticker alone is never the unique key."""

    family: MarketFamily
    symbol: str
    venue: str
    network: str | None = None
    instrument_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.family) is not MarketFamily:
            raise ValueError("family must be an explicit MarketFamily")
        for name in ("symbol", "venue"):
            value = getattr(self, name)
            if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
                raise ValueError(f"invalid {name}")
        if self.network is not None and (not isinstance(self.network, str) or _TOKEN.fullmatch(self.network) is None):
            raise ValueError("invalid network")
        if self.instrument_id is not None:
            _nonblank("instrument_id", self.instrument_id, max_len=256)
        if self.family is MarketFamily.MEMECOIN and (not self.network or not self.instrument_id):
            raise ValueError("memecoins require network AND unique contract/mint identifier")

    @property
    def canonical_key(self) -> tuple[str, str, str, str, str]:
        return (self.family.value, self.venue.upper(), self.symbol.upper(),
                (self.network or "").lower(), self.instrument_id or "")


@dataclass(frozen=True, slots=True)
class Evidence:
    """A value and its provenance; an upcoming scheduled event is not an observed outcome."""

    source: str
    field: str
    value: float
    event_at_ms: int
    published_at_ms: int
    received_at_ms: int
    kind: EvidenceKind = EvidenceKind.OBSERVED

    def __post_init__(self) -> None:
        _nonblank("source", self.source)
        _nonblank("field", self.field)
        _finite("value", self.value)
        for name in ("event_at_ms", "published_at_ms", "received_at_ms"):
            _timestamp(name, getattr(self, name))
        if type(self.kind) is not EvidenceKind:
            raise ValueError("invalid evidence kind")
        if self.published_at_ms > self.received_at_ms:
            raise ValueError("information cannot be received before publication")
        if self.kind is EvidenceKind.OBSERVED and self.event_at_ms > self.published_at_ms:
            raise ValueError("observed outcome cannot precede the event")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    asset: AssetRef
    decision_at_ms: int
    observations: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.asset, AssetRef):
            raise ValueError("snapshot requires an AssetRef")
        _timestamp("decision_at_ms", self.decision_at_ms)
        if type(self.observations) is not tuple or not all(isinstance(o, Evidence) for o in self.observations):
            raise ValueError("observations must be a tuple of Evidence")
        seen: set[tuple[str, str, int, int]] = set()
        for item in self.observations:
            if item.received_at_ms > self.decision_at_ms:
                raise ValueError("future information detected: evidence received after decision")
            key = (item.source, item.field, item.event_at_ms, item.received_at_ms)
            if key in seen:
                raise ValueError("duplicated evidence")
            seen.add(key)


@dataclass(frozen=True, slots=True)
class MarketAssessment:
    """Inert specialist conclusion. It cannot specify a broker, order or account."""

    asset: AssetRef
    as_of_ms: int
    status: OpportunityStatus
    rationale: str
    expected_net_r: float | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.asset, AssetRef):
            raise ValueError("assessment requires AssetRef")
        _timestamp("as_of_ms", self.as_of_ms)
        if type(self.status) is not OpportunityStatus:
            raise ValueError("status must be OpportunityStatus")
        _nonblank("rationale", self.rationale, max_len=2048)
        if self.expected_net_r is not None:
            _finite("expected_net_r", self.expected_net_r)
        if self.confidence is not None:
            _finite("confidence", self.confidence)
            if not (0 <= self.confidence <= 1):
                raise ValueError("confidence outside [0, 1]")
