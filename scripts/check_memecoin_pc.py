"""Diagnostic local, sans démarrage du serveur ni accès réseau externe."""

import argparse
import hashlib
import importlib
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = (
    (54, "ff4650933eceb915e1c45d011da36b4dec280e54", "5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18",
     "3892ab610954fce7551581b1e7526bcefdcff1b10e0637cc6e7ac7b6688a857f"),
    (55, "5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18", "18b31bb901f656d6126b370f47c510775f135c57",
     "4686e4b9f866a7d73afa6bd547ac454a3c04af73ae1236c0cde3bfa5603d7b2b"),
)


def manifest(base, head):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.PIPE)

    paths = git("diff", "--no-renames", "--name-only", "-z", base, head).split(b"\0")
    digest = hashlib.sha256()
    for path in sorted(filter(None, paths)):
        blob = git("cat-file", "blob", head + ":" + path.decode("utf-8"))
        digest.update(hashlib.sha256(blob).hexdigest().encode("ascii") + b"  " + path + b"\n")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, type=Path)
    args = parser.parse_args()
    checks = {"Python " + sys.version.split()[0] + " (cible 3.12)": sys.version_info[:2] == (3, 12)}
    for module in ("fastapi", "uvicorn", "websockets", "httpx"):
        try:
            importlib.import_module(module)
            checks[module] = True
        except (ImportError, OSError) as exc:
            checks[f"{module} ({exc})"] = False
    try:
        with socket.socket() as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", 8000))
        checks["Port local 8000 disponible"] = True
    except OSError as exc:
        checks[f"Port local 8000 ({exc})"] = False

    for number, base, head, expected in FROZEN:
        try:
            checks[f"Manifeste SHA-256 #{number}"] = manifest(base, head) == expected
        except (OSError, subprocess.SubprocessError):
            checks[f"Manifeste #{number} indisponible (vérifier Git et les objets des PR #53 à #55)"] = False
    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ÉCHEC'}] {label}")
    sys.path.insert(0, str(ROOT))
    try:
        from sentinelle.dmh.memecoin_analytics import build_analytics_report, render_report

        print(render_report(build_analytics_report(args.db_path)))
    except (ImportError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(f"Rapport indisponible : {exc}")
        checks["analytics"] = False
    print("Cockpit local : python -m sentinelle --demo --port 8000")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
