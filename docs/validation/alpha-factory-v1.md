# ALPHA FACTORY v1 — académie de traders, Shadow Council et vitesse d'apprentissage

Mission : https://github.com/chadown62-glitch/Trader-/issues/38 (roadmap #36).
Base : PR #37, `research/expert-strategy-evidence-v0` (`6d990a4`). Branche : `claude/alpha-factory-v1`.

**Ce laboratoire ne trade pas.** Aucun module de `sentinelle/alpha/` n'importe le courtier, les
services, l'API, le portefeuille de l'application, le réseau ou `subprocess` (contrôlé par analyse
du code dans les tests). Portefeuille simulé : 30 € fictifs. Aucune promotion de modèle. Les
protocoles gelés `research/prospective-v2` et `research/macro-shadow-v3` sont identiques au bit près
(empreinte globale vérifiée avant et après). Aucun fichier de GPT Work (#35 : superviseur, collecte,
Préparation, `docs/WINDOWS.md`, outbox) n'a été touché.

## Résultats en bref

- **Aucune amélioration répliquée hors échantillon n'est démontrée.** Cela vaut pour tous les concurrents, y compris le conseil multi-agents et les trois méthodes nouvelles, et ce sur les marchés synthétiques comme sur les historiques réels. Le seul verdict scientifiquement acceptable est donc « **non démontré** ».
- **Témoin négatif (bruit) :** aucun concurrent ni aucun vainqueur de tournoi ne s'y annonce rentable.
- **Témoin positif (règle cachée apprenable) :**
  - le banc le détecte avec peu d'essais : 3 marchés sur 3 en développement ;
  - il ne le détecte plus avec les 55 essais du protocole officiel (1 sur 3).
  - Conséquence : un futur protocole confirmatoire devra préenregistrer **un seul** candidat.
- **Ce que les modèles apprennent :** surtout *quel enseignant croire*. Leur AUC dépasse à peine celle de la seule identité de l'enseignant.
- **Efficacité :** le RegimeRouter atteint dès 50 observations les mêmes résultats que le modèle du projet, pour environ 1/40 du calcul. C'est le meilleur candidat pour une prochaine expérience prospective, sans aucun avantage démontré à ce jour.
- **Conseil multi-agents :** aucun des six agents n'a de contribution démontrée. Sur le témoin positif, le conseil fait moins bien qu'un modèle unique, et l'agent structure y coûte 9 €.
- **Données réelles** (exploratoire, mi-2025 → novembre 2026) : suivre tous les enseignants aurait perdu 14,41 € sur 30 € ; les filtres appris réduisent cette perte, mais aucun gain n'est démontré.
- **Intégrité :** 3 bugs de mon propre code ont été trouvés en cours de route et corrigés, chacun avec un test : une fuite d'équité dans le conseil, des registres en collision, un tournoi qui mélangeait deux marchés. **La relance officielle reproduit à l'identique 825 résultats d'apprentissage et 540 variantes d'ablation.**

---

## 1. Audit de la PR #37 (Lot A.1)

Les 19 tests de #37 ont été réexécutés sous CPython 3.12.3, hors réseau (filtre seccomp) : 19 réussis.
Des sondes exécutables ont ensuite démontré **8 défauts réels**, tous corrigés dans
`sentinelle/ai/expert_lab.py` :

| # | Défaut démontré | Conséquence | Correction |
|---|---|---|---|
| 1 | Décision enregistrée **après** que son issue soit connue, acceptée | entraînement sur du « génie » rétrospectif | `recorded_after_outcome` |
| 2 | Secondes et millisecondes mélangées | une étiquette en ms paraissait « mûre » | horodatages en secondes uniquement (1e9–1e11) |
| 3 | Même décision sous deux identifiants | comptée deux fois | `duplicate_decision_content` (on garde le plus petit identifiant) |
| 4 | Identifiants en conflit | l'ordre d'entrée décidait du gagnant (+5 R ou −1 R) | toutes les copies en quarantaine, résultat indépendant de l'ordre |
| 5 | Gain « réalisé » avec une simple URL de fill | aucun prix, heure ni frais | `fill_at`, `fill_price`, `exit_at`, `exit_price`, `costs_r` exigés et cohérents |
| 6 | Drapeaux `verified` auto-déclarés suffisants | provenance non auditée | `ProvenanceAudit` indépendant (auditeur, date ≤ cut-off, empreintes SHA-256, niveau de preuve) |
| 7 | Réception tardive et révisions non modélisées | fuite par donnée arrivée après la décision | `features_received_at`, révisions horodatées, `retrospective_revision` |
| 8 | Purge silencieuse aux bornes des fenêtres | lignes disparues sans trace | partie `purged` rendue par `chronological_partitions` |

