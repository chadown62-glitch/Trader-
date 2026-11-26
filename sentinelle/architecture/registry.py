"""Lazy, bounded market-specialist router and per-asset knowledge cache.

No trading side effects, network, training, or model imports. Concrete models are
loaded only through registered factories, and never on module import.
"""

from __future__ import annotations

from collections import OrderedDict
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

from .contracts import AssetRef, MarketAssessment, MarketFamily, MarketSnapshot, _timestamp


class SpecialistUnavailable(LookupError):
    """Fail-closed when no specialist for the requested family/asset exists."""


@dataclass(frozen=True, slots=True)
class AssetKnowledge:
    """Read-only metadata known no later than the snapshot's decision time."""

    asset: AssetRef
    available_at_ms: int
    version: str
    facts: Mapping[str, str | int | float | bool]

    def __post_init__(self) -> None:
        if not isinstance(self.asset, AssetRef):
            raise ValueError("knowledge requires AssetRef")
        _timestamp("available_at_ms", self.available_at_ms)
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("knowledge version is required")
        if not isinstance(self.facts, Mapping):
            raise ValueError("facts must be a mapping")
        cleaned = {}
        for k, v in self.facts.items():
            if not isinstance(k, str) or not k.strip() or not isinstance(v, (str, int, float, bool)):
                raise ValueError("facts must have text keys and scalar values")
            if isinstance(v, float) and not math.isfinite(v):
                raise ValueError("knowledge value must be finite")
            cleaned[k] = v
        object.__setattr__(self, "facts", MappingProxyType(cleaned))


class MarketSpecialist(Protocol):
    def assess(self, snapshot: MarketSnapshot, knowledge: AssetKnowledge) -> MarketAssessment: ...


class KnowledgeStore:
    """Bounded LRU keyed by (asset, as-of instant), avoiding future-state replay leaks."""

    def __init__(self, loader: Callable[[AssetRef, int], AssetKnowledge], *, max_entries: int = 16):
        if not callable(loader) or type(max_entries) is not int or max_entries <= 0:
            raise ValueError("knowledge store requires loader and positive capacity")
        self.loader = loader
        self.max_entries = max_entries
        self._cache: OrderedDict[tuple[tuple[str, str, str, str, str], int], AssetKnowledge] = OrderedDict()

    def get(self, asset: AssetRef, as_of_ms: int) -> AssetKnowledge:
        _timestamp("as_of_ms", as_of_ms)
        key = (asset.canonical_key, as_of_ms)
        if key not in self._cache:
            value = self.loader(asset, as_of_ms)
            if not isinstance(value, AssetKnowledge) or value.asset.canonical_key != asset.canonical_key:
                raise ValueError("knowledge loader returned wrong asset")
            if value.available_at_ms > as_of_ms:
                raise ValueError("future knowledge detected")
            self._cache[key] = value
            if len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return self._cache[key]

    @property
    def cached_count(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()


class SpecialistRegistry:
    """Selects a specialist by exact instrument first, otherwise by family.

    Both factory and memory access happen only on demand. No order API exists.
    An LRU bounds live model instances for limited CPU/GPU memory.
    """

    def __init__(self, knowledge: KnowledgeStore, *, max_loaded: int = 4):
        if not isinstance(knowledge, KnowledgeStore) or type(max_loaded) is not int or max_loaded <= 0:
            raise ValueError("registry requires knowledge store and positive max_loaded")
        self.knowledge = knowledge
        self.max_loaded = max_loaded
        self._family: dict[MarketFamily, Callable[[], MarketSpecialist]] = {}
        self._assets: dict[tuple[str, str, str, str, str], Callable[[], MarketSpecialist]] = {}
        self._instances: OrderedDict[tuple, MarketSpecialist] = OrderedDict()

    def register_family(self, family: MarketFamily, factory: Callable[[], MarketSpecialist]) -> None:
        if type(family) is not MarketFamily or not callable(factory) or family in self._family:
            raise ValueError("invalid or duplicate family specialist")
        self._family[family] = factory

    def register_asset(self, asset: AssetRef, factory: Callable[[], MarketSpecialist]) -> None:
        if not isinstance(asset, AssetRef) or not callable(factory) or asset.canonical_key in self._assets:
            raise ValueError("invalid or duplicate asset specialist")
        self._assets[asset.canonical_key] = factory

    def assess(self, snapshot: MarketSnapshot) -> MarketAssessment:
        if not isinstance(snapshot, MarketSnapshot):
            raise ValueError("invalid snapshot")
        asset_key = snapshot.asset.canonical_key
        if asset_key in self._assets:
            selector = ("asset", *asset_key)
            factory = self._assets[asset_key]
        elif snapshot.asset.family in self._family:
            selector = ("family", snapshot.asset.family.value)
            factory = self._family[snapshot.asset.family]
        else:
            raise SpecialistUnavailable(f"no specialist for {snapshot.asset.family.value}")
        # Knowledge is validated BEFORE loading a potentially expensive model.
        knowledge = self.knowledge.get(snapshot.asset, snapshot.decision_at_ms)
        if selector not in self._instances:
            model = factory()
            if not callable(getattr(model, "assess", None)):
                raise TypeError("specialist must implement assess(snapshot, knowledge)")
            self._instances[selector] = model
            if len(self._instances) > self.max_loaded:
                self._instances.popitem(last=False)
        self._instances.move_to_end(selector)
        result = self._instances[selector].assess(snapshot, knowledge)
        if (not isinstance(result, MarketAssessment)
                or result.asset.canonical_key != asset_key
                or result.as_of_ms != snapshot.decision_at_ms):
            raise ValueError("specialist returned an invalid or mismatched assessment")
        return result

    @property
    def loaded_count(self) -> int:
        return len(self._instances)

    def unload(self) -> None:
        """Drop references; resource cleanup remains the factory/model owner's job."""
        self._instances.clear()
        self.knowledge.clear()
