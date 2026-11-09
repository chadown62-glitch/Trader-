# Résultats et preuves

> Ce document rapporte **tous** les résultats, y compris les mauvais. Un résultat passé, même mesuré honnêtement,
> ne garantit rien pour l'avenir. Résultats obtenus le 9 novembre 2026 sur les données publiques de Binance (paires USDC).

## 1. Méthode

| Point | Règle appliquée |
|-------|-----------------|
| Frais | 0,1 % par ordre (taux de base de Binance), plus 0,05 % de glissement de prix à chaque exécution, à l'achat comme à la vente |
| Exécution | Entrée au prix d'ouverture de la bougie suivante, stops déclenchés au plus bas réellement atteint, exécution au prix d'ouverture en cas de trou de cotation |
| Stop et objectif dans la même bougie | On suppose que le **stop** a été touché en premier (hypothèse pessimiste) |
| Positions | Une seule à la fois par crypto : pas de comptage multiple de situations qui se chevauchent |
| Apprentissage | Validation chronologique (« walk-forward ») avec purge : l'IA n'apprend jamais sur des résultats inconnus au moment du test |
| Période de contrôle | Les 20 % les plus récents, utilisés uniquement pour le verdict |
| Période sous scellés | Les 180 derniers jours, retirés de toute l'étude et utilisés **une seule fois** pour l'évaluation finale |

## 2. Preuves que l'apprentissage fonctionne (tests automatiques)

| Test | Attendu | Obtenu |
|------|---------|--------|
| Marché purement aléatoire (aucun avantage possible) | L'IA ne doit **pas** prétendre avoir trouvé une stratégie | « non validé » ✅ |
| Marché contenant un vrai motif caché (hausse entre 0 h et 12 h) | L'IA doit le découvrir sur des données jamais vues | Elle passe de −0,39 R à **+0,36 R** par trade et identifie l'heure comme facteur principal ✅ |
| Caractéristiques sans fuite d'information | Ajouter des bougies futures ne change aucune valeur passée | Identique à 10⁻⁹ près ✅ |
| Fenêtre en direct = historique complet | Les calculs en direct sont identiques à ceux de l'apprentissage | Identique ✅ |

Ces vérifications, et plus de 70 autres, tournent avec `pytest` (voir `tests/`).

## 3. Étude des stratégies sur données réelles

8 cryptos (BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK), environ 3,3 ans jusqu'au 13 mai 2026 (période sous scellés exclue), **sans filtre IA**.
Résultat moyen par trade en R, frais compris :

| Stratégie | 1 heure | 4 heures | 1 jour |
|-----------|---------|----------|--------|
| Retour à la moyenne | −0,26 R (826 trades) | −0,17 R (225) | −0,02 R (21) |
| Élan haussier | −0,19 R (740) | −0,01 R (163) | −0,28 R (24) |
| Cassure de tendance | −0,14 R (1 708) | **+0,10 R** (456) | **+0,26 R** (100) |
| Suivi de tendance (stop suiveur) | +0,03 R (550) | **+0,18 R** (163) | **+0,24 R** (40) |

**Lecture.** Sur les cryptos, les stratégies de court terme et de « retour à la moyenne » perdent de l'argent une fois les frais payés.
Le suivi de tendance garde un avantage sur les unités de temps longues. C'est cohérent avec ce qu'on observe généralement sur ces marchés.
Les intervalles de confiance restent larges : l'avantage est probable, pas prouvé.

**Décision** (prise avant de regarder la période sous scellés) : unité de temps de **4 heures** et stratégies de **tendance** activées par défaut.
Les autres stratégies restent disponibles dans les réglages.

## 4. Configuration retenue : tendance en 4 heures

### Période de contrôle de l'étude (environ 8 mois jusqu'en mai 2026, jamais vue pendant l'apprentissage)

| | Résultat moyen | Portefeuille | Baisse max |
|--|----------------|--------------|------------|
| Avec l'IA | +0,23 R (37 trades) | **+3,4 %** | 4,5 % |
| Tous les signaux | +0,05 R (104 trades) | −0,9 % | 4,2 % |
| Acheter et garder (moyenne des 8 cryptos) | | **−38,3 %** | |

### Évaluation finale sur la période sous scellés (mai à novembre 2026, utilisée une seule fois)

| | Portefeuille | Baisse max | Trades |
|--|--------------|------------|--------|
| Avec l'IA | **−1,4 %** | 2,4 % | 6 |
| Tous les signaux | −1,7 % | 4,6 % | 39 |
| Acheter et garder | **−12,9 %** | | |

