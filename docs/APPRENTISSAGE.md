# Apprentissage : champion, challenger, preuves

Mis à jour le 12 novembre 2026.

Ce document décrit comment l'IA de Sentinelle apprend, comment un nouveau modèle remplace (ou non) l'ancien, et ce qui a été réellement mesuré. Tout se passe en argent fictif.

Le but n'est pas d'afficher une IA « intelligente » : c'est de pouvoir **démontrer** qu'elle apprend, ou de dire honnêtement qu'on ne peut pas encore le démontrer.

---

## 1. Les statuts d'un modèle

| Statut | Ce qu'il fait |
|--------|---------------|
| **Champion** | Le modèle approuvé : c'est lui qui décide, en argent fictif. Il a gagné sur une période qu'il n'avait jamais vue, avec une confiance d'au moins 95 %. |
| **Challenger** | Un nouveau modèle validé seul, mais pas encore prouvé meilleur que le champion. **Il ne trade pas** : ses décisions sont seulement enregistrées, à côté de celles du champion. |
| **À l'essai** | Prometteur, pas prouvé. Utilisé à taille réduite, et seulement s'il n'y a pas de champion. |
| **Retiré** | Ancien champion (conservé : on peut y revenir) ou modèle écarté. |
| **Rejeté** | Non validé, ou moins bon que le champion. |

Chaque version est **immuable** : son fichier porte une empreinte (SHA-256) vérifiée à chaque chargement. Un fichier modifié est refusé. Une version n'est jamais réécrite. Les paramètres, la période vue pendant l'apprentissage et les mesures de validation sont enregistrés avec elle.

---

## 2. Le cycle d'apprentissage

1. **Observer** : à chaque clôture de bougie, l'IA examine les signaux des stratégies.
2. **Enregistrer avant le résultat** : pour chaque situation, la décision et la probabilité du champion, du challenger et de l'ancien champion encore suivi sont écrites **avant** que l'issue soit connue. Les situations refusées sont enregistrées aussi.
3. **Attendre l'issue** : chaque situation est suivie jusqu'à son stop, son objectif ou sa durée maximale, qu'elle ait été prise ou non.
4. **Construire les exemples** : selon le contrat d'exécution unique ([EXECUTION.md](EXECUTION.md)), sans aucune donnée future.
5. **Entraîner un challenger** : régulièrement, avec toutes les données connues.
6. **Le tester sur des données jamais vues** : les 20 % les plus récents sont mis de côté.
7. **Le comparer au champion sur les mêmes situations**, qu'aucun des deux n'a vues.
8. **Mesurer la différence et son incertitude.**
9. **Promouvoir seulement si les règles ci-dessous sont remplies.**
10. **Surveiller après la promotion.**
11. **Revenir en arrière** si le nouveau champion fait nettement moins bien que l'ancien.

---

## 3. Les règles de promotion (fixées d'avance)

1. Un modèle n'est **validé** que s'il gagne sur une période qu'il n'a jamais vue, avec une confiance d'au moins 95 %.
2. **Sans champion**, le premier modèle validé le devient.
3. **Avec un champion**, un modèle validé ne le remplace que s'il fait **mieux sur les mêmes situations**, jamais vues par aucun des deux :
   - au moins **30 situations** où les deux modèles décident différemment ;
   - sur au moins **6 semaines** ;
   - et une différence dont l'intervalle de confiance (90 %, soit 95 % d'un seul côté) est **entièrement positif**.
4. S'il fait **nettement moins bien**, il est rejeté. Si l'on **ne peut pas conclure**, il attend comme challenger, sans trader. Au bout de 60 jours sans conclusion, il cède sa place au challenger suivant.
5. **Après une promotion**, l'ancien champion reste suivi 90 jours. Si le nouveau fait nettement moins bien que lui sur les mêmes situations, **retour automatique** à l'ancien.
6. **Rien n'est effacé** : chaque étape est écrite dans l'historique des modèles (page **Apprentissage**).

