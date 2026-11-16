# Durcissement du hub (mission 52) — validation

**SIMULATION — ARGENT FICTIF.** Branche `claude/hub-hardening-v1`, empilée sur #51 (`claude/direct-market-hub-v1`),
révision traçable du hub. Aucune donnée réseau nouvelle : la validation rejoue la capture archivée de la mission 51
(`docs/validation/mesures/market-hub-v1/capture-1794851893380.json.gz`, empreinte `92611d8f…`) avec les règles de la
mission 52. Aucun ordre réel, aucune clé, aucun service permanent, aucune fusion.

## 1. Résumé

| Point de la mission | Ce qui change | Preuve |
|---|---|---|
| 1. Validation clarifiée | La validation publie séparément **A. le parcours utilisateur** (limites par défaut, portefeuille neuf de 30 €) et **B. la mesure de coût** (portefeuille de mesure isolé, limites désactivées). Refus au-delà des plafonds prouvés de bout en bout (service, API de l'application complète). | `tests/test_dmh_hardening.py`, `tests/test_dmh_api.py::test_caps_are_enforced_end_to_end_through_the_api`, `tests/test_dmh_validation.py` |
| 2. Reliquats | Le capital immobilisé (reliquats, provision de gaz, dépôts) entre dans le coût économique de chaque route, **compté comme perdu** (règle prudente) ; affichage séparé des frais payés, de la valeur des reliquats et du capital disponible ; **refus d'une route qui immobilise plus de 1 % du montant** (plafond réglable jusqu'à 5 %). | idem, vue du panel vérifiée dans Chromium |
| 3. Coûts incertains | Chaque route est chiffrée dans 4 scénarios (central, commissions basses, commissions hautes, défavorable) ; **la route retenue est la meilleure dans le scénario défavorable**, pas la moins chère au prix central ; abstention si le net central est ≤ 0 ou le net défavorable < 0 ; toute décision qui change selon les commissions non vérifiées, ou dont le coût vient surtout d'elles, est signalée. | `test_the_cheapest_route_at_central_prices_is_not_chosen_when_it_is_fragile`, `test_abstention_when_profit_exists_only_at_central_costs` |
| 4. Petits ordres | Refus par défaut sous **10 €** ; seuil économique calculé par plateforme et par actif. | tableau D ci-dessous, `test_economic_minimum_per_venue_and_asset` |
| 5. Portefeuille partagé | Réconciliation croisée avec le grand livre du broker principal (euros du hub = mouvements `hub_*` au centime près, réservations vues des deux côtés), concurrence broker/hub, pertes et limite journalière, redémarrage de l'application, erreurs de valorisation. | `tests/test_dmh_portfolio.py` |

Règles **préétablies**, fixées par cette mission à partir de principes de prudence (et non ajustées sur un résultat de
stratégie) : immobilisation ≤ 1 % ; choix au scénario défavorable ; abstention si le net central ≤ 0 ou le net
défavorable < 0 ; minimum 10 €.

## 2. Résultats (capture archivée rejouée, même données que la mission 51)

Capture `capture-1794851893380.json.gz` (empreinte `92611d8fa0d7…`), avantage brut HYPOTHÉTIQUE 100 pb (sert seulement à classer les routes).

#### A. Parcours utilisateur : limites par défaut appliquées, portefeuille neuf de 30 €