Les tests de #37 ont été adaptés : même intention, mais avec un fill horodaté et un audit. Un test
a changé d'attendu (le n° 4) : des enregistrements en conflit ne désignent plus de gagnant. **41
tests**, dont 22 nouveaux, chacun ayant échoué sur la v0.

**Limite qui demeure :** la porte vérifie la *cohérence* des preuves, pas leur authenticité. Un
audit humain archivé reste indispensable avant tout entraînement sur de vraies décisions de trader.

## 2. Catalogue des sources (Lot A.2)

Fichier versionné : `sentinelle/alpha/catalogue_v1.json`, en version 1.0.0, identifié par l'empreinte de son contenu.

**Niveaux de preuve :**

| Niveau | Signification | Peut enseigner une règle ? | Peut étiqueter un gain ? |
|---|---|---|---|
| `claimed_result` | résultat seulement déclaré (livre, interview, vidéo) | **non** | **non** |
| `public_heuristic` | idée du domaine public | oui | non |
| `documented_method` | règles publiées reproductibles | oui | non |
| `timestamped_decision` | décision d'un individu horodatée, preuve archivée | oui | non |
| `verified_fill` | exécution réelle vérifiée et auditée | oui | oui, avec un audit |
| `counterfactual` | issue simulée par le laboratoire | — | **jamais un gain réalisé** |

**Contenu du catalogue :**
- Méthodes et sources (10 entrées) : Moskowitz-Ooi-Pedersen 2012 (momentum de série temporelle), canal de Donchian, structure des règles « Turtles » (Faith 2007), Wilder 1978 (ATR, RSI), Connors-Alvarez 2008 (RSI 2), repli dans la tendance, Amihud 2002 (illiquidité), risque fixe en R.
- Deux exemples de résultats seulement déclarés, interdits comme étiquettes : les performances des Turtles et une vidéo « x5 » type.

**Ce que le laboratoire s'interdit :**
- Ne réimplémenter que des règles générales : aucun texte, aucune donnée et aucun historique de trader copiés ; aucun contenu payant extrait.
- Ne construire aucune liste de « super-traders ».
- Refuser une entrée qui déclare `verified`, `performance`, `win_rate` ou `returns`, ainsi qu'un résultat déclaré marqué comme enseignant.

**Constat :** aucune source de la v1 n'a de fills vérifiés. Le laboratoire n'a donc **aucune**
étiquette de gain d'expert : il n'apprend que des règles et de ses propres issues simulées, datées.

## 3. Les cinq enseignants (Lot A.3)

Les enseignants sont définis dans `sentinelle/alpha/teachers.py`. Leurs paramètres ont été fixés avant tout test et n'ont pas été optimisés. Ils travaillent sur des bougies de 4 h et achètent seulement (pas de vente à découvert).

