"""Registre des capacités C001–C100 : fidèle au dossier du propriétaire, vérifiable dans le code, sans promotion."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

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
        if i["statut"] in registre.CLAIMS_TESTED:
            assert i["preuves"] and i["tests"] and registre.referenced(i)  # référencement : nécessaire, pas suffisant
        if i["statut"] == "EXPÉRIMENT_REJETÉ":
            assert "research/scalp-v1/" in i["note"]
    assert reg["statuts"]["EXPÉRIMENT_VALIDÉ_PROVISOIRE"] == 0  # aucune capacité n'est validée par ce seul inventaire


def test_each_capability_carries_its_validation_method_expected_value_and_lot():
    reg = {i["id"]: i for i in committed()["capacites"]}
    domains = {titre: key for key, (titre, _) in registre.DOMAINES.items()}
    for cid, i in reg.items():
        assert i["methode_validation"].endswith(registre.CRITERES[domains[i["domaine"]]]) and "préenregistrés" in i["methode_validation"]
        assert i["valeur_ajoutee_attendue"].endswith("attendue, non démontrée")
        assert i["lot"] == registre.LOTS.get(cid, "suivants (dépendances et valeur marginale)")
    assert sorted(c for c, lot in registre.LOTS.items() if lot.startswith("1")) == ["C061", "C062", "C091", "C094", "C095", "C097", "C099"]
    assert sum(lot.startswith("2") for lot in registre.LOTS.values()) == 12 and sum(lot.startswith("3") for lot in registre.LOTS.values()) == 6


def test_a_test_that_only_names_a_symbol_is_not_counted_as_executing_it(tmp_path):
    named = tmp_path / "test_nomme.py"
    named.write_text("from sentinelle.scalp import essais  # holm\n\n\ndef test_nomme():\n    assert essais\n", encoding="utf-8")
    called = tmp_path / "test_appelle.py"
    called.write_text("from sentinelle.scalp import essais\n\n\ndef test_appelle():\n    assert len(essais.holm([0.01, 0.02])) == 2\n",
                      encoding="utf-8")
    item = {"statut": "EXISTANTE_ET_TESTÉE", "preuves": [{"fichier": "sentinelle/scalp/essais.py", "symbole": "holm"}], "tests": [str(named)]}
    runs = {str(named): registre.trace(str(named)), str(called): registre.trace(str(called))}
    assert registre.referenced(item)  # le nom figure dans le test...
    cov = registre.coverage_of(item, runs)
    assert cov["niveau"] == "non constatée" and cov["a_confirmer"]  # ...mais le corps de holm n'a jamais tourné
    item["tests"] = [str(called)]
    cov = registre.coverage_of(item, runs)
    assert cov["niveau"] == "constatée" and not cov["a_confirmer"]
    failing = tmp_path / "test_echoue.py"
    failing.write_text("from sentinelle.scalp import essais\n\n\ndef test_echoue():\n    assert essais.holm([0.01]) == []\n", encoding="utf-8")
    item["tests"] = [str(failing)]
    cov = registre.coverage_of(item, {str(failing): registre.trace(str(failing))})
    assert cov["niveau"] == "non constatée" and cov["tests_en_echec"] == [str(failing)]  # un test en échec ne prouve rien


def test_stored_execution_measurement_qualifies_every_claimed_status():
    reg = committed()
    m = reg["mesure_execution"]
    assert m["commit"] and m["python"].startswith("3.12") and sum(m["niveaux"].values()) == 100
    assert set(m["tests"]) == {t for i in reg["capacites"] for t in i["tests"]}
    for i in reg["capacites"]:
        cov = i["couverture_effective"]
        assert cov["niveau"] == registre.coverage_level(cov["symboles"], bool(i["preuves"]))
        assert [(s["fichier"], s["symbole"]) for s in cov["symboles"]] == [(p["fichier"], p["symbole"]) for p in i["preuves"]]
        assert cov["a_confirmer"] == (i["statut"] in registre.CLAIMS_TESTED and cov["niveau"] != "constatée")
        assert (i["id"] in m["statuts_a_couverture_a_confirmer"]) == cov["a_confirmer"]


def test_the_checker_catches_a_wrong_reference():
    reg = committed()
    item = next(i for i in reg["capacites"] if i["preuves"])
    item["preuves"][0]["symbole"] = "symbole_inexistant"
    assert any("symbole absent" in p for p in registre.check(reg, strict_lines=False))


def test_frozen_baseline_points_to_the_published_results():
    base = committed()["baseline"]
    assert len(base["references"]) == 3
    audited = committed()["main_audite"]
    if registre.commit_available(base["main"]) and registre.commit_available(audited):  # le gel précède ou égale l'audit
        assert subprocess.run(["git", "merge-base", "--is-ancestor", base["main"], audited], cwd=ROOT).returncode == 0
    for ref in base["references"]:
        src = ROOT / ref["provenance"]["fichier"]
        assert registre.digest(src) == ref["provenance"]["sha256"]  # résultats inchangés depuis le gel
        assert ref["pire_baisse_pct"] > 0 and ref["semaines"] == 464


def test_frozen_module_fingerprints_match_their_content_at_the_audited_commit():
    # Comparaison au contenu git du commit du gel, pas à l'arbre courant : les modules peuvent évoluer ensuite.
    base = committed()["baseline"]
    if not registre.commit_available(base["main"]):
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"la CI doit récupérer le commit du gel {base['main']}")
        pytest.skip(f"commit {base['main']} absent de l'historique local : git fetch origin {base['main']}")
    assert len(base["modules_sha256"]) == 6
    assert registre.check_fingerprints(base) == []
    first = next(iter(base["modules_sha256"]))
    tampered = {**base, "modules_sha256": {**base["modules_sha256"], first: "0" * 64}}
    assert registre.check_fingerprints(tampered) == [f"{first} : empreinte différente du contenu au commit {base['main']}"]