**Conclusion honnête.** Sur ces six mois, le système a **perdu un peu (−1,4 %)** pendant que le marché perdait beaucoup plus (−12,9 %).
Il protège le capital, mais il **n'a pas gagné d'argent** sur cette période. Le suivi de tendance a besoin de marchés qui montent franchement.

## 5. Ce que voit l'utilisateur aujourd'hui

Entraînement depuis le panel sur 4 ans, en 4 heures, avec 8 cryptos. La période de contrôle couvre les 10 derniers mois :
- **verdict « non validé »** : 22 trades seulement, espérance négative récente (−0,39 R) ;
- conséquence : l'IA s'entraîne en argent fictif mais **le mode réel reste verrouillé**. C'est voulu.

Simulation accélérée sur un an (« tous les signaux ») : **−2,5 %**, baisse max 4,7 %, 42 trades. Sur la même période, acheter et garder les 8 cryptos donnait **−45,6 %**.
Le filtre de climat de marché a tenu le système à l'écart pendant les mois de baisse.

## 6. Test indépendant sur 2018-2022 (historique USDT jamais utilisé)

Les stratégies ont été choisies sur 2023-2026. Les années 2018-2022 (paires USDT, mêmes cryptos) n'avaient jamais été regardées.
C'est donc un test indépendant :

| Stratégie (2018-2022) | 4 heures | 1 jour |
|-----------------------|----------|--------|
| Suivi de tendance | **+0,33 R** (275 trades, IC95 +0,15 à +0,53) | **+0,77 R** (79, IC95 +0,33 à +1,33) |
| Cassure de tendance | **+0,16 R** (781, IC95 +0,07 à +0,25) | **+0,41 R** (180, IC95 +0,21 à +0,61) |
| Élan haussier | +0,15 R (266) | +0,48 R (36) |
| Retour à la moyenne | −0,13 R (340) | +0,25 R (41) |

Par année, en 4 heures, pour les stratégies de tendance : **2019 +0,14 R · 2020 +0,26 R · 2021 +0,14 R · 2022 −0,25 R**.

**Lecture.** L'avantage du suivi de tendance existe bien hors de la période qui a servi à le choisir, ce qui est encourageant.
Mais il dépend du climat : il gagne en marché haussier et perd un peu en marché baissier (2022). Le filtre de climat limite ces pertes en réduisant le nombre de trades.
Attention au biais du survivant : ces 8 cryptos sont celles qui existent encore aujourd'hui.

### Entraînement complet avec historique étendu (2017-2026)

La période de contrôle couvre les 18 derniers mois (mai 2025 à novembre 2026), une période baissière :

| Unité de temps | Résultat moyen | Portefeuille | Acheter et garder | Verdict |
|----------------|----------------|--------------|-------------------|---------|
| 4 heures | +0,03 R (234 trades) | −0,1 % à −1,2 % | −31,1 % | non validé (trop incertain) |
| 1 jour | +0,00 R (54 trades) | +0,9 % à +1,7 % | −28,6 % | non validé (trop incertain) |

Le système est resté à l'équilibre pendant que le marché perdait près de 30 %. Mais aucun avantage n'est **prouvé** sur cette période : la validation reste refusée.

## 7. Allocation par tendance (étudiée, non retenue)

Principe : être investi sur une crypto quand son climat est haussier, en USDC sinon (journalier, 8 cryptos, 2018-2026, frais compris).

| | Total | Par an | Baisse maximale |
|--|-------|--------|-----------------|
| Allocation par tendance | +1 678 % | +45,5 % | **54 %** |
| Acheter et garder | +6 045 % | +71,0 % | 80 % |

L'allocation réduit la baisse maximale, mais **54 % reste incompatible avec « jouer la sécurité »**, et elle a fait pire qu'acheter et garder en 2025 (−35 % contre −25 %). Non retenue.

## 8. Ce qui pourrait améliorer les résultats (pistes, non prouvées)

1. ✅ *Fait* : historique étendu via les paires USDT (voir §6).
2. **Unité de temps journalière** : l'avantage paraît plus net, mais il y a trop peu de trades pour le prouver.
3. **Allocation de portefeuille par tendance** : être investi quand la tendance de fond est haussière et en USDC sinon, sans signaux d'entrée fins.
4. **Laisser tourner l'entraînement en direct** : c'est la seule preuve qui compte vraiment, car ce sont des données que personne n'a pu « optimiser ».

> La période sous scellés ayant été utilisée, toute nouvelle amélioration devra être jugée sur des données **futures**
> (entraînement en direct), sinon le risque de « sur-optimiser » le passé devient trop grand.
