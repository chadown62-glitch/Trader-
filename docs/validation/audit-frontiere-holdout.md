# Audit reproductible de la frontière du holdout

`tests/test_holdout_boundary_audit.py` exécute `train_walk_forward` sur des
candidats synthétiques et observe les données effectivement reçues par ses
fonctions de calibration et de sélection. Aucun compte ni donnée financière
réelle n'est utilisé.

## Constat 1 — résultat non connu à la frontière

Un candidat dont `decision_ts < hold_start` mais `exit_ts >= hold_start` fait
partie de `dev`, car ce découpage utilise uniquement `decision_ts`. La fonction
`payoff_table(dev)` utilise son résultat pour calculer les gains/pertes attendus
par stratégie avant la sélection du seuil. Le test modifie uniquement l'issue de
ce candidat et observe une modification des gains moyens effectivement passés
par le pipeline. Cela démontre une fuite vers l'étape de sélection ; le test ne
mesure pas son impact sur le seuil choisi ou sur les performances historiques.

La purge `eval_train = dev[dev.exit_ts < hold_start]` existe plus tard, mais ne
retire pas rétroactivement ce candidat des calculs de calibration et du seuil.
Les éléments OOS issus de `dev` peuvent aussi avoir des issues postérieures à la
frontière. Une correction doit filtrer les données dont l'issue est connue
strictement avant la frontière à **toutes** les étapes de sélection ; le
comportement aux frontières égales doit être défini explicitement.

## Constat 2 — réutilisation des mêmes étiquettes OOS

Le second test observe que `fit_calibration` et `select_threshold` reçoivent les
mêmes étiquettes OOS, dans le même ordre. Cela peut rendre optimistes les
métriques rapportées sur ces mêmes observations. Ce seul constat ne démontre
pas que la période holdout finale, distincte, a été directement réutilisée pour
la calibration. En revanche, la fuite décrite au constat 1 doit être corrigée
avant de considérer le holdout comme pleinement indépendant.

## Pistes de correction et de validation

* Purger les candidats de sélection et de calibration dont l'issue n'est pas
  disponible avant la frontière du holdout, avec un embargo adapté à l'horizon.
* Séparer chronologiquement apprentissage, calibration, choix de seuil et
  évaluation, ou réaliser un cross-fitting temporel purgé pour l'ensemble du
  pipeline. Conserver une dernière période réellement vierge.
* Modifier artificiellement les issues postérieures à la frontière et vérifier
  que les modèles, les payoffs et le seuil précédents ne changent pas ; inclure
  les frontières égales, les chevauchements et les horloges manquantes.
* Enregistrer les fenêtres, graines, paramètres et versions ; mesurer les écarts
  sur plusieurs périodes hors échantillon et avec coûts réalistes. Ces tests ne
  démontrent ni apprentissage généralisé ni rentabilité future.

Aucune modification du moteur d'entraînement n'est proposée dans cette branche
d'audit ; elle est destinée à une correction et une revue distinctes.
