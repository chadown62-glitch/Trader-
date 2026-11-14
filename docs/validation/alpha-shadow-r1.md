# ALPHA SHADOW — RegimeRouter-R1 : candidat unique, protocole prospectif « shadow » et méthode de preuve (mission #41)

Branche `claude/alpha-shadow-r1`, empilée sur la PR #40 (`claude/alpha-factory-v1`, tête `529f4c5`).
Rien n'est fusionné, `main` n'est pas touché, aucun service n'a été démarré, aucun ordre réel ou fictif
n'est passé par ce laboratoire. Les protocoles gelés `research/prospective-v2` et
`research/macro-shadow-v3` sont identiques à l'octet près (test `test_frozen_v2_and_v3_protocols_are_bit_identical`).
Aucun fichier du périmètre de GPT Work (#39 : superviseur, collecte, API Préparation, SQLite, pages,
outbox, fichiers Windows) n'est modifié.

**État du protocole : PRÉENREGISTRÉ, NON DÉMARRÉ.** L'étude prospective exige un accord distinct du
propriétaire (voir § 9).

## 0. Résultats en bref

1. **Le candidat est gelé.** RegimeRouter-R1 est un fichier (`research/alpha-shadow-r1/r1-model.json`,
   empreinte `33c765de…8123911`) : une table, une marge, des règles. Il ne peut plus apprendre ni être
   réglé ; toute modification change son empreinte et il est refusé.
2. **Sur sa fenêtre de contrôle (déjà étudiée, donc exploratoire), R1 perd.** Du 11/10/2025 au 13/11/2026,
   sur BTC/EUR, ETH/EUR et SOL/EUR : R1 −4,25 € sur 30 € (baisse maximale 22 %) ; « toutes les entrées »
   −7,99 € (baisse maximale 31 %). R1 fait mieux que la référence, mais ne gagne pas. Avec la règle
   préenregistrée, ce résultat s'appelle « non démontré ».
3. **Le protocole est écrit et immuable** (`research/alpha-shadow-r1/protocol.json`, empreinte
   `57db07cc…2fc6b4a`) : une hypothèse, un candidat, une comparaison primaire, une fenêtre future de
   365 jours qui ne peut pas commencer avant le 16/11/2026, un essai, une lecture à la fin.
4. **L'analyse de puissance est défavorable, et c'est un résultat.** Avec la volatilité observée, un an
   ne permet de prouver qu'un avantage d'environ +50 % par an (puissance 91 %). Pour +20 % par an la
   puissance est de 15 % ; pour +5 % par an elle est nulle. Le protocole peut réfuter R1 (critère
   d'arrêt, régression) ; il a très peu de chances de le confirmer.
5. **Rejoué avec les règles du protocole, R1 est arrêté par la limite de risque.** Le critère d'arrêt est
   la limite du projet (baisse maximale de 10 %). Sur la fenêtre de contrôle, le moteur arrête R1 le
   11/12/2025, huit semaines après le début, après 18 trades dont 17 perdants (§ 6.1). Si l'avenir
   ressemble à cette période, R1 sera rejeté bien avant la fin.
6. **L'infrastructure de preuve fonctionne et se laisse difficilement tromper** : décisions écrites avant
   le prix suivant, refus de toute anomalie de données, portefeuille simulé indépendant par bras,
   registre ancré, reprise après arrêt brutal sans décision antidatée (§ 5, § 8). Sur les données réelles,
   le moteur redonne le calcul de développement au dernier chiffre, et le même registre (55 908 lignes)
   sous deux versions de Python (§ 6.1).
7. **Un défaut d'évaluation de la PR #40 est mesuré** (§ 6.2) : les positions encore ouvertes en fin de
   fenêtre y étaient écartées, ce qui peut déplacer un résultat de plus de 2 € sur 30 €.
8. **« 40 fois moins cher » était sous-estimé** (§ 7) : le routeur coûte environ 290 fois moins de temps
   de calcul que le modèle du projet (90 fois en temps écoulé, 16 fois en mémoire). Cela reste un critère
   secondaire : à ce prix il obtient le même résultat, pas un meilleur. Ni l'un ni l'autre ne prédit mieux
   que le simple taux de gain de l'enseignant.

**Recommandation.** Ne pas engager un an d'observation sur R1 en l'état : la preuve positive est hors
de portée statistique et le critère d'arrêt a de fortes chances de se déclencher. L'infrastructure,
elle, est réutilisable telle quelle pour un futur candidat. La décision appartient au propriétaire.

## 1. Ce qui est livré

| Élément | Fichiers |
|---|---|
| Contrat `alpha_shadow_contract_v1` (bougie, carnet, décision, référence, résultat mûr, rapport) | `sentinelle/alpha/shadow/contract.py` |
| Série en ligne, indicateurs sans futur | `sentinelle/alpha/shadow/online.py` |
| Candidat figé R1 | `sentinelle/alpha/shadow/r1.py`, `research/alpha-shadow-r1/r1-model.json` |
| Registre en pas atomiques, ancres, reprise | `sentinelle/alpha/shadow/ledger.py` |
| Moteur shadow (admission, décisions, simulation par bras) | `sentinelle/alpha/shadow/engine.py` |
| Analyse préenregistrée, verdicts, puissance | `sentinelle/alpha/shadow/analysis.py` |
| Manifeste immuable, journal des essais | `sentinelle/alpha/shadow/protocol.py`, `research/alpha-shadow-r1/` |
| Développement et gel (hors moteur) | `sentinelle/alpha/shadow/develop.py`, `reference.py` |
| Audit du coût de calcul, courbes d'apprentissage | `sentinelle/alpha/fastlearner.py`, `tools/alpha_bench.py` |
| Commande de recherche bornée | `tools/alpha_shadow.py` |
| Tests | `tests/test_alpha_shadow_contract.py`, `_engine.py`, `_protocol.py`, `test_alpha_fastlearner.py` |

Une seule modification d'un fichier existant : `sentinelle/alpha/execution.py` accepte un prix d'entrée
observé (`entry_fill`, facultatif, comportement inchangé sans lui).

## 2. Le candidat : RegimeRouter-R1

**Algorithme et hyperparamètres** : ceux de la PR #40, inchangés (a priori 20, marges −∞ / 0 / 0,05 /
0,1 / 0,2, au moins 15 entrées pour retenir une marge, 6 climats = moyenne 50 au-dessus de la moyenne 200
× tercile du rang de volatilité sur 120 bougies). À la clôture d'une bougie, pour chaque entrée proposée
par un enseignant et non refusée par l'enseignant d'abstention :
`score = somme des R nets / (nombre + 20)` ; **entrée si le score dépasse la marge**.