| Enseignant | Entrée (à la clôture, exécution à l'ouverture suivante) | Sortie | Sources |
|---|---|---|---|
| Tendance | moyenne 50 > moyenne 200, clôture > moyenne 50, rendement sur 24 bougies > 0 | clôture < moyenne 50, stop suiveur à 3 ATR, 180 bougies au plus | TSMOM, Wilder, risque fixe |
| Cassure | clôture > plus haut des 20 bougies précédentes | clôture < plus bas des 10 précédentes, stop à 2 ATR | Donchian, Turtles, Wilder |
| Repli | tendance haussière, clôture < moyenne 20, RSI(3) < 30 | clôture > moyenne 20, stop à 1,5 ATR, 30 bougies au plus | heuristique, Wilder |
| Retour à la moyenne | RSI(2) < 10, clôture > moyenne 200 | clôture > moyenne 5, stop à 3 ATR, 15 bougies au plus | Connors, Wilder |
| Abstention / liquidité | refuse si écart > 25 bps, volatilité extrême, volume < 25 % de la médiane | — | Amihud, Wilder |

Chaque décision est une séquence interprétable : entrée ou absence d'entrée, plan (stop, sortie, durée maximale), taille simulée sur 30 €, raisons, condition d'invalidation et sources.

**Ce qui n'est pas calculé :** aucune étiquette de « trade gagnant ». L'issue n'existe qu'après l'exécution simulée, à sa date (`available_at`).

**Variantes de sortie apprenables :** la sortie de l'enseignant, un stop serré, une sortie à la durée, un objectif à 2 R. Elles produisent des issues *contrefactuelles*, qui servent à apprendre à mieux sortir.

## 4. Données (Lot A.4)

**Marchés synthétiques déterministes** (`markets.py`) :
- vérité cachée, réservée au rapport par climat de marché ;
- régimes : tendance, range, calme, volatil ;
- `noise` : aucun avantage, témoin négatif ;
- `regime_shift` : l'avantage change en cours de route ;
- `hidden_rule` : témoin positif ; une poussée de volume visible annonce 12 bougies de continuation, ce qui est apprenable à partir des seules données as_of.

**Pièges adversariaux :** trous de liquidité (écarts de 60 à 250 bps), annonces macro dont la révision arrive 3 jours plus tard, colonne « fuite » (rendement de la bougie suivante), flux d'expert ne montrant que des gagnants.

**Historiques publics** (`bars.load_history_csv`) : lecture seule, empreinte SHA-256 consignée ; bougies invalides et doublons écartés, jamais corrigés ; alignement sans bougie inventée.

## 5. Exécution et portefeuille

L'exécution (`execution.py`) applique les formules pures de `sentinelle.execution.contract`, celles-là mêmes que le projet utilise pour l'étiquetage, le backtest et le trading fictif. Elle n'importe aucun courtier.

**Règles d'exécution :**
- décision à la clôture, entrée au premier prix suivant, aggravé du demi-écart et du glissement ;
- entrée abandonnée si ce prix est déjà sous le stop ;
- stop prioritaire ; un trou de cotation est exécuté au pire du stop et de l'ouverture ;
- frais de 0,1 % par ordre ;
- résultat ventilé ainsi : `gross_r − cost_r = net_r`.

**Portefeuille :** 30 €, risque de 0,5 % par trade, minimum de 5 € (relevé seulement si le risque reste ≤ 1 %), 3 positions au plus, pas de levier ni de solde négatif. L'argent d'une sortie n'est réutilisable qu'à la bougie suivante (hypothèse prudente).

## 6. Shadow Council (Lot B)

Le conseil est défini dans `council.py`.

**Les six agents :**

| Agent | Rôle |
|---|---|
| Structure | tendance, range, volatilité ; s'oppose aux configurations incohérentes |
| Entrées / sorties | espérance nette (modèle calibré ou taux de l'enseignant, source indiquée), « pourquoi attendre ? » (prix étiré de plus de 2,5 ATR), variante de sortie apprise, « pourquoi sortir ? » |
| Liquidité / coûts | coût en R, veto si marché mince, « et si le carnet est faux ? » (coûts ×3) |
| Macro | seules les versions publiées à l'instant comptent ; abstention près d'une annonce à fort impact |
| Équipe rouge | fuite (données postérieures à la décision), données périmées, fragilité aux coûts, faux consensus, probabilité non conditionnelle |
| Risque | **veto** : positions maximales, actif déjà ouvert, minimum de 5 € impossible sous 1 %, perte du jour au-delà de 3 % |

**Le coordinateur** classe les entrées par espérance nette, pénalise l'incertitude et choisit **ABSTAIN** sauf preuve du contraire. Chaque décision archive :
- les propositions de chaque agent ;
- les désaccords ;
- « pourquoi attendre ? », « pourquoi sortir ? » et « et si le carnet est faux ? » ;
- les vetos.

**Garanties :**
- Les plans et les décisions sont des objets figés, sans méthode : ils ne savent rien exécuter.
- Le registre (`ledger.py`) est en ajout seul et chaîné par SHA-256. Une décision y est écrite avant son issue ; une issue ne peut ni précéder sa décision, ni référencer une décision absente ; toute réécriture du passé est détectée.
- **Causalité de bout en bout :** les décisions recalculées sur un historique tronqué juste après la décision sont identiques.

### Résultats des ablations (mesure officielle, moyenne de 3 marchés par famille)

P&L net moyen en euros sur la fenêtre d'évaluation (≈ 333 jours, 30 € fictifs au départ) :

| Famille | Conseil complet | Vote | Modèle unique | Toutes les entrées | Achat-conservation |
|---|---|---|---|---|---|
| bruit (témoin négatif) | −0,14 | +0,21 | −0,21 | −2,82 | +21,20 |
| régimes | +1,38 | +0,81 | +1,91 | −1,52 | +17,70 |
| rupture de régime | −1,06 | −4,18 | +0,64 | −9,66 | +20,18 |
| règle cachée ×1 (témoin positif) | +9,40 | +14,28 | **+19,89** | +13,40 | +27,56 |
| règle cachée ×0,5 | +1,34 | +4,83 | +1,65 | +1,88 | +22,74 |

**Contribution marginale de chaque agent** (conseil complet − conseil sans l'agent, en €) :

| Famille | Structure | Entrées / sorties | Liquidité | Macro | Équipe rouge | Risque |
|---|---|---|---|---|---|---|
| bruit | −0,07 | +0,96 | 0,00 | 0,00 | −0,81 | +0,10 |
| régimes | +1,23 | +0,79 | 0,00 | 0,00 | −1,42 | 0,00 |
| rupture de régime | +0,38 | +4,94 | 0,00 | 0,00 | +1,05 | −0,12 |
| règle cachée ×1 | **−8,98** | −1,07 | 0,00 | +0,04 | 0,00 | +0,15 |
| règle cachée ×0,5 | −1,69 | −1,13 | 0,00 | −0,32 | −0,81 | −0,13 |

**Verdict : les six agents sont « retirables (contribution non démontrée) ».** Aucune contribution
marginale n'est positive avec une borne basse > 0 sur 2 marchés sur 3.

- Sur le témoin positif, l'agent structure **coûte** 9 € : il refuse des cassures qui étaient justement le bon signal.
- Liquidité et macro n'ont aucun effet sur ces marchés, ce qui est attendu : aucun effet de prix n'y est planté et les trous de liquidité sont rares.
- **Le conseil complet fait moins bien que le modèle unique** sur le témoin positif (+9,40 € contre +19,89 €). Dans cette version, la délibération ajoute surtout de la prudence et de la friction.
- L'achat-conservation domine sur les marchés synthétiques, qui montent par hasard et par convexité. C'est un rappel : le meilleur résultat n'est pas forcément celui d'un trader actif.


## 7. Learning Velocity Challenge (Lot C)

**Protocole :**
- Préenregistrement : `velocity.preregister` écrit la configuration, son empreinte, le nombre d'essais et la règle de décision **avant** toute évaluation ; il refuse de réécrire un plan différent dans le même dossier.
- Fenêtres : apprentissage 50 %, calibration 15 %, sélection 15 %, évaluation 20 %, disjointes. Embargo de 30 bougies ; purge des issues connues après la fin de leur fenêtre.
- Budgets égaux pour tous : les 50, 100, 200 ou 400 dernières situations mûres, ou toutes.
- Mode figé : la fenêtre d'évaluation ne sert jamais à apprendre ni à régler (vérifié par un test « espion »).

**Concurrents :**
- références : rien (`cash`), toutes les entrées (`take_all`), chaque enseignant seul ;
- **modèle du projet** : la recette « mixte » de `sentinelle.ai.model` (arbres simples et linéaire), calibrée par Platt ;
- trois méthodes nouvelles, testables :
  - **ErrorReplay** : réentraînement qui pondère les erreurs mesurées hors échantillon (plis chronologiques) et les grosses pertes, et qui apprend aussi des **entrées refusées** dont l'issue est connue ;
  - **RegimeRouter** : choix de l'enseignant selon le climat connu à l'instant (tendance × volatilité), moyenne d'issues mûres rétrécie vers 0 ; quasiment gratuit en calcul ;
  - **ExitLearner** : choix de la meilleure *sortie* par enseignant et par climat, d'après les issues contrefactuelles mûres ; plus **RouterWithExits**, la combinaison préenregistrée des deux.

**Mesures :**
- prédictives, séparées des mesures financières : AUC, Brier, log-loss, ECE. Le score de Brier est comparé au **taux de gain de chaque enseignant** et non à une constante globale (trop facile à battre) ;
- financières sur 30 € fictifs : P&L net, trades, baisse maximale, rotation, frais, résultat contrefactuel des abstentions, résultats par climat et par actif ;
- coût : temps CPU, pic de mémoire, et valeur par 100 observations, par minute CPU et par Mo.

**Décision :**
- comparaison appariée jour par jour à `take_all`, avec un intervalle bootstrap par blocs de 7 jours ;
- Bonferroni sur le nombre d'essais par marché ;
- verdict « **amélioration répliquée hors échantillon** » seulement si la borne basse est positive sur au moins 2/3 des marchés indépendants d'une même famille (et au moins 2) ; sinon « non démontré ».

**Shadow Tournament :** tous les concurrents (apprenants × budgets, conseil, vote) sont classés sur la **sélection** ; seul le vainqueur est jugé, une fois, sur l'**évaluation**. On rapporte aussi la « malédiction du vainqueur » et la corrélation de rang entre sélection et évaluation.

**Puissance du banc (graines de développement 41 à 43, rapportée en toute transparence) :**
- avec 6 000 bougies (≈ 200 jours d'évaluation), le témoin positif n'était détecté sur aucun marché, et la version au signal doublé sur 1 seulement ;
- avec 10 000 bougies (≈ 333 jours), le témoin positif est détecté sur 3 marchés sur 3 ;
- la configuration officielle utilise donc 10 000 bougies.

**Graines officielles :** 1001, 2002 et 3003, jamais exécutées pendant le développement. Les graines de développement sont 1 à 11, 21, 41 à 43, 101 et 202 ; 101 et 202 ont servi au passage rapide de mise au point et ont donc été écartées.

### Résultats du Learning Velocity Challenge (mesure officielle)

**Identité de la mesure :**
- préenregistrement `1310130ddd31c93d…`, écrit avant tout calcul ;
- 15 marchés (5 familles × 3 graines) de 10 000 bougies de 4 h et 3 actifs ;
- 11 concurrents × 5 budgets = 55 essais par marché, d'où un intervalle par essai à 99,91 % (Bonferroni) ;
- code du commit `19b1077`.

**Exécutions :**
- Premier passage : 47 minutes, en concurrence avec d'autres calculs.
- Relance après correction du tournoi (`b9d67df`) : 6 minutes. Elle redonne **à l'identique** les 825 résultats d'apprentissage et les 540 variantes d'ablation. Les temps CPU cités viennent de cette relance.

**Budget complet** (P&L net moyen sur 3 marchés, en € ; écart à « toutes les entrées » ; AUC du concurrent / AUC de la seule identité de l'enseignant ; temps CPU d'apprentissage) :

| Famille | Toutes les entrées | Modèle du projet | ErrorReplay | RegimeRouter | ExitLearner | Meilleur enseignant seul |
|---|---|---|---|---|---|---|
| bruit | −2,82 | −0,21 (AUC 0,681 / 0,661 ; 1,5 s) | −0,63 (0,651 ; 9,1 s) | −0,23 (0,667 ; 0,08 s) | −3,64 | cassure +0,28 |
| régimes | −1,52 | +1,91 (0,693 / 0,664 ; 1,5 s) | +4,52 (0,676 ; 8,6 s) | +1,86 (0,670 ; 0,02 s) | −0,96 | cassure +2,27 |
| rupture de régime | −9,66 | +0,64 (0,736 / 0,718) | 0,00 | 0,00 | −9,21 | tendance −2,17 |
| règle cachée ×1 | +13,40 | +19,89 (0,584 / 0,546 ; 1,3 s) | +19,55 (0,564 ; 8,8 s) | +19,60 (0,557 ; 0,03 s) | +10,85 | cassure +21,03 |
| règle cachée ×0,5 | +1,88 | +1,65 (0,602 / 0,597) | +4,31 | +4,30 | +1,85 | cassure +5,44 |

**Verdicts :**
- Règle préenregistrée (écart à la référence seulement) : « amélioration répliquée » uniquement sur la rupture de régime, où même `cash` l'obtient (3/3).
- **Règle stricte, gain absolu ET relatif : aucune amélioration répliquée hors échantillon, pour aucun concurrent, dans aucune famille.**
- Bruit : aucun verdict positif, ni sous une règle ni sous l'autre (critère P0 tenu).

**Ce qui a été réellement appris :**

1. **Les apprenants apprennent surtout *quel enseignant suivre*.**
   - Leur AUC dépasse à peine, ou n'atteint pas, celle de la simple identité de l'enseignant (par exemple 0,584 contre 0,546 sur le témoin positif ; 0,681 contre 0,661 sur le bruit).
   - Sur le témoin positif, ils retrouvent le résultat de l'enseignant « cassure » seul (+19,6 à +19,9 € contre +21,0 €), sans le dépasser.
2. **Vitesse d'apprentissage :** le RegimeRouter atteint dès **50 observations** l'essentiel de son écart sur le témoin positif (+5,96 € à 50 observations, +6,20 € avec toutes). Il le fait pour **0,03 s de calcul et 0,1 Mo, contre 1,3 s et 1,4 Mo** pour le modèle du projet, qui obtient le même écart (+6,49 €). À performance égale, c'est le concurrent le plus économe, mais aucun des deux n'est démontré meilleur que la référence.
3. **ErrorReplay** (apprendre de ses erreurs passées et des refus) ne bat pas le modèle du projet de façon démontrée. Il coûte 6 à 7 fois plus de calcul. **Non justifié dans cette version.**
4. **ExitLearner** dégrade le résultat partout (par exemple −2,56 € contre la référence sur le témoin positif) : les meilleures sorties contrefactuelles du passé ne se transposent pas. **À retirer.**
5. **Puissance :**
   - Le même témoin positif était détecté sur 3 marchés sur 3 en développement, avec 3 essais par marché. Il ne l'est plus que sur 1 sur 3 avec les 55 essais du protocole officiel : la correction pour essais multiples absorbe la puissance.
   - Un futur protocole confirmatoire devra préenregistrer **un seul candidat principal** (par exemple le RegimeRouter, le moins coûteux).

**Shadow Tournament** (classé sur la sélection, jugé une fois sur l'évaluation) :
- Règle stricte : aucun vainqueur répliqué.
- Les vainqueurs sont souvent `cash` (bruit, rupture de régime), ce qui est attendu quand tout perd sur la sélection.
- La corrélation de rang entre sélection et évaluation va de **−0,46 à +0,42 sur le bruit**, contre **+0,67 à +0,98 sur le témoin positif** (une exception à −0,04). Le classement de sélection n'a donc de valeur que lorsqu'une structure réelle existe. C'est un diagnostic utile avant toute sélection future.
- « Malédiction du vainqueur » : écart sélection − évaluation compris entre −0,10 % et +0,09 % par jour selon les marchés.

**Docteur des erreurs** (politique « toutes les entrées », fenêtre d'évaluation, issues mûres, 3 marchés cumulés) :

| Famille | Fausses entrées | Refus injustifiés (contrefactuels) | Sorties trop précoces | Tués par les coûts |
|---|---|---|---|---|
| règle cachée ×1 | 902 (−0,75 R) | 85 (+1,54 R) | 639 | 51 |
| règle cachée ×0,5 | 1 010 (−0,71 R) | 100 (+0,91 R) | 698 | 70 |

Sur le témoin positif, l'abstention pour liquidité a écarté 85 entrées qui auraient rapporté +1,54 R en moyenne. C'est une **piste**, pas une conclusion : à tester dans un futur protocole préenregistré. Ces chiffres sont contrefactuels, jamais des gains manqués « réels ». Toutes les familles sont dans `mesures/alpha-factory-v1/docteur-et-climats-synthetique.json`.

Tableaux complets (tous budgets, tous concurrents, ablations, tournoi) : [mesures/alpha-factory-v1/tableaux.md](mesures/alpha-factory-v1/tableaux.md).


## 8. Données publiques réelles (exploratoire)

**Données :**
- historiques publics Binance, 4 h, BTC, ETH, SOL, BNB, XRP et ADA cotés en USDT ;
- fichiers en lecture seule ; empreintes SHA-256 dans `mesures/alpha-factory-v1/summary-public-exploratoire.json` ;
- 13 694 bougies alignées, du 22/08/2020 au 10/11/2026.

**Conditions :**
- conversion USDT → EUR supposée à 5 bps par ordre ; écart de 4 bps supposé (aucun historique d'écart) ;
- fenêtre d'évaluation : les 20 % les plus récents, de mi-2025 à novembre 2026 ;
- préenregistrement `afc5cd13…`.

**Ces périodes ont déjà été étudiées par le projet : résultat EXPLORATOIRE.**

| Groupe | Toutes les entrées | Achat-conservation | Modèle du projet | RegimeRouter | Conseil complet | Meilleur enseignant |
|---|---|---|---|---|---|---|
| 6 actifs | −14,41 € (baisse max. 49 %) | −15,07 € | −3,37 € | −0,84 € | −2,77 € | tendance −0,29 € |
| BTC, ETH, SOL | −12,19 € | −14,24 € | **+2,16 €** | −4,67 € | −2,69 € | tendance −2,96 € |
| BNB, XRP, ADA | −7,97 € | −11,68 € | −1,64 € | +0,61 € | −2,00 € | tendance +2,32 € |

**Constats :**
- Sur cette période baissière, suivre les cinq enseignants aurait fait perdre près de la moitié des 30 €. Tous les filtres appris réduisent fortement cette perte.
- **Aucun ne gagne de façon démontrée** : verdict « non démontré » partout, et tournoi « non démontré » sur les trois groupes.
- AUC des modèles : 0,62 à 0,69, contre 0,60 à 0,70 pour la seule identité de l'enseignant. Même constat que sur les marchés synthétiques.
- Les deux groupes d'actifs sont corrélés : ce ne sont pas des réplications indépendantes.


## 9. Limites

- **« Meilleurs traders » :**
  - aucune décision horodatée ni aucun fill vérifié d'un vrai trader n'est disponible sous licence dans cette v1 ;
  - les méthodes publiées sont des règles, pas des preuves de rentabilité ;
  - les performances déclarées (Turtles, vidéos) restent inutilisables : biais du survivant, absence de relevés, coûts d'exécution inconnus, comptes et inventaires non comparables.
- **Marchés synthétiques :** ils ne sont pas le marché réel. Un succès sur la règle cachée prouve seulement que le banc *sait* détecter un progrès, pas qu'un tel signal existe sur les cryptomonnaies.
- **Puissance limitée :** environ 333 jours d'évaluation par marché ; une amélioration faible peut passer inaperçue (verdict « non démontré », pas « inexistant »).
- **Données réelles :** sans écart achat/vente historique (hypothèse de 4 bps), avec une conversion USDT → EUR supposée à 5 bps par ordre. Les périodes ont déjà été étudiées par le projet : le résultat est exploratoire. Les deux groupes d'actifs sont corrélés et ne constituent pas des réplications indépendantes.
- **Simplifications :** pas d'exécution partielle ni d'impact de marché ; ATR et indicateurs sur des bougies de 4 h seulement.
- **Windows 11 : NON TESTÉ** (aucun Windows disponible). Le code est en Python pur, avec numpy et scikit-learn, sans dépendance système.

## 9 bis. Tests réellement exécutés

**Environnement :** Linux x86-64, CPython 3.12.3, dépendances épinglées (CCXT 4.4.99 complet), lanceur `tools/security/offline_exec` (seccomp : création de socket refusée, refus vérifié avant chaque suite).

| Exécution | Résultat |
|---|---|
| Tests de #37 avant modification (`tests/test_expert_lab.py`) | 19 réussis |
| Suite complète de la base #37 (`6d990a4`) | **538 réussis, 1 ignoré** |
| Suite complète de cette branche | **666 réussis, 1 ignoré** (+128) |

**Tests ajoutés ou adaptés :**

| Fichier | Tests |
|---|---|
| `tests/test_expert_lab.py` | 41 (19 de #37 adaptés, 22 ajoutés) |
| `tests/test_alpha_core.py` | 60 |
| `tests/test_alpha_council.py` | 27 |
| `tests/test_alpha_velocity.py` | 14 |
| `tests/test_alpha_cli.py` | 5 |

- Le test ignoré, `test_diagnostic.py` (démarrage réel du panel), exige des sockets locales que le filtre interdit ; il existait déjà sur la base.
- Aucun test existant n'a été supprimé ni marqué « ignoré ». Les tests de #37 ont été adaptés comme indiqué au §1.

**Critères P0 de la mission, couverts par des tests :**

| Critère | Comment il est vérifié |
|---|---|
| Fuite de futur, famille caractéristiques | invariance par préfixe de chaque indicateur et de chaque caractéristique ; colonne « fuite » détectée |
| Fuite de futur, famille actualités révisées | révision invisible avant sa publication ; l'agent macro ignore une annonce publiée après la décision |
| Fuite de futur, famille étiquettes | issue disponible strictement après la décision ; purge aux bornes ; décision enregistrée après son issue refusée |
| Fuite de futur, famille source d'expert trompeuse | flux « que des gagnants » auto-déclaré vérifié : rien d'admis ; entrées de catalogue déclarant une performance ou une vérification refusées |
| Zéro ordre possible depuis le Shadow Council | analyse du code de chaque module : aucun import du courtier, des services, de l'API, du réseau ou de `subprocess`, aucun nom d'ordre ; import réel sans chargement d'un module courtier ; objets figés sans méthode |
| Étiquettes de gain sans fill horodaté | refusées : heure, prix et frais exigés (`expert_lab`) ; aucune source du catalogue v1 ne peut étiqueter un gain |
| Bruit sans avantage | ni les concurrents, ni le tournoi ne s'annoncent rentables (tests et mesure officielle) |
| Succès | seule une amélioration répliquée hors échantillon est acceptée ; règle stricte : gain absolu et relatif |

**Hors de portée de cette session :**
- **Windows 11 : NON TESTÉ** (aucun Windows disponible) ;
- navigateur : non concerné, aucune interface ajoutée.

## 9 ter. Prochaines étapes proposées

1. **Protocole confirmatoire à candidat unique**, préenregistré : RegimeRouter contre « toutes les entrées », sur une période **future** (observation prospective, 30 € fictifs, aucun ordre réel), avec une analyse de puissance pour fixer la durée.
2. Retirer ExitLearner et ErrorReplay de la suite des candidats (non justifiés) ; garder le conseil comme **outil d'explication** (désaccords, refus, questions « pourquoi ? ») plutôt que comme décideur, tant qu'aucun agent n'est démontré utile.
3. Pistes du docteur des erreurs : l'abstention pour liquidité a-t-elle trop refusé ? À tester sur un protocole futur, jamais sur les données déjà vues.
4. Sources « verified_fill » : si des décisions horodatées et des fills audités d'un vrai trader deviennent disponibles sous licence, les faire passer par `expert_lab` + `ProvenanceAudit`. Aucune n'existe dans la v1.

## 10. API stable (pour une intégration future avec GPT Work)

Toutes ces fonctions sont hors ligne, sans effet de bord hors du dossier de sortie demandé :

- `sentinelle.ai.expert_lab` : `ExpertDecision`, `ProvenanceAudit`, `admission(decision, cutoff, audit)`, `build_dataset(rows, cutoff=, audits=)`, `visible_snapshot`, `chronological_partitions` (clés `train`, `calibration`, `evaluation`, `purged`) ;
- `sentinelle.alpha.catalogue` : `load_catalogue()`, `Catalogue.can_teach_rules`, `Catalogue.can_label_profit` ;
- `sentinelle.alpha.bars` : `Bars`, `Universe`, `indicators`, `feature_row`, `FEATURES`, `load_history_csv`, `align` ;
- `sentinelle.alpha.teachers` : `TEACHERS`, `TeacherDecision`, `abstention_mask`, `EXIT_VARIANTS` ;
- `sentinelle.alpha.execution` : `CostModel`, `Plan`, `Outcome`, `simulate_trade`, `PortfolioRules`, `run_portfolio` ;
- `sentinelle.alpha.council` : `Proposal`, `CouncilDecision.to_record()`, `build_council`, `run_council`, `AGENTS` ;
- `sentinelle.alpha.ledger` : `ShadowLedger.record_decision`, `record_outcome`, `verify` ;
- `sentinelle.alpha.velocity` : `ChallengeConfig`, `preregister`, `run_challenge` ; `sentinelle.alpha.experiments` : `council_ablation`, `tournament` ;
- commande en ligne : `python -m tools.alpha_factory {catalogue,run,real,report}`.

Pour toute intégration dans le panel, ces objets sont en lecture seule. Le conseil ne doit jamais recevoir un courtier ; s'il le faut, ajouter un test d'import plutôt que d'assouplir la règle.
