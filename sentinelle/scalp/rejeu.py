"""Protection des résultats publiés et traçabilité des relectures OMEGA."""

import hashlib
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "research" / "scalp-v1"


def check_paths(output, journal):
    """Refuser une destination existante avant la collecte et toute confusion résultat/journal."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Rejeu déjà présent : {output}. Choisir une destination --out neuve.")
    if journal:
        target = Path(journal)
        if target.resolve() == output.resolve():
            raise ValueError("Le journal et le résultat du rejeu doivent avoir des destinations distinctes.")
        if target.exists() and any(target.samefile(old) for old in RESULTS.glob("*.json")):
            raise ValueError("Une archive scientifique publiée ne peut pas servir de journal.")


def provenance(study, original_commit, loaded, modules):
    archive = RESULTS / f"omega-{study}-v1.json"
    return {
        "archive_v1": {"nom": archive.name, "commit": original_commit, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "code_sha256": {name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
                        for name in modules},
        "archives_crypto": {symbol: report for symbol, (_, report) in loaded.items()},
    }


def write_result(output, report):
    data = (json.dumps(report, ensure_ascii=False, default=float, allow_nan=False) + "\n").encode("utf-8")
    with Path(output).open("xb") as stream:
        stream.write(data)
    return hashlib.sha256(data).hexdigest()