| Actif | Montant | Issue | Route | Coût (pb) | Immobilisé | Motifs |
|---|---|---|---|---|---|---|
| BTC | 5 € | **REFUS DE SÉCURITÉ** | kraken — BTC/EUR direct | 79,9 | 0,00 % | montant 5.00 € inférieur au minimum 10.00 € |
| BTC | 10 € | **AUTORISÉE** | bitvavo — BTC/EUR direct | 50,4 | 0,00 % | — |
| BTC | 30 € | **REFUS DE SÉCURITÉ** | bitvavo — BTC/EUR direct | 50,2 | 0,00 % | position 30.00 € > limite 10.00 € ; exposition sur BTC 30.00 € > limite 10.00 € ; la réserve de liquidités tomberait sous 6.00 € ; exposition totale 100 % > 80 % |
| ETH | 5 € | **REFUS DE SÉCURITÉ** | kraken — ETH/EUR direct | 79,9 | 0,00 % | montant 5.00 € inférieur au minimum 10.00 € |
| ETH | 10 € | **AUTORISÉE** | bitvavo — ETH/EUR direct | 50,2 | 0,00 % | — |
| ETH | 30 € | **REFUS DE SÉCURITÉ** | bitvavo — ETH/EUR direct | 50,0 | 0,00 % | position 30.00 € > limite 10.00 € ; exposition sur ETH 30.00 € > limite 10.00 € ; la réserve de liquidités tomberait sous 6.00 € ; exposition totale 100 % > 80 % |
| SOL | 5 € | **REFUS DE SÉCURITÉ** | kraken — SOL/EUR direct | 81,4 | 0,00 % | montant 5.00 € inférieur au minimum 10.00 € |
| SOL | 10 € | **AUTORISÉE** | bitvavo — SOL/EUR direct | 50,1 | 0,00 % | — |
| SOL | 30 € | **REFUS DE SÉCURITÉ** | binance — SOL/EUR direct | 33,0 | 0,12 % | position 30.00 € > limite 10.00 € ; exposition sur SOL 30.00 € > limite 10.00 € ; la réserve de liquidités tomberait sous 6.00 € ; exposition totale 100 % > 80 % |

#### B. Mesure de coût : portefeuille de mesure isolé, limites de l'utilisateur NON appliquées

Ces chiffres comparent des routes ; ils ne disent pas qu'une opération de ce montant serait autorisée (voir A).

| Actif | Montant | Décision | Route retenue | Coût estimé (pb) [bas ; haut] | Coût simulé sur les carnets (pb) | Moteur existant (pb) | Écart estimé | Écart simulé |
|---|---|---|---|---|---|---|---|---|
| BTC | 5 € | ROUTE | kraken — BTC/EUR direct | 79,9 [49,9 ; 94,1] | 79,9 | 40,0 | -39,9 | -39,9 |
| BTC | 10 € | ROUTE | bitvavo — BTC/EUR direct | 50,4 [30,1 ; 64,5] | 50,1 | 40,0 | -10,4 | -10,1 |
| BTC | 30 € | ROUTE | bitvavo — BTC/EUR direct | 50,2 [30,1 ; 64,3] | 50,1 | 40,0 | -10,2 | -10,1 |
| ETH | 5 € | ROUTE | kraken — ETH/EUR direct | 79,9 [50,0 ; 94,0] | 79,9 | 40,0 | -39,9 | -39,9 |
| ETH | 10 € | ROUTE | bitvavo — ETH/EUR direct | 50,2 [30,3 ; 64,3] | 50,2 | 40,0 | -10,2 | -10,2 |
| ETH | 30 € | ROUTE | bitvavo — ETH/EUR direct | 50,0 [30,0 ; 64,1] | 50,0 | 40,0 | -10,0 | -10,0 |
| SOL | 5 € | ROUTE | kraken — SOL/EUR direct | 81,4 [51,4 ; 94,8] | 81,4 | 40,0 | -41,4 | -41,4 |
| SOL | 10 € | ROUTE | bitvavo — SOL/EUR direct | 50,1 [30,1 ; 64,2] | 50,1 | 40,0 | -10,1 | -10,1 |
| SOL | 30 € | ROUTE | binance — SOL/EUR direct | 33,0 [16,5 ; 46,4] | 21,5 | 40,0 | 7,0 | 18,5 |

#### C. Meilleure route de chaque type (pb aller-retour ; frais payés et capital immobilisé séparés)

