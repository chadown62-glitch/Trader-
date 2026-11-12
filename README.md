# Sentinelle

Bot de trading crypto **prudent**, avec un panel de gestion complet (PC et téléphone), une IA qui apprend en argent fictif, et le pilotage par Telegram.

> 🔒 **Simulation uniquement.** Cette version ne peut ni envoyer, ni modifier, ni annuler un ordre réel : les cours sont réels, l'argent est toujours fictif. Ce n'est pas un réglage, le code d'exécution réelle a été retiré. Détails et preuves : **[docs/SECURITE.md](docs/SECURITE.md)**.

> **Avertissement.** Le trading comporte un risque de perte en capital. Aucun bot ne peut garantir des gains, et les résultats passés, réels ou simulés, ne préjugent pas des résultats futurs.
> Ce logiciel ne constitue ni un conseil financier, ni un conseil fiscal.

## Ce que vous pouvez faire

- **Trader vous-même** sur des graphiques en temps réel :
  - en **argent fictif** sur les prix réels, avec un **portefeuille unique de 30 €** tenu en euros et partagé avec l'IA ([docs/PORTEFEUILLE.md](docs/PORTEFEUILLE.md)) ;
  - avec un **calculateur de taille selon le risque**, un stop-loss et un objectif visibles sur le graphique ;
  - avec **l'avis de l'IA** sur chaque crypto.
- **Entraîner l'IA** :
  - sur des années d'historique, avec une validation honnête sur une période qu'elle n'a jamais vue ;
  - **vérifier qu'elle sait apprendre** : un banc d'essai lui cache des règles dans des marchés artificiels et mesure ce qu'elle retrouve ;
  - puis **en direct, 24 h/24, avec de l'argent fictif**. Chaque décision, prise ou refusée, est expliquée, et chaque signal est suivi jusqu'à son issue.
- **Suivre le bulletin de fiabilité** de l'IA : modèle validé, 30 jours de résultats en direct, gains nets, baisse contenue. Il mesure des preuves en argent fictif et n'ouvre aucun mode réel.
- **Page « Performances »** : résultat net par trade et par jour (jours sans trade compris), réalisé et latent séparés, baisse maximale, résultats par stratégie, signaux refusés et leurs raisons. Aucune projection, aucun chiffre « annualisé ».
- **Simulation accélérée** : rejouer des mois de marché en quelques secondes, avec exactement les mêmes règles que l'apprentissage et le trading fictif, dans les conditions de votre portefeuille ([docs/EXECUTION.md](docs/EXECUTION.md)).
- **Fiches cryptos** : tout ce que Sentinelle sait de chaque crypto suivie (ampleur normale des mouvements, pires chutes, lien avec le Bitcoin, liquidité, résultats passés de chaque stratégie).
- **Canaux de signaux Telegram** lus en **observation** : Sentinelle mesure la vraie fiabilité de chaque canal, sur les vrais prix.
- **Contexte de marché** (Fear & Greed, indices boursiers, taux de financement) et **calendrier économique automatique** : pas de nouvelle position autour des annonces importantes (inflation, emploi, Fed, BCE).
- **Bouclier temps réel** : pas de nouvelle position pendant un mouvement brutal, une cascade de liquidations, un carnet d'ordres trop mince, ou juste après une actualité majeure.
- **Telegram** : alertes, résumé quotidien et commandes (`/etat`, `/positions`, `/gains`, `/pause`, `/urgence`…), réservés à votre compte.
- **Actualités** analysées par une **IA locale gratuite** (Ollama, sur votre carte graphique) : médias crypto, communiqués officiels (Fed, BCE, SEC) et, en option, canaux Telegram de dépêches lus en quelques secondes. Elles servent de « garde du corps » qui bloque les achats en cas de très mauvaise nouvelle confirmée par deux sources.

## Sécurité intégrée

- **Gestionnaire de risque** avec droit de veto :
  - 0,5 % du capital risqué par trade (plafond absolu de 2 %) ;
  - limites de perte par jour et par semaine ;
  - **arrêt complet** à −10 % depuis le plus haut ;
  - pause après une série de pertes ;
  - **aucun levier**.
- **Verrou « simulation uniquement »** à toutes les couches (courtier, client de marché, comptes, positions, agents, API, Telegram), sans interrupteur. Aucune clé de plateforme d'échange n'est demandée, lue ou utilisée.
- **Panel** protégé par mot de passe, résistant aux requêtes forgées, jamais exposé sur internet.
- **Bouton 🛑 Urgence** : arrêter les IA, ou tout revendre.
- **Vérification des prix** sur une seconde plateforme avant chaque entrée.
- **Double authentification** facultative sur le panel.
- **Sauvegarde quotidienne** de la base, et alerte externe si le PC s'arrête.

