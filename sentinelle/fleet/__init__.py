"""Flotte d'agents autonomes (mission 57) : douze spécialistes qui RÉUTILISENT les fonctions existantes, un coordinateur local.

Garanties :
- aucun agent ne passe d'ordre, ne signe, ne réentraîne un modèle ni ne promeut quoi que ce soit ; une décision de la
  flotte est au mieux une PROPOSITION SIMULÉE, qui doit encore franchir le moteur de veto indépendant du hub
  (``sentinelle.dmh.security``) : le Risk Commander ajoute des restrictions, il ne remplace jamais ce moteur ;
- exécution finie : un cycle traite des événements et s'arrête ; aucun processus permanent ;
- budgets de calcul (temps processeur, mémoire, modèles simultanés, GPU) ; analyse lourde seulement sur un
  événement pertinent ; agent en échec mis en quarantaine ; agent inutile désactivé pour la session (réversible) ;
- chaque décision est archivée dans un journal chaîné, avec les constats, les désaccords et la contribution de chacun.
"""
CAN_TRADE = False
AUTO_RETRAIN = False
AUTO_PROMOTION = False
