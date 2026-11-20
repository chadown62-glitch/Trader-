# MEMECOIN ALPHA ENGINE (Solana) — mission 56

> **Simulation.** Aucune transaction n'est construite, signée ni diffusée ; aucune clé, aucun portefeuille. Les seules
> lectures passent par le client public verrouillé du hub (`sentinelle.dmh.net.PublicClient`) : liste blanche des
> hôtes et des chemins, exécution Jupiter interdite, paramètres de portefeuille refusés. Passer le veto ne garantit ni
> l'absence d'arnaque ni un gain.

## Verdict

**AUCUN AVANTAGE DÉMONTRÉ.** Sur 136 nouveaux pools Solana collectés lors de deux collectes (17 et 20 novembre 2026) :

- **127 pools contrôlés (121 tokens distincts), 127 refusés par le veto.** Aucun n'a passé, donc aucun trade « avec
  veto » n'existe à évaluer.
- Le flux des nouveaux pools est presque entièrement composé de lancements minuscules : la liquidité médiane est de
  1 601 $, et seuls 9 pools sur 136 atteignent le minimum de 20 000 $ du veto.
- **82 des 127 pools (65 %) n'ont plus aucun échange après la décision du veto.** Ils se sont arrêtés dans les
  minutes qui ont suivi.
- Les cinq règles de sélection n'ont déclenché qu'un à deux ordres chacune sur la partie d'entraînement, et aucun sur
  la partie de développement. Ce n'est pas assez pour conclure dans un sens ou dans l'autre.
- La **partie réservée (20 %) reste intacte**. Aucune règle n'est candidate, donc aucune évaluation n'y a été lancée.
  Elle reste disponible pour un futur protocole préenregistré.
- Le modèle de coûts est **validé** sur 72 allers-retours réellement cotés par Jupiter. Il est légèrement prudent :
  coût médian mesuré de 3,0 %, modèle à 3,3 %.

Ce résultat est négatif, et il est publié tel quel. Il ne permet pas de conclure qu'un trading de memecoins rentable
existe ici. Il montre surtout qu'au moment de leur détection, presque tous les nouveaux tokens sont trop petits ou trop
dangereux pour être achetés.

## 1. Architecture (`sentinelle/memecoin/`)

| Module | Rôle |
|---|---|
| `collect.py` (module A) | détection des nouveaux pools (GeckoTerminal), bougies minute, contrôles de sécurité, seconde lecture, qualité des sources |
| `guardian.py` (module B) | veto anti-arnaque : VETO, ABSTENTION ou PASSE, avec ses motifs |
| `lab.py` (module C et banc) | 5 règles de sélection figées, jeu de données causal, coûts DEX complets, contrôle du veto, tests de stress |
| `tools/memecoin.py` | `collect`, `follow` et `lab` : commandes à exécution finie, aucun service |

Le commit `dc72a76` (17 novembre 2026 à 09:33 UTC) fixe :
- les règles du veto ;
- les cinq règles de sélection et leurs sorties ;
- les hypothèses de coûts.

Il a été fait pendant la première collecte (09:27 à 09:38 UTC), avant toute analyse des données. **Ces éléments n'ont
plus changé depuis.** Pour le vérifier : `git diff dc72a76 -- sentinelle/memecoin/guardian.py` est vide, et les
constantes de `lab.py` sont identiques.

Ce qui a changé ensuite :
- la collecte : seconde lecture, nouvel essai après un refus ;
- les contrôles : veto causal, lecture par motif, couverture de l'issue.

## 2. Sources publiques (module A) et leur qualité

Chaque réponse est horodatée et hachée (SHA-256). Si une source ne répond pas, la donnée reste **absente** et le
veto s'abstient.

| Source | Usage |
|---|---|
| GeckoTerminal | nouveaux pools Solana, bougies minute (`before_timestamp` pour relire une fenêtre passée) |
| RPC Solana public | autorités d'émission et de gel, extensions Token-2022 |
| Rugcheck (rapport complet) | détenteurs hors pools et verrous connus, créateur et historique, verrouillage de la liquidité, signalements |
| Jupiter (devis sans portefeuille) | aller-retour d'achat puis de revente de 0,05 SOL : revendabilité **effective** et coût réel |
| DexScreener | second avis sur la liquidité et le prix |

