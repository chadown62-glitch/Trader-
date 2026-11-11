# Validation de cours FX — composant isolé

Ce composant ne lit aucun réseau, ne s'installe pas dans le portefeuille et ne
passe aucun ordre. Le blocage des paires non EUR reste en place. Il s'agit d'un
socle de calcul, pas d'une conversion de fonds opérationnelle.

Il accepte explicitement EUR, USD, USDC et USDT, une fourchette bid/ask, une
source descriptive et une date par paire. Vendre la devise de base utilise le
bid ; l'inverse utilise 1/ask. Une conversion indirecte exige un intermédiaire
explicite, contrôle l'âge de chaque cours et applique les frais à chaque étape.
Aucune parité automatique n'est supposée. Les valeurs sont des Decimal et les
cours manquants, périmés, futurs ou incohérents provoquent un refus.

La provenance n'est qu'une chaîne fournie par l'appelant : son authenticité,
la fiabilité du fournisseur et l'horodatage effectif doivent être établis par
un futur adaptateur de données publiques. Il faut encore définir les règles
d'arrondi propres au marché, la persistance des cours et des frais, l'intégration
transactionnelle au portefeuille, les réservations et la comptabilité lors des
ventes. Les tests utilisent exclusivement des taux fictifs.
