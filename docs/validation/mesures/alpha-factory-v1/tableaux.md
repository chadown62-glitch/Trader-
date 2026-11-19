# ALPHA FACTORY v1 — tableaux complets (générés par `python -m tools.alpha_factory report`)

## Marchés synthétiques (mesure officielle)

Données : synthétique · préenregistrement `1310130ddd31c93d…` · confiance par intervalle 99,91 % · durée 375.4 s

#### noise@0.0

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | 50 | +0,00 | +2,82 | 0,0 | 0,0 % | -0,137 | 0,500 (0,661) | 0,00 | 0,0 | +5,64 | 0/3 | non démontré |
| cash | tout | +0,00 | +2,82 | 0,0 | 0,0 % | -0,095 | 0,500 (0,661) | 0,00 | 0,0 | +0,16 | 0/3 | non démontré |
| error_replay | 50 | -2,39 | +0,43 | 40,7 | 8,4 % | -0,034 | 0,631 (0,661) | 1,04 | 0,5 | +0,86 | 0/3 | non démontré |
| error_replay | tout | -0,63 | +2,19 | 7,0 | 2,7 % | -0,039 | 0,651 (0,661) | 9,10 | 2,1 | +0,13 | 0/3 | non démontré |
| exit_learner | 50 | -2,78 | +0,04 | 155,3 | 21,8 % | -0,137 | 0,500 (0,661) | 0,04 | 0,1 | +0,08 | 0/3 | non démontré |
| exit_learner | tout | -3,64 | -0,83 | 157,7 | 22,5 % | -0,095 | 0,500 (0,661) | 0,09 | 0,3 | -0,05 | 0/3 | non démontré |
| project_mixte | 50 | -2,39 | +0,43 | 40,7 | 8,4 % | -0,036 | 0,640 (0,661) | 1,13 | 0,6 | +0,86 | 0/3 | non démontré |
| project_mixte | tout | -0,21 | +2,61 | 14,7 | 2,0 % | -0,007 | 0,681 (0,661) | 1,51 | 1,4 | +0,15 | 1/3 | non démontré |
| regime_router | 50 | -1,07 | +1,75 | 12,3 | 4,7 % | -0,007 | 0,658 (0,661) | 0,02 | 0,0 | +3,49 | 0/3 | non démontré |
| regime_router | tout | -0,23 | +2,59 | 1,7 | 0,8 % | +0,003 | 0,667 (0,661) | 0,08 | 0,1 | +0,15 | 0/3 | non démontré |
| router_with_exits | 50 | -0,94 | +1,88 | 12,0 | 5,0 % | -0,007 | 0,658 (0,661) | 0,02 | 0,1 | +3,75 | 0/3 | non démontré |
| router_with_exits | tout | -0,23 | +2,59 | 1,7 | 0,8 % | +0,003 | 0,667 (0,661) | 0,07 | 0,3 | +0,15 | 0/3 | non démontré |
| take_all | tout | -2,82 | +0,00 | 172,3 | 20,8 % | -0,095 | 0,500 (0,661) | 0,00 | 0,0 | +0,00 | 0/3 | non démontré |
| teacher:breakout | 50 | +0,28 | +3,09 | 63,0 | 12,1 % | -0,137 | 0,500 (0,661) | 0,00 | 0,0 | +6,19 | 1/3 | non démontré |
| teacher:breakout | tout | +0,28 | +3,09 | 63,0 | 12,1 % | -0,095 | 0,500 (0,661) | 0,00 | 0,0 | +0,18 | 1/3 | non démontré |
| teacher:mean_reversion | 50 | -1,09 | +1,73 | 51,3 | 4,9 % | -0,137 | 0,500 (0,661) | 0,00 | 0,0 | +3,46 | 0/3 | non démontré |
| teacher:mean_reversion | tout | -1,09 | +1,73 | 51,3 | 4,9 % | -0,095 | 0,500 (0,661) | 0,00 | 0,0 | +0,10 | 0/3 | non démontré |
| teacher:pullback | 50 | -3,96 | -1,14 | 153,0 | 14,5 % | -0,137 | 0,500 (0,661) | 0,00 | 0,0 | -2,29 | 0/3 | non démontré |
| teacher:pullback | tout | -3,96 | -1,14 | 153,0 | 14,5 % | -0,095 | 0,500 (0,661) | 0,00 | 0,0 | -0,07 | 0/3 | non démontré |
| teacher:trend | 50 | -0,02 | +2,79 | 52,7 | 5,4 % | -0,137 | 0,500 (0,661) | 0,00 | 0,0 | +5,59 | 0/3 | non démontré |
| teacher:trend | tout | -0,02 | +2,79 | 52,7 | 5,4 % | -0,095 | 0,500 (0,661) | 0,00 | 0,0 | +0,16 | 0/3 | non démontré |

