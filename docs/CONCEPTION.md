# Sentinelle : dossier de conception

> **Nom provisoire.** Version 0.4, 9 novembre 2026 : première version du logiciel réalisée (voir §0 bis).
> Bot de trading crypto qui tourne 24 h/24, se pilote depuis Telegram et depuis un logiciel de gestion dédié, et analyse les actualités avec une IA.

---

## 0. En bref

- **Marché** : les cryptomonnaies, seul marché ouvert 24 h/24 et 7 j/7.
- **Prudence d'abord** : achat réel de cryptos (« spot »), **sans levier**, uniquement sur les plus liquides (BTC et ETH au départ).
- **Surveillance continue, trades sélectifs** : le bot observe en permanence mais n'agit que quand plusieurs signaux vont dans le même sens.
- **IA** : elle lit les actualités en direct et les note. Au départ elle peut seulement **freiner** le bot (bloquer un achat, réduire une position). Elle n'aura le droit de l'accélérer que si ses signaux font leurs preuves.
- **Gestionnaire de risque** : un module de règles fixes qui a le dernier mot sur toutes les décisions, y compris celles de l'IA.
- **Telegram** : notifications, commandes et lecture de canaux de signaux (A + B + C).
- **Logiciel de gestion** : une application web qui marche sur PC comme sur téléphone. Elle regroupe le tableau de bord, les réglages, l'historique et le bouton d'arrêt d'urgence.
- **Tout tourne sur votre PC** : le bot et l'IA (locale, gratuite) fonctionnent sur votre ordinateur allumé 24 h/24. Tout se pilote aussi depuis le téléphone (Telegram + logiciel de gestion).
- **Argent fictif d'abord** : le mode réel reste verrouillé tant que le bot n'a pas fait ses preuves en simulation.

---

## 0 bis. Ce qui a été réalisé (version 0.1 du logiciel)

