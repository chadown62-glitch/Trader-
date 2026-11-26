"""Deterministic offline contract tests for C00 multi-market foundation."""
from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError

from sentinelle.architecture import (
    AssetKnowledge, AssetRef, Evidence, EvidenceKind, KnowledgeStore, MarketAssessment,
    MarketFamily, MarketSnapshot, OpportunityStatus, SpecialistRegistry, SpecialistUnavailable,
)

T = 1_780_000_000_000


def asset(family=MarketFamily.CRYPTO, symbol="BTC/EUR", venue="BINANCE", **kw):
    return AssetRef(family, symbol, venue, **kw)


def snapshot(a=None, when=T):
    return MarketSnapshot(a or asset(), when, (Evidence("exchange", "close", 100.0, when-2000, when-1000, when-500),))


def store(load=None, cap=2):
    return KnowledgeStore(load or (lambda a, ts: AssetKnowledge(a, ts, "v1", {"asset": a.symbol})), max_entries=cap)


class Dummy:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def assess(self, snap, knowledge):
        self.calls.append((snap.asset, knowledge.version))
        return MarketAssessment(snap.asset, snap.decision_at_ms, OpportunityStatus.WATCH, self.name)


class MarketContractsTest(unittest.TestCase):
    def test_all_six_market_families(self):
        self.assertEqual(len(MarketFamily), 6)
        for fam in MarketFamily:
            kw = {"network": "solana", "instrument_id": "Mint0001"} if fam is MarketFamily.MEMECOIN else {}
            self.assertEqual(asset(fam, **kw).family, fam)

    def test_asset_identity_separates_venues_families_and_contracts(self):
        btc = asset()
        self.assertNotEqual(btc.canonical_key, asset(venue="KRAKEN").canonical_key)
        self.assertNotEqual(btc.canonical_key, asset(MarketFamily.ETF).canonical_key)
        sol1 = asset(MarketFamily.MEMECOIN, "DOG", "DEX", network="solana", instrument_id="mintA")
        sol2 = asset(MarketFamily.MEMECOIN, "DOG", "DEX", network="solana", instrument_id="mintB")
        self.assertNotEqual(sol1.canonical_key, sol2.canonical_key)
        self.assertEqual(btc.canonical_key, asset(venue="binance").canonical_key)

    def test_reject_meme_ticker_without_contract(self):
        with self.assertRaisesRegex(ValueError, "require network"):
            asset(MarketFamily.MEMECOIN, "DOG", "DEX")

    def test_reject_invalid_asset(self):
        for kwargs in ({"symbol": ""}, {"venue": " "}, {"family": "crypto"}, {"symbol": "X;DROP TABLE"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                asset(**kwargs)

    def test_evidence_no_future_publication_or_observed_outcome(self):
        with self.assertRaisesRegex(ValueError, "before publication"):
            Evidence("feed", "price", 20.0, T-1000, T, T-50)
        with self.assertRaisesRegex(ValueError, "precede the event"):
            Evidence("feed", "price", 20.0, T, T-100, T)
        with self.assertRaisesRegex(ValueError, "future information"):
            MarketSnapshot(asset(), T, (Evidence("feed", "price", 20.0, T-1000, T-900, T+1),))

    def test_scheduled_future_event_can_be_known_early(self):
        event = Evidence("calendar", "scheduled_rate_meeting", 1.0, T+86_400_000, T-5000, T-4000,
                         EvidenceKind.SCHEDULED_EVENT)
        MarketSnapshot(asset(MarketFamily.FOREX), T, (event,))

    def test_reject_seconds_bool_nan_duplicate(self):
        with self.assertRaises(ValueError):
            MarketSnapshot(asset(), 1_780_000_000, ())
        with self.assertRaises(ValueError):
            MarketSnapshot(asset(), True, ())
        with self.assertRaises(ValueError):
            Evidence("feed", "price", math.nan, T-2000, T-1000, T-500)
        x = snapshot().observations[0]
        with self.assertRaisesRegex(ValueError, "duplicated"):
            MarketSnapshot(asset(), T, (x, x))

    def test_assessment_is_inert_and_validated(self):
        a = MarketAssessment(asset(), T, OpportunityStatus.CANDIDATE, "Setup plausible", 0.01, 0.55)
        self.assertFalse(hasattr(a, "place_order"))
        with self.assertRaises(FrozenInstanceError):
            a.status = OpportunityStatus.REJECT
        with self.assertRaises(ValueError):
            MarketAssessment(asset(), T, OpportunityStatus.WATCH, "x", confidence=1.2)
        with self.assertRaises(ValueError):
            MarketAssessment(asset(), T, OpportunityStatus.WATCH, "x", expected_net_r=float("inf"))


class LazyRegistryTest(unittest.TestCase):
    def test_factory_not_loaded_until_needed(self):
        created=[]
        reg=SpecialistRegistry(store())
        reg.register_family(MarketFamily.CRYPTO, lambda: created.append("new") or Dummy("crypto"))
        self.assertEqual(created, [])
        self.assertEqual(reg.assess(snapshot()).rationale, "crypto")
        self.assertEqual(created, ["new"])
        reg.assess(snapshot())
        self.assertEqual(created, ["new"])

    def test_per_asset_override_does_not_affect_other_assets(self):
        reg=SpecialistRegistry(store())
        reg.register_family(MarketFamily.CRYPTO, lambda: Dummy("family"))
        eth=asset(symbol="ETH/EUR")
        reg.register_asset(eth, lambda: Dummy("eth"))
        self.assertEqual(reg.assess(snapshot(eth)).rationale, "eth")
        self.assertEqual(reg.assess(snapshot()).rationale, "family")

    def test_market_without_specialist_fails_closed(self):
        reg=SpecialistRegistry(store())
        with self.assertRaises(SpecialistUnavailable):
            reg.assess(snapshot(asset(MarketFamily.EQUITY, "AAPL", "NASDAQ")))
        self.assertEqual(reg.loaded_count, 0)

    def test_invalid_knowledge_fails_before_model_loading(self):
        created=[]
        bad=store(lambda a,ts: AssetKnowledge(a, ts+100, "v2", {"future": 99}))
        reg=SpecialistRegistry(bad)
        reg.register_family(MarketFamily.CRYPTO, lambda: created.append(True) or Dummy("bad"))
        with self.assertRaisesRegex(ValueError, "future knowledge"):
            reg.assess(snapshot())
        self.assertEqual(created, [])

    def test_cache_bounded_and_asof_sensitive(self):
        loads=[]
        memory=store(lambda a,ts: loads.append((a.symbol,ts)) or AssetKnowledge(a, ts, "v1", {}),cap=2)
        a=asset()
        memory.get(a,T)
        memory.get(a,T)
        self.assertEqual(len(loads),1)
        memory.get(a,T+1000)
        memory.get(a,T+2000)
        self.assertEqual(memory.cached_count,2)
        memory.get(a,T)
        self.assertEqual(len(loads),4)

    def test_model_lru_bounded_and_can_unload(self):
        reg=SpecialistRegistry(store(),max_loaded=1)
        reg.register_family(MarketFamily.CRYPTO, lambda: Dummy("crypto"))
        reg.register_family(MarketFamily.FOREX, lambda: Dummy("fx"))
        reg.assess(snapshot())
        reg.assess(snapshot(asset(MarketFamily.FOREX,"EUR/USD","FX")))
        self.assertEqual(reg.loaded_count,1)
        reg.unload()
        self.assertEqual(reg.loaded_count,0)
        self.assertEqual(reg.knowledge.cached_count,0)

    def test_wrong_asset_or_date_assessment_rejected(self):
        class Liar:
            def assess(self, snap, knowledge):
                return MarketAssessment(asset(symbol="ETH/EUR"), snap.decision_at_ms,
                                        OpportunityStatus.CANDIDATE, "wrong asset")
        reg=SpecialistRegistry(store())
        reg.register_family(MarketFamily.CRYPTO, Liar)
        with self.assertRaisesRegex(ValueError, "mismatched assessment"):
            reg.assess(snapshot())

    def test_mutation_of_knowledge_forbidden(self):
        k=AssetKnowledge(asset(), T, "v1", {"foo": 1})
        with self.assertRaises(TypeError):
            k.facts["foo"]=2

    def test_duplicate_registration_is_rejected(self):
        reg=SpecialistRegistry(store())
        reg.register_family(MarketFamily.CRYPTO,Dummy)
        with self.assertRaises(ValueError):
            reg.register_family(MarketFamily.CRYPTO,Dummy)


if __name__ == "__main__":
    unittest.main()