#### regimes@1.0

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | 50 | +0,00 | +1,52 | 0,0 | 0,0 % | -0,017 | 0,500 (0,658) | 0,00 | 0,0 | +3,03 | 1/3 | non démontré |
| cash | tout | +0,00 | +1,52 | 0,0 | 0,0 % | -0,079 | 0,500 (0,664) | 0,00 | 0,0 | +0,08 | 1/3 | non démontré |
| error_replay | 50 | +1,91 | +3,42 | 86,7 | 3,1 % | -0,074 | 0,566 (0,658) | 0,83 | 0,5 | +6,84 | 1/3 | non démontré |
| error_replay | tout | +4,52 | +6,04 | 20,7 | 2,9 % | -0,059 | 0,676 (0,664) | 8,59 | 2,3 | +0,32 | 1/3 | non démontré |
| exit_learner | 50 | -3,97 | -2,45 | 187,0 | 22,1 % | -0,017 | 0,500 (0,658) | 0,01 | 0,1 | -4,91 | 0/3 | non démontré |
| exit_learner | tout | -0,96 | +0,56 | 130,0 | 20,5 % | -0,079 | 0,500 (0,664) | 0,05 | 0,3 | +0,03 | 0/3 | non démontré |
| project_mixte | 50 | +1,91 | +3,42 | 86,7 | 3,1 % | -0,070 | 0,576 (0,658) | 1,09 | 0,5 | +6,84 | 1/3 | non démontré |
| project_mixte | tout | +1,91 | +3,42 | 8,7 | 1,7 % | -0,041 | 0,693 (0,664) | 1,47 | 1,5 | +0,18 | 1/3 | non démontré |
| regime_router | 50 | +1,91 | +3,42 | 86,7 | 3,1 % | +0,010 | 0,647 (0,658) | 0,02 | 0,0 | +6,84 | 1/3 | non démontré |
| regime_router | tout | +1,86 | +3,38 | 17,7 | 2,6 % | +0,027 | 0,670 (0,664) | 0,02 | 0,1 | +0,18 | 1/3 | non démontré |
| router_with_exits | 50 | +0,93 | +2,45 | 98,3 | 3,9 % | +0,010 | 0,647 (0,658) | 0,02 | 0,1 | +4,90 | 1/3 | non démontré |
| router_with_exits | tout | +1,86 | +3,38 | 17,7 | 2,6 % | +0,027 | 0,670 (0,664) | 0,07 | 0,3 | +0,18 | 1/3 | non démontré |
| take_all | tout | -1,52 | +0,00 | 170,7 | 20,1 % | -0,079 | 0,500 (0,664) | 0,00 | 0,0 | +0,00 | 0/3 | non démontré |
| teacher:breakout | 50 | +2,27 | +3,78 | 57,3 | 13,7 % | -0,017 | 0,500 (0,658) | 0,00 | 0,0 | +7,57 | 1/3 | non démontré |
| teacher:breakout | tout | +2,27 | +3,78 | 57,3 | 13,7 % | -0,079 | 0,500 (0,664) | 0,00 | 0,0 | +0,20 | 1/3 | non démontré |
| teacher:mean_reversion | 50 | -0,74 | +0,77 | 42,7 | 3,9 % | -0,017 | 0,500 (0,658) | 0,00 | 0,0 | +1,55 | 1/3 | non démontré |
| teacher:mean_reversion | tout | -0,74 | +0,77 | 42,7 | 3,9 % | -0,079 | 0,500 (0,664) | 0,00 | 0,0 | +0,04 | 1/3 | non démontré |
| teacher:pullback | 50 | -4,03 | -2,51 | 147,7 | 15,4 % | -0,017 | 0,500 (0,658) | 0,00 | 0,0 | -5,03 | 0/3 | non démontré |
| teacher:pullback | tout | -4,03 | -2,51 | 147,7 | 15,4 % | -0,079 | 0,500 (0,664) | 0,00 | 0,0 | -0,13 | 0/3 | non démontré |
| teacher:trend | 50 | +0,69 | +2,20 | 41,7 | 4,6 % | -0,017 | 0,500 (0,658) | 0,00 | 0,0 | +4,40 | 1/3 | non démontré |
| teacher:trend | tout | +0,69 | +2,20 | 41,7 | 4,6 % | -0,079 | 0,500 (0,664) | 0,00 | 0,0 | +0,12 | 1/3 | non démontré |

