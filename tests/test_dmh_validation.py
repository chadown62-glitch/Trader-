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
        assert a["user_path"]["outcome"] == b["user_path"]["outcome"] and a["user_path"]["route"] == b["user_path"]["route"]


def test_cost_measures_and_the_user_path_are_reported_separately():
    """Mission 52 : une mesure de coût à 30 € (portefeuille de mesure isolé) ne vaut pas autorisation pour l'utilisateur."""
    pub = json.loads((MEASURES / "validation.json").read_text(encoding="utf-8"))
    for r in pub["rows"]:
        assert "hors limites" in r["measure"] and r["user_path"]["limits"].startswith("par défaut")
        u = r["user_path"]
        if r["notional_eur"] > 10:
            assert u["outcome"] == "REFUS DE SÉCURITÉ" and any("position" in x for x in u["reasons"])
        if r["notional_eur"] < 10:
            assert u["outcome"] == "REFUS DE SÉCURITÉ" and any("minimum" in x for x in u["reasons"])
        if u["outcome"] == "AUTORISÉE":
            assert u["immobilized_pct"] <= 1.0 and r["notional_eur"] == 10
    tables = (MEASURES / "tableaux.md").read_text(encoding="utf-8")
    assert "Parcours utilisateur" in tables and "limites de l'utilisateur NON appliquées" in tables and "Seuil économique" in tables


def test_estimates_agree_with_simulated_execution_on_the_same_books():
    pub = json.loads((MEASURES / "validation.json").read_text(encoding="utf-8"))
    for r in pub["rows"]:
        sim = r["simulated"].get("round_trip_cost_bps")
        if r["chosen"] and sim is not None:
            # même base : frais payés simulés + reliquat conservé (capital immobilisé) ≈ coût économique estimé
            leftover_bps = r["simulated"]["leftover_cost_eur"] / r["simulated"]["spent_eur"] * 1e4
            assert abs(sim + leftover_bps - r["chosen"]["cost_bps"]) < 2.5, (r["asset"], r["notional_eur"])
            assert r["simulated"]["reconciled"]
