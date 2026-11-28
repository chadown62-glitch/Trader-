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

- **Direction du chantier.** À compter de la reprise du 25 novembre, GPT Work dirige également ce chantier. La répartition indicative du développement (75 % GPT Work et 25 % Claude Code) et la revue indépendante prévues par #181 s'appliquent aux périmètres suivants :
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

### Lot P1 du 25 novembre — interface, données et restitution scientifique

Le propriétaire autorise les vingt vérifications ci-dessous dans ce chantier existant. Le signalement P1-01 et les deux effectifs rapportés proviennent de son installation. La reproduction doit préciser la version, la période, les données et le capital. Le statut de chaque point évolue avec ses preuves (signalé, reproduit, amélioration, non évaluable ou corrigé).

| Réf. | Objet et condition d'acceptation |
|---|---|
| P1-01 | Simulation de 90 jours à 30 € fictifs. Vérifier les besoins de conversion avant le lancement. Si le carnet historique EUR/USDC requis manque, indiquer la date UTC et la couverture prouvée, sans taux inventé, interpolation ni utilisation du taux actuel. |
| P1-02 | Expliquer « Simulation indisponible » à partir du motif effectivement enregistré pour le rapport affiché. |
| P1-03 | Vérifier sur les horodatages, les labels arrivés à maturité et les fenêtres déjà utilisées quelles périodes peuvent être présentées comme jamais vues. |
| P1-04 | Afficher « indisponible » pour une performance inconnue, et zéro uniquement pour une valeur mesurée. |
| P1-05 | Afficher séparément la connexion du transport WebSocket et la fraîcheur des cotations, y compris après reconnexion. |
| P1-06 | Associer chaque réponse de rapport à la version demandée, y compris si les réponses se terminent dans l'ordre inverse. |
| P1-07 | Retrouver le bon résultat de backtest après rechargement, avec son identifiant, son statut et ses paramètres. |
| P1-08 | Présenter l'horodatage enregistré pour chaque décision. Signaler un horodatage manquant. |
| P1-09 | Présenter les noms et symboles Memecoin disponibles et conserver l'identifiant du token en cas d'homonymie ou de métadonnée manquante. |
| P1-10 | Calculer la durée de détention à partir des heures d'exécution disponibles, pour un lot ouvert ou clôturé. |
| P1-11 | Documenter le cours, la source et la date de toute conversion des gains ou pertes en euros. En cas de couverture manquante, conserver la devise native et signaler la limite. |
| P1-12 | Présenter séparément les gains et pertes réalisés et latents, avec leur devise. |
| P1-13 | Indiquer la fraîcheur des cotations utilisées pour valoriser les positions. Identifier les horloges disponibles (source, réception). |
| P1-14 | Distinguer les états d'un stop actif, d'un seuil atteint et d'une sortie effectivement exécutée. |
| P1-15 | Vérifier le rapprochement entre session Memecoin et portefeuille principal à partir des écritures. Si les sessions sont indépendantes, l'indiquer et éviter toute double comptabilisation. |
| P1-16 | Montrer couverture historique demandée, disponible et exploitable par actif et horizon avec les bornes UTC. |
| P1-17 | Expliquer les 1 205 situations résolues rapportées pour huit années demandées à partir des comptages conservés à chaque étape. Si le rapport source manque, chiffrer uniquement sur une reproduction identifiée. |
| P1-18 | Examiner le signal filtré parmi les 103 rapportés, préciser le dénominateur, le seuil, la version et les motifs disponibles. Préserver les seuils et les rejets scientifiques. |
| P1-19 | Distinguer l'éligibilité statistique, l'autorisation humaine liée à la version et le modèle actif en reprenant le contrat du correctif moteur P0. |
| P1-20 | Rendre consultables les données manquantes et les périodes exclues avec leur raison, par jeu de données et version de modèle. |

Les correctifs d'interface, d'API, de persistance des résultats et de vérification de couverture peuvent être préparés et testés localement après formalisation de ce lot. Leur intégration sur main suit la fusion du cadrage et reste limitée aux points identifiés ci-dessus. Toute correction dans le moteur de simulation ou d'apprentissage documente le défaut reproduit et ses tests avant modification. La réconciliation P1-15 est un audit comptable. Tout transfert entre portefeuilles ou changement de règle d'exécution demande une décision distincte.

