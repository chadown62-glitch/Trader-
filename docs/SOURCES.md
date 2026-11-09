# Sources d'information en temps réel

Mis à jour le 9 novembre 2026. Ce document passe en revue les sources et techniques proposées par Gemini, une par une : ce qui a été branché, ce qui ne l'a pas été, et pourquoi.

Quatre critères ont servi au tri :
- **gratuit**, ou un coût justifié par un gain prouvé ;
- **utilisable** depuis votre PC Windows, sans serveur à louer ;
- **utile** pour une stratégie qui décide à la clôture des bougies de 4 heures ;
- **mesurable** : une source ne pèse sur les décisions que si son utilité est démontrée.

Toutes les sources ci-dessous ont été testées sur le réseau réel le 9 novembre 2026.

---

## 1. Ce qui est branché

### Les « yeux » : informations

| Source | Rôle | Délai | Test réel |
|--------|------|-------|-----------|
| **Calendrier économique** (Forex Factory, fichier public de la semaine) | Aucune nouvelle position 60 minutes avant et après une annonce à fort impact (USD et EUR par défaut). Les valeurs attendues et précédentes sont affichées. | Relu toutes les 3 h | 4 annonces cette semaine, dont l'inflation américaine (CPI) le 10 novembre à 13 h 30 UTC |
| **Fed, BCE, SEC** (flux RSS officiels) | Communiqués lus à la source, avant leur reprise par les médias, puis analysés par l'IA locale | Lecture toutes les 10 min (réglable) | 20, 15 et 25 communiqués reçus |
| **Médias crypto** (Cointelegraph, CoinDesk, Decrypt, Bitcoin Magazine, The Block) | Actualités analysées par l'IA locale | Lecture toutes les 10 min | 10 à 39 articles par flux, le plus récent datant de moins de 3 h |
| **Canaux Telegram de dépêches** (à choisir vous-même) | Flashs reçus **en quelques secondes** et analysés dès leur réception | Quelques secondes | Testé avec des messages types ; la connexion à votre compte reste à vérifier sur votre PC |
| **Carnet d'ordres Binance** (public) | Écart achat/vente et liquidité vérifiés avant chaque entrée | Au moment de la décision | BTC : écart 0,00 %, 764 000 USDC disponibles à moins de 0,5 % du prix |
| **Liquidations forcées OKX** (public, BTC et ETH) | Détection des cascades de ventes forcées sur les marchés à levier | Chaque minute | 24 h relues au démarrage : 5 421 liquidations, 57 M$ au total |

**Regroupement des doublons.** Une même nouvelle reprise par cinq médias n'est analysée qu'une fois (titres comparés mot à mot). Elle n'est « confirmée » que si **au moins deux sources différentes** la rapportent. Une rumeur répétée par un seul média ne bloque donc rien.

### Le « bouclier » : quand ne pas entrer

Le bouclier ne vend rien et ne prédit rien. Il **empêche seulement d'ouvrir une position au mauvais moment**. Pour un achat manuel, il affiche un avertissement sans bloquer.

| Garde | Déclenchement | Pause | Mesure sur données réelles |
|-------|---------------|-------|----------------------------|
| **Mouvement brutal** | Bougie d'une minute ≥ 6 fois l'amplitude habituelle **et** ≥ 0,8 %. Un choc sur le BTC concerne tout le marché. | 30 min | Sur 30 jours et 8 cryptos : pause **0,1 % (BTC) à 2 % (ADA) du temps**. Les 30 minutes suivantes sont **environ 4 à 20 fois plus agitées** que la normale. |
| **Écart achat/vente** | Écart > 0,20 % | Le temps que l'écart se resserre | En temps normal : 0,00 à 0,01 % sur BTC, ETH et SOL |
| **Liquidité** | Moins de 3 fois la taille de l'ordre disponible à moins de 0,5 % du prix | Idem | En temps normal : 400 à 1 500 fois un ordre de 500 USDC |
| **Cascade de liquidations** | Plus de 10 M$ liquidés en 15 min sur le BTC et l'ETH (OKX), et plus de 20 fois le niveau habituel | 1 h | Habituel : environ 0,1 M$ par quart d'heure. Pic à **24 M$** le 8 novembre 2026 à 21 h 52 UTC, pendant la chute du BTC de **1,9 % en une minute** |
| **Actualité majeure** | Nouvelle jugée très importante (≥ 0,9) et fiable (≥ 0,7) par l'IA locale, dans un sens ou dans l'autre | 60 min | — |

Les seuils se règlent dans **Réglages → Bouclier**. L'état du bouclier apparaît sur le tableau de bord, et dans `/etat` sur Telegram.

### Le journal « microstructure »

