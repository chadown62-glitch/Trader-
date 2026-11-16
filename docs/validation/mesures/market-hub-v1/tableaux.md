Capture `capture-1794851893380.json.gz` (empreinte `92611d8fa0d7…`), avantage brut HYPOTHÉTIQUE 100 pb (sert seulement à classer les routes).

#### Décision du hub et coût aller-retour, face au moteur existant (mêmes données)

| Actif | Montant | Décision | Route retenue | Coût estimé (pb) [bas ; haut] | Coût simulé sur les carnets (pb) | Moteur existant (pb) | Écart estimé | Écart simulé |
|---|---|---|---|---|---|---|---|---|
| BTC | 5 € | ROUTE | kraken — BTC/EUR direct | 79,8 [49,9 ; 94,1] | 79,9 | 40,0 | -39,8 | -39,9 |
| BTC | 10 € | ROUTE | binance — BTC/EUR direct | 18,3 [13,7 ; 610,9] | 18,8 | 40,0 | 21,7 | 21,2 |
| BTC | 30 € | ROUTE | binance — BTC/EUR direct | 20,0 [15,1 ; 220,3] | 20,2 | 40,0 | 20,0 | 19,8 |
| ETH | 5 € | ROUTE | binance — EUR → USDT → ETH | 39,8 [30,1 ; 571,3] | 39,5 | 40,0 | 0,2 | 0,5 |
| ETH | 10 € | ROUTE | binance — ETH/EUR direct | 19,7 [14,8 ; 189,1] | 19,7 | 40,0 | 20,3 | 20,3 |
| ETH | 30 € | ROUTE | binance — ETH/EUR direct | 20,1 [15,1 ; 79,2] | 20,1 | 40,0 | 19,9 | 19,9 |
| SOL | 5 € | ROUTE | binance — EUR → USDT → SOL | 41,9 [32,0 ; 381,3] | 41,8 | 40,0 | -1,9 | -1,8 |
| SOL | 10 € | ROUTE | binance — SOL/EUR direct | 21,4 [16,4 ; 89,3] | 21,4 | 40,0 | 18,6 | 18,6 |
| SOL | 30 € | ROUTE | binance — SOL/EUR direct | 21,5 [16,5 ; 46,4] | 21,5 | 40,0 | 18,5 | 18,5 |

#### Meilleure route de chaque type (coût aller-retour estimé, pb)

