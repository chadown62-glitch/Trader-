# Windows 11 : diagnostic et pré-vol de l'observation

Mis à jour le 12 novembre 2026.

> **État de la validation Windows : NON TESTÉ.** Les auteurs de cette version n'ont pas de
> Windows 11. Le diagnostic et la procédure ci-dessous ont été exécutés **sous Linux** uniquement.
> Le premier passage sur votre PC est la première vraie validation Windows. Tant qu'un rapport
> produit sur un vrai Windows 11 n'existe pas, la page « Préparation » du panel affiche
> « Installation validée sur Windows 11 : NON TESTÉ » et l'observation reste **BLOQUÉE**.

Rien de ce qui suit ne passe d'ordre réel, ne demande de clé, ne démarre de service permanent
ni ne modifie votre dossier `data`.

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

Au 12 novembre 2026, même sur un PC parfaitement installé, le verdict est **BLOCKED** :

- **Lanceur d'observation continue** : pas encore livré (mission #30 : collecte continue fiable,
  reprise après panne, comparaison contrefactuelle). C'est voulu : sans lui, rien ne doit être déclaré prêt.
- **Période réservée** : elle commence le 13/11/2026 00:00 UTC.
- **Réglages gelés** : l'installation par défaut suit 8 cryptos en USDC ; le protocole gelé exige
  exactement les réglages de `research/prospective-v2/settings.json` (BTC, ETH, SOL en euros…).
  Le lanceur de la mission #30 devra les appliquer sur une installation dédiée, jamais à la main pendant une observation.
- **Historique** : il faut au moins 1 500 bougies de 4 h par crypto ; elles seront téléchargées
  (données publiques) au démarrage de l'observation, jamais pendant un contrôle.
- **Windows 11 : NON TESTÉ** tant que la procédure ci-dessus n'a pas été exécutée sur votre PC.

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
