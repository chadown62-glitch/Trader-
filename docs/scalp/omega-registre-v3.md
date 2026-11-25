# SENTINELLE OMEGA — Registre de recherche et passation
Date : 25 novembre 2026. Statut : propositions de recherche, pas de nouvelles missions ni de fonctionnalités validées.

## Gouvernance
- Sprint 5 et catalogue C001–C100 uniquement; rattachement exact à établir dans SPEC.md avant développement.
- Paper trading exclusivement; pas de passage à l'argent réel, de levier, de short, de clé privée, de promotion automatique, ni d'observation prospective sans autorisation.
- PR #54/#55 gelées. Claude Code et GPT Work polyvalents après actualisation du cadrage; audit croisé, aucun doublon, réservation des fichiers. Au plus quatre agents actifs dans le moteur.
- Le 1er décembre est un bilan de recherche; le 5 décembre est un bilan scientifique, non une date d'investissement.
- Le registre ne change pas les contraintes de sécurité et ne prétend pas démontrer une rentabilité.

## Catalogue conservé
| Concept | Fonction | Test proposé |
|---|---|---|
| HIVE | Apprentissage mutualisé | Comparer modèle individuel, mutualisé et hybride sur partitions temporelles et actifs jamais vus. |
| GENOME | Profils statistiques par actif | Mesurer stabilité, effectif disponible et erreur des profils par famille. |
| RADAR | Univers d'observation étendu | Comparer couverture, qualité des données et apport marginal; univers point-in-time. |
| ATLAS | Dépendances entre actifs | Mesurer stabilité des corrélations, regroupements et dépendance en crise. |
| PHANTOM | Portefeuilles fictifs parallèles | Rejouer les mêmes données et coûts sans ordre réel ni modification du portefeuille principal. |
| ORACLE | Prévisions probabilistes | Mesurer Brier, calibration, incertitude et couverture. |
| NEMESIS | Contre-expertise adversariale | Injecter ruptures, données manquantes, coûts et défauts simulés. |
| CORTEX | Combinaison de modèles | Comparer aux modèles seuls, à des références simples et aux erreurs corrélées. |
| CHRONOS | Échelles temporelles | Vérifier l'alignement temporel et la contribution de chaque horizon. |
| MEMORY | Mémoire des erreurs | Indexer essais, hypothèses rejetées, versions et causes d'échec. |
| PULSE | Détection de dérive | Mesurer délai, fausses alertes et stabilité sur ruptures synthétiques. |
| IMMUNITY | Qualité et anomalies des données | Tester données incohérentes, périmées, manipulées, absence et provenance. |
| EVOLUTION | Champion/challenger | Comparer des configurations figées; aucune promotion automatique. |
| TITAN | Efficience informatique | Mesurer temps, mémoire, I/O et reproductibilité sur matériel cible. |
| TRUTH | Intégrité scientifique | Contrôler essais multiples, biais, témoins et réutilisation de l'historique. |
| BORDER | Détection de transfert négatif | Comparer transfert et estimation individuelle sur familles cohérentes et incohérentes; évaluer sur données non utilisées pour sélectionner. |
| ECHO | Redondance des connaissances | Mesurer la corrélation des erreurs entre modèles; comparer à une référence simple et éliminer les doublons conceptuels. |
| QUARANTINE | Gestion explicite de l'inconnu | Évaluer la capacité à signaler un actif ou régime hors distribution; aucun veto assoupli. |
| TIMELOCK | Traçabilité temporelle | Vérifier date de publication, réception, disponibilité et corrections des sources. |
| REPLAY | Bibliothèque de ruptures synthétiques | Construire des scénarios reproductibles de dérive et d'incidents, indépendants des résultats financiers historiques. |