À chaque décision de l'IA, Sentinelle note aussi :
- l'écart achat/vente et la liquidité ;
- la **pression acheteuse ou vendeuse** des 15 dernières minutes (part des transactions à l'achat contre celles à la vente) ;
- le Fear & Greed, le taux de financement et les liquidations récentes.

Ces données **ne changent pas encore les décisions**. Elles permettront de vérifier, sur plusieurs mois, si elles auraient amélioré les résultats. C'est le même principe que pour l'IA : rien n'est utilisé avant d'être prouvé.

---

## 2. Ce qui n'est pas branché, et pourquoi

| Suggestion | Décision | Raison |
|------------|----------|--------|
| Bloomberg Terminal, Refinitiv (LSEG) | ✗ | Plus de 25 000 € par an et par poste : hors de portée d'un particulier |
| Benzinga Pro, The Fly, Unusual Whales, Polygon, Databento, LunarCrush ou Santiment (offres payantes) | ✗ | Abonnements de plusieurs dizaines à plusieurs centaines d'euros par mois. Pour une décision toutes les 4 h, les sources gratuites suffisent. |
| API de X (Twitter) | ✗ | Lecture payante et très bruitée |
| Reddit | ✗ | L'API refuse les accès sans compte (erreur 403 au test). L'intérêt du « sentiment de foule » n'est pas prouvé. |
| GDELT (actualités mondiales) | ✗ pour l'instant | Injoignable lors du test (délai dépassé) et très bruité. À réévaluer. |
| Alpha Vantage, Tiingo, Financial Modeling Prep, Trading Economics | ✗ pour l'instant | Clé obligatoire et offres gratuites très limitées. Le calendrier Forex Factory couvre déjà le besoin. |
| SEC EDGAR (dépôts des entreprises) | ◐ | Les communiqués de la SEC sont lus. Les dépôts d'entreprises concernent surtout les actions. |
| Banque du Japon, Banque d'Angleterre | ◐ facultatif | Ajoutez « JPY, GBP » dans **Réglages → Calendrier macro → Monnaies suivies**. Leur effet sur les cryptos est plus faible que celui de la Fed. |
| FRED (statistiques de la Fed de Saint-Louis) | ✗ | Données publiées avec retard : utiles en recherche, pas pour réagir |
| « Squawk » audio + transcription automatique | ✗ | Abonnement payant, et quelques secondes gagnées n'ont pas de valeur pour une stratégie en 4 h |
| MarineTraffic, FlightAware | ✗ | Aucun lien démontré avec le prix des cryptos |
| Kafka, RabbitMQ, Redis | ✗ | Conçus pour des millions de messages répartis sur plusieurs serveurs. Ici, une file interne suffit, et c'est un risque de panne en moins. |
| FinBERT, vLLM | ✗ | L'IA locale (Qwen 2.5 7B via Ollama) lit déjà le français et l'anglais et donne une analyse détaillée. FinBERT ne dit que « positif » ou « négatif », et vLLM ne fonctionne pas directement sous Windows. |
| Latence inférieure à 50 ms, serveurs à côté de la plateforme | ✗ | Course perdue d'avance face aux professionnels, et inutile en 4 h. Le bouclier préfère **ne pas entrer pendant la tempête**. |
| Flux en continu (WebSocket) de Binance ou Bybit | ✗ pas nécessaire | Le relevé toutes les 5 s suffit au bouclier. Les stops réels sont posés **chez la plateforme** et réagissent instantanément, même PC éteint. |
| CVD (volume cumulé acheteur/vendeur), détection de « spoofing » | ◐ | La pression acheteuse/vendeuse est enregistrée à chaque décision (journal ci-dessus). Elle sera testée avant d'être utilisée. |
| Détection de régime par modèle de Markov caché (HMM) | ✗ pour l'instant | Pas encore testé. Le filtre actuel (BTC au-dessus de ses moyennes de fond) est simple et validé. Un HMM ajoute des paramètres à estimer, donc un risque de sur-apprentissage : il faudra le tester sur des données jamais vues avant de l'adopter. |
| Taille de position par critère de Kelly | ✗ | Kelly exige de connaître précisément l'avantage. Une petite erreur d'estimation conduit à risquer beaucoup trop. Le risque fixe de 0,5 % par trade est plus sûr. |
| Trader l'écart entre l'annonce et la prévision (« surprise ») | ✗ | Les valeurs attendues sont affichées. Trader la surprise demande d'être plus rapide que les algorithmes professionnels, ce qui est impossible ici. |
| Acheter juste après une cascade de liquidations | ✗ | Non prouvé. Le bouclier fait l'inverse : il interdit d'entrer pendant la cascade. |
| Coupure quotidienne, validation glissante (walk-forward), entraînement en argent fictif | ✓ déjà présents | Pertes maximales par jour et par semaine, arrêt complet sur baisse maximale, validation glissante purgée, IA d'entraînement en argent fictif |

---

## 3. La stratégie « croisement EMA 9 / 21 en 1 minute »

Gemini proposait un « moteur de tendance » : acheter quand la moyenne courte (9) croise au-dessus de la longue (21), sur des bougies d'une minute. Elle **n'a pas été ajoutée**, car elle a été **mesurée** sur 30 jours de données réelles (Binance, novembre 2026). Le signal est pris à la clôture et exécuté à l'ouverture suivante.

| Unité de temps | Trades en 30 jours (BTC) | Trades gagnants | Résultat sans frais | Résultat avec frais de 0,1 % | Simple détention |
|----------------|--------------------------|-----------------|---------------------|------------------------------|------------------|
| 1 minute | 1 011 | 28 % | −6,6 % | **−87,6 %** | −9,7 % |
| 5 minutes | 216 | 25 % | −9,1 % | −41,0 % | −9,7 % |
| 15 minutes | 65 | 28 % | −2,9 % | −14,8 % | −9,7 % |

L'ETH et le SOL donnent le même résultat : **−81 % à −89 % en un mois** en 1 minute, une fois les frais payés.

En une minute, le prix bouge surtout au hasard. Environ 1 000 allers-retours par mois coûtent à eux seuls bien plus que tout gain possible. Les stratégies de Sentinelle, déjà testées sur des années, font l'inverse : **peu de trades**, en 4 h, avec un objectif bien supérieur aux frais.

La partie technique de la demande (ccxt asynchrone, pandas_ta) n'a pas été reprise non plus. Sentinelle utilise déjà ccxt et calcule ses propres indicateurs, vérifiés par des tests. pandas_ta n'est plus publié qu'en version bêta (0.4), qui impose numba 0.61.2 et donc une version de numpy antérieure à 2.3. L'ajouter obligerait à revenir à d'anciennes versions de bibliothèques dont Sentinelle dépend, sans aucun gain.
