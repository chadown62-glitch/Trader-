# ALPHA FACTORY v2 — recherche de rentabilité après l'évaluation unique (mission 55)

> **Simulation, argent fictif.** Aucun ordre réel, aucun service permanent, aucune promotion. Le candidat retenu reste
> FIGÉ : rien de ce qui suit ne modifie sa règle, ses paramètres, ses coûts ou le verdict de la PR #54.

## 1. Point de départ

L'évaluation unique (PR #54) a retenu **un** candidat sur 15 : `mom_ts_28`. C'est un momentum de 4 semaines sur
BTC et ETH, révisé chaque dimanche : on détient chaque actif dont le rendement des 28 derniers jours est positif.
Ce verdict ne vaut que pour l'historique, et la fenêtre 2020-2026 est désormais consultée. La seule façon honnête de
confirmer cet avantage est donc d'observer des journées que personne n'a encore vues. M55 prépare cette observation
et décrit d'où venait l'avantage historique.

## 2. Protocole prospectif `alpha-factory-v2-p1` (enregistré avant toute observation)

| Élément | Valeur |
|---|---|
| Empreinte | `bb162f0d52c5472a4fc0386da5d97bdf09213a612d21ad41de122d66d5df3aad` (commit `2e9e483`, poussé le 17/11/2026) |
| Source | plan `alpha-factory-v2-e1` `6f4f436f…`, résultats `278f320e…` |
| Données | bougies journalières publiques BTCUSDT et ETHUSDT ; l'historique jusqu'au 12/11/2026 doit reproduire **à l'octet près** l'archive de l'évaluation, sinon refus |
| Première décision | clôture du dimanche **22/11/2026** ; premier jour évalué : 23/11/2026 |
| Exécution et coûts | identiques à l'évaluation : ordres au marché à l'ouverture, contrat #50 (commission 10 pb = hypothèse non vérifiée), modèle prudent de reliquats, règles de marché figées, 30 unités fictives |
| Critère principal | écart quotidien moyen face à la détention de BTC+ETH à la même exposition moyenne, test unilatéral, erreur standard par bootstrap par blocs de 7 jours |
| Regards | un par an (365 jours), 9 au plus ; bornes d'O'Brien-Fleming (risque total 5 %) |
| Confirmation | borne franchie **et** résultat net > 0 **et** résultat net > 0 en tension |
| Arrêt pour risque | baisse maximale > 50 % depuis le début |
| Futilité | arrêt recommandé (non contraignant) si l'écart est négatif à partir du 2e regard |
| Entre deux regards | chiffres descriptifs seulement, sans valeur de décision |

**Bornes en score z, regard par regard (années 1 à 9)** : 5,76 · 4,00 · 3,19 · 2,74 · 2,43 · 2,21 · 2,04 · 1,90 · 1,79.

**Durée réaliste.** Probabilité cumulée de confirmer, selon l'avantage réel. C'est une approximation gaussienne
dimensionnée sur l'historique, donc indicative.

| Avantage réel | Année 3 | Année 5 | Année 7 | Année 9 |
|---|---|---|---|---|
| celui de 2020-2026 (+0,097 %/jour) | 11 % | 55 % | 85 % | 96 % |
| celui de la période 2 seulement (+0,055 %/jour) | 4 % | 29 % | 58 % | 78 % |
| aucun | 0,07 % | 0,9 % | 2,6 % | 5,0 % |

Il faut donc s'attendre à **plusieurs années** avant une conclusion. Une conclusion rapide serait elle-même suspecte.

**Utilisation** (commandes à exécution finie, lancées à la main) :

```
python -m tools.alpha_prospective fetch   --root data/alpha-prospective/AAAA-MM-JJ   # 2 paires publiques, ≤ 10 requêtes
python -m tools.alpha_prospective observe --root data/alpha-prospective/AAAA-MM-JJ   # inscrit les décisions, affiche l'état
python -m tools.alpha_prospective verify                                             # protocole, coûts, code intacts ?
```