#### regime_shift@1.0

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | 50 | +0,00 | +9,66 | 0,0 | 0,0 % | -0,191 | 0,500 (0,713) | 0,00 | 0,0 | +19,33 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| cash | tout | +0,00 | +9,66 | 0,0 | 0,0 % | -0,198 | 0,500 (0,718) | 0,00 | 0,0 | +0,51 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| error_replay | 50 | +0,00 | +9,66 | 0,0 | 0,0 % | -0,094 | 0,640 (0,713) | 0,82 | 0,5 | +19,33 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| error_replay | tout | +0,00 | +9,66 | 0,0 | 0,0 % | -0,008 | 0,716 (0,718) | 8,00 | 2,3 | +0,51 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| exit_learner | 50 | -10,58 | -0,92 | 225,3 | 36,7 % | -0,191 | 0,500 (0,713) | 0,05 | 0,1 | -1,84 | 0/3 | non démontré |
| exit_learner | tout | -9,21 | +0,45 | 168,0 | 32,6 % | -0,198 | 0,500 (0,718) | 0,06 | 0,3 | +0,02 | 0/3 | non démontré |
| project_mixte | 50 | +0,00 | +9,66 | 0,0 | 0,0 % | -0,090 | 0,647 (0,713) | 0,94 | 0,6 | +19,33 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| project_mixte | tout | +0,64 | +10,30 | 2,3 | 0,9 % | +0,035 | 0,736 (0,718) | 1,21 | 1,5 | +0,54 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| regime_router | 50 | +0,00 | +9,66 | 0,0 | 0,0 % | -0,009 | 0,702 (0,713) | 0,04 | 0,0 | +19,33 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| regime_router | tout | +0,00 | +9,66 | 0,0 | 0,0 % | +0,007 | 0,722 (0,718) | 0,06 | 0,1 | +0,51 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| router_with_exits | 50 | +0,00 | +9,66 | 0,0 | 0,0 % | -0,009 | 0,702 (0,713) | 0,02 | 0,1 | +19,33 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| router_with_exits | tout | +0,00 | +9,66 | 0,0 | 0,0 % | +0,007 | 0,722 (0,718) | 0,07 | 0,3 | +0,51 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| take_all | tout | -9,66 | +0,00 | 197,0 | 33,1 % | -0,198 | 0,500 (0,718) | 0,00 | 0,0 | +0,00 | 0/3 | non démontré |
| teacher:breakout | 50 | -6,96 | +2,71 | 66,3 | 25,0 % | -0,191 | 0,500 (0,713) | 0,00 | 0,0 | +5,41 | 0/3 | non démontré |
| teacher:breakout | tout | -6,96 | +2,71 | 66,3 | 25,0 % | -0,198 | 0,500 (0,718) | 0,00 | 0,0 | +0,14 | 0/3 | non démontré |
| teacher:mean_reversion | 50 | -1,04 | +8,62 | 44,3 | 4,2 % | -0,191 | 0,500 (0,713) | 0,00 | 0,0 | +17,24 | 2/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| teacher:mean_reversion | tout | -1,04 | +8,62 | 44,3 | 4,2 % | -0,198 | 0,500 (0,718) | 0,00 | 0,0 | +0,45 | 2/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| teacher:pullback | 50 | -4,13 | +5,54 | 183,0 | 15,1 % | -0,191 | 0,500 (0,713) | 0,00 | 0,0 | +11,07 | 2/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| teacher:pullback | tout | -4,13 | +5,54 | 183,0 | 15,1 % | -0,198 | 0,500 (0,718) | 0,00 | 0,0 | +0,29 | 2/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| teacher:trend | 50 | -2,17 | +7,49 | 46,0 | 8,0 % | -0,191 | 0,500 (0,713) | 0,00 | 0,0 | +14,99 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |
| teacher:trend | tout | -2,17 | +7,49 | 46,0 | 8,0 % | -0,198 | 0,500 (0,718) | 0,00 | 0,0 | +0,39 | 3/3 | avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés |

#### hidden_rule@1.0

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | 50 | +0,00 | -13,40 | 0,0 | 0,0 % | -0,133 | 0,500 (0,546) | 0,00 | 0,0 | -26,81 | 0/3 | non démontré |
| cash | tout | +0,00 | -13,40 | 0,0 | 0,0 % | -0,009 | 0,500 (0,546) | 0,00 | 0,0 | -0,79 | 0/3 | non démontré |
| error_replay | 50 | +20,50 | +7,10 | 67,7 | 4,5 % | -0,010 | 0,502 (0,546) | 0,92 | 0,5 | +14,19 | 1/3 | non démontré |
| error_replay | tout | +19,55 | +6,15 | 79,3 | 4,8 % | -0,002 | 0,564 (0,546) | 8,79 | 2,1 | +0,36 | 1/3 | non démontré |
| exit_learner | 50 | +8,66 | -4,74 | 181,3 | 8,4 % | -0,133 | 0,500 (0,546) | 0,04 | 0,1 | -9,49 | 0/3 | non démontré |
| exit_learner | tout | +10,85 | -2,56 | 162,3 | 10,9 % | -0,009 | 0,500 (0,546) | 0,05 | 0,3 | -0,15 | 0/3 | non démontré |
| project_mixte | 50 | +20,99 | +7,59 | 67,3 | 4,5 % | -0,011 | 0,525 (0,546) | 1,11 | 0,5 | +15,18 | 1/3 | non démontré |
| project_mixte | tout | +19,89 | +6,49 | 74,0 | 4,6 % | +0,008 | 0,584 (0,546) | 1,33 | 1,4 | +0,38 | 1/3 | non démontré |
| regime_router | 50 | +19,37 | +5,96 | 68,0 | 4,3 % | -0,014 | 0,548 (0,546) | 0,03 | 0,0 | +11,93 | 1/3 | non démontré |
| regime_router | tout | +19,60 | +6,20 | 72,0 | 4,6 % | -0,001 | 0,557 (0,546) | 0,03 | 0,1 | +0,36 | 1/3 | non démontré |
| router_with_exits | 50 | +14,30 | +0,90 | 80,0 | 4,8 % | -0,014 | 0,548 (0,546) | 0,03 | 0,1 | +1,80 | 0/3 | non démontré |
| router_with_exits | tout | +17,90 | +4,50 | 70,3 | 6,3 % | -0,001 | 0,557 (0,546) | 0,06 | 0,3 | +0,26 | 1/3 | non démontré |
| take_all | tout | +13,40 | +0,00 | 167,0 | 8,6 % | -0,009 | 0,500 (0,546) | 0,00 | 0,0 | +0,00 | 0/3 | non démontré |
| teacher:breakout | 50 | +21,03 | +7,63 | 65,7 | 4,5 % | -0,133 | 0,500 (0,546) | 0,00 | 0,0 | +15,26 | 1/3 | non démontré |
| teacher:breakout | tout | +21,03 | +7,63 | 65,7 | 4,5 % | -0,009 | 0,500 (0,546) | 0,00 | 0,0 | +0,45 | 1/3 | non démontré |
| teacher:mean_reversion | 50 | -1,51 | -14,91 | 38,7 | 5,5 % | -0,133 | 0,500 (0,546) | 0,00 | 0,0 | -29,83 | 0/3 | non démontré |
| teacher:mean_reversion | tout | -1,51 | -14,91 | 38,7 | 5,5 % | -0,009 | 0,500 (0,546) | 0,00 | 0,0 | -0,88 | 0/3 | non démontré |
| teacher:pullback | 50 | -4,67 | -18,08 | 114,0 | 15,9 % | -0,133 | 0,500 (0,546) | 0,00 | 0,0 | -36,15 | 0/3 | non démontré |
| teacher:pullback | tout | -4,67 | -18,08 | 114,0 | 15,9 % | -0,009 | 0,500 (0,546) | 0,00 | 0,0 | -1,06 | 0/3 | non démontré |
| teacher:trend | 50 | +4,99 | -8,41 | 33,7 | 3,3 % | -0,133 | 0,500 (0,546) | 0,00 | 0,0 | -16,83 | 0/3 | non démontré |
| teacher:trend | tout | +4,99 | -8,41 | 33,7 | 3,3 % | -0,009 | 0,500 (0,546) | 0,00 | 0,0 | -0,49 | 0/3 | non démontré |