| Collecte | Début (UTC) | Fin (UTC) | Requêtes | Pools | Seconde lecture |
|---|---|---|---|---|---|
| 1 | 2026-11-17 09:27 | 09:38 | 358 | 64 | 20 novembre, jusqu'à 16 h après la décision |
| 2 | 2026-11-20 12:19 | 12:34 | 393 | 72 | même jour, environ 90 minutes après la collecte |

Qualité mesurée :
- **délai de détection** : médiane de 2,5 à 2,6 minutes après la création du pool ;
- **plateformes** : pump.fun domine (96 pools sur 136), puis Meteora et PumpSwap ;
- **bougies de la première collecte** : seulement 61 % des demandes ont abouti (refus HTTP 429 de GeckoTerminal) ; la
  seconde collecte, à un débit réduit, les a toutes obtenues (72 sur 72) ;
- **devis Jupiter** : de 89 à 97 % des achats ont obtenu une réponse ;
- **écart de liquidité entre GeckoTerminal et DexScreener** : souvent énorme sur les pools naissants. C'est pourquoi un
  désaccord de plus de 50 % provoque une abstention.

Constats de débit :
- la méthode RPC publique des plus gros comptes est limitée (HTTP 429) : Rugcheck sert de source des détenteurs ;
- GeckoTerminal refuse au-delà d'environ 9 requêtes par minute, bien en dessous de sa limite affichée. Le débit est
  donc ramené à 0,12 requête/s, avec une pause de 65 s puis un seul nouvel essai après un refus.

## 3. Veto anti-arnaque (module B), règles fixées d'avance

- **Donnée essentielle absente ou contradictoire → ABSTENTION**, jamais un feu vert par défaut. Exemples : mint
  illisible, aucun rapport, aucun devis, désaccord de plus de 50 % sur la liquidité entre GeckoTerminal et DexScreener.
- **Un seul signal de danger → VETO** :
  - autorité d'émission ou de gel active ;
  - extension Token-2022 à risque, ou taxe de transfert ;
  - signalement « danger » de Rugcheck, ou historique d'arnaques du créateur ;
  - 10 plus gros détenteurs (hors pools et verrous) au-delà de 35 %, ou plus gros au-delà de 15 % ;
  - créateur au-delà de 10 % ;
  - liquidité non verrouillée sur un pool classique ;
  - **aucune route de revente**, ou aller-retour immédiat au-delà de 10 % de perte ;
  - liquidité sous 20 000 $ ;
  - des achats sans aucune vente depuis une heure.
- **Signaux faibles → ABSTENTION** : pool de moins de 30 minutes, mouvement de plus de 50 % en 5 minutes, activité
  concentrée sur peu de portefeuilles, réseaux de portefeuilles liés.

## 4. Sélection (module C) et coûts

### Les cinq règles, à paramètres figés

| Règle | Entrée | Sortie |
|---|---|---|
| `momentum_15` | +10 % sur 15 min avec volume doublé | objectif +20 %, stop −10 %, 60 min au plus |
| `breakout_60` | cassure du plus haut de 60 min avec volume triplé | suiveuse 10 %, 60 min au plus |
| `trend_ema` | croisement des moyennes exponentielles 5/20 | croisement inverse, stop −15 %, 120 min au plus |
| `reversal_30` | −30 % depuis le sommet de 60 min, bougie verte et volume | objectif +15 %, stop −15 %, 45 min au plus |
| `fast_exit` | entrée momentum | objectif +8 %, stop −5 %, 15 min au plus |

### Coûts d'un ordre de 11 $ (≈ 10 €, le minimum du hub)

- frais du DEX déclarés par plateforme (1 % par prudence si la plateforme est inconnue) ;
- impact à produit constant, avec deux bornes pour la réserve passée, inconnue :
  - « central » : √(prix relatif) ;
  - « prudent » : jamais plus que la réserve actuelle ;
