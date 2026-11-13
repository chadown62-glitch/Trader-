# Expert Strategy Lab v0 — première brique de recherche exécutable

**Portée :** hors ligne, aucune transaction, aucune promotion de modèle, aucun gain historique revendiqué. Cette PR initie la [roadmap #36](https://github.com/chadown62-glitch/Trader-/issues/36) sans modifier la politique de trading, les protocoles prospectifs v2 / macro v3 ni le portefeuille fictif de 30 EUR.

## Pourquoi cette étape ?

Une vidéo annonçant un x5 ou un trader ne montrant que ses bons points d'entrée n'est **pas** une base d'entraînement fiable. Sélectionner uniquement les gagnants donne un modèle trompeusement performant (biais de survivance, fuite du futur). On doit observer des entrées, des refus, des pertes, des sorties et les conditions exactes connues au moment de chaque décision.

## Interface de recherche

Le fichier `sentinelle/ai/expert_lab.py` définit `ExpertDecision`, `admission`, `build_dataset`, `visible_snapshot` et `chronological_partitions`.

- **Provenance :** source, pièce justificative de l'expertise, preuve de permission de réutilisation et référence de chaque décision ; toutes les décisions sont datées.
- **Causalité :** le timestamp des caractéristiques doit précéder la décision, et la preuve de la décision doit avoir été enregistrée à temps (tolérance explicite de 120 s). Les labels et performances ne deviennent visibles qu'après leur date de disponibilité.
- **Comptabilité scientifique :** un bénéfice « réalisé » exige un fill documenté. Une action ignorée ou un contrefactuel n'est pas transformé en rendement réel.
- **Fenêtres indépendantes :** séparation chronologique entraînement / calibration / évaluation, embargo et purge des labels encore inconnus au bord de chaque fenêtre.
- **Refus traçables :** les échantillons rejetés et les raisons sont conservés ; aucune donnée n'est effacée de l'installation utilisateur.

**Limite importante :** les indicateurs `rights_verified` / `source_verified` et les URLs ne vérifient pas automatiquement les licences ou la vérité d'un historique ! Cette vérification doit être **indépendante, archivée et signée par l'opérateur** avant tout entraînement sur de vraies décisions. Les exemples utilisés par les tests sont artificiels.

## Tests effectués dans un environnement Python isolé

```sh
python -m pytest -q tests/test_expert_lab.py
```

Résultat local avant dépôt : **19 tests réussis** sous Python 3.13.5, pytest 9.0.2, avec copies du module et des tests. **La suite complète TRADER AI / Python 3.12 et Windows n'ont pas été exécutés dans cette session.** Ne pas présenter ce résultat comme une validation de la chaîne intégrée.

## Missions suivantes, séparées

1. Catalogue de sources **réellement autorisées**, archive immuable des preuves d'authenticité et des droits, vérification des fills et des horodatages.
2. Enseignants de référence explicites (trend following, pullback, mean reversion, risk management), apprentissage par imitation avec entrées/sorties horodatées ; inclure les erreurs et les abstentions.
3. **Courbes de vitesse d'apprentissage** : performance sur observations inédites à taille de données et budget CPU/GPU égaux ; comparer Brier / calibration **et** P&L net EUR30, frais, drawdown et abstentions.
4. Agents spécialistes en shadow, ablation de la contribution de chacun, puis validation prospective préenregistrée. **Jamais de modification de v2/v3 en cours, ni de promotion automatique sur des données déjà explorées.**

**Cette brique n'est pas un bot gagnant :** c'est une garantie élémentaire contre l'entraînement sur du faux « génie » reconstruit après coup.
