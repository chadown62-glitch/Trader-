"""COST OPTIMIZER — moteur de coûts et d'optimisation d'exécution (hors ligne, recherche, aucune promotion).

Ce paquet MESURE ce que coûte une exécution fictive et compare des façons d'exécuter. Il ne passe
aucun ordre, n'ouvre aucune connexion et ne modifie ni le courtier fictif, ni le portefeuille, ni les
protocoles gelés. Il ne lit que des données publiques déjà collectées par ``tools.cost_fetch``.

Règles de preuve (chaque valeur porte son niveau, voir ``evidence``) :

- OBSERVED : lu dans une réponse publique datée et hachée (filtres de marché, carnet, transactions) ;
- ESTIMATED : calculé à partir de données observées par une méthode déclarée, avec son incertitude ;
- DECLARED : publié par la plateforme (grille tarifaire) et archivé, mais pas observé sur une exécution ;
- HYPOTHETICAL : scénario, quand aucune donnée n'existe ; jamais présenté comme une mesure.

Principes :

- une économie n'existe que si l'ordre est exécuté : un ordre à cours limité non exécuté ne
  « gagne » rien, il se termine soit sans position (coût d'opportunité mesuré), soit au marché ;
- les bornes prudente et optimiste d'une règle de remplissage sont rapportées ensemble ;
- réduire les coûts ne crée pas d'avantage : sans avantage brut, le résultat net reste négatif ;
- le modèle de coûts est figé (empreinte) avant toute évaluation de stratégie ; il n'est jamais
  calibré sur les résultats d'ALPHA FACTORY v2.
"""

CAN_TRADE = False
STATUS = "RESEARCH"
CONTRACT = "cost_contract_v1"