#### hidden_rule@0.5

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | 50 | +0,00 | -1,88 | 0,0 | 0,0 % | -0,051 | 0,500 (0,595) | 0,00 | 0,0 | -3,75 | 0/3 | non démontré |
| cash | tout | +0,00 | -1,88 | 0,0 | 0,0 % | -0,024 | 0,500 (0,597) | 0,00 | 0,0 | -0,11 | 0/3 | non démontré |
| error_replay | 50 | -0,66 | -2,53 | 122,0 | 14,3 % | -0,029 | 0,551 (0,595) | 1,11 | 0,5 | -5,07 | 0/3 | non démontré |
| error_replay | tout | +4,31 | +2,44 | 63,7 | 9,4 % | -0,032 | 0,548 (0,597) | 8,24 | 2,1 | +0,14 | 0/3 | non démontré |
| exit_learner | 50 | +2,53 | +0,65 | 183,7 | 15,9 % | -0,051 | 0,500 (0,595) | 0,02 | 0,1 | +1,31 | 0/3 | non démontré |
| exit_learner | tout | +1,85 | -0,02 | 157,7 | 15,0 % | -0,024 | 0,500 (0,597) | 0,07 | 0,3 | -0,00 | 0/3 | non démontré |
| project_mixte | 50 | -0,66 | -2,53 | 122,0 | 14,3 % | -0,030 | 0,549 (0,595) | 0,98 | 0,5 | -5,07 | 0/3 | non démontré |
| project_mixte | tout | +1,65 | -0,22 | 49,3 | 8,9 % | -0,017 | 0,602 (0,597) | 1,38 | 1,4 | -0,01 | 0/3 | non démontré |
| regime_router | 50 | +2,09 | +0,22 | 34,7 | 6,7 % | -0,020 | 0,581 (0,595) | 0,03 | 0,0 | +0,43 | 0/3 | non démontré |
| regime_router | tout | +4,30 | +2,43 | 55,0 | 8,3 % | -0,008 | 0,575 (0,597) | 0,06 | 0,1 | +0,14 | 0/3 | non démontré |
| router_with_exits | 50 | +0,68 | -1,20 | 33,7 | 7,2 % | -0,020 | 0,581 (0,595) | 0,02 | 0,1 | -2,40 | 0/3 | non démontré |
| router_with_exits | tout | +3,09 | +1,21 | 58,0 | 8,8 % | -0,008 | 0,575 (0,597) | 0,07 | 0,3 | +0,07 | 0/3 | non démontré |
| take_all | tout | +1,88 | +0,00 | 175,3 | 14,5 % | -0,024 | 0,500 (0,597) | 0,00 | 0,0 | +0,00 | 0/3 | non démontré |
| teacher:breakout | 50 | +5,44 | +3,57 | 63,7 | 9,3 % | -0,051 | 0,500 (0,595) | 0,00 | 0,0 | +7,13 | 0/3 | non démontré |
| teacher:breakout | tout | +5,44 | +3,57 | 63,7 | 9,3 % | -0,024 | 0,500 (0,597) | 0,00 | 0,0 | +0,21 | 0/3 | non démontré |
| teacher:mean_reversion | 50 | -1,72 | -3,59 | 40,7 | 6,0 % | -0,051 | 0,500 (0,595) | 0,00 | 0,0 | -7,19 | 0/3 | non démontré |
| teacher:mean_reversion | tout | -1,72 | -3,59 | 40,7 | 6,0 % | -0,024 | 0,500 (0,597) | 0,00 | 0,0 | -0,21 | 0/3 | non démontré |
| teacher:pullback | 50 | -4,74 | -6,62 | 139,3 | 16,8 % | -0,051 | 0,500 (0,595) | 0,00 | 0,0 | -13,23 | 0/3 | non démontré |
| teacher:pullback | tout | -4,74 | -6,62 | 139,3 | 16,8 % | -0,024 | 0,500 (0,597) | 0,00 | 0,0 | -0,38 | 0/3 | non démontré |
| teacher:trend | 50 | +1,98 | +0,10 | 43,3 | 4,1 % | -0,051 | 0,500 (0,595) | 0,00 | 0,0 | +0,20 | 0/3 | non démontré |
| teacher:trend | tout | +1,98 | +0,10 | 43,3 | 4,1 % | -0,024 | 0,500 (0,597) | 0,00 | 0,0 | +0,01 | 0/3 | non démontré |

