# ALPHA FACTORY v2 — préparation de l'évaluation unique (mission 52)

Branche `claude/alpha-factory-v2-readiness`, empilée sur COST OPTIMIZER (#50) : le laboratoire (#49) et le contrat de
coûts (#50) sont dans sa lignée, le hub (#51) n'y est pas (le laboratoire n'en dépend pas). **Aucune évaluation n'a été
lancée, aucun plan n'est enregistré, aucune stratégie n'a été exécutée sur les données réelles** : seuls des panneaux
synthétiques ont servi aux tests ; sur l'archive réelle, seuls le catalogue de la plateforme et la liste des actifs ont
été lus (couverture des règles), jamais un rendement.

## 1. Compatibilité du contrat de coûts #50 avec le laboratoire #49

Commande : `python -m tools.alpha_lab costs-check` (vérifie, n'adopte rien).

| Scénario (par ordre, pb) | BTC, ETH | Autres | Aller-retour BTC, ETH / autres | Commission | Conversion |
|---|---|---|---|---|---|
| Actuel « prudent » (provisoire) | 22,00 | 28,00 | 44,0 / 56,0 | 10 | 5 |
| Actuel « tendu » (provisoire) | 36,00 | 54,00 | 72,0 / 108,0 | 10 | 5 |
| Proposé « mesure » (#50) | 21,88 | 26,36 | 43,8 / 52,7 | 10 (hypothèse) | 11,67 |
| Proposé « tension » (#50) | 30,66 | 37,95 | 61,3 / 75,9 | 10 (hypothèse) | 14,51 |

Résultat : **compatible**. Le fichier proposé se charge sans erreur, ses scénarios sont ordonnés (brut ≤ mesure ≤
tension), ses valeurs sont exactement celles publiées par le contrat `cost_contract_v1` (test
`test_the_cost_contract_of_pr50_is_readable_by_the_lab_and_matches_its_published_values`), le minimum d'ordre (5) et
les actifs majeurs (BTC, ETH) correspondent. **Il n'est pas adopté** : le laboratoire garde son fichier provisoire tant
que vous ne l'avez pas décidé.

## 2. Simulation des reliquats (correction)

Avant : le simulateur prélevait la commission dans les liquidités et n'arrondissait aucune quantité ; il ignorait donc
les reliquats que COST OPTIMIZER a mesurés (médiane 2,6 % d'une position de 10 € sur BTC/ETH).

Maintenant (seulement si les règles de marché sont fournies ; sans elles, comportement strictement identique) :
- quantité achetée arrondie au **pas de la plateforme** ; commission prélevée **dans l'actif reçu** ; conversion payée
  en liquidités ;
- à la vente, seule la part multiple du pas est vendable ; le reste est un **reliquat compté comme perdu** à sa
  création (règle prudente, la même que celle du hub), retiré du portefeuille et rapporté à part (`dust_lost`) ;
- minimum d'ordre = le plus grand du contrat (5) et de celui de la plateforme ; un ordre qui tombe sous le minimum
  après arrondi est refusé ;
- règles lues dans le **catalogue archivé** de la plateforme, vérifié par son empreinte : 775 actifs sur 775 couverts
  (les paires retirées de la cote y restent, statut « BREAK ») ;
- le plan déclarera le modèle d'exécution et l'empreinte du catalogue ; l'exécution refuse un catalogue différent.

Tests (données synthétiques) : sans règles, résultats identiques ; avec un pas grossier, chaque reliquat vaut au plus
un pas et la conservation de l'argent est exacte (capital = valeur finale + frais + écart + reliquats) ; avec un pas
très fin, le modèle rejoint l'ancien ; jamais de liquidités créées ni de position négative sur un panneau aléatoire ;
ordre refusé sous le minimum après arrondi.

Ordre de grandeur **structurel** (synthétique, pas un résultat de stratégie) : un aller-retour de 30 € en BTC au pas
de 0,00001 laisse ≈ 0,58 € de reliquat ; une règle qui échange souvent un petit capital y perd beaucoup.

## 3. Verdict : ALPHA FACTORY v2 est-il prêt pour son évaluation unique ?

**Techniquement prêt, mais PAS ENCORE prêt à être lancé.** L'évaluation ne doit pas commencer tant que les points
suivants ne sont pas tranchés :

| Point | État |
|---|---|
| Code du laboratoire, modèle de reliquats, compatibilité du contrat | prêt, testé (données synthétiques) |
| Plan préenregistré | non enregistré (étape suivante, après votre accord) |
| **Décision 1 : adopter le contrat #50** (scénarios « mesure » et « tension ») | **en attente de vous** |
| **Décision 2 : commission** (0,10 %, non vérifiée ; la confirmer, ou l'accepter comme hypothèse déclarée) | **en attente de vous** |
| **Décision 3 : conversion à chaque ordre** (recommandé) plutôt qu'« USDT conservé » | **en attente de vous** |
| Décision 4 : ordre au marché à l'ouverture (déjà le cas du laboratoire) | conforme |
| Décision 5 : modéliser les reliquats | fait ; il sera appliqué dès l'enregistrement du plan (à confirmer) |
| Archive des données (775 actifs, manifeste `f301ffe9…`, catalogue `3ec824f8…`) | présente dans l'espace de travail temporaire, **pas dans le dépôt** : elle doit être conservée entre l'enregistrement et l'exécution (ou re-téléchargée AVANT l'enregistrement) |

Après votre accord : (1) remplacer le fichier de coûts par la proposition #50 (nouvelle version, nouvelle empreinte) ;
(2) `python -m tools.alpha_lab register --archive …` (fige le code, les candidats, les coûts, le modèle de reliquats,
l'archive) ; (3) `python -m tools.alpha_lab run …` une seule fois ; (4) publier les résultats tels quels, y compris
négatifs.

## 4. Limites

- Pas de Windows : l'évaluation tourne dans cet environnement Linux ; Windows 11 n'est pas testé.
- Les filtres de marché sont ceux du jour de l'archive, appliqués à tout l'historique (les pas ont pu changer).
- La règle « reliquat perdu » est prudente : en réalité il peut être regroupé ou converti (avec des frais).