La campagne Chromium couvre les quinze pages existantes sur ordinateur et mobile, les quatre capitaux de simulation (30, 100, 1 000 et 10 000 €), les actions, graphiques, erreurs d'API, données absentes et reconnexions. Une preuve précise le SHA, le scénario, le résultat attendu et celui observé sur données fictives isolées. Les nouveaux défauts reçoivent une gravité et un rattachement à ce lot. Chaque PR reste limitée à six fichiers, avec revue indépendante et les trois CI.

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
- **Décision du propriétaire (24 novembre 2026) : moteur unique.** Le propriétaire demande un seul moteur et un seul portefeuille pour les memecoins, les cryptos et les actions, les marchés étant choisis par lui. Il veut gagner en année haussière comme en année baissière, et vise environ 1,30 % par semaine. Il demande de ne plus multiplier les missions ni les PR. Le moteur est livré dans une seule PR, en simulation exclusive :
  - **Poches réutilisées sans réglage nouveau.** La poche crypto réunit les cryptos et memecoins choisis, aux règles figées de la position de fond (`frozen-system-v1`). La poche actions applique la règle G2 de Faber aux 8 fonds indiciels déjà publiés.
  - **Portefeuille unique.** Les parts de base sont fixées avant mesure (crypto 35 %, actions 65 %). Une poche sans position cède sa part aux poches investies, pour que le capital ne dorme pas. Les règles sont consignées dans `research/scalp-v1/trials.jsonl` avant tout calcul.
  - **Baisses.** La vente à découvert reste interdite (CLAUDE.md). En baisse, le moteur sort vers les obligations, l'or, les matières premières ou les liquidités. L'actualité sert de veto d'entrée.
  - **Objectif hebdomadaire.** Les 1,30 % par semaine ne sont ni un paramètre ni un critère : la part des semaines qui les atteignent est seulement publiée.
  - **Mesure unique.** Elle porte sur la période de 2018 à novembre 2026, avec chaque année publiée. La période réservée, déjà consommée par l'évaluation unique (#140), n'y figure qu'à titre d'historique, publiée à part.
  - **Garde-fous et argent réel.** Les critères avant argent réel restent ceux ci-dessus. La preuve prospective reste obligatoire.
  - **Résultat mesuré (`research/scalp-v1/moteur-unique-2018-2026.json`).** Période du 1er janvier 2018 au 22 novembre 2026, 464 semaines :
    - **Rendement :** +149 %, soit +10,8 % par an, pour une pire baisse de 21 %.
    - **Semaines :** +0,22 % en moyenne. 251 sont positives et 209 négatives. 109 atteignent +1,30 % ou plus.
    - **Années baissières :** −7,4 % en 2018 et −12,3 % en 2022, contre −73 % et −64 % pour le BTC.
    - **Références :** SPY +12,9 % par an (pire baisse 33,7 %), 60/40 +8,3 % par an (21,2 %).
    - **Transferts entre poches :** ils n'apportent rien. Les parts fixes font +151,6 %, avec une pire baisse de 17,8 %. Les 355 transferts coûtent environ 534 € pour 1 000 € de départ.
    - **Memecoins :** ils dégradent la poche crypto (pire baisse 40,7 % avec les cryptos, contre 27,1 % sans).
    - **Verdict :** les critères avant argent réel ne sont pas atteints.

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

## Extension du Sprint 5 — catalogue de capacités C001–C100 (décision du propriétaire du 24 novembre 2026)

- **Autorisation.** Le propriétaire valide l'étude et la réalisation progressive de 100 capacités candidates. Le dossier est transcrit dans `docs/scalp/catalogue-C001-C100.md`. Cette extension s'inscrit dans le Sprint 5, seul sprint en cours, et ne crée aucune mission autonome. Aucune implémentation qui dépend du catalogue ne commence avant la fusion de ce cadrage sur main.
- **Nature des capacités.** Ce sont des composants légers, pas des agents autonomes. Au plus quatre agents restent actifs, avec des modèles légers chargés à la demande et jamais plusieurs grands modèles de langage en même temps.
  - **Point ouvert pour l'audit indépendant** : la flotte M57 (`sentinelle/fleet/agents.py`) déclare douze agents consultés à la demande. Le coordinateur limite les modèles lourds (un seul), pas le nombre d'agents consultés. La lecture de la règle « quatre agents actifs » revient au propriétaire ; rien n'est modifié en attendant.
- **Garde-fous inchangés.**
  - Simulation exclusive : aucun ordre réel, aucune clé privée, aucun levier, aucune vente à découvert, aucune promotion automatique, aucun service permanent ni observation prospective sans autorisation spécifique.
  - Archives #54 (`5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18`) et #55 (`18b31bb901f656d6126b370f47c510775f135c57`) intouchables.
  - Guardian, Risk Commander, causalité, plafonds de risque et protections réseau jamais assouplis. Ils ne sont modifiés que pour corriger un défaut démontré.
  - Sources publiques uniquement, dans le respect de leurs quotas et conditions d'accès.
  - La période réservée, déjà consommée, n'est plus une période aveugle. Il ne reste aucune période historique aveugle.