| Actif | Montant | Type | Route | Coût économique | Frais payés (€) | Immobilisé (€ ; %) | Défavorable | Remplissage | Latence (s) | Finalité (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 5 € | cex_limit | kraken — BTC/EUR à cours limité | 63,5 | 0,0317 | 0,000 ; 0,00 % | 72,6 | 82% | 0,5 | 0 |
| BTC | 5 € | cex_direct | kraken — BTC/EUR direct | 79,9 | 0,0399 | 0,000 ; 0,00 % | 94,1 | 100% | 0,5 | 0 |
| BTC | 5 € | cex_via_stable | kraken — EUR → USDT → BTC | 160,4 | 0,0801 | 0,000 ; 0,00 % | 180,4 | 100% | 1,0 | 0 |
| BTC | 10 € | cex_limit | bitvavo — BTC/EUR à cours limité | 38,9 | 0,0385 | 0,000 ; 0,00 % | 48,0 | 82% | 0,5 | 0 |
| BTC | 10 € | cex_direct | bitvavo — BTC/EUR direct | 50,4 | 0,0501 | 0,000 ; 0,00 % | 64,5 | 100% | 0,5 | 0 |
| BTC | 10 € | cex_via_stable | kraken — EUR → USDT → BTC | 160,4 | 0,1601 | 0,000 ; 0,00 % | 180,4 | 100% | 1,0 | 0 |
| BTC | 30 € | cex_limit | bitvavo — BTC/EUR à cours limité | 38,7 | 0,1156 | 0,000 ; 0,00 % | 47,8 | 82% | 0,5 | 0 |
| BTC | 30 € | cex_direct | bitvavo — BTC/EUR direct | 50,2 | 0,1503 | 0,000 ; 0,00 % | 64,3 | 100% | 0,5 | 0 |
| BTC | 30 € | cex_via_stable | kraken — EUR → USDT → BTC | 160,2 | 0,4804 | 0,000 ; 0,00 % | 180,2 | 100% | 1,0 | 0 |
| ETH | 5 € | cex_limit | kraken — ETH/EUR à cours limité | 63,4 | 0,0317 | 0,000 ; 0,00 % | 72,5 | 82% | 0,5 | 0 |
| ETH | 5 € | cex_direct | kraken — ETH/EUR direct | 79,9 | 0,0400 | 0,000 ; 0,00 % | 94,0 | 100% | 0,5 | 0 |
| ETH | 5 € | cex_via_stable | kraken — EUR → USDT → ETH | 161,4 | 0,0807 | 0,000 ; 0,00 % | 181,0 | 100% | 1,0 | 0 |
| ETH | 10 € | cex_limit | bitvavo — ETH/EUR à cours limité | 38,6 | 0,0386 | 0,000 ; 0,00 % | 47,6 | 82% | 0,5 | 0 |
| ETH | 10 € | cex_direct | bitvavo — ETH/EUR direct | 50,2 | 0,0502 | 0,000 ; 0,00 % | 64,3 | 100% | 0,5 | 0 |
| ETH | 10 € | cex_via_stable | kraken — EUR → USDT → ETH | 160,4 | 0,1603 | 0,000 ; 0,00 % | 180,2 | 100% | 1,0 | 0 |
| ETH | 10 € | dex_funded | base — EUR → USDC → retrait → échange | 1618,8 | 0,8402 | 0,779 ; 7,79 % | 4478,8 | 98% | 1803,0 | 900 |
| ETH | 30 € | cex_limit | bitvavo — ETH/EUR à cours limité | 38,5 | 0,1155 | 0,000 ; 0,00 % | 47,6 | 82% | 0,5 | 0 |
| ETH | 30 € | cex_direct | bitvavo — ETH/EUR direct | 50,0 | 0,1500 | 0,000 ; 0,00 % | 64,1 | 100% | 0,5 | 0 |
| ETH | 30 € | cex_via_stable | binance — EUR → USDT → ETH | 109,3 | 0,1223 | 0,206 ; 0,69 % | 130,1 | 100% | 1,0 | 0 |
| ETH | 30 € | dex_funded | base — EUR → USDC → retrait → échange | 548,7 | 0,8876 | 0,758 ; 2,53 % | 1349,8 | 98% | 1803,0 | 900 |
| SOL | 5 € | cex_limit | kraken — SOL/EUR à cours limité | 64,1 | 0,0321 | 0,000 ; 0,00 % | 72,5 | 82% | 0,5 | 0 |
| SOL | 5 € | cex_direct | kraken — SOL/EUR direct | 81,4 | 0,0407 | 0,000 ; 0,00 % | 94,8 | 100% | 0,5 | 0 |
| SOL | 5 € | cex_via_stable | kraken — EUR → USDT → SOL | 161,5 | 0,0808 | 0,000 ; 0,00 % | 190,9 | 100% | 1,0 | 0 |
| SOL | 10 € | cex_limit | bitvavo — SOL/EUR à cours limité | 38,5 | 0,0385 | 0,000 ; 0,00 % | 47,6 | 82% | 0,5 | 0 |
| SOL | 10 € | cex_direct | bitvavo — SOL/EUR direct | 50,1 | 0,0501 | 0,000 ; 0,00 % | 64,2 | 100% | 0,5 | 0 |
| SOL | 10 € | cex_via_stable | kraken — EUR → USDT → SOL | 162,9 | 0,1629 | 0,000 ; 0,00 % | 191,6 | 100% | 1,0 | 0 |
| SOL | 10 € | dex_funded | solana — EUR → USDC → retrait → échange | 2138,6 | 1,5367 | 0,698 ; 6,98 % | 3744,0 | 95% | 28,0 | 13 |
| SOL | 30 € | cex_limit | binance — SOL/EUR à cours limité | 30,8 | 0,0577 | 0,035 ; 0,12 % | 39,2 | 82% | 0,5 | 0 |
| SOL | 30 € | cex_direct | binance — SOL/EUR direct | 33,0 | 0,0645 | 0,035 ; 0,12 % | 46,4 | 100% | 0,5 | 0 |
| SOL | 30 € | cex_via_stable | binance — EUR → USDT → SOL | 77,1 | 0,1265 | 0,105 ; 0,35 % | 107,3 | 100% | 1,0 | 0 |
| SOL | 30 € | dex_funded | solana — EUR → USDC → retrait → échange | 720,3 | 1,5794 | 0,678 ; 2,26 % | 1332,7 | 95% | 28,0 | 13 |

#### D. Seuil économique d'achat par plateforme et par actif (règle de la mission 52)

Minimum = le plus grand de : plancher prudent de 10 € ; minimum de la plateforme + un pas d'arrondi, commission comprise ; montant pour lequel un reliquat d'un pas reste ≤ 1 % du montant.

| Actif | Plateforme | Pas | Valeur d'un pas (€) | Minimum plateforme (€) | Minimum reliquat (€) | **Seuil économique (€)** | Contrainte |
|---|---|---|---|---|---|---|---|
| BTC | binance | 1e-05 | 0,5895 | 5,60 | 58,95 | **58,95** | reliquat |
| BTC | kraken | 1e-08 | 0,0006 | 0,45 | 0,06 | **10,00** | plancher prudent |
| BTC | coinbase | 1e-08 | 0,0006 | 0,85 | 0,06 | **10,00** | plancher prudent |
| BTC | bitstamp | 1e-08 | 0,0006 | 10,04 | 0,06 | **10,04** | minimum de la plateforme |
| BTC | bitvavo | 1e-08 | 0,0006 | 5,01 | 0,06 | **10,00** | plancher prudent |
| ETH | binance | 0.0001 | 0,1653 | 5,17 | 16,53 | **16,53** | reliquat |
| ETH | kraken | 1e-08 | 0,0000 | 0,45 | 0,00 | **10,00** | plancher prudent |
| ETH | coinbase | 1e-08 | 0,0000 | 0,85 | 0,00 | **10,00** | plancher prudent |
| ETH | bitstamp | 1e-06 | 0,0017 | 10,04 | 0,17 | **10,04** | minimum de la plateforme |
| ETH | bitvavo | 1e-08 | 0,0000 | 5,01 | 0,00 | **10,00** | plancher prudent |
| SOL | binance | 0.001 | 0,0646 | 5,07 | 6,46 | **10,00** | plancher prudent |
| SOL | kraken | 1e-08 | 0,0000 | 0,45 | 0,00 | **10,00** | plancher prudent |
| SOL | coinbase | 0.001 | 0,0646 | 0,92 | 6,46 | **10,00** | plancher prudent |
| SOL | bitstamp | 1e-05 | 0,0006 | 10,04 | 0,06 | **10,04** | minimum de la plateforme |
| SOL | bitvavo | 1e-08 | 0,0000 | 5,01 | 0,00 | **10,00** | plancher prudent |

#### Ressources

- 9 décisions rejouées en 0,53 s, CPU 0,52 s, mémoire Python maximale 0,6 Mo, mémoire du processus 34 Mo ;
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

## 3. Ce qui change par rapport aux conclusions de la mission 51

- **Le reliquat change le choix de plateforme.** Sur Binance, le pas de quantité du BTC (0,00001 BTC ≈ 0,59 €) laisse
  presque un pas invendable à chaque achat (commission prélevée dans l'actif reçu) : 5,8 % d'un achat de 10 €. Avec
  la règle prudente, Binance n'est économique pour le BTC qu'à partir d'environ **59 €**, et pour l'ETH à partir
  d'environ **16,5 €** : au-delà du plafond de position (15 €). Le hub retient donc Bitvavo (8 décimales, aucun
  reliquat) à **≈ 50 pb aller-retour**, dont ≈ 99 % de commissions **non vérifiées** (hypothèse 0,25 %).
- **Face au moteur existant (40 pb d'hypothèse)**, le hub est désormais **plus cher de ≈ 10 pb** à 10–30 € pour BTC,
  ETH et SOL (sauf SOL à 30 €) : il compte le reliquat et l'écart que le moteur existant ignore. L'économie mesurée en
  mission 51 (≈ 19 à 21 pb) supposait que le reliquat de Binance n'était pas une perte : elle n'est **pas confirmée**
  sous la règle prudente.
- **L'économie démontrée du routage reste vraie sur les mêmes données** : paire en euros contre détour par
  l'USDT sur la même plateforme (deux conversions évitées).
- **Seuls les achats de 10 € passent le parcours utilisateur** ; 5 € (minimum) et 30 € (plafond de position) sont
  refusés : les mesures à 30 € ne sont que des mesures de coût.

## 4. Portefeuille partagé (point 5)

| Contrôle | Résultat |
|---|---|
| Euros du hub = mouvements `hub_achat`/`hub_vente`/`hub_frais` du grand livre principal | égalité au centime près après achat et vente |
| Écriture parasite dans le grand livre principal | détectée, état « désynchronisé », nouvelles opérations bloquées |
| Réservations du hub vues par le broker principal (et l'inverse) | concordantes ; 8 fils mêlant ordres du broker et réservations du hub : jamais plus de 30 € engagés |
| Redémarrage de l'application | réservation interrompue libérée (opération ABANDONNÉE, jamais retentée) ; crochets du broker rebranchés ; valeur totale ≈ 30 € |
| Perte de 30 % sur une position | perte du jour constatée, nouvelle entrée refusée (limite journalière) |
| Valorisation invalide (NaN, infinie, négative) | refusée à l'écriture ; valeur stockée invalide ignorée (coût d'achat, signalé) ; valeur ancienne jamais au-dessus du coût |

## 5. Limites, dont Windows

- **Windows 11 : non testé** (aucun Windows dans cet environnement). Points vérifiés par construction : l'outil de
  validation ne dépend plus du module `resource` (absent de Windows : vérifié en simulant son absence) ; les tests
  multiprocessus utilisent `spawn`, le mode natif de Windows ; chemins via `pathlib`. Points NON vérifiés : certificats
  et proxy d'entreprise (httpx utilise ses certificats par défaut), performance de SQLite en mode WAL sur disque
  Windows, affichage du panel dans Edge.
- Commissions et frais de retrait : hypothèses ; la décision les signale mais ne peut pas les vérifier.
- La règle « reliquat = perte » est volontairement prudente : en réalité un reliquat peut être regroupé lors d'un achat
  suivant ou converti par la plateforme (avec des frais).
- Les seuils économiques sont calculés sur les filtres et prix de la capture du 16/11/2026.
