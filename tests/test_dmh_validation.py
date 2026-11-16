"""DIRECT MARKET HUB — la validation publiée se reproduit exactement à partir de la capture archivée (aucun réseau)."""
import hashlib
import json
import logging
from pathlib import Path

import pytest

from tools import dmh_capture

logging.disable(logging.CRITICAL)
MEASURES = Path(__file__).resolve().parent.parent / "docs" / "validation" / "mesures" / "market-hub-v1"


def test_published_validation_is_reproduced_exactly_from_the_archived_capture(tmp_path):
    caps = sorted(MEASURES.glob("capture-*.json.gz"))
    assert len(caps) == 1, "une seule capture publiée"
    cap = caps[0]
    digest = cap.with_name(cap.name + ".sha256").read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(cap.read_bytes()).hexdigest() == digest
    published = json.loads((MEASURES / "validation.json").read_text(encoding="utf-8"))
    again = dmh_capture.validate(cap, tmp_path)
    assert again["capture_sha256"] == published["capture_sha256"] == digest
    assert len(again["rows"]) == len(published["rows"]) == 9
    for a, b in zip(again["rows"], published["rows"]):
        assert (a["asset"], a["notional_eur"], a["decision"]) == (b["asset"], b["notional_eur"], b["decision"])
        ca, cb = a["chosen"] or {}, b["chosen"] or {}
        assert ca.get("label") == cb.get("label")
        if ca:
            assert ca["cost_bps"] == pytest.approx(cb["cost_bps"], abs=1e-9)
        sa, sb = a["simulated"].get("round_trip_cost_bps"), b["simulated"].get("round_trip_cost_bps")
        assert (sa is None) == (sb is None) and (sa is None or sa == pytest.approx(sb, abs=1e-9))
        assert a["security"]["allowed"] == b["security"]["allowed"]


def test_estimates_agree_with_simulated_execution_on_the_same_books():
    pub = json.loads((MEASURES / "validation.json").read_text(encoding="utf-8"))
    for r in pub["rows"]:
        sim = r["simulated"].get("round_trip_cost_bps")
        if r["chosen"] and sim is not None:
            assert abs(sim - r["chosen"]["cost_bps"]) < 2.0, (r["asset"], r["notional_eur"])  # cohérence estimation / simulation
            assert r["simulated"]["reconciled"]
