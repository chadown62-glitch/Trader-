# Mission Control #39 — livraison reconstruite

**Préparé / NON DÉMARRÉ. Windows 11 NON TESTÉ ; navigateur NON VALIDÉ.**
Base exacte : #35, `2078aa671bb0b87f9773de462686c41da0a30fb3`.
Branche : `work/mission-control-v1`, PR cible `work/autonomous-supervisor`.

## Récupération avant toute reconstruction

Le dossier original `/workspace/scratch/6168c8d38e5c/trader-ai` est accessible,
mais correspond à #35. Son status était propre ; aucun reset effectué.
Inspection des branches, reflogs, objets et fichiers de tous les checkouts disponibles :
`487ac291372a4ca9441cb4bc736994e38ab9a943` et `50427b9` absents, ainsi que
`evidence.py`, les archives, les fichiers non commités et les logs `/tmp/trader39*`
cités dans `work/mission-control-v1-resume`. Aucun outil de reconnexion à un
ancien environnement n'est exposé. **LOST_LOCAL_ARTIFACTS dans l'environnement
accessible** ; cela ne prouve pas leur effacement sur toute infrastructure externe.

Reconstitution dans un NOUVEAU worktree ; dépôt original préservé. Les anciens
548/549 tests et la réservation future décrite dans le handoff ne qualifient pas
cet arbre. Ils ne sont pas repris comme preuves. Les archives historiques déjà
présentes ont été réutilisées, sans les télécharger ou les réécrire.

## Audit indépendant et correctifs

Les quatre contre-tests appliqués à #35 échouent réellement (logs/JUnit
`mesures/mission-control/base-countertests.*`) :

1. Le second CLI écrit des métadonnées avant le verrou global. Le CLI corrigé
   acquiert maintenant le verrou OS avant toute ouverture State/métadonnée/backend.
2. Une exception fatale devient `stopped` dans finally. Elle reste maintenant
   `failed_closed` avec son type et sa date ; l'exception se propage.
3. Un simple JSON avec `windows_actually_tested=true` suffisait à l'accord.
   Il faut désormais un rapport local scellé HMAC (clé locale non financière),
   JUnit complet sans échec/erreur/skip, hôte, binaire Python, dépendances et
   hashes du code. L'accord est revérifié à chaque tick. Ce sceau est une preuve
   locale d'intégrité, **pas une attestation matérielle ou une défense contre
   l'administrateur de la machine**.
4. Le carnet dérivé pouvait changer après rehash sans changer les HTTP bruts.
   Symbol/endpoint, SHA, bids/asks, lastUpdateId et réception sont rapprochés.
   Le REST ne donne toujours pas d'heure d'événement exchange : limite conservée.

