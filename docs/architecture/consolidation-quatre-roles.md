# Consolidation des douze agents en quatre rôles actifs — préparation

Décision du propriétaire du 25 novembre 2026 (`SPEC.md`, phase OMEGA) : préparer la consolidation architecturale des 12 agents en quatre rôles actifs, **sans supprimer les protections existantes**. Ce document et `sentinelle/fleet/roles.py` sont une préparation : la flotte, le coordinateur et l'ordre de décision ne changent pas.

## État actuel (M57, M58)

- `sentinelle/fleet/agents.py` déclare douze agents. Aucun n'a de pouvoir d'ordre : `CAN_TRADE`, `AUTO_RETRAIN` et `AUTO_PROMOTION` valent `False` et sont vérifiés par le coordinateur.
- `sentinelle/fleet/coordinator.py` consulte, pour chaque événement, les agents concernés, dans des budgets de temps, de mémoire et de modèles (un seul modèle lourd à la fois).
- Cinq agents sont **essentiels** (`ESSENTIAL`), donc jamais sautés ni désactivés : System Doctor, Market Sentinel, Rug Pull Guardian, Risk Commander et Exit Guardian.
- **Ordre de décision fixé d'avance**, le plus prudent l'emportant :
  1. alerte bloquante → abstention ;
  2. règle de sortie → sortie proposée ;
  3. veto anti-arnaque ;
  4. refus du risque ;
  5. absence de signal → attendre ;
  6. objection → attendre ;
  7. sinon, proposition simulée.
- **Point ouvert depuis M58** : la règle « quatre agents actifs au plus » visait-elle les agents consultés ou les rôles ? La préparation ci-dessous propose de l'appliquer aux **rôles**. Chaque rôle reste composé de spécialistes déterministes et légers.

## Correspondance proposée

| Rôle | Agents | Responsabilité | Protections portées |
|---|---|---|---|
| **Veille** | System Doctor, Market Sentinel, Regime Detector, News & Event Analyst | Santé du système, fraîcheur et cohérence des données, régime de marché, dépêches | Alerte bloquante → abstention (System Doctor, Market Sentinel) |
| **Signal** | Alpha Hunter, Challenger Lab | Signaux d'entrée ; état des protocoles de recherche, en lecture seule | Aucune (le signal ne peut qu'être bloqué) |
| **Garde** | Rug Pull Guardian, Risk Commander, Exit Guardian | Veto anti-arnaque, limites du portefeuille fictif, sorties | Veto, refus du risque, sorties jamais bloquées |
| **Exécution** | Liquidity Scout, Cost Router, Portfolio Optimizer | Profondeur, coût complet, taille compatible avec le risque et le minimum d'ordre | Objections → attendre |

Le seul agent lourd, News & Event Analyst, reste dans la veille. Il est appelé seulement si l'événement est pertinent, et jamais en même temps qu'un autre modèle.

## Invariants non négociables

1. Aucun drapeau de capacité ne change : `CAN_TRADE`, `AUTO_RETRAIN` et `AUTO_PROMOTION` restent faux.
2. L'ordre de décision du coordinateur est conservé à l'identique.
3. Les cinq protections restent essentielles : jamais sautées par un budget, jamais désactivées.
4. Le Guardian (`tokenrisk.assess_candidate`) et le Risk Commander ne sont jamais assouplis.
5. Le journal en ajout seul consigne toujours chaque constat, chaque désaccord et la contribution de chaque agent.

`roles.check()` vérifie ces points pour le rangement : un rôle par agent, aucun rôle vide, et des protections situées dans la veille ou la garde et déclarées essentielles. Les tests le contrôlent.

## Étapes, chacune soumise au propriétaire

1. **Vue par rôle (cette préparation).** `roles.by_role()` présente une décision par rôle sans jamais la recalculer, et désigne les rôles décisifs : ceux dont le retrait changerait la décision. `roles.role_report()` cumule le coût (temps, mémoire) et l'apport de chaque rôle à partir des statistiques du coordinateur.
2. **Mesure.** Rejouer les événements consignés de la session fictive memecoin, puis publier le coût et l'apport de chaque rôle. Aucun agent n'est retiré sur cette base.
3. **Proposition de code.** Créer quatre classes de rôle qui appellent leurs spécialistes dans l'ordre actuel. Un test d'équivalence exige des décisions identiques à celles de la flotte de douze agents, sur les événements consignés et sur les cas de `tests/test_fleet.py`.
4. **Validation humaine**, puis affichage par rôle dans le cockpit. Les spécialistes restent dans le code tant que le propriétaire n'a pas décidé.

## Constats de la préparation

- Sur les cas de `tests/test_fleet.py`, un veto ou un refus désigne la garde comme rôle décisif. Un impact excessif désigne l'exécution, une donnée périmée la veille.
- Quand aucun signal n'existe, **aucun** constat isolé n'est décisif : l'attente ne dépend d'aucun agent en particulier. La mesure d'apport par retrait ne suffit donc pas à juger l'utilité du rôle « signal ».

## Ce qui n'est pas fait

- Aucun agent n'est supprimé, fusionné ou désactivé.
- Le coordinateur et le cockpit ne sont pas modifiés.
- Le point ouvert sur l'interprétation de la règle « quatre agents actifs » reste à trancher par le propriétaire.