**Données de développement** : bougies publiques de 4 h, Binance, du 01/06/2021 au 13/11/2026 exclu
(11 946 bougies par actif, aucune manquante), téléchargées avec l'outil du projet
(`tools.collect_public`, archive immuable). Empreintes des jeux : BTC/EUR `b34e4c88…`, ETH/EUR `6722134b…`,
SOL/EUR `4151bb81…`. Coûts : 0,1 % par ordre, glissement 5 points de base, écart 4 points de base.

**Chaîne de gel** (procédure publiée dans le commit `60243e1` **avant** son exécution) :

| Étape | Période | Situations | Rôle |
|---|---|---|---|
| Apprentissage | 01/06/2021 → 21/02/2024 | 2 154 | issues mûres dans la table |
| Calibration indépendante | 21/02/2024 → 16/12/2024 | 729 | fenêtre disjointe, ajoutée à la table rétrécie |
| Présélection unique | 16/12/2024 → 10/10/2025 | 702 | choix de la marge (5 valeurs essayées) |
| Contrôle | 11/10/2025 → 13/11/2026 | 726 | ne règle rien : activité et puissance |

75 situations dont l'issue chevauchait une frontière ont été purgées. Marge retenue : **0,0**. Enseignant
de référence (meilleur R net moyen, apprentissage + calibration) : **cassure**.

**La table gelée** (18 cases) : R1 entre sur la cassure dans les 6 climats (scores +0,14 à +0,59 R), sur
la tendance dans un seul climat (tendance haussière, volatilité basse : +0,21 R), sur le retour à la
moyenne dans un seul climat appuyé sur 7 observations (+0,04 R). Il refuse le repli partout.

Le bras descriptif « modèle du projet » est figé dans `reference-project-mixte.joblib` (produit avec
scikit-learn 1.9.1 ; il se charge sous la version épinglée 1.8.0 avec un avertissement de version et y
donne exactement les mêmes décisions).