La comparaison se fait d'abord sur l'historique, au moment de l'entraînement. Mais le champion a souvent déjà vu cette période pendant son propre apprentissage : la comparaison serait alors injuste et elle est **refusée**. Le challenger est ensuite comparé au champion sur les **situations vécues en direct**, que ni l'un ni l'autre ne connaissait.

---

## 4. Ce qui rend la preuve plus exigeante qu'avant

| Correction | Pourquoi |
|------------|----------|
| **Incertitude par blocs de temps** | Plusieurs cryptos montent ou baissent ensemble, et des positions se chevauchent : les trades ne sont pas indépendants. L'incertitude est calculée par mois (par semaine pour une différence entre deux modèles), avec la loi de Student sur le nombre de blocs. Il faut au moins 6 blocs. Le résultat n'est jamais plus optimiste que le calcul habituel. |
| **Plus d'essais, plus d'exigence** | Chaque configuration différente essayée sur les mêmes données augmente le risque de valider un modèle par hasard. La confiance exigée devient 95 % ÷ le nombre de configurations essayées sur 180 jours (correction de Bonferroni). Un ré-entraînement identique ne compte pas comme un nouvel essai. |
| **Seuil choisi à part** | La calibration des probabilités et le choix du seuil d'espérance utilisaient les mêmes exemples. Désormais : calibration sur la première moitié (dans le temps) des prédictions hors échantillon, seuil choisi sur la seconde. |
| **Pas de fuite à la frontière** | Un exemple décidé avant la période de contrôle mais dénoué pendant elle dépend de prix de cette période : il est écarté de l'apprentissage, de la calibration et du choix du seuil. |
| **Références à risque égal** | La simulation « tous les signaux », référence des rapports d'entraînement, utilisait une taille de position réduite de moitié. Elle utilise maintenant la même taille que la simulation avec l'IA. |
| **Calibration contrôlée** | Le score de Brier (justesse des probabilités) est comparé à celui d'une prévision constante (le taux de gain moyen). La marge d'erreur annoncée dans le plan d'un trade vient de la calibration mesurée sur des situations jamais vues. |

---

## 5. Le plan écrit avant chaque trade

Avant chaque entrée (et aussi en cas de refus), l'IA écrit un plan, enregistré avec la décision :

1. raisons du signal (stratégie, climat de la crypto et du marché, volatilité, avis du modèle) ;
2. scénario haussier, baissier et neutre, avec leurs probabilités, qui se partagent 100 % ;
3. point d'entrée (premier prix disponible après la clôture) et condition d'abandon ;
4. stop et invalidation ;
5. objectif et rapport gain/risque (ou stop suiveur) ;
6. probabilité de gain et **marge d'erreur mesurée** (sans modèle : aucune probabilité n'est annoncée) ;
7. frais attendus (plateforme, glissement, conversion) ;
8. taille autorisée et risque en euros ;
9. événements qui font abandonner le trade.

Le plan **ne force jamais un trade** : il décrit une décision prise par le gestionnaire de risque et le modèle.

---

## 6. Le journal des décisions

Page **Apprentissage**, pour chaque situation examinée : le plan, ce que l'IA savait (caractéristiques et contexte), l'issue réelle ou théorique, la **cause la plus probable** et ce que le **modèle actuel** déciderait aujourd'hui sur la même situation.

| Cause | Quand |
|-------|-------|
| **Les frais** | Le prix est allé dans le bon sens, pas assez pour couvrir les coûts. |
| **Le mouvement du marché** | Le Bitcoin a bougé d'au moins 3 % dans le sens du résultat pendant le trade. |
| **Le signal s'est trompé** | L'IA annonçait au moins 60 % de chances de gain et le marché n'explique pas la perte. |
| **Issue conforme aux probabilités** | Le résultat était attendu au vu des chances annoncées. Ce n'est pas une erreur à corriger. |

La **mémoire de marché** (par crypto et par climat) affiche une « moyenne prudente », ramenée vers zéro tant qu'il y a moins de 30 cas : une corrélation fragile n'est pas présentée comme une certitude.

