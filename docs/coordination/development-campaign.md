# Campagne de développement autonome M54 → M59 — point de reprise

Fichier versionné, mis à jour à la fin de chaque mission ou avant une interruption prévisible.
Paper trading uniquement : 30 € fictifs, aucun ordre réel, aucune clé privée, aucun levier, aucun service permanent,
aucune fusion sur `main`, aucune promotion de modèle.

## Mission active

**M54 — évaluation unique d'ALPHA FACTORY v2** · branche `claude/alpha-factory-v2-evaluation` (base : `claude/alpha-factory-v2-readiness`, PR #53).

## Missions terminées

| Mission | Branche | Dernier commit | PR |
|---|---|---|---|
| #50 COST OPTIMIZER | `claude/cost-optimizer-v1` | `3a8a048` | #50 (brouillon) |
| #51 DIRECT MARKET HUB | `claude/direct-market-hub-v1` | `f4edab8` | #51 (brouillon) |
| #52 durcissement du hub | `claude/hub-hardening-v1` | `eec05e7` | #52 (brouillon, base #51) |
| #52 préparation d'ALPHA FACTORY v2 | `claude/alpha-factory-v2-readiness` | `ff46509` | #53 (brouillon, base #50) |

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

1. M54 : enregistrer le plan (`python -m tools.alpha_lab register --archive data/alpha-lab/archive-20261113`), le versionner,
   puis UNE exécution (`run --output docs/validation/mesures/alpha-factory-v2`), publier tous les résultats, PR brouillon.
2. M55 : selon le verdict de M54 (diagnostic des échecs ou préparation prospective) ; ne jamais réutiliser la fenêtre
   2020-01-01 → 2026-11-12 comme données vierges.
3. M56 → M59 : voir le programme (memecoins Solana, flotte d'agents, intégration, audit global).

## Erreurs, blocages

Aucun à ce stade.

## Reprise

- Python 3.12 isolé : `python -m pytest -q -p no:cacheprovider tests/test_alpha_lab*.py` (données synthétiques uniquement).
- Archive perdue : retélécharger les bougies avec `python -m tools.alpha_lab_fetch` dans un dossier neuf, puis y remettre le
  manifeste et le catalogue versionnés (`gunzip -k exchangeInfo.json.gz`) ; `python -m tools.alpha_lab verify --archive DOSSIER`
  refuse toute différence (empreinte par fichier).
- Ne jamais relancer `run` sur ce plan pour « confirmer » : le journal en garde une seule exécution.
