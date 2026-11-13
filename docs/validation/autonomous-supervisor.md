# Mission #34 — intégration #32/#33 et superviseur continu préparé

Livraison du 13 novembre 2026. Mission : https://github.com/chadown62-glitch/Trader-/issues/34.
Branche `work/autonomous-supervisor`, empilée sur #32 (`f279e711c2164270903c09f6250639bc67168a9c`).
Aucune fusion, fermeture de PR, modification de main, campagne prospective ou tâche permanente effectuée.

## Réconciliation des deux branches sœurs

#32 et #33 ont pour ancêtre #29 (`048c62a84dad5e036ad08dfda6f4d1c2e45258b4`).
Le moteur #32 est conservé comme parent : il porte les transactions et les reprises déjà validées.
Les 24 fichiers du diff #29..#33 (`359ac88305c333b5e13db17f0934b0dd835537e7`)
ont été examinés et portés ; aucun merge aveugle de la branche ancienne.

| Fichiers / domaine | Décision et vérification |
|---|---|
| `services/core.py`, `tools/prospective.py` | #33 ne les modifie pas ; versions #32 conservées. Aucun nouveau portefeuille, moteur ou laboratoire. |
| `api/server.py` | Ajout ciblé GET `/api/observation/preflight`. Middleware d'origine conservé. Authentification adaptée pour que ce GET ne renouvelle pas la session. |
| `preflight.py` | Apport #33 ; lecture sur copie SQLite + WAL, contrôle d'intégrité de la copie. État du superviseur, archives/carnets réellement vérifiés, provenance et validation PC ajoutés. `RUNNER_AVAILABLE` reste False. |
| `tools/diagnose_windows.py` | Diagnostic complet #33 remplace le petit diagnostic historique #32 ; sondes publiques optionnelles, panel jetable, copie des données, empreintes du code et chemin de l'installation dans le rapport. |
| `tools/diagnostic_windows.ps1`, `scripts/lanceur.py` | Diagnostic jetable #33 conservé. Tests superviseur/reprise ajoutés, code de sortie d'échec propagé ; UTF-8 BOM + CRLF conservés. `diagnostic.bat` reste le relais de `lancer.bat --diagnostic`. |
| `web/main.js`, `store.js`, `styles.css` | Navigation Préparation, feuille mobile, dates UTC et contrôles de débordement #33 conservés. |
| Vues apprentissage, learning, macro, trade | Corrections #33 des états vides, libellés internes, frais, prix périmés, lisibilité mobile conservées. |
| Vues performances et préparation | #33 porté ; état réel du superviseur ajouté. Les totaux du compte partagé manuel/IA ne sont pas présentés comme une campagne prospective isolée. Aucun bouton de démarrage. |
| `test_preflight.py`, `test_diagnostic.py`, `e2e/phase_h_check.js`, `e2e/run.py` | Tests #33 conservés ; nom du test du lanceur actualisé (absence de validation, et non absence du fichier). Aucun skip ajouté pour cacher un échec. |
| README, GUIDE, WINDOWS, marches-publics, `.gitignore` | Documentation #33 intégrée puis actualisée. `phase-h-2026-11-12.md` est le compte rendu historique de Claude, pas une nouvelle preuve d'exécution. |
| Captures, archives, outbox, T0/T1/T2, replay causal #32 | Préservés ; la primitive `step` est appelée telle quelle. Lecture d'installation sur copie WAL et finalisation des intentions expirées ajoutées. |

## Fonctionnement et garanties

`python -m tools.supervise status --data ...` lit seulement les fichiers. Aucun code d'import,
page web, diagnostic, `prepare`, `approve` ou `resume` ne lance de boucle.
Le mode `run` exige un plan immuable et un accord distinct lié à un vrai diagnostic Windows 11.
Par défaut il s'arrête après **un tick** ; seul `--continuous` demande explicitement la boucle.
Aucun service Windows, daemon, démarrage automatique ou tâche planifiée n'est installé.