Ajouts : profondeur consommée liée à l'identité exchange, persistante entre
réceptions et redémarrages ; horloge de clôture revérifiée après collecte ;
STOP indépendant d'un plan corrompu ; snapshots SQLite/WAL refusés si le writer
change pendant la copie ; essai à durée finie (contrôle entre callbacks, pas
interruption forcée d'une transaction synchrone) ; mesures CPU/wall des ticks
achevés, sans extrapolation de disponibilité.

## Observatoire livré

API GET authentifiées `/api/evidence` et `/api/evidence/export`, sans renouveler
la session ; copies SQLite privées, pas d'App/broker/downloader démarré par la
lecture. Commande d'export daté :

```powershell
python -m tools.mission_control --data data --output rapports/evidence-20261113.json
```

Le chemin de sortie doit être neuf. L'export possède un SHA-256 et le schéma
`sentinelle.evidence/v1` ; voir `docs/contracts/evidence-v1.schema.json`.

Vue Mission Control : catégories synthétique / historique / prospective
séparées, grand livre unique EUR30 et attribution manuel/IA explicitement
inconnue. Réalisé net et frais inclus viennent des positions comptabilisées,
rapprochement par actif avec le ledger, fills/partiels/outbox, dernière equity
horodatée et âge. **Aucun MTM actuel n'est inventé sans prix/FX récents** : null,
ainsi que coûts de fonctionnement, vitesse d'apprentissage et uptime continu.

Le runner enregistre désormais la provenance capture/protocole/modèle/réglages
avant les prédictions. Une prévision sans cette preuve antérieure demeure non
classée ; un rapport public ultérieur ne peut pas la qualifier rétroactivement.
Les scores AUC/Brier/calibration utilisent seulement les labels matures connus
à l'heure de l'export. L'issue théorique d'un signal reste distincte du P&L du
portefeuille. Les bins incluent p=1. Le statut global reste
`NOT_ENOUGH_EVIDENCE` ou `BLOCKED`, jamais « stratégie gagnante ».

## Archives et replays

Deux replays complets ont redonné **exactement les mêmes métriques et SHA des
traces financières**, sur 7 662 bougies 4h par BTC/EUR, ETH/EUR, SOL/EUR :

| Bras indépendant EUR30 | Juil.–déc. 2025 | Janv.–juin 2026 |
|---|---:|---:|
| CASH | 30,00000000 | 30,00000000 |
| HOLD plafonné | 30,09301665 | 23,11758795 |
| Tendance simple | 29,81053335 | 30,00000000 |
| V2 gelé | 30,98401625 | 30,00000000 |

Ces périodes ont **déjà été explorées**. Pas de nouvel entraînement, tuning,
holdout indépendant, promotion, gain promis ni extrapolation au pilote.
Backtester OHLC, frais/slippage et contraintes de marché modélisés ; pas de
reconstruction L2 historique ni équivalence avec le sizing opérationnel
training/en-essai. Les quatre comptes sont uniquement des contrefactuels en
mémoire, pas quatre portefeuilles principaux persistants.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m tools.validation.evidence_bench --archives data/research/public-eur-v1 --output rapports/bench-neuf
```

La spécification et les résultats sont immuables dans chaque dossier de sortie.
Les résultats nuls/négatifs restent conservés. Intervalles par blocs de 7 jours,
confiance corrigée pour huit bras/fenêtres ; la comparaison relative appariée et
le contrôle de régimes pour conclure à une supériorité restent une étape future.

## Sondes réelles bornées

Trois nouveaux GET /time : HTTP 200, 8,291 / 0,311 / 0,312 s. La première
requête est **inadmissible** au seuil 5 s. Trois points ne donnent pas un P99.
Capture réelle finie : 1 500 bougies 4h closes/actif, 11,39 s au total.
Latence maximale 7,025 s, dernière clôture âgée d'environ 5 492 s :
**inadmissible pour un pas prospectif**. Zéro prédiction ou ordre exécuté.
Bruts, manifestes et ZIP conservés ; vérification d'intégrité hors réseau réussie.
SHA ZIP : `5b37a77eb79504411354e6af86bf88eabf6153ed11443c5022b65eba8673626e`.
L'absence d'observation depuis le début de v2 reste une lacune, sans rattrapage.

## Vérification reproductible

Les fichiers bruts datés sont dans `mesures/mission-control`. Les quatre échecs
sur #35 sont attendus et ne sont pas des échecs de la branche livrée. Voir
`verification.json` pour les nombres exacts du dernier arbre vérifié.

```bash
cc -O2 -Wall -Wextra -Werror tools/security/offline_exec.c -o /tmp/mc-offline
env -i PATH=/usr/bin:/bin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/mc-offline /chemin/python3.12 -m pytest -o addopts='' -q --junitxml=/tmp/mission-control.xml
```

Seccomp hérité par les sous-processus, CCXT complet ; le panel sur socket locale
est rejoué séparément. Parcours Chromium existant et Mission Control tentés :
blocage avant affichage par socket Unix ProcessSingleton interdite. Aucun
screenshot ni succès visuel annoncé. Windows et PowerShell non disponibles ici.

## Limites de livraison et suite

Ce travail reconstitue les protections et l'observatoire, pas tous les fichiers
perdus à l'octet près. Couverture financière quotidienne multi-actifs complète,
MTM authentique instantané et statistiques de supériorité appariées restent
incomplètes tant que les preuves prospectives manquent. Le registre exporte
les absences ; il ne les remplit pas par interpolation.

Aucune nouvelle réservation prospective n'est fabriquée depuis la description
du fichier perdu. V2/v3 et leurs modèles demeurent inchangés. Contrat et matrice
de fusion avec Claude : `docs/contracts/mission-control-alpha-factory.md`.
Les enveloppes du blueprint #42 attendent une mission ultérieure après revue.
