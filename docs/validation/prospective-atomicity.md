# Préparation prospective depuis #23

## Transaction et reprise

Le chemin normal de l'agent (`record=True`, hors backtest) utilise deux étapes :

1. Commit d'une intention de prédiction : caractéristiques, prix du signal,
   probabilités, version du modèle, seuil, empreinte des réglages et heure
   d'enregistrement. Aucun ordre n'est encore soumis.
2. Transaction réentrante englobant ordre fictif, fills, soldes, grand livre,
   position, décision finale et marqueur de bougie traitée. En cas d'arrêt brutal
   du processus avant commit, cette transaction est annulée ; l'intention reste.

La même bougie ne peut être engagée une deuxième fois après commit. La reprise
conserve les probabilités enregistrées et refuse des paramètres, une version de
modèle ou des caractéristiques modifiées. Les valeurs manquantes restent
comparables sans être transformées en données connues.

Au redémarrage du service d'agents, une intention de la dernière bougie permet
sa reprise. Une intention plus ancienne devient un refus explicite (« signal
expiré ») et n'est pas exécutée tardivement. La migration est additive : nouvelle
table `prospective_bars`, sans effacer les anciens comptes ou candidats.

Le test de panne utilise une base sur disque et un sous-processus qui exécute
réellement un fill papier, puis appelle `os._exit(77)` sans nettoyage Python.
À la réouverture : prédiction conservée, aucun fill ni débit orphelin. Deux
sous-processus successifs de reprise laissent exactement un fill, une position,
une décision et une bougie engagée. Un autre cas vérifie le rollback sur exception
et la reprise dans le même processus. Ce test utilise un compte isolé fictif
historique à 10 000 USDC ; il ne touche pas au portefeuille principal EUR.

Les garanties concernent SQLite et l'arrêt de processus. Elles ne constituent
pas un test de coupure électrique réelle ni de panne matérielle. Les notifications
WebSocket ne constituent pas une preuve de commit ; un outbox transactionnel
reste à évaluer avant une collecte permanente destinée à des consommateurs externes.

## Modèle et protocole figés

`research/prospective-v2` contient le modèle réel, sa fiche, les réglages, les
critères d'évaluation et la période UTC du 13/11/2026 au 13/02/2027. Les empreintes
sont vérifiées par `python -m tools.prospective check --root research/prospective-v2`.
Le fichier joblib est un artefact de confiance créé ici ; ne jamais charger un
joblib provenant d'un tiers non fiable.

Le modèle reprend la recette à 500 candidats résolus avant juillet 2025, sans
optimisation sur les résultats consultés. Son statut reste candidat gelé, pas
Champion promu. Aucun historique exploré ne peut servir à le promouvoir. La
comparaison prospective préenregistrée est contre le cash à capital identique,
bootstrap quotidien par blocs de 7 jours, 2 000 tirages, au moins 30 trades et
28 jours ; les bornes absolue et relative doivent être positives. Même dans ce
cas, aucune promotion automatique n'est autorisée par ce protocole.

## Préflight reproductible, sans service permanent

1. Extraire cette branche et installer les dépendances Python 3.12.
2. Exécuter les tests avec le lanceur hors réseau de `tools/security/README.md`.
3. Vérifier le protocole gelé avec la commande `check` ci-dessus ; lire aussi
   `model-card.json` et `evaluation-plan.json`.
4. Vérifier les archives déjà présentes avec `load_archive`, sans les télécharger.
5. Valider le navigateur desktop/mobile et Windows 11 dans les environnements
   correspondants avant de demander un démarrage permanent.

`SupervisedObserver(app, root)` fournit un adaptateur de pas explicites : pas de
thread, de planificateur, de téléchargement ni d'entraînement automatique. Il
exige l'unique portefeuille principal EUR initial à 30 €, les réglages publics
figés et l'arrêt des autres agents autonomes. Chaque appel `on_bar` vérifie les
empreintes, la période, l'actif et une bougie clôturée et récente. Il ne réinitialise
jamais un portefeuille existant. Les données transmises doivent provenir du
pipeline public causal existant et les prix de l'application doivent être frais.

Le constructeur et le refus avant la période sont testés avec un modèle réel
et une application temporaire sur données artificielles. La collecte publique
en continu, la reprise complète du processus de supervision et l'outbox restent
à tester de bout en bout : **ne pas lancer de démon permanent avec cette livraison**.
Aucun service, aucune observation publique prospective n'a été démarré ici.

## Interface et systèmes

La suite inclut les contrôles comptables de Performances (dont sortie partielle).
Les tests Playwright ont été retentés : navigateur attendu absent, puis Chromium
système bloqué par `socket() failed: Operation not permitted`. Aucun succès
ordinateur/mobile n'est revendiqué. Windows 11 n'est pas disponible : conserver
la procédure de `marches-publics.md`. Aucun test Windows n'a été annoncé.

La préparation v1 est conservée mais explicitement invalide : elle contenait un intervalle de réentraînement à zéro, rejeté par la configuration. v2 conserve le même modèle et désactive uniquement `learning`, avec un intervalle valide. Aucune observation n’a utilisé v1.