Le journal `research/alpha-factory-v2/prospective-p1/journal.jsonl` est chaîné et en ajout seul. Une décision déjà
inscrite qui changerait, à cause de données ou de code modifiés, fait refuser l'observation.

## 3. D'où venait l'avantage historique (descriptif, fenêtre consultée)

Tableaux complets : `docs/validation/mesures/alpha-factory-v2-prospective/diagnostic.md`. Ils sont régénérés à
l'identique depuis l'archive, et le rejeu reproduit exactement le résultat publié.

- **Régimes** : l'essentiel vient des marchés **haussiers** (+2 496 % contre +1 199 % pour BTC+ETH) et **agités**
  (+1 770 % contre +582 %). En marché baissier, la règle reste peu exposée (36 %) et limite la casse : +129 % contre
  +14 %. En volatilité moyenne, l'écart est presque nul (+0,011 %/jour).
- **Entrées et sorties** : 75 épisodes, dont seulement 48 % sont gagnants ; l'épisode médian perd 0,7 %. Les gains
  viennent de quelques longues tendances : rapport gain/perte de 7,9, et les 5 meilleurs épisodes font 61 % des gains.
  C'est le profil classique du suivi de tendance : beaucoup de petites pertes, rares grandes hausses, longues périodes
  ingrates. À la sortie, 55 % des meilleures hausses atteintes sont conservées. Après une sortie, le prix monte en
  moyenne de 3 % sur 28 jours : les sorties ne sont pas mieux placées qu'au hasard (49 % suivies d'une baisse).
- **Coûts, délai, liquidité** : la règle reste positive à 60 pb de commission par ordre (+1 169), avec un jour de
  retard (+1 724) et avec un minimum de 10 (+1 738) ; la commission d'équilibre dépasse 200 pb. Un ordre de 15 USDT
  représente environ 10⁻⁸ du volume quotidien de BTC : aucun impact de marché. Le délai à l'intérieur d'une journée
  n'est pas mesurable avec des bougies journalières.
- **Taille et risque** : pleine taille, baisse maximale 38 %. Pour tenir la limite de 10 % du projet, il faudrait
  26 % de la taille (≈ +19 %/an, approché). Mais sur 30 € cela ferait des ordres de 3,95 € par actif, **impossibles**
  avec le minimum de 10 € du hub : il faudrait environ 76 € de capital. Avec 30 €, la taille la plus prudente
  réalisable correspond à une limite de baisse d'environ 30 % (ordres de 11,86 €).

## 4. Pourquoi les 14 autres échouent sur 30 € (pour orienter la suite)

- **Les reliquats coûtent plus que les frais** pour 9 règles : pas de quantité grossiers des petites cryptos et
  commission prélevée dans l'actif reçu. Le modèle prudent, choisi d'avance, compte ces reliquats comme perdus.
- **9 règles ne peuvent passer aucun ordre** avec un minimum de 10 € : 30 € répartis sur 3 à 5 lignes.
- **4 règles n'ont aucun avantage, même sans coûts** (retour à la moyenne, value, momentum double).

## 5. Prochaines expériences proposées (aucune n'est lancée ici)

1. **Observer** `alpha-factory-v2-p1` chaque semaine, à la main, sans rien changer.
2. **Exécution des petits comptes** (descriptif d'abord, sur données d'entraînement seulement) : calibrer chaque achat
   pour que la quantité reçue après commission soit un multiple du pas, ou payer la commission dans un autre jeton.
   On mesurerait le gain sur les reliquats. Toute règle qui en dépendrait exigerait une nouvelle évaluation sur des
   données **futures**.
3. **Taille selon le risque** : une version de `mom_ts_28` à taille réduite serait une NOUVELLE règle. Elle ne pourrait
   être évaluée que prospectivement, par un protocole distinct, jamais en remplacement de celui-ci.

Aucune de ces pistes ne réutilise la fenêtre 2020-01-01 → 2026-11-12 comme donnée vierge.
