Pools : 136 (bougies : 113, 5791 minutes) · contrôlés : 127 · découpage {'train': 82, 'dev': 27, 'reserved': 27} · liste réservée `839f7c22e88781a1…` · SOL ≈ 71 $

#### Veto (à la collecte)

| Décision | Tokens |
|---|---|
| VETO | 127 |
| ABSTENTION | 0 |
| PASSE | 0 |

Motifs les plus fréquents : signalement (173) ; pool trop récent (127) ; liquidité sous le minimum (118) ; plus gros détenteur (54) ; 10 plus gros détenteurs (52) ; aucune route de revente (42) ; liquidité contradictoire entre sources (19) ; aller-retour immédiat (18).

#### Contrôle en avant du veto (décision à la collecte, issue lue plus tard)

| Décision | Tokens | Effondrés (−90 %) | Rendement médian | Moyenne nette après coûts | Part nette positive | Minutes après |
|---|---|---|---|---|---|---|
| VETO | 45 | +9 % | -0,6 % | -33,7 % | +11 % | 25 |

Issue lisible pour 45 tokens ; aucun échange après la décision : 82 ; aucune bougie avant la décision : 0 ; sans seconde lecture : 0 ; sans contrôle : 9. Liquidité des pools à la collecte : médiane 1 601 $ ; 9e décile 11 518 $ ; au moins le minimum du veto : 9 pools.

Par famille de motif (descriptif ; un token peut cumuler plusieurs motifs) :

| Motif | Tokens | Effondrés (−90 %) | Rendement médian | Moyenne nette après coûts | Part nette positive |
|---|---|---|---|---|---|
| créateur déjà signalé | 16 | +6 % | -0,3 % | -33,4 % | +0 % |
| concentration des détenteurs | 6 | +0 % | -26,8 % | -101,2 % | +17 % |
| revente impossible ou chère | 9 | +11 % | -18,4 % | -115,2 % | +0 % |
| liquidité faible | 42 | +10 % | -1,0 % | -37,0 % | +5 % |
| liquidité non verrouillée | 8 | +38 % | +12,2 % | -20,4 % | +62 % |
| aucun signalement Rugcheck « danger » | 16 | +0 % | -1,9 % | -14,2 % | +0 % |

#### Règles de sélection (ordre de 11 $ ≈ 10 €, coûts complets)

