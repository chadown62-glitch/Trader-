# Campagne de développement autonome M54 → M59 — point de reprise

Fichier versionné, mis à jour à la fin de chaque mission ou avant une interruption prévisible.
Paper trading uniquement : 30 € fictifs, aucun ordre réel, aucune clé privée, aucun levier, aucun service permanent,
aucune fusion sur `main`, aucune promotion de modèle.

## Mission active

**Aucune.** M54 à M57 sont terminées et publiées en brouillon. M58 et M59 attendent une décision humaine : `SPEC.md`
ne marque aucun sprint `[EN COURS]` (les sprints 1 à 4 sont `[TERMINÉ]`), et la loi du dépôt (`CLAUDE.md`, § 2)
limite le travail au sprint en cours.

## Missions terminées

| Mission | Branche | Dernier commit | PR |
|---|---|---|---|
| #50 COST OPTIMIZER | `claude/cost-optimizer-v1` | `3a8a048` | #50 (brouillon) |
| #51 DIRECT MARKET HUB | `claude/direct-market-hub-v1` | `f4edab8` | #51 (brouillon) |
| #52 durcissement du hub | `claude/hub-hardening-v1` | `eec05e7` | #52 (brouillon, base #51) |
| #52 préparation d'ALPHA FACTORY v2 | `claude/alpha-factory-v2-readiness` | `ff46509` | #53 (brouillon, base #50) |
| M54 évaluation unique d'ALPHA FACTORY v2 | `claude/alpha-factory-v2-evaluation` | `5284a71` | #54 (brouillon, base #53) |
| M55 protocole prospectif et diagnostic | `claude/alpha-factory-v2-prospective` | `18b31bb` | #55 (brouillon, base #54, gelée) |
| Calibrage des sorties et de la taille (memecoin) | `feat/memecoin-strategy-calibration` | `dc2a19f` | #67 (fusionnée) |
| Sorties calibrées dans la simulation papier | `feat/memecoin-simulator-calibrated-exits` | `807432c` | #69 (prête ; CI rouge pour dérive d'environnement) |
| M56 MEMECOIN ALPHA ENGINE | `claude/memecoin-alpha-v1` | `c6e2b9c` | #70 (brouillon, base `main`) |
| M57 flotte de 12 agents | `claude/agent-fleet-v1` | voir la PR | #71 (brouillon, base #70) |

### Résultat de M54 (exécution unique, 17/11/2026)

