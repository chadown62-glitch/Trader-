"""ALPHA FACTORY v2 — laboratoire de stratégies (hors ligne, exploratoire, aucune promotion).

But : chercher des stratégies dont l'avantage NET (frais, écart, glissement compris) est
démontrable, et non un meilleur score prédictif. Le laboratoire :

- n'étudie que des règles simples, tirées de la littérature, à paramètres fixés AVANT toute mesure
  (momentum, value, suivi de tendance, cassure, retour à la moyenne, spécificités crypto) ;
- simule un portefeuille fictif de 30 unités : achat seulement, jamais de vente à découvert, jamais
  de levier, minimum d'ordre de la plateforme ;
- décide à la clôture d'une journée avec les seules journées déjà closes, exécute à l'ouverture
  suivante (contrat d'exécution du projet) ;
- utilise un univers daté : à chaque date, seules les cryptos alors cotées et alors liquides,
  y compris celles retirées de la cote depuis (pas de biais du survivant par construction) ;
- compare chaque candidat à l'abstention, à la simple détention et à une détention de même
  exposition ; corrige les essais multiples ; exige la tenue sur deux périodes indépendantes ;
- conserve les résultats négatifs et explique les échecs.

Il réutilise l'infrastructure de preuve de la PR #48 (intervalles par blocs, puissance, journal
chaîné) sans modifier ni son candidat gelé ni son protocole. Aucun module n'importe le courtier,
le réseau, l'API, le superviseur ou le portefeuille de l'application.
"""

CAN_TRADE = False
LONG_ONLY = True
LEVERAGE = 1.0
CAPITAL = 30.0
STATUS = "EXPLORATORY"
