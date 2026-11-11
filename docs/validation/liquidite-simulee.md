# Banc d'exécution sur carnet fictif

`sentinelle.execution.liquidity` contient un calculateur déterministe indépendant
pour estimer l'exécution d'un ordre taker à partir d'un carnet L2 horodaté. Les
achats consomment les asks, les ventes les bids ; une limite interdit un prix
moins favorable. Les niveaux sont parcourus dans l'ordre, la quantité est
arrondie au pas, et les frais sont calculés sur chaque exécution. Le résultat
indique le prix moyen, les frais, les quantités exécutée et restante, ainsi que
la variation de la devise de cotation. Un budget ou solde disponible peut
limiter l'exécution. Un manque de profondeur donne une exécution partielle.

Le carnet doit comprendre deux côtés non croisés, triés, avec des prix et
quantités positifs, et un horodatage non futur et suffisamment récent. Les
valeurs des tests sont entièrement artificielles.

Ce composant **n'est pas encore raccordé au broker ni au portefeuille**. Il ne
réserve pas de soldes et ne persiste pas les fills. `LiquidityPool` consomme
atomiquement les niveaux partagés en mémoire, y compris entre threads, et refuse
une cotation de remplacement à date égale ou antérieure. Le calculateur pur
`simulate_taker` ne consomme pas de carnet. Un orchestrateur doit coupler la
consommation au registre, traiter les exécutions partielles avec les protections OCO et adapter le carnet
au temps sans fuite d'information. Il ne simule ni délai, ni annulations,
ni concurrence, ni file d'attente d'un ordre maker. Le résultat n'est donc
pas une reproduction garantie d'un marché réel.
