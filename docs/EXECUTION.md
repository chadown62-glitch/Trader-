# Contrat d'exécution unique

Mis à jour le 11 novembre 2026.

Sentinelle a trois moteurs qui « exécutent » des trades :

| Moteur | À quoi il sert | Fichier |
|--------|----------------|---------|
| **Étiquetage** | Dire ce qu'aurait donné chaque signal passé : ce sont les exemples sur lesquels l'IA apprend | `sentinelle/ai/labeling.py` |
| **Simulation accélérée** (backtest) | Rejouer des mois de marché avec l'agent, le gestionnaire de risque et le courtier simulé | `sentinelle/backtest/engine.py` |
| **Trading en argent fictif** | Trader en direct sur les prix réels, avec le portefeuille fictif | `sentinelle/execution/paper.py`, `positions.py` |

Si ces moteurs n'appliquaient pas les mêmes règles, l'IA apprendrait une chose et en ferait une autre. Ils appliquent donc **un seul contrat**, dont les formules sont écrites une seule fois dans `sentinelle/execution/contract.py`.

## L'écart qui existait

Avant cette version :
- l'étiquetage achetait à l'**ouverture de la bougie qui suit** le signal ;
- la simulation accélérée achetait à la **clôture de la bougie du signal**, c'est-à-dire au prix même qui avait servi à décider. Elle utilisait un prix qu'on ne peut pas obtenir : il faut avoir vu la clôture pour décider, et l'ordre ne peut partir qu'après ;
- la sortie « durée maximale » se faisait à la clôture de la bougie où elle était décidée, pour la même raison.

C'est corrigé : les trois moteurs exécutent au premier prix disponible **après** la décision.

---

## Les neuf règles

| # | Sujet | Règle |
|---|-------|-------|
| 1 | **Disponibilité d'un signal** | Un signal calculé sur une bougie n'existe qu'à sa **clôture**. Aucune décision n'utilise une donnée postérieure. |
| 2 | **Premier prix disponible** | L'ordre s'exécute au premier prix connu après la décision : l'**ouverture de la bougie suivante** sur l'historique, le **premier relevé de prix** en direct. |
| 3 | **Délai et contraintes** | L'ordre part au moment de la décision. Il est refusé sans prix récent, sans taux de change récent, ou sous les minimums de la plateforme. |
| 4 | **Ordres au marché et à cours limité** | Au marché : premier prix disponible, aggravé du glissement (et du demi-écart du carnet en direct). À cours limité : au prix limite, ou à l'ouverture si elle est plus favorable ; sans glissement. |
| 5 | **Frais et glissement** | Frais sur le montant de chaque ordre (0,10 % par défaut). Glissement sur les ordres au marché et sur les stops (0,05 % par défaut). |
| 6 | **Trous de cotation et stops** | Un stop s'exécute au **pire** du prix du stop et de l'ouverture, glissement compris. Un trou de cotation coûte donc plus que le risque prévu. |
| 7 | **Stop et objectif dans la même bougie** | Le stop est supposé touché **en premier** (hypothèse défavorable). |
| 8 | **Exécution partielle ou refus** | Un ordre est exécuté en entier ou refusé. Si le premier prix disponible est déjà au niveau du stop ou en dessous, l'entrée est **abandonnée**. |
| 9 | **Données manquantes, anciennes ou incohérentes** | Une bougie incohérente (prix nul ou absent, plus haut sous la clôture…) ne déclenche ni décision ni exécution, et elle est comptée. En direct, aucune entrée sur des prix périmés. L'historique d'apprentissage ne contient jamais de trou de cotation de plus de trois jours. |

Deux précisions :
- **Sortie à la durée maximale** : décidée à la clôture d'une bougie, exécutée à l'ouverture de la suivante.
- **Relèvement d'un stop** (point mort, stop suiveur) : décidé à la clôture d'une bougie, actif à partir de la suivante. Si le prix d'ouverture est déjà sous le nouveau stop, la position est vendue à cette ouverture.

