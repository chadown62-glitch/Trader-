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

## SPRINT 5 : INTÉGRATION, INTERFACE & PIVOT LIQUIDITÉ (M58) `[EN COURS]` — campagne moteur de 48 h actives (stabilisation P0 terminée)
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

- **Stabilisation P0 `[TERMINÉ]` (PR #91, fusionnée le 21 novembre 2026, commit `34dee21087c3632a03c164166fe27fbdb15a5068`) :** la livraison du 20 novembre reste acquise. Ce volet P0 conserve son périmètre technique. La campagne moteur complémentaire est définie ci-dessous et prend effet après fusion de son cadrage sur main :
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

- **Autorisation propriétaire du 21 novembre 2026.** Cette campagne prolonge le Sprint 5 existant après la fusion de #91 (commit `34dee21087c3632a03c164166fe27fbdb15a5068`). Son cadrage doit être fusionné sur main avant tout développement hors P0. Le développement du cockpit est suspendu pour cette campagne ; il reprend uniquement dans le chantier Interface web et application Windows V1 ci-dessous.
- **Claude Code, budget de 24 h actives.** Diversification et fiabilisation des stratégies, régimes de marché, réduction des faux signaux, mauvaises entrées et pertes, exécution causale, résilience réseau et préparation de cinq profils expérimentaux de risque.
- **GPT Work, budget de 24 h actives.** Méthode reproductible sur les 50 derniers jours historiques disponibles, séparation temporelle développement/évaluation, comparaison indépendante des cinq profils avec frais et glissement, drawdown, pertes et journées sans transaction, tests extrêmes et audit du moteur livré par Claude.
- Les deux budgets sont indépendants. Une publication de consignes ne vaut pas démarrage d'agent. Claude ne peut être annoncé actif qu'après ACK ou identifiant de session authentifié.

### Propriété des fichiers et interfaces

- **Périmètre conditionnel de Claude.** Les fichiers moteur que Claude peut être amené à modifier sont `sentinelle/dmh/memecoin_strategies.py`, `memecoin_simulator.py`, `memecoin_learner.py`, `memecoin_calibrator.py`, le scanner (`established_pools.py`, `smart_wallets.py`), le réseau (`net.py`, budget du client dans `service.py`), le worker de session (`memecoin_worker.py`) et leurs tests moteur (`tests/test_memecoin_*.py` hors évaluation, `tests/test_dmh_*.py` concernés). Cette liste borne le périmètre ; elle n'autorise pas à modifier tous ces fichiers. Chaque modification est justifiée par un défaut reproduit ou une exigence vérifiable de ce cadrage, dans une PR ciblée d'au plus six fichiers qui énumère les chemins exacts avant modification. Le Guardian (`tokenrisk.py`) et la flotte (`sentinelle/fleet/`) ne sont modifiés que pour corriger un défaut reproduit, jamais pour assouplir un veto ou une limite.
- Work possède les nouveaux outils et tests d'évaluation dédiés dans `tools/evaluation/`, `tests/test_memecoin_evaluation*.py` et les rapports nouveaux dans `docs/evaluation/`. Il audite le moteur en lecture seule et transmet les défauts reproduits à Claude.
- Les modules partagés, schémas SQLite et contrats existants sont réutilisés. Toute modification d'interface est décrite dans la PR moteur avant son utilisation par l'évaluateur. Aucun agent ne réécrit les fichiers appartenant à l'autre. Les modifications de SPEC restent dans la PR de cadrage.
- Le contrat d'expérience à figer avant l'évaluation comprend version de schéma, SHA moteur, profil et paramètres complets, capital/devise, jeu de données et SHA-256, origine des horodatages, observations, signaux, décisions, ordres différés, exécutions, coûts et statuts de valorisation. Les champs absents sont signalés explicitement.
- Chaque agent n'écrit que son propre journal de temps actif (`docs/evaluation/active-time/claude.jsonl` pour Claude, `docs/evaluation/active-time/work.jsonl` pour Work), par exception à la propriété du dossier `docs/evaluation/`.
- Les cinq profils portent des identifiants stables et des limites explicites (exposition, taille par token, réserve, pertes, concurrence, liquidité et coûts). Leurs valeurs chiffrées restent à préenregistrer conjointement avant essai. Aucun profil ne peut lever le Guardian, l'interdiction du levier/short ou la causalité de #91.

### Garanties obligatoires du moteur

Ces garanties s'appliquent à toute PR moteur de la campagne. Chacune est couverte par des tests déterministes, hors ligne, sous Linux et Windows.

1. **Causalité :** le contrat d'exécution de #91 est conservé. Une décision ne s'exécute jamais sur la cotation qui l'a produite : elle devient un ordre fictif en attente, exécuté sur une cotation ultérieure du pool exact, distincte et fraîche. Aucune heure de publication n'est inventée. Les entrées sans cotation valide expirent. Le Guardian et les limites de la flotte sont revérifiés à l'exécution. Un redémarrage ne duplique jamais une exécution.
2. **Valorisation :** trois états distincts, jamais confondus : valeur de liquidation confirmée (cotation du pool exact à ce cycle), valorisation indisponible, estimation prudente affichée à part. Aucune valorisation automatique définitive à 0 $. Aucune estimation, aucun prix et aucun rendement fictif ne sont présentés comme validés. Une position non cotable ne bloque jamais de façon irréversible la session : les entrées, les sorties et les journées évaluables restent possibles selon des règles écrites.
3. **Reprise après erreur :** une erreur pendant un cycle est consignée, ne laisse aucun état partiel et n'arrête jamais définitivement le worker. Les reprises sont bornées et temporisées. Un refus de sécurité reste bloquant.
4. **Budget réseau borné :** le budget de requêtes se renouvelle par fenêtre de temps, reste plafonné et respecte les limites de débit de chaque source. Une réponse 429 est respectée, jamais contournée. Les actifs suivis sont bornés, et ceux qui portent un ordre en attente sont cotés en priorité.
5. **Journal en ajout seul :** chaque cycle laisse une trace causale : cotations et provenance, signaux, verdicts du Guardian et de la flotte, décisions, résolutions d'ordres, exécutions, coûts, valorisation et erreurs. Cette trace n'est jamais modifiée ni supprimée. Elle suffit à reconstruire les résultats.
6. **Profils expérimentaux :** SAFE, MODERATE, RISK, ULTRA RISK et MAX sont des variantes indépendantes, avec une session et des paramètres propres. Aucun objectif de rendement n'est codé comme résultat attendu. Aucun profil ne lève le Guardian, l'interdiction du levier et de la vente à découvert, ni la causalité de #91.

### Paramètres expérimentaux fixés avant les résultats

- Les paramètres expérimentaux sont fixés, justifiés et versionnés dans la PR qui les introduit, avant tout examen des résultats. C'est notamment le cas de l'écart maximal entre les deux observations comparées par Momentum et des limites chiffrées des cinq profils.
- Aucun paramètre n'est réglé sur le jeu d'évaluation finale ni sur les 50 jours réservés à l'évaluation. Une modification ultérieure exige une nouvelle version, justifiée avant consultation de ce jeu.

### Décisions du propriétaire (21 novembre 2026)

- **Campagne prospective sur données réelles :** non couverte par ce cadrage. Elle exige une autorisation distincte, une base SQLite dédiée et une configuration figée.
- **Fusion (directive du propriétaire du 21 novembre 2026) :** l'approbation humaine individuelle n'est plus une condition de lancement ni de fusion. Une PR de la campagne est fusionnée dès que les contrôles CI obligatoires (pytest ubuntu-latest, pytest windows-latest, Vue cockpit Chromium) sont verts sur son dernier commit, sans contourner aucune protection technique de `main`. Les tests restent obligatoires et aucun test n'est déclaré réussi sans avoir été exécuté. Les étapes déjà autorisées s'enchaînent sans attendre de nouvelle instruction ; un blocage technique est signalé, puis le travail continue sur une tâche indépendante déjà autorisée.

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

## Extension du Sprint 5 — chantier Interface web et application Windows V1

- **Autorisation du propriétaire du 23 novembre 2026.** Le propriétaire demande explicitement ce chantier. Il s'inscrit dans le Sprint 5, seul sprint en cours, et lève, pour lui seul, la suspension du développement du cockpit. Aucune modification d'interface n'a lieu avant la fusion de ce cadrage sur main.
- **Articulation avec la campagne moteur.** La campagne moteur, ses garanties et ses budgets sont inchangés. Le temps de ce chantier n'est pas imputé aux 24 h actives de Claude sur le moteur. Il n'entre pas dans `docs/evaluation/active-time/claude.jsonl` ; il est consigné à part, avec le même schéma, dans `docs/interface/active-time.jsonl`.
- **Objectif.** Une application de qualité, intuitive, stable et complète. Un seul logiciel, deux présentations : le cockpit web et une application de bureau Windows 11 qui réutilise le même cockpit et la même API locale.

### Responsabilités et fichiers

- **Claude Code.** GPT Work est indisponible ; Claude Code mène le chantier dans ce périmètre :
  - le cockpit `web/` (HTML, JavaScript, CSS, bibliothèques déjà embarquées) ;
  - les routes de `sentinelle/api/` strictement nécessaires à l'interface ;
  - les tests d'interface : `tests/e2e/`, tests Pytest d'API et d'interface ;
  - l'emballage Windows (`scripts/`, lanceurs `.bat`, module de démarrage de bureau) ;
  - le job cockpit de la CI ;
  - la documentation de livraison dans `docs/interface/`.
- **Règles métier hors chantier.** Le moteur, le Guardian (`tokenrisk.py`), le Risk Commander, la flotte, les profils, Kelly et la causalité ne changent pas dans ce chantier ; l'interface affiche ce qu'ils décident. Un défaut moteur relevé suit les règles de la campagne moteur.
- **Fichiers de GPT Work et archives.** Les fichiers réservés à GPT Work (`tools/evaluation/`, `tests/test_memecoin_evaluation*.py`, rapports de `docs/evaluation/`) ne sont pas modifiés. Les archives #54/#55 restent gelées.

### Périmètre

1. **Version web.**
   - Base : le cockpit Vue 3 + FastAPI + Lightweight Charts existant, sans étape de compilation ajoutée ni chargement depuis un CDN.
   - Écrans : affichage adapté à l'ordinateur, la tablette et le téléphone ; mode clair et sombre.
   - États visibles : chargement, erreur, absence de données et déconnexion.
   - Montants : en euros et en pourcentage, selon la devise de la session.
   - Données fictives : toujours présentées comme simulées.
   - Règles métier : jamais dupliquées dans le navigateur.
2. **Application de bureau Windows 11.**
   - Emballage léger : fenêtre dédiée sur WebView2 au moyen d'une bibliothèque légère. Electron et les dépendances lourdes sont exclus, sauf justification écrite.
   - Serveur : lié à `127.0.0.1` sur un port local, démarré et arrêté proprement avec la fenêtre. L'application de bureau n'active jamais l'option `--remote`.
   - Installation : lancement depuis un raccourci du Bureau ; méthode de packaging reproductible documentée.
   - L'application n'est déclarée opérationnelle qu'après un test effectif sous Windows 11, consigné.
3. **Refonte UX/UI.**
   - Tableau de bord principal clair, navigation simplifiée, cockpit de trading fictif lisible.
   - Graphiques interactifs ; positions, gains et pertes.
   - Surveillance des stratégies et des agents.
   - Page Memecoin : scanner, décisions du Guardian, risque, historique.
   - Journal d'activité consultable et paramètres regroupés.
   - La fonction prime sur l'apparence.
4. **Audit des actions.** Chaque action interactive de chaque page est inventoriée dans `docs/interface/inventaire-actions.md`, selon huit rubriques :
   1. libellé et emplacement ;
   2. effet attendu ;
   3. gestionnaire JavaScript ;
   4. route FastAPI ;
   5. changement d'état attendu ;
   6. résultat constaté ;
   7. erreurs et cas limites ;
   8. test automatisé.

   Aucune action ne doit être décorative. Une fonction indisponible est signalée comme telle, jamais simulée.

### Critères de validation

- **Effet vérifié.** Chaque action visible a un comportement réel, vérifiable, sûr et compréhensible. Son test vérifie l'effet attendu sur le système : une réponse HTTP 200 ne suffit pas.
- **Matrice Playwright,** sur une base SQLite temporaire et jetable, couvrant :
  - Chromium sur ordinateur et l'affichage mobile ;
  - les états vides et les erreurs d'API ;
  - la session expirée ;
  - le WebSocket coupé puis rétabli ;
  - le double clic et les commandes concurrentes ;
  - les paramètres enregistrés puis rechargés ;
  - l'arrêt et le redémarrage ;
  - les données fictives jamais présentées comme réelles.
- **Intégration continue.** Les parcours critiques entrent dans la CI. Les contrôles requis (pytest ubuntu-latest, pytest windows-latest, Vue cockpit Chromium) restent obligatoires et verts sur chaque PR.
- **Windows.**
  - Automatisé : un test de démarrage et d'arrêt du serveur de l'application de bureau s'exécute sous windows-latest.
  - Manuel : la validation sous Windows 11 par le propriétaire est consignée avant toute annonce d'application opérationnelle.
- **Livrables.**
  - Interface web améliorée ; application Windows lançable.
  - Tests des parcours critiques.
  - Inventaire des actions et leur statut ; liste des fonctions réellement opérationnelles.
  - Rapport des défauts corrigés ; instructions de lancement et de diagnostic Windows.
  - Limites et tests encore nécessaires.

### Séquence et contraintes

- **Séquence de PR.** PR ciblées d'au plus six fichiers, qui énumèrent leurs chemins exacts, dans cet ordre :
  1. cadrage (cette section) ;
  2. inventaire et audit fonctionnel ;
  3. correction des défauts réellement reproduits ;
  4. navigation et design ;
  5. renforcement des tests Playwright ;
  6. emballage Windows et tests de démarrage ;
  7. validation globale et documentation de livraison.

  La séquence s'adapte aux défauts observés : aucun défaut n'est inventé, aucune PR n'est multipliée sans nécessité. Chaque PR est fusionnée selon la directive de fusion du 21 novembre 2026, après réussite des contrôles requis.
- **Sécurité.**
  - Simulation exclusive : aucun courtier réel, aucune clé privée, aucun ordre réel, aucun levier, aucun short, aucune promotion automatique.
  - L'API n'est jamais exposée publiquement.
  - Le Guardian, les limites du Risk Commander et la causalité des transactions fictives sont préservés.
  - Aucune campagne prospective n'est lancée ; la campagne de 72 heures a son propre démarrage autorisé.

## Extension du Sprint 5 — chantier Moteur court terme : veilleurs, IA principale et actualité

- **Autorisation du propriétaire du 23 novembre 2026.** Le propriétaire demande directement à Claude Code de se concentrer sur le moteur : l'IA, les agents, les bots. Il veut des trades courts gagnants et réguliers, très peu de pertes, un apprentissage sans données biaisées, l'actualité en temps réel, des tests et des résultats démontrés. Ce chantier s'inscrit dans le Sprint 5, seul sprint en cours. Il ne modifie ni la campagne moteur memecoin et ses budgets, ni le chantier Interface. Aucun développement n'a lieu avant la fusion de ce cadrage sur main.
- **Architecture voulue par le propriétaire.**
  - **Veilleurs.** Des agents légers et nombreux surveillent en même temps tous les marchés suivis : tendances, lignes de tendance, cassures, flux acheteur/vendeur, volume, actualité. Leurs calculs sont déterministes et peu coûteux. Ils ne décident rien : ils émettent une alerte datée avec ses raisons.
  - **IA principale.** Le modèle le plus lourd reste au repos tant qu'aucune alerte n'arrive. Sur alerte, il analyse plus profondément et décide si le trade est pris. Il s'abstient par défaut.
  - **Risk Commander et gestionnaire de risque.** Ils gardent le dernier mot, inchangés, et ne sont jamais assouplis.
  - **Profils de risque.** Le profil prudent est le seul actif au départ, pour beaucoup de petites victoires. Un profil plus risqué n'est activé que par le propriétaire, jamais automatiquement.

### Constat de départ (mesuré)

- **Résultats existants (`docs/RESULTATS.md`).**
  - §3 et §9 : en dessous de 4 heures, les stratégies perdent une fois les frais payés ; l'EMA 9/21 en 1 minute a été rejetée.
  - §10 : le modèle fait à peine mieux que le hasard sur les périodes récentes. Ses stratégies perdent sur 25 autres cryptos liquides.
- **Mesure exploratoire du 23 novembre 2026.**
  - Données : 12 mois de bougies d'une minute Binance, BTC, ETH et SOL contre USDC, de septembre 2025 à août 2026.
  - Méthode : entrées au hasard, frais de 0,1 % et glissement de 0,01 % par côté, soit 0,22 % par aller-retour. Elle sera refaite de façon reproductible dans la PR 2.
  - Un objectif de +0,3 % (stop −0,3 %) exige environ 87 % de trades gagnants pour être rentable.
  - Un objectif de +2 % (stop −1 %) exige environ 41 %. Le hasard en obtient 22 à 29 %.
  - Les réglages à fort taux de réussite gagnent 56 à 68 % des trades et perdent quand même de l'argent (objectif +1 %, stop −3 %).
  - Une hausse de +2 % dans l'heure n'arrive que dans 0,7 % (BTC) à 2,5 % (SOL) des heures.
- **Conséquence.** Le chantier vise le **trading court sélectif** :
  - des objectifs d'environ 1 à 3 % ;
  - une détention de quelques minutes à un jour ;
  - des trades rares, très choisis, mais pris sur un grand nombre de marchés surveillés.

  Le micro-scalping de quelques dixièmes de pour cent est exclu aux frais actuels. Il ne serait réexaminé que sur une grille de frais datée nettement plus basse.

### Objectif mesurable

- **Mesures.**
  - Critère principal : espérance nette positive par trade après frais et glissement, avec intervalle de confiance.
  - Drawdown borné.
  - Régularité : part des semaines positives.
  - Comparaison à CASH et à l'achat-conservation, avec le même capital et les mêmes coûts.
- **Taux de réussite.** Il est suivi, mais n'est jamais l'objectif seul.
- **Cibles de rendement.** Aucune n'est codée comme résultat attendu, ni 4 % par jour ni une autre. Aucune ne déclenche une opération, une hausse de risque ou une promotion.

### Périmètre (Claude Code)

1. **Coûts d'abord.**
   - Grille de frais datée : niveau, remise BNB, maker/taker.
   - Écart et glissement mesurés.
   - Seuil de rentabilité affiché pour chaque réglage.
2. **Données sans biais.**
   - Bougies d'une minute publiques de Binance (`data.binance.vision`), avec vérification des sommes de contrôle publiées, des trous et des horodatages.
   - Univers défini à la date, et non sur les seuls survivants.
   - Étiquetage par triple barrière identique aux règles de sortie fictives : entrée à l'ouverture suivante, et stop retenu si le stop et l'objectif sont touchés dans la même bougie.
   - Validation chronologique purgée avec embargo.
   - Registre de tous les essais, y compris négatifs, et correction pour essais multiples.
3. **Veilleurs.**
   - Un veilleur par marché suivi, au comptant et en position acheteuse seulement.
   - L'ensemble est borné par le budget réseau et les limites de débit des sources.
   - Les alertes et leurs raisons sont conservées en ajout seul.
   - L'élargissement du nombre de marchés est mesuré, jamais supposé utile : les cryptos restent très corrélées au BTC.
4. **IA principale plus intelligente, pas plus grosse.**
   - Caractéristiques porteuses d'information à court terme : flux acheteur/vendeur, surprise de volume, régime de volatilité, avance du BTC sur les autres cryptos, heure de séance, contexte d'actualité.
   - Modèles simples et calibrés.
   - **Abstention** : un trade n'est pris que si l'espérance calibrée dépasse le coût avec une marge fixée à l'avance.
5. **Actualité en temps réel.**
   - Archive horodatée en ajout seul : heure de publication et de réception, source, empreinte.
   - Annotation par l'IA locale existante, avec un seul modèle lourd chargé à la fois.
   - Études d'événements mesurées à l'heure de réception.
   - L'actualité sert d'abord de veto et d'alerte. Elle ne devient une caractéristique du modèle qu'une fois assez d'événements archivés.
   - Aucun article rétrodaté ni aucun jugement après coup n'est utilisé pour apprendre.
6. **Preuve.** Une évaluation historique unique sur la période réservée, puis une observation prospective fictive à configuration figée.

- **Hors périmètre initial.**
  - Les actions boursières : pas de source publique gratuite en temps réel intégrée, et des horaires de bourse. Elles pourront être ajoutées plus tard, en veille seule, sur décision du propriétaire.
  - Le levier, la vente à découvert et les dérivés comme instruments de trading.
- **Décision du propriétaire (nuit du 23 au 24 novembre 2026).** Le propriétaire demande un système d'agents qui surveille les cryptos, les memecoins et les actions, relié aux actualités avec le moins de délai possible, et qui répartit le capital entre ces marchés pour stabiliser les rendements. Le périmètre s'étend, en simulation exclusive :
  - **Actions et fonds indiciels.** Historique journalier public (cours ajustés des dividendes). Allocation par tendance entre grandes classes d'actifs (actions américaines et mondiales, obligations, or, matières premières, immobilier coté), aux règles publiées et fixées avant mesure. Le système ne s'appuie sur aucun courtier et ne transmet aucun ordre.
  - **Actualités à faible délai.** Un agent de veille interroge des sources publiques : annonces de cotation et de retrait de Binance, déclarations officielles des entreprises cotées (formulaires 8-K de la SEC), communiqués de la Réserve fédérale et fils de presse. Chaque dépêche est conservée avec son heure de publication et son heure de réception, et le délai entre les deux est mesuré. Les dépêches graves servent d'abord d'alerte et de veto ; leur effet sur les prix ne sera mesuré que sur l'archive ainsi constituée.
  - **Répartition entre poches.** La part de chaque poche (crypto, actions et fonds) est fixée avant mesure. Plusieurs parts sont publiées côte à côte, sans en retenir une après coup.
  - **Garde-fous inchangés.** Aucun ordre réel, aucun levier, aucune vente à découvert, aucun service permanent lancé sans le propriétaire. La date du 15 décembre 2026 évoquée par le propriétaire pour engager de l'argent réel ne modifie ni ces garde-fous ni les critères préenregistrés avant toute décision d'argent réel.

### Période réservée et critères préenregistrés

- **Évaluation historique unique.** Elle porte sur la période du 1er septembre 2026 00:00 UTC au 22 novembre 2026 23:59 UTC. Le développement et tous les réglages n'utilisent que des données antérieures au 1er septembre 2026. Le chargement de la période réservée est refusé par le code hors évaluation.
- **Limite de cette période.** Les bougies d'une minute d'octobre et novembre 2026 ont déjà servi à fixer les seuils du bouclier (§8) et à rejeter l'EMA 9/21 (§9). La période n'est donc pas totalement inédite pour le projet : la preuve prospective reste obligatoire.
- **Constat négatif.** Si aucune configuration ne montre d'avantage net sur le développement, la période réservée n'est pas consommée et le constat est publié.
- **Critères avant toute décision d'argent réel.** Ils sont proposés par Claude et modifiables par le seul propriétaire avant le début de l'observation :
  - au moins 8 semaines d'observation prospective fictive et au moins 150 trades clôturés, à configuration figée ;
  - espérance nette par trade positive avec 95 % de confiance (rééchantillonnage par blocs) ;
  - drawdown maximal d'au plus 10 % et au moins 60 % de semaines positives ;
  - résultat meilleur que CASH, et de même signe que l'évaluation historique unique.
- **Argent réel.** Sentinelle reste en simulation exclusive. La décision d'engager de l'argent réel appartient au seul propriétaire, hors de Sentinelle. Ces critères ne l'automatisent pas.
- **Indépendance.** GPT Work est indisponible ; Claude développe et évalue. Le préenregistrement, l'usage unique de la période réservée, le registre complet des essais et la preuve prospective compensent cette absence d'auditeur indépendant.

### Fichiers, séquence et contraintes

- **Fichiers.**
  - Nouveaux modules dans `sentinelle/scalp/`, tests dans `tests/test_scalp_*.py`.
  - Protocole et rapports dans `research/scalp-v1/`, documentation dans `docs/scalp/`.
  - Raccordement minimal à l'agent fictif, aux actualités et au gestionnaire de risque existants, décrit dans chaque PR.
- **Composants protégés.** Le Guardian, le Risk Commander, le bouclier et la causalité ne sont jamais assouplis. Les fichiers de GPT Work ne sont pas modifiés. Les archives #54 (`5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18`) et #55 (`18b31bb901f656d6126b370f47c510775f135c57`) restent intégralement gelées.
- **Séquence de PR.** PR ciblées d'au plus six fichiers, qui énumèrent leurs chemins exacts :
  1. cadrage (cette section) et protocole préenregistré ;
  2. laboratoire court terme : données d'une minute, coûts, triple barrière, mesure de référence reproductible ;
  3. veilleurs et caractéristiques : alertes datées sur les marchés suivis ;
  4. IA principale : modèle avec abstention, validation purgée, registre des essais ;
  5. actualité : archive horodatée, annotation, études d'événements ;
  6. raccordement à l'agent fictif, désactivé par défaut, avec le profil prudent ;
  7. évaluation unique sur la période réservée et rapport, y compris négatif ;
  8. préparation de l'observation prospective : configuration figée et tableau de bord.

  La séquence s'adapte aux résultats ; aucune PR n'est multipliée sans nécessité. Chaque PR est fusionnée selon la directive de fusion du 21 novembre 2026, après réussite des contrôles requis.
- **Temps.** Il est consigné dans `docs/scalp/active-time.jsonl`, avec le même schéma, hors budget des 24 h actives moteur.
- **Sécurité.**
  - Simulation exclusive : aucun ordre réel, aucune clé privée, aucun levier, aucun short, aucune promotion automatique.
  - Aucun service permanent. L'observation prospective ne démarre que sur ordre explicite du propriétaire, et la campagne de 72 heures garde son propre démarrage autorisé.
