# Résultats et preuves

> Ce document rapporte **tous** les résultats, y compris les mauvais. Un résultat passé, même mesuré honnêtement,
> ne garantit rien pour l'avenir. Résultats obtenus les 9 et 10 novembre 2026 sur les données publiques de Binance (paires USDC).

## 1. Méthode

| Point | Règle appliquée |
|-------|-----------------|
| Frais | 0,1 % par ordre (taux de base de Binance), plus 0,05 % de glissement de prix à chaque exécution, à l'achat comme à la vente |
| Exécution | Entrée au prix d'ouverture de la bougie suivante, stops déclenchés au plus bas réellement atteint, exécution au prix d'ouverture en cas de trou de cotation |
| Stop et objectif dans la même bougie | On suppose que le **stop** a été touché en premier (hypothèse pessimiste) |
| Positions | Une seule à la fois par crypto : pas de comptage multiple de situations qui se chevauchent |
| Apprentissage | Validation chronologique (« walk-forward ») avec purge : l'IA n'apprend jamais sur des résultats inconnus au moment du test |
| Verdict « validé » | Gain moyen positif sur la période de contrôle avec **95 % de confiance** (moyenne − 1,645 erreur type > 0) et au moins 30 trades |
| Historique | Jamais de trou de cotation : la paire tradée sur sa période récente continue, précédée de la paire USDT (voir §10) |
| Période de contrôle | Les 20 % les plus récents, utilisés uniquement pour le verdict |
| Période sous scellés | Les 180 derniers jours, retirés de toute l'étude et utilisés **une seule fois** pour l'évaluation finale |

## 2. Preuves que l'apprentissage fonctionne (tests automatiques)

| Test | Attendu | Obtenu |
|------|---------|--------|
| Marché purement aléatoire (aucun avantage possible) | L'IA ne doit **pas** prétendre avoir trouvé une stratégie | « non validé » ✅ |
| Marché contenant un vrai motif caché (hausse entre 0 h et 12 h) | L'IA doit le découvrir sur des données jamais vues | Elle passe de −0,39 R à **+0,36 R** par trade et identifie l'heure comme facteur principal ✅ |
| Caractéristiques sans fuite d'information | Ajouter des bougies futures ne change aucune valeur passée | Identique à 10⁻⁹ près ✅ |
| Fenêtre en direct = historique complet | Les calculs en direct sont identiques à ceux de l'apprentissage | Identique ✅ |

Ces vérifications, et plus de 130 autres, tournent avec `pytest` (voir `tests/`). Le banc d'essai complet de l'apprentissage est décrit au §10.

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

Entraînement lancé depuis le panel (4 heures, 8 cryptos, modèle « mixte »), mesuré le 10 novembre 2026 :

| Historique | Période de contrôle (jamais vue) | Avec le tri de l'IA | Tous les signaux | Acheter et garder | Verdict |
|------------|----------------------------------|---------------------|------------------|-------------------|---------|
| 8 ans (choix par défaut) | 18 mois, depuis mai 2025 | **+0,19 R** par trade (87 trades), portefeuille **+6,6 %**, baisse max 5,0 % | +0,02 R (225 trades), −0,6 %, baisse max 4,8 % | **−43,1 %** | non validé, **modèle à l'essai** |
| 4 ans | 8 mois, depuis février 2026 | +0,00 R (62 trades), −2,2 %, baisse max 7,9 % | −0,07 R (71 trades), −1,8 % | −8,9 % | non validé |

**Lecture.** Sur 18 mois jamais vus, le tri de l'IA fait nettement mieux que « prendre tous les signaux », et le portefeuille gagne un peu pendant que le marché perd 43 %.
Mais avec 87 trades, la fourchette probable va de −0,09 R à +0,47 R : le hasard pourrait encore expliquer ce résultat. Le verdict reste donc **« non validé »**.
- Le modèle est mis **« à l'essai »** : l'IA d'entraînement s'en sert en argent fictif, avec une taille réduite de moitié. C'est le seul moyen d'obtenir des preuves sur des données vraiment nouvelles.
- **Le mode réel reste verrouillé.** C'est voulu.

Simulation accélérée sur un an (« tous les signaux », mesurée le 9 novembre) : **−2,5 %**, baisse max 4,7 %, 42 trades. Sur la même période, acheter et garder les 8 cryptos donnait **−45,6 %**.
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

### Entraînement complet avec historique étendu (2018-2026)

