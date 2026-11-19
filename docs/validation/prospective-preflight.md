# Mission #30 — Pré-vol supervisé et replay financier séparé

Base : PR #29, commit 048c62a84dad5e036ad08dfda6f4d1c2e45258b4.
Mission : https://github.com/chadown62-glitch/Trader-/issues/30.
Les PR/branches ouvertes ont été examinées avant le travail. Aucune fusion dans main.

## Frontières et transactions

~~~mermaid
flowchart TD
  A["GET publics autorisés"] --> B["Réponses brutes, pages et SHA"]
  B --> C["Pré-vol en lecture : UTC, clôture, trous, prix, modèle, compte"]
  C --> M["T0 : gestion des positions et checkpoint de bougie"]
  M --> D["T1 : intention et prédiction durables"]
  D --> E["T2 : fill fictif, grand livre, décision, outbox"]
  E --> F["Après commit : diffusion avec event_id"]
  M --> F
  F --> G["Réception et déduplication par le consommateur"]
  E --> H["Annotation macro as_of dans le sidecar"]
  H --> I["Rapports et issues futures"]
  E --> I
~~~

- Lecture : modèle/réglages gelés, installation existante, captures immuables et
  données macro disponibles à la décision.
- Écriture : archive HTTP séparée, T1 puis T2 SQLite, annotation dans une seconde
  base après le commit, rapports datés sans écrasement.
- Notifications : événements durables dans T2, diffusion uniquement après commit.
  Les hooks internes de suivi des positions restent synchrones dans la transaction :
  ils servent à la comptabilité et aux protections, pas à l'envoi externe.
- Effets externes : GET publics bornés uniquement dans collect ; aucun ordre réseau.
  Aucun téléchargement dans run, le backtest ou les tests offline.
- Les réglages, poids et fichiers research/prospective-v2 et macro-shadow-v3, et le
  code news/shadow épinglé par v3, restent strictement identiques à #29.
  Le code services/champion.py épinglé par les inscriptions v4 reste également inchangé.
  Les nouveaux gates sont interdits hors backtest ; aucun bras macro n'est activé sur v2.

## Runner borné

tools/observe.py sépare collect et run. run est dry-run par défaut, 1 à 3 captures
maximum par invocation, budget coopératif de 1 à 180 secondes (défaut 120).
Pas de démon, planification, attente infinie ou thread Engine démarré.
Le budget limite chaque requête et est vérifié entre lectures de flux et pas ;
ce n'est pas un kill matériel d'un calcul CPU arbitraire.

Le pré-vol exige une installation existante, un compte principal papier unique
tenu en EUR, capital initial 30, des réglages exactement égaux à ceux gelés et
aucun agent autonome enregistré comme actif. Il ne crée ni ne réinitialise
le portefeuille. Les soldes actuels et l'historique sont conservés.

La capture porte sur BTC/EUR, ETH/EUR et SOL/EUR, 4 h, avec 1 500 bougies fermées
pour les caractéristiques (warmup_bars). Chaque page, réponse d'origine, manifest
et dérivé est vérifié à la réutilisation. Doublons, trous, OHLC incohérents,
période incomplète, catalogue incomplet et fichier altéré refusés. Les réponses
429 ou JSON invalides restent conservées séparément : elles ne deviennent pas
une page valide. Retry-After et backoff sont persistés, sans boucle de réessai.
Un crash pendant une page laisse réutilisables les pages réussies.

Seuls GET time, exchangeInfo, klines et depth sur data-api.binance.vision sont
autorisés par ce collecteur. Aucune configuration d'identifiants, endpoint privé,
redirection, actualité ou accès Forex Factory. Les réponses sont limitées à 4 Mo
en lecture de flux, délai de 20 s maximum par requête, réduit par le budget restant.

Prêt exige : fenêtre prospective admissible, clôture connue depuis au plus 180 s,
capture non future, UTC distante à moins de 5 s, latence HTTP au plus 5 s,
carnets reçus depuis au plus 30 s. La profondeur REST Binance ne donne pas ici
d'horodatage d'événement de marché : timestamp de réception explicitement déclaré,
source_event_time null. Ce contrôle ne prouve pas l'âge exact du carnet à l'exchange.
Il s'agit d'une limitation d'observation, pas d'une précision inventée.

Un rapport de dry-run/non-prêt indique les raisons, âges, origine et SHA, sans
écriture dans les tables de trading. Les erreurs normales de CLI produisent aussi
un rapport et un code de sortie 1. Une interruption brutale ne peut pas écrire
un bilan final ; l'état durable permet la reprise. Un fichier de rapport existant
n'est jamais remplacé : choisir un nouveau nom.

