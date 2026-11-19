# Mission 028 — Portage sélectif de Claude #26 sur #27

Mission : https://github.com/chadown62-glitch/Trader-/issues/28.
Base exacte : PR #27, d3704b1d06f0d162b6c576473e90e2a3c71936e1.
Contribution examinée : PR #26, 1880ef6084f32827787df52a6e265fcc52045466,
diff depuis #20, cca6354fe231872b26896970039b6160c7126cdf.
Aucune fusion de branche ancienne. Aucun changement de main.

## Matrice de réconciliation

| Contribution #26 | Décision | Implémentation / raison vérifiable |
|---|---|---|
| Fenêtres, purge, calibration / seuil dans trainer.py | Déjà fait, ancienne variante incompatible | #19 conserve quatre fenêtres distinctes, modèle figé avant calibration/sélection, purge des issues ; aucun diff dans trainer.py. #26 réutilise OOS pour calibration et sélection lorsque les données sont insuffisantes : non porté. |
| ai/metrics.py, ai/compare.py, frozen_vs_retrained | À écarter pour la promotion | Résultats de signaux en R, masques non-overlapping et bootstrap ne remplacent pas deux portefeuilles EUR avec risque, coûts, liquidité et capital communs. ai/evaluation.py et evaluation_windows de #19 restent en place. Pas de nouvelle recherche sur les historiques explorés. |
| SHA du modèle, paramètres, promoted_at, model_events | Porté et renforcé | ModelStore : écriture exclusive sans écrasement, SHA sur chaque lecture même en cache, désérialisation des octets effectivement vérifiés ; paramètres conservés, événements append-only. |
| Chargement des anciens modèles | Adapté | Migration additive, SHA null explicite pour ancien artefact ; aucune empreinte d'origine inventée. Lecture conservée si valide, nouvelle comparaison / promotion refusée sans SHA. Fichier corrompu refusé, jamais réécrit. |
| Premier modèle validé devient champion ; promotion manuelle sans comparaison | Incompatible | La preuve appariée indépendante de #19 reste exigée. Référence périmée refusée ; promotion répétée idempotente. Une importation n'est jamais une promotion. |
| review_models, retour automatique 90 jours, challenger remplacé après 60 jours | À écarter pour v2/v3 | Revue informative, aucune nouvelle tâche périodique. Aucun rollback/promote_challenger alternatif. Verrous persistants dès préparation d'un observateur v2 ou d'une comparaison v4, jusqu'à sa fin ; pas de contournement du registre. |
| Prédictions shadow champion / challenger | Porté autrement | Protocole distinct paired-forecasts-v4 préenregistré, versions / paramètres / SHA / réglages / période / critères épinglés. Tous les signaux communs, y compris refusés. Prédictions et plan dans la transaction d'intention durable de #24 avant l'ordre. Échec du recorder = aucun ordre. |
| live_comparison : outcome_r × take | Incompatible comme preuve financière | AUC, Brier, calibration sur toutes les prévisions communes résolues ; aucune attribution des fills/PnL du champion au challenger. financial_comparison reste null, verdict insuffisant. |
| Plan explicable agent/plan.py | Porté et corrigé | Signal, climat, entrée, stop, objectif, horizon, coûts en R et inconnues. Aucun partage arbitraire 75/25 des pertes ; aucune marge individuelle issue d'une formule IID sur labels dépendants ; aucun stop présenté comme prix garanti. |
| Journal, relecture du modèle actuel et « cause probable » | Porté sélectivement | Liaison explicite candidate_id, avec lecture des clés #24 et bar_ts #26 ; résultat du portefeuille, frais et sorties partielles séparés des labels théoriques. Aucune prédiction rétrospective présentée comme prévision préenregistrée ; pas de cause causale attribuée à une perte isolée. |
| PSI et outcome_mix sur toutes les données d'entraînement | Différé | Pas de nouvelles métadonnées apprises après consultation d'un holdout ou ajoutées aux artefacts v2/v3. Pas de réécriture des modèles gelés. |
| Journal des expériences | Porté sur l'entraînement actuel | Enregistrement avant le travail, paramètres / empreinte, résultat négatif, échec ou interruption conservés. Pas d'oubli après 180 jours. Les outils d'expériences scientifiques existants gardent leurs propres archives ; pas de second laboratoire. |
| API / page Apprentissage | Portées sur API actuelle | Trois GET authentifiés /api/ai/champion, /journal, /experiments ; vue lisible des modèles, règles, prévisions, plans, résultats comptables. Aucun bouton de nouveau balayage historique ou de rollback. L'onglet existant « Capacités d'apprentissage » reste disponible dans IA. |
| Risque identique en backtest | Déjà fait | #19/#23 normalisent déjà les budgets, capital, FX et coûts ; pas de remplacement d'engine.py par la version ancienne. |
| Migrations db.py | Adaptées | Tables ajoutées, colonnes models ajoutées ; pas de suppression/réinitialisation, ni copie de la base ancienne. Événements, prévisions, issues et protocoles append-only. |
| Docs et tests de #26 modifiant certaines seeds | Non repris tels quels | Tests ciblés adaptés à l'architecture moderne ; aucun changement de seed / skip pour obtenir un verdict positif. Le chiffre annoncé de −0,002 R et son IC appartiennent à l'ancien protocole : non reproduits ici, pas une preuve nouvelle de rentabilité. |

