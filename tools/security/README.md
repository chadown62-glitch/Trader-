# Exécution locale des tests sans réseau

`offline_exec.c` est un lanceur de test Linux x86-64. Il installe un filtre
seccomp hérité par ses descendants : création de socket, connexion et mise en
place d'io_uring sont refusées ; seul `socketpair` Unix est permis pour les
tests ASGI en mémoire. Il ferme les descripteurs hérités autres que l'entrée,
la sortie et l'erreur standard avant l'exécution. L'installation du filtre est
obligatoire : si elle échoue, la commande testée ne démarre pas.

Après installation préalable des dépendances dans un environnement virtuel :

```sh
cc -O2 -Wall -Wextra tools/security/offline_exec.c -o /tmp/sentinelle-offline
env -i PATH=/usr/bin:/bin PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /tmp/sentinelle-offline .venv/bin/python -m pytest -q tests/test_lockdown.py
```

Faites un essai de refus local avant la suite : `socket.socket()` doit renvoyer
`PermissionError`. Ne passez aucune clé ou compte réel. L'environnement doit
rester isolé par le système ou un conteneur lorsque c'est possible ; ce filtre
local vise les tests Python du dépôt, pas l'exécution de programmes malveillants
ayant accès à des processus extérieurs, à des descripteurs standards déjà
connectés ou à des privilèges supplémentaires. Il ne couvre pas les autres
architectures et ne constitue pas une preuve de sûreté de l'application.

## Installation et vérification de la chaîne intégrée

Sur Linux x86-64 avec CPython 3.12, préparer les dépendances dans un environnement dédié. L'installation et l'exécution sont deux étapes distinctes.

```bash
python3.12 -m venv /tmp/sentinelle-env
/tmp/sentinelle-env/bin/python -m pip install -r tools/security/requirements-linux-py312.txt
cc -O2 -Wall -Wextra -Werror tools/security/offline_exec.c -o /tmp/sentinelle-offline
env -i PATH=/usr/bin:/bin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/sentinelle-offline /tmp/sentinelle-env/bin/python -m pytest -o addopts='' -q
```

La distribution CCXT complète est requise, avec ses modules synchrone, asynchrone et Pro. Le test construit les façades publiques Binance et Kraken en mémoire. Les autres tests pilotent des doubles de marché et vérifient les appels observés. Le filtre seccomp s'applique aussi aux processus enfants. Cette vérification concerne le code du dépôt et ces dépendances sur Linux x86-64.
