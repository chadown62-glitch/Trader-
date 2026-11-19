# Premières mesures sur archives publiques EUR

Origine : historiques publics Binance, 4 h, BTC/EUR, ETH/EUR et SOL/EUR. Capital isolé de 30 € par politique. Ces périodes ont déjà servi à la recherche : résultats exploratoires, aucune promotion autorisée.

| Période | Politique | Capital final EUR | Rendement net | Trades clôturés | Frais EUR | Drawdown |
|---|---|---:|---:|---:|---:|---:|
| 2025-07-01 → 2026-01-01 exclu | all_signals | 30.573015 | 1.910 % | 45 | 0.569011 | 6.734 % |
| 2025-07-01 → 2026-01-01 exclu | cash | 30.000000 | 0.000 % | 0 | -0.000000 | 0.000 % |
| 2025-07-01 → 2026-01-01 exclu | passive | 30.093017 | 0.310 % | 0 | 0.017910 | 27.028 % |
| 2025-07-01 → 2026-01-01 exclu | trend_follow | 29.810533 | -0.632 % | 14 | 0.157413 | 2.994 % |
| 2025-07-01 → 2026-01-01 exclu | Sentinelle 500 candidats | 30.984016 | 3.280 % | 36 | 0.478753 | 7.263 % |
| 2025-07-01 | Sentinelle 1500 candidats | indisponible | — | — | — | — |
| 2026-01-01 → 2026-07-01 exclu | all_signals | 30.000000 | 0.000 % | 0 | -0.000000 | 0.000 % |
| 2026-01-01 → 2026-07-01 exclu | cash | 30.000000 | 0.000 % | 0 | -0.000000 | 0.000 % |
| 2026-01-01 → 2026-07-01 exclu | passive | 23.117588 | -22.941 % | 0 | 0.017920 | 28.810 % |
| 2026-01-01 → 2026-07-01 exclu | trend_follow | 30.000000 | 0.000 % | 0 | -0.000000 | 0.000 % |
| 2026-01-01 → 2026-07-01 exclu | Sentinelle 500 candidats | 30.000000 | 0.000 % | 0 | -0.000000 | 0.000 % |
| 2026-01-01 | Sentinelle 1500 candidats | indisponible | — | — | — | — |

## Ce qui est observé

En 2025 S2, le modèle à 500 candidats clôture 36 trades : gain net moyen de 0,027334 EUR par trade, rendement quotidien moyen 0,018503 %, 44,44 % de trades gagnants, profit factor 1,25994. Les frais sont déjà déduits. Exposition moyenne par bougie 9,944 %, maximum 60,120 % (la valorisation peut dépasser légèrement le plafond d’entrée).

Son AUC vaut 0,563837, son Brier 0,320240. Probabilité moyenne annoncée 64,63 %, proportion observée gagnante 39,49 % sur 157 candidats : la calibration est trop optimiste dans cet essai. Cela ne justifie pas de modifier les paramètres sur cette même période.

L’intervalle bootstrap absolu de rendement quotidien est [-0,074867 % ; +0,112438 %], confiance 98,75 % par intervalle, blocs de 7 jours, 2 000 tirages. Il contient zéro. Ces intervalles sont descriptifs et conditionnels à cet historique déjà étudié.

En 2026 S1, aucun candidat de test n’est produit par les stratégies activées : décisions=0, refus=0 et exposition=0. L’inactivité provient ici de l’absence de signaux admissibles, pas d’un classement probabiliste ou d’un seuil refusant des candidats. AUC et Brier restent indisponibles.

Seulement 559 puis 717 candidats de développement sont disponibles. Le Challenger de 1 500 candidats préenregistré ne peut être entraîné. Aucun remplacement a posteriori par une taille plus favorable : la comparaison Champion/Challenger demeure incomplète.

Le passif conserve ses positions : zéro trade clôturé ne signifie pas absence de positions ou absence de variation de capital. Il investit le panier plafonné à l’entrée ; son exposition varie ensuite avec les prix. Les minimums 5 EUR et précisions sont modélisés fixes, pas reconstruits historiquement.

## Reproduction et provenance

Le protocole a été figé au commit local 99e9798 avant l’exécution. Le premier essai `eur-v1` est conservé. Un second passage `eur-v1-instrumented` ajoute exposition et motifs de refus, sans changement de politique, seuils ou données ; les rendements nets sont identiques au premier. Le second utilise le commit local 46c8ffa. Le bundle Git joint conserve ces commits locaux ; les commits publiés via GitHub peuvent avoir une identité différente tout en partageant les arbres de fichiers.

Les JSON contiennent données quotidiennes, sous-fenêtres purgées, calibration par tranches, coûts, motifs de refus, versions et empreinte du protocole. Aucune installation ni portefeuille utilisateur n’a été utilisé.

## Archives exactes

Couverture commune : 2023-01-01 00:00 UTC inclus → 2026-07-01 00:00 UTC exclu. 7 662 bougies par actif, 22 986 au total. Aucun trou, doublon ou mouvement >50 % détecté par les contrôles configurés. Ce contrôle ne garantit pas que la source elle-même est exempte d’erreurs.

| Actif | SHA-256 des données canoniques | SHA-256 du manifeste |
|---|---|---|
| BTC-EUR | `d91edafacf6b389a2e9a0004d775b55369e074cb04188abbe986097fb79799ff` | `bccd22f2a2001785d82066dfa0db940490d101c8144c270e0377730ea52edab6` |
| ETH-EUR | `77c10d12ad9d27050e531561e686990142512e7a66231c22a00efd40980efd66` | `ba7f7f727a58388a43d68521e1bf59e779b074001b4c8207f470046c968a93d9` |
| SOL-EUR | `afe3a1b45584596837714f952d72d19b2fa5ccf2d43a7b724336b1e03b629b24` | `4832f6576987a1eb5ea466fa8a0c1686a563625d2a837e2f9ecd5efa95d4d248` |

## Prochaines étapes

- Valider le rendu de la page Performances dans un navigateur accessible et sur Windows 11.
- Tester la collecte prospective dans une instance supervisée, avec archive des informations connues au signal, atomicité et reprise après crash. La journalisation existante des candidats est postérieure à la tentative d’entrée ; ce parcours mérite un test de panne entre exécution fictive et écriture avant de déclarer l’observation prospective fiable.
- Réserver de nouvelles données avant toute nouvelle sélection de paramètres ; ne pas considérer les résultats présents comme une preuve indépendante.
- Ne pas augmenter les tailles ni changer les stratégies à la seule lecture de ces périodes pour revendiquer ensuite un succès. Aucune amélioration algorithmique ni rentabilité durable n’est démontrée ici.
