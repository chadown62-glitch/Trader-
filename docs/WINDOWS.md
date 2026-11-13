# Windows 11 : diagnostic et pré-vol de l'observation

Mis à jour le 13 novembre 2026 (intégration #32/#33, mission #34).

> **État de la validation Windows : NON TESTÉ.** Les auteurs de cette version n'ont pas de
> Windows 11. Le diagnostic et la procédure ci-dessous ont été exécutés **sous Linux** uniquement.
> Le premier passage sur votre PC est la première vraie validation Windows. Tant qu'un rapport
> produit sur un vrai Windows 11 n'existe pas, la page « Préparation » du panel affiche
> « Installation validée sur Windows 11 : NON TESTÉ » et l'observation reste **BLOQUÉE**.

Rien de ce qui suit ne passe d'ordre réel, ne demande de clé, ne démarre de service permanent
ni ne modifie votre dossier `data`. Le lanceur historique peut installer les dépendances
dans `.venv` ; privilégiez le PowerShell isolé pour ce diagnostic.

---

## 1. Le plus simple : double-clic

`diagnostic.bat` lance deux contrôles l'un après l'autre :

1. le contrôle rapide habituel (Python, composants, accès aux données de marché, Ollama) ;
2. le **diagnostic complet** (`tools/diagnose_windows.py`), qui écrit un rapport dans le dossier
   `rapports\` ; ce rapport apparaît ensuite dans le panel, page **Préparation**.

## 2. La procédure complète (PowerShell), recommandée pour valider Windows 11

Elle installe TRADER AI **à neuf dans un dossier temporaire**, sans toucher à votre installation,
puis exécute le diagnostic et les tests rapides.

1. Ouvrez **PowerShell** dans le dossier de TRADER AI (clic droit dans l'Explorateur → « Ouvrir dans le terminal »).
2. Lancez :

   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\diagnostic_windows.ps1
   ```

   Options : `-SansReseau` (aucune lecture des prix publics), `-SansTests` (pas de tests rapides),
   `-Conserver` (garder le dossier temporaire pour inspection).
3. Attendez la fin (quelques minutes, surtout l'installation des composants). Le script s'arrête
   tout seul ; il ne laisse aucun programme en marche.
4. Envoyez au coordinateur les deux fichiers créés dans `rapports\` :
   `diagnostic-AAAAMMJJ-HHMMSS.json` et `diagnostic-windows-AAAAMMJJ-HHMMSS.log`.
   Ils contiennent des chemins de votre PC (nom d'utilisateur Windows) mais aucun secret.

### Ce que fait le script, étape par étape

| Étape | Détail | Ce qui est modifié |
|---|---|---|
| Système | Version et build de Windows (Windows 11 = build 22000 ou plus) | rien |
| Python | Vérifie `py -3.12` | rien |
| Copie | Copie le code **sans** `data`, `.venv`, `.git`, `rapports` vers `%TEMP%\trader-ai-diagnostic-…` | dossier temporaire |
| Environnement | Crée un Python neuf et installe `requirements.txt` + `pytest` | dossier temporaire |
| Diagnostic | `python -m tools.diagnose_windows --donnees <votre data> --reseau --rapport …` | `rapports\` seulement |
| Tests rapides | `pytest` sur le pré-vol, le diagnostic, le verrou, le portefeuille et l'API | dossier temporaire |
| Nettoyage | Supprime le dossier temporaire | — |

### Ce que contrôle le diagnostic

**Installation** (verdict « FONCTIONNELLE » ou « PROBLÈMES À CORRIGER », code de sortie 0 ou 1) :
- version de Python et composants ;
- base SQLite **dans un dossier temporaire** : création, contrôle d'intégrité, réouverture, portefeuille de 30 € fictifs en euros ;
- verrou « simulation uniquement » (le courtier réel refuse toute création) ;
- intégrité des modèles et protocoles gelés (v2 : protocole, modèle, réglages ; v3 : protocole, catalogue de sources, code d'analyse ; v1 écartée) ;
- port 8765 : libre, occupé par TRADER AI, ou occupé par un autre programme (utilisez alors `lancer.bat --port 8770`) ;
- **démarrage du vrai panel** en démonstration hors ligne, sur un port libre et des données temporaires :
  page servie, API protégée par mot de passe, protection contre les requêtes forgées, portefeuille 30 €,
  page de préparation ; **arrêt** ; **redémarrage** (mot de passe et portefeuille conservés) ; arrêt final ;
- votre dossier `data` : **lu seulement** (la base est lue sur une copie temporaire) ; son empreinte est
  comparée avant et après. Si TRADER AI tournait pendant le diagnostic, il a pu écrire lui-même :
  le rapport le signale en « ATTENTION ».
- avec `--reseau` : prix publics récents pour BTC/EUR, ETH/EUR, SOL/EUR (lecture publique Binance, sans compte).

**Observation prospective** : verdict **READY** ou **BLOCKED**, avec la raison de chaque blocage
(même contenu que la page Préparation du panel). Avec `--strict`, le code de sortie vaut 3 si BLOCKED.

## 3. Ce qui bloque aujourd'hui, et pourquoi c'est normal

Le superviseur #34 est livré et testé sur fixtures, mais reste **non activé et non validé sur
votre PC**. La présence de son fichier ne suffit jamais pour READY.

- **Accord distinct et rapport PC** : le plan doit correspondre aux empreintes du code, du modèle
  et de l'installation. Aucun lancement sans Windows 11 réellement testé et approbation explicite.
- **Périodes** : v2 commence le 13/11/2026 UTC, v3 le 14/11/2026. Le retard reste une couverture
  incomplète ; aucune prévision passée n'est reconstruite, aucune date n'est déplacée.
- **Réglages** : seuls ceux de `research/prospective-v2/settings.json` sont acceptés. Le contrôle
  refuse l'installation incompatible au lieu de changer ses réglages ou de réinitialiser son compte.
  Ne copiez pas une base d'essai sur vos données existantes. En cas de blocage, faites examiner
  le rapport pour préparer une migration explicite ; cette livraison ne la fait pas silencieusement.
- **Collecteur / opération** : le démarrage autorisé du collecteur peut compléter les 1 500 bougies
  4h publiques BTC/EUR, ETH/EUR, SOL/EUR. Une opération fictive exige en plus une clôture et des
  carnets admissibles lors du pré-vol #32. Historique ancien = archivage seulement.
- **Forex Factory** reste facultatif et désactivé faute de droits, sans bloquer les prix seuls.

## 4. Tests navigateur (développeurs)

Sur n'importe quel système avec Node.js et Playwright (`npx playwright install chromium`) :

```sh
python tests/e2e/run.py
```

Chaque parcours démarre un panel de démonstration jetable (port libre, dossier temporaire),
le teste en 1440 px et 390 px, puis l'arrête. Rien n'est fait sur votre installation.

## 5. Limites

- Le script PowerShell n'a jamais été exécuté sur Windows par ses auteurs.
- Le diagnostic ne vérifie ni Telegram, ni Ollama en profondeur, ni un long entraînement.
- Il ne remplace pas une surveillance continue : il s'exécute, conclut, puis s'arrête.
- Sous Windows, l'arrêt du panel de test est immédiat (pas d'arrêt « propre ») ; le redémarrage
  vérifie justement que la base reste saine après un arrêt brutal.

## 6. Plan de lancement reproductible — à utiliser après revue et accord

Ces commandes sont une **procédure future**, pas une demande de lancer le service maintenant.
Restez sur la branche intégrée ; ne fusionnez rien dans main. Fermez les autres agents autonomes,
arrêtez le panel pendant le diagnostic, sauvegardez votre dossier data et conservez les journaux.
Le compte principal existant doit être EUR/30 € fictifs ; les anciens comptes sont conservés.

1. Exécuter le PowerShell isolé de la section 2 **sans** `-SansTests` ni `-SansReseau`.
   Revoir JSON + transcript : Windows 11 build ≥22000, Python 3.12, SQLite/paper-only/modèles intacts,
   démarrage/arrêt/reprise du panel réussis, aucun contrôle indispensable omis, tests sans échec.
   Le script retourne maintenant un code non nul si diagnostic/tests échouent. Les empreintes du
   code et le chemin du data figurent dans le JSON. Ce rapport n'atteste pas les rendus du navigateur :
   refaire les six pages sur PC et téléphone (largeurs 1440/390) avant l'accord de lancement.
2. Préparer un plan dans un **nouveau fichier** (aucune collecte ni ordre) :

   ```powershell
   .\.venv\Scripts\python.exe -m tools.supervise prepare --data .\data --output .\rapports\lancement-001.json
   .\.venv\Scripts\python.exe -m tools.supervise status --data .\data
   ```

   Le CLI exige un environnement déjà installé et une installation compatible. `prepare` ne la
   crée pas et ne la corrige pas. Conserver le plan et son `.sha256`. Toute modification de code
   impose un nouveau diagnostic et un nouveau plan, sans toucher aux protocoles scientifiques.
3. **Demander et obtenir votre accord explicite distinct**, après examen du rapport et du plan.
   Ensuite seulement, enregistrer cet accord localement sur votre PC Windows réellement validé :

   ```powershell
   .\.venv\Scripts\python.exe -m tools.supervise approve --plan .\rapports\lancement-001.json --windows-report .\rapports\diagnostic-AAAAMMJJ-HHMMSS.json --output .\rapports\accord-001.json --confirm "AUTORISER OBSERVATION PAPIER CONTINUE"
   ```

   Ce reçu ne démarre rien. Refus hors Windows 11, rapport incomplet, code/installation différents,
   checksum changé, modèle/réglages/période remplacés ou confirmation absente.
4. Avec cet accord, premier **essai supervisé borné** au premier plan :

   ```powershell
   .\.venv\Scripts\python.exe -m tools.supervise run --plan .\rapports\lancement-001.json --approval .\rapports\accord-001.json --ticks 1
   .\.venv\Scripts\python.exe -m tools.supervise status --data .\data
   ```

   Un lancement loin de la clôture conserve l'historique, sans entrée tardive. Vérifier captures,
   carnets, refus éventuels, journal et comptabilité. Attendre une clôture future fraîche pour
   attester un pas admissible. Le panel n'est READY qu'avec preuves récentes et collecteur actif ;
   après l'essai terminé, son statut actif repasse à faux, comme attendu.
5. **Uniquement dans le périmètre de l'accord donné**, fonctionnement continu supervisé :

   ```powershell
   .\.venv\Scripts\python.exe -m tools.supervise run --plan .\rapports\lancement-001.json --approval .\rapports\accord-001.json --continuous
   ```

   Aucun démarrage automatique. Garder la console et contrôler le bilan quotidien. Ctrl+C arrête
   proprement après la requête en cours. Depuis une autre console :

   ```powershell
   .\.venv\Scripts\python.exe -m tools.supervise stop --plan .\rapports\lancement-001.json
   .\.venv\Scripts\python.exe -m tools.supervise resume --plan .\rapports\lancement-001.json
   ```

   `resume` retire seulement le drapeau d'arrêt : **il ne lance aucun processus**. Relancer `run`
   avec le même plan/accord et le même data reprend l'état durable ; ne supprimez pas la SQLite
   latérale, les captures, le WAL ou le fichier de verrou. Un verrou OS empêche deux superviseurs.
   Au plafond de disque (3Go), conserver/archiver explicitement les preuves avant reprise.

Rapports à retourner : diagnostic JSON + transcript + éventuel échec navigateur + `status`, puis
bilans `data/supervisor/daily/` du lancement autorisé. Ces fichiers peuvent contenir des chemins
locaux ; aucune clé financière n'est requise ni chargée.

## Pilot packet Mission Control #39 (prioritaire sur les anciennes commandes stop)

**Préparé, non démarré. Windows/PowerShell NON TESTÉS dans cette livraison.**
Lancer depuis un terminal PowerShell dans le dossier du projet, code revu et
aucun autre agent actif. Le diagnostic ne réinitialise pas votre portefeuille.
Prévoir Python 3.12, au moins 2 Go libres pour l'environnement et les tests,
puis le budget d'archives configuré (3 Go maximum). Les exigences CPU/RAM
exactes ne sont pas attestées sur votre PC ; surveiller l'essai borné.

```powershell
powershell -ExecutionPolicy Bypass -File tools\diagnostic_windows.ps1
```

Le script copie le code dans TEMP, installe ses dépendances dans un venv jetable,
lit votre dossier data, teste un panel démo temporaire, l'arrête, exécute toute
la suite avec JUnit dans rapports/. Il scelle le JSON seulement si diagnostic
et tests sont réussis, sans skip. Un diagnostic `-SansTests` reste informatif,
inutilisable pour autoriser le lancement. Les logs/rapports/JUnit restent sur
le PC ; la clé de sceau locale `~/.sentinelle/diagnostic.key` n'est pas financière
et ne doit pas être publiée.

L'accord vérifie le même hôte, la version/binaire Python et les mêmes versions
de dépendances : il faut exécuter le pilote dans un environnement correspondant.
Si les versions diffèrent, le lancement est refusé : refaire le diagnostic avec
les dépendances correspondantes, ne pas éditer le rapport pour contourner le refus.
Le sceau n'est pas une attestation matérielle. Le rendu 1440/390 est à tester
séparément sur ce PC avec `python tests/e2e/run.py` et Playwright/Chromium installé.
Ne pas donner l'accord avant revue de ces preuves et des blocages Préparation.

Conditions : code/modèle/protocoles/SHA vérifiés, portefeuille principal EUR30
fictifs existant, aucun concurrent, UTC/skew <5s, >=1500 bougies closes par paire,
clôture fraîche <180s, carnets <30s, chaque latence HTTP <=5s, période gelée
ouverte, reprise/panne et arrêt testés. Un HTTP 200 seul ne suffit pas.

Après validation PC et **autorisation utilisateur explicite distincte seulement** :

```powershell
python -m tools.supervise prepare --data data --output rapports\pilot-plan.json
python -m tools.supervise approve --plan rapports\pilot-plan.json --windows-report rapports\diagnostic-REMPLACER.json --output rapports\pilot-approval.json --confirm "AUTORISER OBSERVATION PAPIER CONTINUE"
python -m tools.supervise run --plan rapports\pilot-plan.json --approval rapports\pilot-approval.json --ticks 1 --max-seconds 120
```

`--max-seconds` est vérifié entre callbacks ; il ne tue pas une transaction
synchrone. Par défaut un seul tick, aucun service ni tâche Windows installé.
Le passage à `--continuous` nécessite l'autorisation distincte ; aucune commande
continue n'a été exécutée ici. Prévoir arrêt de l'essai et revue du journal avant
la décision de passer en continu. Consultation en lecture seule :

```powershell
python -m tools.supervise status --data data
python -m tools.mission_control --data data --output rapports\evidence-NOUVELLE-DATE.json
python -m tools.supervise stop --data data
```

L'arrêt dépose un STOP indépendant de l'intégrité du plan. Il sera lu au prochain
tick (ou Ctrl+C interrompt l'attente). Il ne clôture pas les positions à prix
périmé. Après arrêt, rétablir des prix récents et revoir le risque ; la machine
éteinte n'offre aucune garantie de protection des positions fictives. Pour
réarmer seulement, sans démarrer : `python -m tools.supervise resume --plan
rapports\pilot-plan.json`. Un changement de plan/code/runtime invalide l'accord.
