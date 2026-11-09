# Sentinelle

Bot de trading crypto **prudent**, avec un panel de gestion complet (PC et téléphone), une IA qui apprend en argent fictif, et le pilotage par Telegram.

> **Avertissement.** Le trading comporte un risque de perte en capital. Aucun bot ne peut garantir des gains, et les résultats passés, réels ou simulés, ne préjugent pas des résultats futurs.
> Ce logiciel ne constitue ni un conseil financier, ni un conseil fiscal.

## Ce que vous pouvez faire

- **Trader vous-même** sur des graphiques en temps réel :
  - en **argent fictif** sur les prix réels (comptes « papier »), ou en **argent réel** sur votre exchange ;
  - avec un **calculateur de taille selon le risque**, un stop-loss et un objectif visibles sur le graphique ;
  - avec **l'avis de l'IA** sur chaque crypto.
- **Entraîner l'IA** :
  - sur des années d'historique, avec une validation honnête sur une période qu'elle n'a jamais vue ;
  - puis **en direct, 24 h/24, avec de l'argent fictif**. Chaque décision, prise ou refusée, est expliquée, et chaque signal est suivi jusqu'à son issue.
- **Lancer l'IA sur votre portefeuille réel**, seulement quand le bulletin de préparation est au vert : modèle validé, 30 jours de résultats en direct, gains nets, baisse contenue.
- **Simulation accélérée** : rejouer des mois de marché en quelques secondes, avec exactement les mêmes règles.
- **Telegram** : alertes, résumé quotidien et commandes (`/etat`, `/positions`, `/gains`, `/pause`, `/urgence`…), réservés à votre compte.
- **Actualités** analysées par une **IA locale gratuite** (Ollama, sur votre carte graphique). Elles servent de « garde du corps » qui bloque les achats en cas de très mauvaise nouvelle confirmée.

## Sécurité intégrée

- **Gestionnaire de risque** avec droit de veto :
  - 0,5 % du capital risqué par trade (plafond absolu de 2 %) ;
  - limites de perte par jour et par semaine ;
  - **arrêt complet** à −10 % depuis le plus haut ;
  - pause après une série de pertes ;
  - **aucun levier**.
- **Stop-loss posé chez l'exchange** : il protège même si le PC s'éteint. Les positions sont réconciliées avec l'exchange au redémarrage, et un stop manquant est reposé.
- **Clés API** stockées uniquement sur votre PC. Utilisez-les **sans droit de retrait**.
- **Panel** protégé par mot de passe, résistant aux requêtes forgées, jamais exposé sur internet.
- **Bouton 🛑 Urgence** : arrêter les IA, ou tout revendre.

## Démarrage rapide (Windows)

1. Installez **Python 3.12** depuis [python.org](https://www.python.org/downloads/) en cochant « Add python.exe to PATH ».
2. Téléchargez ce dépôt (Code → Download ZIP) et extrayez-le.
3. Double-cliquez sur **`lancer.bat`**. Le panel s'ouvre sur http://127.0.0.1:8765.

Autres lanceurs :
- **`demo.bat`** : démo hors-ligne sur un marché simulé.
- **`diagnostic.bat`** : vérification de l'installation.
- Mac et Linux : `./lancer.sh`.

Mode d'emploi complet : **[docs/GUIDE.md](docs/GUIDE.md)**.

## Résultats mesurés

Les résultats, y compris les mauvais, sont dans **[docs/RESULTATS.md](docs/RESULTATS.md)**. En résumé, sur données réelles et frais compris :
- les stratégies de court terme perdent de l'argent ; le **suivi de tendance en 4 h** garde un léger avantage ;
- sur l'année écoulée, le système a fait **−2,5 %** pendant que le marché (8 cryptos) faisait **−45,6 %** : **il protège le capital, il n'enrichit pas** ;
- l'IA est aujourd'hui **« non validée »**, donc elle s'entraîne en argent fictif et le mode réel reste verrouillé.

## Pour les développeurs

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest            # 77 tests : brokers, IA, risque, API, flux temps réel, Telegram, actualités
python -m sentinelle --demo # panel sur un marché simulé
node tests/e2e/ui_check.js  # vérification du panel dans un navigateur (Playwright)
```

| Dossier | Contenu |
|---------|---------|
| `sentinelle/market` | Données de marché (Binance, marché simulé), historique, centrale temps réel |
| `sentinelle/execution` | Brokers simulé et réel (CCXT), gestion des positions |
| `sentinelle/ai` | Indicateurs, features, stratégies, étiquetage, méta-modèle, entraînement walk-forward |
| `sentinelle/risk` | Gestionnaire de risque |
| `sentinelle/agent` | Agent de trading (commun à l'entraînement, au réel et aux backtests) |
| `sentinelle/backtest` | Simulation accélérée |
| `sentinelle/services` | Application, moteur temps réel, comptes, entraînement, bulletin de préparation |
| `sentinelle/api` | Serveur web sécurisé et WebSocket |
| `sentinelle/news`, `sentinelle/notify` | Actualités (Ollama) et Telegram |
| `web/` | Panel (Vue 3 et Lightweight Charts, sans compilation) |

Dossier de conception : [docs/CONCEPTION.md](docs/CONCEPTION.md).

Graphiques : TradingView Lightweight Charts™ (Apache 2.0), Copyright © TradingView, Inc. — [tradingview.com](https://www.tradingview.com/).
