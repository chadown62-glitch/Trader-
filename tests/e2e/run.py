"""Lance les vérifications navigateur contre une instance de démonstration JETABLE.

    python tests/e2e/run.py                       # tous les parcours
    python tests/e2e/run.py phase_h_check.js      # un parcours

Pour chaque script : démarre `python -m sentinelle --demo --no-browser` sur un port libre avec un
dossier de données temporaire neuf, attend qu'il réponde, exécute le script Node (Playwright),
arrête le panel et supprime le dossier. Jamais sur une installation d'utilisateur, jamais en ligne :
le marché est simulé. Exécution finie (délais maximaux partout).

Prérequis : Node.js et le paquet `playwright` avec Chromium (`npx playwright install chromium`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.diagnose_windows import _free_port, _start_panel, _stop_panel, _wait_health  # noqa: E402

SCRIPTS = ["mission_control_check.js", "phase_h_check.js", "performance_check.js", "champion_check.js", "macro_check.js"]
TIMEOUT = 300


def run(script: str, output: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="sentinelle-e2e-", ignore_cleanup_errors=True) as tmp:
        port = _free_port()
        proc = _start_panel(port, Path(tmp) / "data", Path(tmp) / "panel.log")
        try:
            if not _wait_health(port, proc, 90):
                print(f"[{script}] le panel de démonstration n'a pas démarré", file=sys.stderr)
                print((Path(tmp) / "panel.log").read_text(errors="replace")[-2000:], file=sys.stderr)
                return 1
            env = {**os.environ, "BASE": f"http://127.0.0.1:{port}", "OUTPUT": str(output)}
            t = time.monotonic()
            r = subprocess.run(["node", str(ROOT / "tests" / "e2e" / script)], cwd=str(ROOT), env=env, timeout=TIMEOUT)
            print(f"[{script}] {'réussi' if r.returncode == 0 else 'ÉCHEC'} en {time.monotonic() - t:.1f} s")
            return r.returncode
        finally:
            _stop_panel(proc)


def main(argv: list[str]) -> int:
    output = Path(os.environ.get("OUTPUT") or tempfile.mkdtemp(prefix="sentinelle-captures-"))
    output.mkdir(parents=True, exist_ok=True)
    failures = [s for s in (argv or SCRIPTS) if run(s, output) != 0]
    print(f"Captures : {output}")
    if failures:
        print("Échecs : " + ", ".join(failures), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