| Actif | Montant | Type | Route | Coût | Bas | Haut | Remplissage | Reliquat (€) | Latence (s) | Finalité (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 5 € | cex_limit | kraken — BTC/EUR à cours limité | 63,4 | 23,1 | 72,6 | 82% | 0,000 | 0,5 | 0 |
| BTC | 5 € | cex_direct | kraken — BTC/EUR direct | 79,8 | 49,9 | 94,1 | 100% | 0,000 | 0,5 | 0 |
| BTC | 5 € | cex_via_stable | kraken — EUR → USDT → BTC | 160,1 | 100,5 | 180,4 | 100% | 0,000 | 1,0 | 0 |
| BTC | 10 € | cex_limit | binance — BTC/EUR à cours limité | 16,9 | 12,0 | 604,8 | 82% | 0,580 | 0,5 | 0 |
| BTC | 10 € | cex_direct | binance — BTC/EUR direct | 18,3 | 13,7 | 610,9 | 100% | 0,580 | 0,5 | 0 |
| BTC | 10 € | cex_via_stable | binance — EUR → USDC → BTC | 37,9 | 28,7 | 728,2 | 100% | 0,671 | 1,0 | 0 |
| BTC | 30 € | cex_limit | binance — BTC/EUR à cours limité | 18,0 | 12,8 | 213,4 | 82% | 0,560 | 0,5 | 0 |
| BTC | 30 € | cex_direct | binance — BTC/EUR direct | 20,0 | 15,1 | 220,3 | 100% | 0,560 | 0,5 | 0 |
| BTC | 30 € | cex_via_stable | binance — EUR → USDT → BTC | 39,9 | 30,2 | 270,6 | 100% | 0,631 | 1,0 | 0 |
| ETH | 5 € | cex_via_stable | binance — EUR → USDT → ETH | 39,8 | 30,1 | 571,3 | 100% | 0,256 | 1,0 | 0 |
| ETH | 5 € | cex_limit | kraken — ETH/EUR à cours limité | 63,4 | 23,1 | 72,5 | 82% | 0,000 | 0,5 | 0 |
| ETH | 5 € | cex_direct | kraken — ETH/EUR direct | 79,9 | 50,0 | 94,0 | 100% | 0,000 | 0,5 | 0 |
| ETH | 10 € | cex_limit | binance — ETH/EUR à cours limité | 18,2 | 13,0 | 182,6 | 82% | 0,155 | 0,5 | 0 |
| ETH | 10 € | cex_direct | binance — ETH/EUR direct | 19,7 | 14,8 | 189,1 | 100% | 0,155 | 0,5 | 0 |
| ETH | 10 € | cex_via_stable | binance — EUR → USDT → ETH | 40,3 | 30,5 | 306,6 | 100% | 0,246 | 1,0 | 0 |
| ETH | 10 € | dex_funded | base — EUR → USDC → retrait → échange | 840,3 | 27,8 | 4478,8 | 98% | 0,091 | 1803,0 | 900 |
| ETH | 30 € | cex_limit | binance — ETH/EUR à cours limité | 18,6 | 13,3 | 72,7 | 82% | 0,135 | 0,5 | 0 |
| ETH | 30 € | cex_direct | binance — ETH/EUR direct | 20,1 | 15,1 | 79,2 | 100% | 0,135 | 0,5 | 0 |
| ETH | 30 € | cex_via_stable | binance — EUR → USDT → ETH | 40,8 | 30,8 | 130,1 | 100% | 0,206 | 1,0 | 0 |
| ETH | 30 € | dex_funded | base — EUR → USDC → retrait → échange | 295,9 | 21,7 | 1349,8 | 98% | 0,071 | 1803,0 | 900 |
| SOL | 5 € | cex_via_stable | binance — EUR → USDT → SOL | 41,9 | 32,0 | 381,3 | 100% | 0,155 | 1,0 | 0 |
| SOL | 5 € | cex_limit | kraken — SOL/EUR à cours limité | 64,1 | 23,9 | 72,5 | 82% | 0,000 | 0,5 | 0 |
| SOL | 5 € | cex_direct | kraken — SOL/EUR direct | 81,4 | 51,4 | 94,8 | 100% | 0,000 | 0,5 | 0 |
| SOL | 10 € | cex_limit | binance — SOL/EUR à cours limité | 19,1 | 13,9 | 82,1 | 82% | 0,055 | 0,5 | 0 |
| SOL | 10 € | cex_direct | binance — SOL/EUR direct | 21,4 | 16,4 | 89,3 | 100% | 0,055 | 0,5 | 0 |
| SOL | 10 € | cex_via_stable | binance — EUR → USDT → SOL | 41,9 | 32,0 | 216,8 | 100% | 0,145 | 1,0 | 0 |
| SOL | 10 € | dex_funded | solana — EUR → USDC → retrait → échange | 1536,7 | 175,9 | 3744,0 | 95% | 0,144 | 28,0 | 13 |
| SOL | 30 € | cex_limit | binance — SOL/EUR à cours limité | 19,2 | 13,9 | 39,2 | 82% | 0,035 | 0,5 | 0 |
| SOL | 30 € | cex_direct | binance — SOL/EUR direct | 21,5 | 16,5 | 46,4 | 100% | 0,035 | 0,5 | 0 |
| SOL | 30 € | cex_via_stable | binance — EUR → USDT → SOL | 42,2 | 32,2 | 107,3 | 100% | 0,105 | 1,0 | 0 |
| SOL | 30 € | dex_funded | solana — EUR → USDC → retrait → échange | 526,5 | 69,4 | 1332,7 | 95% | 0,124 | 28,0 | 13 |

#### Ressources

- 9 décisions rejouées en 0,34 s, CPU 0,34 s, mémoire Python maximale 0,5 Mo, mémoire du processus 34 Mo ;
- décision en direct (réseau compris) : BTC 5 € 11.2 s, BTC 10 € 12.0 s, BTC 30 € 12.0 s, ETH 5 € 27.2 s, ETH 10 € 26.4 s, ETH 30 € 26.3 s, SOL 5 € 11.3 s, SOL 10 € 11.4 s, SOL 30 € 11.4 s

#### Disponibilité des sources pendant la capture

| Source | Appels | Erreurs | Disponibilité | Latence médiane (ms) |
|---|---|---|---|---|
| api.bitvavo.com | 60 | 0 | 100% | 200 |
| api.exchange.coinbase.com | 78 | 0 | 100% | 79 |
| api.jup.ag | 6 | 0 | 100% | 523 |
| api.kraken.com | 96 | 0 | 100% | 134 |
| api.mainnet-beta.solana.com | 6 | 0 | 100% | 940 |
| data-api.binance.vision | 114 | 0 | 100% | 139 |
| ethereum-rpc.publicnode.com | 51 | 0 | 100% | 94 |
| mainnet.base.org | 57 | 0 | 100% | 47 |
| www.bitstamp.net | 90 | 0 | 100% | 132 |