| Élément prévu | État | Écart avec le plan initial |
|---------------|------|---------------------------|
| Moteur, données de marché, simulateur, risque, backtests | ✅ | — |
| Panel de gestion (PC + téléphone) | ✅ | Trading manuel complet ajouté (comptes papier et réel) |
| IA de trading (méta-modèle, validation chronologique, entraînement en direct, bulletin) | ✅ | Unité de temps par défaut **4 h** et stratégies de **tendance** (meilleurs résultats mesurés, voir docs/RESULTATS.md) |
| Connexion à un exchange réel | ✅ testée contre un faux exchange | **À tester sur le testnet** avant tout usage réel |
| Telegram (notifications + commandes + confirmation) | ✅ | Lecture de canaux de signaux (A) : **pas encore** (prévue plus tard, en observation) |
| Actualités + IA | ✅ | IA **locale** (Ollama, gratuite) au lieu de l'API Claude |
| Hébergement | ✅ | Sur **votre PC** Windows (choix « zéro frais d'IA ») |
| Tests | ✅ 77 tests automatiques + vérification dans un navigateur | — |

Résultat honnête à ce jour : l'IA est **non validée** sur les données récentes, donc le mode réel reste verrouillé.
Le système protège le capital (−2,5 % sur un an quand le marché faisait −45,6 %), mais il n'a pas encore prouvé qu'il gagne de l'argent.

---

## 1. À savoir avant tout

### 1.1 Aucun bot ne gagne à tous les coups

Les marchés réagissent à des événements imprévisibles : annonces, piratages, krachs, décisions politiques. Aucun bot, aucun fonds et aucun trader professionnel n'a 100 % de trades gagnants, et aucun logiciel n'est sans erreur. Un bot qui affiche 100 % de réussite cache presque toujours quelque chose :
- des pertes effacées de l'historique ;
- l'absence de stop-loss : il « attend que ça remonte »… jusqu'au jour où ça ne remonte pas ;
- ou une arnaque pure et simple.

Les objectifs réalistes du projet :
1. **Protéger le capital** : limiter les pertes avant de chercher les gains.
2. **Avoir une espérance positive après frais** : sur un grand nombre de trades, les gains l'emportent sur les pertes.
3. **Faire mieux, sur la durée, que d'acheter du bitcoin et de le garder**, avec des chutes beaucoup plus faibles. Ce n'est pas garanti. Ce sont les tests qui le diront.

### 1.2 Le taux de réussite ne veut rien dire tout seul

|                         | Bot A : « 95 % de réussite » | Bot B : « 45 % de réussite » |
|-------------------------|------------------------------|------------------------------|
| Trades gagnants         | 95 sur 100, +1 € chacun      | 45 sur 100, +20 € chacun     |
| Trades perdants         | 5 sur 100, −30 € chacun      | 55 sur 100, −10 € chacun     |
| **Bilan sur 100 trades** | 95 − 150 = **−55 €**        | 900 − 550 = **+350 €**       |

Sentinelle est conçu comme le bot B : des pertes petites et contrôlées, et des gains qu'on laisse courir.

### 1.3 Ce que l'IA peut faire, et ce qu'elle ne peut pas faire

**Elle peut :**
- lire des centaines d'actualités par jour, en continu, sans fatigue ;
- repérer vite les événements dangereux : piratage d'une plateforme, interdiction, faillite, stablecoin qui perd sa parité avec le dollar ;
- résumer, classer et noter chaque information.

**Elle ne peut pas :**
- prédire l'avenir ;
- battre les fonds professionnels en vitesse : ils réagissent aux nouvelles en millisecondes, et quand une actualité est publiée le prix a souvent déjà bougé ;
- être infaillible : elle peut se tromper, ou se laisser tromper par une fausse information.

L'IA sert donc d'abord de **garde du corps** (éviter les catastrophes). Elle devient **conseillère** seulement une fois qu'elle a fait ses preuves. Pour le vérifier, elle tourne en **mode observation** : chaque avis qu'elle donne est enregistré avec ce qui se serait passé si on l'avait suivi. On connaît ainsi sa valeur réelle avant de lui donner plus de pouvoir.

### 1.4 Qui pilote ?

Ce dossier et le code sont rédigés avec l'assistance de Claude, une IA. Mais une fois installé, le bot tourne **sur votre ordinateur et votre compte**, à votre nom. Ni Claude ni personne d'autre ne le surveille à votre place. Vous gardez la main à tout moment (pause, arrêt d'urgence, réglages), et c'est vous qui décidez si de l'argent réel y est engagé.

**Règle d'or : n'y mettez qu'une somme dont la perte totale ne changerait rien à votre vie.**

### 1.5 Sécurité ou profit maximum ?

Les deux s'opposent. Plus on cherche le gain maximum (levier, petites cryptos, trades très nombreux), plus le risque de tout perdre augmente. Vous avez demandé de « jouer la sécurité » : **en cas de conflit, la sécurité l'emporte toujours.**

Concrètement, le bot surveille le marché 24 h/24 mais ne trade que quand les conditions sont réunies : plutôt quelques trades par semaine que des dizaines par jour, car chaque trade coûte des frais.

---

## 2. Les décisions prises

| Sujet | Décision | Pourquoi |
|-------|----------|----------|
| Marché | Cryptomonnaies | Le seul marché ouvert 24 h/24, 7 j/7 |
| Type de trading | Spot (achat réel), **sans levier ni vente à découvert** | On ne peut pas perdre plus que sa mise et il n'y a pas de liquidation forcée |
| Cryptos tradées | BTC et ETH au départ (liste blanche réglable) | Les plus liquides et les plus difficiles à manipuler |
| Monnaie de référence | USDC (EUR possible en réglage) | Stablecoin conforme au règlement européen MiCA. Plusieurs grandes plateformes ont retiré l'USDT aux clients européens |
| Marché baissier | Le bot reste en USDC | En spot on ne gagne qu'à la hausse : rester à l'écart pendant les baisses est la meilleure protection |
| Plateforme (exchange) | Le bot fonctionne avec toutes les plateformes. Le choix final se fait en phase 6, parmi les plateformes agréées MiCA | Changer de plateforme reviendra à changer un réglage |
| Données pendant la simulation | Prix publics en direct, sans compte | On commence sans ouvrir aucun compte |
| Telegram | A + B + C | Votre demande, avec des garde-fous (voir §4.7) |
| IA | **Locale par défaut** : modèle open source (familles Qwen, Llama ou Mistral, ~8 milliards de paramètres) via le logiciel gratuit Ollama, sur votre carte graphique. API Claude en option, activable par un simple réglage | Aucun abonnement IA. Votre RTX 4060 Ti (8 Go) fait tourner ce type de modèle rapidement. L'API Claude reste disponible si vous voulez une analyse plus fine plus tard |
| Logiciel de gestion | Application web | Une seule application pour PC et téléphone, rien à installer côté navigateur |
| Hébergement | Votre PC Windows (i5-14400F, RTX 4060 Ti 8 Go, 16 Go de RAM), allumé 24 h/24. Un serveur en ligne reste possible plus tard | L'IA locale a besoin de votre carte graphique : bot et IA sur la même machine, sans frais d'hébergement |
| Mode par défaut | Simulation (argent fictif) | Le réel est verrouillé (voir §5) |

### 2.1 Choix techniques

| Élément | Choix | Pourquoi |
|---------|-------|----------|
| Langage | Python 3.12 | La référence pour la finance et l'IA, avec des bibliothèques éprouvées |
| Connexion aux exchanges | CCXT | Une interface unique pour plus de 100 plateformes |
| Calculs | pandas, numpy | Indicateurs et statistiques |
| Logiciel de gestion | Serveur FastAPI + interface web ; graphiques de prix avec la librairie open source Lightweight Charts | Fonctionne sur PC et sur téléphone |
| Base de données | SQLite | Un seul fichier, aucune maintenance, sauvegarde facile |
| Telegram | python-telegram-bot (notifications, commandes) + Telethon (lecture de canaux) | Bibliothèques de référence |
| IA | Ollama (IA locale) par défaut ; SDK officiel d'Anthropic en option | Module interchangeable : même format de réponse strict et vérifié, quel que soit le moteur |
| Déploiement | Installation native sous Windows (script d'installation fourni), démarrage automatique avec le PC | Plus simple et plus léger que Docker sous Windows, accès direct à la carte graphique pour Ollama |
| Qualité | Tests automatiques (pytest), journal détaillé | Moins de bugs et une traçabilité complète |

### 2.2 Organisation du code (prévue)

```
config/              réglages (fichier YAML) + secrets (.env, jamais publié)
sentinelle/
  data/              collecte des prix, bougies, indices
  news/              sources d'actualités + analyse IA
  strategies/        stratégies de trading
  risk/              gestionnaire de risque
  execution/         simulateur + connexion à l'exchange
  telegram/          bot (notifications, commandes) + lecture de canaux
  api/               serveur du logiciel de gestion
  storage/           base de données
  backtest/          tests sur l'historique
web/                 interface du logiciel de gestion
tests/               tests automatiques
docs/                documentation
```

---

## 3. Architecture générale

```
 SOURCES
 Prix crypto · Indices & macro · Sentiment · Actualités · Canaux Telegram
      │
      ▼
 ANALYSE
 Analyse technique · Climat du marché · IA actualités · IA signaux Telegram
      │
      ▼
 DÉCISION
 Stratégies : « Faut-il acheter ? vendre ? attendre ? »
      │
      ▼
 GESTIONNAIRE DE RISQUE  (règles fixes, dernier mot, veto sur tout)
      │
      ▼
 EXÉCUTION
 Simulateur (argent fictif, par défaut)   |   Exchange réel (verrouillé)
      │
      ▼
 BASE DE DONNÉES  (chaque décision est enregistrée avec sa raison)
      │
      ├────────▶  Logiciel de gestion (PC + téléphone)
      └────────▶  Telegram (alertes + commandes)
```

Dans l'autre sens, le logiciel de gestion et Telegram envoient des **commandes** au bot : pause, arrêt d'urgence, réglages.

---

## 4. Les modules en détail

### 4.1 Données de marché

- Prix, bougies (1 min à 1 jour), volumes et carnet d'ordres, en direct.
- Historique de plusieurs années pour les backtests.
- **Vérifications automatiques** : un prix aberrant, une donnée en retard ou un trou dans l'historique bloquent tout trade sur l'actif concerné jusqu'au retour à la normale.

### 4.2 Sources d'information

Principe : **plus de sources ne veut pas dire plus de gains.** Chaque source est ajoutée puis mesurée. Celle qui n'améliore pas les résultats est désactivée, ce qui réduit le bruit et les coûts. Le bot continue de fonctionner quand une source tombe en panne : il passe simplement en mode plus prudent.

| Catégorie | Sources | Apport | Coût | Phase |
|-----------|---------|--------|------|-------|
| Prix et volumes crypto | API publique de l'exchange | Base de toutes les décisions | Gratuit | 1 |
| Historique | Archives publiques de prix | Backtests | Gratuit | 1 |
| Sentiment | Indice « Fear & Greed » crypto | Euphorie ou panique du marché | Gratuit | 1 |
| Actualités crypto | Flux RSS : CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Magazine… | Régulation, piratages, ETF, adoption | Gratuit | 4 |
| Agrégateurs d'actualités | CryptoPanic ou équivalent | Plus de volume et de diversité | Gratuit ou payant selon l'offre | 4 |
| Annonces officielles | Exchanges (listings, retraits de cryptos, maintenances), régulateurs (SEC, AMF, ESMA) | Événements à fort impact | Gratuit | 4 |
| Produits dérivés | Taux de financement et intérêt ouvert (données publiques) | Repère l'excès de levier chez les autres traders, qui précède souvent les chutes en cascade | Gratuit | 4 |
| Marchés traditionnels | S&P 500, Nasdaq, VIX (indice de la peur), indice dollar, or, taux US à 10 ans | Climat général : appétit ou aversion pour le risque | Gratuit ou faible coût | 4 |
| Calendrier macro | Réunions de la Fed, chiffres de l'inflation et de l'emploi américains | Pics de volatilité prévisibles | Gratuit | 4 |
| Données on-chain | Volume des stablecoins (gratuit), flux vers les exchanges (payant) | Entrées et sorties d'argent | Gratuit ou payant | Plus tard |
| Réseaux sociaux | Reddit, X (Twitter) | Repère le buzz, mais très bruité et facile à manipuler ; l'API de X est très chère | Variable | Plus tard, optionnel |
| Canaux Telegram | Vos canaux de signaux | Signaux, d'abord en observation | Gratuit | 2 |

### 4.3 Le cerveau IA (actualités)

**Deux moteurs possibles, au choix dans les réglages :**
- **IA locale (par défaut, gratuite)** : un modèle open source tourne sur votre carte graphique via Ollama. Rien ne sort de votre PC et il n'y a aucun abonnement. Elle est un peu moins fine pour les actualités complexes, mais suffisante pour trier les nouvelles et juger si elles sont positives ou négatives. Comme l'IA ne peut que freiner le bot au départ, une erreur de sa part fait rater une occasion sans faire perdre d'argent.
- **API Claude (option payante)** : analyse plus fine, sur les serveurs d'Anthropic, payée à l'usage via un compte Anthropic **distinct de l'abonnement à l'application Claude** (voir §8).

Le mode observation (§1.3) permet de comparer objectivement les deux moteurs sur vos propres données.

Le circuit d'une actualité :
1. **Collecte** en continu depuis toutes les sources.
2. **Dédoublonnage** : la même info reprise par dix sites compte une fois. Les vieilles nouvelles republiées sont ignorées grâce à leur date de publication.
3. **Tri rapide** : un modèle IA rapide et économique écarte ce qui n'a pas d'importance.
4. **Analyse approfondie** des actualités importantes. L'IA remplit une fiche au format strict :
   - cryptos concernées (uniquement celles de la liste blanche) ;
   - type d'événement : régulation, piratage, ETF, partenariat, macro, etc. ;
   - sentiment (de −1 à +1), importance (de 0 à 1) et niveau de confiance ;
   - horizon d'impact (minutes, heures, jours) et résumé en une phrase, en français.
5. **Score d'actualité** par crypto, mis à jour en continu. Il est visible dans le logiciel et dans Telegram.

Les garde-fous :
- **L'IA ne passe jamais d'ordre elle-même.** Elle produit des scores, que la stratégie et le gestionnaire de risque utilisent.
- **Phase de départ : elle ne peut que freiner** :
  - bloquer un achat ;
  - réduire la taille d'une position ;
  - resserrer un stop ;
  - déclencher une alerte critique.
- **Une information doit être confirmée par plusieurs sources fiables** avant de peser sur une décision.
- **Protection contre les articles piégés** : un texte peut contenir des instructions cachées destinées à l'IA. Sa réponse reste donc une fiche aux valeurs bornées, et toute réponse hors format est rejetée.
- **Budget IA plafonné** chaque jour (option API Claude). Une fois le plafond atteint, le bot continue sans IA, en mode plus prudent.
- **Mode observation** : chaque avis est enregistré et son résultat mesuré (voir §1.3).

### 4.4 Les stratégies

Les décisions passent par **4 couches**. Un trade n'est ouvert que si les quatre sont au vert.

1. **Le climat (régime de marché)** : haussier, neutre ou baissier. Il se calcule à partir de la tendance de fond du BTC (moyenne mobile sur 200 jours), du climat des marchés traditionnels et du sentiment global. **En régime baissier, aucune nouvelle position n'est ouverte.**
2. **Le moment (signal technique)**. Deux stratégies au départ :
   - **« Tendance »** : achat quand le prix casse un plus haut récent en climat haussier, puis on laisse courir avec un stop suiveur. Peu de trades gagnants, mais des gains plus gros que les pertes.
   - **« Repli »** : achat sur une baisse passagère au sein d'une tendance haussière, pour viser un retour vers la moyenne. Plus de trades gagnants, mais des gains plus petits.
3. **Le danger (filtre actualités IA)** : veto en cas d'actualité grave, taille réduite si le climat des actualités est négatif, aucune nouvelle entrée autour des grandes annonces macro.
4. **Le garde-fou (gestionnaire de risque)** : calcul de la taille de position, contrôle des limites, veto final.

**Les sorties** :
- stop-loss initial, calculé selon la volatilité (ATR) ;
- stop suiveur, qui remonte avec le prix ;
- prise de bénéfices partielle à l'objectif ;
- sortie si rien ne se passe au bout d'un certain temps ;
- sortie d'urgence (actualité grave ou limite de perte atteinte).

**Honnêteté** : ces stratégies sont des points de départ classiques. Rien ne prouve à l'avance qu'elles seront rentables une fois les frais payés : le backtest puis la simulation trancheront. Pour éviter le piège de la « sur-optimisation » (une stratégie réglée pour coller au passé qui échoue ensuite), chaque stratégie a peu de paramètres. Elle est testée sur plusieurs années, dont au moins une période de baisse, et validée sur une période qui n'a pas servi à la régler. **Si aucune stratégie ne passe les tests, la bonne décision est de ne pas passer en réel, et le logiciel le dira clairement.**

### 4.5 Le gestionnaire de risque

C'est le cœur du système. Ses règles sont codées « en dur » : ni l'IA, ni une stratégie, ni un signal Telegram ne peuvent les contourner.

| Règle | Valeur par défaut | Réglable |
|-------|-------------------|----------|
| Risque par trade (perte si le stop est touché) | 0,5 % du budget | 0,1 à 1 % (plafond absolu : 2 %) |
| Positions ouvertes en même temps | 3 au maximum | 1 à 5 |
| Exposition totale | 60 % du budget au maximum | 10 à 100 % |
| Exposition sur une même crypto | 30 % du budget au maximum | 10 à 50 % |
| Perte maximale sur la journée | −2 %, puis plus de nouveau trade jusqu'au lendemain | Oui |
| Perte maximale sur 7 jours glissants | −5 %, puis pause et alerte ; reprise par vous | Oui |
| Baisse maximale depuis le plus haut (drawdown) | −10 %, puis **arrêt complet** ; reprise par vous | Oui (plafond : −25 %) |
| Pertes consécutives | 4 d'affilée, puis pause de 24 h | Oui |
| Stop-loss | **Obligatoire** sur chaque position, posé directement chez l'exchange | Non désactivable |
| Levier | **Interdit** | Non réglable |
| Rapport gain attendu / frais | Au moins 3 fois les frais aller-retour, sinon pas de trade | Oui |
| Annonces macro majeures | Pas de nouvelle position 1 h avant et 1 h après | Oui |
| Budget alloué au bot | Le bot n'utilise que ce montant, même si le compte contient davantage | Oui |
| Rachat « pour se refaire » après une perte | Interdit (délai minimal avant de reprendre une position sur la même crypto) | Oui |

### 4.6 L'exécution des ordres

**Simulateur (par défaut)** :
- prix réels en direct ;
- frais réels de l'exchange visé ;
- glissement de prix (slippage) simulé, volontairement un peu pessimiste ;
- tailles minimales et arrondis de l'exchange respectés.

Ainsi les résultats simulés restent aussi proches que possible du réel.

**Réel (verrouillé)** :
- **Ordres à cours limité** de préférence : frais réduits et pas de mauvaise surprise sur le prix. Un ordre qui n'est pas exécuté à temps est annulé.
- **Stop-loss envoyé chez l'exchange juste après l'achat** : la position reste protégée même si le bot, le serveur ou la connexion tombe en panne.
- **Réconciliation** au démarrage puis toutes les minutes : le bot compare ce qu'il croit détenir à ce que dit l'exchange. En cas de désaccord, c'est l'exchange qui fait foi et vous êtes alerté.
- **Identifiant unique pour chaque ordre** : pas de double achat si une coupure réseau survient pendant l'envoi.
- Respect des limites de requêtes de l'exchange et horloge synchronisée.

### 4.7 Telegram

**A. Lecture de canaux de signaux**
- Le bot se connecte avec votre compte Telegram via l'API officielle. Il faut un identifiant d'application, créé gratuitement sur my.telegram.org. Un bot classique ne peut pas lire les canaux dont il n'est pas administrateur.
- L'IA extrait de chaque message le signal (crypto, sens, prix d'entrée, objectif, stop). Le signal passe ensuite par le même circuit que les autres : stratégies, filtres et gestionnaire de risque.
- **Mode observation par défaut** : le bot enregistre ce que chaque signal aurait rapporté et calcule la **fiabilité réelle de chaque canal**. Beaucoup de canaux mettent en avant leurs gains et effacent leurs pertes, d'où l'intérêt de mesurer soi-même.
- Un canal ne peut devenir « actif » qu'après au moins 30 signaux observés avec un bilan positif, et seulement si **vous** l'activez.
- Les signaux portant sur des cryptos hors liste blanche sont ignorés : c'est la principale protection contre les opérations de « pump & dump ».

**B. Notifications**
- Ouverture et fermeture de trade, avec la raison et le gain ou la perte.
- Résumé quotidien, à l'heure de votre choix.
- Alertes : limite de perte atteinte, erreur, actualité critique, redémarrage du bot, source en panne.
- Niveau de détail réglable : tout, important seulement ou critique seulement.

**C. Commandes**

| Commande | Action |
|----------|--------|
| `/etat` | État du bot, mode, voyants |
| `/positions` | Positions ouvertes |
| `/gains` | Gains et pertes (jour, semaine, mois, total) |
| `/actus` | Dernières actualités importantes analysées |
| `/rapport` | Rapport détaillé |
| `/pause`, `/reprendre` | Suspendre ou reprendre les nouveaux trades |
| `/urgence` | **Tout revendre en USDC et tout arrêter**, avec confirmation |
| `/aide` | Liste des commandes |

**Sécurité** :
- Le bot ne répond **qu'à votre compte** : il garde une liste blanche de votre identifiant Telegram. Sans elle, n'importe qui découvrant le bot pourrait le piloter.
- Les commandes sensibles demandent une confirmation par bouton, et un code PIN si vous l'activez.
- Ne faites jamais transiter de clé API ni de mot de passe par Telegram.

### 4.8 Le logiciel de gestion

Une application web, protégée par mot de passe, utilisable sur PC et sur téléphone.

| Page | Contenu |
|------|---------|
| Tableau de bord | Bandeau du mode bien visible (SIMULATION en bleu, RÉEL en rouge), capital, gains et pertes, courbe du capital, voyants de chaque module, boutons **Pause** et **Arrêt d'urgence** |
| Marchés | Graphiques de prix avec les entrées et sorties du bot |
| Positions et ordres | Ce qui est ouvert en ce moment |
| Historique | Tous les trades, chacun avec son explication |
| Actualités | Flux analysé par l'IA : score, cryptos concernées, impact, filtres |
| Signaux Telegram | Canaux suivis et fiabilité mesurée de chacun |
| Statistiques | Taux de réussite, espérance, rapport gains/pertes, baisse maximale, frais payés, **comparaison avec « acheter et garder »** |
| Backtest | Tester une stratégie sur l'historique en un clic |
| Réglages | Risque, cryptos, stratégies, sources, IA (modèle, budget), Telegram, notifications. Chaque réglage est expliqué, avec sa valeur conseillée, et les modifications sont historisées |
| Journal | Événements et erreurs du système |
| Mode réel | La liste des conditions de déverrouillage (voir §5) |

**Accès** : sur le PC, dans le navigateur. Depuis le téléphone, via un réseau privé sécurisé gratuit (Tailscale) : le logiciel n'est **jamais exposé directement sur internet**. Il est protégé par mot de passe, avec une double authentification en option (code à 6 chiffres d'une appli comme Google Authenticator). Les clés (Telegram, IA, exchange) se saisissent dans le logiciel, sans aucun fichier à modifier.

### 4.9 Le journal « pourquoi »

Chaque décision est enregistrée avec ses raisons : climat, signal, score des actualités, contrôles de risque passés ou refusés. Exemple :

> *Achat ETH à 3 412 $ : climat haussier, cassure du plus haut sur 20 bougies de 4 h, actualités neutres (+0,1), risque 0,5 % (stop à 3 310 $). Trade autorisé.*

Vous pouvez toujours savoir **pourquoi** le bot a agi, ou pourquoi il s'est abstenu.

---

## 5. Les trois modes et le verrou du mode réel

| Mode | Argent | Usage |
|------|--------|-------|
| **Backtest** | Aucun, données passées | Tester une stratégie sur plusieurs années, en quelques minutes |
| **Simulation** (par défaut) | Fictif, avec les prix réels en direct | Rodage 24/7 dans les conditions du réel |
| **Réel** | Votre argent | Seulement après déverrouillage |

**Le mode réel ne se déverrouille que si toutes ces conditions sont remplies.** Le logiciel les vérifie automatiquement.

- [ ] Backtest positif après frais, sur plusieurs années incluant une période de baisse, et validé sur une période qui n'a pas servi aux réglages.
- [ ] Au moins 30 jours de simulation continue **et** au moins 25 trades simulés.
- [ ] Résultat de simulation positif après frais, avec une baisse maximale inférieure à 10 %.
- [ ] Aucune erreur critique durant les 7 derniers jours.
- [ ] Checklist de sécurité validée par vous : clés API sans droit de retrait, double authentification activée, restriction d'adresse IP, budget fixé.
- [ ] Confirmation finale tapée par vous.

Le réel démarre ensuite avec un **petit budget**. Il n'augmente que progressivement, et seulement si les résultats réels confirment ceux de la simulation.

---

## 6. Sécurité

- **Clés API de l'exchange** :
  - droits « lecture + trading » uniquement, **retrait désactivé**, pour que le bot ne puisse jamais sortir l'argent du compte ;
  - restreintes à l'adresse IP de votre box internet, si elle est fixe (c'est le cas chez certains fournisseurs ; sinon, on choisira une autre solution avant le réel) ;
  - rangées dans un fichier de secrets sur votre PC, jamais dans le code, jamais sur GitHub, jamais dans Telegram.
