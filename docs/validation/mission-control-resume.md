# Mission Control #39 — reprise après perte de l’environnement

État du 13 novembre 2026. **Ce fichier est un point de reprise, pas une livraison du code ni une PR validée.**
Le runtime a été déclaré indisponible et exec_command répond : « No usable execution environment is available ».
Le connecteur GitHub reste disponible. Ne pas reconstruire ou remplacer le travail local avant de tenter sa récupération.

## État du dépôt de travail avant interruption

- Mission lue intégralement : https://github.com/chadown62-glitch/Trader-/issues/39 ; commentaires : aucun.
- Mission Claude #38 lue ; aucun import/cherry-pick ni modification de son laboratoire/enseignants/ShadowCouncil.
- Base exacte #35 : 2078aa671bb0b87f9773de462686c41da0a30fb3.
- Branche LOCALE : work/mission-control-v1.
- Chemin de travail : /workspace/scratch/6168c8d38e5c/trader-ai.
- Checkpoint local 1 : 487ac291372a4ca9441cb4bc736994e38ab9a943, arbre 0b472f7218b7e642178a26ab3efea3eb35c71ac8.
- Checkpoint local 2 : 50427b9 (préfixe observé), parent checkpoint 1.
- Ces commits n’étaient pas poussés avant la perte d’accès. Le commit 1 et la branche distante de travail ont répondu 404 à la vérification GitHub.
- Des modifications supplémentaires et documents restent hors commit. Leur dernier état doit être inspecté après reconnexion.
- main n’a pas été modifié ; aucun service permanent, trading réel, clé financière ou promotion activé.

## Travail réellement développé localement

1. Contre-tests indépendants de #35 : quatre défauts reproduits (4 échecs avant correctifs) :
   métadonnées écrites avant verrou, échec fatal perdu dans finally, accord Windows sans preuves de tests/runtime,
   carnet rehashé non rapproché de la réponse HTTP brute.
2. Correctifs : verrou avant écriture, failed_closed/cause, fingerprint runtime/hôte/dépendances,
   rapport Windows scellé avec JUnit sans échec/erreur/skip, reçu HMAC local (aucune clé financière),
   vérification d’accord avant chaque tick, arrêt d’urgence indépendant des validations.
3. Ticks instrumentés CPU/temps mural, essai borné par max_seconds ; aucun uptime 24/7 supposé.
4. Pré-vol et copies SQLite/WAL stables ; API GET authentifiées sans renouvellement de session, écritures ou fetch.
5. Observatoire et exports sentinelle.evidence/v1, catégories synthétique/historique/prospective séparées,
   ledger manuel/IA explicitement non attribué, frais, partial exits, MTM, réconciliation, sources et couverture.
6. Vue Mission Control, tables, export JSON daté, AUC/Brier/calibration et métriques manquantes null.
7. Baselines causales CASH/HOLD/tendance/v2 à EUR30 en mémoire ; aucun entraînement ni tuning.
8. Derniers correctifs : bins de calibration, provenance d’une ancienne prévision non empruntée à un journal futur,
   profondeur liée à lastUpdateId conservée entre réceptions/redémarrages.
9. Une dernière correction UTC à la frontière de 4h a été tentée quand le runtime a disparu :
   son application/exécution NE DOIT PAS être considérée comme vérifiée.
10. Documentation Windows et contrat de fusion Claude, réservation future distincte, rédigés localement.

## Fichiers principaux à récupérer

- sentinelle/services/evidence.py, platform_validation.py, supervisor.py, performance.py
- sentinelle/preflight.py, sentinelle/api/server.py
- tools/supervise.py, observe.py, public_capture.py, public_probe.py, mission_control.py, pilot_packet.py
- tools/diagnose_windows.py, tools/diagnostic_windows.ps1
- tools/validation/evidence_bench.py
- tests/test_evidence.py, test_evidence_bench.py, test_mission_control_audit.py
- tests/e2e/mission_control_check.js et adaptations des tests existants
- web/js/views/mission_control.js, performance.js, web/js/main.js
- docs/contracts/evidence-v1.schema.json, mission-control-alpha-factory.md
- docs/validation/mission-control.md, docs/WINDOWS.md, README.md
- research/mission-control-v1/reservation.json et reservation.sha256 (nouvelle réservation, ne touche pas v2/v3)
- docs/validation/mesures/mission-control-baselines/
- docs/validation/mesures/mission-control-reproduction/
- docs/validation/mesures/mission-control-capture/
- docs/validation/mesures/mission-control-public-probe/

Les caches fetch.sqlite des sondes sont volontairement non versionnés. Les réponses HTTP brutes, ZIP et SHA le sont.
Le point de reprise ne contient aucune copie de ces fichiers : leur récupération nécessite l’accès au filesystem d’origine.