#### Ablation du conseil — noise@0.0

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | -0,14 | 1,7 | non démontré | — |  |
| without:structure | -0,07 | 2,7 | non démontré | -0,07 | retirable (contribution non démontrée) |
| only:structure | -0,85 | 128,7 | non démontré | — |  |
| without:entry_exit | -1,09 | 122,3 | non démontré | +0,96 | retirable (contribution non démontrée) |
| only:entry_exit | +0,42 | 14,0 | non démontré | — |  |
| without:liquidity | -0,14 | 1,7 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:liquidity | -2,81 | 172,3 | non démontré | — |  |
| without:macro | -0,14 | 1,7 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:macro | -2,88 | 174,0 | non démontré | — |  |
| without:red_team | +0,67 | 10,0 | non démontré | -0,81 | retirable (contribution non démontrée) |
| only:red_team | -2,89 | 177,0 | non démontré | — |  |
| without:risk | -0,24 | 2,0 | non démontré | +0,10 | retirable (contribution non démontrée) |
| only:risk | -3,20 | 175,3 | non démontré | — |  |
| vote | +0,21 | 100,7 | non démontré | — |  |
| take_all | -2,82 | 172,3 | non démontré | — |  |
| single_model | -0,21 | 14,7 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | +21,20 | 3,0 | non démontré | — |  |

#### Ablation du conseil — regimes@1.0

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | +1,38 | 19,0 | non démontré | — |  |
| without:structure | +0,15 | 32,3 | non démontré | +1,23 | retirable (contribution non démontrée) |
| only:structure | +1,00 | 121,7 | non démontré | — |  |
| without:entry_exit | +0,60 | 114,0 | non démontré | +0,79 | retirable (contribution non démontrée) |
| only:entry_exit | +1,97 | 45,7 | non démontré | — |  |
| without:liquidity | +1,38 | 19,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:liquidity | -1,38 | 168,3 | non démontré | — |  |
| without:macro | +1,38 | 19,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:macro | -1,88 | 166,7 | non démontré | — |  |
| without:red_team | +2,80 | 28,3 | non démontré | -1,42 | retirable (contribution non démontrée) |
| only:red_team | -1,59 | 172,0 | non démontré | — |  |
| without:risk | +1,38 | 19,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:risk | -1,41 | 171,0 | non démontré | — |  |
| vote | +0,81 | 87,3 | non démontré | — |  |
| take_all | -1,52 | 170,7 | non démontré | — |  |
| single_model | +1,91 | 8,7 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | +17,70 | 3,0 | non démontré | — |  |

#### Ablation du conseil — regime_shift@1.0

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | -1,06 | 12,0 | amélioration répliquée hors échantillon | — |  |
| without:structure | -1,44 | 13,3 | amélioration répliquée hors échantillon | +0,38 | retirable (contribution non démontrée) |
| only:structure | -5,12 | 138,3 | non démontré | — |  |
| without:entry_exit | -6,00 | 131,7 | non démontré | +4,94 | retirable (contribution non démontrée) |
| only:entry_exit | -2,15 | 30,3 | amélioration répliquée hors échantillon | — |  |
| without:liquidity | -1,06 | 12,0 | amélioration répliquée hors échantillon | +0,00 | retirable (contribution non démontrée) |
| only:liquidity | -8,94 | 194,3 | non démontré | — |  |
| without:macro | -1,06 | 12,0 | amélioration répliquée hors échantillon | +0,00 | retirable (contribution non démontrée) |
| only:macro | -9,16 | 192,0 | non démontré | — |  |
| without:red_team | -2,11 | 24,3 | amélioration répliquée hors échantillon | +1,05 | retirable (contribution non démontrée) |
| only:red_team | -8,82 | 197,3 | non démontré | — |  |
| without:risk | -0,93 | 11,7 | amélioration répliquée hors échantillon | -0,12 | retirable (contribution non démontrée) |
| only:risk | -9,58 | 195,3 | non démontré | — |  |
| vote | -4,18 | 85,3 | non démontré | — |  |
| take_all | -9,66 | 197,0 | non démontré | — |  |
| single_model | +0,64 | 2,3 | amélioration répliquée hors échantillon | — |  |
| cash | +0,00 | 0,0 | amélioration répliquée hors échantillon | — |  |
| buy_and_hold | +20,18 | 3,0 | amélioration répliquée hors échantillon | — |  |

#### Ablation du conseil — hidden_rule@1.0

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | +9,40 | 34,7 | non démontré | — |  |
| without:structure | +18,38 | 78,0 | non démontré | -8,98 | retirable (contribution non démontrée) |
| only:structure | +9,28 | 97,3 | non démontré | — |  |
| without:entry_exit | +10,47 | 93,3 | non démontré | -1,07 | retirable (contribution non démontrée) |
| only:entry_exit | +17,12 | 78,0 | non démontré | — |  |
| without:liquidity | +9,40 | 34,7 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:liquidity | +12,55 | 160,3 | non démontré | — |  |
| without:macro | +9,36 | 35,0 | non démontré | +0,04 | retirable (contribution non démontrée) |
| only:macro | +12,71 | 162,0 | non démontré | — |  |
| without:red_team | +9,40 | 34,7 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:red_team | +12,63 | 163,7 | non démontré | — |  |
| without:risk | +9,25 | 35,3 | non démontré | +0,15 | retirable (contribution non démontrée) |
| only:risk | +12,85 | 163,0 | non démontré | — |  |
| vote | +14,28 | 111,0 | non démontré | — |  |
| take_all | +13,40 | 167,0 | non démontré | — |  |
| single_model | +19,89 | 74,0 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | +27,56 | 3,0 | non démontré | — |  |

