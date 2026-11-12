# Reproductibilité : nouvelle référence exacte v1

L'ancien rapport `production.json` est conservé sans modification. Pour le
scénario tendance seed 41, 7 200 bougies et six actifs artificiels :

- Empreinte CSV publiée : `ff2472455230284939c6fca8009267a09698530a8f9eed472df36d448f5d9e97`.
- Empreinte CSV obtenue ici : `608583600a7cc497293b15f9c866f51c9274b916c2170d65a7471482204c6f9e`.
- La désactivation NumPy de AVX512F, AVX2 et FMA3 produit la même empreinte locale,
  et n'explique donc pas l'écart avec l'ancienne référence.

Les versions du fichier de dépendances ont été réinstallées. Les paramètres,
ordre des actifs/colonnes, dates et code de génération sont identiques. Le CSV
utilise toujours `%.17g`. Les AUC et portefeuilles publiés coïncident ; certains
Brier diffèrent jusqu'à environ 9e-11 et des calibrations diffèrent légèrement.
Ces observations sont compatibles avec un écart numérique, sans en établir
l'origine exacte. Les entrées exactes anciennes et le contexte numérique complet
n'avaient pas été archivés : une empreinte seule ne permet pas de les reconstruire.
**L'ancienne reproduction exacte reste non résolue.** Aucun arrondi n'est ajouté
pour fabriquer artificiellement une concordance.

La nouvelle référence conserve les **six séries intégrales**, en JSONL compressé
avec temps gzip nul et valeurs IEEE-754 `float.hex()` sans perte. L'ordre des
symboles et colonnes est fixé, la chronologie est conservée. L'empreinte porte
sur les entrées décompressées, indépendamment des formats CSV, fins de ligne et
représentations décimales. Une variation d'un ULP change l'empreinte. Les versions
Python, système, NumPy, pandas, scikit-learn, SciPy et CCXT figurent au manifeste.

Empreinte des entrées canoniques :
`988036659e3babe24c0027621daa2fd932e6605cb1b187ab42441d2868bfc3f9`.

Deux évaluations locales, l'une à partir des séries générées, l'autre à partir
des entrées archivées et relues, ont donné des objets de résultats **exactement
égaux**. Cela ne démontre pas une égalité entre systèmes différents.

Trois niveaux doivent rester distincts :

1. **Entrées exactes** : vérifier manifeste et SHA-256, puis recharger les valeurs
   archivées au lieu de régénérer un jeu supposé identique.
2. **Reproduction numérique** : comparer des champs numériques avec tolérances
   annoncées (ici comparaison descriptive Brier/probabilités à atol=1e-8,
   rtol=1e-8 ; ne pas utiliser ces tolérances pour décider une promotion).
3. **Résultats bit à bit** : égalité stricte des valeurs en environnement fixé ;
   les métadonnées d'horodatage et de durée de calcul restent distinctes.

Le bundle de recherche contient `synthetic-reference-v1/inputs.jsonl.gz`, son
manifeste et son résultat. Le laboratoire accepte `frozen_history` pour réévaluer
ces entrées. Le test de round-trip, de mutation d'un ULP et de corruption du
fichier compressé est exécuté hors réseau. Les anciens rapports ne sont jamais
remplacés par cette nouvelle référence.
