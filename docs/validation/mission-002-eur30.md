# Mission 002 : portefeuille EUR et simulation — travail en cours

Cette branche est empilée sur la revue paper-only de la PR #7, elle-même basée
sur la PR #5. Rien n'a été fusionné dans `main`.

## Comportement proposé

* Un portefeuille principal simulé est créé une seule fois à 30 EUR. Les ordres
  manuels et l'agent d'entraînement utilisent son même identifiant de compte.
  Le redémarrage ne remet pas les soldes à 30 EUR.
* Les anciens comptes, soldes, ordres et positions restent enregistrés mais les
  anciens comptes sont en lecture seule. L'agent précédemment actif est arrêté à
  la création du nouveau portefeuille. Une nouvelle activation est explicite.
* Seules les paires cotées en EUR sont exécutables avec le portefeuille EUR.
  Les paires USD, USDC et USDT sont refusées : aucune parité ni conversion
  automatique n'est supposée. Les anciennes configurations en USDC restent
  conservées ; il faut choisir une liste de paires EUR avant de démarrer l'IA.
* Les historiques USDT ne sont plus accolés à ceux d'une autre monnaie sans
  conversion. En attendant les taux historiques, l'historique peut être plus court.
* Les réservations et ouvertures passent sous transaction SQLite. Un ordre ne
  peut pas être exécuté deux fois par deux traitements concurrents. Un ordre
  déclenché par un gap et devenu trop cher est rejeté et libère sa réservation.
* Les frais des deux côtés et le glissement fixe s'appliquent, ainsi que les
  minimums du marché connus du simulateur. Les cours expirés et les bougies
  périmées, futures ou incohérentes sont refusés. Une valorisation sans cours
  disponible est signalée comme indisponible et non évaluée à zéro.
* Une demande de fermeture ou de modification est refusée si le cours est
  indisponible. Le remplacement d'un stop et la fermeture sont transactionnels :
  une erreur restaure les protections précédentes. Un montant sous le minimum
  de vente ne fait plus déclarer une position fermée sans vente fictive.
* La mise à jour du solde, de l'exécution et de la position est atomique : une
  panne du suivi de position annule l'exécution fictive. Si un stop initial
  demandé ne peut pas être créé, l'entrée n'est pas conservée.
* Le backtest est isolé dans une base SQLite en mémoire et son capital par défaut
  est 30 unités de la monnaie sélectionnée. Il ne modifie pas le portefeuille.

## Limites à résoudre avant validation de l'issue #6

1. Conversion EUR/USD/USDC/USDT **non implémentée**. Il faut fournir des taux
   horodatés, leurs règles d'arrondi, de fraîcheur et les frais de conversion.
2. Le paper trading utilise le prix observé alors que le backtest décide sur la
   clôture et peut exécuter au prix de cette clôture. La parité temporelle et
   l'exécution au premier prix suivant restent à concevoir et à tester.
3. La source synthétique calcule sa bougie partielle à partir de la bougie entière
   pré-générée. Elle n'est donc pas une preuve d'absence de fuite de données
   futures ; ne pas l'utiliser pour valider la généralisation d'un modèle.
4. Le spread, la profondeur, la liquidité, les exécutions partielles et les frais
   propres à chaque place ne sont pas reproduits complètement. Le glissement est
   fixe ; les seuils de marché sont ceux connus de la source de données.
5. L'horodatage de bougie limite l'ancienneté possible d'un cours mais ne prouve
   pas que l'exchange a transmis une nouvelle transaction dans la minute.
6. Le fait que l'agent puisse trader 30 EUR dans un scénario artificiel ne
   garantit ni sa capacité à trader tous les marchés avec leurs minimums réels,
   ni sa rentabilité. Les paires EUR listées par défaut n'ont pas été vérifiées
   auprès des exchanges pendant ces tests hors réseau.
7. `paper.initial_balance` et `ai.extended_history` restent dans le schéma pour
   relire les réglages historiques, mais ne modifient pas le capital de 30 EUR ni
   ne réactivent la fusion de monnaies. Ils sont retirés de l'interface.
8. Les suites ont été exécutées sans réseau de sortie, sans compte réel et avec
   un paquet CCXT local partiel ; installer les dépendances complètes du projet
   dans un environnement isolé pour une nouvelle validation indépendante.
9. Un stop simulé conservé en base ne peut être exécuté que si de nouvelles
   données arrivent. Aucun prix ni exécution ne sont garantis pendant une panne.

Le verdict de l'issue #6 reste **travail en cours / tests incomplets** : ni la
simulation réaliste complète, ni la conversion multi-devises, ni l'apprentissage
ou un rendement futur ne sont démontrés par cette branche.

## Tests temporels reproductibles

`tests/test_temporal_contract_gaps.py` contient deux tests marqués `xfail`
strictement : ces exigences sont actuellement en échec et un passage inattendu
rend la suite rouge jusqu'à révision. Dans le scénario du backtest, la clôture
est 100 et l'ouverture suivante 120 : avec 5 points de base de glissement, le
code exécute à **100,05**, contre **120,06** à l'ouverture suivante. L'autre
modifie uniquement la clôture future pré-générée d'une bougie synthétique et
constate que la partie supposée déjà observée change.

Ces deux échecs ont aussi été exécutés avec `--runxfail` pour confirmer qu'ils
reproduisent bien les défauts annoncés. Ils ne sont ni des validations ni une
preuve que d'autres fuites temporelles n'existent pas.