Le plan enregistre les chemins de l'installation et du protocole, modèle/réglages/périodes v2,
empreintes v3, sources Python du moteur et du superviseur, vues JS/CSS et requirements.
Toute modification de ces fichiers invalide le plan et l'accord ; préparer un nouveau plan de
lancement est alors nécessaire. Cela ne réécrit aucun protocole scientifique. Les reçus sont
une trace locale de la décision de l'opérateur, pas une signature inviolable contre quelqu'un
qui peut modifier le code ou les fichiers.

La validation Windows et l'accord autorisent le **collecteur**. La capacité d'écrire une décision
fictive reste soumise, à chaque clôture, au pré-vol strict #32 : compte unique EUR30 existant,
réglages exacts, agents autonomes arrêtés, modèle intact, 1 500 bougies 4h fermées par actif,
absence de trous/altération, UTC distante à ±5s, HTTP ≤5s, clôture ≤180s, carnet reçu ≤30s.
Un compteur « collecteur actif » n'est donc pas une attestation d'exécution admissible.

- Les clôtures UTC sont attendues au moins 3s avant collecte. Une clôture découverte après 180s
  est archivée `history_only`, sans fabriquer de prédiction ni d'ordre rétroactif.
- Une panne après T1 conserve le modèle, la probabilité et les caractéristiques de la prévision.
  Si elle est trop vieille à la reprise, elle est finalisée une seule fois comme refus (« signal expiré »).
  Les fills/positions/décisions/outbox continuent d'utiliser l'idempotence et les transactions #32.
- Chaque clôture a un chemin durable unique ; les pages réussies sont reprises après crash.
  États, tentatives, délais et événements sont stockés dans une SQLite latérale WAL/FULL.
  Verrou de fichier OS exclusif par installation, libéré aussi après arrêt brutal.
- Les positions ouvertes sont entretenues entre clôtures sur **de nouveaux** carnets publics,
  sans appel aux stratégies et sans nouvelle prédiction. Cotations brutes et SHA conservés.
  Un 429 impose un cooldown durable, y compris après redémarrage ; aucune exécution sur prix périmé.
- La macro est facultative et s'exécute après le pas prix, dans un sidecar séparé : Fed, BLS, BEA,
  BCE, Eurostat, un appel maximum par tick et backoff/leases de v3. Le code gelé v3 n'est pas modifié.
  Aucune collecte Forex Factory sans droits ; aucun Ollama/GPU, entraînement ou promotion automatique.
- Les erreurs macro n'interrompent pas la politique prix gelée. Aucune nouvelle source ne filtre ses ordres.
- `status` expose heartbeat, dernière protection, origine, tentatives, erreurs, clôtures observées,
  clôtures manquantes et capacité du dernier pas. Le panel n'affiche READY que si les prérequis,
  le code/rapport PC/accord, le collecteur actif et les archives/cotations récentes sont vérifiés.
- Les périodes gelées restent v2 **13/11/2026–13/02/2027 UTC** et v3 **14/11/2026–14/02/2027 UTC**.
  Les créneaux antérieurs au lancement restent manquants. Aucune date n'est décalée.

### Limites de consommation et d'exploitation

Poll 15s ; collecte de clôture au plus 110s de budget coopératif ; GET individuel au plus 20s,
réponses au plus 4Mo ; quatre tentatives par clôture, espacées d'au moins 30s ; cotations au plus
20s pour l'ensemble du lot. Réglages de ressources hors bornes rejetés. BLAS/OMP limités à un
thread dans le CLI ; pas de GPU. À 3Go de fichiers du superviseur, arrêt explicite sans suppression.
Le plafond est vérifié entre ticks : une écriture en cours peut le dépasser d'un lot borné.
L'utilisateur doit archiver explicitement les fichiers avant reprise. Aucun nettoyage automatique.