- **Dépôt GitHub** : ce dépôt est actuellement **public**. Il est conseillé de le passer en **privé** avant d'y ajouter vos réglages. De toute façon, les secrets n'y seront jamais envoyés : le fichier `.env` est exclu.
- **Double authentification (2FA)** sur l'exchange, sur Telegram et sur votre e-mail.
- **Logiciel de gestion** protégé par mot de passe (double authentification en option), jamais exposé sur internet, avec blocage après plusieurs essais ratés.
- **PC 24 h/24** : mise en veille désactivée, redémarrages de Windows Update limités à des heures choisies, redémarrage automatique du PC après une coupure de courant (réglage du BIOS) et relance automatique du bot au démarrage. Un onduleur est un plus, pas une obligation.
- **Sauvegarde automatique** quotidienne de la base de données.
- **Arnaques** : personne de sérieux ne vous demandera une clé API avec droit de retrait, vos codes 2FA, ni de l'argent « pour débloquer vos gains ». Les canaux Telegram regorgent de faux « bots à 100 % » et de faux gestionnaires de compte.

---

## 7. Pannes et imprévus

| Situation | Réaction prévue |
|-----------|-----------------|
| Coupure de courant ou d'internet, PC redémarré, bot planté | Les stop-loss sont déjà posés chez l'exchange, donc les positions restent protégées. Au redémarrage, réconciliation puis reprise. Un service de surveillance externe vous alerte si le bot ne donne plus signe de vie |
| Exchange en panne ou en maintenance | Pause automatique, alerte, reprise au retour à la normale |
| Krach éclair | Les stops jouent, la limite de perte journalière s'applique, pas de rachat « pour se refaire » |
| Piratage d'un exchange détecté dans les actualités | Alerte critique, arrêt des achats et proposition de tout passer en USDC. C'est vous qui décidez de retirer vos fonds |
| L'USDC perd sa parité avec le dollar | Alerte critique et pause |
| IA indisponible ou budget épuisé | Le bot continue sans IA avec des tailles réduites, ou se met en pause (au choix dans les réglages) |
| L'IA se trompe, ou tombe sur une fausse information | Pouvoir limité de l'IA, confirmation par plusieurs sources, cryptos très liquides uniquement |
| Une source de données tombe | Le bot continue avec les autres et un voyant orange s'allume dans le logiciel |
| Prix aberrant | Vérification croisée entre deux sources, trade refusé en cas d'incohérence |
| Série de pertes | Pause automatique (voir §4.5) |
| Signal Telegram douteux | Filtré par la liste blanche, la fiabilité mesurée et le gestionnaire de risque |
| Bug dans le code | Tests automatiques, limites de risque codées en dur, arrêt d'urgence, démarrage en simulation |
| Frais trop élevés | Filtre « gain attendu ≥ 3 fois les frais », suivi des frais dans les statistiques |
| Erreur de réglage | Valeurs bornées, confirmation des changements sensibles, historique des modifications |

