# LOI DU DÉPÔT — SENTINELLE AI / TRADER AI

## 1. SÉCURITÉ ET GEL SCIENTIFIQUE (PRIORITÉ ABSOLUE)
- **PAPER TRADING UNIQUEMENT :** Aucun ordre réel, aucune clé privée, aucun levier, aucun short, aucun passage automatique au trading réel.
- **GEL DES BRANCHES SCIENTIFIQUES :** Les artefacts et protocoles de la **PR #54** (évaluation historique `mom_ts_28`) et de la **PR #55** (confirmation prospective, clôture dimanche 22 novembre 2026) sont STRICTEMENT INTOUCHABLES. Interdiction de modifier rétroactivement leurs scripts, données ou résultats.
- Aucune promotion automatique d'un modèle sans validation humaine.

## 2. ANTI-BOUCLE INFINIE (RÈGLES D'EXÉCUTION)
- Interdiction formelle de diviser le projet en nouvelles missions autonomes.
- Travaille UNIQUEMENT sur le Sprint marqué `[EN COURS]` dans `SPEC.md`.
- **Limite stricte :** Maximum 6 fichiers créés ou modifiés par Pull Request pour toutes les futures fonctionnalités. Dérogation unique : la PR d’assemblage initial #50–#52, branche `feat/sprint1-assemble-50-52`, peut dépasser cette limite. Cette exception ne s’applique à aucune autre PR.
- Une seule PR active à la fois. Une fois le critère de validation atteint, arrête-toi et attends la validation humaine.

## 3. CONTRAINTES MATÉRIELLES & ENVIRONNEMENT
- Cible d'exécution : **Windows 11**, Python 3.12, FastAPI, SQLite, Vue 3.
- Hardware : Intel i5-14400F, **RTX 4060 Ti (8 Go VRAM)**, 16 Go RAM.
- Architecture événementielle légère obligatoire : interdiction de charger simultanément plusieurs LLM lourds. Privilégier les modèles quantitatifs `scikit-learn` et le chargement à la demande.

## 4. DEFINITION OF DONE (CRITÈRE DE FIN DE PR)
- Aucune PR ne peut être déclarée prête si les tests `pytest` ciblés échouent.
- Chaque PR doit fournir dans sa description la commande exacte à exécuter sous Windows 11 pour vérifier visuellement ou en terminal le résultat produit.
