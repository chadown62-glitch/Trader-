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

## SPRINT 2 : CAPITAL CONFIGURABLE & API `[TERMINÉ]`
- **Objectif :** Supprimer le plafond global codé en dur de 30 € fictifs sans réécrire les archives historiques.
- **Périmètre :**
  1. Paramétrage dynamique du capital de simulation (`30 €`, `100 €`, `1 000 €`, `10 000 €` ou montant libre).
  2. Respect des minimums d'ordre des plateformes, frais variables et reliquats selon le capital choisi.
  3. Exposition des portefeuilles et simulations via endpoints FastAPI.
- **Critère de validation :** Lancer deux simulations distinctes (ex: 30 € vs 1 000 €) via l'API et vérifier que les archives PR #54 restent intactes.

## SPRINT 3 : COCKPIT VUE 3 & ORCHESTRATEUR 4 AGENTS `[TERMINÉ]`
- **Objectif :** Connecter l'interface visuelle et activer une flotte d'agents légère compatible 8 Go VRAM.
- **Périmètre :**
  1. Connexion du frontend Vue 3 + Lightweight Charts aux endpoints FastAPI (courbes, positions fictives, métriques nettes après frais).
  2. Orchestrateur événementiel limité à 4 spécialistes à la demande : `Regime Detector`, `Alpha Hunter`, `Risk Commander` (moteur déterministe indépendant), `Cost Router`.
- **Exécution locale :** Les quatre étapes partagent le cycle de vie de `ia_entrainement` et fonctionnent sans Ollama. L’assistant DMH à la demande sérialise ses inférences et demande le déchargement après chaque réponse. Si plusieurs clients utilisent la même instance Ollama, configurer le processus serveur avec `OLLAMA_MAX_LOADED_MODELS=1` et `OLLAMA_NUM_PARALLEL=1`. Le pic de VRAM dépend du modèle et du contexte et se vérifie sur la machine cible.
- **Critère de validation :** Visualisation en direct des signaux et des décisions tracées des 4 agents dans le navigateur sous Windows 11.

## SPRINT 4 : SCANNER MEMECOIN SOLANA V0 & ANTI-RUG PULL `[TERMINÉ]`
- **Simulation V1 :** le Guardian filtre les pools. Sniper, Momentum, Dip Hunter et Whale Tail opèrent dans la seule session fictive memecoin.
- **Cockpit :** l’onglet Memecoin affiche le capital, les exécutions, les rendements nets et les statistiques des positions clôturées. Son worker démarre uniquement sur demande et reprend la session conservée dans SQLite. Une pause laisse terminer le cycle en cours.
- **Capital :** le journal est en USD. Un dépôt fictif initial en EUR utilise le cours acheteur public EUR/USD horodaté à l’ouverture. Les évaluations suivantes sont en USD. Aucun mouvement n’affecte les comptes majors.
- **Vérification Windows 11 :** sous Python 3.12, exécuter `python -m pytest`, puis `python -m sentinelle --demo --no-browser`. Dans Edge, ouvrir l’adresse locale indiquée, se connecter et sélectionner Memecoin. Vérifier le capital, Démarrer, le compteur de cycles, puis Pause et Cycle manuel après le délai de régulation. Les données publiques et les quotas conditionnent la disponibilité des observations.

## SPRINT 5 : INTÉGRATION, INTERFACE & PIVOT LIQUIDITÉ (M58) `[EN COURS]`
- **Objectif :** Tirer la leçon de M56 : sur 127 nouveaux pools contrôlés à leur détection, la liquidité médiane était de 1 601 $, un tiers n'avait aucune route de revente et aucun ne passait le veto. Le scanner memecoin vise désormais les **pools établis**, et la flotte d'agents (M57) rejoint la session fictive.
- **Périmètre :**
  1. **Pools établis :** candidats issus des pools Solana en tendance (GeckoTerminal), retenus seulement si la liquidité atteint au moins 20 000 $, si le pool a au moins une heure, si la dernière heure montre au moins 5 000 $ de volume avec au moins 10 achats et 10 ventes, et si un aller-retour Jupiter de 0,05 SOL, coté sans portefeuille, perd au plus 10 %. Le veto anti-arnaque (`tokenrisk.assess_candidate`) s'applique ensuite, toujours en dernier mot.
  2. **Veto opérant :** le RPC Solana public refuse la lecture des plus gros comptes (HTTP 403 ou 429). Le veto lit alors les détenteurs dans le rapport Rugcheck, hors pools et verrous connus (10 premiers ≤ 35 %, premier ≤ 15 %). Token-2022 reste refusé, sauf si ses seules extensions sont des métadonnées.
  3. **Flotte d'agents (M57) :** consultée avant chaque entrée fictive. Le veto, le risque et les alertes l'emportent toujours. La flotte n'a aucun pouvoir d'ordre.
  4. **Cockpit Memecoin :** entonnoir de filtrage (vus, refusés par motif, retenus), liquidité des candidats et décisions de la flotte.
- **Contraintes :** au plus 6 fichiers par PR ; PR #54 et #55 gelées ; simulation pure (`asset_domain="memecoin"`) ; aucun ordre réel, aucune clé, aucune promotion automatique.
- **Critère de validation :** `python -m pytest` passe sous Linux et Windows (CI). Dans le cockpit Memecoin, l'entonnoir des pools établis et les décisions de la flotte s'affichent.