#### Ablation du conseil — hidden_rule@0.5

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | +1,34 | 36,0 | non démontré | — |  |
| without:structure | +3,03 | 68,0 | non démontré | -1,69 | retirable (contribution non démontrée) |
| only:structure | +2,81 | 111,3 | non démontré | — |  |
| without:entry_exit | +2,47 | 106,0 | non démontré | -1,13 | retirable (contribution non démontrée) |
| only:entry_exit | +3,91 | 70,7 | non démontré | — |  |
| without:liquidity | +1,34 | 36,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:liquidity | +2,02 | 170,3 | non démontré | — |  |
| without:macro | +1,66 | 36,3 | non démontré | -0,32 | retirable (contribution non démontrée) |
| only:macro | +2,03 | 169,3 | non démontré | — |  |
| without:red_team | +2,15 | 39,0 | non démontré | -0,81 | retirable (contribution non démontrée) |
| only:red_team | +1,95 | 174,0 | non démontré | — |  |
| without:risk | +1,47 | 36,3 | non démontré | -0,13 | retirable (contribution non démontrée) |
| only:risk | +2,08 | 174,7 | non démontré | — |  |
| vote | +4,83 | 108,7 | non démontré | — |  |
| take_all | +1,88 | 175,3 | non démontré | — |  |
| single_model | +1,65 | 49,3 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | +22,74 | 3,0 | non démontré | — |  |

#### Shadow Tournament

| Marché | Graine | Concurrents | Vainqueur (sélection) | P&L évaluation (€) | Malédiction du vainqueur (/jour) | Corrélation de rang sélection→évaluation |
|---|---|---|---|---|---|---|
| noise@0.0 | 1001 | 57 | cash|100 | +0,00 | +0,000 % | -0,46 |
| noise@0.0 | 2002 | 57 | error_replay|tout | -1,26 | +0,058 % | +0,28 |
| noise@0.0 | 3003 | 57 | cash|100 | +0,00 | +0,000 % | +0,42 |
| regimes@1.0 | 1001 | 57 | council_full | +6,53 | -0,041 % | +0,22 |
| regimes@1.0 | 2002 | 57 | cash|100 | +0,00 | +0,000 % | +0,99 |
| regimes@1.0 | 3003 | 57 | error_replay|tout | +2,92 | -0,010 % | +0,16 |
| regime_shift@1.0 | 1001 | 57 | error_replay|200 | -0,06 | +0,010 % | +0,76 |
| regime_shift@1.0 | 2002 | 57 | cash|100 | +0,00 | +0,000 % | +1,00 |
| regime_shift@1.0 | 3003 | 57 | cash|100 | +0,00 | +0,000 % | +0,94 |
| hidden_rule@1.0 | 1001 | 57 | regime_router|tout | +40,23 | -0,079 % | +0,98 |
| hidden_rule@1.0 | 2002 | 57 | error_replay|100 | +5,11 | +0,061 % | +0,79 |
| hidden_rule@1.0 | 3003 | 57 | error_replay|50 | +19,00 | -0,005 % | +0,96 |
| hidden_rule@0.5 | 1001 | 57 | teacher:breakout|100 | +15,40 | -0,056 % | +0,69 |
| hidden_rule@0.5 | 2002 | 57 | project_mixte|400 | -2,20 | +0,092 % | -0,04 |
| hidden_rule@0.5 | 3003 | 57 | error_replay|100 | +3,32 | +0,041 % | +0,67 |

Verdicts du tournoi : noise@0.0 : non démontré (1/3); regimes@1.0 : non démontré (1/3); regime_shift@1.0 : avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés (3/3); hidden_rule@1.0 : avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés (2/3); hidden_rule@0.5 : avantage relatif seulement ; gain absolu non démontré sur les mêmes marchés (2/3)

## Historiques publics (exploratoire)

Données : public-exploratoire · préenregistrement `afc5cd13065c429d…` · confiance par intervalle 99,85 % · durée 151.5 s

#### real-all@nan

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | tout | +0,00 | +14,41 | 0,0 | 0,0 % | -0,110 | 0,500 (0,663) | 0,00 | 0,1 | +0,30 | 0/1 | non démontré |
| error_replay | tout | -4,37 | +10,04 | 154,0 | 27,1 % | -0,017 | 0,663 (0,663) | 9,72 | 5,2 | +0,21 | 1/1 | non démontré |
| exit_learner | tout | -11,43 | +2,99 | 312,0 | 41,9 % | -0,110 | 0,500 (0,663) | 0,19 | 0,8 | +0,06 | 0/1 | non démontré |
| project_mixte | tout | -3,37 | +11,04 | 171,0 | 22,7 % | +0,008 | 0,672 (0,663) | 1,34 | 3,7 | +0,23 | 1/1 | non démontré |
| regime_router | tout | -0,84 | +13,58 | 100,0 | 21,7 % | +0,003 | 0,679 (0,663) | 0,07 | 0,2 | +0,28 | 1/1 | non démontré |
| router_with_exits | tout | -0,84 | +13,58 | 100,0 | 21,7 % | +0,003 | 0,679 (0,663) | 0,20 | 0,8 | +0,28 | 1/1 | non démontré |
| take_all | tout | -14,41 | +0,00 | 394,0 | 49,2 % | -0,110 | 0,500 (0,663) | 0,00 | 0,1 | +0,00 | 0/1 | non démontré |
| teacher:breakout | tout | -2,97 | +11,44 | 154,0 | 23,6 % | -0,110 | 0,500 (0,663) | 0,00 | 0,1 | +0,24 | 1/1 | non démontré |
| teacher:mean_reversion | tout | -4,56 | +9,85 | 158,0 | 15,4 % | -0,110 | 0,500 (0,663) | 0,00 | 0,1 | +0,21 | 0/1 | non démontré |
| teacher:pullback | tout | -9,97 | +4,44 | 311,0 | 34,8 % | -0,110 | 0,500 (0,663) | 0,00 | 0,1 | +0,09 | 0/1 | non démontré |
| teacher:trend | tout | -0,29 | +14,12 | 141,0 | 13,7 % | -0,110 | 0,500 (0,663) | 0,00 | 0,1 | +0,30 | 1/1 | non démontré |