---

## 8. Coûts estimés

| Poste | Estimation mensuelle |
|-------|----------------------|
| Données de marché et actualités RSS | Gratuit |
| Électricité du PC allumé 24 h/24 | Environ 10 € (à peu près 50 à 70 W en moyenne) |
| Frais de trading | Environ 0,1 à 0,5 % par ordre selon l'exchange : c'est **le principal coût caché**, d'où la sélectivité du bot |
| Sources premium (optionnel, plus tard) | De 0 à plusieurs dizaines d'euros |
| IA locale (par défaut) | Gratuit (comprise dans l'électricité) |
| IA API Claude (option) | Selon l'option ci-dessous, avec un plafond journalier réglable |

**Options API Claude** (seulement si vous activez l'IA payante). Estimation pour environ 500 actualités par jour après dédoublonnage, aux tarifs de novembre 2026 en dollars, à revérifier :

| Option | Fonctionnement | Coût estimé |
|--------|----------------|-------------|
| Éco | Tout est analysé par le modèle rapide et économique (Claude Haiku) | ≈ 3 à 5 $/mois |
| Équilibrée | Tri par Claude Haiku, puis analyse approfondie par le modèle le plus avancé (Claude Opus) des ~5 % d'actualités importantes | ≈ 20 $/mois |
| Max | Tout est analysé par Claude Opus | ≈ 130 à 150 $/mois |

**Attention aux petits budgets** : les coûts fixes pèsent lourd. Avec 300 € de budget et 25 € de frais mensuels, il faudrait gagner environ 8 % par mois rien que pour être à zéro, ce qui est irréaliste. Avec un petit budget, restez sur l'IA locale et considérez la première phase réelle comme un **test technique**, pas comme une source de revenus.

---

## 9. Fiscalité et cadre légal (France)

*Ceci n'est ni un conseil fiscal ni un conseil financier.*

- Les plus-values sur cryptomonnaies sont **imposables**.
- Les comptes ouverts sur des plateformes étrangères doivent être **déclarés chaque année** (formulaire 3916-bis), sous peine d'amende.
- Les règles dépendent du type d'opération. A priori, les échanges entre cryptos (stablecoins compris) ne sont pas imposés immédiatement, contrairement aux ventes en euros. Un trading très fréquent peut aussi changer le régime applicable. **Faites valider votre situation par un expert-comptable avant le passage en réel.**
- Le logiciel exportera l'historique complet des opérations (CSV) pour la déclaration.
- Utiliser un bot pour son compte personnel est légal. Choisissez une plateforme autorisée à servir les clients français (agrément MiCA), à vérifier sur le registre de l'ESMA et les listes de l'AMF.

---

## 10. Feuille de route

| Phase | Contenu | Ce qu'il vous faudra | Argent réel |
|-------|---------|----------------------|-------------|
| **1. Le moteur** | Données de marché en direct, climat de marché, 2 stratégies, gestionnaire de risque, simulateur, backtests, base de données | Installer le logiciel sur votre PC avec un guide pas à pas (Python, Ollama). Aucun compte de trading, aucun argent investi | Non |
| **2. Telegram** | Notifications (B), commandes (C), lecture des canaux en observation (A) | Créer un bot via @BotFather (5 min, guidé) ; pour A, des identifiants sur my.telegram.org | Non |
| **3. Logiciel de gestion** | Tableau de bord, réglages, historique, statistiques, backtests en un clic | Rien | Non |
| **4. IA et sources** | Actualités en direct et analyse IA, indices, macro, sentiment, dérivés | Rien avec l'IA locale (téléchargement du modèle, ~5 Go). Un compte API Claude seulement si vous choisissez l'option payante | Non |
| **5. Rodage** | Simulation 24/7 pendant au moins 30 jours, bilan chiffré, ajustements | Laisser le PC allumé 24 h/24 | Non |
| **6. Réel prudent** | Connexion à un exchange agréé, petit budget, verrou | Compte exchange vérifié (KYC), clés API sans retrait, 2FA | Oui, petit budget |
| **7. Amélioration continue** | Nouvelles sources et stratégies, toujours testées en simulation d'abord | — | — |

---

## 11. Questions ouvertes

1. Votre PC peut-il rester allumé 24 h/24 ? Connaissez-vous votre fournisseur internet (pour savoir si votre adresse IP est fixe) ?
2. Le budget envisagé pour la phase réelle, même approximatif : est-ce une somme dont la perte ne vous mettrait pas en difficulté ?
3. IA : locale (gratuite, par défaut) confirmée ?
4. Suivez-vous déjà des canaux Telegram de signaux ? Lesquels ?
5. Validez-vous les principes de prudence : simulation d'abord, verrou du mode réel, limites de perte ?

---

## Lexique

| Terme | Signification |
|-------|---------------|
| **API** | « Porte d'accès » qui permet à un logiciel de dialoguer avec un service (exchange, Telegram, IA) |
| **ATR** | Mesure de la volatilité moyenne d'un actif, utilisée pour placer les stops |
| **Backtest** | Test d'une stratégie sur les données du passé |
| **Drawdown** | Baisse du capital depuis son plus haut |
| **Espérance** | Gain moyen par trade, pertes comprises : c'est elle qui doit être positive |
| **Exchange** | Plateforme d'échange de cryptomonnaies |
| **KYC** | Vérification d'identité imposée par les plateformes |
| **Levier** | Emprunt qui multiplie la mise, et donc aussi les pertes : il peut tout faire perdre très vite |
| **MiCA** | Règlement européen qui encadre les cryptomonnaies et les plateformes |
| **Pump & dump** | Manipulation : on fait monter artificiellement une petite crypto, puis on revend sur le dos des suiveurs |
| **Simulation (paper trading)** | Trading en direct avec de l'argent fictif |
| **Slippage (glissement)** | Écart entre le prix visé et le prix réellement obtenu |
| **Spot** | Achat réel d'une crypto, que vous possédez vraiment |
| **Stablecoin** | Crypto dont la valeur suit une monnaie (1 USDC ≈ 1 dollar) |
| **Stop-loss** | Ordre de vente automatique qui limite la perte si le prix descend à un niveau fixé |
| **Stop suiveur** | Stop-loss qui remonte avec le prix pour protéger les gains |
| **Ollama** | Logiciel gratuit qui fait tourner des IA open source sur votre propre ordinateur |
