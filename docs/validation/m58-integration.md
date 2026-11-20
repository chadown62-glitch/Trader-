# M58 — Intégration, interface et pivot liquidité (Sprint 5)

> **Simulation.** Aucun ordre réel, aucune clé, aucun portefeuille, aucune promotion automatique. Les cotations Jupiter
> sont des devis sans portefeuille : rien n'est construit, signé ni diffusé. Tout reste dans `asset_domain="memecoin"`.

## 1. Pourquoi ce pivot

La mission M56 (PR #70) a mesuré le flux des nouveaux pools Solana au moment de leur détection :

- 127 pools contrôlés : liquidité médiane de 1 601 $ ;
- 42 pools (un tiers) sans aucune route de revente ;
- 82 pools sans aucun échange après la décision ;
- aucun pool n'a passé le veto.

Chercher des entrées parmi des pools nés depuis quelques minutes ne mène donc à rien. M58 part des **pools établis**.

## 2. Pools établis (`sentinelle/dmh/established_pools.py`)

Les candidats viennent des pools Solana en tendance (GeckoTerminal, une requête par passage). Chaque pool passe ces
critères dans l'ordre, du moins coûteux au plus coûteux. Le premier critère manqué arrête l'examen et devient le motif
consigné :

| Étape | Critère | Requêtes |
|---|---|---|
| liquidité | au moins 20 000 $ | aucune |
| ancienneté | pool d'au moins une heure | aucune |
| activité | dernière heure : au moins 5 000 $ de volume, 10 achats et 10 ventes | aucune |
| revente | aller-retour Jupiter de 0,05 SOL (achat puis revente), coté sans portefeuille : au plus 10 % de perte | 2 |
| veto | `tokenrisk.assess_candidate`, toujours en dernier mot | 3 à 4 |

Règles de fonctionnement :
- **Donnée absente = refus.** Une date de création inconnue, un devis manquant ou une revente impossible font refuser
  le pool.
- **Contrôles bornés.** Un passage examine à fond 4 pools au plus.
- **Délai de six heures.** Un token déjà décidé n'est pas recontrôlé avant six heures.
- **Les décisions sont consignées** avec l'agent `established_pools`.

La session fictive (`memecoin_simulator.tick`) tire désormais ses candidats de ces pools. La cotation suit l'adresse
exacte de la paire, sur PumpSwap, Raydium, Meteora ou Orca. Les positions déjà ouvertes gardent leur gestion.

**Conséquence assumée :** la stratégie *Sniper* (pool de moins de 3 minutes) ne se déclenche plus. Les mesures de M56
montrent que ces pools ne sont pas négociables.

## 3. Un veto qui peut enfin répondre (`sentinelle/dmh/tokenrisk.py`)

Deux défauts empêchaient le veto de `main` d'accepter quoi que ce soit, même un token sain :

1. **Concentration illisible.** Le RPC Solana public refuse la lecture des plus gros comptes : HTTP 403 sur un nœud,
   429 avec de longues attentes sur l'autre. Le veto refusait donc tous les tokens, faute de données. Il lit
   maintenant les détenteurs réels dans le rapport Rugcheck, hors pools et verrous connus, avec les seuils de M56 :
   10 premiers ≤ 35 % et premier ≤ 15 %. La chaîne sert de repli. Si les deux sources échouent, le veto refuse.
2. **Token-2022 refusé en bloc.** Tous les tokens pump.fun sont des Token-2022, avec des métadonnées pour seules
   extensions. Ils sont désormais admis si leurs seules extensions sont descriptives. Toute autre extension reste un
   veto : frais ou crochet de transfert, délégué permanent, état gelé par défaut, transfert suspendable, etc.

Les signalements Rugcheck (niveaux « danger » et « warn ») et les autorités d'émission et de gel restent des veto,
sans changement.

**Mesure réelle**, le 20 novembre 2026, sur les pools en tendance (premier passage, base vide) :

- 16 pools vus et 4 examinés à fond : 3 retenus et 1 refusé par le veto (« Mutable metadata ») ;
- aucun refus aux trois premiers filtres : les pools en tendance sont tous liquides, anciens et actifs ;
- 21 requêtes et 18,6 s pour le passage, soit moins de 5 s par pool examiné, contre environ 30 s quand le veto
  attendait le RPC public (247 s pour 8 pools) ;
- second passage, 1 minute plus tard : 4 tokens en délai de six heures, 4 nouveaux examinés, dont 1 retenu et 3 refusés
  par le veto.

Dans une mesure précédente, 8 pools examinés à fond ont donné 4 retenus et 4 refusés pour de vrais dangers :
- Token-2022 avec délégué permanent et transferts suspendables ;
- 10 premiers détenteurs à 41 % et à 70 % ;
- métadonnées modifiables.

C'est un instantané. **Il ne mesure aucune rentabilité.**

## 4. La flotte d'agents dans la session (`sentinelle/fleet/`)

Les douze agents de M57 ont été adaptés à la chaîne de `main`. Avant chaque entrée fictive, la session transmet à la
flotte :
- les signaux des stratégies ;
- le verdict du veto ;
- l'aller-retour Jupiter mesuré à la sélection du pool ;
- l'impact et le coût d'aller-retour, calculés avec le modèle du simulateur ;
- l'état de la session : positions et perte.

L'ordre de décision est fixé d'avance :
1. une alerte donne une abstention ;
2. un veto bloque l'entrée ;
3. un refus du risque bloque l'entrée ;
4. sans signal, la flotte attend ;
5. une objection fait attendre ;
6. sinon, la flotte propose une entrée fictive.

**Une entrée fictive n'a lieu que sur cette proposition.** Les refus et objections propres à la flotte :

| Agent | Bloque l'entrée si… |
|---|---|
| Rug Pull Guardian | veto refusé, ou aller-retour mesuré au-delà de 10 % ; revente non confirmée = attente |
| Liquidity Scout | impact estimé au-delà de 2 % |
| Cost Router | coût d'aller-retour estimé au-delà de 10 % |
| Risk Commander | aucune taille possible, 12 positions ouvertes, ou perte de session au-delà de 50 % |
| Market Sentinel | donnée sans date, périmée (plus de 15 min) ou datée du futur |

Les sorties ne passent jamais par un blocage : la politique calibrée (#69) continue de s'appliquer. Les 50 dernières
décisions de la flotte, avec leurs motifs et les agents décisifs, sont gardées dans la session. Les budgets de calcul se
mesurent en temps réel, car l'horloge processeur de Windows avance par pas de 15,6 ms.

## 5. Cockpit Memecoin

La carte « Pools établis & flotte d'agents » couvre les dernières 24 h :
- pools vus, refusés par motif et retenus ;
- liquidité médiane ;
- critères en clair ;
- candidats retenus, avec leur liquidité, leur volume sur 1 h et leur aller-retour mesuré ;
- dernières décisions de la flotte.

Elle lit `GET /api/dmh/memecoin/funnel`, une route authentifiée en lecture seule, et se rafraîchit à chaque cycle de
session.

## 6. Découpage en PR (6 fichiers au plus chacune)

| PR | Contenu | État |
|---|---|---|
| #74 Pivot | `SPEC.md`, `net.py`, `tokenrisk.py`, `established_pools.py`, `memecoin_simulator.py`, `tests/test_memecoin_engine.py` | fusionnée, CI verte (Ubuntu, Windows, Chromium) |
| #75 Flotte | `sentinelle/fleet/` (3 fichiers), `memecoin_simulator.py`, `tests/test_fleet.py` | fusionnée, CI verte |
| #76 Cockpit | `api.py`, `memecoin.js`, `tests/e2e/memecoin_check.js`, `tests/test_memecoin_cockpit.py`, `tests/test_dmh_concurrency.py`, ce document | ce document |

Prérequis fusionnés le même jour : #69 (sorties calibrées et environnement de CI), #68 (radar Solana), #73 (gardes réseau
des tests de sécurité : seule la boucle locale passe, ce qui rend la CI Windows exécutable). Sur #73, la CI Windows a
donné 1122 réussis, 0 échec, 0 erreur.

## 7. Limites

- Les seuils sont des règles prudentes fixées d'avance, pas des valeurs optimisées.
- Le modèle d'impact du simulateur, à produit constant, est approximatif pour les pools à liquidité concentrée
  (Raydium CLMM, Meteora DLMM). L'aller-retour Jupiter réel sert de garde-fou.
- Rugcheck devient la source principale de la concentration : c'est un service tiers. Son absence fait refuser.
- **Aucun avantage de rendement n'est démontré.** Les pools établis sont plus sûrs et revendables, ce qui ne les rend
  pas rentables. Une évaluation demanderait un protocole préenregistré et des données fraîches.

## 8. Vérifier sous Windows 11 (PowerShell, racine du dépôt)

```powershell
py -3.12 -m pytest tests/test_memecoin_engine.py tests/test_fleet.py tests/test_memecoin_cockpit.py -q
py -3.12 -m sentinelle --demo --no-browser
```

Ouvrez l'adresse locale indiquée dans Edge, connectez-vous, puis sélectionnez Memecoin. La carte « Pools établis &
flotte d'agents » apparaît sous la courbe du capital. Après un « Cycle manuel », l'entonnoir et les décisions de la
flotte se remplissent, selon la disponibilité des sources publiques.

Ces commandes ont été testées sous Linux avec Python 3.12. Leur passage sous Windows dépend de la CI Windows.