- frais du réseau (base, priorité et pourboire) ;
- rente du compte de jeton (immobilisée, rendue à la clôture) ;
- 10 % de transactions échouées, chacune retardant d'une minute ;
- MEV de 30 pb.

Ce sont des hypothèses, **jamais présentées comme mesurées**. Le modèle est comparé aux allers-retours réellement
cotés par Jupiter (section 5.4).

### Causalité

- À la minute *i*, une règle ne voit que les bougies jusqu'à *i* (testé par troncature) ; l'exécution a lieu à la
  minute suivante.
- Le veto est décidé à la collecte, avec les seules données d'alors. **Seuls les trades ouverts après cette décision
  comptent** dans la comparaison « avec veto » / « sans veto ».
- Les pools sont découpés par date de création : 60 % pour l'entraînement, 20 % pour le développement et
  **20 % réservés**. La liste réservée est hachée (`839f7c22e88781a1…`), et aucune stratégie n'a été calculée dessus.

## 5. Résultats

Tableaux complets : [`mesures/memecoin-v1/lab.md`](mesures/memecoin-v1/lab.md). Données brutes et empreintes :
[`mesures/memecoin-v1/`](mesures/memecoin-v1/).

### 5.1 Veto à la collecte

| Décision | Pools |
|---|---|
| VETO | 127 |
| ABSTENTION | 0 |
| PASSE | 0 |

Neuf pools n'ont pas été contrôlés : la collecte borne le nombre de contrôles de sécurité (60 puis 64 pools).

L'unité de mesure est le pool : un même token peut en avoir plusieurs. Chaque pool cumule de 1 à 9 motifs de veto. Les plus fréquents :

| Motif | Occurrences |
|---|---|
| signalement Rugcheck « danger » | 173 (98 pools) |
| pool de moins de 30 minutes (signal faible) | 127 |
| liquidité sous le minimum | 118 |
| plus gros détenteur au-delà de 15 % | 54 |
| 10 plus gros détenteurs au-delà de 35 % | 52 |
| **aucune route de revente** | 42 |
| liquidité contradictoire entre sources | 19 |
| aller-retour immédiat au-delà de 10 % | 18 |
| liquidité ni verrouillée ni brûlée | 15 |

Deux constats découlent de ces chiffres :
- **Un pool sur trois (42 sur 127) était invendable** au moment de la détection : Jupiter ne trouvait aucune route de
  revente. Un acheteur n'aurait pas pu sortir.
- **Avec les règles actuelles, aucun pool ne peut passer à sa première lecture.** Le veto décide de 7 à 19 minutes
  après la création du pool, donc tout pool porte le signal « moins de 30 minutes » et le veto s'abstient au mieux.
  Sur ces données, cela ne change rien : chacun des 127 pools a aussi au moins un motif de veto fort, et 27 sont
  refusés pour leur seule liquidité. Mais pour qu'un pool puisse un jour passer, il faudra le relire plus tard (§ 6).

### 5.2 Issue après la décision (contrôle en avant)

On compare le prix à l'instant de la décision avec le dernier prix lu ensuite.

| Décision | Pools à l'issue lisible | Effondrés (−90 %) | Rendement médian | Moyenne nette après coûts | Part nette positive | Minutes de vie après la décision (médiane) |
|---|---|---|---|---|---|---|
| VETO | 45 | 9 % | −0,6 % | −33,7 % | 11 % | 25 |

Couverture : l'issue est lisible pour 45 pools. Pour 82 autres, **aucun échange** n'a eu lieu après la décision. Ces
82 pools ne sont pas comptés comme effondrés, faute de prix. Leur absence de la table rend donc l'issue du veto
**plus favorable qu'elle ne l'est**.

Issue par famille de motif (descriptif : petits effectifs, et un même pool peut figurer dans plusieurs familles) :