> **Correction du 10 novembre 2026.** Le tableau publié la veille était faussé par un défaut de données, corrigé depuis (voir §10 : trou de cotation des paires USDC).
> Voici les mesures refaites sur un historique continu. La période de contrôle couvre les 18 derniers mois (mai 2025 à novembre 2026), une période baissière.

| Unité de temps | Avec le tri de l'IA | Tous les signaux | Acheter et garder | Verdict |
|----------------|---------------------|------------------|-------------------|---------|
| 4 heures | +0,19 R (87 trades), portefeuille +6,6 % | +0,02 R (225 trades), −0,6 % | −43,1 % | non validé (pas encore prouvé à 95 %), modèle à l'essai |
| 1 jour | +0,00 R (54 trades), portefeuille +1,2 % | +0,00 R (54 trades), +0,9 % | −24,2 % | non validé |

En 4 heures, le système gagne un peu pendant que le marché perd beaucoup. En 1 jour, il y a trop peu de situations (862) pour que l'IA apprenne quoi que ce soit : son tri n'apporte rien.

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

## 10. Capacités d'apprentissage de l'IA (10 novembre 2026)

### a) Le banc d'essai : l'IA sait-elle apprendre ?

Sur un vrai marché, personne ne connaît la « bonne réponse ». Le banc d'essai fabrique donc des marchés artificiels où **une règle est cachée exprès**,
puis mesure quelle part de cette règle l'IA retrouve sur des données jamais vues, avec exactement la même chaîne d'entraînement que pour le vrai marché.
**Taux d'apprentissage** = (score de l'IA − hasard) / (score parfait − hasard). Chaque case est une moyenne sur 6 marchés différents.

| Règle cachée | Ancien modèle : 1 000 / 3 000 / 8 000 situations | Nouveau modèle « mixte » : 1 000 / 3 000 / 8 000 |
|--------------|--------------------------------------------------|--------------------------------------------------|
| Simple (une seule caractéristique) | 80 % / 89 % / 95 % | **94 % / 99 % / 99 %** |
| Combinée (deux conditions à la fois) | 85 % / 96 % / 97 % | **87 % / 97 % / 98 %** |
| En cloche (bon au milieu, mauvais aux extrêmes) | 62 % / 84 % / 89 % | **73 % / 83 % / 93 %** |
| Signal faible (noyé dans le hasard, comme sur un marché) | 4 % / 53 % / 71 % | **29 % / 85 % / 95 %** |
| **Moyenne** | **75 %** | **86 %** |

Autres recettes essayées (moyenne) : arbres simples seuls 87 %, choix automatique 86 %, comité de trois modèles 82 %, modèle linéaire seul 62 % (il ne voit pas la règle en cloche).
La recette « mixte » (arbres simples + modèle linéaire) est retenue : elle égale la meilleure sur le banc d'essai **et** elle est la meilleure sur données réelles (voir c).

Deux épreuves complètent ce taux :

| Épreuve | Résultat | Lecture |
|---------|----------|---------|
| **Bruit pur** (aucune règle à trouver) | Avant : un modèle était « validé » à tort dans **8 entraînements sur 18**. Après correction du critère : **2 sur 72**, et jamais par le tri de l'IA | L'ancien critère de validation était trop indulgent. Le nouveau exige 95 % de confiance (§1). |
| **Changement de règle** (la règle s'inverse aux trois cinquièmes de l'historique) | Taux d'adaptation : **0 %**, quelle que soit la recette, avec ou sans « oubli progressif » | Quand le marché change brutalement de logique, l'IA ne s'adapte pas tout de suite. Sa protection : son tri n'est pas validé tant que la nouvelle règle n'est pas apprise. |

Ce test se lance depuis le panel : **IA → Capacités d'apprentissage** (version courte, environ deux minutes). Sur la version courte, le modèle « mixte » obtient 97 % avec 4 000 situations.

### b) Défaut de données trouvé et corrigé : le trou de cotation

Binance a **retiré les paires USDC fin septembre 2022**, puis les a recotées entre mars 2023 (BTC, ETH, BNB) et mars 2024 (DOGE).
Avec un historique de plus de 4 ans, l'apprentissage enjambait ce trou comme s'il n'existait pas : les moyennes et les rendements calculés autour étaient faux
(pour le DOGE, il manquait presque tout de 2019 à 2024).
- Le réglage par défaut du panel pour l'entraînement (8 ans) était touché, ainsi que le tableau « historique étendu » du §6, refait depuis.
- L'étude des stratégies (§3 et §4, moins de 4 ans) et le test indépendant 2018-2022 (§6, paires USDT) n'étaient **pas** touchés.
- Correction : l'historique d'apprentissage ne contient plus jamais de trou de plus de 3 jours. La paire tradée est gardée sur sa période récente continue, et la paire USDT, cotée sans interruption depuis 2017, comble ce qui précède. Un test automatique le vérifie.