Ctrl+C et `stop` sont coopératifs ; un GET en cours termine ou expire avant l'arrêt.
L'état « actif » devient périmé après 45s sans heartbeat : un appel lent peut donc le dégrader.
Le budget ne tue pas un calcul natif bloqué ; l'arrêt OS brutal reste récupérable, testé ci-dessous.
Les rapports par clôture sont immuables et groupés par date dans `daily/` ; une journée sans
observation n'a pas de performance inventée. Une journée observée sans trade reste mesurable.
Les compteurs de couverture décrivent les pas complets, pas chaque fill individuellement.
Une panne pendant un pas ensuite expiré peut conserver des décisions partielles au journal ;
elle n'est pas requalifiée comme une clôture entièrement observée.

L'outbox est **au moins une fois**, avec event_id stable ; aucune livraison Telegram n'est attestée
par son simple stockage. Aucun listener externe/Telegram n'est activé dans ce superviseur : les
événements restent disponibles dans SQLite pour les journaux et une future consommation dédupliquée.

## Validation de cette livraison

**Résultat exécuté sur la branche intégrée : 517 tests réussis, 0 échec, 0 xfail,
1 skip (socket locale interdite sous seccomp).** Ce test a ensuite réussi séparément : 1/1.
Les 23 nouveaux cas du superviseur font partie de ce total. La suite a duré 170,77s ;
un avertissement Starlette sur l'adaptateur httpx, sans échec. Ruff ciblé et `git diff --check` réussis.
Le diagnostic Linux complet a également démarré, arrêté et redémarré un panel jetable,
produit son rapport puis terminé ; il reste BLOCKED et ne valide pas Windows.
Les détails et empreintes sont dans `mesures/supervisor-integration.json`.
Commandes (Linux, CPython 3.12, CCXT complet) :

```sh
cc -O2 -Wall -Wextra tools/security/offline_exec.c -o /tmp/trader-ai-offline
env -i PATH=/usr/bin:/bin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/trader-ai-offline .venv/bin/python \
  -m pytest -o addopts='' -q --junitxml=/tmp/trader34-full.xml
# Socket locale séparée du filtre réseau : instance jetable, arrêt garanti
.venv/bin/python -m pytest -o addopts='' -q \
  tests/test_diagnostic.py::test_real_panel_starts_answers_stops_and_restarts_in_a_temporary_folder
# Navigateurs, séparément (chaque panel est jetable)
CHROMIUM=/usr/bin/chromium .venv/bin/python tests/e2e/run.py
```

Les nouveaux tests comprennent : une journée accélérée de six clôtures 4h, entretiens répétés,
modèle/stratégies/risque/portefeuille réels avec entrées artificielles, frais et réconciliation,
actualités datées et annotations as_of, pré-vol authentifié sans fetch ni modification de fichier,
lecture de bases actives avec WAL, fenêtres/UTC, absence de collecte, signal expiré, 429/cooldown,
reprise de trois arrêts os._exit(77) (capture, décision commitée, état superviseur commit),
verrou multiprocessus, stop/reprise, disque plein et accord/rapport/code/période incorrects.
Les injections de panne T1/T2/outbox et d'annotation #32 restent dans la suite complète.
Le modèle synthétique forçant les entrées sert uniquement à couvrir ces chemins de test.

Le test sur socket locale a réussi séparément. Sous seccomp, ce même test est explicitement skip
car socket() est interdit. Les **quatre parcours Chromium ont été tentés ici et échouent avant
l'affichage**, sur création du socket Unix de ProcessSingleton (`Operation not permitted`).
Aucune validation visuelle desktop/mobile de cette branche ne peut être annoncée dans ce runtime.
Les assertions #33 et leurs captures historiques restent des preuves de leur propre branche.
Windows/PowerShell **non exécutés** : procédure et rapport du PC toujours nécessaires.
Aucun gain financier, apprentissage supérieur ou observation prospective authentique nouveau n'est démontré.

## Lancement futur : barrière humaine

Voir [WINDOWS.md](../WINDOWS.md) pour les étapes et commandes. D'abord diagnostic jetable et
revue du rapport, puis plan lié à l'installation existante, **demande d'accord distincte**,
reçu explicite, essai supervisé borné et seulement ensuite éventuelle boucle au premier plan.
Rien de cela n'a été lancé sur une installation réelle pendant cette mission.