En exécution explicite, toutes les paires sont validées avant les ordres.
Capture/protocole sont réservés pour la clôture du compte ; un remplacement sous
la même clôture est refusé. Les intents/fills #24 conservent leur idempotence.
Le premier rapport complet est conservé et les autres bilans sont ajoutés par SHA.
Les décisions sont datées avec l'horloge de marché utilisée pour les prédictions
et les fills. Le modèle et les critères ne sont pas retouchés.

T0 rend également idempotente la gestion des positions déjà ouvertes : durée,
relèvement de stop, clôture et résolution des labels sont commités avec un
checkpoint par compte/agent/paire/bougie. Sans cela, un redémarrage sur la même
bougie pouvait incrémenter une seconde fois bars_held. Le test de reprise compare
maintenant aussi les positions et événements complets après deux relances.

## Outbox : garantie exacte et limite

event_outbox est additive. SQLite utilise synchronous=FULL. Les événements
position/decision/trade_closed sont insérés dans la transaction de leurs écritures
de trading. Les callbacks après commit respectent les savepoints : un rollback
interne ou externe supprime également les notifications correspondantes.

La diffusion est **au moins une fois**, avec event_id stable. Sans destinataire,
l'événement reste en attente. Une exception d'un listener laisse la ligne à
reprendre ; drain est borné. dispatched_at signifie « accepté par les listeners
locaux », pas « livré par Telegram » ni « lu par le téléphone ». WebSocket reste
un affichage transitoire, pouvant être reconstruit à partir des journaux.

Un crash entre un effet externe et son acquittement peut entraîner une rediffusion.
L'exactement-une-fois de l'effet exige que le consommateur déduplique event_id avec
une réception durable. Ce comportement est testé sur une inbox SQLite indépendante.
Aucune garantie exactement-une-fois d'une API externe non idempotente n'est inventée.
Aucun Telegram ou autre message réel n'a été envoyé.

## Tests de bout en bout

Fixtures entièrement synthétiques, datées au 15 novembre 2026, exécutées avec une
horloge de test, sans prétendre qu'une observation publique future a déjà eu lieu.

Le test passe par HTTP MockTransport (format public réel), pagination/reprise,
archive exacte, CaptureSource offline, caractéristiques réelles, SupervisedObserver,
PaperBroker L2, risque, portefeuille EUR30, prédiction/intention, journal, grand
livre, annotation as_of et rapport Performances. Un test utilise le véritable
artefact v2 inchangé. Un autre modèle synthétique documenté force la couverture
du chemin d'entrée ; il n'est jamais installé/promu dans une installation réelle.

Sous-processus os._exit(77) :
- pendant une pagination après des pages réussies ;
- après le fill, avant commit de T2 : fill annulé, prédiction/plan conservés ;
- après commit, avant diffusion ;
- après une réception externe durable, avant acquittement outbox ;
- après annotation du sidecar, avant bilan du pas.

Après deux reprises : pas de double ordre/fill, même état de prévisions, mêmes
annotations, un seul effet dans l'inbox dédupliquée. Tests additionnels de 429,
backoff, raw corrompu, JSON invalide conservé, version de capture/protocole
remplacée, contexte requis absent, modèle gelé, refus temporels et installation
absente. Aucune base utilisateur utilisée.

## Replay macro-replay-v1 distinct

tools/validation/macro_replay.py réutilise run_backtest, TradingAgent, RiskManager,
PaperBroker, PositionManager et le contrat d'exécution existants. Trois bases
indépendantes commencent chacune à 30 EUR, avec les mêmes coûts, minima, risque,
modèle et prix. L'annulation d'une entrée modifie naturellement la liquidité,
l'exposition, les positions et les décisions ultérieures du portefeuille alternatif.

Les gates utilisent exclusivement ShadowStore.context(as_of) :
- prices_only : aucune abstention supplémentaire du nouveau shadow ;
- prices_calendar : abstention calendar_would_abstain ;
- prices_calendar_news : abstention calendrier ou news_would_abstain.

Les protections fixes préexistantes du risque sont communes aux trois bras.
L'entrée reste au premier événement de la bougie suivante. Un carnet n'est
sélectionné que si timestamp <= décision ; chaque événement L2 est ensuite rejoué
dans l'ordre, avec consommation de profondeur propre à chaque portefeuille.
Reste possible un mode OHLC explicitement marqué approximation sans carnet.
Aucun faux chemin L2 reconstitué à partir des extrêmes d'une bougie.

Le protocole est écrit avant l'expérience et épingle données, contexte/version,
modèle, réglages, minima de marché, carnets et code. Les substitutions sont
refusées. Les nouveaux essais sont limités aux fixtures synthétiques : l'activation
sur des archives publiques macro complètes reste à revoir. Les marchés non-EUR
sont refusés ; aucune équivalence de stablecoin ni conversion historique inventée.

