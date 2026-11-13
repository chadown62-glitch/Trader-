# Contrat Mission Control v1 et future fusion Claude

L'API GET authentifiée `/api/evidence` et son export `/api/evidence/export` produisent
`sentinelle.evidence/v1` selon `evidence-v1.schema.json`. Aucun import du laboratoire
Claude, aucun enseignant, ShadowCouncil, modèle, stratégie ou promotion modifié.

| Producteur actuel | Champs stables | Règle de consommation future |
|---|---|---|
| Mission Control | `generated_at`, `categories`, erreurs | Catégories jamais agrégées ; erreur = BLOCKED |
| Origine avant prédiction | account/bar, capture/protocol/model/settings SHA, réception/enregistrement | Une provenance créée après la prévision ne la qualifie pas rétroactivement |
| Décision exportée | available_at, recorded_at, candidate_id, modèle, refus, p_win | Horloges UTC en secondes, `bar_close_ms` en millisecondes |
| Issue théorique | `maturity`, `theoretical_outcome_r`, scores AUC/Brier | Seulement après exit_ts et resolved_at ; jamais un profit réalisé |
| Grand livre | EUR30, réalisé net, frais inclus, gap par actif, snapshot daté | Manuel et IA partagent le compte ; attribution au modèle non prouvée |
| Baselines | CASH/HOLD/TREND/V2 isolées, traces, SHA, coûts OHLC | Descriptif historique déjà exploré, promotion interdite |
| Claude, intégration ultérieure | `alpha_shadow_contract_v1` | Adaptateur explicite après revue des deux PR ; aucune compatibilité implicite |
| Blueprint #42, mission ultérieure | DecisionEvidenceV1 / OutcomeEnvelopeV1 / ObservationExportV1 | Contrats distincts à implémenter ultérieurement ; v1 ne prétend pas les remplacer |

## Matrice de fusion

- Conserver #35 comme base exclusive de #39, ne pas cherry-pick #40/#41.
- Revoir ultérieurement le mapping identifiants modèle/stratégie, unités et disponibilité
  de chaque champ avant tout raccordement.
- Mission Control ne remplit ni `ErrorDiagnosisV1` ni `HypothesisRegistry`.
- Champs non mesurés : `null` (coût, vitesse d'apprentissage, disponibilité continue,
  MTM actuel). Un zéro n'est jamais un substitut à l'absence de preuve.
- Les exports ne peuvent pas démarrer un agent, un entraînement, un ordre ou une promotion.