## Protocole parallèle et causalité

Les fichiers research/prospective-v2 et research/macro-shadow-v3, ainsi que tous les
modules news/shadow épinglés par v3, restent identiques à #27. Les poids, paramètres
et critères déjà figés n'ont pas changé.

Le protocole v4 est un mécanisme de préenregistrement, pas une nouvelle expérience
de marché déclarée réussie. Aucun vrai couple de modèles n'a été sélectionné ici,
aucune observation publique prospective démarrée. Les essais de cette mission
utilisent des fixtures identifiées comme synthétiques et des comptes isolés.

Chaque enregistrement v4 fige deux artefacts et leurs paramètres, les réglages, les
actifs et l'horizon, une période future, le code de comparaison, les métriques et
les critères. Les fenêtres v4 déjà enregistrées ne peuvent pas se chevaucher :
une tentative négative / interrompue consomme également la période. Les périodes
historiques consultées ne sont pas admissibles comme période prospective future.

Pour chaque signal commun : decision_ts, available_at et recorded_at explicites,
prix connu, coûts estimés, exposition/capital avant exécution, contexte et version
de chaque modèle. signal_pass est un filtre de signal, pas une autorisation de
risque ni un ordre exécuté. Le passage du risque et les fills du portefeuille
restent ceux de #24. L'état d'origine et le plan ne sont pas reconstruits après coup.

La prédiction est append-only dans la même transaction que le candidat préparé.
Cette transaction est commitée avant toute entrée. Le fill, la décision finale,
le journal et prospective_bars restent dans la deuxième transaction atomique.
Le test os._exit(77), après un vrai fill PaperBroker sur 30 EUR, prouve :
intention et prévisions conservées, fill annulé, reprise deux fois => un seul
fill / une seule position, même prévision et candidate_id lié.

Les issues des signaux sont ajoutées uniquement après leur maturité et la réception
de leur résolution. Les lectures as_of excluent les issues reçues / écrites plus
tard. Elles restent théoriques, sans profits du portefeuille. Une absence de
position liée reste indisponible et n'est pas remplacée par un outcome_r.

Les diagnostics sont appariés sur l'univers commun complet, pas sur les seuls
signaux retenus. AUC, Brier et calibration sont séparés des résultats financiers.
L'intervalle descriptif de différence de Brier ré-échantillonne les moyennes
journalières par blocs circulaires de 7 jours (2 000 tirages), seulement à partir
de 30 observations et 28 jours continus observés ; les jours manquants ne sont
pas imputés comme des observations nulles. Correction pour toutes les
préinscriptions v4 connues, sans effacement après 180 jours.
Même un intervalle favorable ne permet aucune promotion ni déclaration de PnL.

## Mode opératoire reproductible, borné et supervisé

Depuis la racine du dépôt, Python 3.12, dépendances du projet. Les commandes
suivantes ne démarrent ni téléchargement, ni boucle, ni serveur. Utiliser le
dossier d'une installation papier existante ; aucune création de portefeuille
par cet outil. Sous Windows remplacer python par le python du venv local.

1. Vérifier le gel : python -m tools.prospective check --root research/prospective-v2.
2. Importer les octets exacts du modèle v2 dans le registre :
   python -m tools.champion_shadow import-v2 --data DOSSIER.
   Une réécriture joblib après chargement n'est pas bit-identique dans cet
   environnement : l'import vérifie et copie les octets exacts. Un conflit de
   version existante est refusé, sans remplacement.
3. Disposer d'un challenger déjà enregistré, entraîné/calibré/sélectionné avant
   la période future ; ne pas le choisir par optimisation des résultats explorés.
   Les rôles champion/challenger du protocole désignent référence/candidat ;
   un modèle importé n'obtient pas pour autant le statut « champion approuvé ».
