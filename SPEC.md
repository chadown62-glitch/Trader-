# SPÉCIFICATION DIRECTRICE — V1 TRADER AI (OBJECTIF 15 DÉCEMBRE 2026)

## JALONS DE LIVRAISON
- **22 novembre 2026 :** Clôture de la première observation prospective `mom_ts_28` (PR #55 — figée).
- **15 décembre 2026 :** Livraison V1 intégrée et démontrable sous Windows 11.
- **20 décembre 2026 :** Bilan et réduction de périmètre si nécessaire.
- **14 janvier 2027 :** Date limite de stabilisation V1.

---

## SPRINT 1 : CONSOLIDATION & FUSION SUR `main` `[TERMINÉ]`
- **Objectif :** Rendre `main` exécutable et à jour avec les briques déjà développées, sans altérer PR #54 et PR #55.
- **Périmètre :**
  1. Audit des branches PR #50 (Cost Optimizer), PR #51-#52 (Direct Market Hub), PR #53 (Alpha Factory v2).
  2. Vérification de l'état de `docs/coordination/development-campaign.md`.
  3. Fusion ordonnée sur `main` et vérification de compatibilité Windows 11.
- **Critère de validation :** `pytest` passe sur `main` et le serveur FastAPI démarre localement sans erreur.

## SPRINT 2 : CAPITAL CONFIGURABLE & API `[EN COURS]`
- **Objectif :** Supprimer le plafond global codé en dur de 30 € fictifs sans réécrire les archives historiques.
- **Périmètre :**
  1. Paramétrage dynamique du capital de simulation (`30 €`, `100 €`, `1 000 €`, `10 000 €` ou montant libre).
  2. Respect des minimums d'ordre des plateformes, frais variables et reliquats selon le capital choisi.
  3. Exposition des portefeuilles et simulations via endpoints FastAPI.
- **Critère de validation :** Lancer deux simulations distinctes (ex: 30 € vs 1 000 €) via l'API et vérifier que les archives PR #54 restent intactes.

## SPRINT 3 : COCKPIT VUE 3 & ORCHESTRATEUR 4 AGENTS `[À VENIR]`
- **Objectif :** Connecter l'interface visuelle et activer une flotte d'agents légère compatible 8 Go VRAM.
- **Périmètre :**
  1. Connexion du frontend Vue 3 + Lightweight Charts aux endpoints FastAPI (courbes, positions fictives, métriques nettes après frais).
  2. Orchestrateur événementiel limité à 4 spécialistes à la demande : `Regime Detector`, `Alpha Hunter`, `Risk Commander` (moteur déterministe indépendant), `Cost Router`.
- **Critère de validation :** Visualisation en direct des signaux et des décisions tracées des 4 agents dans le navigateur sous Windows 11.

## SPRINT 4 : SCANNER MEMECOIN V0 & STABILISATION `[À VENIR]`
- **Objectif :** Poser la première brique Memecoin Solana en observation pure (zéro exécution complexe).
- **Périmètre :**
  1. Collecte de nouveaux tokens/pools Solana et filtre déterministe `Anti-Rug Pull Guardian` (permissions mint/freeze, liquidité, concentration détenteurs, droit de refus).
  2. Audit global de sécurité, reproductibilité et documentation d'installation Windows 11.
- **Critère de validation :** Le scanner détecte et filtre des tokens en mode observation avec journalisation des refus, sans aucun ordre réel.
