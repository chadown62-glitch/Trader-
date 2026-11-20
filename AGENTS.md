# LOI DU DÉPÔT — SENTINELLE AI / TRADER AI

## 1. SÉCURITÉ ET GEL SCIENTIFIQUE (PRIORITÉ ABSOLUE)
- **PAPER TRADING UNIQUEMENT :** Aucun ordre réel, aucune clé privée, aucun levier, aucun short, aucun passage automatique au trading réel.
- **GEL DES BRANCHES SCIENTIFIQUES :** Les artefacts et protocoles de la **PR #54** (évaluation historique `mom_ts_28`) et de la **PR #55** (confirmation prospective, clôture dimanche 22 novembre 2026) sont STRICTEMENT INTOUCHABLES. Interdiction de modifier rétroactivement leurs scripts, données ou résultats.
- Aucune promotion automatique d'un modèle sans validation humaine.

## 2. ANTI-BOUCLE INFINIE (RÈGLES D'EXÉCUTION)
- Interdiction formelle de diviser le projet en nouvelles missions autonomes.
- Travaille UNIQUEMENT sur le Sprint marqué `[EN COURS]` dans `SPEC.md`.
- Une PR peut modifier tous les fichiers nécessaires à la tâche autorisée. Documenter le périmètre et les tests.
- Enchaîner les tâches déjà autorisées sans arrêt intermédiaire. Le gel scientifique, le mode fictif et la validation humaine de toute promotion de modèle restent obligatoires.
- La CI teste les PR vers main et vérifie les deux manifestes SHA-256. La fusion squash automatique respecte les contrôles et revues exigés sur main. Activer Allow auto-merge et rendre obligatoires les jobs pytest (ubuntu-latest) et pytest (windows-latest) dans les paramètres GitHub.

## 3. CONTRAINTES MATÉRIELLES & ENVIRONNEMENT
- Cible d'exécution : **Windows 11**, Python 3.12, FastAPI, SQLite, Vue 3.
- Hardware : Intel i5-14400F, **RTX 4060 Ti (8 Go VRAM)**, 16 Go RAM.
- Architecture événementielle légère obligatoire : interdiction de charger simultanément plusieurs LLM lourds. Privilégier les modèles quantitatifs `scikit-learn` et le chargement à la demande.

## 4. DEFINITION OF DONE (CRITÈRE DE FIN DE PR)
- Aucune PR ne peut être déclarée prête si les tests `pytest` ciblés échouent.
- Chaque PR doit fournir dans sa description la commande exacte à exécuter sous Windows 11 pour vérifier visuellement ou en terminal le résultat produit.
