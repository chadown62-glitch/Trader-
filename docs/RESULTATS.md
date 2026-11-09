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

## 8. Bouclier temps réel : mesures (novembre 2026)

Le bouclier n'achète ni ne vend : il interdit d'ouvrir une position au mauvais moment. Ses seuils ont été vérifiés sur des données réelles pour qu'il ne bloque pas le bot trop souvent.

**Mouvement brutal** (bougie d'une minute ≥ 6 fois la normale et ≥ 0,8 %, pause de 30 min), mesuré sur 30 jours de bougies d'une minute (Binance, paires USDC) :

| Crypto | Pauses en 30 jours | Temps en pause | Agitation des 30 min suivantes |
|--------|--------------------|----------------|--------------------------------|
| BTC | 2 | 0,14 % | 11,9 × la normale |
| ETH | 15 | 1,12 % | 4,4 × |
| SOL | 19 | 1,41 % | 3,7 × |
| BNB | 5 | 0,47 % | 19,6 × |
| XRP | 13 | 0,99 % | 3,9 × |
| DOGE | 21 | 1,63 % | 5,2 × |
| ADA | 28 | 2,09 % | 4,8 × |
| LINK | 24 | 1,79 % | 3,7 × |

Chaque crypto reste donc ouverte aux entrées environ 98 % du temps ou plus. Les pauses tombent sur des périodes où le marché reste très agité : entrer à ce moment-là exposerait le stop à des mouvements bien plus grands que prévu. Avec un seuil de 0,6 %, il y aurait jusqu'à 5 fois plus de pauses (BTC), suivies d'une agitation moindre. Le seuil de 0,8 % est donc conservé.

**Cascades de liquidations** (OKX, BTC et ETH, 24 h d'historique) : environ 0,1 M$ liquidé par quart d'heure en temps normal. Le plus gros pic des 24 heures, 24 M$, coïncide avec la chute du BTC de 1,9 % en une minute (8 novembre 2026, 21 h 55 UTC), 57 fois l'amplitude habituelle. Seuil retenu : 10 M$ en 15 minutes et 20 fois le niveau habituel. Le seuil initial de 50 M$ n'aurait pas vu cet épisode.

## 9. Stratégie « EMA 9/21 en 1 minute » (proposée, rejetée)

Croisement de moyennes mobiles exponentielles 9 et 21, achat au croisement haussier, vente au croisement baissier, signal à la clôture et exécution à l'ouverture suivante. 30 derniers jours, Binance, paires USDC :

| Unité de temps | Crypto | Trades | Trades gagnants | Sans frais | Frais 0,075 % | Frais 0,1 % | Simple détention |
|----------------|--------|--------|-----------------|------------|---------------|-------------|------------------|
| 1 min | BTC | 1 011 | 28 % | −6,6 % | −79,5 % | −87,6 % | −9,7 % |
| 1 min | ETH | 1 046 | 27 % | −8,9 % | −81,0 % | −88,8 % | −15,1 % |
| 1 min | SOL | 1 023 | 27 % | −12,5 % | −81,1 % | −88,7 % | −21,7 % |
| 5 min | BTC | 216 | 25 % | −9,1 % | −34,3 % | −41,0 % | −9,7 % |
| 15 min | BTC | 65 | 28 % | −2,9 % | −11,9 % | −14,8 % | −9,7 % |

Même sans aucun frais, la stratégie ne fait pas mieux que la simple détention. Avec les frais réels, elle détruit le capital en un mois. Rejetée (détails dans [SOURCES.md](SOURCES.md)).

## 10. Ce qui pourrait améliorer les résultats (pistes, non prouvées)

1. ✅ *Fait* : historique étendu via les paires USDT (voir §6).
2. **Unité de temps journalière** : l'avantage paraît plus net, mais il y a trop peu de trades pour le prouver.
3. **Allocation de portefeuille par tendance** : être investi quand la tendance de fond est haussière et en USDC sinon, sans signaux d'entrée fins.
4. **Laisser tourner l'entraînement en direct** : c'est la seule preuve qui compte vraiment, car ce sont des données que personne n'a pu « optimiser ».
5. **Journal « microstructure »** : l'écart achat/vente, la liquidité et la pression acheteuse ou vendeuse sont enregistrés à chaque décision depuis novembre 2026. Après quelques mois, on pourra mesurer s'ils auraient amélioré les choix.

> La période sous scellés ayant été utilisée, toute nouvelle amélioration devra être jugée sur des données **futures**
> (entraînement en direct), sinon le risque de « sur-optimiser » le passé devient trop grand.
