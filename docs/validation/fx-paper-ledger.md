# Conversion de devises fictives — branche de travail

La route `POST /api/accounts/{id}/fx` convertit des soldes **fictifs** du seul
portefeuille principal EUR. Les monnaies acceptées sont EUR, USD, USDC et USDT.
L'utilisateur fournit `base`, `target`, `amount`, un `request_id` unique, et
éventuellement un intermédiaire `via`. Le client ne peut pas fournir le taux.
L'historique est accessible par `GET /api/accounts/{id}/fx`.

Chaque étape utilise le meilleur bid ou l'inverse du meilleur ask d'un carnet
spot public de la source de marché configurée. Le serveur demande le carnet ;
aucune clé ni méthode privée d'exchange n'est utilisée. Il exige l'horodatage
fourni par le marché, récent de 60 secondes maximum et non futur, un écart
bid/ask maximum de 3 %, et une profondeur suffisante au premier niveau. Deux
étapes ne peuvent différer de plus de 5 secondes. Aucun taux absent n'est
remplacé par une parité supposée. Si ces conditions échouent, rien ne bouge.

Le débit, le crédit et le journal détaillé sont écrits ensemble dans une
transaction SQLite. Les soldes réservés par des ordres ne peuvent pas être
convertis. Les frais configurés pour la simulation sont appliqués par étape et
la sortie est arrondie vers le bas à 8 décimales. Une même requête répétée avec
son identifiant renvoie l'opération initiale sans nouveau débit ; un identifiant
réutilisé avec des paramètres différents est refusé. Le taux, le carnet, les
frais et la source descriptive de chaque étape sont conservés.
La profondeur déjà consommée du même carnet horodaté est également enregistrée
dans la même transaction pour empêcher plusieurs conversions de la réutiliser.

La valorisation EUR des monnaies détenues demande également des cours récents
et une profondeur suffisante ; elle devient indisponible plutôt que de supposer
une valeur nulle ou une équivalence stablecoin. Le solde EUR restant seul est
affiché comme liquidité immédiatement utilisable pour les ordres EUR.

## Limites importantes

* L'intégration est testée avec des carnets artificiels uniquement. Aucun taux
  réel, aucune disponibilité de paire et aucune qualité de fournisseur n'ont
  été vérifiés en ligne. Certains exchanges ne transmettent aucun horodatage de
  carnet dans CCXT : dans ce cas la conversion est systématiquement refusée.
  La source est une étiquette descriptive, pas une preuve d'authenticité.
  Le [manuel CCXT](https://github.com/ccxt/ccxt/wiki/manual#order-book-structure)
  confirme que cet horodatage est en millisecondes et peut être absent.
* Le simulateur ne consomme que le premier niveau du carnet : il refuse les
  montants au-delà de cette profondeur. Il ne reproduit pas les exécutions
  partielles, la concurrence des autres acteurs ni les changements de carnet
  entre lecture et exécution. Un nouveau carnet public peut encore afficher la
  liquidité utilisée par une opération fictive précédente. L'API est disponible mais aucun écran dédié n'a
  encore été ajouté.
* Les actifs cotés USD/USDC/USDT ne sont pas encore négociables directement :
  le courtier continue de refuser les paires dont la devise de cotation diffère
  de l'EUR. Il reste à convertir correctement risques, frais et P&L ainsi que
  les prix historiques, puis à tester les achats et ventes correspondants.
* Les soldes existants sont stockés en nombres SQLite REAL ; les enregistrements
  FX conservent les décimales en texte. Une migration comptable complète vers
  des unités entières ou Decimal persisté et une revue des arrondis demeurent
  nécessaires avant de prétendre à une précision comptable absolue.
* La simulation reste paper-only. Ces tests ne prouvent ni rentabilité, ni
  absence absolue de contournement du verrou par du code Python arbitraire.
