Plan `cost-optimizer-v1-m1` · empreinte `1880fac08245…` · collecte : {'archived': 1246, 'checksum_failed': 0, 'duplicate_id': 3, 'empty': 1, 'id_collision': 3, 'missing': 0, 'planned': 1252} · 7458 mesures

#### Coût d'un ordre au marché de 10 €, par classe (points de base du montant)

| Classe | Échantillons (avec prix) | Sans liquidité visible | Demi-écart à l'ouverture [IC 95 %] | Demi-écart en journée (médiane) | Coût total à 1 s [IC 95 %] | Glissement au-delà de l'écart [IC 95 %] | p90 à 1 s | p99 à 1 s | Coût médian à 30 s |
|---|---|---|---|---|---|---|---|---|---|
| paires en euros | 336 (281) | 16,4 % | 1,05 [0,71 ; 1,41] | 0,54 | 9,69 [9,33 ; 10,03] | -1,25 [-1,63 ; -0,88] | 14,73 | 27,90 | 10,00 |
| conversion EUR/USDT | 120 (111) | 7,5 % | 1,15 [0,81 ; 1,56] | 0,46 | 11,15 [10,78 ; 11,59] | -0,08 [-0,30 ; 0,11] | 14,40 | 14,61 | 10,46 |
| BTC et ETH (USDT) | 240 (240) | 0,0 % | 0,13 [0,07 ; 0,21] | 0,01 | 9,95 [9,92 ; 9,99] | -0,15 [-0,20 ; -0,11] | 10,99 | 13,74 | 10,03 |
| memecoins (USDT) | 154 (145) | 5,8 % | 3,21 [2,14 ; 4,69] | 2,01 | 11,80 [10,87 ; 13,03] | -1,33 [-1,80 ; -0,85] | 17,91 | 37,38 | 12,45 |
| autres cryptos (USDT) | 393 (382) | 2,8 % | 1,69 [1,38 ; 2,02] | 1,26 | 10,42 [10,16 ; 10,70] | -1,29 [-1,66 ; -0,95] | 15,00 | 25,82 | 10,65 |

#### Aller-retour au marché de 10 € (achat puis vente, commission comprise)

| Classe | Allers-retours mesurés | Moyenne | Médiane | p90 | p99 |
|---|---|---|---|---|---|
| paires en euros | 247 | 19,03 | 19,86 | 23,09 | 30,02 |
| conversion EUR/USDT | 111 | 22,30 | 20,92 | 28,80 | 29,21 |
| BTC et ETH (USDT) | 240 | 19,90 | 20,00 | 20,16 | 21,78 |
| memecoins (USDT) | 136 | 23,15 | 20,16 | 30,70 | 41,61 |
| autres cryptos (USDT) | 373 | 20,78 | 20,66 | 24,69 | 37,06 |

#### Carnets observés (sondes du 16/11/2026) et erreur de l'estimateur

| Paire | Sondes | Demi-écart observé (médiane) | Montant au meilleur prix (médiane) | Impact 30 € | Impact 100 € |
|---|---|---|---|---|---|
| ADAUSDT | 12 | 3,251 | 34085 | 0,000 | 0,000 |
| BNBUSDT | 12 | 0,084 | 13245 | 0,000 | 0,000 |
| BTCEUR | 12 | 0,031 | 205 | 0,000 | 0,000 |
| BTCUSDT | 12 | 0,001 | 294655 | 0,000 | 0,000 |
| DOGEUSDT | 12 | 0,847 | 6583 | 0,000 | 0,000 |
| ETHEUR | 12 | 0,030 | 460 | 0,000 | 0,000 |
| ETHUSDT | 12 | 0,027 | 72956 | 0,000 | 0,000 |
| EURUSDT | 12 | 0,454 | 80006 | 0,000 | 0,000 |
| PEPEUSDT | 12 | 13,245 | 111355 | 0,000 | 0,000 |
| SHIBUSDT | 12 | 12,837 | 27120 | 0,000 | 0,000 |
| SOLEUR | 12 | 0,772 | 114 | 0,000 | 0,000 |
| SOLUSDT | 12 | 0,700 | 14054 | 0,000 | 0,000 |
| TRXUSDT | 12 | 1,649 | 17810 | 0,000 | 0,000 |
| XRPUSDT | 12 | 0,520 | 9726 | 0,000 | 0,000 |

