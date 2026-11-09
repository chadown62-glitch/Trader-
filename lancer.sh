#!/usr/bin/env sh
# Lanceur Mac / Linux : ./lancer.sh  (options : --demo, --diagnostic, --remote, --port 8765)
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/lanceur.py "$@"
fi
echo "Python 3.11 ou plus récent est nécessaire : https://www.python.org/downloads/"
exit 1