### c) Ce qui a été essayé pour mieux apprendre sur le vrai marché

Protocole : 8 cryptos, 4 heures, stratégies de tendance, 3 063 situations de 2018 à février 2025, validation chronologique purgée en 5 périodes.
La période de contrôle (après février 2025) n'a **pas** servi à choisir. Score : 0,50 = hasard. « Gain du tri » : résultat de la moitié la mieux notée, moins celui de toutes les situations.
Les écarts sont donnés avec leur fourchette à 90 % (rééchantillonnage par blocs mensuels).

| Piste | Score | Gain du tri | Écart avec l'ancien modèle | Décision |
|-------|-------|-------------|----------------------------|----------|
| Ancien modèle (arbres fins) | 0,534 | +0,04 R | référence | remplacé |
| Arbres simples seuls | 0,538 | +0,05 R | +0,004 (non significatif) | disponible |
| Modèle linéaire seul | 0,569 | +0,14 R | +0,035 [+0,008 ; +0,065] | disponible, mais aveugle aux règles en cloche |
| **Mixte : arbres simples + linéaire** | **0,561** | **+0,13 R** | **+0,027 [+0,004 ; +0,051]**, positif sur les 5 périodes | ✅ **adopté** |
| Mixte + oubli progressif (demi-vie 2 ans) | 0,567 | +0,12 R | +0,033 [+0,007 ; +0,059] | neutre par rapport au mixte : disponible, désactivé |
| Régression directe du résultat en R | 0,552 | +0,09 R | +0,018 [+0,001 ; +0,035] | non retenu (moins bon que le mixte) |

Pistes « plus de données » et « plus de connaissances », mesurées avec le modèle à arbres simples (score de référence 0,538) :

| Piste | Résultat | Décision |
|-------|----------|----------|
| Apprendre aussi sur 25 autres cryptos liquides | Score 0,535 : aucun gain | ✗ non retenu |
| Profil de chaque crypto (lien au BTC, liquidité, tempérament) | +0,013 sur 8 cryptos, mais une période tombe à 0,40 ; −0,008 avec les 25 autres cryptos | ✗ instable : l'IA apprend l'identité des cryptos « par cœur » |
| Force relative au BTC, jour de la semaine | +0,001 et +0,003 | ✗ rien de mesurable |
| État général du marché (part des cryptos en hausse…) | +0,007 sur 8 cryptos, −0,005 avec les 25 autres | ✗ pas robuste |
| Mémoire par crypto (résultats passés de chaque stratégie sur chaque crypto) | Corrélation avec le résultat suivant : 0,03 (8 863 cas) ; aucun gain pour le modèle | ✗ le passé d'une crypto ne prédit presque rien |
| Flux acheteur/vendeur de chaque bougie (qui achète « au marché ») | −0,006 sur 8 cryptos ; +0,007 [+0,001 ; +0,014] sur 33 cryptos | ◐ petit effet sur l'univers élargi seulement : à revoir |
| Apprendre sur **toutes** les bougies (135 000 à 475 000 exemples), pas seulement sur les signaux | Score par période : 0,53 · 0,52 · 0,54 · 0,53 · 0,50 · 0,48 ; moins bon sur les signaux (−0,02 à −0,03) | ✗ non retenu |

**Ce que ces mesures disent.**
1. **L'IA sait apprendre** : quand une règle existe, elle la retrouve (86 % en moyenne, 95 % d'un signal faible avec 8 000 exemples).
2. **Le vrai marché contient très peu d'information stable.** Même avec 475 000 exemples, un modèle fait à peine mieux que le hasard sur la période suivante, et plus du tout sur les plus récentes. Le frein n'est pas la capacité d'apprendre : c'est ce qu'il y a à apprendre.
3. **Plus un modèle est simple, mieux il résiste.** C'est le modèle le plus simple qui gagne sur données réelles, et les « connaissances » ajoutées n'aident pas de façon fiable.
4. **Le tri de l'IA a un petit avantage réel** : sur 33 cryptos (modèle à arbres simples), son score reste au-dessus du hasard sur les 5 périodes (0,51 à 0,56), et la moitié qu'elle préfère rapporte +0,06 R par trade quand l'ensemble fait −0,01 R.