| Classe | Comparaisons | Erreur moyenne (estimé − observé) [IC 95 %] | Part sous-estimée |
|---|---|---|---|
| paires en euros | 20 | 0,274 [-0,299 ; 0,856] | 50 % |
| conversion EUR/USDT | 12 | -0,038 [-0,189 ; 0,151] | 25 % |
| BTC et ETH (USDT) | 23 | 0,196 [0,088 ; 0,319] | 17 % |
| memecoins (USDT) | 33 | 0,556 [0,051 ; 1,464] | 3 % |
| autres cryptos (USDT) | 57 | -0,110 [-0,335 ; 0,100] | 23 % |

#### Méthodes d'exécution (ordre de 10 €, toutes classes, achats et ventes)

| Méthode | Part exécutée | dont passive | Coût moyen | Coût médian | Gain vs marché à 1 s [IC 95 %] | Mouvement pendant l'attente (médiane) |
|---|---|---|---|---|---|---|
| market@1000ms | 97 % | 0 % | 10,39 | 10,28 | 0,00 [0,00 ; 0,00] | — |
| market@30000ms | 98 % | 0 % | 10,42 | 10,46 | -0,03 [-0,18 ; 0,12] | — |
| market@5000ms | 97 % | 0 % | 10,40 | 10,42 | -0,01 [-0,09 ; 0,08] | — |
| improve/300s/conservative/cancel | 82 % | 82 % | 8,52 | 9,40 | 1,26 [1,00 ; 1,57] | 0,00 |
| improve/300s/conservative/market | 99 % | 82 % | 11,69 | 9,57 | -1,36 [-1,97 ; -0,79] | 0,00 |
| improve/300s/optimistic/cancel | 90 % | 90 % | 8,35 | 9,43 | 1,62 [1,26 ; 2,08] | 0,00 |
| improve/300s/optimistic/market | 99 % | 90 % | 10,65 | 9,51 | -0,34 [-0,97 ; 0,30] | 0,00 |
| improve/30s/conservative/cancel | 59 % | 59 % | 8,78 | 9,50 | 0,24 [-0,06 ; 0,55] | 0,00 |
| improve/30s/conservative/market | 98 % | 59 % | 11,57 | 9,97 | -1,20 [-1,57 ; -0,87] | 0,00 |
| improve/30s/optimistic/cancel | 74 % | 74 % | 8,61 | 9,51 | 0,86 [0,50 ; 1,25] | 0,00 |
| improve/30s/optimistic/market | 98 % | 74 % | 10,79 | 9,64 | -0,45 [-0,85 ; -0,04] | 0,00 |
| improve/60s/conservative/cancel | 68 % | 68 % | 8,74 | 9,49 | 0,48 [0,22 ; 0,76] | 0,00 |
| improve/60s/conservative/market | 98 % | 68 % | 11,74 | 9,77 | -1,40 [-1,84 ; -0,95] | 0,00 |
| improve/60s/optimistic/cancel | 81 % | 81 % | 8,56 | 9,49 | 1,03 [0,71 ; 1,43] | 0,00 |
| improve/60s/optimistic/market | 99 % | 81 % | 10,87 | 9,57 | -0,56 [-1,02 ; -0,07] | 0,00 |
| join/300s/conservative/cancel | 78 % | 78 % | 8,34 | 9,25 | 1,25 [0,99 ; 1,54] | 0,00 |
| join/300s/conservative/market | 99 % | 78 % | 12,17 | 9,53 | -1,85 [-2,49 ; -1,23] | 0,00 |
| join/300s/optimistic/cancel | 90 % | 90 % | 8,11 | 9,20 | 1,85 [1,46 ; 2,31] | 0,00 |
| join/300s/optimistic/market | 99 % | 90 % | 10,62 | 9,35 | -0,32 [-0,97 ; 0,35] | 0,00 |
| join/30s/conservative/cancel | 52 % | 52 % | 8,64 | 9,37 | -0,00 [-0,28 ; 0,25] | 0,00 |
| join/30s/conservative/market | 98 % | 52 % | 11,80 | 9,98 | -1,43 [-1,78 ; -1,13] | 0,00 |
| join/30s/optimistic/cancel | 72 % | 72 % | 8,38 | 9,35 | 1,06 [0,69 ; 1,47] | 0,00 |
| join/30s/optimistic/market | 98 % | 72 % | 10,69 | 9,57 | -0,35 [-0,75 ; 0,06] | 0,00 |
| join/60s/conservative/cancel | 62 % | 62 % | 8,60 | 9,34 | 0,30 [0,05 ; 0,57] | 0,00 |
| join/60s/conservative/market | 98 % | 62 % | 12,08 | 9,76 | -1,74 [-2,17 ; -1,30] | 0,00 |
| join/60s/optimistic/cancel | 80 % | 80 % | 8,32 | 9,30 | 1,24 [0,90 ; 1,65] | 0,00 |
| join/60s/optimistic/market | 99 % | 80 % | 10,78 | 9,52 | -0,48 [-0,95 ; 0,01] | 0,00 |