- **Cible expérimentale.** +1,30 % par semaine en moyenne géométrique, net de frais, sur 52 semaines, avec une pire baisse visée de 10 %. Ce n'est jamais une fonction à optimiser sur les données d'évaluation, ni un critère qui augmente le risque, assouplit un veto ou choisit un réglage après consultation.
- **Registre de couverture versionné.** `research/scalp-v1/registre-capacites.json` est produit par `sentinelle/scalp/registre.py` et vérifié par `tests/test_scalp_registre.py`. Pour chaque capacité, il donne :
  - le statut vérifié : `EXISTANTE_ET_TESTÉE`, `PARTIELLE`, `ABSENTE`, `BLOQUÉE_DONNÉES`, `EXPÉRIMENT_REJETÉ` ou `EXPÉRIMENT_VALIDÉ_PROVISOIRE` ;
  - les preuves dans le code (fichier, symbole et lignes exactes au commit audité) ;
  - les tests cités, avec deux contrôles distincts (correction de l'audit indépendant de #148) :
    - le référencement : un test cité nomme le symbole ou son module. Il est nécessaire, pas suffisant ;
    - l'exécution effective (`python -m sentinelle.scalp.registre --execution`) : chaque fichier de test cité est rejoué sous traceur de lignes. Le niveau est « constatée » si le corps de chaque fonction citée a tourné pendant un test réussi. Un statut existant, partiel ou rejeté sans exécution constatée est marqué « couverture à confirmer » ;
  - les dépendances, l'hypothèse falsifiable, le contrat de données résumé et le verdict.
  
  Une capacité n'entre dans aucune décision fictive sans approbation humaine.
- **Inventaire au commit `e7be7af`.**
  - 32 capacités existantes et testées, 43 partielles, 17 absentes, 3 bloquées faute de données, 5 expériences rejetées et aucune validée.
  - Les 27 capacités de priorité haute se répartissent en 6 existantes et testées, 17 partielles, 3 absentes et 1 bloquée.
- **Baseline figé.** Il est inscrit dans le registre (clé `baseline`) : SHA de main, empreintes des modules (vérifiées contre leur contenu git à ce SHA, pas contre l'arbre courant, pour laisser le moteur évoluer), rendement hebdomadaire géométrique net, pire baisse, coûts, transactions, semaines positives, tests et provenance des résultats. Il comprend trois références :
  - B0, momentum simple : référence des capacités d'analyse technique ;
  - B1, vote 2 sur 3, volatilité visée, avec fonds : seul ratio de Sharpe dégonflé probable (#147) ;
  - B2, moteur unique U1 (#143).
  
  Les résultats négatifs de #140 et #146 et les conclusions de #147 sont conservés. Aucune date d'achèvement des 100 capacités n'est fixée.
- **Définition de fait d'une capacité.** Elle comprend :
  - statut vérifié, fichiers et lignes, hypothèse falsifiable, entrées, sorties et métriques ;
  - contrat de données : provenance, licence, horodatages, disponibilité effective, couverture, trous, identifiants, latence ;
  - tests déterministes : cas normal, absent, ancien, dupliqué, contradictoire, incident réseau, absence de fuite temporelle et invariants de sécurité ;
  - comparaison avec et sans la capacité, sur mêmes périodes, capital, coûts et univers, avec intervalles de confiance et correction des essais multiples ;
  - verdict : utilité technique, avantage historique, avantage prospectif, rejet ou non évaluable.
- **Ordre de construction.**
  - **Lot 0** : ce cadrage, le registre et le baseline.
  - **Lot 1 : fiabilité des mesures et exécution**, avec C091, C094, C095, C061, C062, C097 et C099. Il couvre l'univers défini à la date (LUNA corrigée en #145, MATIC → POL à relier), les essais multiples (#147), un outil d'ablation, les frais datés, les minima d'ordre, les stress de coûts et l'écart entre simulation et cotation accessible. Les conversions et les fonds européens restent dans le périmètre autorisé. Livrables : tests de propriétés et rapport d'invariants.
  - **Lot 2 : actualités, régimes et abstention**, avec C001, C003, C005, C006, C007, C021, C024, C026, C030, C082, C083 et C084.
    - `flash.py` est renforcé sans réécrire ses huit sources.
    - Les actualités positives restent en observation fantôme, jamais en signal d'achat automatique.
    - La comparaison « sans actualités » contre « actualités et vetos » attend une archive suffisante.
  - **Lot 3 : microstructure et maîtrise du risque**, avec C043, C046, C048, C067, C074 et C079. Les tests A/B se font hors ligne, sur des cotations datées, avec les données manquantes déclarées. Aucune donnée historique n'est inventée.
  - **Lots suivants** : selon les dépendances et la valeur marginale démontrée, sans multiplier les PR ni intégrer cent filtres dans le moteur.
- **Règle d'arrêt.** Un indicateur dont le gain net est non significatif, instable ou issu d'une période déjà optimisée n'est pas activé dans la décision. Une capacité de sécurité peut être conservée malgré un rendement inférieur. Une capacité rejetée peut être abandonnée comme filtre.
- **Répartition.**
  - La répartition OMEGA ci-dessous s'applique au développement et à la revue du Sprint 5. Les engagements et résultats historiques restent consignés à leur date.
  - Le propriétaire décide de toute observation prospective, de toute promotion et de toute extension des sources.
- **Rapport d'acceptation de chaque PR.**
  - Numéro, SHA, au plus six fichiers, et les trois contrôles CI verts sur le dernier commit (Ubuntu, Windows, Chromium).
  - Identifiants Cxxx, fichiers et lignes, état avant et après, tests exécutés, commande Windows 11. Windows n'est déclaré validé qu'après une exécution Windows effective.
  - Protocoles préenregistrés et résultats négatifs conservés.
  - Gel #54/#55 et invariants vérifiés : simulation exclusive, Guardian, Risk Commander, causalité.
  - Rendement, intervalles de confiance, pire baisse, coûts, actifs, périodes et seuils d'abstention, ou la mention NON MESURÉ.

## Extension du Sprint 5 — phase de recherche OMEGA (décision du propriétaire du 25 novembre 2026)

- **Autorisation.**
  - Le propriétaire autorise la phase de recherche OMEGA dans le Sprint 5, seul sprint en cours.
  - Elle ne crée ni sprint ni succession de missions autonomes.
  - **Direction technique.** GPT Work est l’ingénieur principal du programme moteur OMEGA du Sprint 5, sans échéance au retour de Claude. La répartition indicative du développement est de 75 % pour GPT Work et 25 % pour Claude Code. À son retour, Claude intervient en complément technique sur des lots aux interfaces et fichiers convenus. Chaque auteur fait examiner ses modifications par l’autre ingénieur ou un réviseur indépendant. Un succès CI ne vaut pas revue indépendante.
  - **Corrections autorisées après fusion de ce cadrage.** GPT Work traite d’abord la promotion de modèles (éligibilité statistique distincte d’un consentement humain explicite, traçable et lié à la version), puis les cotations (VERIFIED, INCONSISTENT, UNVERIFIED, STALE et conséquences sur les entrées). Viennent ensuite HIVE #172 (capital initial, drawdown, univers point-in-time et résultats futurs indisponibles), les horloges PULSE et FLASH, l’intégrité du cache des carnets et l’échec Windows de #177. Les recalculs ont un nouvel identifiant et conservent les résultats antérieurs. La consolidation respecte les bases réelles et l’ordre des dépendances. La réconciliation PHANTOM conserve les scripts et empreintes divergents avant la reprise du programme OMEGA existant.
  - **Preuves et continuité.** GPT Work conserve ses outils et rapports dans `tools/evaluation/`, `tests/test_memecoin_evaluation*.py` et `docs/evaluation/`. Les journaux scientifiques sont en ajout seul. Avant chaque interruption de quota, l’auteur consigne HEAD, diff, essais réellement exécutés, CI restante et prochaine opération. Les trois contrôles obligatoires et la limite de six fichiers par PR s’appliquent à chaque nouveau HEAD. C100 requiert une autorisation distincte.
- **Nature des 25 concepts OMEGA.**
  - HIVE, GENOME, RADAR, ATLAS, PHANTOM, ORACLE, NEMESIS, CORTEX, CHRONOS, MEMORY, PULSE, IMMUNITY, EVOLUTION, TITAN, TRUTH, BORDER, ECHO, QUARANTINE, TIMELOCK, REPLAY, LINEAGE, BLINDSPOT, MIRROR, COMPASS et PROVENANCE.
  - Ce sont des propositions de composants ou de méthodes expérimentales, pas des agents : au plus quatre agents restent actifs dans le moteur.
  - Chaque concept est rattaché aux capacités C001–C100 avant toute implémentation : `docs/scalp/omega-cadrage.md`.
  - Le registre SENTINELLE OMEGA V3 (25 novembre 2026), transmis par le propriétaire en cours de séance, est versé au dépôt en copie conforme : `docs/scalp/omega-registre-v3.md` (SHA-256 `2310472d4d986c52be6da5ec0a89d69cbe7f9c7f15ae33b278d71627538ac0e3`).
    - Il fait foi pour la fonction et le test proposé de chaque concept. La correspondance de `docs/scalp/omega-cadrage.md` en tient compte.
    - Il reste un registre de **propositions de recherche** : il ne crée aucune mission et ne valide aucune fonctionnalité.
    - Ses trois expériences synthétiques (HIVE/BORDER, CORTEX/ECHO, PULSE/QUARANTINE) sont à reproduire dans le dépôt, à partir de leur code et de leur description, comme bibliothèque de ruptures synthétiques (REPLAY).
    - Les contrôles synthétiques reconstruits avant sa réception (#172 à #174) restent publiés et signalés comme tels.
- **Cibles expérimentales**, jamais des fonctions à optimiser sur les données d'évaluation :
  - +1,30 % par semaine en moyenne géométrique, net de frais ;
  - pire baisse de 10 % au plus ;
  - au moins 60 % de semaines positives ;
  - un avantage prédictif reproductible ;
  - une compatibilité avec des capitaux fictifs de 30, 100, 1 000 et 10 000 €.
- **Échéances.**
  - Premier bilan : 1er décembre 2026.
  - Bilan scientifique consolidé : 5 décembre 2026.
  - Elles ne justifient ni hausse du risque ni baisse des exigences de validation. Le 5 décembre est un bilan scientifique, pas une autorisation d'engager de l'argent réel.
- **Programme et part de l'effort.**
  - **A — HIVE, GENOME, BORDER (35 %)** : apprentissage mutualisé entre actifs contre spécialisation individuelle.
    - Trois références : individuelle, collective et hybride.
    - Mesures : erreurs prédictives, généralisation dans le temps, généralisation à des actifs exclus de l'apprentissage, transfert négatif.
    - Le bénéfice de la mutualisation observé en synthétique n'est pas tenu pour démontré sur les marchés.
  - **B — CORTEX, ECHO (25 %)** : complémentarité effective des modèles.
    - Mesures : corrélation des erreurs, biais communs, dépendances informationnelles.
    - Recherche distincte de C086 (redondance des caractéristiques, #164) et de C087 (ensemble léger).
    - Toujours comparée à des références simples ; aucun modèle ajouté sans contribution mesurée.
  - **C — RADAR, ATLAS (15 %)** : élargissement de l'univers observé (cryptos, memecoins, actions, fonds).
    - Inventaire : couverture, historiques, qualité, dépendances, coûts et liquidité.
    - Univers défini à la date, actifs disparus inclus quand les données existent.
    - Observer plus d'actifs n'augmente jamais automatiquement l'exposition. Toute nouvelle source attend l'autorisation du propriétaire.
    - D'après le registre V3 :
      - RADAR mesure la couverture, la qualité des données et l'apport marginal ;
      - ATLAS mesure la stabilité des corrélations, les regroupements et la dépendance en crise.
  - **D — PULSE, QUARANTINE (15 %)** : dégradation des modèles, y compris à entrées d'apparence normale.
    - Distinguer la dérive des entrées, le biais des prédictions et la hausse des erreurs.
    - Synthétique d'abord, puis ruptures moins marquées, progressives et dépendantes.
    - Mesures : délai de détection, fausses alertes, situations non détectées.
    - Tous les vetos déterministes sont conservés.
    - QUARANTINE, d'après le registre V3 : signaler explicitement un actif ou un régime hors distribution.
      - États distincts : diagnostic inconnu, données périmées, erreur confirmée.
      - Une alerte n'autorise ni changement automatique de modèle ni modification des vetos.
  - **E — intégrité scientifique (10 %)** : principes transversaux.
    - TRUTH : essais multiples, biais, témoins et réutilisation de l'historique contrôlés ; résultats négatifs conservés.
    - TIMELOCK : dates de publication, de réception, de disponibilité et de correction des sources vérifiées ; préenregistrement poussé avant calcul.
    - MIRROR : contrôles négatifs et signaux artificiels sans information, auxquels le laboratoire ne doit attribuer aucune capacité prédictive.
    - MEMORY : mémoire des essais, des hypothèses rejetées, des versions et des causes d'échec, en ajout seul.
    - PROVENANCE : identifiants des jeux de données, fenêtres et empreintes associés à chaque prédiction expérimentale.
    - Les essais multiples sont corrigés, et les résultats sont reproductibles pour l'audit de GPT Work.
- **Méthode.**
  - Hypothèses falsifiables, préenregistrées dans `research/scalp-v1/trials.jsonl` et poussées avant tout calcul.
  - Références simples, modèles légers (`scikit-learn`, 16 Go de RAM, 8 Go de VRAM), données et calculs mutualisés, durée réelle mesurée.
  - Aucun historique déjà consulté présenté comme aveugle.
  - PR ciblées de six fichiers au plus, fusionnées après les trois contrôles obligatoires.
  - En cas de blocage de GitHub, les travaux locaux autorisés continuent, sans contourner aucune protection.
- **Mémoire et passation.** Le dossier `docs/scalp/dossier-evaluation-independante.md` et le registre des capacités sont tenus à jour :
  - hypothèses, résultats chiffrés, rejets et leurs raisons ;
  - fichiers, PR et SHA, tests et CI ;
  - temps actif, prochaines étapes et points soumis à l'audit indépendant.

  GPT Work doit pouvoir reprendre sans reconstruire le contexte.
- **Recherches autorisées après les premiers résultats** (décision du propriétaire du 25 novembre 2026, soir). Toutes sont menées exclusivement en simulation. Les informations réellement nouvelles passent avant la multiplication de modèles sur les mêmes données.
  - **C042, carnets d'ordres.** Étude des archives publiques de carnets d'ordres. Elle vérifie leur intégrité (sommes de contrôle publiées), leur couverture et leur disponibilité dans le temps. C'est une nouvelle source autorisée.
  - **Archives d'actualités.** Inventaire des sources publiques, puis étude historique hors ligne. Aucune collecte prospective n'est autorisée.
  - **Calibration de l'IA principale (C082).** Correction expérimentale, préenregistrée, jugée hors échantillon. Aucune intégration sans validation humaine.
  - **PULSE.** Étalonnage des seuils sur un protocole séparé, préenregistré, avec des scénarios indépendants de ceux de l'étalonnage.
  - **Consolidation des 12 agents en quatre rôles actifs.** Préparation architecturale seulement. Aucune protection n'est supprimée.
  - **A1** reste une candidate expérimentale.
  - **C100.** Le protocole d'observation prospective est préparé et figé. L'observation elle-même ne démarre pas sans une autorisation distincte.
  - Les fusions déjà préparées sont terminées. Tous les résultats négatifs sont conservés, et la compatibilité avec Windows 11 est maintenue.
- **Garde-fous inchangés.**
  - Simulation exclusive : aucun ordre réel, aucune clé privée, aucun levier, aucune vente à découvert, aucune promotion automatique.
  - Guardian, Risk Commander et protections scientifiques jamais assouplis.
  - #54 et #55 intégralement gelées.
  - Au plus quatre agents actifs.
  - Aucun service permanent ni nouvelle observation prospective sans autorisation distincte.

## Extension du Sprint 5 — programme multi-actifs et cerveau central V4 (proposition du 26 novembre 2026, revue par GPT Work, validée par le propriétaire le 27 novembre 2026 sous réserve de la revue indépendante, des corrections nécessaires et des trois CI vertes)

Cette section formalise le cadrage demandé par le propriétaire le 26 novembre 2026, **avant toute implémentation**. Elle intègre les compléments de GPT Work (commit `7f2686c`, ancienne #202, fermée).

- **Décision du propriétaire du 27 novembre 2026.**
  - Cadrage validé, sous réserve de la revue indépendante, des corrections nécessaires et des trois CI vertes.
  - Utilisation historique de SLV via Yahoo autorisée.
  - Recherche d'une nouvelle source publique pour XMR autorisée ; toute intégration reste soumise à sa validation.
  - GPT Work finalise le correctif du Risk Commander et les revues indépendantes. Claude consolide #200, #201 et #203 dans cet ordre, puis engage la recherche quantitative sur les historiques autorisés dès la fusion de ce cadrage.

- **Priorité.**
  - Les intégrations et corrections P0/P1 en cours passent en premier.
  - Ce cadrage autorise la préparation. L'implémentation opérationnelle attend trois conditions : l'intégration de ces corrections, la fusion de ce texte, et l'accord Work–Claude sur les interfaces et les fichiers de chaque lot.
  - Tant que ce texte n'est pas fusionné, seules les recherches OMEGA déjà autorisées se poursuivent.
  - Chaque PR compte au plus six fichiers. Elle exige une revue indépendante et les CI Ubuntu, Windows et Chromium vertes sur sa tête.
- **Objectif stratégique.**
  - Un registre de spécialistes logiques, un par instrument, légers, déterministes et chargés à la demande, sous un cerveau central unique.
  - Au plus quatre rôles centraux actifs : Regime Detector, Alpha Hunter, Risk Commander et Cost Router.
  - Cible initiale proposée de 100 instruments : 70 cryptos, 10 actions, 8 indices, 6 matières premières et 6 paires de devises.
  - Le registre peut dépasser 100 entrées sans augmenter le nombre de rôles centraux actifs.
  - Identifiant stable : classe, sous-jacent, place, produit et devise.
  - Un indice peut servir de référence. Simuler une position exige un instrument admissible distinct, long et au comptant, avec ses propres prix et coûts.
  - Objectif de recherche : au moins deux stratégies validées par instrument. Le compteur de validations peut rester à zéro.
  - Une stratégie n'est jamais tenue pour rentable sur la seule base d'un backtest favorable.
  - **Cible expérimentale du propriétaire** : +1,30 % net par semaine (environ +96 % par an composé), avec un drawdown maximal visé de 10 %.
    - C'est un repère de recherche, jamais un critère de validation ni une promesse.
    - Sous l'hypothèse optimiste de rendements hebdomadaires normaux, rester sous 10 % de drawdown avec au moins 70 % de chances sur trois ans suppose une volatilité d'environ 18 % par an, soit un Sharpe annuel d'environ 3,7.
    - C'est plus du double du plus fort avantage que l'historique journalier permet de détecter (MIRROR v5). Chaque bilan mesure l'écart à cette cible.
- **Instruments prioritaires et couverture, établie par granularité.**
  - Les bornes journalières proviennent d'ATLAS (`research/scalp-v1/omega-atlas-v1.json`, #175). Elles décrivent la couverture des séries.
  - Elles ne décrivent ni la couverture minute ni la profondeur exécutable (L2), qui sont indiquées à part.

  | Priorité | Série journalière (bornes ATLAS) | Minute | Profondeur L2 | Limite à lever |
  |---|---|---|---|---|
  | BTC, ETH | BTCUSDT et ETHUSDT au comptant, du 17/08/2017 au 22/11/2026 | archives Binance au comptant publiées ; couverture à établir | contrats perpétuels seulement, mesurés de 2023-01 à 2026-08 (C042, #179) ; aucune archive au comptant | profondeur et change à chaque instant simulé |
  | SOL | SOLUSDT, du 11/08/2020 au 22/11/2026 | idem | non mesurée | date d'admission |
  | XMR | XMRUSDT, du 15/03/2019 au 20/02/2024 (paire retirée) | idem, jusqu'au retrait | non mesurée | retrait ; recherche d'une nouvelle source publique autorisée le 27 novembre, intégration soumise à validation (voir ci-dessous) |
  | Or | GLD, proxy de l'or, du 18/11/2004 au 23/11/2026 | aucune | aucune | produit effectivement simulable et frais ; équivalent UCITS en euros non mesuré |
  | Argent | SLV (Yahoo, autorisée le 27 novembre), 5 117 séances du 28/04/2006 au 31/08/2026, plus long trou de 5 jours | aucune | aucune | produit effectivement simulable et frais |
  | Nasdaq-100, S&P 500 | QQQ du 03/01/2000 au 24/11/2026 ; SPY du 03/01/2000 au 23/11/2026 | aucune | aucune | ETF ou indice de référence, dividendes, écart de suivi |

- **Inventaire des sources** :
  - Avant toute intégration, inventorier aussi les actions, devises et autres candidats.
  - Pour chaque source et instrument, consigner :
    - autorisation et licence ;
    - calendrier, fuseau et devise ;
    - dates de cotation, de retrait et de réception ;
    - granularité, trous et ajustements ;
    - disponibilité du bid/ask, profondeur et frais.
  - Classer chaque usage comme admissible, référence seule ou non évaluable, avec son motif.
  - Toute nouvelle source est soumise au propriétaire avant collecte ou utilisation. Une donnée historique manquante reste inconnue.
  - **Proposition pour XMR après février 2024**, à valider avant toute collecte :
    - série de recherche : archives OHLCVT publiques de Kraken (XMR/USD), téléchargeables depuis l'ouverture du marché ;
    - recoupement : XMR-USD de Yahoo, source déjà utilisée, prix agrégé de référence ;
    - admissibilité : Kraken a arrêté XMR pour les clients de l'EEE le 31 octobre 2024, après Binance (février 2024) et OKX (janvier 2024). Pour un compte dans l'EEE, XMR après ces dates est donc classé « référence seule » : il peut servir à la recherche, pas à simuler une position.
- **Coordinateur et sécurité (cerveau central V4)**, propriété de GPT Work :
  - **Coordinateur unique.**
    - Étendre `sentinelle/fleet/coordinator.py`.
    - Un seul coordinateur sélectionne les avis pertinents.
    - Les calculs sont partagés par instrument, par instant de disponibilité et par version des entrées.
    - Les protections déterministes restent obligatoires.
  - **Mémoire.**
    - Mémoire globale : hypothèses, versions et désaccords.
    - Mémoire individuelle : observations et résultats par instrument, avec leur date réelle de disponibilité.
  - **Modes et supervision.**
    - Normal : exige les preuves requises.
    - Vigilance : signale un désaccord ou une anomalie.
    - Dégradé : restreint les analyses selon la couverture.
    - Protection : bloque toute nouvelle exposition touchée si une protection obligatoire échoue, manque ou est en quarantaine.
    - Les sorties prévues restent soumises aux règles existantes.
    - La reprise exige des contrôles sains et une transition tracée.
    - Aucun mode, avis majoritaire, budget CPU ou objectif de rendement ne peut autoriser de passer outre le veto ou le risque.
    - Le cockpit expose le mode, la fraîcheur, les alertes, les protections présentes ou indisponibles et le motif de chaque décision.
  - **Défaut reproduit à la frontière du coordinateur.**
    - Commit `1a76255`, lignes 74–76, 111–115, 128 et 189–194 ; candidat synthétique de `tests/test_fleet.py`.
    - Avec 12 positions ouvertes, la référence donne REFUS.
    - Avec une exception injectée dans `Risk Commander.run`, le coordinateur donne trois fois ATTENDRE puis PROPOSITION_SIMULÉE au quatrième appel. Le veto indépendant reste requis.
    - Cet essai porte sur la décision intermédiaire. Reproduit par GPT Work, puis confirmé indépendamment par Claude Code sur le même commit.
  - **Critère d'acceptation du coordinateur V4** (à ne pas confondre avec la règle V4 de #200) :
    - aux mêmes entrées, bloquer toute augmentation d'exposition pendant toute la quarantaine d'une protection obligatoire manquante ou en échec ;
    - tracer la protection absente ;
    - vérifier aussi les chemins appelants et la reprise.
- **Standard de validation**, fondé sur l'expérience préenregistrée OMEGA TRUTH/MIRROR (#200) :
  - **Ce que montre #200** (version 3, monde martingale).
    - Ces résultats sont conditionnels à son protocole synthétique : huit actifs GARCH simulés, 45 variantes, 2 600 jours, coûts fixes. Ils ne mesurent aucun marché réel.
    - Sans correction des essais multiples, un programme de deux stratégies par actif valide des stratégies fausses dans 92 à 99 % des mondes sans avantage.
    - Avec une correction de Holm sur tout le programme, ce risque tombe à environ 3 %.
    - Mais avec trois ans hors échantillon, un vrai Sharpe de 1,5 n'est retrouvé que trois fois sur dix, actif par actif.
    - Juger une même règle sur la moyenne de plusieurs actifs (jugement collectif) détecte plus souvent un avantage commun, sans perdre le contrôle des fausses validations : 18 % contre 9 % pour un Sharpe de 0,5. C'est un constat descriptif sur 100 simulations, sans test d'écart.
    - **Rectificatif du 28 novembre 2026** (revue indépendante de GPT Work) : quand l'avantage est propre à un seul actif, aucun des deux jugements ne le retrouve bien. Il y a 7/100 validations collectives, 9/100 validations individuelles quelque part, mais 6/100 seulement sur l'actif réellement porteur. L'infériorité du jugement collectif n'est donc pas démontrée.
    - **Correction d'interprétation du 27 novembre 2026** (signalée par le coordinateur) : une règle validée collectivement sur huit actifs ne constitue pas huit stratégies validées individuellement. La lecture « multiplie par huit le nombre de stratégies-actif validées » est retirée. Aucun résultat de MIRROR v1 à v3 n'est réécrit ; la distinction est mesurée par MIRROR v4, préenregistrée à part.
  - **Ce que montre MIRROR v4** (#203, avis scientifique favorable de GPT Work le 28 novembre ; synthétique, vérité connue, futur jamais lu) :
    - quand l'avantage ne concerne que les quatre cryptos, la moitié des stratégies-actif du comptage collectif ×8 n'ont aucun avantage réel ;
    - le jugement collectif suivi d'un jugement individuel n'en retient aucune fausse dans les six mondes, mais beaucoup moins de vraies ;
    - avec 34 tests corrigés ensemble et trois ans hors échantillon, un avantage de Sharpe 1 commun aux huit actifs ne donne en moyenne qu'environ une stratégie validée pour l'ensemble des huit instruments. L'objectif de deux par instrument exige donc des confirmations prospectives ;
    - la contribution marginale mesurée sur la fenêtre de validation ne filtre pas les faux.
  - **Ce que montre MIRROR v5** (#203, avis scientifique favorable de GPT Work le 28 novembre ; puissance du jugement individuel sur la longueur réelle de chaque historique, en journalier ; Holm sur 34 tests en borne prudente, 18 valeurs p fixées à 1 ; argent : durée de GLD par hypothèse, conservée même après l'autorisation de SLV) :
    - un Sharpe net de 1,5 n'est retrouvé que 6 à 14 % du temps sur BTC, ETH, SOL et XMR ;
    - sur l'or, l'argent, le Nasdaq-100 et le S&P 500, 50 % de puissance n'est atteint qu'à partir d'un Sharpe d'environ 1,2 à 1,3, et 80 % jamais en dessous de 1,5 ;
    - l'historique journalier seul ne permettra donc pas de valider deux stratégies par instrument : confirmations prospectives, granularité plus fine si ses coûts le permettent, ou spécialistes maintenus en observation.
  - **Avant calcul** :
    - préenregistrement poussé, avec la grille, les familles, le nombre total d'essais et la puissance attendue ;
    - périodes déjà consultées déclarées comme telles.
  - **Causalité** :
    - décision à la clôture, exécution fictive à l'ouverture suivante ;
    - aucun usage des plus hauts ou plus bas futurs pour choisir une entrée ou une sortie ;
    - apprentissage et jugement séparés chronologiquement, avec embargo.
  - **Coûts complets**, au comptant :
    - commissions datées (C061) ;
    - écart, glissement (C099) et profondeur consommée ;
    - minima d'ordre et exécutions partielles (C062, simulateur de GPT Work) ;
    - change horodaté en euros et latence d'exécution.
  - **Règle minimale** (V6 de #200) :
    - test hors échantillon à erreur type robuste ;
    - trois niveaux distincts, jamais confondus :
      - **validation collective** : la règle jugée sur la moyenne d'un groupe d'actifs. Elle oriente la recherche mais ne compte pour aucun instrument ;
      - **validation individuelle par instrument** : la même règle jugée sur cet instrument seul ;
      - **contribution marginale au portefeuille commun** : rejeu avec et sans la stratégie, depuis le même état, à risque comparable ;
    - seule une validation individuelle par instrument, assortie d'une contribution marginale positive, compte une stratégie pour l'objectif de deux par instrument ;
    - tous les tests présentés au même bilan (collectifs, individuels et par actif) corrigés ensemble par Holm ;
    - résultat toujours positif avec des coûts doublés ;
    - au moins deux tiers des sous-périodes hors échantillon positifs ;
    - plusieurs régimes représentés.
  - **Comparaisons obligatoires** : détention simple, liquidités, momentum simple et B0, à coûts identiques et sur les mêmes fenêtres causales.
- **Stratégies et portefeuille commun.**
  - **Entrée du registre.** Chaque entrée associe un instrument et une version de stratégie à :
    - ses données et leurs empreintes ;
    - ses règles d'entrée et de sortie, et la disponibilité de son signal ;
    - ses paramètres figés ;
    - ses coûts détaillés et son rendement net ;
    - son drawdown depuis le capital initial et son incertitude ;
    - ses essais antérieurs et son statut scientifique ;
    - son niveau de validation, distinguant validation collective (avec le groupe d'actifs jugé), validation individuelle sur l'instrument et contribution marginale.
  - **Statuts** : candidate, préenregistrée, évaluée, rejetée, preuve insuffisante, validée sur l'historique, confirmée prospectivement.
  - L'autorisation humaine éventuelle est enregistrée à part. Elle est liée à la version exacte et à son empreinte.
  - **Comparaison dans le moteur existant.** Mêmes fenêtres causales, au comptant, après tous les coûts ci-dessus.
  - **Portefeuille fictif commun.**
    - Le cash et la liquidité y sont réservés une seule fois.
    - Les rejoueurs utilisent des copies isolées de ce portefeuille.
    - Les anciennes sessions et archives conservent leur comptabilité.
  - **Mesures.**
    - Aux quatre capitaux fictifs (30, 100, 1 000 et 10 000 €).
    - Rendement géométrique net, drawdown, espérance par transaction, régularité des gains, profit factor et incertitude statistique.
    - Avec références simples, séparation temporelle et essais multiples corrigés.
    - Résultats négatifs conservés ; périodes exclues et résultats indéterminés rapportés.
  - **Contribution marginale, par ablation.**
    - Rejouer avec et sans la stratégie, depuis le même état (C095), sur une période qui n'a pas servi à la sélectionner (MIRROR v4).
    - Mesurer le rendement net, le risque et le drawdown, en tenant compte des signaux concurrents, des coûts et des corrélations.
    - Une stratégie ne compte que si elle améliore le portefeuille commun.
  - **Statut final.**
    - Une stratégie qui passe tout cela est « validée sur l'historique », non « rentable ».
    - Sa confirmation exige une observation prospective (C100), autorisée à part.
    - Aucune promotion n'est automatique.
- **Axes de recherche** (Claude Code, 25 % du développement, recherche quantitative) :
  - stratégies candidates propres à chaque actif ;
  - apprentissage mutualisé entre instruments (HIVE, GENOME, BORDER) : une même règle testée sur plusieurs actifs à la fois gagne en puissance, mais sa validation collective doit être suivie d'une validation par instrument ; détection des transferts négatifs entre actifs et des stratégies redondantes ;
  - spécialisation selon les régimes ;
  - optimisation causale des entrées ;
  - prises de bénéfices fixes et adaptatives ;
  - sorties progressives et protections des pertes ;
  - abstention quand l'avantage estimé est insuffisant ;
  - contribution marginale au portefeuille commun.
- **Répartition et interfaces** (confirmées par GPT Work le 26 novembre) :
  - **GPT Work, ingénieur principal (75 %)** : infrastructure, intégration, moteur et simulateur, coordinateur (`sentinelle/fleet/`), comptabilité et cockpit (`web/`, `sentinelle/api/`).
  - **Claude Code (25 %)** : hypothèses, règles d'entrée et de sortie, et validations, dans des modules de recherche qui lisent le moteur sans le modifier.
    - Fichiers : `sentinelle/scalp/multi_*.py` et `tests/test_scalp_multi_*.py`.
    - Résultats dans `research/multi-v1/`.
    - Registre des essais `research/multi-v1/trials.jsonl`, en ajout seul.
    - Toute modification d'une fonction partagée (coûts, simulateur, données) passe par GPT Work.
  - **Avant d'éditer le code**, convenir ensemble :
    - de la version des contrats, de leurs exemples fictifs et des tests d'acceptation ;
    - d'un propriétaire unique par fichier.

    Examiner d'abord les contrats de #47 (identité, observations datées, avis et registre) et les quatre rôles de #180.
  - **Interface d'échange** : une fiche JSON par stratégie dans `research/multi-v1/strategies/`.
    - Elle porte l'entrée du registre ci-dessus et le SHA du préenregistrement.
    - Claude fournit la fiche et un avis horodaté, sans pouvoir d'ordre.
    - Work rend le résultat du rejeu, la décomposition des coûts et les motifs de refus, liés aux mêmes identifiants.
    - Le moteur ne lit que les fiches confirmées **et** approuvées par le propriétaire.
  - **Revue** : chaque PR est examinée par l'autre ingénieur ; un succès de CI ne vaut pas revue. Les deux parties conservent la preuve de leur accord.
- **Étapes**, sans nouvelle mission autonome :
  1. Intégration des corrections P0/P1 en cours, puis fusion de ce cadrage (validé par le propriétaire le 27 novembre) après revue indépendante et trois CI vertes.
  2. Inventaire des sources, et données des instruments prioritaires complétées par les seules sources validées.
  3. Première vague préenregistrée : familles simples fixées d'avance par actif, avec un test collectif entre actifs, des tests individuels par instrument, la contribution marginale et la puissance déclarée (calibrée sur MIRROR v4).
  4. Mesure de la contribution marginale au portefeuille commun.
  5. Bilan de recherche le 1er décembre, bilan scientifique le 5 décembre.
- **Garde-fous inchangés.**
  - Simulation exclusive : aucun ordre réel, aucune clé privée, aucun levier ni vente à découvert, aucune promotion automatique.
  - Aucun nouveau service permanent ; C100 arrêté, non démarré.
  - Aucune nouvelle source sans validation.
  - #54 et #55 strictement gelées.
