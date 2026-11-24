"""Registre des capacités C001–C100 : fidèle au dossier du propriétaire, vérifiable dans le code, sans promotion."""

import json
import re
from pathlib import Path

from sentinelle.scalp import registre

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "docs" / "scalp" / "catalogue-C001-C100.md"


def committed():
    return json.loads(registre.OUT.read_text(encoding="utf-8"))


def test_the_registry_holds_exactly_the_hundred_capabilities_of_the_owner_dossier():
    text = CATALOGUE.read_text(encoding="utf-8")
    dossier = re.findall(r"^- (C\d{3}) — (.+?)\.( \[HAUTE\])?$", text, flags=re.M)
    assert [d[0] for d in dossier] == [f"C{n:03d}" for n in range(1, 101)]
    reg = {i["id"]: i for i in committed()["capacites"]}
    assert list(reg) == [d[0] for d in dossier]
    for cid, title, high in dossier:
        assert reg[cid]["titre"] == title.replace("’", "'")
        assert reg[cid]["priorite"] == ("HAUTE" if high else "normale")
    assert sum(i["priorite"] == "HAUTE" for i in reg.values()) == 27


def test_every_reference_exists_in_the_current_code():
    # Les lignes sont exactes au commit audité ; ici, chaque fichier, symbole, test et dépendance doit exister.
    assert registre.check(committed(), strict_lines=False) == []


def test_statuses_are_backed_by_evidence_and_nothing_enters_a_decision():
    reg = committed()
    assert set(reg["statuts"]) == set(registre.STATUTS) and sum(reg["statuts"].values()) == 100
    for i in reg["capacites"]:
        assert i["integration_decision"] == "aucune sans approbation humaine"
        if i["statut"] in ("EXISTANTE_ET_TESTÉE", "PARTIELLE", "EXPÉRIMENT_REJETÉ"):
            assert i["preuves"] and i["tests"] and registre.tested(i)
        if i["statut"] == "EXPÉRIMENT_REJETÉ":
            assert "research/scalp-v1/" in i["note"]
    assert reg["statuts"]["EXPÉRIMENT_VALIDÉ_PROVISOIRE"] == 0  # aucune capacité n'est validée par ce seul inventaire


def test_the_checker_catches_a_wrong_reference():
    reg = committed()
    item = next(i for i in reg["capacites"] if i["preuves"])
    item["preuves"][0]["symbole"] = "symbole_inexistant"
    assert any("symbole absent" in p for p in registre.check(reg, strict_lines=False))


def test_frozen_baseline_points_to_the_published_results():
    base = committed()["baseline"]
    assert len(base["references"]) == 3 and base["main"] == committed()["main_audite"]
    for ref in base["references"]:
        src = ROOT / ref["provenance"]["fichier"]
        assert registre.digest(src) == ref["provenance"]["sha256"]  # résultats inchangés depuis le gel
        assert ref["pire_baisse_pct"] > 0 and ref["semaines"] == 464
    for path, sha in base["modules_sha256"].items():
        assert (ROOT / path).exists() and len(sha) == 64