| Motif | Pools | Effondrés (−90 %) | Rendement médian | Moyenne nette après coûts | Part nette positive |
|---|---|---|---|---|---|
| créateur déjà signalé | 16 | 6 % | −0,3 % | −33,4 % | 0 % |
| concentration des détenteurs | 6 | 0 % | −26,8 % | −101,2 % | 17 % |
| revente impossible ou chère | 9 | 11 % | −18,4 % | −115,2 % | 0 % |
| liquidité faible | 42 | 10 % | −1,0 % | −37,0 % | 5 % |
| liquidité non verrouillée | 8 | 38 % | +12,2 % | −20,4 % | 62 % |
| aucun signalement « danger » | 16 | 0 % | −1,9 % | −14,2 % | 0 % |

Une moyenne nette inférieure à −100 % n'est pas une erreur. Dans un pool presque vide, le coût d'impact d'un ordre de
11 $ dépasse la mise. Ces pools sont de toute façon refusés pour leur liquidité. La médiane est la mesure à lire.

Les huit pools à liquidité non verrouillée ont la meilleure médiane (+12,2 %), mais aussi le plus d'effondrements
(38 %). C'est le profil d'un retrait de liquidité : le prix monte, puis la liquidité disparaît. Huit pools ne
suffisent pas à en tirer une règle.

### 5.3 Règles de sélection

| Règle | Entraînement (central) | Entraînement, délai de 2 min | Développement |
|---|---|---|---|
| `momentum_15` | 1 ordre, +7,9 % | 1 ordre, −7,8 % | aucun ordre |
| `breakout_60` | 2 ordres, −4,9 % en moyenne | 2 ordres, −4,9 % | aucun ordre |
| `trend_ema` | 2 ordres, −4,9 % en moyenne | 2 ordres, −4,9 % | aucun ordre |
| `reversal_30` | 2 ordres, −15,9 % en moyenne | 2 ordres, −52,6 % | aucun ordre |
| `fast_exit` | 1 ordre, +8,5 % | 1 ordre, −7,8 % | aucun ordre |

Les colonnes « avec veto » sont vides dans tous les cas : aucun pool n'a passé. Les deux seuls ordres gagnants
(`momentum_15` et `fast_exit`) ont eu lieu dans un pool qui s'est ensuite effondré. Un délai de 2 minutes les rend
perdants. Avec un ou deux ordres par règle, ces chiffres ne prouvent rien. Ils ne justifient aucune sélection de
candidat pour la partie réservée.

### 5.4 Modèle de coûts face aux devis réels

Les devis Jupiter sans portefeuille ont donné 72 allers-retours mesurés de 0,05 SOL (≈ 3,6 $) :

| | Coût d'aller-retour médian |
|---|---|
| mesuré | 3,0 % |
| modèle | 3,3 % |
| écart médian | −0,3 point (le modèle est légèrement prudent) |

L'écart absolu au 9e décile atteint 62 points. Les plus gros écarts vont dans les deux sens :
- **le marché coûte bien plus que le modèle** pour cinq pools dont la liquidité affichée allait de 1 700 $ à
  103 000 $ : l'aller-retour mesuré y perdait de 69 à 100 %. Une liquidité affichée ne prouve donc pas qu'on peut
  revendre. Le veto refuse ces pools grâce au devis réel (aller-retour au-delà de 10 %) ;
- **le modèle coûte bien plus que le marché** pour trois pools dont GeckoTerminal affichait une réserve presque nulle
  (moins de 1 $), alors que l'aller-retour réel ne perdait qu'environ 1 %. La réserve affichée était fausse. Le veto
  refuse aussi ces pools, pour leur liquidité.

### 5.5 Tests de stress (règle `momentum_15`, scénarios synthétiques)

| Scénario | Résultat |
|---|---|
| référence (tendance régulière) | +18,3 % net |
| **retrait de liquidité pendant la position** | **−96,9 %** : le stop ne protège pas |
| frais du réseau ×100 (congestion) | +3,6 % |
| frais du DEX ×3 | +16,9 % |
| toutes les transactions échouent une fois | +18,3 % |
| retard de 3 minutes | +18,3 % |
| liquidité ÷100 (600 $) | +6,3 % |
| taxe de transfert activée (Token-2022) | VETO |
| toutes les sources de sécurité en panne | ABSTENTION |

