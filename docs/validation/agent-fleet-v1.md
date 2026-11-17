# Flotte d'agents autonomes — mission 57

> **Simulation.** Aucun agent ne passe d'ordre, ne signe, ne réentraîne un modèle ni ne promeut quoi que ce soit. Une décision
> de la flotte est au mieux une **proposition simulée** : elle doit encore franchir le moteur de veto indépendant du hub
> (`sentinelle.dmh.security`), que le Risk Commander ne remplace jamais.

## 1. Les douze agents (code : `sentinelle/fleet/agents.py`)

| Agent | Rôle | Réutilise |
|---|---|---|
| System Doctor | santé : sources, disque, journal, horloge | statistiques de sources (M56), lecture du journal chaîné (#48) |
| Market Sentinel | fraîcheur et cohérence des données | horodatages et empreintes des collectes |
| Regime Detector | tendance et volatilité du BTC (veille) | règle des régimes de M55 |
| Alpha Hunter | signaux des règles figées | règles du module C (M56), `mom_ts_28` (M54) |
| Rug Pull Guardian | veto anti-arnaque | `sentinelle.memecoin.guardian` (M56) |
| Liquidity Scout | impact de prix d'un ordre | modèle à produit constant (M56), carnets du hub (#51) |
| Cost Router | coût complet d'un aller-retour | coûts DEX (M56), routeur du hub (#51/#52) |
| Risk Commander | limites du portefeuille fictif (restrictions seulement) | limites du hub (#52) |
| Exit Guardian | règles de sortie, effondrement | sorties du module C (M56) |
| News & Event Analyst | dépêches ; modèle local seulement si pertinent | `sentinelle.news`, IA locale |
| Portfolio Optimizer | taille compatible avec le risque et le minimum d'ordre | logique de taille de M55, minimum du hub |
| Challenger Lab | état des protocoles (lecture seule) | registre du laboratoire, protocole prospectif de M55 |

## 2. Coordinateur (code : `sentinelle/fleet/coordinator.py`)

- **Choix des agents** selon la tâche : candidat memecoin, ordre CEX, position, marché, santé, recherche. Les agents
  essentiels passent d'abord ; les analyses lourdes passent en dernier, et seulement si l'événement est pertinent
  (seuil 0,5).
- **Budgets** : temps processeur par événement (au-delà, les agents non essentiels sont sautés), mémoire déclarée,
  **un modèle local à la fois**, GPU déclaré.
- **Pannes** : un agent qui lève une erreur rend une abstention, sans arrêter la flotte. Il passe en quarantaine après
  3 échecs, et peut être réactivé (`enable`).
- **Agents inutiles** : un agent non essentiel, coûteux et jamais décisif est désactivé pour la session. C'est
  réversible et consigné. Un agent bon marché reste actif.
- **Ordre de décision fixé d'avance** :
  1. alerte bloquante → abstention ;
  2. sortie de position, qui n'est jamais bloquée ;
  3. veto ;
  4. refus du risque ;
  5. pas de signal → attendre ;
  6. une objection → attendre, en consignant le désaccord ;
  7. sinon, proposition simulée.
- **Archivage** : chaque décision est écrite dans un journal chaîné en ajout seul. Elle garde les constats, les
  désaccords, la **contribution** de chaque agent (la décision changerait-elle sans lui ?), les agents sautés et
  leurs motifs, et le temps processeur et la mémoire de chaque agent.
- **Exécution finie** : `Coordinator.run(événements)` traite une liste puis s'arrête. Il n'y a ni boucle ni service.

## 3. Rejeu sur une capture réelle (M56)

`python -m tools.fleet replay --capture data/memecoin/capture-1794908298.jsonl.gz --output DOSSIER`. La capture
fait 64 nouveaux pools Solana, dont 60 contrôlés, empreinte `9467c3e2…`. Chaque token est présenté à l'instant de son
contrôle, avec les seules bougies déjà closes.

| Décision | Nombre |
|---|---|
| VETO | 39 |
| INFO (bilan de santé) | 1 |
| Proposition simulée | 0 |

- Le Rug Pull Guardian a été décisif 39 fois sur 39.
- Aucun agent n'a échoué.
- Le rejeu complet prend 0,09 s ; aucun agent ne dépasse quelques millisecondes.

Tableau complet : `docs/validation/mesures/agent-fleet-v1/fleet-report.md`.

Deux défauts constatés sur ce rejeu ont été corrigés avant publication :
- **Désactivation à tort** : la règle désactivait l'Alpha Hunter pour « jamais décisif », alors que c'était seulement
  parce que le veto bloquait tout. Elle ne vise plus que les agents coûteux.
- **Coût croissant** : le System Doctor relisait tout le journal à chaque événement, un coût qui grandit avec le
  journal (440 ms sur ce rejeu). Il ne le contrôle plus qu'aux bilans de santé (0,8 ms).

## 4. Tests

`tests/test_fleet.py`, sur données synthétiques, vérifie :
- l'absence de tout chemin d'exécution, de réentraînement et de promotion (analyse du code) ;
- qu'une proposition reste soumise au veto indépendant, et que le veto l'emporte sur un signal ;
- le refus d'un petit ordre ;
- la sortie proposée lors d'un effondrement, même sous refus du risque ;
- l'abstention sur des données périmées ou un journal altéré ;
- l'analyse lourde seulement si pertinente, dans la limite des modèles simultanés ;
- la quarantaine ;
- la désactivation réversible ;
- le budget de calcul ;
- le constat de M55 retrouvé par le Portfolio Optimizer : 76 € nécessaires pour tenir la limite de risque.

## 5. Limites

- La flotte n'est pas encore branchée sur l'interface ni sur le service du hub (mission 58).
- Le News & Event Analyst n'utilise le modèle local que s'il est fourni. Sans lui, il s'abstient, sans jamais faire
  d'appel distant.
- Les seuils des agents sont des règles prudentes fixées d'avance, non optimisées.
