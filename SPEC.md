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

## SPRINT 5 : INTÉGRATION, INTERFACE & PIVOT LIQUIDITÉ (M58) `[EN COURS]` — stabilisation P0 et campagne moteur à cadrer
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

- **Stabilisation P0 `[EN COURS]` (21 novembre 2026) :** la livraison du 20 novembre reste acquise. Ce volet P0 conserve son périmètre technique. La campagne moteur complémentaire est définie ci-dessous et prend effet après fusion de son cadrage sur main :
  1. **Exécution causale :** dans la session (`tick`), une décision prise sur une cotation ne s'exécute jamais sur cette même cotation. Elle devient un ordre fictif en attente, conservé dans la session, puis exécuté sur une cotation reçue plus tard, à un cycle ultérieur, et datant de moins de 2 minutes. Dans cette cotation, l'état de marché du pool exact (prix, profondeur, volumes, transactions, capitalisation) doit différer de celui du snapshot de décision et du snapshot précédent. L'empreinte de la réponse DexScreener complète couvre aussi les autres paires du token : elle n'est conservée que comme provenance, car une variation ailleurs dans la réponse ne prouve pas une nouvelle cotation du pool. L'heure de réception, les deux empreintes et l'heure de décision sont conservées. DexScreener n'expose aucune heure de publication du prix et aucune n'est inventée. Une entrée sans cotation valide sous 10 minutes expire. Avant chaque entrée, le Guardian, les limites de risque de la flotte (impact, coût d'aller-retour, revendabilité, positions ouvertes, perte de session), la fiabilité de la valorisation, les plafonds, la réserve de liquidités, les frais et le glissement sont revérifiés sur la cotation d'exécution. Un refus de la flotte à ce moment annule l'ordre ; ce second avis est conservé dans l'exécution ou le journal des ordres, pas dans le journal des décisions de la flotte. Une sortie s'exécute au prix de la cotation suivante : un stop à −20 % est un seuil de déclenchement, pas une perte maximale garantie. Une vente sans valeur réalisable ne crédite aucun montant. Un redémarrage ne duplique jamais une exécution.
  2. **Valorisation :** les marques sont revalidées après le traitement. Le statut de l'équité vaut `validated`, `stale` ou `unavailable`, et la dernière valeur connue reste séparée. Les erreurs de valorisation sont conservées. Aucune nouvelle entrée n'a lieu tant que la valorisation n'est pas fiable. Le rendement sur une journée (`return_24h`) n'est mesuré qu'entre deux points validés séparés de 24 h à 24 h 15 min, et la durée réellement mesurée est indiquée. Une période plus longue n'est jamais présentée comme une journée. Sinon, le rendement est marqué non évaluable avec sa raison : fin non validée, historique de moins de 24 h, trou dans la courbe ou points non validés 24 h plus tôt. Les points non validés de la période sont comptés. Identité vérifiée : trésorerie + valeur de liquidation des lots = équité fictive, chaque coût étant déduit une seule fois.
  3. **Guardian M58 :** un pool admis par M58 est revérifié avec le seuil M58 de 20 000 $, au lieu du défaut global de 25 000 $ conservé pour les autres usages. Les autres vetos sont inchangés.
  4. **Limites connues :**
     - **Runner cloud :** sa cadence reste inchangée, avec un cycle toutes les 6 heures. Toute entrée qu'il décide expire avant le cycle suivant, faute de cotation valide sous 10 minutes. Il ne mesure donc plus la performance des nouvelles entrées. Il reste utile pour le filtrage, les vetos, les avis de la flotte et le suivi des lots déjà ouverts, dont les sorties s'exécutent jusqu'à 6 heures après leur déclenchement. Les « entrées fictives autorisées » de son bilan sont des avis de la flotte, pas des entrées exécutées. Pour mesurer les nouvelles entrées, il faut le cockpit local avec un intervalle de cycle nettement inférieur à 10 minutes (15 s par défaut).
     - **Risques résiduels de l'exécution différée :**
       - les signaux ne sont pas réévalués à l'exécution : une entrée peut s'exécuter, dans les 10 minutes, après la fin du signal et à un prix plus élevé qu'à la décision, sans limite d'écart de prix ;
       - la flotte juge le plafond d'exposition du token, plus grand que la mise réellement calculée : l'impact et le coût qu'elle estime sont prudents ;
       - l'aller-retour Jupiter jugé est le dernier mesuré par le scanner, qui ne revoit un pool qu'après 6 heures, et seulement s'il est encore en tendance : il peut dater de plusieurs heures ;
       - une sortie ne passe jamais par la flotte et s'exécute au prix suivant, quel qu'il soit ;
       - une entrée annulée n'est pas rejouée : seul un signal ultérieur peut en décider une nouvelle.
- **Contraintes de la stabilisation :** une seule PR, ouverte en brouillon et fusionnée seulement après une revue humaine ; simulation exclusive ; PR #54 et #55 gelées ; aucune ancienne session SQLite réécrite ; aucun seuil de stratégie modifié pour améliorer un rendement. La cible expérimentale de +5 % net par jour n'influence aucune décision.

## Extension du Sprint 5 — campagne moteur de 48 heures actives

- **Autorisation propriétaire du 21 novembre 2026.** Cette campagne prolonge le Sprint 5 existant après la fusion de #91 (commit `34dee21087c3632a03c164166fe27fbdb15a5068`). Son cadrage doit être fusionné sur main avant tout développement hors P0. Le développement du cockpit est suspendu.
- **Claude Code, budget de 24 h actives.** Diversification et fiabilisation des stratégies, régimes de marché, réduction des faux signaux, mauvaises entrées et pertes, exécution causale, résilience réseau et préparation de cinq profils expérimentaux de risque.
- **GPT Work, budget de 24 h actives.** Méthode reproductible sur les 50 derniers jours historiques disponibles, séparation temporelle développement/évaluation, comparaison indépendante des cinq profils avec frais et glissement, drawdown, pertes et journées sans transaction, tests extrêmes et audit du moteur livré par Claude.
- Les deux budgets sont indépendants. Une publication de consignes ne vaut pas démarrage d'agent. Claude ne peut être annoncé actif qu'après ACK ou identifiant de session authentifié.

### Propriété des fichiers et interfaces

- Claude possède les modifications moteur dans `sentinelle/dmh/memecoin_strategies.py`, `memecoin_simulator.py`, `memecoin_learner.py`, `memecoin_calibrator.py`, ainsi que les adaptations ciblées du scanner et du réseau et leurs tests moteur. Chaque PR énumère les chemins exacts avant modification.
- Work possède les nouveaux outils et tests d'évaluation dédiés dans `tools/evaluation/`, `tests/test_memecoin_evaluation*.py` et les rapports nouveaux dans `docs/evaluation/`. Il audite le moteur en lecture seule et transmet les défauts reproduits à Claude.
- Les modules partagés, schémas SQLite et contrats existants sont réutilisés. Toute modification d'interface est décrite dans la PR moteur avant son utilisation par l'évaluateur. Aucun agent ne réécrit les fichiers appartenant à l'autre. Les modifications de SPEC restent dans la PR de cadrage.
- Le contrat d'expérience à figer avant l'évaluation comprend version de schéma, SHA moteur, profil et paramètres complets, capital/devise, jeu de données et SHA-256, origine des horodatages, observations, signaux, décisions, ordres différés, exécutions, coûts et statuts de valorisation. Les champs absents sont signalés explicitement.
- Les cinq profils portent des identifiants stables et des limites explicites (exposition, taille par token, réserve, pertes, concurrence, liquidité et coûts). Leurs valeurs chiffrées restent à préenregistrer conjointement avant essai. Aucun profil ne peut lever le Guardian, l'interdiction du levier/short ou la causalité de #91.

### Protocole historique et audit indépendant

- Inventorier d'abord la couverture publique réellement disponible sur les 50 jours les plus récents terminés à une borne UTC figée. Conserver sources, dates de publication/réception, fuseau, trous, tokens disparus et biais de sélection. L'univers historique est défini au point-in-time, sans sélectionner seulement les survivants actuels.
- Proposition initiale à préenregistrer avant consultation des résultats d'évaluation, 30 jours de développement, 10 de calibration/sélection et 10 d'évaluation finale verrouillée. Purger les observations dont la cible ou la durée de détention traverse une frontière. Toute adaptation du découpage est justifiée avant ouverture du bloc final.
- Si la couverture ne permet pas 50 jours ou manque d'éléments critiques (liquidité, coûts, rug pulls, cotations), publier les lacunes et le périmètre réellement évaluable. Un scénario synthétique demeure étiqueté synthétique et ne remplace pas les observations manquantes.
- Geler les cinq profils et le budget fini d'essais avant le bloc final. Ne jamais réutiliser ce bloc pour régler les paramètres. Conserver tous les essais et résultats négatifs. Comparer à CASH et à une référence simple causale, fixée avant évaluation, avec capital, univers et coûts identiques.
- Mesurer rendement net cumulé/composé, distributions journalières, drawdown, pertes consécutives, volatilité, profit factor, trades, jours sans transaction, taux de journées négatives et atteignant les cibles expérimentales. Fournir effectifs, incertitude et sensibilité, en tenant compte de la dépendance temporelle.
- Tester cotations rejouées/périmées/manquantes, 429/timeouts, trous de marché, disparition de liquidité, rug pull/invendabilité, frais prioritaires, délais, gaps de prix, exécutions partielles et reprise après panne. Vérifier absence de fuite future, double exécution et double déduction de coûts. Une valorisation indisponible ne devient pas un rendement nul.
- Le runner cloud à un cycle toutes les 6 h reste soumis à la limite de #91 (expiration des nouvelles entrées après 10 min). Son bilan ne constitue pas une évaluation des nouvelles entrées. Les replays utilisent la cadence d'observation réellement disponible.
- Les cibles quotidiennes de 2 à 10 % et plus sont expérimentales. Elles ne déclenchent aucune opération, augmentation de risque ou promotion automatique.

### Comptabilisation et clôture

- Journal par agent, append-only, avec identifiant de session, début et fin UTC, tâche, preuves (commit, diff ou rapport de test), secondes actives créditées et périodes exclues. Emplacement proposé pour les futurs journaux, `docs/evaluation/active-time/<agent>.jsonl`.
- Les secondes créditées ne dépassent jamais l'intervalle observé. Retirer attentes CI, quotas, pauses et inactivité. Ne pas créditer les anciens travaux ni inférer le temps actif d'une date de commit. Les intervalles non attestés restent à zéro. La préparation de ce cadrage n'est pas créditée au développement moteur.
- État initial, Claude 0 h attestée sur 24 h et Work 0 h attestée sur 24 h. Un checkpoint incomplet ne vaut pas fin du budget.
- Bilan final seulement après 24 h actives prouvées pour chaque agent, avec changements, jeux de données, SHA, tests, limites, régressions et préparation de la stabilisation suivante.
- PR ciblées, au plus six fichiers selon les contraintes du Sprint 5, vérification des tests ciblés et intégrés sous Linux/Windows. Windows n'est attesté que sur une exécution Windows effective.
- Simulation exclusive `asset_domain="memecoin"`, aucun ordre réel, clé privée, levier, short ou promotion automatique. Aucune nouvelle mission hors campagne, aucun service permanent. Les archives #54 (`5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18`) et #55 (`18b31bb901f656d6126b370f47c510775f135c57`) restent intégralement gelées.