La **surveillance du champion** signale : des résultats en direct nettement inférieurs à ceux de sa validation, des pertes nettes, ou un marché qui ne ressemble plus aux données d'apprentissage (indice de stabilité des caractéristiques supérieur à 0,25).

---

## 7. Ce qui a été réellement mesuré

### a) Ré-apprendre aide-t-il ? (historique réel, 12 novembre 2026)

Méthode : un modèle est entraîné puis **figé** au milieu de l'historique. La suite est découpée en six périodes. Au début de chaque période, un second modèle est **ré-entraîné** avec tout ce qui est connu. Les deux décident ensuite sur la période, qu'aucun n'a vue. On compare leurs résultats situation par situation (en R, frais compris), avec une incertitude par semaines.

Données : Binance, 8 cryptos (ADA, BNB, BTC, DOGE, ETH, LINK, SOL, XRP), bougies de 4 heures, environ 8 ans, stratégies de tendance, recette « mixte ». 3 898 situations dénouées ; 1 704 comparées.

| Mesure | Modèle figé | Modèle ré-entraîné |
|--------|-------------|--------------------|
| Situations prises | 689 | 386 |
| Résultat moyen d'une situation prise | +0,037 R | +0,056 R |
| Total sur les situations comparées | +25,5 R | +21,5 R |
| Score de tri (AUC, 0,5 = hasard) | 0,435 | 0,519 |

**Différence : −0,002 R par situation, intervalle à 90 % de −0,032 à +0,028 R, sur 34 semaines.** Verdict : **non départagés**.

Ce que cela veut dire :
- ré-apprendre **ne fait ni mieux ni moins bien, de façon démontrable**, qu'un modèle figé sur cette période ;
- le modèle ré-entraîné trie mieux (AUC 0,52 contre 0,44 : le modèle figé fait **moins bien que le hasard** sur les périodes récentes, signe que le marché a changé) ;
- mais il prend beaucoup moins de trades, et au total les deux se valent ;
- les résultats varient fortement d'une période à l'autre (de −36 R à +15 R par période) : le hasard pèse lourd.

Cette mesure confirme le constat du §10 de [RESULTATS.md](RESULTATS.md) : sur ces cryptos, les prix passés contiennent très peu d'information stable. L'architecture champion / challenger est là pour **éviter de dégrader** le modèle, pas pour promettre un progrès.

Durée de la mesure : 15 secondes (historique déjà en cache). Elle se relance depuis la page **Apprentissage**.

### b) Effet des nouvelles exigences sur des marchés artificiels

Sur des marchés fabriqués avec une règle cachée « faible » (banc d'essai, 2 500 situations, 8 essais), avec les nouvelles règles :
- validés : 3 sur 8 (dont un en « règles seules ») ;
- à l'essai : 2 sur 8 ;
- non validés : 3 sur 8.

Sur du **bruit pur** (aucune règle, 8 essais) : **0 validé, 0 à l'essai**.

Avec 200 configurations essayées, la même règle faible n'est plus validée : il faudrait davantage de données.

---

## 8. Limites

- Avec 30 € et peu de trades par semaine, la plupart des comparaisons en direct resteront longtemps « pas encore assez de données ». C'est voulu : mieux vaut ne rien conclure que conclure à tort.
- La correction de Bonferroni est prudente, parfois trop : elle peut rejeter un vrai progrès.
- Les blocs de temps (un mois, une semaine) sont un choix raisonnable, pas une vérité : des dépendances plus longues (cycles de marché) ne sont pas couvertes.
- La cause « probable » d'un gain ou d'une perte est une règle simple, pas une analyse complète.
- La marge d'erreur des probabilités n'existe que pour les modèles entraînés avec cette version.
- Un modèle entraîné avant cette version ne porte pas la date de fin de ce qu'il a vu : aucune comparaison historique équitable n'est possible avec lui ; seule la comparaison en direct compte.
