# Archives publiques et protocole EUR

Téléchargement exclusivement public, sans clé :

```sh
python -m tools.collect_public --root data/research/public-eur-v1 --start 2023-01-01 --end 2026-07-01
```

L'API spot publique Binance est paginée à 1 000 bougies. La période est en UTC,
borne de début incluse et fin exclue. BTC/EUR, ETH/EUR et SOL/EUR sont les seules
paires autorisées par cet outil. Il n'y a ni compte ni ordre financier.

Chaque réponse brute est conservée sans modification. Une relance avec les mêmes
paramètres reprend les pages existantes ; une autre période exige un autre dossier.
Une réponse invalide conservée exige une intervention explicite : elle n'est pas
écrasée. Les valeurs OHLCV positives, volumes, ordre des dates, doublons, continuité
et alignement 4 h sont contrôlés. Les mouvements supérieurs à 50 % sont signalés
sans réparation. Une période comportant des bougies ouvertes est refusée.
Aucun trou n'est comblé artificiellement. Si le processus meurt pendant l'écriture
d'une page, la page partielle est conservée et doit être inspectée explicitement.
L'outil suppose un seul collecteur par dossier ; les écritures sont exclusives.

Le manifeste final référence les réponses par SHA-256 et les données dérivées
par une empreinte distincte. Les décimaux sont sérialisés exactement, sans passer
par float64 pour les empreintes. Chaque chargement vérifie manifeste, pages et
reconstruction. Le protocole épingle le manifeste : un manifeste et sa somme
locale seuls ne constituent pas une authentification indépendante de l'archive.
Les versions d'archives sont des dossiers séparés, jamais remplacés.

## Évaluation enregistrée avant entraînement

`protocols/public-eur-exploratory-v1.json` est enregistré dans le commit
`99e9798`, avant la première évaluation. Il fixe actifs, capital, frais,
slippage, risque, horizon, tailles 500/1500, périodes et références. Le pipeline
Sentinelle conserve son ajustement/calibration/sélection/contrôle temporels avec
purge de toutes les issues aux frontières. Chaque fenêtre réellement utilisée
est inscrite dans les résultats. Les contraintes de minimums et de précision
sont **modélisées fixes**, pas des filtres de plateforme reconstruits historiquement.
La simulation OHLC ne représente pas la profondeur ni la priorité dans un carnet.

```sh
python -m tools.validation.historical register --root data/research/public-eur-v1 --protocol NOUVEAU-PROTOCOLE.json
python -m tools.validation.historical run --root data/research/public-eur-v1 --protocol docs/validation/protocols/public-eur-exploratory-v1.json --output data/research/experiments/NOUVEL-ESSAI
```

Le runner n'importe aucun téléchargeur, vérifie les archives hors réseau, utilise
le vrai trainer, le vrai agent, le broker fictif et un compte en mémoire à 30 EUR.
L'identifiant d'essai est réservé avant l'évaluation et ne peut être réutilisé,
même après un échec. Les résultats incomplets et négatifs sont conservés.

**Les périodes 2023–2026 ont déjà été étudiées dans `docs/RESULTATS.md`.**
Les expériences présentes sont exploratoires. Leur protocole interdit toute
promotion et le runner ne modifie aucun registre ni portefeuille principal.
Une période prospective proposée est réservée du 13 novembre 2026 au
13 février 2027, sans lancer de service permanent dans cette session. Il faudra
préenregistrer le collecteur et le modèle réellement déployé avant observation.

## Windows

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m tools.diagnose_windows --reseau --rapport
.\.venv\Scripts\python.exe -m sentinelle --demo --no-browser --data "$env:TEMP\sentinelle-validation" --port 8765
```

Procédure complète et rejouable (installation neuve en dossier temporaire, diagnostic, tests rapides) : [docs/WINDOWS.md](../WINDOWS.md) et `tools/diagnostic_windows.ps1`. Le diagnostic utilise un dossier temporaire, contrôle SQLite et le portefeuille
après réouverture, l'API de performances et le refus du broker réel. Il n'exige
aucun GPU. Un diagnostic exécuté sous Linux **ne prouve pas** la compatibilité
Windows. Les tests complets, navigateur, installation et entraînement sous
Windows restent à exécuter sur Windows 11 ; ne pas utiliser les données réelles
existantes pour ce premier essai. Ne pas transférer le lanceur seccomp Linux sous
Windows : pour les tests de sécurité, couper les sorties réseau au niveau du
système ou employer un environnement de test isolé approprié.