---

## Différences assumées entre l'historique et le direct

Elles viennent de la finesse des données, pas des règles.

| Sujet | Sur l'historique (étiquetage, simulation) | En direct (argent fictif) |
|-------|-------------------------------------------|---------------------------|
| Premier prix après la décision | Ouverture de la bougie suivante | Dernier prix relevé (toutes les 5 secondes par défaut), au plus quelques secondes autour de la clôture |
| Ordre des événements dans une bougie | Inconnu : stop d'abord, par prudence | Connu : les relevés arrivent dans l'ordre réel |
| Écart achat/vente | Non disponible : seul le glissement s'applique | Demi-écart lu sur le carnet d'ordres, en plus du glissement |
| Date d'une sortie | Clôture de la bougie où elle a lieu (prudent pour séparer apprentissage et contrôle) | Instant du relevé |
| Monnaie | Monnaie de cotation, coût de conversion ajouté aux frais | Euros, taux de change réel à chaque exécution |
| Limites de risque (perte du jour, série de pertes, exposition) | Appliquées dans la simulation ; absentes de l'étiquetage, qui juge chaque signal isolément | Appliquées |

La dernière ligne explique pourquoi la simulation prend **moins** de trades que l'étiquetage n'en compte : une seule position à la fois par crypto, et les limites de risque.

---

## Preuves

`tests/test_execution_contract.py`, sur un flux artificiel déterministe de 15 000 bougies d'une heure :

| Vérification | Résultat |
|--------------|----------|
| Chaque trade de la simulation accélérée retrouve son exemple étiqueté : même instant d'entrée, même prix (ouverture suivante + glissement), mêmes niveaux, même issue, même résultat en R à 10⁻⁶ près | 85 trades comparés, tous identiques |
| Les sorties couvrent les trois cas : stop, objectif, durée maximale | oui |
| Le trading fictif au fil des prix (un relevé par bougie, puis trois relevés par bougie) donne exactement les trades de la simulation : prix, quantités, frais, résultat, capital final | identique dans les deux cas |
| Les situations suivies en direct, prises ou refusées, reçoivent exactement l'issue de l'étiquetage | plus de 200 comparées, toutes identiques |
| Même passé, avenir remplacé par un autre : les décisions prises dans le passé commun sont identiques, ainsi que les caractéristiques, les signaux et les étiquettes | identiques |
| Un signal donné par la dernière bougie connue n'est ni exécuté ni étiqueté | vérifié |
| Stop et objectif dans la même bougie, trou de cotation sous le stop, entrée abandonnée, bougies incohérentes : même comportement dans l'étiquetage et chez le courtier | vérifié |

Les nombres ci-dessus sont ceux du test ; ils ne mesurent aucune rentabilité.

## Simulation accélérée lancée depuis le panel

Elle tourne maintenant dans les **conditions de votre portefeuille** : son capital (30 € par défaut, converti une fois au taux du moment), les minimums réels de la plateforme et le coût de conversion ajouté aux frais. Vous pouvez saisir un autre capital. Les signaux refusés faute de taille suffisante sont comptés.

Les deux simulations de référence d'un rapport d'entraînement (avec et sans le tri de l'IA) gardent un capital de 10 000 : elles mesurent la qualité des décisions, sans la contrainte des petits montants.

## Limites connues

- Les exécutions partielles ne sont pas simulées.
- L'ordre réel des prix à l'intérieur d'une bougie d'historique est inconnu ; l'hypothèse « stop d'abord » est défavorable, pas exacte.
- Un historique où il manque quelques bougies (moins de trois jours) est traité comme continu : l'entrée se fait à la bougie suivante disponible.
- Les résultats mesurés avant cette version par la simulation accélérée (docs/RESULTATS.md) utilisaient l'ancienne règle d'entrée. Ils n'ont pas été refaits ici ; les résultats « en R » issus de l'étiquetage, eux, utilisaient déjà la bonne règle.