Les six tests du replay couvrent comptabilité après frais, sorties partielles,
stop partiel puis complet, portefeuilles sans ordre, indices de calendrier
manquants, rumeur reçue avant/après décision, invariance face à un fait futur,
données/minima modifiés et interdiction d'activer les gates en paper prospectif.
Une publication ancienne reçue après la décision ne peut supprimer son entrée.

Une expérience d'ingénierie synthétique archivée donne :
prix seuls / prix+calendrier : 28,11731484 EUR après un trade perdant et 4 fills,
frais 0,01615118 EUR ; prix+calendrier+actualités : 30 EUR, aucun ordre.
Le calendrier du fixture est de faible impact, une rumeur fictive précède
l'entrée du troisième bras. Ces valeurs vérifient le replay et la comptabilité,
**pas l'utilité des actualités sur marché authentique**. L'échantillon est trop
petit ; verdict non promouvable, aucune validation financière indépendante.

Les entrées exactes, carnets, observations fictives, protocole, SHA et fills sont
dans mesures/prospective-preflight/replay-*.json. Aucune actualité/calendrier FF
authentique n'est redistribué. Les sondes GET réelles sont dans public-probes.json,
hors expérience, sans ordre ni intégration à une fenêtre prospective revendiquée.

## Procédure manuelle, avant tout lancement autorisé

D'abord revue de la PR, validation locale Windows, confirmation du dossier réel,
du modèle, des réglages et des dates admissibles. Ne pas décaler les fenêtres
gelées si leur démarrage est manqué. Ne pas sélectionner de Challenger à partir
des résultats historiques déjà explorés ; le runner ne choisit aucun Challenger.

Commandes depuis la racine, dans un terminal supervisé :

~~~text
python -m tools.observe collect --capture NOUVEAU_DOSSIER_CAPTURE --report NOUVEAU_BILAN_COLLECTE.json
python -m tools.observe run --data INSTALLATION_EXISTANTE --capture DOSSIER_CAPTURE --report NOUVEAU_PREVOL.json
~~~

Ces deux commandes n'exécutent aucun ordre. La première télécharge, la seconde
vérifie hors réseau en dry-run. La fenêtre v2 et la fraîcheur doivent être valides.
Au 12 novembre, l'exécution prospective v2 n'est pas encore admissible.

Une fois l'accord de démarrage obtenu, --execute réalise un nombre fini de pas
papier (maximum trois captures) puis ferme l'application. --macro-data pointe
optionnellement sur un sidecar v3 existant, sans collecte d'actualités, avec les
contrôles du protocole v3. Aucun accès FF sans droits confirmés. Les événements
sans destinataire restent conservés ; le bilan indique outbox_pending.
Aucune tâche de lancement automatique n'a été installée ni démarrée ici.

Windows 11 : ouvrir PowerShell dans le dépôt, vérifier Python 3.12, créer/activer
le venv, installer les dépendances, puis exécuter python -m tools.diagnose_windows.
Contrôler SQLite au redémarrage, dry-run avec captures locales et compte existant,
puis navigateur sur 1440/390 px (Performances, Apprentissage, Macro).
Utiliser une installation de test avant le dossier utilisateur ; ne pas réinitialiser
les données en cas d'erreur. Aucun test Windows exécuté ici (Linux uniquement).

## Limites vérifiées

Suite #29 réexécutée : **433 tests réussis** en 104,76 s. Livraison finale :
**456 réussis**, dont **23 nouveaux**, aucun échec / erreur / skip / xfail,
118,78 s, CPython 3.12.14 Linux + CCXT 4.4.99 complet, seccomp sans réseau.
Un avertissement Starlette/TestClient conservé. Bilan machine et empreintes :
mesures/prospective-preflight/verification.json. Les tests ciblés sont rejouables
avec python -m pytest -o addopts='' -q tests/test_observation_e2e.py tests/test_macro_replay.py ;
sur Linux utiliser le lanceur seccomp tools/security/offline_exec.c comme les
procédures de validation existantes. Le diagnostic local a été réexécuté sous
Linux avec succès ; il ne valide pas Windows.

Les GET publics time/exchangeInfo/depth ont répondu HTTP 200 ; les trois paires
EUR sont présentes. Le premier appel a pris ~9 s : dépassement du critère de 5 s,
donc pas de déclaration « prêt en production ». Aucun téléchargement complet
prospectif authentique, aucun nouvel entraînement réel ni promotion n'a été lancé.

Chromium a été relancé pour les tests 1440/390 : blocage avant la première page,
socket Unix interdite. Serveur démo temporaire arrêté. Rendu navigateur non validé.
Les suites API/Performances/Apprentissage/Macro existantes sont réexécutées.
Ruff sur les fichiers de cette mission et pip check sont contrôlés ; les deux
anciens F401 de #27 restent documentés, dont le fichier épinglé v3.