**Comparaison confirmatoire** : variante retenue sur la moitié d'estimation : `improve/300s/conservative/market` ; sur la moitié de validation, gain moyen face au marché -1,94 [-2,83 ; -1,16] pb (règle prudente), borne optimiste -0,62 [-1,63 ; 0,40] pb. Amélioration démontrée : **non**.

#### Optimisme du remplissage « au toucher » (moteur actuel sur bougies)

| Variante | Échantillons | Bougie du jour touchée | Exécuté selon le ruban |
|---|---|---|---|
| join/300s/conservative/cancel | 445 | 100 % | 78 % |
| join/300s/optimistic/cancel | 445 | 100 % | 93 % |
| join/30s/conservative/cancel | 445 | 100 % | 54 % |
| join/30s/optimistic/cancel | 445 | 100 % | 78 % |
| join/60s/conservative/cancel | 445 | 100 % | 64 % |
| join/60s/optimistic/cancel | 445 | 100 % | 86 % |

#### Routage : paire en euros, ou conversion EUR → USDT puis paire en USDT (achat de 10 €)

| Actif | Paires mesurées | Direct (moyenne) | Via USDT (moyenne) | Écart via − direct [IC 95 %] |
|---|---|---|---|---|
| BTC | 53 | 10,15 | 21,00 | 10,85 [10,07 ; 11,69] |
| ETH | 51 | 9,79 | 21,22 | 11,43 [10,00 ; 12,87] |
| SOL | 30 | 11,99 | 22,06 | 10,08 [6,03 ; 14,11] |

#### Situations défavorables (ordre de 10 € au marché, états connus la veille)

| État | Seuil | Coût médian (état) | p90 (état) | Coût médian (reste) | Demi-écart médian (état / reste) |
|---|---|---|---|---|---|
| lendemain d'un mouvement de plus de 10 % | 55 éch. | 9,47 | 15,00 | — | 1,69 / — |
| prior_day_move (quart supérieur) | 0,0424 | 10,02 | 15,94 | 10,35 | 0,70 / 0,47 |
| vol30 (quart supérieur) | 0,0535 | 10,37 | 16,47 | 10,24 | 1,30 / 0,44 |

#### Reliquats et tailles réelles (aller-retour de 10 €, frais prélevés dans l'actif reçu, filtres observés)

| Classe | Échantillons | Reliquat médian (part de la position) | p90 | Écart de taille médian |
|---|---|---|---|---|
| paires en euros | 145 | 1,9 % | 6,0 % | -1,0 % |
| conversion EUR/USDT | 56 | 0,9 % | 0,9 % | -0,5 % |
| BTC et ETH (USDT) | 120 | 2,6 % | 6,6 % | -1,1 % |
| memecoins (USDT) | 74 | 0,0 % | 1,7 % | -0,0 % |
| autres cryptos (USDT) | 195 | 0,8 % | 5,2 % | -0,5 % |

#### Contrat de coûts proposé (un ordre, points de base)

| Classe | Commission | Demi-écart (principal) | Glissement (principal) | Demi-écart (tension) | Glissement (tension) | Aller-retour preneur (principal) |
|---|---|---|---|---|---|---|
| paires en euros | 10,00 (HYPOTHETICAL) | 1,41 | 0,00 | 3,42 | 5,39 | 22,81 |
| conversion EUR/USDT | 10,00 (HYPOTHETICAL) | 1,56 | 0,11 | 4,40 | 0,11 | 23,34 |
| BTC et ETH (USDT) | 10,00 (HYPOTHETICAL) | 0,21 | 0,00 | 0,42 | 2,55 | 20,41 |
| memecoins (USDT) | 10,00 (HYPOTHETICAL) | 4,69 | 0,00 | 7,71 | 5,43 | 29,37 |
| autres cryptos (USDT) | 10,00 (HYPOTHETICAL) | 2,02 | 0,00 | 4,19 | 5,73 | 24,04 |

Conversion par ordre : 11,67 pb (tension : 14,51 pb). Méthode recommandée : `market@1000ms`.

#### Bénéfice net d'un avantage brut HYPOTHÉTIQUE, après les coûts mesurés (aller-retour au marché, 10 €, un an)

