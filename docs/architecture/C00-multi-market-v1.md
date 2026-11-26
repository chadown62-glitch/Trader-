# TRADER AI — C00.1: socle multi-marchés local

## Pourquoi ce composant ?

TRADER AI devra accueillir six familles d'analyses indépendantes :
crypto, memecoins, actions, Forex, ETF et matières premières. Une famille peut
partager des techniques avec une autre, mais les actifs doivent conserver leur
identité, leur mémoire et leur évaluation spécifiques.

Ce premier lot introduit des contrats de **recherche**, pas un moteur de trading.
Il ne prédit aucun rendement et n'ouvre aucune position.

## Contrats livrés

- **AssetRef** : famille + symbole + plateforme + réseau + identifiant d'instrument.
  Un memecoin exige son adresse de contrat / identifiant de mint et sa blockchain.
  Deux tokens de même ticker ne deviennent pas artificiellement un seul actif.
- **Evidence** : source, champ, valeur et trois horodatages UTC millisecondes
  (événement, publication et réception). Un événement économique annoncé dans
  le futur est identifié comme *scheduled_event* et ne constitue pas son résultat.
- **MarketSnapshot** : ensemble d'observations accessibles à la date de décision ;
  interdit toute donnée reçue après cette date et les doublons identiques.
- **MarketAssessment** : avis inerte candidate, watch ou reject, avec justification
  et métriques facultatives. Il ne contient ni compte ni instruction au courtier.
- **AssetKnowledge** : faits immuables associés à l'actif et datés ; un fait
  reçu dans le futur n'est pas disponible pendant un replay historique.
- **KnowledgeStore** : charge les faits sur demande, au bon instant historique,
  et limite la taille du cache.
- **SpecialistRegistry** : charge une IA uniquement au moment de l'analyse.
  Priorité au spécialiste de l'actif, sinon à celui de la famille.
  Limite le nombre de spécialistes chargés ; aucune famille inconnue ne peut
  produire une décision par défaut.

## Exemple d'utilisation

Une sentinelle demande l'analyse de BTC/EUR sur Binance à 12 h.
Elle construit le snapshot des observations déjà reçues à 12 h.
Le registre charge alors le spécialiste Crypto (ou celui de BTC, s'il existe),
puis uniquement la mémoire autorisée de BTC. L'IA produit un avis.
Ce résultat ne constitue **jamais** un ordre ni une autorisation de trader.

Pour un memecoin, le ticker seul n'est pas une identité suffisante :
il faut son réseau et son contrat/mint, afin de ne pas mélanger deux tokens.

## Vérification réalisée

17 tests ciblés + 4 sous-tests ont été exécutés dans un environnement local
Python 3.13.5 avec pytest 9.0.2. Ils réussissent. Les tests couvrent :
horodatages invalides, données futures, événements économiques programmés,
tokens homonymes, chargement paresseux, mémoires futures, remplacement de
spécialiste par actif, bornes de cache et refus d'un résultat incohérent.

Commande pour reproduire depuis la racine du dépôt :

    python -m pytest -q tests/test_market_specialists.py

**Limites de la preuve :** les tests ont été exécutés sur une copie locale
du contenu, pas en CI sur l'arbre GitHub après publication. Pas de test de
l'ensemble du dépôt, pas de test de performances GPU ou Windows ici.

## Prochaines étapes (séparées, non réalisées)

1. Relier ce registre aux contrats déjà développés par Claude dans ALPHA
   SHADOW et aux preuves de la PR #46 de GPT Work. Aucun adaptateur n'a
   été créé à l'aveugle, car les formats et horodatages diffèrent.
2. Ajouter des spécialistes réels **locaux**, comparés d'abord aux références
   quantitatives simples sur des données séparées de l'entraînement.
3. Construire une bibliothèque de connaissances versionnée par actif avec
   provenance causalement vérifiable ; ne jamais réinjecter les résultats
   futurs dans une mémoire du passé.
4. Benchmark CPU, RAM, VRAM, temps de chargement et libération GPU.
   Le cache actuel ne garantit pas la libération des allocations CUDA.
5. Intégrer les opportunités au portefeuille unique de 30 EUR fictifs,
   uniquement lorsque les contraintes de taille minimale, liquidité,
   coût de change, séance boursière et exécution ont été vérifiées.

## Garde-fous

- Code 100 % local, standard library Python ; aucun client exchange, aucun LLM
  distant, aucune API de trading et aucun service permanent.
- Aucun changement au courtier, à main, aux protocoles gelés v2/v3 ou aux
  branches des missions Claude / GPT Work.
- Aucun trading réel, aucun levier, aucune promotion de modèle automatique.
- Aucun des six spécialistes n'est encore entraîné ou déclaré rentable.
- Cette PR nécessite une revue de code et des tests d'intégration avant fusion.
