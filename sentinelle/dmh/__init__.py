"""DIRECT MARKET HUB (mission 51) — connexion publique aux marchés crypto et exécution FICTIVE optimisée.

Ce paquet ne peut, par construction, ni signer, ni diffuser, ni envoyer un ordre réel :
- aucune clé, aucune phrase de récupération, aucun portefeuille, aucune permission de dépense ;
- le client réseau n'accepte que des lectures publiques listées une à une (`net.py`) ;
- toute demande non simulée est refusée par `guard.py` (refus explicite, journalisé, code 403 dans l'API) ;
- le broker réel historique (`sentinelle.execution.live`) n'est jamais importé ici (test statique).

Couches, strictement séparées (voir docs/architecture/direct-market-hub-v1.md) :
1. données publiques (`net`, `cex`, `dex`, `observe`, `tokens`) ;
2. préparation de stratégie (`strategy`, `llm`) ;
3. recherche de routes (`router`) ;
4. coûts et risque (`costs`, `security`) ;
5. validation de l'ordre fictif (`security`) ;
6. simulation d'exécution (`simulate`) ;
7. réconciliation (`ledger`) ;
8. affichage et audit (`service`, `api`, panel `web/js/views/dmh.js`).
"""

VERSION = "dmh-1.0.0"
ARCHITECTURE = "direct-market-hub-v1"
PAPER_ONLY = True
CAN_SIGN = False
CAN_BROADCAST = False
LABEL = "SIMULATION — ARGENT FICTIF"
