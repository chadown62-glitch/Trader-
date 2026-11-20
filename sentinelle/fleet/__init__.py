"""Flotte d'agents (M57, intégrée à la session memecoin par M58) : douze spécialistes et un coordinateur local.

Garanties :
- aucun agent ne passe d'ordre, ne signe, ne réentraîne un modèle ni ne promeut quoi que ce soit ; une décision de la
  flotte est au mieux une PROPOSITION SIMULÉE. Dans la session memecoin, elle n'autorise qu'une entrée FICTIVE, déjà
  passée par le veto anti-arnaque (``tokenrisk.assess_candidate``) ; le veto, le risque et les alertes l'emportent ;
- exécution finie : un cycle traite des événements et s'arrête ; aucun processus permanent ;
- budgets de calcul (temps par événement, mémoire, modèles simultanés, GPU) ; analyse lourde seulement sur un
  événement pertinent ; agent en échec mis en quarantaine ; agent inutile désactivé pour la session (réversible) ;
- chaque décision garde les constats, les désaccords et la contribution de chaque agent.
"""
CAN_TRADE = False
AUTO_RETRAIN = False
AUTO_PROMOTION = False