Plan `alpha-factory-v2-e1` `6f4f436f…` (commit `d4a9969`, poussé avant l'exécution) ; résultats `results.json`
`278f320e…`. Verdict fixé d'avance : **avantage démontré sur l'historique pour `mom_ts_28` seulement** (momentum
4 semaines BTC/ETH, hebdomadaire) : p corrigée 0,0006 (absolu) et 0,022 (relatif), positif sur les deux périodes et en
tension ; MAIS avantage en forte baisse en période 2, 65 % des gains sur ETH, baisse maximale 38 % (> 10 %), données
BTC/ETH déjà étudiées par le projet. 4 « prometteurs non démontrés », 10 échecs. Rapport :
`docs/validation/alpha-factory-v2-evaluation.md`.

### Résultat de M55

Protocole prospectif `alpha-factory-v2-p1` `bb162f0d…` enregistré le 17/11/2026 (commit `2e9e483`) : `mom_ts_28` figé,
première décision à la clôture du **22/11/2026**, un regard par an (9 au plus), bornes d'O'Brien-Fleming. Probabilité de
confirmer en 5 ans : 55 % si l'avantage historique persiste, 29 % s'il ressemble à la période 2. Diagnostic descriptif :
avantage né des marchés haussiers et agités ; 48 % d'épisodes gagnants, 5 épisodes = 61 % des gains ; sur 30 €, la taille
compatible avec la limite de 10 % est impossible (ordres de 3,95 € < 10 €). Rapport : `docs/validation/alpha-factory-v2-prospective.md`.

### Résultat de M56 (deux collectes, 17 et 20/11/2026)

Règles du veto, de sélection et de coûts figées dans `dc72a76` avant toute analyse. **Aucun avantage démontré** :
136 pools, 127 contrôlés (121 tokens), **127 refusés** par le veto ; liquidité médiane 1 601 $ (9 pools ≥ 20 000 $) ;
42 sans route de revente ; 82 sans aucun échange après la décision. Règles : 1 à 2 ordres en entraînement, 0 en
développement. Partie réservée intacte (`839f7c22…`). Coût d'aller-retour : 3,0 % mesuré (72 devis Jupiter) contre
3,3 % modélisé. Rapport : `docs/validation/memecoin-alpha-v1.md` ; données : `docs/validation/mesures/memecoin-v1/`.

### Résultat de M57

Douze agents et un coordinateur local sans pouvoir d'ordre. Rejeu des deux captures : 39 puis 65 veto, aucune
proposition, aucun échec d'agent, 0,09 s puis 0,21 s. Rapport : `docs/validation/agent-fleet-v1.md`.

PR préservées, non modifiées : #40, #46, #47, #48 (R1 figé), #49.

## Données et empreintes (M54)

- Archive journalière publique (Binance, paires USDT, 775 symboles dont 257 retirés de la cote, du 17/08/2017 au 12/11/2026) :
  manifeste `f301ffe98652c3c0d2f4a87269e1b62187612dbf0c51206bac133dc6c61feb23`, catalogue `3ec824f817706fe1f91d952ea25c68ccb33229bad9b018a950876f59fed24d65`.
- Versionnés dans `research/alpha-factory-v2/archive/` : manifeste (empreinte de chaque fichier) et catalogue compressé
  (le catalogue ne se retélécharge pas à l'identique). Les bougies (29 Mo) ne sont pas versionnées : copie de travail dans
  `data/alpha-lab/archive-20261113/` (ignoré par Git) ; elles se retéléchargent à l'identique (voir « Reprise »).
- Contrat de coûts adopté : `research/alpha-factory-v2/cost-hypotheses-v2.json` = proposition #50, empreinte
  `56da1470cf32e1603618dd52f54e22bd0a2134db08d947d0bea45d64ac179c40` ; décision : `cost-adoption-v2.json`.

## Prochaines actions précises

1. Observation hebdomadaire, à la main, à partir du 23/11/2026 : `python -m tools.alpha_prospective fetch --root
   data/alpha-prospective/AAAA-MM-JJ` puis `observe --root …` ; versionner le journal. Ne rien changer au candidat.
2. PR #69 : la CI échoue pour des raisons d'environnement, pas de code (scikit-learn 1.9.1 sur ubuntu, fins de ligne
   des fichiers gelés sous Windows, Playwright 1.56.1 sur ubuntu 26.04). Correctifs proposés dans sa description ;
   ils touchent `.github/` et attendent un accord.
3. M58 (intégration, interface, performances) : à cadrer après accord humain et un sprint `[EN COURS]` dans `SPEC.md`.
   Contraintes relevées : `main` a sa propre pile memecoin (`sentinelle/dmh/memecoin_*`, onglet Memecoin) ; la PR #68
   ouverte modifie `sentinelle/dmh/api.py`, `solana_scanner.py` et `web/js/views/memecoin.js`. Cette branche
   (`claude/integration-v1`) contient la lignée gelée #54/#55 : **ne pas l'ouvrir telle quelle vers `main`**. Repartir
   de #71 si M58 est autorisée.
4. Piste M56 v2 (non appliquée, nouveau préenregistrement requis) : relire les tokens 30 à 60 min après leur création,
   chercher parmi les pools ≥ 20 000 $, collecter plusieurs jours.

## Erreurs, blocages

- CI de la PR #69 rouge pour dérive d'environnement (voir ci-dessus).
- Aucun sprint `[EN COURS]` dans `SPEC.md` : M58 et M59 en attente.

## Reprise

- Python 3.12 isolé : `python -m pytest -q -p no:cacheprovider tests/test_alpha_lab*.py` (données synthétiques uniquement).
- Archive perdue : retélécharger les bougies avec `python -m tools.alpha_lab_fetch` dans un dossier neuf, puis y remettre le
  manifeste et le catalogue versionnés (`gunzip -k exchangeInfo.json.gz`) ; `python -m tools.alpha_lab verify --archive DOSSIER`
  refuse toute différence (empreinte par fichier).
- Ne jamais relancer `run` sur ce plan pour « confirmer » : le journal en garde une seule exécution.
