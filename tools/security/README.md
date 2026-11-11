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
