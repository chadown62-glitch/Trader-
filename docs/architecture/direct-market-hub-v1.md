# DIRECT MARKET HUB v1 — audit de l'existant et architecture (mission 51)

Version d'architecture `direct-market-hub-v1`, code `sentinelle/dmh/` (version `dmh-1.0.0`). Branche
`claude/direct-market-hub-v1`, empilée sur COST OPTIMIZER (#50) pour réutiliser son moteur et ses mesures.

**SIMULATION — ARGENT FICTIF.** Le hub lit des données publiques réelles et simule l'exécution. Il ne peut ni
signer, ni diffuser une transaction, ni envoyer un ordre réel : il n'existe dans ce paquet ni clé, ni phrase de
récupération, ni portefeuille, ni permission de dépense, et le client réseau refuse tout point d'accès privé.

## 1. Audit de l'existant

| Composant | Où | Constat | Réutilisation |
|---|---|---|---|
| Verrou « 100 % simulation » | `sentinelle/safety.py` | refus inconditionnels (`forbid`), journalisés, 403 dans l'API | réutilisé tel quel par le hub (`guard.py`) |
| Ancien broker réel | `sentinelle/execution/live.py` | `LiveBroker` est une butée : impossible à construire, chaque méthode refuse | **jamais importé** par le hub (test statique) |
| Client CCXT public | `sentinelle/market/sources.py` | façade `PublicExchange` sans identifiants, Binance seulement | non utilisé : le hub a son propre client public à liste blanche, plus strict (aucun appel générique) |
| Broker fictif et portefeuille de 30 € | `execution/paper.py`, `services/portfolio.py` | un seul compte principal en euros ; soldes jamais négatifs ; grand livre | **les euros du hub sont ceux de ce compte** (`MainAccountCash`) ; deux crochets ajoutés au broker (réservations et valeur du hub), sans effet quand le hub est absent |
| Carnets et liquidité | `execution/liquidity.py`, `book_execution.py` | parcours de carnet en preneur, données datées | principes repris (parcours, refus si périmé) dans `router.walk` |
| Change EUR/USDC | `market/fx.py`, `execution/fx_ledger.py` | taux daté, refus sans taux frais | le hub n'en a pas besoin pour les paires en euros ; les conversions sont des étapes chiffrées de la route |
| COST OPTIMIZER (#50) | `sentinelle/costs/` | niveaux de preuve, mesures 2020-2026, contrat figé | `Value`, `hypothesis`, `weakest` réutilisés ; a priori de glissement, écarts de tension et ordres passifs repris des mesures publiées |
| Multi-marchés (#47) | branche `architecture/c00-market-specialists-v1` | contrats `AssetRef` (identité = famille, lieu, réseau, identifiant), `Evidence` horodatée UTC | lignée différente : **non fusionnée** ; le hub suit les mêmes principes (identité ≠ symbole, heures UTC en ms, aucune information future) ; adaptateur prévu, non branché |
| Mission Control (#46) | branche `work/mission-control-v1` | observatoire, pré-vol Windows | lignée différente : non fusionnée ; l'observatoire du hub est autonome |
| Panel | `web/js/*` | Vue sans compilation, une vue par page | nouvelle vue `web/js/views/dmh.js`, entrée « Hub de marché » |

## 2. Architecture : huit couches strictement séparées

| Couche | Modules | Peut | Ne peut pas |
|---|---|---|---|
| 1. Données publiques | `net`, `cex`, `dex`, `observe`, `tokens`, `view` | lire des carnets, règles, transactions publiques, devis non signés, frais de réseau | écrire, signer, s'authentifier |
| 2. Stratégie | `llm`, formulaire du panel | proposer un brouillon (IA locale) | exécuter, changer les limites, passer en réel |
| 3. Routes | `router.build_routes` | énumérer et chiffrer les routes aller-retour | choisir une route sans sortie |
| 4. Coûts et risque | `router` (`Cost`, `Leg`, `Route`), `assumptions`, `tokenrisk` | composantes avec niveau de preuve et fourchette | présenter une hypothèse comme une mesure |
| 5. Validation | `security` | veto absolu (financier, tokens, technique) | être contourné par l'IA ou l'API (plafonds constants) |
| 6. Simulation | `simulate.PaperExecutor` | exécuter fictivement d'après des données datées | remplir sans preuve (contact du prix, devis périmé) |
| 7. Réconciliation | `ledger` | réserver, régler, journaliser, réconcilier, reprendre | engager deux fois, dépasser le capital, vendre à découvert |
| 8. Affichage et audit | `service`, `api`, `web/js/views/dmh.js` | montrer, demander une confirmation explicite, arrêter | démarrer un service permanent |

### Chaîne d'une opération fictive

1. **Prévisualisation** : routes chiffrées (aller ET retour), choix sur le résultat net attendu d'un avantage brut
   *annoncé* (hypothèse), ou ABSTENTION ; la route retenue est relue juste avant le contrôle de sécurité.
2. **Confirmation** explicite, avec un identifiant de demande unique : la rejouer renvoie le même résultat.
3. **Nouveau contrôle à l'exécution** : données relues, route recalculée, dérive bornée, réglages inchangés
   (empreinte), verdict de sécurité refait.
4. **Réservation atomique** (transaction `BEGIN IMMEDIATE`, partagée avec le broker fictif existant).
5. **Simulation**, puis **règlement atomique** ; pour une transaction sur la chaîne, une trace locale est
   écrite AVANT le règlement : après une panne, l'opération est réglée d'après cette trace, une seule fois.

### Verrous en refus par défaut

- `guard.inspect_request` : toute demande dont `mode` n'est pas exactement `paper`, ou qui contient un champ lié
  au réel (signature, diffusion, portefeuille, clé, levier, vente à découvert, emprunt, retrait, pont), ou un
  secret, est refusée (403) avant toute analyse ; le secret n'est jamais recopié dans le message.
- `net.PublicClient` : liste blanche d'hôtes, de chemins (expressions complètes) et de méthodes JSON-RPC de
  lecture ; les méthodes d'écriture et les points d'exécution (Jupiter `/execute`, chemins privés des plateformes)
  sont refusés comme des opérations réelles ; un paramètre de portefeuille (`taker`, `payer`…) aussi.
- `guard.check_runtime` : avant chaque simulation, vérifie les constantes et la classe exacte de l'exécuteur.
- Plafonds absolus (`security.ABSOLUTE`, `MappingProxyType`) : non modifiables ; une valeur trafiquée en base est
  ramenée au plafond (`effective`) ; une demande d'origine IA est refusée.
- Aucun fil, aucune tâche planifiée : tout est calculé à la demande ; l'arrêt d'urgence bloque toute nouvelle
  opération (état persistant, lu à chaque opération ; une lecture en échec vaut refus).

## 3. Connecteurs publics

| Source | Données | Remarques |
|---|---|---|
| Binance (`data-api.binance.vision`) | carnet, règles, transactions, heure | commission non publiée sans compte : hypothèse 0,10 % |
| Kraken | carnet, règles (`AssetPairs`), transactions, heure (à la seconde) | les grilles de frais ne sont plus publiques (tableaux vides) : hypothèse 0,25/0,40 % |
| Coinbase Exchange | carnet (heure de la source), règles, transactions, heure | paires USDC absentes (HTTP 404) ; hypothèse 0,60/1,20 % |
| Bitstamp | carnet (µs), règles, transactions | minimum 10 € ; hypothèse 0,30/0,40 % |
| Bitvavo | carnet (**heure en nanosecondes**, convertie), règles, transactions | l'unité a d'abord été refusée par le contrôle d'horloge, puis convertie explicitement ; hypothèse 0,15/0,25 % |
| Jupiter (Swap API V2, `GET /swap/v2/order` sans portefeuille) | devis non signé, route (places et tokens), frais de la plateforme | documentation officielle consultée le 16/11/2026 : `/swap/v1/quote` est « non maintenu » ; accès sans clé limité à 0,5 requête/s ; toute réponse contenant une transaction est refusée |
| Uniswap V3 (QuoterV2 par `eth_call`) | devis, palier de frais, estimation de gaz ; pool vérifié auprès de la fabrique officielle | adresses relevées dans la documentation officielle (Ethereum, Base) |
| Nœuds publics Ethereum, Base, Solana | frais (historique, priorité), dépôt de compte de token, décimales, autorités | Base : frais de publication sur Ethereum lus sur l'oracle du réseau |
| GoPlus, honeypot.is, RugCheck | signaux de risque des tokens | traités comme des signaux ; une source muette n'est jamais un feu vert |
| CoinGecko, liste « Uniswap Labs Default », liste vérifiée de Jupiter | confirmations d'identité des tokens | registre : deux confirmations indépendantes + lecture sur la chaîne |

Contrôle des données : heure UTC de réception (ms) et heure de la source quand elle existe ; carnet refusé s'il
est croisé, non trié, en double, non fini ou daté du futur ; fraîcheur bornée ; doublons, retours en arrière et
trous détectés ; consensus entre plateformes (une source éloignée est écartée ; deux sources contradictoires
rendent le prix inutilisable) ; un prix sur la chaîne éloigné de plus de 150 pb du consensus bloque la route ;
reprises bornées avec temporisation, respect du délai demandé par la source (`Retry-After`), coupe-circuit par
hôte, budget de requêtes. Une source indisponible est affichée INDISPONIBLE ; aucun prix n'est reconstruit.

## 4. Catalogue des incidents techniques

| Situation | Détection | Réaction | Journal | Reprise |
|---|---|---|---|---|
| Panne Internet ou nœud | exception réseau, délai | source INDISPONIBLE ; route impossible ; opération refusée | erreur par hôte (`health`) | à la demande suivante, après la fin du coupe-circuit |
| Données périmées | âge du carnet ou du devis > limite | veto `perime` / `devis_perime` | verdict | nouvelle prévisualisation |
| Horloges désynchronisées | heure de source dans le futur ; décalage mesuré face à Binance et Coinbase | donnée refusée ; veto `horloge` | verdict | corriger l'horloge du système |
| Réseau saturé, frais en hausse | gaz ou priorité du moment | veto `gaz` au-delà du budget | verdict | attendre ; budget réglable sous plafond |
| Limites de débit | HTTP 429 | attente du délai annoncé, puis coupe-circuit | erreur par hôte | automatique, borné |
| Délai dépassé | exception | reprise bornée (lecture seulement) puis INDISPONIBLE | erreur par hôte | demande suivante |
| Réponse malformée | parseurs stricts | `MalformedResponse` : rien n'est utilisé | erreur par hôte | demande suivante |
| Transaction simulée échouée | nouveau devis < minimum accepté | état FAILED, gaz payé | exécution « frais sans exécution » | nouvelle opération (jamais automatique) |
| Transaction apparaissant deux fois | identifiant de demande unique ; une trace par opération | la seconde renvoie la première | journal chaîné | — |
| Reconnexion après interruption | démarrage du service | `recover()` | `reprise_apres_arret` | automatique, sans nouvel essai |
| Redémarrage pendant une opération | état RESERVED sans trace / SUBMITTED avec trace | ABANDONNÉE (réservation libérée) / réglée d'après la trace | `reprise_apres_arret` | nouvelle demande si voulu |
| Erreur de quantité ou de décimales | pas de quantité, décimales du registre vérifiées sur la chaîne | arrondi au pas ; décimales différentes : token non sûr | verdict | — |
| Portefeuille désynchronisé | `reconcile()` | blocage de toute nouvelle opération | `desynchronisation` | réconciliation réussie |
| Réglages changés pendant la décision | empreinte des limites, hypothèses et registre | refus à l'exécution | refus | nouvelle prévisualisation |
| Concurrence entre stratégies | transactions sérialisées, opérations en cours par actif | refus de la seconde réservation ; veto `concurrence` | refus | — |
| Réorganisation de chaîne | `simulate_reorg` avant finalité | compensation (rien n'est effacé), gaz conservé | `reorganisation` | — |
| Attente de finalité | `final_ms` des positions | vente impossible avant finalité | — | automatique à finalité |
| Devis annulé ou expiré | âge, nouveau devis à l'exécution | refus | verdict | nouvelle prévisualisation |
| Protocole mis à jour ou disparu | pool absent de la fabrique, place inconnue sur la route | route impossible ou veto `contrat_inconnu` | verdict | revue du registre et des places connues |
| Perte de valorisation | prix indisponible | valeur prudente précédente (24 h), sinon coût d'achat, affichée avec sa date | — | relecture |

## 5. Ce qui n'est pas fait (et pourquoi)

- Pas de connexion aux branches #46 et #47 : lignées différentes, fusion non autorisée sans revue.
- Pas de ponts entre réseaux, pas de BTC « enveloppé » : risques de dépositaire et de pont, refus explicite.
- Pas d'exécution automatique par l'IA : elle propose un brouillon ; l'utilisateur confirme chaque opération.
- Pas de service permanent : ordres à cours limité et finalité mis à jour à la demande.
