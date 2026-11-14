"""ALPHA SHADOW — preuve prospective à candidat unique (mission #41).

Ce sous-paquet ne sert qu'à PROUVER (ou réfuter) une seule hypothèse : la politique figée
RegimeRouter-R1 améliore-t-elle le résultat net d'un portefeuille fictif de 30 € ?

Garanties de conception, vérifiées par ``tests/test_alpha_shadow_*.py`` :

- aucune capacité d'ordre, aucun réseau, aucun service : fonctions pures qui consomment des
  observations ``as_of`` déjà collectées par un autre composant (contrat indépendant) ;
- strictement en ligne : une décision à la clôture d'une bougie ne voit que les bougies déjà
  admises ; aucun objet du moteur ne contient une bougie future ;
- chaque décision (y compris les refus et les abstentions) est écrite dans un registre chaîné
  AVANT le prix suivant ; les résultats viennent d'une simulation causale indépendante, par bras,
  jamais d'un « gain évité » ;
- toute anomalie de données (retard, trou, doublon, révision, bougie future ou incohérente)
  conduit à un refus compté dans la couverture, jamais à une observation inventée ;
- ``shadow_only`` : distinct des protocoles gelés v2 / v3, qu'il ne lit ni ne modifie.
"""

CONTRACT = "alpha_shadow_contract_v1"
SHADOW_ONLY = True
CAN_TRADE = False