#### real-group-1@nan

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | tout | +0,00 | +12,19 | 0,0 | 0,0 % | -0,137 | 0,500 (0,697) | 0,00 | 0,1 | +0,50 | 0/1 | non démontré |
| error_replay | tout | -3,36 | +8,84 | 125,0 | 27,6 % | -0,029 | 0,672 (0,697) | 19,79 | 2,9 | +0,36 | 1/1 | non démontré |
| exit_learner | tout | -11,33 | +0,87 | 252,0 | 41,0 % | -0,137 | 0,500 (0,697) | 0,07 | 0,4 | +0,04 | 0/1 | non démontré |
| project_mixte | tout | +2,16 | +14,36 | 75,0 | 14,4 % | +0,006 | 0,694 (0,697) | 26,82 | 2,0 | +0,58 | 1/1 | non démontré |
| regime_router | tout | -4,67 | +7,53 | 159,0 | 22,1 % | -0,001 | 0,689 (0,697) | 0,03 | 0,1 | +0,31 | 1/1 | non démontré |
| router_with_exits | tout | -2,41 | +9,78 | 112,0 | 21,2 % | -0,001 | 0,689 (0,697) | 0,10 | 0,4 | +0,40 | 1/1 | non démontré |
| take_all | tout | -12,19 | +0,00 | 318,0 | 42,6 % | -0,137 | 0,500 (0,697) | 0,00 | 0,1 | +0,00 | 0/1 | non démontré |
| teacher:breakout | tout | -3,14 | +9,06 | 125,0 | 27,4 % | -0,137 | 0,500 (0,697) | 0,00 | 0,1 | +0,37 | 1/1 | non démontré |
| teacher:mean_reversion | tout | -2,91 | +9,29 | 105,0 | 10,1 % | -0,137 | 0,500 (0,697) | 0,00 | 0,1 | +0,38 | 0/1 | non démontré |
| teacher:pullback | tout | -7,55 | +4,64 | 222,0 | 25,9 % | -0,137 | 0,500 (0,697) | 0,00 | 0,1 | +0,19 | 0/1 | non démontré |
| teacher:trend | tout | -2,96 | +9,24 | 109,0 | 16,1 % | -0,137 | 0,500 (0,697) | 0,00 | 0,1 | +0,38 | 0/1 | non démontré |

#### real-group-2@nan

| Concurrent | Budget | P&L net moyen (€) | Écart vs toutes les entrées (€) | Trades | Baisse max. | Brier vs taux enseignant | AUC (réf. enseignant) | CPU (s) | Mo | Écart €/100 obs. | Réplications > 0 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cash | tout | +0,00 | +7,97 | 0,0 | 0,0 % | -0,049 | 0,500 (0,602) | 0,00 | 0,1 | +0,24 | 0/1 | non démontré |
| error_replay | tout | -0,74 | +7,23 | 141,0 | 18,6 % | +0,002 | 0,620 (0,602) | 8,81 | 3,7 | +0,22 | 0/1 | non démontré |
| exit_learner | tout | -8,11 | -0,15 | 311,0 | 36,1 % | -0,049 | 0,500 (0,602) | 0,09 | 0,5 | -0,00 | 0/1 | non démontré |
| project_mixte | tout | -1,64 | +6,32 | 122,0 | 25,0 % | +0,000 | 0,625 (0,602) | 1,43 | 2,6 | +0,19 | 0/1 | non démontré |
| regime_router | tout | +0,61 | +8,58 | 89,0 | 19,4 % | -0,000 | 0,619 (0,602) | 0,06 | 0,2 | +0,26 | 0/1 | non démontré |
| router_with_exits | tout | +0,61 | +8,58 | 89,0 | 19,4 % | -0,000 | 0,619 (0,602) | 0,12 | 0,5 | +0,26 | 0/1 | non démontré |
| take_all | tout | -7,97 | +0,00 | 374,0 | 33,4 % | -0,049 | 0,500 (0,602) | 0,00 | 0,1 | +0,00 | 0/1 | non démontré |
| teacher:breakout | tout | -1,41 | +6,55 | 159,0 | 23,3 % | -0,049 | 0,500 (0,602) | 0,00 | 0,1 | +0,20 | 0/1 | non démontré |
| teacher:mean_reversion | tout | -3,27 | +4,70 | 95,0 | 10,9 % | -0,049 | 0,500 (0,602) | 0,00 | 0,1 | +0,14 | 0/1 | non démontré |
| teacher:pullback | tout | -8,32 | -0,36 | 253,0 | 27,8 % | -0,049 | 0,500 (0,602) | 0,00 | 0,1 | -0,01 | 0/1 | non démontré |
| teacher:trend | tout | +2,32 | +10,29 | 106,0 | 6,8 % | -0,049 | 0,500 (0,602) | 0,00 | 0,1 | +0,31 | 0/1 | non démontré |