## Démarrage rapide (Windows)

1. Installez **Python 3.12** depuis [python.org](https://www.python.org/downloads/) en cochant « Add python.exe to PATH ».
2. Téléchargez ce dépôt (Code → Download ZIP) et extrayez-le.
3. Double-cliquez sur **`lancer.bat`**. Le panel s'ouvre sur http://127.0.0.1:8765.

Autres lanceurs :
- **`demo.bat`** : démo hors-ligne sur un marché simulé.
- **`diagnostic.bat`** : vérification de l'installation.
- Mac et Linux : `./lancer.sh`.

Mode d'emploi complet : **[docs/GUIDE.md](docs/GUIDE.md)**. Ce qu'il reste à faire : **[docs/PLAN.md](docs/PLAN.md)**. Sources d'information retenues et écartées : **[docs/SOURCES.md](docs/SOURCES.md)**.

## Résultats mesurés

Les résultats, y compris les mauvais, sont dans **[docs/RESULTATS.md](docs/RESULTATS.md)**. En résumé, sur données réelles et frais compris :
- les stratégies de court terme perdent de l'argent ; le **suivi de tendance en 4 h** garde un léger avantage ;
- exemple : un croisement de moyennes « EMA 9/21 » en 1 minute fait environ 1 000 trades par mois et perd **80 à 89 %** en un mois une fois les frais payés ;
- sur 2018-2022 (années jamais utilisées pour concevoir les stratégies), le suivi de tendance est **positif** (+0,33 R par trade en 4 h) ;
- sur l'année écoulée, le système a fait **−2,5 %** pendant que le marché (8 cryptos) faisait **−45,6 %** : **il protège le capital, il n'enrichit pas** ;
- sur 25 autres cryptos liquides, les mêmes stratégies **perdent** (−0,06 à −0,09 R par trade) : l'avantage est concentré sur les plus grandes cryptos, et ajouter des cryptos ne multiplie pas les gains ;
- **l'IA sait apprendre** : sur le banc d'essai, elle retrouve en moyenne 86 % d'une règle cachée (75 % avec l'ancien modèle) et ne « découvre » rien dans du bruit pur ;
- **le vrai marché contient très peu d'information stable** : même avec 475 000 exemples, un modèle fait à peine mieux que le hasard sur la période suivante ;
- le tri de l'IA aide tout de même : sur 18 mois jamais vus, **+0,19 R par trade contre +0,02 R sans tri**, portefeuille simulé à +6,6 % pendant que le marché perdait 43 % ;
- ce n'est pas encore prouvé à 95 % : l'IA est donc **« non validée »**, son modèle est **« à l'essai » en argent fictif**.

## Pour les développeurs

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest            # brokers, IA, risque, API, flux temps réel, Telegram, actualités, verrou de sécurité
python -m sentinelle --demo # panel sur un marché simulé
node tests/e2e/ui_check.js  # vérification du panel dans un navigateur (Playwright)
```

| Dossier | Contenu |
|---------|---------|
| `sentinelle/market` | Données de marché (Binance, marché simulé), historique, centrale temps réel |
| `sentinelle/safety.py` | Verrou « simulation uniquement » (refus inconditionnels, journal des tentatives) |
| `sentinelle/execution` | Courtier simulé, gestion des positions (l'ancien courtier réel n'est plus qu'une butée qui refuse tout) |
| `sentinelle/ai` | Indicateurs, features, stratégies, étiquetage, méta-modèle, entraînement walk-forward |
| `sentinelle/risk` | Gestionnaire de risque |
| `sentinelle/agent` | Agent de trading (commun à l'entraînement en direct et aux backtests) |
| `sentinelle/backtest` | Simulation accélérée |
| `sentinelle/services` | Application, moteur temps réel, comptes, entraînement, bulletin de fiabilité |
| `sentinelle/api` | Serveur web sécurisé et WebSocket |
| `sentinelle/news`, `sentinelle/notify` | Actualités (Ollama) et Telegram |
| `web/` | Panel (Vue 3 et Lightweight Charts, sans compilation) |

Dossier de conception : [docs/CONCEPTION.md](docs/CONCEPTION.md).

Graphiques : TradingView Lightweight Charts™ (Apache 2.0), Copyright © TradingView, Inc. — [tradingview.com](https://www.tradingview.com/).