## Expérience synthétique HIVE / BORDER effectivement réalisée
Graine NumPy 20261125. 1 000 répétitions, chacune avec 20 familles de 50 actifs (1 million d'estimations par méthode et scénario).
Paramètre latent = effet famille N(0, 0.7²) + effet individuel N(0, 0.4²); estimateur individuel bruité par N(0, 1/8).
Estimateur collectif = moyenne des 50 estimations de la famille; hybride = 0.55 individuel + 0.45 collectif (pondération fixée pour cette expérience, non optimisée sur les résultats).
Scénarios : stabilité, bruit supplémentaire futur N(0, 0.8²), inversion de l'effet individuel pour 35 % des actifs, inversion de l'effet famille, puis familles peu cohérentes (effet famille N(0, 0.15²), individuel N(0, 1.2²)).
Métrique : erreur quadratique moyenne sur le paramètre latent artificiel, pas un rendement ni un backtest.
| Scénario | Individuel | Collectif | Hybride |
|---|---:|---:|---:|
| stable | 0.125195 | 0.159070 | 0.071322 |
| heterogeneite_accrue | 0.764807 | 0.798175 | 0.710705 |
| rupture_individuelle | 0.349323 | 0.163622 | 0.196640 |
| rupture_collective | 2.115646 | 2.149521 | 2.061772 |
| familles_peu_coherentes | 0.125121 | 1.409114 | 0.324619 |

Conclusion : mutualiser n'est pas toujours bénéfique. Dans le scénario de familles peu cohérentes, l'hybride dégrade l'estimation individuelle. Une rupture collective fait échouer les trois méthodes. Tester le transfert négatif, la dérive et le signalement de l'inconnu est plus important que de supposer un bénéfice uniforme.
Limites : génération artificielle, paramètres choisis pour illustrer des mécanismes, aucune donnée de marché, pas d'intervalle de confiance ni preuve de bénéfice financier. Les simulations chiffrées annoncées précédemment dans la conversation ne sont pas utilisées comme preuves ici.

## Ordre proposé de recherche, non lancé
1. Réconcilier les concepts avec le catalogue C001–C100 et le dossier de preuves; ne pas dupliquer les capacités.
2. Définir les références, les partitions, le coût de calcul, les métriques et les critères de rejet avant toute évaluation.
3. Tester HIVE/BORDER, CORTEX/ECHO et RADAR/TIMELOCK sur données synthétiques et partitions autorisées, sans présenter l'historique déjà consulté comme aveugle.
4. Auditer les résultats et conserver aussi les échecs. Pas de promotion ni de nouvelle observation prospective sans autorisation.
5. Actualiser le registre et le dossier indépendant, avec SHAs, tests et limites. Aucun fichier GitHub modifié par le coordinateur.



## Phase 3 — CORTEX / ECHO : expérience synthétique exécutée
Graine NumPy 20261126, quatre scénarios de 250 000 observations à huit erreurs de prédiction (8 millions d'erreurs générées). Pour chaque observation, e_i = b + sqrt(rho) * Z_commun + sqrt(1-rho) * Z_i, avec Z indépendants normaux centrés réduits. Comparaison de moyennes équipondérées des 1, 2, 4 et 8 premières prédictions; aucune optimisation.

| Corrélation rho | Biais b | EQM 1 | EQM 2 | EQM 4 | EQM 8 | Réduction relative 1→8 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 1.000976 | 0.500145 | 0.249582 | 0.125486 | 87.46 % |
| 0.5 | 0 | 1.002152 | 0.752430 | 0.625527 | 0.563989 | 43.72 % |
| 0.9 | 0 | 1.000930 | 0.951754 | 0.926611 | 0.914363 | 8.65 % |
| 0.9 | 0.5 | 1.252992 | 1.202156 | 1.177640 | 1.165453 | 6.99 % |

Théorie : EQM = b² + rho + (1-rho)/k. Les erreurs communes et biais systématiques persistent; la diversité artificielle est définie par construction, non découverte sur des marchés. Aucune mesure de rendement, de drawdown, de coûts ni de bénéfice financier. Pas de sélection de configuration exploitable pour investir.

Reproduction (Python/NumPy, hors dépôt) :
```python
import numpy as np
rng = np.random.default_rng(20261126)
for rho, b in [(0,0),(.5,0),(.9,0),(.9,.5)]:
    z = rng.normal(size=(250000,1))
    u = rng.normal(size=(250000,8))
    e = b + np.sqrt(rho)*z + np.sqrt(1-rho)*u
    print(rho, b, [np.mean(e[:,:k].mean(axis=1)**2) for k in (1,2,4,8)])
```

### Cinq extensions conceptuelles à examiner (pas de nouvelles missions)
- 21 — LINEAGE : inventorier les sources et transformations communes des modèles; tester la détection de dépendances de provenance. C086, C087, C092.
- 22 — BLINDSPOT : mesurer les erreurs communes sur des scénarios artificiels rares, distinguer erreur partagée et erreur individuelle. C079, C084, C095.
- 23 — MIRROR : évaluer des contrôles négatifs et signaux artificiels sans information; vérifier que le laboratoire ne leur attribue pas une capacité prédictive. C093, C094, C095.
- 24 — COMPASS : distinguer incertitude estimée et erreur observée par diagnostics de calibration, avec données synthétiques dégradées. C082, C083, C089.
- 25 — PROVENANCE : journaliser identifiants des jeux de données, fenêtres et empreintes associés à chaque prédiction expérimentale. C081, C092, C093.

### Rapprochement avec main, vérifié le 25 novembre
Le registre main indique C086 EXPÉRIMENT_REJETÉ (sentinelle/scalp/apprentissage.py:41–53, 56–57, 92–98), C087 EXISTANTE_ET_TESTÉE mais avantage historique non démontré (sentinelle/scalp/brain.py:33–87 et sentinelle/ai/bench.py:139–185). C093 et C095 existantes et testées pour leur utilité technique. Ces nouvelles idées ne remplacent pas les essais précédents et ne justifient pas de rouvrir une hypothèse rejetée sans différence méthodologique documentée. SPEC.md SHA de contenu 4bdf99fa074ae1dabc59ba6add76fd5e93437ccb; lignes 350–353,395–396 : quatre agents actifs, paper-only, réserves GPT Work et autorisation distincte. Aucun code GitHub modifié. Version v2 = copie du document initial enrichie, non une mémoire automatique garantie. Aucune nouvelle observation prospective lancée.


## Phase 4 — QUARANTINE / PULSE : diagnostic de dérive synthétique exécuté
Graine NumPy 20261127 (identifiant de graine, pas date d'exécution). Cinq scénarios, chacun 2 000 séquences de 512 observations : 5 120 000 couples (entrée, erreur). Entrées et erreurs indépendantes N(0,1) avant rupture à l'indice 256. Après rupture : stable (inchangé), input_only (moyenne de l'entrée +1,5), hidden_error (écart-type des erreurs doublé), both (les deux modifications), hidden_bias (moyenne des erreurs +1,25). Les erreurs deviennent observables avec un retard artificiel de huit observations. Deux diagnostics illustratifs, définis avant exécution : moyenne mobile des entrées sur 32 observations, alerte si valeur absolue >0,65; moyenne mobile des erreurs au carré sur 32 observations, alerte si >2, calculée uniquement à partir des erreurs déjà révélées. Diagnostic combiné : union logique des deux alertes. Aucune optimisation des seuils après exécution.

| Scénario | Entrée, % | Erreur, % | Union, % | Union avant rupture, % | Délai médian conditionnel (observations) |
|---|---:|---:|---:|---:|---:|
| stable | 0.50 | 0.75 | 1.25 | 1.85 | 23.0 |
| input_only | 100.00 | 1.00 | 100.00 | 2.10 | 13.0 |
| hidden_error | 0.50 | 99.95 | 99.95 | 2.20 | 19.0 |
| both | 100.00 | 99.95 | 100.00 | 1.90 | 13.0 |
| hidden_bias | 0.45 | 98.65 | 98.65 | 2.70 | 28.0 |

Les trois premières colonnes mesurent la proportion de séquences avec au moins une alerte entre les indices 256 et 319 inclus; avant rupture : indices 128 à 255. Délai médian calculé uniquement parmi les séquences alertées dans la fenêtre postérieure (dans le scénario stable, il s'agit d'un délai de fausse alerte). Les fenêtres ont des durées différentes, donc leurs taux ne sont pas directement comparables. Une alerte de distribution n'implique pas une dégradation de la performance : le scénario input_only le démontre. Les diagnostics ne prédisent pas une rupture avant son arrivée. Les seuils sont arbitraires et le modèle nul normal/indépendant est optimiste; les ruptures sont fortes et instantanées. Pas de données de marché, ni mesure de rendement ou de drawdown, ni stratégie de placement. Les taux de détection ne sont pas extrapolables aux marchés. Reproduction : fichier omega_drift_reproduction.py; sortie brute omega_drift_results.json.

### Exigences d'architecture à étudier (pas de nouveaux agents ni missions)
- PULSE : séparer dérive de distribution, biais des erreurs et hausse de dispersion. Étiquettes et dates de disponibilité distinctes. Évaluer retards, fausses alertes par durée, ruptures faibles et progressives, dépendances et valeurs manquantes.
- QUARANTINE : état explicite de diagnostic inconnu / données périmées / erreur confirmée. Une alerte n'autorise ni changement automatique de modèle ni modification des vetos. Un diagnostic peut manquer une rupture ou être faux. Tester les incidents sans connexion financière.
- COMPASS : mesurer calibration sur observations dont les résultats sont effectivement connus, sans utiliser de résultats futurs.
- REPLAY : ajouter les cinq scénarios à la bibliothèque de contrôle hypothétique; conserver les scénarios négatifs et paramètres choisis. 
- Rattachement à C084 (dérive), C083 (abstention), C082 (calibration, expérience rejetée), C089 (anomalies), C079 (scénarios extrêmes), C095 (ablation). Rapprochement main vérifié : C084 `sentinelle/scalp/calibration.py:69–102` (PSI et walk-forward), C083 `calibration.py:116–119`, C089 `sentinelle/risk/shield.py:241–263` et `sentinelle/market/crosscheck.py:53–61`. Ces briques ne prouvent pas un avantage financier; l'apport distinct envisagé est le suivi des erreurs retardées et le test de ruptures invisibles aux entrées. Aucun changement GitHub, aucun test du dépôt exécuté, aucun lancement prospectif.

### Reproduction autonome
```python
"""Synthetic diagnostics only; no market data or trading rules."""
import numpy as np
import json
rng = np.random.default_rng(20261127)
n, T, change, window, delay = 2000, 512, 256, 32, 8
scenarios = [('stable',0,0,1),('input_only',1.5,0,1),('hidden_error',0,0,2),('both',1.5,0,2),('hidden_bias',0,1.25,1)]
def rolling(a):
    c = np.concatenate([np.zeros((len(a),1)), np.cumsum(a,axis=1)],axis=1)
    return (c[:,window:]-c[:,:-window])/window
results = {}
for name, dx, bias, scale in scenarios:
    x=rng.normal(size=(n,T)); e=rng.normal(size=(n,T))
    x[:,change:]+=dx
    e[:,change:]=e[:,change:]*scale+bias
    # Fixed illustrative thresholds, assumed baseline standardized; outcome arrives 8 observations later.
    xa=np.zeros((n,T),bool); ea=xa.copy()
    xa[:,window-1:]=np.abs(rolling(x))>.65
    ea[:,window-1+delay:]=(rolling(e**2)[:,:-delay]>2)
    result={}
    for label,a in [('input',xa),('residual',ea),('union',xa|ea)]:
        pre=a[:,128:change].any(axis=1)
        early=a[:,change:change+64]; detected=early.any(axis=1)
        d=np.argmax(early[detected],axis=1)
        result[label]={'pre_alert_pct':round(100*pre.mean(),2),'post64_alert_pct':round(100*detected.mean(),2),'median_delay':float(np.median(d)) if len(d) else None}
    results[name]=result
print(json.dumps({'seed':20261127,'streams_per_scenario':n,'observations':T,'change':change,'window':window,'delay':delay,'results':results},indent=2))
```
