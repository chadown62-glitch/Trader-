"""ALPHA FACTORY v1 — laboratoire de recherche hors ligne (mission #38).

Académie de stratégies enseignantes, conseil multi-agents « shadow », banc de vitesse
d'apprentissage et tournoi hors ligne. Garanties de conception, vérifiées par les tests :

- AUCUNE capacité d'ordre : aucun module de ce paquet n'importe le courtier, les services,
  l'API ou le portefeuille de l'application ; les plans produits sont des objets inertes ;
- aucune connexion réseau, aucun service permanent, aucune écriture hors du dossier de
  sortie demandé explicitement ;
- données « as_of » uniquement : chaque décision à la clôture d'une bougie n'utilise que
  les bougies déjà closes ; exécution au premier prix suivant (contrat du projet) ;
- capital simulé : 30 € fictifs, frais, demi-écart et glissement inclus ;
- aucune promotion : les meilleurs candidats restent des candidats ; les protocoles gelés
  v2 / v3 ne sont ni lus pour régler quoi que ce soit, ni modifiés.

API stable (pour l'intégration future, voir docs/validation/alpha-factory-v1.md) :
``catalogue.load_catalogue``, ``bars.Universe``, ``teachers.TEACHERS``, ``execution.CostModel``,
``execution.simulate_trade``, ``execution.run_portfolio``, ``council.ShadowCouncil``,
``ledger.ShadowLedger``, ``velocity.run_challenge``, ``tournament.run_tournament``.
"""

CAN_TRADE = False
CAPITAL_EUR = 30.0