#### Ablation du conseil — real-all@nan

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | -2,77 | 55,0 | non démontré | — |  |
| without:structure | -9,50 | 142,0 | non démontré | +6,73 | retirable (contribution non démontrée) |
| only:structure | -5,42 | 263,0 | non démontré | — |  |
| without:entry_exit | -6,93 | 244,0 | non démontré | +4,16 | retirable (contribution non démontrée) |
| only:entry_exit | -4,44 | 155,0 | non démontré | — |  |
| without:liquidity | +1,39 | 62,0 | non démontré | -4,16 | retirable (contribution non démontrée) |
| only:liquidity | -14,48 | 329,0 | non démontré | — |  |
| without:macro | -2,77 | 55,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:macro | -14,71 | 384,0 | non démontré | — |  |
| without:red_team | -0,44 | 61,0 | non démontré | -2,33 | retirable (contribution non démontrée) |
| only:red_team | -14,71 | 384,0 | non démontré | — |  |
| without:risk | -2,27 | 58,0 | non démontré | -0,50 | retirable (contribution non démontrée) |
| only:risk | -14,42 | 392,0 | non démontré | — |  |
| vote | -8,24 | 160,0 | non démontré | — |  |
| take_all | -14,41 | 394,0 | non démontré | — |  |
| single_model | -3,37 | 171,0 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | -15,07 | 6,0 | non démontré | — |  |

#### Ablation du conseil — real-group-1@nan

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | -2,69 | 47,0 | non démontré | — |  |
| without:structure | -9,27 | 111,0 | non démontré | +6,57 | retirable (contribution non démontrée) |
| only:structure | -5,50 | 206,0 | non démontré | — |  |
| without:entry_exit | -6,76 | 186,0 | non démontré | +4,07 | retirable (contribution non démontrée) |
| only:entry_exit | -5,16 | 127,0 | non démontré | — |  |
| without:liquidity | -0,52 | 50,0 | non démontré | -2,18 | retirable (contribution non démontrée) |
| only:liquidity | -12,49 | 258,0 | non démontré | — |  |
| without:macro | -2,69 | 47,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:macro | -11,86 | 320,0 | non démontré | — |  |
| without:red_team | -2,69 | 47,0 | non démontré | -0,00 | retirable (contribution non démontrée) |
| only:red_team | -11,86 | 320,0 | non démontré | — |  |
| without:risk | -2,69 | 47,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:risk | -11,57 | 319,0 | non démontré | — |  |
| vote | -4,96 | 139,0 | non démontré | — |  |
| take_all | -12,19 | 318,0 | non démontré | — |  |
| single_model | +2,16 | 75,0 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | -14,24 | 3,0 | non démontré | — |  |

#### Ablation du conseil — real-group-2@nan

| Variante | P&L net moyen (€) | Trades | vs toutes les entrées | Contribution marginale (€) | Recommandation |
|---|---|---|---|---|---|
| council_full | -2,00 | 35,0 | non démontré | — |  |
| without:structure | -4,03 | 100,0 | non démontré | +2,03 | retirable (contribution non démontrée) |
| only:structure | -3,54 | 216,0 | non démontré | — |  |
| without:entry_exit | -3,47 | 191,0 | non démontré | +1,47 | retirable (contribution non démontrée) |
| only:entry_exit | -2,59 | 143,0 | non démontré | — |  |
| without:liquidity | -2,61 | 49,0 | non démontré | +0,62 | retirable (contribution non démontrée) |
| only:liquidity | -9,02 | 327,0 | non démontré | — |  |
| without:macro | -2,00 | 35,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:macro | -7,59 | 375,0 | non démontré | — |  |
| without:red_team | -0,20 | 43,0 | non démontré | -1,79 | retirable (contribution non démontrée) |
| only:red_team | -7,59 | 375,0 | non démontré | — |  |
| without:risk | -2,00 | 35,0 | non démontré | +0,00 | retirable (contribution non démontrée) |
| only:risk | -7,35 | 374,0 | non démontré | — |  |
| vote | -4,20 | 187,0 | non démontré | — |  |
| take_all | -7,97 | 374,0 | non démontré | — |  |
| single_model | -1,64 | 122,0 | non démontré | — |  |
| cash | +0,00 | 0,0 | non démontré | — |  |
| buy_and_hold | -11,68 | 3,0 | non démontré | — |  |

#### Shadow Tournament

| Marché | Graine | Concurrents | Vainqueur (sélection) | P&L évaluation (€) | Malédiction du vainqueur (/jour) | Corrélation de rang sélection→évaluation |
|---|---|---|---|---|---|---|
| real-all@nan | -1 | 35 | council_full | -2,77 | +0,073 % | +0,44 |
| real-group-1@nan | -1 | 35 | router_with_exits|400 | -0,89 | +0,056 % | +0,54 |
| real-group-2@nan | -1 | 35 | regime_router|400 | +0,34 | +0,027 % | +0,63 |

Verdicts du tournoi : real-all@nan : non démontré (1/1); real-group-1@nan : non démontré (1/1); real-group-2@nan : non démontré (0/1)