**Résultats de développement (EXPLORATOIRES, 30 € fictifs, calcul d'un seul bloc)** :

| Bras | Sélection : P&L (€) | Sélection : trades | Contrôle : P&L (€) | Contrôle : trades | Contrôle : baisse max. |
|---|---|---|---|---|---|
| **R1** | +1,60 | 92 | **−4,25** | 130 | 22,2 % |
| Toutes les entrées (référence primaire) | +1,69 | 202 | −7,99 | 264 | 30,9 % |
| Enseignant cassure seul | +2,18 | 79 | −3,86 | 109 | 22,1 % |
| Modèle du projet (mixte) | +2,58 | 82 | −3,86 | 119 | 22,7 % |
| Ne rien faire | 0,00 | 0 | 0,00 | 0 | 0 % |
| Achat-conservation | −2,28 | 3 | −14,14 | 3 | — |

Lecture : sur le contrôle, R1 perd moins que la référence (écart quotidien apparié +0,038 %, intervalle
à 95 % [+0,005 % ; +0,075 %]) mais son rendement absolu n'est pas positif (intervalle [−0,12 % ; +0,08 %]
par jour). Avec la règle du protocole : **non démontré**. Cette période a déjà été étudiée par le projet
(PR #23 et #40) : ce tableau n'a aucune valeur de preuve.

## 3. Le protocole préenregistré `alpha-shadow-r1-p1`

- **Hypothèse** : la politique figée R1 améliore l'espérance nette du portefeuille fictif de 30 € —
  rendement quotidien net > 0 **et** supérieur à celui de « toutes les entrées » — en respectant les
  limites de risque. Réfutée si l'une des deux bornes basses n'est pas strictement positive à la fin,
  ou si le critère d'arrêt est atteint.
- **Un seul candidat, un seul essai, une seule comparaison primaire** : R1 contre « toutes les entrées ».
  Les autres bras (ne rien faire, enseignant cassure seul, modèle du projet, achat-conservation) sont
  **descriptifs** : jamais utilisés pour conclure. « Ne rien faire » seul n'est pas une stratégie à battre :
  le gain absolu est exigé en plus de l'écart à la référence.
- **Fenêtre** : 365 jours, à partir d'une minuit UTC au plus tôt le **16/11/2026**, choisie à l'activation.
  La session doit être créée et ancrée avant ce début ; le moteur refuse de démarrer une session
  prospective dans le passé. Distinct de v2 et v3, `shadow_only`, ne les remplace pas.
- **Univers** : BTC/EUR, ETH/EUR, SOL/EUR, bougies de 4 h. Une décision ne dépend que des bougies
  publiques closes : n'importe qui peut la recalculer. Les carnets ne servent qu'au prix d'exécution.
- **Exécution simulée** : chaque bras a son propre portefeuille de 30 € (risque 0,5 % par trade, minimum
  5 €, 3 positions au plus, 34 % par position), sans lien avec le portefeuille principal. Entrée au premier
  carnet reçu après l'écriture de la décision (dans les 5 minutes) ; à défaut, ouverture suivante +
  demi-écart + glissement, signalée comme approximation. Sorties selon le contrat d'exécution du projet.
  Positions ouvertes à la fin : valorisées à la dernière clôture. Une occasion refusée n'a aucun résultat.
- **Analyse** : rendements quotidiens UTC, bootstrap circulaire par blocs de 7 jours, 2 000 tirages,
  intervalle à 95 % ; les deux conditions ensemble (test d'intersection-union, un seul essai : aucune
  correction supplémentaire). Pendant la fenêtre, le rapport ne montre que la couverture et la sécurité ;
  les résultats sont masqués jusqu'à la fin.
- **Minimums pour conclure** : fenêtre complète ; 95 % des décisions prises à temps ; 90 % des entrées de
  R1 et de la référence exécutées sur carnet horodaté ; 95 % des décisions prouvées antérieures à leur
  résultat par des ancres datées ; au moins 100 entrées de R1 (5e centile de son activité au contrôle) ;
  aucune bougie révisée après coup. Sinon : `INSUFFICIENT_EVIDENCE`.
- **Critères d'arrêt** : baisse maximale du bras R1 ≥ 10 % (limite de risque du projet) → plus aucune
  entrée, verdict `REJECTED` ; empreinte du manifeste, du modèle ou du code différente, ou registre
  incohérent avec ses ancres → `FAILED_INTEGRITY`.
- **Verdicts** : `NOT_STARTED`, `IN_PROGRESS`, `EXPLORATORY`, `FAILED_INTEGRITY`, `INSUFFICIENT_EVIDENCE`,
  `REJECTED`, `INCONCLUSIVE`, `PROMOTION_RECOMMENDED_FOR_FUTURE_PAPER_ONLY`. Sans amélioration robuste, R1
  est rejeté et son rapport conservé. Le verdict favorable ne promeut rien : il recommande un nouveau
  protocole, sous décision humaine.
- **Aucun amendement** : toute modification = nouveau protocole, nouvelle fenêtre future, nouvelle ligne
  au journal des essais (`trials.jsonl`, chaîné, qui refuse une seconde session prospective).
- **Code figé** : le manifeste contient l'empreinte des 14 fichiers qui décident ou simulent ; un test
  échoue si l'un d'eux change.

## 4. Analyse de puissance : ce que la fenêtre peut conclure

Méthode : les séries quotidiennes de R1 et de la référence au contrôle sont centrées, rééchantillonnées
par blocs de 7 jours sur la durée voulue, et un avantage vrai est ajouté à R1 ; on compte la part des
300 simulations où la règle conclut « amélioration ». Règle de durée fixée avant le calcul : plus petite
durée parmi 90, 180, 270 et 365 jours atteignant 80 % de puissance pour +5 % par an ; à défaut 365 jours.

| Durée | 0 % (faux positifs) | +5 %/an | +10 %/an | +20 %/an | +35 %/an | +50 %/an | +75 %/an | +100 %/an |
|---|---|---|---|---|---|---|---|---|
| 90 jours | 0 % | 0 % | 0 % | 3 % | 19 % | 46 % | 81 % | 97 % |
| 180 jours | 0 % | 0 % | 0 % | 4 % | 27 % | 64 % | 97 % | 100 % |
| 270 jours | 0 % | 0 % | 1 % | 13 % | 49 % | 85 % | 100 % | 100 % |
| **365 jours** | 0 % | **0 %** | 1 % | 15 % | 57 % | 91 % | 100 % | 100 % |

Les colonnes au-delà de +20 % ont été ajoutées après le premier calcul, pour information ; elles ne
changent ni la règle ni la durée retenue.

Pourquoi : un portefeuille acheteur exposé à environ la moitié de son capital sur des cryptomonnaies
varie d'environ 0,9 % par jour (≈ 17 % par an). Prouver qu'une moyenne est positive face à ce bruit
demande soit un avantage énorme, soit de nombreuses années. C'est vrai de toute stratégie de ce type,
pas seulement de R1. La comparaison relative est moins bruitée (0,39 % par jour) ; c'est l'exigence de
gain absolu — voulue par la mission — qui coûte la puissance.

Activité attendue de R1 sur 365 jours : 119 entrées (5e centile : 100, minimum retenu).

## 5. Infrastructure de preuve

### 5.1 Contrat `alpha_shadow_contract_v1`

Entrées acceptées : `bar` (bougie close : marché, ouverture, clôture, OHLCV, source, instant de
publication s'il est connu, instant de réception) et `book` (carnet : heure de la plateforme si elle
existe, sinon « réception seule »). Sorties : `decision`, `reference`, `outcome`, `report`.

- Tout champ inconnu est refusé ; instants en millisecondes UTC entières bornées (un instant en secondes
  ou en microsecondes est refusé) ; nombres finis ; un booléen n'est pas un nombre.
- Une décision ne peut contenir aucun champ de résultat. Un refus ou un abandon ne peut porter aucun
  résultat (`RESULT_WITHOUT_FILL`). Un rapport ne peut recommander une promotion hors mode prospectif.
- Migrations explicites : une version inconnue est refusée, jamais interprétée au mieux.
- Indépendant de Mission Control : aucun de ses modules n'est importé. Adaptateur pur fourni pour les
  lignes de bougies publiques (`bar_from_kline`) ; `tools.alpha_shadow ingest` lit un fichier d'export
  (une observation par ligne), avance d'un pas et s'arrête. L'appariement des deux contrats reste à
  faire en revue commune.

### 5.2 Admission des observations

| Situation | Traitement |
|---|---|
| Bougie reçue et traitée dans les 5 minutes | `DECIDABLE` : décision écrite |
| Bougie authentique mais tardive | `HISTORY` : prolonge la série, décision `REFUSED` (`LATE_RECEPTION` ou `LATE_DECISION`) |
| Bougie antérieure manquante | `PENDING` : mise en attente, jamais de trou comblé ; pas de règlement tant qu'un actif manque |
| Bougie future, non close, incohérente, d'un autre marché, mal alignée | refus motivé |
| Doublon identique | ignoré (compté) |
| Bougie révisée après coup | refus `CONFLICTING_REVISION` ; bloque tout verdict favorable |
| Historique insuffisant (< 320 bougies) | décision `REFUSED` (`WARMUP_INCOMPLETE`) |
| Horloge qui recule | pas refusé, registre intact |

Les positions ouvertes restent gérées avec les bougies tardives (une sortie est la conséquence mécanique
d'une décision déjà écrite) ; seule la décision de la bougie tardive est perdue, et comptée.

### 5.3 Résultats : simulation causale indépendante

Le moteur réutilise les formules de la PR #40 (`simulate_trade`), bougie par bougie, sans jamais
recevoir de bougie future. Vérifications : la série en ligne redonne les indicateurs du développement
à l'identique ; le moteur redonne le portefeuille du calcul d'un seul bloc trade par trade ; un rejeu
tronqué donne exactement le début du rejeu complet, empreintes comprises.

Avec carnet : prix moyen payé en remontant les ventes pour le montant voulu, glissement en plus ;
exécution partielle si le carnet est trop mince, refus sous le minimum de 5 €. Un carnet reçu avant
l'écriture de la décision, après le délai, ou dont l'heure de plateforme précède la décision, n'est pas
une preuve de prix. Les sorties (stops touchés dans la bougie) restent modélisées, et c'est dit dans
chaque résultat (`evidence`).

### 5.4 Registre : pourquoi la chaîne de la PR #40 ne suffisait pas

Constat, reproduit par un test : avec le `ShadowLedger` de la PR #40, celui qui peut écrire le fichier
peut le réécrire en entier et recalculer chaque empreinte ; `verify()` répond « vrai ». Une chaîne
détecte une retouche partielle, pas une réécriture complète.

Réponse, sans secret ni service :

1. **Ancres.** Chaque pas rend une ligne courte (numéro et empreinte de la dernière ligne). L'opérateur
   la publie sur un support qu'il ne peut pas réécrire seul et qui date les dépôts (commentaire ou commit
   poussé sur GitHub). `verify_anchors` détecte alors réécriture, troncature, autre session et désordre.
   Ce qui suit la dernière ancre n'est pas prouvé, et le rapport le dit.
2. **Encadrement temporel.** Une décision contient l'empreinte de la bougie qui l'a déclenchée (elle n'a
   pas pu être écrite avant) et elle est couverte par une ancre datée (pas après). Seule la date donnée
   par le support externe compte : notre propre horloge ne prouve rien.
3. **Rejeu déterministe.** Les décisions sont une fonction des bougies enregistrées : à la reprise, le
   moteur recalcule tout et refuse un registre dont une décision a été retouchée, même si toutes les
   empreintes ont été recalculées.
4. **Historique public.** Un registre réécrit avec de faux prix reste cohérent avec lui-même ;
   `verify_against_history` le confronte à un historique obtenu indépendamment.
5. **Pas atomiques et reprise.** Les lignes d'un pas sont écrites d'un bloc et closes par un `commit`.
   Après un arrêt brutal, les lignes sans `commit` sont déclarées nulles (elles restent visibles) ; la
   bougie renvoyée arrive trop tard et sa décision est refusée : aucune décision n'est antidatée.

Limites assumées : la protection vaut ce que vaut la publication des ancres (au moins une avant le début,
puis une par jour) ; le choix du carnet repose sur la complétude du collecteur (il pourrait ne transmettre
que les carnets favorables : les réponses brutes archivées par Mission Control permettront l'audit).

## 6. Fiabilité de l'évaluation (lot D.2)

### 6.1 Le moteur en ligne contre le calcul de développement (vérification des scores)

Sur la fenêtre de contrôle réelle, rejouée bougie par bougie (55 908 lignes de registre, 7 077 décisions,
aucune manquante) :

| Bras | Calcul d'un seul bloc (€) | Moteur en ligne, sans limite de baisse (€) | Moteur en ligne, règles du protocole (€) |
|---|---|---|---|
| R1 | −4,2504 (130 trades) | −4,2504 (130 trades) | **−3,02 (18 trades, arrêté)** |
| Toutes les entrées | −7,9868 (264) | −7,9868 (264) | −7,9868 (264) |
| Enseignant cassure | −3,8554 (109) | −3,8554 (109) | −3,8554 (109) |
| Modèle du projet (fichier figé) | −3,8632 (119) | −3,8632 (119) | −3,8632 (119) |
| Ne rien faire | 0 | 0 | 0 |

- Sans limite de baisse (hors protocole, pour la seule comparaison), le moteur redonne le calcul d'un
  seul bloc **au dernier chiffre** pour tous les bras : aucune fuite, aucune divergence.
- **Avec les règles du protocole, R1 est arrêté le 11/12/2025 à 04:00 UTC**, huit semaines après le début,
  après 18 trades dont 17 perdants (−0,75 R en moyenne) : sa baisse maximale atteint 10,2 %. Si cette
  période avait été la fenêtre prospective, le verdict aurait été `REJECTED`.
- Les 35 838 bougies du registre correspondent à l'historique public rechargé indépendamment.
- **Reproductibilité entre environnements** : le même rejeu donne la même empreinte finale de registre
  (`9213b960…cee853e8`) sous CPython 3.13 / numpy 2.5.4 / scikit-learn 1.9.1 et sous CPython 3.12.3 /
  numpy 2.3.5 / scikit-learn 1.8.0 hors réseau. Le gel de R1 relancé sous l'environnement épinglé du projet
  redonne le même fichier à l'octet près (`a146a766…`).

Fichiers : `mesures/alpha-shadow-r1/rejeu-controle-protocole.json`, `rejeu-controle-sans-limite.json`,
`rejeu-controle-rapport.json`, `reproductibilite.json`.

### 6.2 Défaut d'évaluation de la PR #40 : les positions non closes étaient écartées

Le calcul d'un seul bloc de la PR #40 retire de l'évaluation tout trade qui n'est pas clos à la fin de la
fenêtre. Or être encore ouvert dépend de l'issue : les perdants sortent vite par leur stop, les gagnants
durent. Le moteur les valorise à la dernière clôture (résultat latent, frais de sortie déduits).

Mesure sur 9 marchés synthétiques neufs (graines 7001 à 7003), bras « toutes les entrées » : des positions
restaient ouvertes sur 6 marchés ; l'écart moyen est de +0,32 € sur 30 €, et va de −0,27 € à **+2,35 €**
sur un seul marché (assez pour changer une conclusion). Sur la fenêtre de contrôle réelle, aucune position
n'était ouverte à la fin : pas d'effet. Fichier : `mesures/alpha-shadow-r1/biais-de-purge.json`.

Conséquence : les montants de la PR #40 restent des ordres de grandeur valables, mais un écart de 1 à 2 €
entre deux concurrents ne doit pas y être lu comme un classement. La purge reste correcte pour
l'apprentissage (une issue inconnue ne doit pas servir d'étiquette) ; c'est son emploi pour mesurer un
portefeuille qui était discutable. Le protocole R1 fixe la règle à l'avance : valorisation à la dernière
clôture.

### 6.3 Confusion entre fenêtre de sélection et test final

Le score obtenu sur la fenêtre où un seuil a été choisi est optimiste par construction. Mesure :

- R1, données réelles : +0,080 R par entrée en sélection → **−0,105 R** au contrôle (+1,60 € → −4,25 €).
- Banc à graines neuves (70 cases marché × budget) : le score de sélection dépasse le score d'évaluation
  dans **83 %** des cases pour le routeur (84 % pour le modèle du projet) ; écart médian +0,93 R par entrée.
  Sur marchés aléatoires : +0,97 R en sélection → +0,05 R en évaluation.

C'est pourquoi le protocole ne tire aucune conclusion de la fenêtre de sélection, ni du contrôle : seule la
fenêtre future compte, une fois.

### 6.4 Constantes et règles fixées après coup (inventaire)

| Élément | Où | Fixé quand | Effet |
|---|---|---|---|
| Règle stricte « gain absolu ET relatif » | PR #40, `velocity.strict_verdict` | **après** la mesure officielle de #40 | ne pouvait que retirer des verdicts positifs ; les deux règles ont été publiées |
| Masque du rapport « € par minute de calcul » sous 6 ms | PR #40, `velocity.summarize` | **après** la mesure officielle | présentation seulement |
| Appariement du tournoi par (type, intensité, graine) | PR #40, `experiments.tournament` | **après** (correction d'un défaut) | relance identique pour 825 et 540 résultats |
| Graines officielles 1001 / 2002 / 3003, 10 000 bougies | PR #40 | avant la mesure officielle, après des essais sur d'autres graines | documenté dans #40 |
| Hyperparamètres du routeur (a priori 20, marges, 15 entrées) | PR #40 | pendant le développement de #40 ; **jamais réglés sur les données en euros** | repris tels quels pour R1 |
| Colonnes d'effet au-delà de +20 % par an dans la table de puissance | cette PR | **après** le premier calcul de puissance | information ; ni la règle ni la durée ne changent |
| Latences admises (5 minutes), échauffement (320 bougies) | cette PR, `engine.Limits` | avant l'enregistrement, sans donnée future | figés dans le manifeste |
| Limite de baisse de 10 % | réglages du projet (`risk.max_drawdown`) | antérieure à cette mission | reprise, non choisie d'après le contrôle |

À noter : le contrôle de R1 n'est pas « aveugle ». Le routeur avait déjà été essayé dans la PR #40 sur les
mêmes marchés cotés en USDT, sur une période qui recouvre le contrôle.

### 6.5 Mécanismes de relecture du futur (inventaire)

| Mécanisme | Risque | État |
|---|---|---|
| `dataset.build_situations` (#40) lit toute la série pour calculer les issues | étiquettes seulement, datées, purgées | réservé au développement ; jamais chargé par le moteur |
| `execution.run_portfolio` (#40), première bougie d'une fenêtre | lit la clôture de la bougie courante pour valoriser un portefeuille encore vide | sans effet numérique ; absent du moteur |
| `execution.run_portfolio` (#40), fin de fenêtre | écarte les trades non clos (§ 6.2) | corrigé dans le moteur |
| `council.run_council` (#40) | résultat compté dès l'entrée (corrigé dans #40) | test existant |
| Indicateurs | invariance par troncature (#40) ; série en ligne identique au calcul d'un bloc | tests |
| Moteur shadow | ne détient aucune bougie future ; simulation bornée à la bougie réglée ; rejeu tronqué = début du rejeu complet | tests |
| Rejeu d'historique (`tools.alpha_shadow replay`) | l'horloge est celle des données | mode `replay_exploratory` : verdict plafonné à `EXPLORATORY` |
| Reprise après arrêt | recalcule les pas clos, ne recrée aucune décision | tests |
| Actualités révisées | R1 ne lit aucune actualité : tout enregistrement d'un autre type est refusé | test ; les tests de #37 et #40 couvrent les sources révisées |

## 7. « Fast Learner » : audit du coût de calcul (lot C, exploratoire)

Plan écrit avant la mesure (`plan.json`) : 9 marchés synthétiques à graines jamais utilisées (7001 à
7003 × régimes, règle cachée, bruit ; 6 000 bougies) et l'historique réel de développement ; 7 budgets
d'observations ; 7 répétitions par case, sur la même machine (4 cœurs, CPython 3.13.18), machine au repos.
Temps mesuré sans traceur de mémoire ; mémoire mesurée dans une passe séparée. Tout est **exploratoire**.

### 7.1 Coût : « 40 fois moins cher » était sous-estimé

| Marchés (budget complet) | Routeur : CPU médian | p95 | mémoire | Modèle du projet : CPU médian | p95 | temps mural | mémoire | Rapport CPU [intervalle] | Rapport mural |
|---|---|---|---|---|---|---|---|---|---|
| Régimes (3) | 1,8 ms | 1,8 ms | 0,06 Mo | 431 ms | 761 ms | 121 ms | 1,14 Mo | 243 [142 ; 683] | 67 |
| Règle cachée (3) | 1,9 ms | 2,1 ms | 0,05 Mo | 646 ms | 1 047 ms | 191 ms | 1,03 Mo | 370 [150 ; 673] | 109 |
| Bruit (3) | 1,9 ms | 2,0 ms | 0,05 Mo | 364 ms | 683 ms | 107 ms | 1,04 Mo | 214 [156 ; 448] | 61 |
| Réel, euros (1) | 3,3 ms | 4,4 ms | 0,10 Mo | 390 ms | 554 ms | 119 ms | 1,82 Mo | 119 [78 ; 131] | 36 |

Sur les 70 cases : rapport CPU médian **294** (de 119 à 739), rapport en temps mural médian **88** (de 36
à 208 : le modèle du projet occupe les 4 cœurs), rapport de mémoire médian 16.

**Pourquoi la PR #40 annonçait 40** : elle chronométrait avec le traceur de mémoire actif, qui ralentit
le routeur 5,1 fois et le modèle du projet 2,3 fois. Mesuré de cette façon, le rapport médian tombe à 127 ;
le reste de l'écart vient de marchés et de budgets différents. Le routeur est donc **encore moins cher**
qu'annoncé. Cela reste un critère secondaire : voir § 7.2.

### 7.2 Courbe « observations → calibration → avantage net », données réelles (déjà étudiées)

| Observations | Routeur : Brier vs taux de l'enseignant | Calibration (ECE) | AUC (identité de l'enseignant) | Entrées | dont perdantes | Abstentions | Écart vs toutes les entrées | Regret | Modèle du projet : Brier | AUC | Entrées | dont perdantes | Écart | Regret |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 25 | +0,004 | 0,065 | 0,690 (0,692) | 38 | 30 | 637 | +8,07 € | −0,09 € | −0,071 | 0,687 | 65 | 25 | +7,13 € | +0,86 € |
| 50 | +0,001 | 0,084 | 0,690 (0,692) | 86 | 67 | 543 | +6,61 € | +1,38 € | −0,139 | 0,576 | 147 | 117 | +3,89 € | +4,10 € |
| 100 | +0,005 | 0,081 | 0,685 (0,692) | 101 | 79 | 505 | +6,58 € | +1,41 € | −0,141 | 0,552 | 147 | 117 | +3,89 € | +4,10 € |
| 200 | +0,003 | 0,073 | 0,688 (0,692) | 101 | 79 | 505 | +6,58 € | +1,41 € | −0,095 | 0,666 | 147 | 117 | +3,89 € | +4,10 € |
| 400 | +0,002 | 0,077 | 0,701 (0,692) | 80 | 64 | 549 | +5,39 € | +2,59 € | −0,123 | 0,589 | 147 | 117 | +3,82 € | +4,16 € |
| 800 | +0,000 | 0,079 | 0,692 (0,685) | 109 | 88 | 484 | +3,51 € | +4,48 € | −0,045 | 0,680 | 139 | 109 | +4,65 € | +3,34 € |
| 2 154 | +0,001 | 0,055 | 0,694 (0,703) | 130 | 103 | 407 | +3,74 € | +4,25 € | −0,016 | 0,708 | 119 | 94 | +4,12 € | +3,86 € |

Lecture honnête :

- **Aucun des deux ne prédit mieux que le simple taux de gain de l'enseignant** (Brier ≈ 0 pour le routeur,
  négatif pour le modèle du projet) ; l'AUC est celle que donne la seule identité de l'enseignant.
- **L'écart à la référence ne grandit pas avec les observations** : il est le plus grand avec 25 exemples,
  quand le routeur entre le moins. Dans un marché où presque toutes les entrées perdent, s'abstenir
  davantage suffit à « battre » la référence. Ce n'est pas de l'apprentissage.
- **Le meilleur choix connu après coup était de ne rien faire** : le regret est positif partout, sauf à 25
  observations où le routeur fait jeu égal avec zéro.
- Par actif (budget complet, routeur) : BTC −1,79 €, ETH −1,51 €, SOL −0,95 €. Par climat : R moyen négatif
  dans 3 climats sur 6 (jusqu'à −0,46 R), voisin de zéro dans les autres.
- Quatre entrées sur cinq sont perdantes (103 sur 130) : le résultat tient à quelques gagnants.

### 7.3 Marchés synthétiques (moyennes par famille, graines neuves)

| Marchés | Observations | Routeur : écart | regret | entrées | Modèle du projet : écart | regret | entrées | Routeur, sélection → évaluation |
|---|---|---|---|---|---|---|---|---|
| Régimes | 50 | −0,02 € | +2,43 € | 5 | −0,13 € | +2,54 € | 19 | +6,34 R → −0,08 R |
| Régimes | tout | −0,19 € | +2,60 € | 17 | +0,12 € | +2,29 € | 15 | +2,93 R → +0,04 R |
| Règle cachée (témoin positif) | 50 | +1,55 € | +1,70 € | 29 | +1,96 € | +1,29 € | 65 | +2,65 R → +1,99 R |
| Règle cachée | tout | +3,19 € | +0,06 € | 37 | +3,16 € | +0,09 € | 43 | +2,42 R → +1,77 R |
| Bruit (témoin négatif) | 50 | −0,07 € | +2,40 € | 38 | +1,09 € | +1,24 € | 58 | +0,26 R → −0,02 R |
| Bruit | tout | +2,40 € | −0,07 € | 27 | +2,36 € | −0,04 € | 29 | +0,97 R → +0,05 R |

- Sur le témoin positif, les deux apprenants arrivent au même résultat (regret de 0,06 € et 0,09 €) ; le
  routeur y parvient pour environ 300 fois moins de calcul. **C'est tout ce que le critère secondaire
  établit : coût moindre à résultat égal.**
- **Sur le bruit, l'écart à la référence est positif dans 10 cases sur 21 pour le routeur et 14 sur 21 pour
  le modèle du projet** : « battre toutes les entrées » sur un marché aléatoire ne prouve rien (la référence
  y perd ses frais). C'est exactement pourquoi le protocole exige en plus un gain absolu.
- Regret par seconde de calcul et avantage par euro de pertes d'apprentissage (pertes fictives subies en
  suivant toutes les entrées pendant que les observations étaient produites) : dans le fichier de mesures ;
  ce dernier est très instable (le dénominateur change de signe), aucune tendance n'en ressort.

Fichier : `mesures/alpha-shadow-r1/bench-fastlearner.json` (toutes les cases, répétitions, intervalles).

Aucune méthode nouvelle (mélange d'experts, apprentissage actif, détecteur de dérive) n'a été ajoutée au
protocole : R1 reste le candidat unique.

## 8. Tests

**Environnement :** Linux x86-64, CPython 3.12.3, dépendances épinglées (CCXT 4.4.99 complet, numpy 2.3.5,
scikit-learn 1.8.0), lanceur `tools/security/offline_exec` (filtre seccomp : création de connexion refusée,
refus vérifié juste avant la suite).

| Exécution | Résultat |
|---|---|
| Suite complète de la base (PR #40, `529f4c5`) | 666 réussis, 1 ignoré |
| **Suite complète de cette branche** | **789 réussis, 1 ignoré** (+123), 464 s |
| Test ignoré (panel sur connexion locale, interdit sous le filtre), rejoué à part hors filtre | 1 réussi |
| Ruff sur les fichiers nouveaux ou modifiés | aucune remarque |
| Fichiers gelés v1, v2, v3 | aucun octet modifié (`git diff` vide + test d'empreintes) |
| Windows 11 | **non testé** |
| Tests navigateur | non exécutés (aucune page modifiée) |

Aucun test existant n'a été supprimé ni modifié. Le test d'isolation de la PR #40 couvre un fichier de
plus (`fastlearner.py`), d'où +1 dans `test_alpha_council.py`.

| Fichier | Tests | Ce qu'ils établissent |
|---|---|---|
| `tests/test_alpha_shadow_contract.py` | 32 | champs inconnus, unités et types refusés ; décision sans résultat ; refus sans « gain évité » ; migrations explicites ; pas atomiques ; **réécriture complète invisible pour la chaîne de #40, détectée par les ancres** ; troncature, autre session ; écriture interrompue déclarée nulle ; antériorité prouvée seulement par une ancre datée de l'extérieur |
| `tests/test_alpha_shadow_engine.py` | 32 | indicateurs en ligne identiques au développement ; **portefeuille identique au calcul d'un bloc, trade par trade** ; rejeu tronqué = début du rejeu complet ; décision écrite avant la bougie suivante ; bougies tardives, futures, non closes, incohérentes, révisées, en double, trou, horloge qui recule ; résultat écrit seulement après la bougie de sortie ; coûts majorés ; carnets (avant la décision, après le délai, périmés, croisés, trop minces, prix défavorable) ; limite de baisse ; session prospective impossible dans le passé ; **reprise après arrêt brutal sans décision antidatée** ; registre réécrit refusé au rejeu |
| `tests/test_alpha_shadow_protocol.py` | 50 | modèle figé = routeur d'origine ; artefact ou règles modifiés refusés ; règle « gain absolu ET relatif » ; **bruit jamais annoncé rentable** (taux de faux positifs simulé, 4 marchés aléatoires, chaîne prospective complète) ; **la méthode sait dire oui sur le témoin positif** ; sans carnet ou sans ancre : `INSUFFICIENT_EVIDENCE` ; période explorée : `EXPLORATORY` ; intégrité ; résultats masqués en cours de fenêtre ; arrêt → `REJECTED` ; candidat non prouvé rejeté ; retards, révisions, bougies manquantes comptés ; faux prix confondus par l'historique public ; puissance ; **v2 / v3 identiques à l'octet** ; manifeste intact, écrit une seule fois ; dérive du code détectée ; journal des essais (une seule session prospective) ; isolation (aucun courtier, réseau, service, base) ; commandes |
| `tests/test_alpha_fastlearner.py` | 8 | temps et mémoire mesurés séparément ; budgets égaux ; mesures prédictives, financières et de coût séparées ; résultats financiers déterministes ; statut exploratoire |

Correspondance avec le lot D.1 : marchés aléatoires (4 graines + taux de faux positifs) ; tendances
changeantes (marchés à changement de régime, 2 graines) ; arrêt brutal et retards ; coûts multipliés par 4 ;
latence et faux prix ; prix reçus après la décision ; issues mûries tardivement ; portefeuille de 30 € avec
frais. Actualités révisées : sans objet pour R1, qui n'en lit aucune (refus testé).

Trois défauts de mon propre code ont été trouvés par ces tests et corrigés avant l'enregistrement du
protocole : indicateur d'exécution partielle mal typé (refusé par le contrat) ; reprise impossible après
une première reprise avec fragment illisible ; liste des écarts d'ancrage trop longue pour le rapport.

## 9. Ce qui manque pour commencer une véritable étude prospective

1. Revue de cette PR, puis revue commune du contrat avec l'export de Mission Control (#39), qui n'est
   pas encore publié.
2. Validation du collecteur sous Windows par GPT Work, sur le PC du propriétaire. **Windows 11 : non
   testé ici.**
3. Accord explicite du propriétaire pour une observation à durée finie. Aucun service permanent n'est
   autorisé par cette PR ; la commande `ingest` refuse le mode prospectif.
4. Fraîcheur des données : bougies de 4 h closes reçues en moins de 5 minutes, au moins 320 bougies
   d'historique par actif.
5. Carnets horodatés reçus après chaque décision. Sans eux, le verdict restera `INSUFFICIENT_EVIDENCE`
   (approximation non confirmatoire). Les carnets REST de Binance n'ont pas d'heure de plateforme : ils
   sont acceptés comme « réception seule ».
6. Publication datée des ancres : une avant le début de la fenêtre, puis au moins une par jour.
7. Une décision du propriétaire sur l'intérêt de l'étude, au vu de la puissance (§ 4).

## 10. Limites

- Tout ce qui est chiffré ici porte sur des données simulées ou des périodes déjà étudiées : exploratoire.
- La puissance est estimée à partir d'une seule fenêtre de contrôle de 399 jours : ordre de grandeur.
- Les hyperparamètres de R1 viennent de la PR #40, où le routeur avait déjà été essayé sur les mêmes
  marchés en USDT : le contrôle n'est pas « aveugle ».
- La table de R1 s'arrête fin 2024 ; elle aura deux ans d'âge au début de la fenêtre. C'est le prix du gel.
- Les sorties sont modélisées sur bougies même quand les entrées utilisent un carnet.
- Le bras « modèle du projet » dépend d'un fichier `joblib` et de la bibliothèque d'apprentissage ; s'il
  ne se charge pas, il est déclaré indisponible (aucun effet sur le verdict).
- Aucune innovation supplémentaire (mélange d'experts, apprentissage actif, détecteur de dérive) n'a été
  ajoutée : la mission demandait un candidat unique. `ErrorDiagnosisV1` et `HypothesisRegistry` (#42) sont
  une mission ultérieure.

## 11. Commandes et API stable

```
python -m tools.alpha_shadow contract                 # formats du contrat
python -m tools.alpha_shadow verify                   # manifeste, artefacts et code intacts ?
python -m tools.alpha_shadow replay --archive DOSSIER --output DOSSIER      # rejeu EXPLORATOIRE
python -m tools.alpha_shadow ingest --ledger F --observations F.jsonl --now-ms N [--start-ms S --days J]
python -m tools.alpha_shadow report --ledger F [--anchors F]
python -m tools.alpha_shadow anchor --ledger F        # ligne à publier pour dater le registre
python -m tools.alpha_shadow bench  --output DOSSIER [--archive DOSSIER]    # coût de calcul (exploratoire)
```

API pour l'intégration future : `shadow.contract.validate / make / migrate / bar_from_kline`,
`shadow.engine.ShadowEngine.start / resume / step`, `shadow.engine.SessionConfig`,
`shadow.ledger.ChainLedger / verify_anchors / precedence`, `shadow.analysis.build_report /
verify_against_history / power_table`, `shadow.protocol.load_manifest / session_config`,
`shadow.r1.load_model`.
