# Sprint 1 — assemblage #50–#52 et encodage Windows

Branche : `feat/sprint1-assemble-50-52`. PR destinée à `main`, sans fusion.

## Base et gouvernance

Base distante réellement observée au départ : `e09c84ab2c2da79bb683a0801c95c180cc817557`.
Malgré l'annonce de fusion, #56 était encore `open`, `merged=false` via GitHub,
et les trois fichiers de gouvernance étaient absents après `git fetch`.
Son commit `6593e853512f70895461c61d349666f746966ecf` a donc été repris
uniquement dans cette branche d'intégration. Aucun changement direct de `main`.
`SPEC.md` est identique à #56. `CLAUDE.md` et `AGENTS.md` sont identiques entre eux
et portent seulement la dérogation unique autorisée pour cette PR ; la limite
de six fichiers demeure pour toutes les futures fonctionnalités.

## Assemblage et conflits

Trois merges locaux séquentiels, sans conflit :

1. #50 : `3a8a04869d134fcad1e199d795829d3731e294d9`.
2. #51 : `f4edab87e511df3ad250586ddc81ce0c431422d7`.
3. #52 : `eec05e789e231beab4ad5301559d8c5aae7f6a30`.

Les liens d'ascendance #50 → #51 → #52 ont été vérifiés. Le code métier
`sentinelle/dmh`, `tools/dmh_capture.py` et `web/js/views/dmh.js` est identique
à #52 : ses corrections de sécurité ne sont pas remplacées par #51.
Les dépendances antérieures déjà contenues dans #50 sont conservées.
Aucun merge, rebase ou push sur #53, #54 ou #55 ; ces trois HEAD ne sont pas
ancêtres de la branche d'intégration.

## Correction limitée à l'encodage

45 accès texte dans 27 fichiers Python intégrés rendent explicite `encoding="utf-8"` :
lectures/écritures Path, JSON, journaux de test et CSV gzip texte. Les accès
binaires (modèles, ZIP, checksums calculés sur octets) restent inchangés.
L'inventaire AST ne trouve plus de `.read_text()` ou `.write_text()` sans
encodage dans les fichiers intégrés ; les autres `.open()` ont été distingués
des méthodes métier d'ouverture de positions et des accès ZIP/binaires.
Aucune nouvelle fonctionnalité métier, aucun recalcul de résultat scientifique.

Un test ciblé remplace la locale implicite des accès texte par `cp1252` et
vérifie l'écriture puis la relecture de noms grecs et chinois par le vrai chemin
de préenregistrement, dans un dossier temporaire. Il passe sous Linux.
Ce test ne prétend pas reproduire tout Windows ni exécuter une expérience.

## Validation

Les logs et JUnit finaux sont archivés dans `mesures/sprint1-win11/`.
Suite complète : **1032 passed, 1 skipped, 0 failed, 0 errors**. Durée **488,78 s**, huit avertissements conservés dans `full.log`, dont le chargement d’estimateurs scikit-learn 1.9.1 sous 1.8.0. Aucun modèle gelé réécrit ; compatibilité de ces modèles à revalider avant lancement.
Python 3.12.14, CCXT 4.4.99, Linux, environnement vidé, réseau bloqué par seccomp
hérité des sous-processus. Le test exigeant une socket locale est rejoué
séparément : **1 passed en 6,99 s**, démarrage/API/arrêt/redémarrage/arrêt final.
Le test de locale : **1 passed en 1,05 s**. Aucun test supprimé ou désactivé.
Le lint ciblé signale un import `json` inutilisé déjà présent dans
`tests/test_public_archive.py` de #52 ; il n'est pas corrigé hors périmètre.

## Vérification locale Windows 11 / Python 3.12

Depuis le checkout de cette branche dans PowerShell :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q
```

Démarrer l'API et le panel sur des données de démonstration jetables,
sans utiliser ni réinitialiser le portefeuille utilisateur :

```powershell
$demoData = Join-Path $env:TEMP ('sentinelle-sprint1-' + [guid]::NewGuid().ToString())
.\.venv\Scripts\python.exe -m sentinelle --demo --no-browser --data $demoData --port 8765
```

Dans une seconde fenêtre PowerShell :

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
Start-Process msedge 'http://127.0.0.1:8765/'
```

Vérifier l'affichage, l'authentification locale et le bandeau de simulation du
Hub ; le résultat reste fictif. Arrêter le serveur avec `Ctrl+C`.
Ces commandes ne créent aucune tâche planifiée ni service Windows.
**Windows 11 et Edge n'ont pas été exécutés dans cet environnement Linux.**

## Gel scientifique

Valeurs vérifiées avant les travaux ; revérifiées sur GitHub avant publication, strictement inchangées :

- #54 : `5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18`.
- #55 : `18b31bb901f656d6126b370f47c510775f135c57`.

Aucune écriture sur ces branches ou leurs artefacts, aucun accès à leurs
résultats pour optimiser une stratégie. Aucune promotion, transaction réelle,
clé privée, nouveau modèle ou service permanent activé.