| Classe | Avantage brut par aller-retour | Opérations par an | Net moyen par opération [IC 95 %] | Probabilité net > 0 | Coûts annuels (€) | Net annuel (€) |
|---|---|---|---|---|---|---|
| BTC et ETH (USDT) | 0 pb | 12 | -19,9 [-292,6 ; 273,7] | 46 % | 0,24 | -0,24 |
| BTC et ETH (USDT) | 0 pb | 52 | -19,9 [-152,2 ; 120,7] | 41 % | 1,03 | -1,03 |
| BTC et ETH (USDT) | 25 pb | 12 | 5,1 [-267,6 ; 298,7] | 52 % | 0,24 | +0,06 |
| BTC et ETH (USDT) | 25 pb | 52 | 5,1 [-127,2 ; 145,7] | 55 % | 1,03 | +0,27 |
| BTC et ETH (USDT) | 50 pb | 12 | 30,1 [-242,6 ; 323,7] | 59 % | 0,24 | +0,36 |
| BTC et ETH (USDT) | 50 pb | 52 | 30,1 [-102,2 ; 170,7] | 69 % | 1,03 | +1,57 |
| BTC et ETH (USDT) | 100 pb | 12 | 80,1 [-192,6 ; 373,7] | 72 % | 0,24 | +0,96 |
| BTC et ETH (USDT) | 100 pb | 52 | 80,1 [-52,2 ; 220,7] | 89 % | 1,03 | +4,17 |
| autres cryptos (USDT) | 0 pb | 12 | -20,8 [-294,1 ; 272,5] | 45 % | 0,25 | -0,25 |
| autres cryptos (USDT) | 0 pb | 52 | -20,8 [-153,2 ; 119,4] | 41 % | 1,08 | -1,08 |
| autres cryptos (USDT) | 25 pb | 12 | 4,2 [-269,1 ; 297,5] | 52 % | 0,25 | +0,05 |
| autres cryptos (USDT) | 25 pb | 52 | 4,2 [-128,2 ; 144,4] | 55 % | 1,08 | +0,22 |
| autres cryptos (USDT) | 50 pb | 12 | 29,2 [-244,1 ; 322,5] | 59 % | 0,25 | +0,35 |
| autres cryptos (USDT) | 50 pb | 52 | 29,2 [-103,2 ; 169,4] | 69 % | 1,08 | +1,52 |
| autres cryptos (USDT) | 100 pb | 12 | 79,2 [-194,1 ; 372,5] | 71 % | 0,25 | +0,95 |
| autres cryptos (USDT) | 100 pb | 52 | 79,2 [-53,2 ; 219,4] | 89 % | 1,08 | +4,12 |
| memecoins (USDT) | 0 pb | 12 | -23,2 [-297,9 ; 270,7] | 45 % | 0,28 | -0,28 |
| memecoins (USDT) | 0 pb | 52 | -23,2 [-156,5 ; 115,7] | 39 % | 1,20 | -1,20 |
| memecoins (USDT) | 25 pb | 12 | 1,8 [-272,9 ; 295,7] | 51 % | 0,28 | +0,02 |
| memecoins (USDT) | 25 pb | 52 | 1,8 [-131,5 ; 140,7] | 53 % | 1,20 | +0,10 |
| memecoins (USDT) | 50 pb | 12 | 26,8 [-247,9 ; 320,7] | 58 % | 0,28 | +0,32 |
| memecoins (USDT) | 50 pb | 52 | 26,8 [-106,5 ; 165,7] | 68 % | 1,20 | +1,40 |
| memecoins (USDT) | 100 pb | 12 | 76,8 [-197,9 ; 370,7] | 71 % | 0,28 | +0,92 |
| memecoins (USDT) | 100 pb | 52 | 76,8 [-56,5 ; 215,7] | 88 % | 1,20 | +4,00 |
| paires en euros | 0 pb | 12 | -19,0 [-291,7 ; 275,0] | 46 % | 0,23 | -0,23 |
| paires en euros | 0 pb | 52 | -19,0 [-151,7 ; 121,8] | 41 % | 0,99 | -0,99 |
| paires en euros | 25 pb | 12 | 6,0 [-266,7 ; 300,0] | 52 % | 0,23 | +0,07 |
| paires en euros | 25 pb | 52 | 6,0 [-126,7 ; 146,8] | 56 % | 0,99 | +0,31 |
| paires en euros | 50 pb | 12 | 31,0 [-241,7 ; 325,0] | 59 % | 0,23 | +0,37 |
| paires en euros | 50 pb | 52 | 31,0 [-101,7 ; 171,8] | 70 % | 0,99 | +1,61 |
| paires en euros | 100 pb | 12 | 81,0 [-191,7 ; 375,0] | 72 % | 0,23 | +0,97 |
| paires en euros | 100 pb | 52 | 81,0 [-51,7 ; 221,8] | 89 % | 0,99 | +4,21 |
