# ALPHA FACTORY v2 — évaluation de confirmation unique (mission 54)

> **Simulation, argent fictif.** Ces chiffres viennent de prix historiques réels, mais toutes les exécutions sont fictives :
> rien ici n'est un profit réel, et rien n'autorise un ordre réel. Aucun modèle n'est promu, aucun service n'est démarré.

## 1. Ce qui a été évalué, et dans quel ordre

| Étape | Commit | Contenu |
|---|---|---|
| Préparation (PR #53) | `ff46509` | laboratoire, 15 candidats et 4 références figés, modèle de reliquats |
| Correction d'avant gel, contrat de coûts #50 adopté, sensibilités préenregistrées | `8a4b564` | aucun résultat réel consulté |
| **Plan enregistré** `alpha-factory-v2-e1` | `d4a9969` | empreinte `6f4f436f02ae08767a8152e6cac0568d134383154ee83c7c8538464396954208`, poussé avant l'exécution |
| Lecture des trois verdicts fixée | `b2cee66` | règle écrite et testée avant `results.json` |
| **Exécution unique** | commit des résultats | `docs/validation/mesures/alpha-factory-v2/results.json`, inscrite au journal chaîné |

Le plan fige : la question, les données (empreinte de l'archive), l'univers daté, la fenêtre 2020-01-01 → 2026-11-12 coupée
le 2023-06-01 en deux périodes indépendantes, les 15 candidats et leurs paramètres (un seul jeu chacun, repris de la source citée),
les 4 références, les coûts (empreinte), l'analyse statistique, la règle de décision, les sensibilités et l'empreinte du code.
Le banc refuse de tourner si l'un d'eux a changé. **Aucune seconde exécution ne sera présentée comme une confirmation.**

## 2. Données et empreintes

| Élément | Valeur |
|---|---|
| Source | données publiques Binance (`data-api.binance.vision`), bougies journalières, paires USDT |
| Archive | 775 symboles (518 cotés, 257 retirés de la cote), du 17/08/2017 au 12/11/2026 |
| Manifeste (empreinte de chaque fichier) | `f301ffe98652c3c0d2f4a87269e1b62187612dbf0c51206bac133dc6c61feb23` — versionné dans `research/alpha-factory-v2/archive/` |
| Catalogue de la plateforme (pas, minimums) | `3ec824f817706fe1f91d952ea25c68ccb33229bad9b018a950876f59fed24d65` — versionné compressé (il ne se retélécharge pas à l'identique) |
| Bougies | 29 Mo, non versionnées ; copie de travail `data/alpha-lab/archive-20261113/` (ignorée par Git), retéléchargeables et vérifiées fichier par fichier |
| Contrat de coûts | `cost-hypotheses-v2.json` = proposition #50 à l'octet près, `56da1470cf32e1603618dd52f54e22bd0a2134db08d947d0bea45d64ac179c40` (contrat `965d29bb…`, version 1.0.0) |
| Décision d'adoption | `research/alpha-factory-v2/cost-adoption-v2.json` |

Hypothèses de coûts adoptées (décision de la coordination) : commission 0,10 % par ordre, **hypothèse non vérifiée** ;
conversion EUR/USDT payée à chaque ordre (11,67 pb mesurés) ; ordres simulés au marché ; demi-écart mesuré
(0,21 pb BTC/ETH, 4,69 pb autres) ; modèle prudent de reliquats ; un seul portefeuille de 30 € fictifs.
**Ces choix ne doivent jamais être extrapolés à une plateforme dont les commissions sont différentes.**

## 3. Audit des biais avant le gel (aucun rendement de stratégie consulté)

| Point vérifié | Constat | Traitement |
|---|---|---|
| Intégrité | anciens dossiers de résultats de l'espace temporaire = ALPHA FACTORY v1 (mission #38) ; aucun plan v2 n'existait ; archive conforme à son manifeste | rien à corriger |
| Biais du survivant | 40 retraits de cote connus testés (LUNA, FTT, SRM, HNT, WAVES, OMG, MATIC→POL…) : tous présents ; 257 séries retirées | limite : une paire purgée du catalogue serait absente |
| Symboles réutilisés | un jour manquant coupe la série (14 séries reprises, ex. `LUNA#2`) ; aucune position ne traverse un trou | rien à corriger |
| Données aberrantes | 0 bougie incohérente ; les mouvements extrêmes correspondent à des événements réels (LUNA, FTT, OM, DOGE 2021) avec de forts volumes | rien à corriger |
| Exécution à t+1 | décision à la clôture de t, exécution à l'ouverture de t+1 ; or l'ouverture vaut presque toujours la clôture de la veille (9 écarts > 5 % sur 776 400) : **aucun délai réel** | sensibilité préenregistrée « retard d'un jour » |
| Retraits de cote | vente à la dernière clôture connue, coûts compris : optimiste si la sortie réelle s'est faite plus bas | sensibilités « décote 25 % / 50 % » |
| Règles de marché | pas et minimums du catalogue du 13/11/2026 appliqués à tout l'historique (les minimums historiques étaient souvent de 10 USDT) | sensibilité « minimum 10 » |
| **Défaut démontré, corrigé avant le gel** | avec les règles de marché, une position sous le minimum de la plateforme était refusée à la vente les jours de décision, mais comptée vendable le dernier jour et vendue lors d'un retrait de cote | désormais comptée perdue (même règle prudente que les reliquats) ; tests synthétiques dédiés |
| Unité de compte | montants en USDT ; la conversion est payée à chaque ordre, mais la variation du change EUR/USDT sur les positions n'est pas modélisée | limite publiée |
| Commission | 10 pb : hypothèse non vérifiée | sensibilités 25 / 40 / 60 pb et commission d'équilibre, **hors règle de décision** |

## 4. Règle de décision (rappel du plan)

Un candidat est **retenu pour un futur protocole prospectif** seulement s'il remplit les quatre conditions : gain absolu
(résultat net > 0, probabilité corrigée des 15 essais < 5 %, méthode Romano-Wolf par bootstrap circulaire par blocs de 7 jours) ;
gain relatif face à la détention de même exposition (même test) ; résultat net et écart positifs sur chacune des deux périodes ;
résultat net positif dans le scénario de tension. Lecture fixée avant les résultats : **avantage démontré** (au moins un retenu),
**prometteur mais non démontré** (aucun retenu, mais au moins un candidat positif net, positif en tension, positif sur les deux
périodes et meilleur que la détention de même exposition), sinon **échec**.

## 5. Résultats (tableaux générés depuis `results.json`, aucune valeur saisie à la main)

Plan `alpha-factory-v2-e1` · fenêtre 2020-01-01 → 2026-11-12 (2508 jours ; P1 1247 j, P2 1261 j) · coûts « mesure » (tension : « tension ») · 15 candidats

### Résultats nets (30 unités fictives, scénario principal)

| Candidat | Famille | Résultat net | Par an | Baisse max. | Sharpe | Facteur de profit (jours) | Exposition | Ordres | Brut | Coûts payés | Reliquats perdus | Tension | P1 | P2 | p corrigée (absolu) | p corrigée (relatif) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| breakout_20_10_slots4 | breakout | +3,51 | +1,6 % | 94 % | +0,35 | 1,00 | 64 % | 696 | +79,07 | -35,05 | -72,69 | +5,19 | +190 % | -62 % | 0,490 | 0,990 | non retenu |
| breakout_55_20_majors | breakout | +190,69 | +33,7 % | 54 % | +0,94 | 1,11 | 43 % | 133 | +226,52 | -13,86 | -12,01 | +184,74 | +355 % | +62 % | 0,055 | 0,868 | non retenu |
| crypto_btc_gate_top5 | crypto | +77,45 | +20,4 % | 69 % | +0,62 | 1,03 | 56 % | 372 | +165,82 | -19,57 | -45,60 | +70,26 | +187 % | +25 % | 0,231 | 0,868 | non retenu |
| crypto_lowvol_top3 | crypto | +1039,48 | +68,2 % | 76 % | +1,11 | 1,06 | 99 % | 279 | +1222,17 | -93,87 | -6,75 | +974,29 | +1923 % | +76 % | 0,022 | 0,163 | non retenu |
| crypto_volmanaged_btc | crypto | +146,88 | +29,5 % | 63 % | +0,82 | 1,06 | 78 % | 86 | +155,42 | -4,87 | -0,11 | +143,83 | +139 % | +147 % | 0,098 | 0,999 | non retenu |
| crypto_weekday_btc | crypto | -29,96 | -62,5 % | 100 % | -0,61 | 0,80 | 62 % | 259 | +76,71 | -15,17 | -42,19 | -24,98 | -87 % | -99 % | 0,974 | 1,000 | non retenu |
| meanrev_rsi2_slots4 | mean_reversion | -22,10 | -17,7 % | 76 % | -0,16 | 0,92 | 34 % | 255 | -16,96 | -4,02 | -25,57 | -27,30 | -67 % | -20 % | 0,856 | 1,000 | non retenu |
| meanrev_xs_7_bottom3 | mean_reversion | -23,82 | -20,5 % | 94 % | -0,01 | 0,92 | 56 % | 244 | -29,00 | -5,09 | -21,12 | -24,33 | -89 % | +90 % | 0,816 | 1,000 | non retenu |
| mom_dual_90_top3 | momentum | -28,94 | -38,5 % | 100 % | -0,01 | 0,98 | 82 % | 301 | -17,45 | -14,74 | -43,35 | -22,51 | -59 % | -91 % | 0,816 | 1,000 | non retenu |
| mom_ts_28 | momentum | +1751,39 | +81,2 % | 38 % | +1,51 | 1,17 | 56 % | 298 | +2125,00 | -146,61 | -12,45 | +1631,87 | +1925 % | +193 % | 0,001 | 0,022 | **retenu** |
| mom_xs_28_top3 | momentum | -28,70 | -36,7 % | 100 % | +0,19 | 0,99 | 94 % | 799 | +47,61 | -40,09 | -103,07 | -20,90 | +3 % | -96 % | 0,619 | 0,998 | non retenu |
| trend_cross_50_200 | trend | +121,29 | +26,6 % | 58 % | +0,72 | 1,05 | 58 % | 100 | +131,31 | -5,34 | -4,51 | +118,35 | +391 % | +3 % | 0,150 | 0,990 | non retenu |
| trend_sma_200 | trend | +288,39 | +41,0 % | 51 % | +0,96 | 1,09 | 58 % | 239 | +351,48 | -25,30 | -16,02 | +270,10 | +490 % | +80 % | 0,048 | 0,868 | non retenu |
| value_mom_combo_top3 | value | -24,77 | -22,4 % | 98 % | +0,22 | 0,99 | 96 % | 335 | +7,72 | -18,11 | -37,49 | -27,20 | -44 % | -69 % | 0,615 | 0,999 | non retenu |
| value_rev_365_top3 | value | -27,53 | -30,5 % | 100 % | +0,23 | 0,99 | 98 % | 274 | -29,93 | -20,84 | -27,29 | -29,22 | +44 % | -94 % | 0,610 | 0,998 | non retenu |

### Références simples (mêmes coûts, mêmes jours)

| Référence | Résultat net | Par an | Baisse max. | Sharpe | P1 | P2 |
|---|---|---|---|---|---|---|
| cash | +0,00 | +0,0 % | 0 % | +0,00 | +0 % | +0 % |
| hold_basket_top5 | +61,03 | +17,5 % | 88 % | +0,60 | +100 % | +51 % |
| hold_btc | +265,94 | +39,5 % | 77 % | +0,86 | +277 % | +162 % |
| hold_majors | +423,31 | +48,5 % | 76 % | +0,93 | +748 % | +78 % |

### Pourquoi ils échouent

| Candidat | Motifs | Signalements | Écart quotidien à la détention de même exposition [IC 95 %] | Part du 1er actif dans les gains | Années pour prouver ce Sharpe |
|---|---|---|---|---|---|
| breakout_20_10_slots4 | gain absolu non démontré une fois les essais multiples pris en compte ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,000 % [-0,087 % ; +0,091 %] | 18 % (SUSHI) | 63 |
| breakout_55_20_majors | gain absolu non démontré une fois les essais multiples pris en compte ; ne fait pas mieux qu'une simple détention de même exposition | plus de la moitié des gains vient d'un seul actif ; baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,027 % [-0,035 % ; +0,091 %] | 86 % (ETH) | 9 |
| crypto_btc_gate_top5 | gain absolu non démontré une fois les essais multiples pris en compte ; ne fait pas mieux qu'une simple détention de même exposition | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,035 % [-0,041 % ; +0,111 %] | 30 % (ETH) | 21 |
| crypto_lowvol_top3 | ne fait pas mieux qu'une simple détention de même exposition | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,107 % [+0,013 % ; +0,200 %] | 28 % (BNB) | 6 |
| crypto_volmanaged_btc | gain absolu non démontré une fois les essais multiples pris en compte ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | plus de la moitié des gains vient d'un seul actif ; baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | -0,014 % [-0,043 % ; +0,014 %] | 100 % (BTC) | 12 |
| crypto_weekday_btc | l'avantage brut existe mais les coûts le dépassent ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | -0,185 % [-0,298 % ; -0,097 %] | — (None) | — |
| meanrev_rsi2_slots4 | aucun avantage avant coûts : la règle ne gagne pas, même gratuite ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | -0,054 % [-0,108 % ; +0,000 %] | 12 % (XLM) | — |
| meanrev_xs_7_bottom3 | aucun avantage avant coûts : la règle ne gagne pas, même gratuite ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | -0,056 % [-0,139 % ; +0,027 %] | 20 % (BNB) | — |
| mom_dual_90_top3 | aucun avantage avant coûts : la règle ne gagne pas, même gratuite ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme ; ventes imposées par des retraits de la cote | -0,084 % [-0,200 % ; +0,036 %] | 24 % (LUNA) | — |
| mom_ts_28 | — | plus de la moitié des gains vient d'un seul actif ; baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,097 % [+0,038 % ; +0,155 %] | 65 % (ETH) | 3 |
| mom_xs_28_top3 | l'avantage brut existe mais les coûts le dépassent ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme ; ventes imposées par des retraits de la cote | -0,033 % [-0,172 % ; +0,110 %] | 19 % (LUNA) | 207 |
| trend_cross_50_200 | gain absolu non démontré une fois les essais multiples pris en compte ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | plus de la moitié des gains vient d'un seul actif ; baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,004 % [-0,058 % ; +0,064 %] | 64 % (ETH) | 15 |
| trend_sma_200 | ne fait pas mieux qu'une simple détention de même exposition | plus de la moitié des gains vient d'un seul actif ; baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | +0,026 % [-0,037 % ; +0,086 %] | 71 % (ETH) | 8 |
| value_mom_combo_top3 | l'avantage brut existe mais les coûts le dépassent ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme ; ventes imposées par des retraits de la cote | -0,037 % [-0,145 % ; +0,071 %] | 12 % (ONT) | 165 |
| value_rev_365_top3 | aucun avantage avant coûts : la règle ne gagne pas, même gratuite ; ne fait pas mieux qu'une simple détention de même exposition ; ne tient pas sur les deux périodes indépendantes | baisse maximale au-dessus de la limite de risque du projet ; des ordres n'ont pas pu passer sous le minimum de la plateforme | -0,031 % [-0,153 % ; +0,084 %] | 23 % (XRP) | 144 |

### Généralisation : univers privé de ADA, BNB, BTC, DOGE, ETH, LINK, SOL, XRP

| Candidat | Résultat net | Sharpe | Baisse max. | Écart à la détention de même exposition [IC 95 %] |
|---|---|---|---|---|
| breakout_20_10_slots4 | -23,87 | +0,01 | 98 % | -0,021 % [-0,127 % ; +0,089 %] |
| crypto_btc_gate_top5 | -14,67 | +0,13 | 84 % | +0,003 % [-0,080 % ; +0,085 %] |
| crypto_lowvol_top3 | -28,56 | -0,04 | 99 % | -0,043 % [-0,146 % ; +0,052 %] |
| meanrev_rsi2_slots4 | -17,62 | -0,05 | 71 % | -0,014 % [-0,072 % ; +0,047 %] |
| meanrev_xs_7_bottom3 | -22,74 | -0,06 | 84 % | -0,021 % [-0,104 % ; +0,057 %] |
| mom_dual_90_top3 | -24,15 | +0,37 | 100 % | +0,079 % [-0,074 % ; +0,234 %] |
| mom_xs_28_top3 | -26,50 | +0,36 | 100 % | +0,082 % [-0,082 % ; +0,242 %] |
| value_mom_combo_top3 | -23,96 | +0,29 | 99 % | +0,048 % [-0,075 % ; +0,172 %] |
| value_rev_365_top3 | -27,65 | +0,11 | 100 % | -0,006 % [-0,115 % ; +0,109 %] |

### Généralisation des règles « BTC et ETH » à d'autres paires

| Candidat | Paire | Résultat net | Sharpe | Écart quotidien à la détention | Détention simple de la paire |
|---|---|---|---|---|---|
| breakout_55_20_majors | BNB+TRX | +272,25 | +0,90 | +0,025 % | +5264 % |
| breakout_55_20_majors | EOS+LTC | -17,22 | +0,06 | -0,015 % | -40 % |
| breakout_55_20_majors | MATIC+XRP | +191,37 | +0,78 | +0,056 % | +3410 % |
| mom_ts_28 | BNB+TRX | +708,21 | +1,08 | +0,038 % | +5264 % |
| mom_ts_28 | EOS+LTC | -10,94 | +0,21 | -0,002 % | -40 % |
| mom_ts_28 | MATIC+XRP | +1097,57 | +1,11 | +0,097 % | +3410 % |
| trend_cross_50_200 | BNB+TRX | +258,78 | +0,83 | -0,004 % | +5264 % |
| trend_cross_50_200 | EOS+LTC | -24,92 | -0,08 | -0,045 % | -40 % |
| trend_cross_50_200 | MATIC+XRP | +219,33 | +0,78 | +0,033 % | +3410 % |
| trend_sma_200 | BNB+TRX | +171,16 | +0,75 | -0,026 % | +5264 % |
| trend_sma_200 | EOS+LTC | -24,27 | -0,09 | -0,051 % | -40 % |
| trend_sma_200 | MATIC+XRP | +278,58 | +0,82 | +0,041 % | +3410 % |

### Sensibilité (préenregistrée, DESCRIPTIVE : hors règle de décision et hors tests corrigés)

analyses de sensibilité PRÉENREGISTRÉES et DESCRIPTIVES, séparées de l'évaluation de confirmation : elles n'entrent jamais dans la règle de décision ni dans les tests corrigés des essais multiples, et ne peuvent rien « sauver ».

Résultat net (30 unités fictives) selon la variante ; base : scénario principal du contrat adopté (commission 10 pb par ordre : HYPOTHÈSE NON VÉRIFIÉE), tout le reste inchangé.

| Règle | Principal | commission_25 | commission_40 | commission_60 | decote_sortie_forcee_25 | decote_sortie_forcee_50 | defavorable_cumule | minimum_10 | retard_1_jour | Commission d'équilibre (pb par ordre) |
|---|---|---|---|---|---|---|---|---|---|---|
| breakout_20_10_slots4 | +3,51 | +5,57 | +0,23 | +3,91 | +3,51 | +3,51 | +0,00 | +0,00 | +16,89 | 56,5 |
| breakout_55_20_majors | +190,69 | +184,18 | +173,33 | +158,38 | +190,69 | +190,69 | +233,80 | +179,92 | +256,71 | résultat net encore positif à 200 pb de commission |
| crypto_btc_gate_top5 | +77,45 | +72,75 | +65,81 | +58,02 | +77,45 | +77,45 | +0,00 | +0,00 | +87,25 | résultat net encore positif à 200 pb de commission |
| crypto_lowvol_top3 | +1039,48 | +950,77 | +867,99 | +767,92 | +1039,48 | +1039,48 | +0,00 | +0,00 | +1053,42 | résultat net encore positif à 200 pb de commission |
| crypto_volmanaged_btc | +146,88 | +140,95 | +135,43 | +127,87 | +146,88 | +146,88 | +116,84 | +118,46 | +122,42 | résultat net encore positif à 200 pb de commission |
| crypto_weekday_btc | -29,96 | -24,99 | -24,97 | -29,95 | -29,96 | -29,96 | -29,68 | -19,99 | -29,93 | 0,0 |
| meanrev_rsi2_slots4 | -22,10 | -16,32 | -22,27 | -18,19 | -22,10 | -22,10 | +0,00 | +0,00 | -17,81 | résultat net négatif ou nul même à 0 pb de commission |
| meanrev_xs_7_bottom3 | -23,82 | -29,24 | -24,14 | -27,35 | -23,82 | -23,82 | +0,00 | +0,00 | -29,62 | résultat net négatif ou nul même à 0 pb de commission |
| mom_dual_90_top3 | -28,94 | -27,22 | -22,93 | -21,68 | -28,13 | -28,73 | +0,00 | +0,00 | -21,47 | résultat net négatif ou nul même à 0 pb de commission |
| mom_ts_28 | +1751,39 | +1551,52 | +1366,86 | +1168,84 | +1751,39 | +1751,39 | +1457,69 | +1738,05 | +1723,54 | résultat net encore positif à 200 pb de commission |
| mom_xs_28_top3 | -28,70 | -24,82 | -26,53 | -25,05 | -28,70 | -28,70 | +0,00 | +0,00 | -25,43 | 0,0 |
| trend_cross_50_200 | +121,29 | +119,53 | +114,51 | +111,64 | +121,29 | +121,29 | +122,23 | +141,82 | +118,07 | résultat net encore positif à 200 pb de commission |
| trend_sma_200 | +288,39 | +266,76 | +248,30 | +222,01 | +288,39 | +288,39 | +263,94 | +303,10 | +282,64 | résultat net encore positif à 200 pb de commission |
| value_mom_combo_top3 | -24,77 | -29,27 | -27,27 | -27,25 | -27,13 | -24,86 | +0,00 | +0,00 | -18,70 | 0,0 |
| value_rev_365_top3 | -27,53 | -29,06 | -24,40 | -29,38 | -27,53 | -27,53 | +0,00 | +0,00 | -28,95 | résultat net négatif ou nul même à 0 pb de commission |
| cash | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | +0,00 | — |
| hold_basket_top5 | +61,03 | +57,94 | +50,09 | +50,59 | +61,03 | +61,03 | +0,00 | +0,00 | +51,40 | — |
| hold_btc | +265,94 | +264,15 | +263,07 | +261,13 | +265,94 | +265,94 | +264,15 | +265,94 | +265,94 | — |
| hold_majors | +423,31 | +417,51 | +413,96 | +409,39 | +423,31 | +423,31 | +412,46 | +446,11 | +434,93 | — |

**Conclusion du banc : 1 candidat(s) retenu(s) pour un futur protocole prospectif ; aucune preuve de rentabilité.**

**Verdict : AVANTAGE DÉMONTRÉ** — au moins un candidat passe TOUTES les conditions du plan (sur l'historique : à confirmer en prospectif, jamais « rentable »).

| Candidat | Lecture |
|---|---|
| breakout_20_10_slots4 | échec |
| breakout_55_20_majors | prometteur non démontré |
| crypto_btc_gate_top5 | prometteur non démontré |
| crypto_lowvol_top3 | prometteur non démontré |
| crypto_volmanaged_btc | échec |
| crypto_weekday_btc | échec |
| meanrev_rsi2_slots4 | échec |
| meanrev_xs_7_bottom3 | échec |
| mom_dual_90_top3 | échec |
| mom_ts_28 | avantage démontré |
| mom_xs_28_top3 | échec |
| trend_cross_50_200 | échec |
| trend_sma_200 | prometteur non démontré |
| value_mom_combo_top3 | échec |
| value_rev_365_top3 | échec |

## 6. Verdict

**AVANTAGE DÉMONTRÉ, au sens du plan et sur l'historique seulement, pour un seul candidat : `mom_ts_28`**
(momentum de 4 semaines sur BTC et ETH, révision hebdomadaire ; Moskowitz-Ooi-Pedersen 2012, Liu-Tsyvinski 2021).
Les 14 autres ne sont pas retenus : 4 sont « prometteurs mais non démontrés », 10 sont des échecs.

Ce verdict ne prouve **pas** que la règle est rentable. Il la qualifie seulement pour un protocole **prospectif**
préenregistré : le seul test qui ne puisse plus être contaminé par le passé. Elle n'est ni promue ni branchée sur
un portefeuille.

### `mom_ts_28` face aux 4 références (30 unités fictives, mêmes jours, mêmes coûts)

| | mom_ts_28 | Détention BTC+ETH | Détention BTC | Panier des 5 plus liquides | Liquidités |
|---|---|---|---|---|---|
| Résultat net | **+1 751** | +423 | +266 | +61 | 0 |
| Par an | +81 % | +49 % | +40 % | +18 % | 0 % |
| Baisse maximale | **38 %** | 76 % | 77 % | 88 % | 0 % |
| Sharpe | 1,51 | 0,93 | 0,86 | 0,60 | — |
| Période 1 (2020 → mai 2023) | +1 925 % | +748 % | +277 % | +100 % | 0 % |
| Période 2 (juin 2023 → nov. 2026) | **+193 %** | +78 % | +162 % | +51 % | 0 % |

Détail de la règle retenue : 298 ordres ; commissions et conversion payées 145,2 ; écart et glissement 1,4 ;
reliquats perdus 12,4 ; facteur de profit journalier 1,17 (les deux actifs finissent gagnants : le facteur par actif
n'est pas défini) ; exposition moyenne 56 % ; bêta 0,51 face à BTC+ETH.
Par année : 2020 +365 %, 2021 +273 %, 2022 −18 % (marché −65 %), 2023 +87 % (+123 %), 2024 +53 % (+83 %),
2025 +39 % (−6 %), 2026 +4 % (−25 %).

### Tests statistiques (15 essais corrigés ensemble, Romano-Wolf, blocs de 7 jours)

| Test | Moyenne quotidienne [IC 95 %] | p brute | p corrigée |
|---|---|---|---|
| Gain absolu (battre les liquidités) | +0,192 % [+0,095 % ; +0,286 %] | < 0,001 | **0,0006** |
| Gain relatif (détention BTC+ETH à 56 % d'exposition) | +0,097 % [+0,038 % ; +0,155 %] | < 0,001 | **0,022** |
| Face à la détention de BTC à 100 % (descriptif) | +0,051 % [−0,035 % ; +0,141 %] | — | non significatif |

### Ce qui affaiblit ce résultat (à lire avant tout chiffre)

1. **L'avantage diminue fortement** : écart quotidien à la référence de 0,14 % en période 1, 0,055 % en période 2 ;
   seulement +4 % en 2026. L'essentiel du gain vient du marché haussier de 2020-2021.
2. **Concentration** : 65 % des gains viennent d'ETH.
3. **Risque** : une baisse maximale de 38 % dépasse largement la limite de 10 % du projet. Réduite à 26 % de sa taille
   pour respecter cette limite, la règle aurait fait environ +21 % par an sur l'historique.
4. **Données non vierges pour le projet** : BTC et ETH ont déjà été étudiés par le projet sur 2018-2026. Les paramètres
   n'ont pas été réglés (ceux de la source), et les données de Liu-Tsyvinski s'arrêtent avant 2020, mais la fenêtre n'est
   pas « jamais vue » par l'équipe.
5. **Ne bat pas significativement la simple détention de BTC** (intervalle qui contient zéro) : son apport principal est
   de couper l'exposition dans les baisses, pas de gagner davantage à exposition pleine.
6. **Durée de preuve prospective** : au Sharpe observé, il faudrait environ 3,4 ans d'observation pour le confirmer ;
   au Sharpe plus faible de la période 2, nettement plus.

### Ce qui le renforce

- Tenue aux coûts : positif dans le scénario de tension (+1 632), à 60 pb de commission par ordre (+1 169), et la
  commission d'équilibre dépasse 200 pb par ordre.
- Tenue aux hypothèses d'exécution : retard d'un jour +1 724, minimum de 10 +1 738, cumul défavorable +1 458.
- Généralisation (descriptive) : positif et au-dessus de la détention sur BNB+TRX (+708) et MATIC+XRP (+1 098) ;
  légèrement négatif sur EOS+LTC (−11, paire en baisse de 40 %).

### Les 14 autres candidats

| Lecture | Candidats | Motif principal |
|---|---|---|
| Prometteur non démontré | `trend_sma_200`, `breakout_55_20_majors`, `crypto_btc_gate_top5`, `crypto_lowvol_top3` | positifs, mais pas mieux que la détention de même exposition une fois les 15 essais corrigés ; `crypto_lowvol_top3` (+1 039) perd tout hors des 8 cryptos déjà étudiées (−29) |
| Échec | 10 autres | pas d'avantage brut (4), coûts supérieurs à l'avantage brut (3), instabilité entre périodes |

**Diagnostic transversal (pour M55)** : avec 30 € fictifs, les règles à plusieurs actifs sont écrasées par les
contraintes de petit compte. Les reliquats perdus dépassent souvent les commissions (ex. `mom_xs_28_top3` :
103 de reliquats contre 40 de frais) ; avec un minimum de 10 €, elles ne peuvent même plus passer un seul ordre
(0 ordre dans les variantes « minimum 10 » et « cumul défavorable »). La commission d'équilibre « 0 pb » de trois règles
traduit ce mécanisme : dès qu'une commission est prélevée dans l'actif reçu, chaque achat crée un reliquat invendable
(ex. `crypto_weekday_btc` : +15,6 à 0 pb, −25,0 à 0,01 pb). Le modèle prudent adopté compte ce reliquat comme perdu,
alors qu'en réalité il resterait sur le compte et pourrait être revendu avec l'achat suivant du même actif : c'est
une borne défavorable, choisie d'avance, qui pèse surtout sur les règles qui tournent beaucoup.

## 7. Limites

- Les montants sont en unités de la devise de cotation (USDT) : la conversion est payée à chaque ordre, mais la variation
  du change EUR/USDT sur les positions n'est pas modélisée. 30 → 1 781 n'est pas un montant en euros.
- Commission de 10 pb : hypothèse non vérifiée ; **aucune extrapolation à une plateforme aux commissions différentes**.
- Règles de marché du 13/11/2026 appliquées à l'historique ; ventes au marché sans carnet d'ordres historique ;
  fins de cotation vendues à la dernière clôture (sensibilités publiées).
- Résultat historique en simulation : **aucun bénéfice futur n'est promis**.

## 8. Reproduire

```
python -m tools.alpha_lab verify --archive data/alpha-lab/archive-20261113   # plan, coûts, données, code intacts
python -m tools.alpha_lab report --input docs/validation/mesures/alpha-factory-v2   # tableaux ci-dessus (identiques)
```
`tests/test_alpha_lab_experiment.py` vérifie qu'il existe un seul plan et au plus une exécution, et
`tests/test_alpha_lab_evaluation.py` que les tableaux publiés se régénèrent à l'identique depuis `results.json`.
Ne jamais relancer `run` sur ce plan pour « confirmer » : toute nouvelle évaluation exige des données vraiment
nouvelles et un nouveau plan.
