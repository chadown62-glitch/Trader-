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

## SPRINT 5 : INTÉGRATION, INTERFACE & PIVOT LIQUIDITÉ (M58) `[EN COURS]` — stabilisation P0 uniquement
- **Objectif :** Tirer la leçon de M56 : sur 127 nouveaux pools contrôlés à leur détection, la liquidité médiane était de 1 601 $, un tiers n'avait aucune route de revente et aucun ne passait le veto. Le scanner memecoin vise désormais les **pools établis**, et la flotte d'agents (M57) rejoint la session fictive.
- **Périmètre :**
  1. **Pools établis :** candidats issus des pools Solana en tendance (GeckoTerminal), retenus seulement si la liquidité atteint au moins 20 000 $, si le pool a au moins une heure, si la dernière heure montre au moins 5 000 $ de volume avec au moins 10 achats et 10 ventes, et si un aller-retour Jupiter de 0,05 SOL, coté sans portefeuille, perd au plus 10 %. Le veto anti-arnaque (`tokenrisk.assess_candidate`) s'applique ensuite, toujours en dernier mot.
  2. **Veto opérant :** le RPC Solana public refuse la lecture des plus gros comptes (HTTP 403 ou 429). Le veto lit alors les détenteurs dans le rapport Rugcheck, hors pools et verrous connus (10 premiers ≤ 35 %, premier ≤ 15 %). Token-2022 reste refusé, sauf si ses seules extensions sont des métadonnées.
  3. **Flotte d'agents (M57) :** consultée avant chaque entrée fictive. Le veto, le risque et les alertes l'emportent toujours. La flotte n'a aucun pouvoir d'ordre.
  4. **Cockpit Memecoin :** entonnoir de filtrage (vus, refusés par motif, retenus), liquidité des candidats et décisions de la flotte.
- **Contraintes :** au plus 6 fichiers par PR ; PR #54 et #55 gelées ; simulation pure (`asset_domain="memecoin"`) ; aucun ordre réel, aucune clé, aucune promotion automatique.
- **Critère de validation :** `python -m pytest` passe sous Linux et Windows (CI). Dans le cockpit Memecoin, l'entonnoir des pools établis et les décisions de la flotte s'affichent.
- **Livré le 20 novembre 2026 :** PR #74 (pools établis et veto), #75 (flotte d'agents) et #76 (cockpit), fusionnées avec la CI verte sous Ubuntu, Windows et Chromium. Prérequis fusionnés le même jour : #68, #69 et #73.
- **Pipeline cloud continu :** le workflow `memecoin-paper-sim.yml` tourne toutes les 6 heures (`0 */6 * * *`) et à la demande (`workflow_dispatch`). Il exécute un cycle borné de la chaîne M58 complète avec `memecoin_cloud_runner` : pools établis, veto anti-arnaque, flotte d'agents, puis sorties calibrées (stop −20 %, 20 % vendus à 1,25×, paliers x3/x6/x10, stop suiveur adaptatif). La session SQLite dédiée est reprise d'une exécution à l'autre (`actions/cache`, puis artefact de secours). Le bilan est écrit dans le résumé de l'exécution (`$GITHUB_STEP_SUMMARY`), lisible depuis l'onglet Actions sur mobile :
  - capital, équité, P&L net, win rate, profit factor et drawdown maximal ;
  - compteurs du filtrage et des vetos, sur le passage et sur 24 h ;
  - décisions de la flotte et détail par stratégie.
  Les rapports JSON, texte et Markdown sont déposés en artefacts. L'analytique complète vient de `memecoin_analytics.build_analytics_report` ; tant que ce module n'est pas sur `main`, le bilan l'indique au lieu d'échouer. Simulation uniquement : aucun ordre réel, aucune clé.

- **Stabilisation P0 `[EN COURS]` (21 novembre 2026) :** la livraison du 20 novembre reste acquise. L'autorisation est limitée à cette correction technique, sans nouvelle mission, et ne couvre aucune autre évolution du sprint :
  1. **Exécution causale :** dans la session (`tick`), une décision prise sur une cotation ne s'exécute jamais sur cette même cotation. Elle devient un ordre fictif en attente, conservé dans la session, puis exécuté sur une cotation reçue plus tard, à un cycle ultérieur, dont l'empreinte diffère de celle du snapshot de décision et du snapshot précédent, et datant de moins de 2 minutes. L'heure de réception, l'empreinte et l'heure de décision sont conservées. DexScreener n'expose aucune heure de publication du prix et aucune n'est inventée. Une entrée sans cotation valide sous 10 minutes expire. Avant chaque entrée, le Guardian, la fiabilité de la valorisation, les plafonds, la réserve de liquidités, les frais et le glissement sont revérifiés. Une sortie s'exécute au prix de la cotation suivante : un stop à −20 % est un seuil de déclenchement, pas une perte maximale garantie. Une vente sans valeur réalisable ne crédite aucun montant. Un redémarrage ne duplique jamais une exécution.
  2. **Valorisation :** les marques sont revalidées après le traitement. Le statut de l'équité vaut `validated`, `stale` ou `unavailable`, et la dernière valeur connue reste séparée. Les erreurs de valorisation sont conservées. Aucune nouvelle entrée n'a lieu tant que la valorisation n'est pas fiable. Le rendement sur 24 h n'est publié qu'entre deux points validés. Identité vérifiée : trésorerie + valeur de liquidation des lots = équité fictive, chaque coût étant déduit une seule fois.
  3. **Guardian M58 :** un pool admis par M58 est revérifié avec le seuil M58 de 20 000 $, au lieu du défaut global de 25 000 $ conservé pour les autres usages. Les autres vetos sont inchangés.
- **Contraintes de la stabilisation :** une seule PR, ouverte en brouillon et fusionnée seulement après une revue humaine ; simulation exclusive ; PR #54 et #55 gelées ; aucune ancienne session SQLite réécrite ; aucun seuil de stratégie modifié pour améliorer un rendement. La cible expérimentale de +5 % net par jour n'influence aucune décision.
