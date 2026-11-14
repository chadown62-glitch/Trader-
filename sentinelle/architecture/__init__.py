"""Contracts and lazy routing for independent, paper-only market specialists.

This package does not import the broker, exchange clients, or model runtimes.
"""

from .contracts import (
    AssetRef,
    Evidence,
    EvidenceKind,
    MarketAssessment,
    MarketFamily,
    MarketSnapshot,
    OpportunityStatus,
)
from .registry import AssetKnowledge, KnowledgeStore, MarketSpecialist, SpecialistRegistry, SpecialistUnavailable

__all__ = [
    "AssetRef", "Evidence", "EvidenceKind", "MarketAssessment", "MarketFamily", "MarketSnapshot",
    "OpportunityStatus", "AssetKnowledge", "KnowledgeStore", "MarketSpecialist", "SpecialistRegistry",
    "SpecialistUnavailable",
]