| Règle | Partie | Ordres | Gagnants | Moyenne nette [IC 95 %] | Médiane | Total net ($) | Facteur de profit | Dans des pools effondrés |
|---|---|---|---|---|---|---|---|---|
| momentum_15 | train:central | 1 | +100 % | +7,9 % [+7,9 % ; +7,9 %] | +7,9 % | +0.86 | — | +100 % |
| momentum_15 | train:prudent | 1 | +100 % | +7,9 % [+7,9 % ; +7,9 %] | +7,9 % | +0.86 | — | +100 % |
| momentum_15 | train:sans_veto | 1 | +100 % | +7,9 % [+7,9 % ; +7,9 %] | +7,9 % | +0.86 | — | — |
| momentum_15 | train:avec_veto | 0 | — | — | — | — | — | — |
| momentum_15 | train:retard_2_min | 1 | +0 % | -7,8 % [-7,8 % ; -7,8 %] | -7,8 % | -0.86 | 0,00 | — |
| momentum_15 | dev:central | 0 | — | — | — | — | — | — |
| momentum_15 | dev:prudent | 0 | — | — | — | — | — | — |
| momentum_15 | dev:sans_veto | 0 | — | — | — | — | — | — |
| momentum_15 | dev:avec_veto | 0 | — | — | — | — | — | — |
| momentum_15 | dev:retard_2_min | 0 | — | — | — | — | — | — |
| breakout_60 | train:central | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | +0 % |
| breakout_60 | train:prudent | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | +0 % |
| breakout_60 | train:sans_veto | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | — |
| breakout_60 | train:avec_veto | 0 | — | — | — | — | — | — |
| breakout_60 | train:retard_2_min | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | — |
| breakout_60 | dev:central | 0 | — | — | — | — | — | — |
| breakout_60 | dev:prudent | 0 | — | — | — | — | — | — |
| breakout_60 | dev:sans_veto | 0 | — | — | — | — | — | — |
| breakout_60 | dev:avec_veto | 0 | — | — | — | — | — | — |
| breakout_60 | dev:retard_2_min | 0 | — | — | — | — | — | — |
| trend_ema | train:central | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | +0 % |
| trend_ema | train:prudent | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | +0 % |
| trend_ema | train:sans_veto | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | — |
| trend_ema | train:avec_veto | 0 | — | — | — | — | — | — |
| trend_ema | train:retard_2_min | 2 | +0 % | -4,9 % [-6,9 % ; -2,9 %] | -4,9 % | -1.07 | 0,00 | — |
| trend_ema | dev:central | 0 | — | — | — | — | — | — |
| trend_ema | dev:prudent | 0 | — | — | — | — | — | — |
| trend_ema | dev:sans_veto | 0 | — | — | — | — | — | — |
| trend_ema | dev:avec_veto | 0 | — | — | — | — | — | — |
| trend_ema | dev:retard_2_min | 0 | — | — | — | — | — | — |
| reversal_30 | train:central | 2 | +0 % | -15,9 % [-24,1 % ; -7,8 %] | -15,9 % | -3.51 | 0,00 | +100 % |
| reversal_30 | train:prudent | 2 | +0 % | -15,9 % [-24,1 % ; -7,8 %] | -15,9 % | -3.51 | 0,00 | +100 % |
| reversal_30 | train:sans_veto | 2 | +0 % | -15,9 % [-24,1 % ; -7,8 %] | -15,9 % | -3.51 | 0,00 | — |
| reversal_30 | train:avec_veto | 0 | — | — | — | — | — | — |
| reversal_30 | train:retard_2_min | 2 | +0 % | -52,6 % [-97,3 % ; -7,8 %] | -52,6 % | -11.57 | 0,00 | — |
| reversal_30 | dev:central | 0 | — | — | — | — | — | — |
| reversal_30 | dev:prudent | 0 | — | — | — | — | — | — |
| reversal_30 | dev:sans_veto | 0 | — | — | — | — | — | — |
| reversal_30 | dev:avec_veto | 0 | — | — | — | — | — | — |
| reversal_30 | dev:retard_2_min | 0 | — | — | — | — | — | — |
| fast_exit | train:central | 1 | +100 % | +8,5 % [+8,5 % ; +8,5 %] | +8,5 % | +0.94 | — | +100 % |
| fast_exit | train:prudent | 1 | +100 % | +8,5 % [+8,5 % ; +8,5 %] | +8,5 % | +0.94 | — | +100 % |
| fast_exit | train:sans_veto | 1 | +100 % | +8,5 % [+8,5 % ; +8,5 %] | +8,5 % | +0.94 | — | — |
| fast_exit | train:avec_veto | 0 | — | — | — | — | — | — |
| fast_exit | train:retard_2_min | 1 | +0 % | -7,8 % [-7,8 % ; -7,8 %] | -7,8 % | -0.86 | 0,00 | — |
| fast_exit | dev:central | 0 | — | — | — | — | — | — |
| fast_exit | dev:prudent | 0 | — | — | — | — | — | — |
| fast_exit | dev:sans_veto | 0 | — | — | — | — | — | — |
| fast_exit | dev:avec_veto | 0 | — | — | — | — | — | — |
| fast_exit | dev:retard_2_min | 0 | — | — | — | — | — | — |

Validation du modèle de coûts sur 72 allers-retours mesurés (devis Jupiter de 0,05 SOL) : mesuré +3,0 %, modèle +3,3 %, écart médian -0,3 % (écart positif : le marché coûte plus que le modèle (le modèle est alors optimiste)).

#### Tests de stress (règle momentum_15, scénarios synthétiques)

| Scénario | Résultat |
|---|---|
| référence (tendance régulière) | 1 ordre(s), moyenne nette +18,3 %, total +2.01 $ |
| retrait de liquidité pendant la position | 1 ordre(s), moyenne nette -96,9 %, total -10.66 $ |
| frais du réseau ×100 (congestion) | 1 ordre(s), moyenne nette +3,6 %, total +0.39 $ |
| frais du DEX ×3 | 1 ordre(s), moyenne nette +16,9 %, total +1.85 $ |
| toutes les transactions échouent une fois | 1 ordre(s), moyenne nette +18,3 %, total +2.01 $ |
| retard de 3 minutes | 1 ordre(s), moyenne nette +18,3 %, total +2.01 $ |
| liquidité ÷100 (600 $) | 1 ordre(s), moyenne nette +6,3 %, total +0.69 $ |
| taxe de transfert activée (Token-2022) | veto : VETO (extension Token-2022 à risque (frais de transfert, crochet ou délégué permanent)) |
| toutes les sources de sécurité en panne | veto : ABSTENTION (aucun contrôle de sécurité pour ce token) |
