# COST OPTIMIZER v1 — coûts réels d'exécution fictive et méthodes d'exécution

Branche `claude/cost-optimizer-v1`, empilée sur la branche de préparation d'ALPHA FACTORY v2
(`claude/alpha-factory-v2`, elle-même empilée sur la PR #48). Rien n'est fusionné, `main` n'est pas
touché, aucun service n'est démarré, aucun ordre réel ou fictif n'est passé par ce moteur. Les PR #40,
#46, #47 et #48, le candidat gelé de la PR #48 et les protocoles gelés v2/v3 ne sont pas modifiés.

**Décision du 15/11/2026 (option B) respectée** : aucune évaluation d'ALPHA FACTORY v2 n'a été lancée ;
son plan n'est pas enregistré. Le contrat de coûts ci-dessous est construit sans lire aucun résultat de
stratégie (il n'en existe aucun sur données réelles).

## Résumé

Unité : le **point de base** (pb) vaut 0,01 % du montant de l'ordre ; 10 pb sur 10 € = 1 centime.

**Démontré par des mesures sur des exécutions reconstituées (1 246 échantillons, 2020 → 2026)**

1. Sur Binance au comptant, un ordre **au marché** de 5 à 30 € coûte presque exactement la commission :
   9,7 à 11,8 pb selon la classe d'actifs. La taille n'a aucun effet mesurable entre 5 et 30 €, et
   l'impact dans les carnets observés est nul jusqu'à 100 €.
2. Un **aller-retour** au marché (achat puis vente) coûte en moyenne 19 à 23 pb. Les memecoins et les
   « autres cryptos » ont une queue défavorable (1 % des allers-retours au-delà de 37 à 42 pb).
3. **Seule réduction de coût démontrée** : pour BTC, ETH et SOL achetés avec des euros, la paire en
   euros coûte **10 à 11 pb de moins par achat** que la conversion EUR → USDT suivie de la paire en
   USDT (intervalles de confiance à 95 % entièrement positifs).
4. Les **ordres à cours limité** ne font pas mieux que le marché une fois les non-exécutions traitées.
   La comparaison confirmatoire unique, fixée à l'avance, donne un **surcoût** de 1,94 pb
   [1,16 ; 2,83] pour la meilleure variante passive. Les variantes « annuler si non exécuté »
   semblent économiser 1 à 2 pb, mais laissent **10 à 48 % des ordres non exécutés** : ce n'est pas
   une économie.
5. Le moteur actuel est **optimiste** sur trois points mesurés : il exécute 100 % des ordres passifs
   dès que la bougie touche leur prix (le ruban réel : 54 à 93 %) ; il compte 5 pb de conversion au
   lieu de 11,7 ; il ne compte aucun écart achat/vente sans carnet (0,2 à 4,7 pb mesurés). Il est
   **pessimiste** sur le glissement des grandes paires (5 pb comptés, environ 0 mesuré).

**Reste hypothétique** : la commission de 0,10 % (la grille publique n'a pas pu être archivée ici),
la règle de l'actif dans lequel elle est prélevée, et tous les coûts des memecoins échangés sur la
chaîne (frais de réseau, pool, taxe, robots).

**Ce travail ne rend aucune stratégie rentable.** Sans avantage brut, le résultat net attendu est le
coût changé de signe : −19 à −23 pb par aller-retour sur une paire en USDT, environ −44 à −53 pb
depuis un portefeuille en euros, quelle que soit la méthode d'exécution.

**Proposition pour ALPHA FACTORY v2** : le contrat `cost_contract_v1`, figé, et sa traduction
`alpha-lab-cost-hypotheses-proposal.json` (section 8). Par ordre, scénario principal : 21,9 pb
(BTC, ETH) et 26,4 pb (autres) ; tension : 30,7 et 38,0 pb. Ces valeurs sont proches des
hypothèses provisoires (22 et 28) pour le scénario principal et nettement moins sévères en tension
(36 et 54).

## 1. Ce qui est livré

| Élément | Fichiers |
|---|---|
| Niveaux de preuve obligatoires (observé, estimé, déclaré, hypothétique) | `sentinelle/costs/evidence.py` |
| Grille de frais, filtres de marché observés | `sentinelle/costs/venues.py` |
| Minimums d'ordre, pas de quantité, reliquats | `sentinelle/costs/inventory.py` |
| Ruban de transactions publiques, estimation des meilleurs prix | `sentinelle/costs/tape.py` |
| Méthodes d'exécution (marché, à cours limité, partiels, non-exécutés, repli) | `sentinelle/costs/execution.py` |
| Carnets observés (réutilise le calculateur L2 existant `execution/liquidity.py`) | `sentinelle/costs/book.py` |
| Modèles par marché (crypto complet ; memecoins sur la chaîne en hypothèses ; autres refusés) | `sentinelle/costs/markets.py` |
| Incertitude (bootstrap par dates), bénéfice net d'un avantage hypothétique | `sentinelle/costs/stats.py`, `benefit.py` |
| Plan de mesure figé, mesures, contrat versionné et traduction pour ALPHA FACTORY v2 | `sentinelle/costs/plan.py`, `measure.py`, `contract.py` |
| Audit du moteur existant | `sentinelle/costs/audit.py` |
| Collecte publique bornée ; commande ; rapport | `tools/cost_fetch.py`, `tools/cost_optimizer.py`, `tools/cost_report.py` |
| Plan, journal, contrat | `research/cost-optimizer-v1/` |
| Mesures publiées | `docs/validation/mesures/cost-optimizer-v1/` |

## 2. Audit des composants existants

Quatorze hypothèses de coût ont été relevées dans le code existant (`python -m tools.cost_optimizer
audit`, détail dans `docs/validation/mesures/cost-optimizer-v1/audit.json`). Aucune n'était appuyée
sur une mesure : toutes sont de niveau HYPOTHÉTIQUE. Aucun de ces fichiers n'est modifié par cette
mission ; le verdict ci-dessous sert à l'intégration future.

| Composant | Où | Hypothèse actuelle | Verdict après mesure |
|---|---|---|---|
| Commission | `config.paper.fee_rate`, `execution/paper.py` | 10 pb, preneur et teneur | Cohérente avec la grille VIP 0 supposée ; non vérifiée ici |
| Actif des frais | `execution/paper.py` | toujours prélevée en devise de cotation | **Optimiste** : sans BNB, l'achat paie en actif reçu ; reliquat médian 2,6 % d'une position de 10 € sur BTC/ETH (p90 6,6 %) |
| Glissement | `config.paper.slippage_bps` | 5 pb fixes | **Pessimiste** à 10 € : glissement au-delà de l'écart ≤ 0 en moyenne dans toutes les classes ; 2,5 à 5,7 pb en tension sur les cryptos (30 €, 5 s) |
| Glissement du protocole gelé v2 | `research/prospective-v2/settings.json` | 10 pb | Incohérence avec les 5 pb par défaut ; fichier gelé, non modifié |
| Écart achat/vente sans carnet | `execution/paper.py` | 0 | **Optimiste** : 0,21 (BTC/ETH) à 4,69 pb (memecoins) par ordre, borne haute retenue |
| Écart, glissement et conversion du laboratoire alpha | `sentinelle/alpha/execution.py` | écart 4, glissement 5, conversion 0 | Conversion nulle **optimiste** (11,7 pb mesurés) |
| Conversion de devise | `config.portfolio.fx_cost_bps` | 5 pb par ordre, sans commission | **Optimiste** : 11,67 pb (commission + demi-écart + glissement EUR/USDT), 14,51 en tension |
| Ordre limite sur bougies | `execution/contract.py` | exécuté dès que la bougie touche le prix | **Optimiste** : 100 % d'exécution supposée contre 54 à 93 % sur le ruban |
| Ordre limite sur carnet | `execution/book_execution.py` | seule la partie qui traverse est exécutée | Prudent, confirmé |
| Ordre au marché | `execution/contract.py` | dernier prix + glissement + demi-écart | Juste si les paramètres sont ceux du contrat |
| Pas de quantité par défaut | `market/markets.py` | pas 1e-8, minimum 5 € | **Optimiste** : les pas observés sont plus grossiers et créent les reliquats |
| Seuil de rentabilité | `risk/manager.py` | gain visé ≥ 3 × frais estimés | À recalculer avec le contrat (frais estimés aujourd'hui trop bas sur les paires en USDT) |
| Bouclier : écart maximal | `risk/shield.py` | entrée refusée au-delà de 20 pb d'écart complet | PEPE et SHIB ont un écart complet observé d'environ 26 pb (un seul pas de prix) : ils seraient presque toujours refusés ; à trancher à l'intégration |
| Hypothèses provisoires ALPHA FACTORY v2 | `research/alpha-factory-v2/cost-hypotheses-v1.json` | 10 + 5 + 2/8 + 5 | Remplacées par la proposition mesurée (section 8), à valider |

## 3. Méthode

### 3.1 Sources et niveaux de preuve

| Composante | Source | Niveau |
|---|---|---|
| Pas de prix, pas de quantité, montant minimal | catalogue public `exchangeInfo`, réponses archivées et hachées | OBSERVÉ |
| Transactions historiques (2020 → 2026) | archive publique officielle `data.binance.vision`, fichiers journaliers vérifiés par leur somme SHA-256 publiée | OBSERVÉ |
| Meilleurs prix et écart à un instant passé | estimés à partir des transactions (dernier prix de chaque côté) | ESTIMÉ, erreur mesurée |
| Carnets récents (16/11/2026) | interface publique `depth`, réponses archivées | OBSERVÉ (heure de réception seule) |
| Commission | grille publique de Binance : la page n'a pas pu être archivée ici (réponse HTTP 202 vide) | HYPOTHÉTIQUE |
| Règle de l'actif des frais | documentation de la plateforme, non archivée ici | HYPOTHÉTIQUE |
| Memecoins sur la chaîne (frais de réseau, pool, taxe, robots) | aucune donnée collectée | HYPOTHÉTIQUE (fourchettes) |

L'interface publique ne sert les transactions que sur environ deux ans ; l'archive officielle les sert
depuis le début de chaque paire, y compris pour les paires retirées de la cote.

### 3.2 Plan de mesure figé avant la collecte

Plan `cost-optimizer-v1-m1`, empreinte `1880fac08245ac33afc64b032209b81d6344c0a7b1c53c23ea599f6dde4e6791`,
enregistré et publié (commit `37668ac`) **avant** tout téléchargement historique. Il contient le tirage
(graine publique), les règles de passage au contrat, la comparaison confirmatoire unique et l'empreinte
du code de calcul.

- 1 252 échantillons, 623 fichiers journaliers, du 01/01/2020 au 12/11/2026 ;
- classes : BTC et ETH en USDT ; autres cryptos en USDT (tirées dans l'univers daté d'ALPHA FACTORY v2,
  tel que connu la veille) ; memecoins cotés (DOGE, SHIB, PEPE, FLOKI, BONK, WIF) ; paires en euros
  (BTC, ETH, SOL) ; conversion EUR/USDT ;
- deux instants par fichier : 00:01 UTC (la minute qui suit l'exécution à l'ouverture supposée par le
  laboratoire) et un instant aléatoire de la journée ;
- ordres de 5, 10 et 30 €, achats et ventes, latences de 1, 5 et 30 secondes, attentes de 30 s, 60 s et 5 min ;
- moitié d'estimation avant le 01/06/2023, moitié de validation après.

### 3.3 Règles de remplissage d'un ordre à cours limité

Sans carnet historique, la file d'attente est inconnue. Deux bornes sont donc toujours calculées :

- **prudente** : l'ordre n'est exécuté que si une transaction s'est faite **au-delà** de son prix (le
  niveau a été entièrement consommé, notre ordre compris) ; s'il améliore le meilleur prix d'un pas,
  il est seul en tête de file et toute transaction à son prix l'exécute ;
- **optimiste** : toute transaction à son prix l'exécute, comme si personne n'était devant lui.

Un ordre non exécuté ne gagne rien : soit il est annulé (aucune position, coût d'opportunité mesuré),
soit le reste est exécuté au marché à la fin de l'attente.

### 3.4 Corrections déclarées après l'enregistrement du plan

Le journal `research/cost-optimizer-v1/journal.jsonl` (chaîné, en ajout seul) contient trois
corrections de code, chacune avec sa cause et son effet. Le test
`test_measurement_plan_is_intact_registered_first_and_its_code_unchanged_or_declared` échoue si un
fichier du calcul change sans déclaration.

| Fichier | Cause | Effet |
|---|---|---|
| `measure.py` | clés du tableau d'impact en nombres au lieu de chaînes | aucun effet sur une valeur |
| `measure.py` | **défaut du plan enregistré** : le tirage avec remise a produit 3 paires (actif, jour) en double ; à la collecte, une fenêtre en a écrasé une autre | chaque identifiant n'est utilisé qu'une fois : 1 246 échantillons distincts sur 1 252 (3 doublons comptés une fois, 3 fenêtres écrasées écartées) |
| `benefit.py` | la moyenne nette du tableau de lecture était simulée (bruit de quelques pb) | moyenne calculée exactement ; mesures et contrat inchangés |

Une mesure de vérification sur une collecte partielle (404 échantillons) figure aussi au journal,
annotée comme telle : elle ne sert ni au contrat ni au rapport.

## 4. Coût d'un ordre au marché

Collecte : 623 fichiers officiels (3,34 Go), **aucune somme de contrôle en échec**, 1 246 échantillons
utilisables, 7 458 mesures. Le coût total est l'écart entre le prix moyen obtenu et le prix milieu
estimé à l'instant de la décision, **commission de 10 pb comprise**. Tableaux complets :
`docs/validation/mesures/cost-optimizer-v1/tableaux.md` (produits par le code, aucune valeur saisie
à la main).

| Classe | Échantillons | Sans liquidité visible | Demi-écart à l'ouverture [IC 95 %] | Coût d'un ordre de 10 € à 1 s [IC 95 %] | p99 | Aller-retour moyen | Aller-retour p99 |
|---|---|---|---|---|---|---|---|
| BTC et ETH (USDT) | 240 | 0 % | 0,13 [0,07 ; 0,21] | 9,95 [9,92 ; 9,99] | 13,74 | 19,90 | 21,78 |
| autres cryptos (USDT) | 393 | 2,8 % | 1,69 [1,38 ; 2,02] | 10,42 [10,16 ; 10,70] | 25,82 | 20,78 | 37,06 |
| memecoins cotés (USDT) | 154 | 5,8 % | 3,21 [2,14 ; 4,69] | 11,80 [10,87 ; 13,03] | 37,38 | 23,15 | 41,61 |
| paires en euros | 336 | 16,4 % | 1,05 [0,71 ; 1,41] | 9,69 [9,33 ; 10,03] | 27,90 | 19,03 | 30,02 |
| conversion EUR/USDT | 120 | 7,5 % | 1,15 [0,81 ; 1,56] | 11,15 [10,78 ; 11,59] | 14,61 | 22,30 | 29,21 |

Lecture :

- au-delà de la commission, un ordre de 10 € coûte de 0 à 2 pb en moyenne ; la commission est
  donc le premier poste, loin devant l'écart et le glissement ;
- le coût est le même pour 5, 10 et 30 € et à 1, 5 ou 30 secondes de latence (moyennes 10,39, 10,40
  et 10,42 pb toutes classes confondues) ;
- « sans liquidité visible » : aucune transaction d'un côté dans la minute précédente ; ces
  échantillons sont comptés, jamais remplacés. Sur les paires en euros, c'est un cas sur six : le
  coût réel y est inconnu ;
- le glissement « au-delà de l'écart » sort légèrement négatif (−0,1 à −1,3 pb) : l'estimateur
  (dernier prix de chaque côté) surestime un peu l'écart du moment. Le contrat ne retient jamais de
  glissement négatif (plancher à 0) : il reste prudent.

**Carnets observés (sondes du 16/11/2026, 12 par paire)** : demi-écart médian de 0,001 pb sur
BTCUSDT, 0,03 sur ETHUSDT, BTCEUR et ETHEUR, 0,08 sur BNB, 0,5 à 1,7 sur XRP, SOL, DOGE, TRX, 3,3 sur ADA, 12,8 à
13,2 sur SHIB et PEPE (un seul pas de prix, très grand par rapport au prix). Impact d'un ordre de 30
ou 100 € : 0 sur les 14 paires. L'estimateur fondé sur les transactions a une erreur médiane nulle ;
il surestime en moyenne l'écart des grandes paires (+0,20 pb) et des memecoins (+0,56 pb) et aucune
classe ne montre de sous-estimation significative : la correction prévue au plan ne s'est pas
déclenchée.

## 5. Méthodes d'exécution : marché, à cours limité, partiels, non-exécutés

Ordre de 10 €, toutes classes, achats et ventes. « Rejoindre » = prix limite au meilleur prix de
notre côté ; « améliorer » = un pas de prix mieux. Gain positif = moins cher que le marché à 1 s.

| Méthode | Part exécutée | dont passive | Coût moyen | Gain face au marché [IC 95 %] |
|---|---|---|---|---|
| marché, 1 s | 97 % | 0 % | 10,39 | référence |
| marché, 30 s | 98 % | 0 % | 10,42 | −0,03 [−0,18 ; 0,12] |
| améliorer, 5 min, prudente, puis marché | 99 % | 82 % | 11,69 | −1,36 [−1,97 ; −0,79] |
| améliorer, 5 min, optimiste, puis marché | 99 % | 90 % | 10,65 | −0,34 [−0,97 ; 0,30] |
| rejoindre, 1 min, prudente, puis marché | 98 % | 62 % | 12,08 | −1,74 [−2,17 ; −1,30] |
| rejoindre, 30 s, prudente, puis annuler | **52 %** | 52 % | 8,64 | −0,00 [−0,28 ; 0,25] |
| rejoindre, 5 min, optimiste, puis annuler | **90 %** | 90 % | 8,11 | 1,85 [1,46 ; 2,31] |

(Les 27 variantes sont dans `tableaux.md`.) Les « 3 % non exécutés » du marché sont les
échantillons sans transaction en face pendant une minute.

**Comparaison confirmatoire unique** (fixée dans le plan avant la collecte) : la variante passive
avec repli au marché la moins chère sur la moitié d'estimation (avant le 01/06/2023) est
« améliorer, 5 min, prudente, puis marché ». Sur la moitié de validation : coût marché − coût
variante = **−1,94 pb [−2,83 ; −1,16]** ; même avec la règle optimiste : −0,62 [−1,63 ; 0,40].
**Amélioration non démontrée** : le contrat garde l'ordre au marché.

Explication la plus probable (sélection adverse, classique pour les ordres passifs, non mesurée
séparément ici) : quand l'ordre passif est exécuté, c'est souvent que le prix vient vers lui (le marché
aurait fait aussi bien) ; quand il ne l'est pas, le prix est parti, et le repli au marché paie plus
cher. Les variantes « annuler » ont un coût moyen plus bas **parce que** les cas défavorables
disparaissent sans être exécutés : 10 à 48 % des décisions ne sont jamais appliquées. Conformément à
la mission, ce n'est pas une économie démontrée.

**Optimisme du remplissage « au toucher » du moteur actuel** (445 échantillons « rejoindre ») :

| Attente | Bougie du jour touchée | Exécuté selon le ruban (prudente – optimiste) |
|---|---|---|
| 30 s | 100 % | 54 – 78 % |
| 1 min | 100 % | 64 – 86 % |
| 5 min | 100 % | 78 – 93 % |

## 6. Situations défavorables, routage, reliquats

**Situations défavorables** (ordre de 10 € au marché, états connus la veille) : les coûts restent
contenus. Quart le plus volatil (volatilité sur 30 jours) : médiane 10,37 pb, p90 16,47 (reste :
10,24) ; le demi-écart y triple (1,30 contre 0,44 pb) mais reste faible. Lendemain d'un mouvement de
plus de 10 % (55 échantillons) : médiane 9,47, p90 15,00. Latence de 30 s : différence non
significative. Faute de carnets historiques, le scénario de **tension** du contrat prend les 90e
centiles (demi-écart de tous les échantillons ; glissement d'un ordre de 30 € à 5 s), ce qui est
plus sévère que tous ces états mesurés.

**Routage depuis des euros** (achat de 10 €, mêmes instants) :

| Actif | Paires mesurées | Paire en euros | EUR → USDT puis paire en USDT | Surcoût du détour [IC 95 %] |
|---|---|---|---|---|
| BTC | 53 | 10,15 | 21,00 | 10,85 [10,07 ; 11,69] |
| ETH | 51 | 9,79 | 21,22 | 11,43 [10,00 ; 12,87] |
| SOL | 30 | 11,99 | 22,06 | 10,08 [6,03 ; 14,11] |

Réserve : la paire en euros est moins liquide (un échantillon sur six sans transaction récente d'un
côté) ; la comparaison porte sur les instants où les deux chemins étaient mesurables.

**Reliquats** (aller-retour de 10 €, commission prélevée dans l'actif reçu, filtres observés) : la
quantité revendable est arrondie au pas de la plateforme ; le reste, invendable seul, est rapporté à
part (sa valeur reste détenue, il n'est pas ajouté aux coûts). Médiane : 2,6 % de la position sur
BTC/ETH (p90 6,6 %), 1,9 % sur les paires en euros, 0,8 % sur les autres cryptos, 0 % sur les
memecoins. L'arrondi réduit aussi la taille achetée de 0 à 1 % (médiane).

**Memecoins échangés sur la chaîne** : non mesurés. Fourchettes HYPOTHÉTIQUES seulement
(`markets.dex_memecoin_hypotheses`) : frais du pool 25 à 100 pb par ordre, frais de réseau de
0,0005 à 0,5 € (Solana) ou de 0,2 à 20 € (Ethereum, soit 2 à 200 % d'un ordre de 10 €), taxe du
jeton de 0 à 20 %, pertes face aux robots de 0 à 200 pb. Même dans le cas le plus favorable (Solana, pool à 0,25 %), un
aller-retour sur la chaîne coûte plus du double d'un aller-retour sur un memecoin coté (≥ 51 pb contre 23).

**Autres marchés** : actions, ETF, forex et matières premières n'ont aucun modèle ; toute demande
lève `CostModelUnavailable` au lieu de renvoyer un coût inventé.

## 7. Comparaison avec le moteur existant

Coût d'un ordre, en pb, pour un portefeuille en euros (conversion comptée à chaque ordre sur une
paire en USDT, comme le moteur actuel) :

| Cas | Moteur actuel | Mesuré, principal | Mesuré, tension | Écart principal − actuel |
|---|---|---|---|---|
| BTC, ETH, SOL en euros | 15,0 (10 + 5 + 0) | 11,41 | 18,81 | **−3,6** (le moteur surestime) |
| BTC, ETH en USDT | 20,0 (10 + 5 + 0 + 5) | 21,88 | 27,48 | **+1,9** (le moteur sous-estime) |
| autres cryptos en USDT | 20,0 | 23,69 | 34,43 | **+3,7** |
| memecoins cotés en USDT | 20,0 | 26,36 | 37,65 | **+6,4** |

Améliorations, par nature :

| Amélioration | Statut | Ordre de grandeur |
|---|---|---|
| Coûts par classe mesurés au lieu d'un forfait | démontrée (précision) | le moteur actuel compte de 3,6 pb de trop (paires en euros) à 6,4 pb de trop peu (memecoins) par ordre |
| Probabilité d'exécution des ordres passifs | démontrée (précision) | le moteur actuel surestime de 7 à 46 points |
| Paire en euros plutôt que détour par l'USDT (BTC, ETH, SOL) | **démontrée (réduction)** | −10 à −11 pb par achat |
| Ordres passifs plutôt qu'au marché | **non démontrée** | surcoût de 1,9 pb en validation |
| Remise BNB (7,5 pb au lieu de 10) | hypothétique | −2,5 pb par ordre, suppose de détenir du BNB |
| Garder l'USDT entre deux opérations au lieu de reconvertir | hypothétique, à décider | jusqu'à −11,7 pb par ordre, mais ajoute un risque de change EUR/USDT non mesuré ici |

## 8. Contrat de coûts proposé pour ALPHA FACTORY v2

Fichiers (figés, empreintes dans `research/cost-optimizer-v1/cost-contract-v1.sha256` et au journal) :

- `cost-contract-v1.json` — `cost_contract_v1` 1.0.0, statut `PROPOSED_FROZEN`, `can_trade: false` ;
  chaque valeur porte son niveau de preuve, sa source, sa méthode et son effectif ;
- `alpha-lab-cost-hypotheses-proposal.json` — la même chose au format que lit le laboratoire
  (`sentinelle/alpha/lab/costs.py`), sans modifier le fichier provisoire de la branche v2.

Règles de passage (inscrites dans le plan **avant** la collecte) : demi-écart = borne haute à 95 %
de la moyenne à l'ouverture ; glissement = max(0, borne haute) ; tension = 90e centiles ; « autres »
du laboratoire = la plus chère des classes « autres cryptos » et « memecoins » ; glissement = le plus
élevé des classes en USDT ; conversion à chaque ordre.

| Composante, par ordre (pb) | Provisoire, principal | Provisoire, tension | Proposé, principal | Proposé, tension | Niveau |
|---|---|---|---|---|---|
| Commission | 10 | 10 | 10 | 10 | HYPOTHÉTIQUE |
| Conversion EUR/USDT | 5 | 5 | 11,67 | 14,51 | mesuré (demi-écart) + hypothèse (commission) |
| Demi-écart BTC, ETH | 2 | 6 | 0,21 | 0,42 | ESTIMÉ, erreur mesurée |
| Demi-écart autres | 8 | 24 | 4,69 | 7,71 | ESTIMÉ, erreur mesurée |
| Glissement | 5 | 15 | 0 | 5,73 | ESTIMÉ |
| **Total BTC, ETH** | **22** | **36** | **21,88** | **30,66** | |
| **Total autres** | **28** | **54** | **26,36** | **37,95** | |
| Aller-retour BTC, ETH / autres | 44 / 56 | 72 / 108 | 43,8 / 52,7 | 61,3 / 75,9 | |

Méthode d'exécution retenue : ordre au marché, latence 1 s. Le contrat a été construit **sans lire
aucun résultat de stratégie** : il n'en existe aucun sur données réelles (option B), et le test
`test_plan_structure_follows_its_own_rules` vérifie que le plan ne référence aucun résultat
d'ALPHA FACTORY v2.

### Bénéfice net d'un avantage brut hypothétique

`sentinelle/costs/benefit.py` répond à « si une stratégie avait tel avantage brut, que resterait-il
après les coûts mesurés ? ». L'avantage est une hypothèse fournie, jamais un chiffre d'ALPHA FACTORY
v2. Aller-retour au marché de 10 € sur paire en USDT, **hors conversion** (ajouter environ 23 pb par
aller-retour depuis des euros), dispersion supposée de 5 % par opération, 52 opérations par an :

| Classe | Avantage brut | Net moyen par aller-retour [IC 95 % sur un an] | Probabilité que l'année soit positive | Net annuel (10 €) |
|---|---|---|---|---|
| BTC et ETH | 0 pb | −19,9 [−152,2 ; 120,7] | 41 % | −1,03 € |
| BTC et ETH | 25 pb | +5,1 [−127,2 ; 145,7] | 55 % | +0,27 € |
| BTC et ETH | 100 pb | +80,1 [−52,2 ; 220,7] | 89 % | +4,17 € |
| memecoins | 0 pb | −23,2 [−156,5 ; 115,7] | 39 % | −1,20 € |
| memecoins | 25 pb | +1,8 [−131,5 ; 140,7] | 53 % | +0,10 € |

Deux leçons : (1) le seuil de rentabilité est le coût lui-même, environ 20 pb par aller-retour sur
une paire en USDT et environ 44 à 53 pb depuis des euros avec reconversion ; (2) **sans aucun
avantage, environ quatre années sur dix sortent positives par pur hasard** : une bonne année ne prouve
rien, d'où l'évaluation unique et préenregistrée prévue pour ALPHA FACTORY v2.

## 9. Limites

- Commission non vérifiée ici (page publique inaccessible) : 0,10 % par ordre, hypothèse.
- Pas de carnets historiques : l'écart passé est estimé à partir des transactions ; l'estimateur est
  validé sur des carnets récents seulement.
- File d'attente inconnue : les ordres passifs sont bornés, jamais supposés exécutés.
- Filtres de marché observés aux 14 et 16/11/2026, appliqués au passé (les pas ont pu changer).
- Les memecoins sur la chaîne ne sont pas mesurés : fourchettes hypothétiques seulement.
- Actions, ETF, forex et matières premières : aucun modèle, refus explicite.
- Windows 11 : non testé.
- Une seule plateforme (Binance au comptant) ; tailles mesurées de 5 à 30 € (sondes jusqu'à 100 €).
- La conversion est comptée à chaque ordre, comme le moteur actuel ; garder l'USDT entre deux
  opérations coûterait moins cher mais ajoute un risque de change qui n'est pas mesuré ici.
- Le plan enregistré contenait un défaut de tirage (3 doublons), corrigé et déclaré (section 3.4).

## 10. Décisions à valider avant l'évaluation unique d'ALPHA FACTORY v2

Ces choix doivent être faits **maintenant**, avant toute évaluation, et jamais en regardant ses
résultats :

1. **Adopter le contrat proposé** (section 8) comme scénarios principal et de tension de la v2, à la
   place des hypothèses provisoires. Recommandé.
2. **Commission** : 0,10 % reste une hypothèse. Si vous pouvez consulter la grille de votre compte
   (ou la page publique depuis votre navigateur) et me la confirmer avec sa date, elle passe au
   niveau DÉCLARÉ ; une valeur différente imposerait une nouvelle version du contrat (1.1.0), pas une
   retouche.
3. **Conversion** : à chaque ordre (recommandé, conforme à la comptabilité en euros du projet) ou
   « USDT conservé » (moins cher, mais risque de change à mesurer d'abord, donc une mission de plus).
4. **Exécution** : ordre au marché ; aucun ordre passif dans la v2, faute d'amélioration démontrée.
5. **Reliquats** : les modéliser dans le simulateur de la v2 (pas de quantité et commission dans
   l'actif reçu) ou les laisser rapportés à part. Recommandé : les modéliser, la règle est déjà
   testée (`inventory.round_trip`).

## 11. Reproduire

Sans réseau, sur les mesures publiées :

```
python -m tools.cost_optimizer verify      # plan intact, journal
python -m tools.cost_optimizer audit       # les 14 hypothèses existantes
python -m tools.cost_optimizer report --measures docs/validation/mesures/cost-optimizer-v1
python -m pytest tests/test_costs_engine.py tests/test_costs_measure.py tests/test_costs_protocol.py -q
```

Mesure complète (3,3 Go publics téléchargés, sommes de contrôle officielles vérifiées) :
`tools/cost_fetch.py bulk --plan research/cost-optimizer-v1/measurement-plan.json --root <archive>`,
puis `probe`, puis `tools/cost_optimizer.py measure --archive … --data … --out …`. Le code refuse
de réenregistrer le plan et d'écraser le contrat figé.

## 12. Empreintes (SHA-256)

| Objet | Empreinte |
|---|---|
| Plan `cost-optimizer-v1-m1` | `1880fac08245ac33afc64b032209b81d6344c0a7b1c53c23ea599f6dde4e6791` |
| Mesures (`measures.json`) | `25a475a07f5ce14c92de4bd5ea093d0c00bff479b24ab961853744788181f9b9` |
| Contrat `cost-contract-v1.json` | `965d29bbf4f965285f7c0815130c0002d29b224707915362e91bfa3d98952a71` |
| Proposition pour le laboratoire | `56da1470cf32e1603618dd52f54e22bd0a2134db08d947d0bea45d64ac179c40` |