## 6. Limites et prochaine version (non appliquée)

**Limites de cette mesure.**
- Il n'y a que deux collectes d'un quart d'heure chacune : c'est un échantillon, pas un régime de marché.
- Les fenêtres de seconde lecture diffèrent entre les deux collectes (16 h et 90 minutes).
- Un pool sans bougie n'a pas d'issue lisible. Il est exclu du contrôle en avant au lieu d'être compté comme perdu.
- Le modèle d'impact suppose un pool à produit constant, ce qui n'est qu'une approximation pour les courbes de
  lancement de pump.fun.

**Pistes pour une version 2.** Elles ne sont **pas appliquées** : chacune exige un nouveau préenregistrement et des
données fraîches. Relancer le même banc sur ces données-ci ne serait pas une confirmation indépendante.
- Relire chaque token de 30 à 60 minutes après sa création, avec un second contrôle de sécurité, pour qu'il puisse
  sortir de l'abstention « pool trop récent ».
- Chercher les candidats parmi les pools déjà liquides (au moins 20 000 $) plutôt que dans le flux des pools nés depuis
  quelques minutes.
- Collecter sur plusieurs jours pour obtenir au moins une centaine de pools qui passent le veto avant toute évaluation.
- Fixer un horizon de seconde lecture identique pour toutes les collectes, par exemple 6 h.

### Comparaison avec le veto déjà présent sur `main` (`sentinelle/dmh/tokenrisk.assess_candidate`)

| Contrôle | `main` (Guardian V0) | M56 |
|---|---|---|
| liquidité minimale | 25 000 $ | 20 000 $, et désaccord entre sources → abstention |
| concentration | 10 premiers comptes **pools compris** ≤ 50 % (RPC public, souvent limité en débit) | 10 premiers détenteurs **hors pools et verrous** ≤ 35 %, plus gros ≤ 15 %, créateur ≤ 10 % (Rugcheck) |
| autorités d'émission et de gel | veto | veto |
| Token-2022 | refusé en bloc | refusé seulement pour les extensions à risque (taxe, crochet, délégué permanent) |
| signalements Rugcheck | veto dès le niveau « warn » | veto au niveau « danger », plus l'historique d'arnaques du créateur |
| **revendabilité effective** | non contrôlée | aller-retour Jupiter sans portefeuille : aucune route → veto |
| liquidité verrouillée ou brûlée | seulement si Rugcheck le signale | veto sous 50 % sur un pool classique |
| source muette | veto | abstention (ni feu vert, ni faux danger) |

Sur ces données, le contrôle de revendabilité a seul refusé un pool sur trois. Le veto de `main` ne le fait pas.
Brancher ce contrôle dans le Guardian de `main` relève d'une mission distincte, et ce document ne le fait pas.

## 7. Reproduire

Les quatre fichiers de données, leurs empreintes (`SHA256SUMS`) et le rapport du banc (`lab.json`, `lab.md`) sont
publiés dans `docs/validation/mesures/memecoin-v1/`. Le test
`tests/test_memecoin.py::test_published_m56_measures_are_intact_and_reproduce_the_published_report` vérifie deux
choses : les empreintes, et que le banc relancé sur ces fichiers redonne le rapport publié.

Sous Windows 11 (PowerShell, depuis la racine du dépôt) :

```powershell
py -3.12 -m pytest tests/test_memecoin.py -q
$m = "docs\validation\mesures\memecoin-v1"
py -3.12 tools\memecoin.py lab --capture "$m\capture-1794908298.jsonl.gz" "$m\capture-1795178083.jsonl.gz" --follow "$m\follow-1794908298-1795177171.jsonl.gz" "$m\follow-1795178083-1795183573.jsonl.gz" --output "$env:TEMP\m56"
```

La seconde commande affiche les tableaux de la section 5. Une nouvelle collecte (`tools\memecoin.py collect --out
data\memecoin`) lit des API publiques et ne passe aucun ordre. Elle produit d'autres données, donc d'autres chiffres.

Ces commandes ont été testées sous Linux avec Python 3.12. **Elles n'ont pas été testées sous Windows.**