## Tests réellement terminés

- Premier contrôle global : 548 passed, 1 skipped, 0 failed, 0 xfail (147,32 s).
- Contrôle global suivant : 549 passed, 1 skipped, 0 failed, 0 xfail (146,35 s).
  Linux / Python 3.12, environnement vidé + seccomp, CCXT complet.
- Le seul skip est le panel sur socket locale sous seccomp ; test rejoué séparément : 1 passed, 6,93 s.
- Test spécifique de réutilisation de profondeur après réouverture : 1 passed, 1,31 s.
- Les contrôles complets suivants ont été lancés après de nouveaux correctifs : résultat final non récupéré.
  **549 ne qualifie donc pas le dernier arbre de travail.**
- Cinq parcours Chromium tentés : Mission Control, phase H, performance, champion, macro.
  Tous bloqués avant affichage par ProcessSingleton socket() failed: Operation not permitted. Aucun screenshot inventé.
- Windows 11 et PowerShell : NON TESTÉS.

Logs encore attendus dans le filesystem :
/tmp/trader39-full.log et .xml (548) ;
/tmp/trader39-final-full.log et .xml (549) ;
/tmp/trader39-socket.log et .xml ;
/tmp/trader39-release.log et .xml (dernier run, à examiner) ;
/tmp/trader39-utc-last-target.log (dernier essai, application incertaine) ;
/tmp/trader39-browser.log.

## Données publiques et résultats réellement mesurés

- Trois sondes GET /time archivées : HTTP 200, latences 7,274 / 0,144 / 0,151 s.
  Première inadmissible (>5s), P99 non estimable sur trois points.
- Capture publique bornée achevée en 8,73 s, 1500 bougies 4h fermées par BTC/EUR, ETH/EUR, SOL/EUR.
  Bornes UTC 2026-03-08 12:00 -> 2026-11-13 12:00 ; réception 2026-11-13 12:45:16,953.
  Clôture vieille de 2716,95s et requête à 6,52s : inadmissible pour un pas prospectif.
  Aucun ordre papier ou prédiction exécuté.
- ZIP local : docs/validation/mesures/mission-control-capture/public-capture.zip, 595586 octets.
  SHA256 aa2aebfabcbafc3cd8d61d77e9e958da58c397a47161d27929805c4cd98a238c.
  Rechargement offline avec intégrité vérifiée : test réussi.
- Archives préexistantes data/research/public-eur-v1 : 7662 bougies/actif, 2023-01-01 -> 2026-07-01,
  rechargées sans réseau, bruts et dérivés vérifiés, pas réécrites.
- Deux exécutions à paramètres identiques ont donné exactement les mêmes métriques et traces financières :
  2025-07-01 -> 2026-01-01 : CASH 30 ; HOLD 30,09301665 ; tendance 29,81053335 ; v2 30,98401625 EUR.
  2026-01-01 -> 2026-07-01 : CASH 30 ; HOLD 23,11758795 ; tendance 30 ; v2 30 EUR.
- v2 : 36 trades clôturés sur la première fenêtre, frais 0,4787527, drawdown 7,2634% ; zéro trade sur la seconde.
- Historique déjà exploré : descriptif uniquement, aucun holdout indépendant, aucune promotion ni avantage durable démontré.
- Le replay est en mode backtest OHLC coûts approximés. Il ne reproduit pas toutes les différences de taille
  du mode opérationnel training/en-essai ; ne pas le présenter comme le rendement du pilote futur.

## Reprise prioritaire

1. Rétablir l’environnement ; inspecter git status/log et les objets locaux avant toute réinitialisation.
2. Sauvegarder/pousser rapidement les checkpoints récupérés sur une branche non-main.
3. Vérifier l’application de la dernière correction UTC ; finir les tests ciblés puis la suite complète.
4. Vérifier les SHA v2/v3/modèles et code macro épinglé par comparaison à #35.
5. La nouvelle réservation future Nov16->Feb13 a été écrite avant les derniers correctifs :
   son code_sha peut être périmé. Ne pas la réécrire ; conserver l’original et, si nécessaire, enregistrer
   une nouvelle révision AVANT sa période, avec supersession explicite. Aucune observation future nouvelle.
6. Corriger les petites imprécisions des docs, notamment le mode backtest vs training/en-essai,
   et vérifier les versions/schema du contrat d’export.
7. Publier les arbres exacts (binaires ZIP inclus) et une PR brouillon sur work/autonomous-supervisor,
   avec résultats bruts réellement vérifiés. Aucun merge main.
8. Windows/browser restent des validations réelles à effectuer, puis accord utilisateur distinct avant service.

Ce point de reprise distant est volontairement séparé : il ne fait pas passer une note pour une livraison du code.