4. Préenregistrer un couple et une période future contenue dans v2 :

~~~text
python -m tools.champion_shadow register --data DOSSIER --champion VERSION_V2 --challenger VERSION_CANDIDATE --settings research/prospective-v2/settings.json --start DEBUT_FUTUR_ISO_AVEC_FUSEAU --end FIN_ISO_AVEC_FUSEAU
~~~

La sortie fournit le document exact et sa SHA. Aucun enregistrement réel v4
n'est livré avec des modèles arbitrairement choisis. Les essais automatisés
enregistrent uniquement des fixtures temporaires.

5. Lecture locale : python -m tools.champion_shadow check --data DOSSIER --protocol SHA.
   Résolution bornée des labels déjà mûrs : même commande avec resolve.
6. Pour la future session supervisée, construire l'observateur v2 existant puis
   tools.champion_shadow.ComparisonAttachment(observer, SHA). Appeler
   attachment.on_bar(symbole, features) sur une seule bougie publique fermée,
   fraîche et entièrement causale à la fois. Les contrôles v2 restent appliqués.
   Le callback supplémentaire journalise sans renvoyer de décision de trading.
   attachment.close() détache le recorder.
7. Aucune intégration au thread Engine ou tâche permanente n'est ajoutée.
   L'autorisation de démarrage reste à obtenir une fois les modèles et la période
   concrets revus. Ne pas démarrer de service 24/7 avec ce guide.

En cas de conflit modèle/réglages, données tardives, callback indisponible :
aucun nouvel ordre, erreur explicite. Une panne pendant l'import d'un artefact
peut laisser un fichier orphelin ; il est conservé et une réinscription sous la
même version est refusée. Une récupération de cet artefact doit être revue ;
aucun nettoyage automatique des fichiers utilisateur.

## Audit des bras macro v3

Les trois bras de #27 sont des annotations et abstentions hypothétiques
prices_only / prices_calendar / prices_calendar_news. Ils n'appellent aucun
broker, ne modifient pas le risque, n'attribuent aucun profit à une annulation.
La PR présente ne les transforme pas silencieusement en politiques actives.
Un replay financier complet causal avec carnets/coûts/risque propres à chaque
portefeuille alternatif reste à réaliser dans une mission distincte.
L'absence de carnet historique doit alors être signalée comme approximation.
Forex Factory reste désactivé sans droits confirmés ; aucune donnée FF
téléchargée ou redistribuée, aucun nouvel argument de contournement.

## Validation exécutée et limites

Référence #27 réexécutée : 409 réussis en 98,70 s. Version intégrée : 433 réussis
en 107,11 s, dont 24 nouveaux tests, aucun échec / erreur / skip / xfail,
sous CPython 3.12.14 Linux avec CCXT 4.4.99 complet, seccomp sans réseau.

Le bilan exact, l'environnement et les SHA des fichiers gelés sont dans
mesures/champion-integration/verification.json ; le rechargement des archives
dans archive-check.json. Les historiques EUR 4 h existants ont été relus hors
réseau et sans écriture, 7 662 bougies par actif, 2023-01-01 inclus →
2026-07-01 exclus, aucune lacune.

Tests d'API, de journaux, de reprise, de migrations et de comptabilité exécutés.
Vue et JavaScript compilés / analysés. Le script tests/e2e/champion_check.js
couvre Apprentissage et Performances sur 1440/390 px, sur une installation
démo jetable. Lancement réellement tenté : Chromium échoue avant création de
page, socket Unix interdite ; aucun rendu ni capture écran validé.
Le serveur démo temporaire a été arrêté après cette tentative.

Pas d'environnement Windows : aucun test Windows 11 exécuté. Conserver
tools/diagnose_windows.py et la procédure de docs/validation/marches-publics.md.
Aucune dépendance d'exécution ni exigence GPU ajoutée.

Ruff global retrouve deux imports inutilisés déjà présents dans #27 :
sentinelle/news/shadow/sources.py:7 et tests/test_public_archive.py:1.
Le premier fichier est épinglé par le protocole v3 ; aucune modification pour
masquer ce diagnostic. Ruff sur tous les fichiers Python de cette mission :
sans erreur. Un avertissement Starlette/TestClient est conservé.

Aucun nouvel entraînement sur marché authentique, aucune promotion, aucun
avantage net démontré par cette mission. Les rapports historiques restent
intacts. Ce lot livre l'intégration et ses garde-fous, pas l'ablation financière
complète ni une validation prospective statistique encore sans observations.
