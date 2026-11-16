# DIRECT MARKET HUB v1 — validation (mission 51)

**SIMULATION — ARGENT FICTIF.** Branche `claude/direct-market-hub-v1`, empilée sur COST OPTIMIZER (#50). Aucun ordre
réel, aucune transaction diffusée, aucune clé, aucun service permanent, aucune fusion. Architecture et audit :
`docs/architecture/direct-market-hub-v1.md`.

## 1. Résumé

Le hub permet de préparer une opération, de comparer ses routes d'exécution aller ET retour, puis de l'exécuter en
simulation sur le portefeuille unique de 30 € fictifs, depuis le panel. Il refuse toute opération non sûre ou non
rentable. Mesuré sur une capture publique du 16/11/2026 (3 actifs × 5, 10 et 30 €), rejouée sans réseau :

1. **10 à 30 €** : la meilleure route est la paire directe en euros (Binance) : **18 à 22 pb aller-retour** ;
   l'exécution simulée sur les carnets capturés confirme l'estimation à 0,5 pb près.
2. **Réduction démontrée sur les mêmes données** : paire en euros contre détour par l'USDT sur la même plateforme,
   **−19,5 à −20,8 pb par aller-retour** (≈ 2 conversions évitées), cohérent avec la mesure indépendante de #50.
3. **Face aux réglages du moteur existant** (40 pb aller-retour pour une paire en USDC depuis l'euro) : 18,5 à
   21,7 pb de moins. Une partie seulement est une économie (la conversion évitée) ; le reste vient de ce que le
   moteur existant compte 5 pb de glissement par ordre là où ≈ 0 est mesuré : c'est une surestimation, pas une économie.
4. **5 €** : le minimum de 5 € de Binance est franchi par l'arrondi de quantité ; le hub se rabat sur Kraken
   (≈ 80 pb, commission hypothétique 0,40 %) ou sur un détour par l'USDT (≈ 40 pb). **Plus cher que ce que suppose
   le moteur existant**, qui ignore ce minimum : à 5 €, mieux vaut ne pas trader.
5. **Réseaux décentralisés** : financés depuis l'euro, **300 à 6 200 pb** à ces montants (retraits et jeton de gaz,
   frais fixes) ; jamais retenus. L'échange lui-même sur Base coûte 1 à 5 pb + 0,001 € de gaz : il ne deviendrait
   compétitif qu'avec des fonds déjà sur la chaîne et des retraits quasi gratuits (hypothèse non vérifiée).
6. **Reliquat** : 10 € de BTC sur Binance laissent ≈ 0,58 € invendables (pas de 0,00001 BTC) ; la valeur reste
   détenue, mais le hub le signale et bascule sur une autre plateforme si l'on retient les bornes hautes.
7. **Aucune stratégie rentable n'est démontrée.** Sans avantage brut annoncé, le hub s'abstient.

## 2. Ce qui est livré

| Élément | Où |
|---|---|
| Verrou « argent fictif » du hub, détecteur de secrets | `sentinelle/dmh/guard.py` |
| Client public à liste blanche (hôtes, chemins, méthodes de lecture), reprises bornées, coupe-circuit, budget | `sentinelle/dmh/net.py` |
| Connecteurs Binance, Kraken, Coinbase, Bitstamp, Bitvavo (carnet, règles, transactions, heure) | `sentinelle/dmh/cex.py` |
| Devis non signés Jupiter (Swap API V2), Uniswap V3 (QuoterV2), frais de réseau, lectures de tokens | `sentinelle/dmh/dex.py`, `abi.py` (keccak-256 en Python pur, vérifié) |
| Identités de tokens (réseau + adresse), imitations, registre vérifié | `sentinelle/dmh/tokens.py`, `research/market-hub-v1/token-registry.json` |
| Observations horodatées, fraîcheur, doublons, consensus | `sentinelle/dmh/observe.py` |
| Vue de marché en direct ou rejouée (reproductible) | `sentinelle/dmh/view.py` |
| Smart Execution Router (aller-retour, niveaux de preuve, fourchettes, abstention) | `sentinelle/dmh/router.py`, `assumptions.py` |
| Moteur de sécurité à veto (financier, tokens, technique ; plafonds absolus) | `sentinelle/dmh/security.py`, `tokenrisk.py` |
| Registre partagé, réservations, journal chaîné, réconciliation, reprise | `sentinelle/dmh/ledger.py` |
| Simulateur CEX/DEX fictif | `sentinelle/dmh/simulate.py` |
| Service, API `/api/dmh`, vue du panel | `sentinelle/dmh/service.py`, `api.py`, `web/js/views/dmh.js` |
| Assistant de stratégie (IA locale, brouillon seulement) | `sentinelle/dmh/llm.py` |
| Adaptateur vers les contrats de #47 (sans fusion) | `sentinelle/dmh/adapters.py` |
| Capture publique bornée et validation hors ligne | `tools/dmh_capture.py`, `docs/validation/mesures/market-hub-v1/` |

## 3. Validation des économies (phase 8)

Méthode : une capture publique bornée (horodatée, hachée) enregistre, scénario par scénario, tout ce que le routeur
lit pour décider (carnets, règles, devis, frais de réseau). La validation la rejoue SANS réseau : décision du hub,
puis exécution simulée de l'entrée et de la sortie sur les carnets capturés, et comparaison avec le moteur existant
sur les mêmes scénarios. Un test vérifie que la validation publiée se reproduit exactement à partir de l'archive.

L'avantage brut de 100 pb ne sert qu'à classer les routes : **ce n'est pas le résultat d'une stratégie**.

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

#### Même plateforme, mêmes carnets : effet du routage (meilleure route de chaque type, pb aller-retour)

| Actif | Montant | Paire directe en euros | Détour EUR → stable → actif | Ordre limité (prudent, rempli 82 %) | Réseau décentralisé financé |
|---|---|---|---|---|---|
| BTC | 5 € | 79,8 (kraken) | 160,1 (kraken) | 63,4 (kraken) | — (BTC enveloppé exclu) |
| BTC | 10 € | 18,3 (binance) | 37,9 (binance) | 16,9 (binance) | — |
| BTC | 30 € | 20,0 (binance) | 39,9 (binance) | 18,0 (binance) | — |
| ETH | 5 € | 79,9 (kraken) | 39,8 (binance) | 63,4 (kraken) | impossible (minimum) |
| ETH | 10 € | 19,7 (binance) | 40,3 (binance) | 18,2 (binance) | 840,3 (Base) |
| ETH | 30 € | 20,1 (binance) | 40,8 (binance) | 18,6 (binance) | 295,9 (Base) |
| SOL | 5 € | 81,4 (kraken) | 41,9 (binance) | 64,1 (kraken) | impossible (minimum) |
| SOL | 10 € | 21,4 (binance) | 41,9 (binance) | 19,1 (binance) | 1 536,7 (Solana) |
| SOL | 30 € | 21,5 (binance) | 42,2 (binance) | 19,2 (binance) | 526,5 (Solana) |

Lecture :

- **Taux de remplissage** : 100 % des ordres au marché simulés sur les carnets capturés ; ordres limités : 82 %
  (fourchette 78–86 %, mesure de #50). Ils paraissent moins chers, mais leur avantage n'a pas été démontré par #50
  et leurs non-exécutions coûtent l'occasion manquée (`(1 − 0,82) × (avantage − coût)`) : jamais retenus d'office.
- **Robustesse** : à 10–30 €, le choix de Binance dépend du reliquat (borne haute 220 à 611 pb pour le BTC) ; aux
  bornes hautes, Bitvavo (8 décimales, aucun reliquat, commission hypothétique 0,25 %) deviendrait préférable : le
  hub le signale (« choix sensible aux hypothèses »).
- **Latence** : décision en direct 11 à 27 s (limites de débit publiques, Jupiter sans clé à 0,5 requête/s) ;
  rejouée : 9 décisions en 0,34 s, CPU 0,34 s, mémoire Python maximale 0,5 Mo, mémoire du processus 34 Mo.
  Latence d'exécution attendue : 0,5 s sur une plateforme ; finalité 13 s (Solana) à 15 min (Base, publication sur
  Ethereum).
- **Disponibilité des données** pendant la capture : 100 % sur les 9 sources sollicitées. Pendant le développement :
  Bitvavo refusait la paire USDT/EUR (HTTP 400, retirée), CoinGecko limitait le débit (HTTP 429, délai respecté),
  et le contrôle d'horloge a d'abord refusé les carnets de Bitvavo (heure en nanosecondes, convertie ensuite).
- **Rejets de sécurité** : aucun sur les 9 routes retenues de la validation ; les refus sont vérifiés par les 20
  scénarios de résistance. En direct, les premières prévisualisations ont été refusées pour carnets trop vieux
  (calcul de 20 à 45 s) : la route retenue est désormais relue juste avant le contrôle.
- **Risque opérationnel** : dépendance à des sources publiques tierces (une panne rend une route impossible,
  jamais « sûre ») ; commissions et retraits hypothétiques.
- **Impact sur le portefeuille** : 52 allers-retours de 10 € par an coûtent ≈ 0,10 € avec la meilleure route, contre
  ≈ 0,21 € selon les réglages du moteur existant et ≈ 0,23 € pour le détour réel par l'USDT mesuré par #50 : un
  écart de l'ordre de 0,1 € par an sur 30 €, à comparer à l'avantage brut qu'aucune stratégie n'a encore démontré.

## 4. Ce que ce travail ne démontre pas

- **Aucune rentabilité de stratégie n'est démontrée** (priorité n° 1 de la mission) : le hub mesure et réduit les
  coûts, il ne crée pas d'avantage. Sans avantage annoncé, il s'abstient. L'évaluation unique d'ALPHA FACTORY v2
  attend toujours la validation du contrat de coûts de #50.
- Les commissions des plateformes sont des **hypothèses** (aucune grille lisible sans compte) ; les choix sont
  vérifiés face à leurs fourchettes et signalés quand ils en dépendent.
- Les frais de retrait (financement d'une route sur la chaîne) sont des hypothèses : les routes décentralisées
  financées depuis l'euro en dépendent entièrement.
- L'exécution est simulée sur des carnets observés à un instant : pas de file d'attente, pas de réaction du
  marché à l'ordre ; la sortie future est estimée sur le carnet présent, bornée par les écarts de tension de #50.

## 5. Tests

| Fichier | Contenu | Résultat |
|---|---|---|
| `tests/test_dmh_resistance.py` | les 20 scénarios obligatoires (31 cas) | réussis |
| `tests/test_dmh_components.py` | keccak/ABI, client public, observations, identités, routeur, propriétés du registre (8 séquences aléatoires), IA locale, rejeu, adaptateur #47 | 25 réussis |
| `tests/test_dmh_concurrency.py` | 12 fils et 4 processus sur les mêmes 30 €, partage avec le broker existant, charge (300 décisions, 400 opérations), sécurité statique, dépendances | 8 réussis |
| `tests/test_dmh_api.py` | application complète : authentification, CSRF, 7 contournements du verrou par HTTP (403), plafonds, parcours de bout en bout sur le portefeuille principal, panel | 11 réussis |
| `tests/test_dmh_validation.py` | la validation publiée se reproduit exactement depuis l'archive ; estimation et simulation cohérentes | 2 réussis |
| `tests/e2e/dmh_check.js` | navigateur Chromium, 1440 px et 390 px : bandeau, navigation, refus sans avantage, limites, observatoire, aucun défilement horizontal, aucune erreur JavaScript | réussi |

Suite complète, environnement isolé sans réseau (CPython 3.12.3) : voir la PR. Windows 11 : non testé.

## 6. Limites et risques résiduels

- Commissions et frais de retrait hypothétiques (fourchettes affichées, choix sensibles signalés).
- Prévisualisation en direct lente (11 à 30 s) : limites de débit publiques (Jupiter sans clé : 0,5 requête/s),
  appels séquentiels aux nœuds publics ; la route retenue est relue juste avant le contrôle de sécurité.
- Nœuds publics et services de contrôle des tokens tiers : leur indisponibilité rend la route impossible, jamais
  « sûre ».
- Liste des places d'échange connues sur Solana arrêtée à la main (`assumptions.KNOWN_SOLANA_AMMS`) : une place
  inconnue bloque la route (refus prudent, parfois excessif).
- Valorisation des positions du hub dans le tableau de bord : dernière valeur prudente (24 h), sinon coût d'achat.
- L'IA locale propose seulement des brouillons ; aucune exécution autonome.
- Windows 11 : non testé (le panel a été vérifié dans Chromium en 1440 px et 390 px).
- Les PR #46 et #47 ne sont pas fusionnées : l'adaptateur vers #47 reproduit ses règles, sans importer son code.

## 7. Reproduire

```
python -m pytest tests/test_dmh_resistance.py tests/test_dmh_components.py tests/test_dmh_concurrency.py \
                 tests/test_dmh_api.py tests/test_dmh_validation.py -q          # sans réseau
python -m tools.dmh_capture validate --capture docs/validation/mesures/market-hub-v1/capture-1794851893380.json.gz
python tests/e2e/run.py dmh_check.js                                              # instance de démonstration jetable
python -m tools.dmh_tokens build          # réseau public : reconstruit le registre vérifié (nouvelle empreinte)
python -m tools.dmh_capture capture --out DOSSIER   # réseau public, borné : nouvelle capture
```