### d) Les stratégies ne marchent pas sur toutes les cryptos

Mêmes règles, mêmes frais, historique continu :

| Univers | Cassure de tendance | Suivi de tendance |
|---------|---------------------|-------------------|
| Les 8 cryptos suivies (2018-2026) | +0,06 R (2 848 trades) | **+0,20 R** (1 283 trades) |
| 25 autres cryptos liquides (2019-2026) | **−0,09 R** (5 627 trades) | **−0,06 R** (2 014 trades) |

L'avantage du suivi de tendance est concentré sur les plus grandes cryptos. Or ces 8 cryptos sont précisément celles qui ont le mieux réussi : une partie de leur bon résultat passé tient à ce choix fait après coup (« biais du survivant »).
**Ajouter des cryptos à la liste suivie ne multiplie donc pas les gains.** La page « Fiches cryptos » montre, pour chaque crypto, ce que chaque stratégie y a donné.

### e) Apprendre en direct

- **Modèle « à l'essai »** : un modèle nettement positif sur la période de contrôle (au moins +0,05 R par trade et +0,05 R de mieux que « tout prendre »), mais pas encore prouvé à 95 %, est utilisé par l'IA d'entraînement en argent fictif, avec une taille réduite de moitié. Jamais en réel.
- **Suivi des prévisions** : pour chaque situation vue en direct, ce que l'IA annonçait est comparé à ce qui s'est passé. Un écart net est signalé.
- **Ré-entraînement automatique** : toutes les 24 heures dès 25 nouvelles situations dénouées. Un nouveau modèle ne devient actif que s'il est validé.

## 10 bis. Ré-apprendre aide-t-il ? (12 novembre 2026)

Mesure « modèle figé contre modèle ré-entraîné » sur l'historique Binance (8 cryptos, 4 heures, environ 8 ans, 3 898 situations dénouées, six périodes que ni l'un ni l'autre n'avait vues). Détail : [APPRENTISSAGE.md](APPRENTISSAGE.md), §7.

| Mesure | Modèle figé | Modèle ré-entraîné |
|--------|-------------|--------------------|
| Situations prises | 689 | 386 |
| Résultat moyen d'une situation prise | +0,037 R | +0,056 R |
| Score de tri (AUC) | 0,435 | 0,519 |

Différence par situation : **−0,002 R**, intervalle à 90 % de −0,032 à +0,028 R, sur 34 semaines. **Non départagés.** Le modèle figé trie moins bien que le hasard sur les périodes récentes (le marché a changé), mais le ré-entraînement ne se traduit pas par un meilleur résultat démontrable.

**Note sur les simulations accélérées de ce document.** Depuis le contrat d'exécution unique ([EXECUTION.md](EXECUTION.md)), la simulation accélérée achète à l'ouverture de la bougie qui suit le signal (avant : à la clôture de la bougie du signal). Les rendements de portefeuille simulés des §4 à §7 ont été mesurés avec l'ancienne règle et n'ont pas été refaits. Les résultats en R issus de l'étiquetage utilisaient déjà la bonne règle.

## 11. Ce qui pourrait améliorer les résultats (pistes, non prouvées)

1. ✅ *Fait* : historique étendu via les paires USDT (voir §6).
2. **Unité de temps journalière** : l'avantage paraît plus net, mais il y a trop peu de trades pour le prouver.
3. **Allocation de portefeuille par tendance** : être investi quand la tendance de fond est haussière et en USDC sinon, sans signaux d'entrée fins.
4. **Laisser tourner l'entraînement en direct** : c'est la seule preuve qui compte vraiment, car ce sont des données que personne n'a pu « optimiser ».
5. **Journal « microstructure »** : l'écart achat/vente, la liquidité et la pression acheteuse ou vendeuse sont enregistrés à chaque décision depuis novembre 2026. Après quelques mois, on pourra mesurer s'ils auraient amélioré les choix. Une première mesure sur l'historique (flux acheteur/vendeur par bougie de 4 heures, §10 c) ne montre presque rien.
6. **Le modèle à l'essai en direct** : ses trades en argent fictif sont la prochaine preuve attendue. Il faut environ 130 trades pour prouver à 95 % un avantage de +0,19 R par trade.

> La période sous scellés ayant été utilisée, toute nouvelle amélioration devra être jugée sur des données **futures**
> (entraînement en direct), sinon le risque de « sur-optimiser » le passé devient trop grand.
