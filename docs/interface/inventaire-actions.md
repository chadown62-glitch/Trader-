# Inventaire des actions du cockpit

Main audité : `6d46dc9` (`web/` et `sentinelle/` identiques à `7ffaadad`), instance de démonstration du 23 novembre 2026.
- **Méthode statique :** extraction de chaque gestionnaire d'événement des 15 pages et de la coque (`web/js/`), avec les
  routes appelées par la fonction correspondante.
- **Méthode dynamique :** `tests/e2e/actions_audit.js`, sur une instance de démonstration jetable. Marché simulé, base
  SQLite temporaire, aucun ordre réel. Le script clique chaque action visible, onglets compris, et consigne les requêtes
  `/api`, leur statut, les messages, les confirmations et les erreurs du navigateur.
- **Tests :** correspondance par route (Pytest) et par libellé (Playwright).

**Bilan :**
- 118 actions inventoriées ;
- 57 avec un effet constaté ;
- 38 non cliquées par l'audit dynamique (raison indiquée) ;
- 3 défauts reproduits ;
- 66 sans test automatisé associé.

**Statut :**
- Aucun bouton n'a été trouvé simulant un succès.
- Les actions de la session Memecoin (Démarrer, Pause, Cycle manuel) ne sont pas cliquées contre l'instance : elles
  interrogent des sources publiques. Elles sont couvertes par `memecoin_check.js` (routes simulées) et par les tests
  Pytest du worker.

**Contrôles particuliers constatés :**
- Connexion : premier mot de passe créé (`POST /api/auth/setup` 200).
- Déconnexion : `POST /api/auth/logout` 200, puis retour à l'écran de connexion.
- Les 15 liens de navigation atteignent leur page ; sur mobile (390 px), la barre basse et le menu « Plus » (refermé après
  le choix) aussi, sans débordement horizontal.
- Arrêt d'urgence :
  - « Tout revendre » reste désactivé tant que URGENCE n'est pas saisi
    (vérifié) ;
  - la revente fictive appelle `POST /api/emergency` (200) ;
  - l'alerte critique diffusée par le serveur s'affiche en rouge, en plus du message de l'action (deux messages pour une
    seule action : voulu, non corrigé).
- Thème clair et sombre : bascule constatée.

**Défauts reproduits** (corrigés dans la PR suivante du chantier) :
- D-UI1 : code trop court → message brut en anglais (« String should have at least 6 characters ») ; même cause pour toute erreur 422 (`web/js/api.js` l. 25-26).
- D-UI2 : champ vide → aucune requête, aucun message, bouton actif (`web/js/views/signals.js` l. 47-51 et 94).
- D-UI3 : champs vides → aucune requête, aucun message, bouton actif (`web/js/views/signals.js` l. 25-30 et 79).

## Coque (connexion, barre, navigation, confirmations) (`web/js/main.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Sentinelle ⟨ store.auth.setupRequire » (`<form>` l.72, @submit.prevent) | ouvre la session | `submit` | — | cookie de session, cockpit affiché | POST /api/auth/setup 200 | — | aucun |
| 2 | « 🛑 Arrêt d'urgence Deux niveaux possibles : » (`<div>` l.113, @click) | ouvre ou ferme le panneau d'arrêt d'urgence | `store.emergencyOpen=false` | — | fenêtre affichée ou masquée | non cliqué : exige la fenêtre d'urgence ouverte : la fermeture est exercée par le parcours après chaque étape, sans ligne propre | — | aucun |
| 3 | « ⏸ Arrêter les IA » (`<button>` l.120, @click) | arrêt d'urgence : pause des IA ou revente fictive | `pause` | POST /api/emergency | agents arrêtés ; positions fictives fermées (revente) | POST /api/emergency 200 ; message « IA arrêtées (0). Les positions gardent leurs stops. » | — | pytest `test_api.py`, `test_lockdown.py` |
| 4 | « Positions de l'IA » (`<button>` l.126, @click) | change l'onglet, le filtre ou la sélection affichée | `scope='ai'` | — | affichage local, sans requête d'écriture | affichage modifié | — | aucun |
| 5 | « Toutes les positions » (`<button>` l.127, @click) | change l'onglet, le filtre ou la sélection affichée | `scope='all'` | — | affichage local, sans requête d'écriture | 1 lecture(s) GET ; affichage modifié | — | aucun |
| 6 | « Tout revendre maintenant » (`<button>` l.130, @click) | arrêt d'urgence : pause des IA ou revente fictive | `flatten` | POST /api/emergency | agents arrêtés ; positions fictives fermées (revente) | POST /api/emergency 200 ; message « Arrêt d'urgence exécuté (0 position(s) fermée(s)) » | — | pytest `test_api.py`, `test_lockdown.py` |
| 7 | « Fermer » (`<button>` l.132, @click) | ouvre ou ferme le panneau d'arrêt d'urgence | `store.emergencyOpen=false` | — | fenêtre affichée ou masquée | non cliqué : exige la fenêtre d'urgence ouverte : la fermeture est exercée par le parcours après chaque étape, sans ligne propre | — | aucun |
| 8 | « ⟨ c.title ⟩ ⟨ c.message ⟩ Tapez « ⟨ c.phrase ⟩ » pour confir » (`<div>` l.144, @click) | répond à la demande de confirmation | `close(false)` | — | action confirmée ou annulée | confirmation demandée (« Réinitialiser « Portefeuille principal » ») | — | aucun |
| 9 | « Annuler » (`<button>` l.150, @click) | répond à la demande de confirmation | `close(false)` | — | action confirmée ou annulée | confirmation demandée (« Réinitialiser « Portefeuille principal » ») | — | aucun |
| 10 | « ⟨ c.confirmLabel ⟩ » (`<button>` l.151, @click) | répond à la demande de confirmation | `close(c.phrase ? c.typed.trim() : true)` | — | action confirmée ou annulée | confirmation demandée (« Réinitialiser « Portefeuille principal » ») | — | aucun |
| 11 | « 🛑 Urgence » (`<button>` l.251, @click) | ouvre ou ferme le panneau d'arrêt d'urgence | `store.emergencyOpen = true` | — | fenêtre affichée ou masquée | 1 lecture(s) GET ; confirmation demandée (« 🛑 Arrêt d'urgence ») ; affichage modifié | — | aucun |
| 12 | « ⟨ theme==='dark' ? '☀' : '☾' ⟩ » (`<button>` l.252, @click) | bascule le thème clair ou sombre | `toggleTheme` | — | thème et préférence locale | aucun effet visible | — | aucun |
| 13 | « ⎋ » (`<button>` l.253, @click) | ferme la session | `logout` | POST /api/auth/logout | retour à l'écran de connexion | POST /api/auth/logout 200 | — | pytest `test_api.py` |
| 14 | « ⟨ n.ico ⟩ ⟨ n.short // n.label.split(' ')[0] ⟩ » (`<a>` l.261, @click) | ouvre ou ferme le menu mobile « Plus » | `more=false` | — | menu affiché ou masqué | affichage modifié | — | aucun |
| 15 | « ⋯ Plus » (`<a>` l.262, @click) | ouvre ou ferme le menu mobile « Plus » | `more = !more` | — | menu affiché ou masqué | affichage modifié | — | e2e `dmh_check.js`; e2e `memecoin_check.js`; e2e `macro_check.js`; e2e `phase_h_check.js`; e2e `champion_check.js` |
| 16 | « ⟨ n.ico ⟩ ⟨ n.label ⟩ » (`<div>` l.264, @click) | ouvre ou ferme le menu mobile « Plus » | `more=false` | — | menu affiché ou masqué | affichage modifié | — | aucun |
| 17 | « ⟨ n.ico ⟩ ⟨ n.label ⟩ » (`<a>` l.265, @click) | ouvre ou ferme le menu mobile « Plus » | `more=false` | — | menu affiché ou masqué | affichage modifié | — | aucun |

## Tableau de bord (`web/js/views/dashboard.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Passer au portefeuille principal… » (`<button>` l.108, @click) | relit la vue d'ensemble | `activateMain` | GET /api/overview<br>POST /api/portfolio/activate | comptes rafraîchis | non cliqué : exige un portefeuille hérité à migrer | — | pytest `test_api.py`, `test_lockdown.py`; pytest `test_portfolio.py` |
| 2 | « ⟨ a.name ⟩ ⟨ a.legacy ? 'ANCIEN COMPTE — INERTE' : a.archive » (`<div>` l.112, @click) | change l'onglet, le filtre ou la sélection affichée | `selected = a.id` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 3 | « ⟨ d ⟩ j » (`<button>` l.137, @click) | change l'onglet, le filtre ou la sélection affichée | `days=d` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 4 | « Démarrer » (`<button>` l.189, @click) | démarre ou arrête un agent fictif | `startAgent(a)` | POST /api/agents/${a.name}/start | état de l'agent, toast | POST /api/agents/ia_entrainement/start 200 ; message « Préparation de l’IA en cours (téléchargement de l’historique)… » | — | e2e `memecoin_check.js`; e2e `phase_h_check.js`; e2e `ui_check.js`; pytest `test_api.py`, `test_lockdown.py` |
| 5 | « Arrêter » (`<button>` l.190, @click) | démarre ou arrête un agent fictif | `stopAgent(a)` | POST /api/agents/${a.name}/stop | état de l'agent, toast | POST /api/agents/ia_entrainement/stop 200 ; message « IA arrêtée (les positions gardent leurs stops) » | — | pytest `test_api.py`, `test_lockdown.py` |

## Performances (`web/js/views/performance.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ⟨ p[1] ⟩ » (`<button>` l.65, @click) | change l'onglet, le filtre ou la sélection affichée | `period=p[0]` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 2 | « ⟨ p[1] ⟩ ⟨ signed(rep.p » (`<tr>` l.94, @click) | change l'onglet, le filtre ou la sélection affichée | `period=p[0]` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |

## Trader (`web/js/views/trade.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ⟨ s.split('/')[0] ⟩ » (`<button>` l.200, @click) | change l'onglet, le filtre ou la sélection affichée | `symbol=s` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 2 | « ⟨ x ⟩ » (`<button>` l.216, @click) | change l'onglet, le filtre ou la sélection affichée | `tf=x` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 3 | « Modifier » (`<button>` l.234, @click) | ouvre ou annule l'édition d'une position | `editPos(p)` | — | formulaire d'édition | non cliqué : exige une position fictive ouverte | — | aucun |
| 4 | « Fermer » (`<button>` l.234, @click) | ferme ou modifie une position fictive | `closePos(p)` | POST /api/positions/${p.id}/close | position fermée ou stops modifiés | non cliqué : exige une position fictive ouverte | — | pytest `test_api.py`, `test_lockdown.py` |
| 5 | « Enregistrer » (`<button>` l.244, @click) | ferme ou modifie une position fictive | `saveEdit` | PATCH /api/positions/${e.id} | position fermée ou stops modifiés | non cliqué : exige une position en édition | — | e2e `ui_check.js`; pytest `test_api.py`, `test_lockdown.py` |
| 6 | « Annuler » (`<button>` l.244, @click) | ouvre ou annule l'édition d'une position | `editing=null` | — | formulaire d'édition | non cliqué : exige une position en édition | — | aucun |
| 7 | « Annuler » (`<button>` l.258, @click) | passe ou annule un ordre fictif | `cancelOrder(o)` | DEL /api/orders/${o.id}?account_id=${accountId.value} | ordre fictif, position | non cliqué : exige un ordre fictif en attente | — | pytest `test_api.py`, `test_lockdown.py` |
| 8 | « Acheter » (`<button>` l.268, @click) | prépare le ticket ou le formulaire | `t.side='buy'` | — | champs du formulaire | désactivé dans l'état de démonstration | — | aucun |
| 9 | « Vendre » (`<button>` l.269, @click) | prépare le ticket ou le formulaire | `t.side='sell'` | — | champs du formulaire | 1 lecture(s) GET ; affichage modifié | — | aucun |
| 10 | « Au marché » (`<button>` l.272, @click) | prépare le ticket ou le formulaire | `t.type='market'` | — | champs du formulaire | 1 lecture(s) GET | — | aucun |
| 11 | « À cours limité » (`<button>` l.272, @click) | prépare le ticket ou le formulaire | `t.type='limit'` | — | champs du formulaire | affichage modifié | — | aucun |
| 12 | « ⟨ cashCcy ⟩ » (`<button>` l.282, @click) | prépare le ticket ou le formulaire | `t.mode='quote'` | — | champs du formulaire | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 13 | « ⟨ base ⟩ » (`<button>` l.282, @click) | prépare le ticket ou le formulaire | `t.mode='base'` | — | champs du formulaire | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 14 | « ⟨ p ⟩ % » (`<button>` l.283, @click) | prépare le ticket ou le formulaire | `setPortion(p)` | — | champs du formulaire | non cliqué : exige le ticket ouvert | — | aucun |
| 15 | « −⟨ p ⟩ % » (`<button>` l.288, @click) | prépare le ticket ou le formulaire | `setSl(p)` | — | champs du formulaire | non cliqué : exige le ticket ouvert | — | aucun |
| 16 | « +⟨ p ⟩ % » (`<button>` l.290, @click) | prépare le ticket ou le formulaire | `setTp(p)` | — | champs du formulaire | non cliqué : exige le ticket ouvert | — | aucun |
| 17 | « ⟨ t.side==='buy' ? 'Acheter' : 'Vendre' ⟩ ⟨ base ⟩ » (`<button>` l.305, @click) | passe ou annule un ordre fictif | `submit` | POST /api/orders | ordre fictif, position | non cliqué : exige un ticket rempli | — | pytest `test_api.py`, `test_configurable_capital.py` |

## IA (`web/js/views/ai.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Entraînement en direct » (`<button>` l.103, @click) | change l'onglet, le filtre ou la sélection affichée | `tab='training'` | — | affichage local, sans requête d'écriture | aucun effet visible | — | aucun |
| 2 | « Bulletin de fiabilité » (`<button>` l.104, @click) | change l'onglet, le filtre ou la sélection affichée | `tab='proof'` | — | affichage local, sans requête d'écriture | affichage modifié | — | aucun |
| 3 | « Apprentissage sur l'historique » (`<button>` l.105, @click) | change l'onglet, le filtre ou la sélection affichée | `tab='learn'` | — | affichage local, sans requête d'écriture | affichage modifié | — | e2e `train_check.js` |
| 4 | « Capacités d'apprentissage » (`<button>` l.106, @click) | change l'onglet, le filtre ou la sélection affichée | `tab='bench'` | — | affichage local, sans requête d'écriture | 2 lecture(s) GET ; affichage modifié | — | e2e `ui_check.js` |
| 5 | « Simulation accélérée » (`<button>` l.107, @click) | change l'onglet, le filtre ou la sélection affichée | `tab='sim'` | — | affichage local, sans requête d'écriture | 1 lecture(s) GET ; affichage modifié | — | e2e `train_check.js` |
| 6 | « Journal des décisions » (`<button>` l.108, @click) | change l'onglet, le filtre ou la sélection affichée | `tab='journal'` | — | affichage local, sans requête d'écriture | 1 lecture(s) GET ; affichage modifié | — | aucun |
| 7 | « ▶ Démarrer l'entraînement » (`<button>` l.119, @click) | démarre ou arrête un agent fictif | `startTraining` | POST /api/agents/ia_entrainement/start | état de l'agent, toast | non cliqué : exige l'onglet d'entraînement en direct arrêté | — | e2e `ui_check.js`; pytest `test_api.py` |
| 8 | « ⏸ Arrêter » (`<button>` l.120, @click) | démarre ou arrête un agent fictif | `stopAgent('ia_entrainement')` | POST /api/agents/${name}/stop | état de l'agent, toast | non cliqué : exige l'agent d'entraînement en marche | — | pytest `test_api.py`, `test_lockdown.py` |
| 9 | « Arrêter et fermer ses positions » (`<button>` l.120, @click) | démarre ou arrête un agent fictif | `stopAgent('ia_entrainement', true)` | POST /api/agents/${name}/stop | état de l'agent, toast | non cliqué : exige l'agent d'entraînement en marche | — | pytest `test_api.py`, `test_lockdown.py` |
| 10 | « 🧠 Lancer l'entraînement » (`<button>` l.200, @click) | lance un entraînement du modèle | `train` | POST /api/ai/train | tâche d'entraînement en cours | POST /api/ai/train 200 ; message « Entraînement lancé : il peut prendre quelques minutes » | — | e2e `train_check.js` |
| 11 | « Rapport » (`<button>` l.218, @click) | rapport, activation ou retrait d'un modèle | `openReport(m.version)` | GET /api/ai/models/${encodeURIComponent(v)}/report | modèle actif ou retiré (validation humaine) | non cliqué : exige un modèle entraîné | — | aucun |
| 12 | « Activer » (`<button>` l.219, @click) | rapport, activation ou retrait d'un modèle | `promote(m)` | POST /api/ai/models/${encodeURIComponent(m.version)}/promote | modèle actif ou retiré (validation humaine) | non cliqué : exige un modèle entraîné | — | aucun |
| 13 | « Retirer » (`<button>` l.220, @click) | rapport, activation ou retrait d'un modèle | `retire(m)` | POST /api/ai/models/${encodeURIComponent(m.version)}/retire | modèle actif ou retiré (validation humaine) | non cliqué : exige un modèle entraîné | — | aucun |
| 14 | « ▶ Lancer la simulation » (`<button>` l.302, @click) | lance une simulation accélérée | `backtest` | POST /api/ai/backtest | résultats de simulation | POST /api/ai/backtest 200 ; message « Simulation lancée » | — | e2e `train_check.js`; pytest `test_api.py` |

## Historique (`web/js/views/history.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Tous » (`<button>` l.50, @click) | change l'onglet, le filtre ou la sélection affichée | `origin=''` | — | affichage local, sans requête d'écriture | 1 lecture(s) GET | — | e2e `phase_h_check.js`; e2e `train_check.js` |
| 2 | « IA » (`<button>` l.50, @click) | change l'onglet, le filtre ou la sélection affichée | `origin='ai'` | — | affichage local, sans requête d'écriture | 3 lecture(s) GET | — | aucun |
| 3 | « Manuels » (`<button>` l.50, @click) | change l'onglet, le filtre ou la sélection affichée | `origin='manual'` | — | affichage local, sans requête d'écriture | 3 lecture(s) GET | — | aucun |
| 4 | « ⟨ dt(t.exit_ts) ⟩ ⟨ t.symbol ⟩ <span class= » (`<tr>` l.79, @click) | change l'onglet, le filtre ou la sélection affichée | `open = open===t.id ? null : t.id` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |

## Fiches cryptos (`web/js/views/knowledge.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ↻ Mettre à jour » (`<button>` l.56, @click) | met à jour les fiches cryptos | `refresh` | POST /api/knowledge/refresh | téléchargement lancé | POST /api/knowledge/refresh 200 ; message « Mise à jour des fiches lancée (téléchargement de l’historique complet) » | — | aucun |
| 2 | « ⟨ i.symbol.split('/')[0] ⟩ ⟨ i.name ⟩ <td » (`<tr>` l.73, @click) | ouvre la fiche d'une crypto | `open(i.symbol)` | GET /api/knowledge/detail?symbol= | détail affiché | libellé dynamique : instances cliquées listées en fin de document | — | pytest `test_learning.py` |

## Apprentissage (`web/js/views/apprentissage.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Relire les journaux » (`<button>` l.37, @click) | relit le champion et le journal des décisions | `load` | GET /api/ai/champion<br>GET /api/ai/journal?limit=60&decision= | liste rafraîchie | 2 lecture(s) GET | — | pytest `test_champion_integration.py` |
| 2 | « Toutes Prises Refusées Préparées » (`<select>` l.92, @change) | relit le champion et le journal des décisions | `load` | GET /api/ai/champion<br>GET /api/ai/journal?limit=60&decision= | liste rafraîchie | 2 lecture(s) GET | — | pytest `test_champion_integration.py` |
| 3 | « ⟨ r.symbol ⟩ · ⟨ r.strategy ⟩ » (`<button>` l.99, @click) | change l'onglet, le filtre ou la sélection affichée | `open = open===r.id ? null : r.id` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |

## Contexte macro (`web/js/views/macro.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Relire le journal local » (`<button>` l.29, @click) | relit le journal macro local | `load` | GET /api/macro-shadow | journal affiché | 1 lecture(s) GET | — | e2e `macro_check.js`; e2e `phase_h_check.js`; pytest `test_macro_shadow.py` |

## Préparation (`web/js/views/preparation.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Refaire le contrôle » (`<button>` l.23, @click) | refait le contrôle de préparation | `load` | GET /api/observation/preflight | état READY/BLOCKED | 1 lecture(s) GET | — | e2e `phase_h_check.js`; pytest `test_preflight.py`, `test_supervisor.py` |

## Memecoin (`web/js/views/memecoin.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ⟨ name ⟩ » (`<button>` l.166, @click) | change l'onglet, le filtre ou la sélection affichée | `tab=name` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 2 | « (sans libellé) » (`<form>` l.177, @submit.prevent) | démarre la session memecoin fictive | `act('start')` | — | worker actif, compteur de cycles | non cliqué : exige la session memecoin, qui interroge des sources publiques : couverte par memecoin_check.js (routes simulées) et les tests Pytest du worker | — | aucun |
| 3 | « ⟨ n.toLocaleString('fr-FR') ⟩ $ » (`<button>` l.180, @click) | prépare le ticket ou le formulaire | `amount = n; currency = 'USD'` | — | champs du formulaire | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 4 | « Pause » (`<button>` l.186, @click) | met en pause la session memecoin | `act('stop')` | — | worker arrêté après le cycle en cours | non cliqué : exige la session memecoin, qui interroge des sources publiques : couverte par memecoin_check.js (routes simulées) et les tests Pytest du worker | — | e2e `memecoin_check.js`; e2e `ui_check.js` |
| 5 | « Cycle manuel » (`<button>` l.187, @click) | exécute un cycle manuel | `act('tick')` | — | révision de session +1 | non cliqué : exige la session memecoin, qui interroge des sources publiques : couverte par memecoin_check.js (routes simulées) et les tests Pytest du worker | — | e2e `memecoin_check.js` |

## Hub de marché (`web/js/views/dmh.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Arrêt immédiat des nouvelles opérations fictives » (`<button>` l.77, @click) | arrête les nouvelles opérations fictives du hub | `halt` | POST /api/dmh/halt | hub en arrêt | POST /api/dmh/halt 200 ; message « Nouvelles opérations fictives arrêtées » | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 2 | « Relancer » (`<button>` l.78, @click) | relance les opérations fictives du hub | `resume` | POST /api/dmh/resume | hub actif | POST /api/dmh/resume 200 ; message « Opérations fictives relancées » | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 3 | « Réconcilier » (`<button>` l.79, @click) | réconcilie le registre fictif | `reconcile` | POST /api/dmh/reconcile | registre réconcilié | POST /api/dmh/reconcile 200 ; message « Portefeuille réconcilié » | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 4 | « ⟨ t[1] ⟩ » (`<button>` l.82, @click) | action locale | `open(t[0])` | — | affichage local | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 5 | « Relire » (`<button>` l.86, @click) | relit les marchés | `loadBoard` | GET /api/dmh/markets?assets=BTC,ETH,SOL | tableau des marchés | 1 lecture(s) GET | — | e2e `dmh_check.js`; e2e `macro_check.js`; e2e `phase_h_check.js`; pytest `test_dmh_concurrency.py` |
| 6 | « ⟨ busy ? 'Calcul…' : 'Prévisualiser (aucune exécution)' ⟩ » (`<button>` l.118, @click) | prévisualise une opération (aucune exécution) | `preview` | POST /api/dmh/preview | devis affiché | non cliqué : exige un formulaire de prévisualisation rempli | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 7 | « Demander un brouillon » (`<button>` l.122, @click) | demande un brouillon de stratégie à l'assistant local | `ask` | POST /api/dmh/strategy | brouillon affiché | désactivé dans l'état de démonstration | — | pytest `test_dmh_concurrency.py` |
| 8 | « Reprendre dans le formulaire » (`<button>` l.126, @click) | modifie la liste ou le formulaire en cours | `useDraft` | — | liste locale (enregistrée ensuite) | non cliqué : exige un brouillon de l'assistant local | — | aucun |
| 9 | « Confirmer l'opération fictive » (`<button>` l.157, @click) | exécute l'opération fictive confirmée | `confirm` | POST /api/dmh/orders | ordre fictif enregistré | non cliqué : exige une prévisualisation acceptée | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 10 | « Mettre à jour les ordres ouverts » (`<button>` l.163, @click) | met à jour les ordres ouverts fictifs | `refreshOpen` | POST /api/dmh/open-orders/refresh | ordres rafraîchis | POST /api/dmh/open-orders/refresh 200 ; message « Ordres ouverts mis à jour » | — | pytest `test_dmh_concurrency.py` |
| 11 | « Réconcilier » (`<button>` l.163, @click) | réconcilie le registre fictif | `reconcile` | POST /api/dmh/reconcile | registre réconcilié | POST /api/dmh/reconcile 200 ; message « Portefeuille réconcilié » | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 12 | « ⟨ p.final ? 'Vendre (fictif)' : 'Finalité en attente' ⟩ » (`<button>` l.171, @click) | prévisualise une opération (aucune exécution) | `previewSell(p)` | POST /api/dmh/preview | devis affiché | non cliqué : exige une position du simulateur du hub | — | pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |
| 13 | « Relire » (`<button>` l.179, @click) | action locale | `open('observatoire')` | — | affichage local | 1 lecture(s) GET | — | e2e `dmh_check.js`; e2e `macro_check.js`; e2e `phase_h_check.js` |
| 14 | « Enregistrer » (`<button>` l.197, @click) | enregistre les limites du hub | `saveLimits` | PUT /api/dmh/limits | limites persistées | PUT /api/dmh/limits 200 ; message « Limites enregistrées » | — | e2e `ui_check.js`; pytest `test_dmh_api.py`, `test_dmh_concurrency.py` |

## Actualités (`web/js/views/news.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ↻ Analyser maintenant » (`<button>` l.29, @click) | lance l'analyse des actualités | `refresh` | POST /api/news/refresh | actualités analysées | POST /api/news/refresh 200 ; message « Analyse lancée : les actualités apparaîtront dans quelques instants » | — | aucun |
| 2 | « Activer le module Actualités » (`<button>` l.38, @click) | enregistre des réglages | `enable` | PUT /api/settings | réglages persistés | PUT /api/settings 200 ; message « Module Actualités activé » | — | pytest `test_api.py`, `test_learning.py` |

## Signaux (`web/js/views/signals.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ↻ Évaluer les signaux » (`<button>` l.60, @click) | évalue les signaux des canaux | `resolve` | POST /api/signals/resolve | compteur évalué | POST /api/signals/resolve 200 ; message « 0 signal(aux) évalué(s) » | — | aucun |
| 2 | « Enregistrer » (`<button>` l.79, @click) | enregistre ou supprime des secrets locaux | `saveCreds` | PUT /api/secrets | secret défini ou effacé | 1 lecture(s) GET | D-UI3 : champs vides → aucune requête, aucun message, bouton actif | e2e `ui_check.js`; pytest `test_api.py`, `test_lockdown.py` |
| 3 | « Recevoir le code » (`<button>` l.79, @click) | demande le code de connexion Telegram | `login` | POST /api/signals/login | code envoyé | désactivé dans l'état de démonstration | — | aucun |
| 4 | « Valider » (`<button>` l.83, @click) | valide le code Telegram | `sendCode` | POST /api/signals/code | compte connecté | non cliqué : exige un code Telegram demandé | — | aucun |
| 5 | « Lire les canaux ci-contre Ce compte sert aussi aux canau » (`<input>` l.89, @change) | action locale | `enable($event.target.checked)` | — | affichage local | non cliqué : exige le compte Telegram | — | aucun |
| 6 | « Ajouter » (`<button>` l.94, @click) | action locale | `addChannel` | — | affichage local | aucun effet visible | D-UI2 : champ vide → aucune requête, aucun message, bouton actif | aucun |
| 7 | « Retirer » (`<button>` l.102, @click) | action locale | `removeChannel(c.channel)` | — | affichage local | non cliqué : exige un canal suivi | — | aucun |

## Réglages (`web/js/views/settings.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ⟨ t[1] ⟩ » (`<button>` l.211, @click) | change l'onglet, le filtre ou la sélection affichée | `tab=t[0]` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |
| 2 | « ✕ » (`<button>` l.217, @click) | modifie la liste ou le formulaire en cours | `removeSymbol(s)` | — | liste locale (enregistrée ensuite) | non cliqué : exige un symbole de la liste (onglet Général) | — | aucun |
| 3 | « Ajouter » (`<button>` l.218, @click) | modifie la liste ou le formulaire en cours | `addSymbol` | — | liste locale (enregistrée ensuite) | non cliqué : exige l'onglet Général | — | aucun |
| 4 | « Enregistrer » (`<button>` l.222, @click) | relit la liste des marchés | `saveGeneral` | GET /api/markets<br>PUT /api/settings | liste rafraîchie | 1 lecture(s) GET ; message « Rien à enregistrer » | — | e2e `ui_check.js`; pytest `test_api.py`; pytest `test_api.py`, `test_learning.py` |
| 5 | « % » (`<input>` l.241, @input) | modifie un champ de réglage (enregistré ensuite) | `setValue(key, f, $event.target.value)` | — | valeur en attente d'enregistrement | non cliqué : exige un onglet de réglages ouvert (champ saisi puis enregistré) | — | aucun |
| 6 | « <input v-else type="number" :step="f.step // 1" :value="disp » (`<input>` l.243, @change) | modifie un champ de réglage (enregistré ensuite) | `setValue(key, f, $event.target.value)` | — | valeur en attente d'enregistrement | non cliqué : exige un onglet de réglages ouvert (champ saisi puis enregistré) | — | aucun |
| 7 | « ⟨ f.help ⟩ <div » (`<input>` l.244, @input) | modifie un champ de réglage (enregistré ensuite) | `setValue(key, f, $event.target.value)` | — | valeur en attente d'enregistrement | non cliqué : exige un onglet de réglages ouvert (champ saisi puis enregistré) | — | aucun |
| 8 | « Enregistrer » (`<button>` l.254, @click) | enregistre des réglages | `save(key)` | PUT /api/settings | réglages persistés | 1 lecture(s) GET ; message « Rien à enregistrer » | — | e2e `ui_check.js`; pytest `test_api.py`, `test_learning.py` |
| 9 | « Supprimer les anciennes clés » (`<button>` l.264, @click) | enregistre ou supprime des secrets locaux | `clearLegacyKeys` | PUT /api/secrets | secret défini ou effacé | non cliqué : exige d'anciennes clés enregistrées | — | pytest `test_api.py`, `test_lockdown.py` |
| 10 | « Enregistrer le jeton » (`<button>` l.283, @click) | enregistre ou supprime des secrets locaux | `saveSecrets(['telegram_token'])` | PUT /api/secrets | secret défini ou effacé | 1 lecture(s) GET ; message « Rien à enregistrer » | — | pytest `test_api.py`, `test_lockdown.py` |
| 11 | « Envoyer un message de test » (`<button>` l.283, @click) | envoie un message Telegram de test | `testTelegram` | POST /api/telegram/test | message reçu ou erreur | POST /api/telegram/test 400 ; message « Envoi impossible : vérifiez le jeton et votre identifiant » ; erreur navigateur : Failed to load resource: the server responded with a status of 400 (Ba | refusé avec message clair | aucun |
| 12 | « Réinitialiser » (`<button>` l.301, @click) | crée, réinitialise ou reprend un compte fictif | `resetAccount(a)` | POST /api/accounts/${a.id}/reset | liste des comptes | confirmation demandée (« Réinitialiser « Portefeuille principal » ») | — | pytest `test_api.py`, `test_configurable_capital.py` |
| 13 | « Reprendre après arrêt de sécurité » (`<button>` l.303, @click) | crée, réinitialise ou reprend un compte fictif | `resumeAccount(a)` | POST /api/accounts/${a.id}/resume | liste des comptes | non cliqué : exige un compte en arrêt de sécurité | — | pytest `test_api.py`, `test_configurable_capital.py` |
| 14 | « Créer » (`<button>` l.316, @click) | relit la vue d'ensemble | `createAccount` | GET /api/overview<br>POST /api/accounts | comptes rafraîchis | non cliqué : exige l'onglet Comptes | — | pytest `test_api.py`, `test_lockdown.py`; pytest `test_api.py`, `test_configurable_capital.py` |
| 15 | « Modifier » (`<button>` l.325, @click) | change le mot de passe | `changePassword` | POST /api/auth/password | nouveau mot de passe actif | POST /api/auth/password 401 ; message « Mot de passe actuel incorrect » ; erreur navigateur : Failed to load resource: the server responded with a status of 401 (Un | refusé avec message clair | aucun |
| 16 | « Activer les notifications » (`<button>` l.328, @click) | active ou désactive les notifications du navigateur | `enableNotif` | — | permission du navigateur | message « Notifications refusées par le navigateur » ; affichage modifié | — | aucun |
| 17 | « Désactiver » (`<button>` l.329, @click) | active ou désactive les notifications du navigateur | `disableNotif` | — | permission du navigateur | non cliqué : exige des notifications actives | — | aucun |
| 18 | « Activer la double authentification » (`<button>` l.334, @click) | prépare la double authentification | `startTotp` | POST /api/auth/2fa/setup | QR code et clé affichés | POST /api/auth/2fa/setup 200 | — | pytest `test_api.py` |
| 19 | « Valider » (`<button>` l.340, @click) | active la double authentification | `enableTotp` | POST /api/auth/2fa/enable | code exigé aux connexions suivantes | POST /api/auth/2fa/enable 422 ; message « String should have at least 6 characters » ; erreur navigateur : Failed to load resource: the server responded with a status of 422 (Un | D-UI1 : code trop court → message brut en anglais (« String should have at least 6 characters ») ; même cause pour toute erreur 422 | pytest `test_api.py` |
| 20 | « Désactiver » (`<button>` l.346, @click) | désactive la double authentification | `disableTotp` | POST /api/auth/2fa/disable | plus de code exigé | non cliqué : exige la double authentification active | — | pytest `test_api.py` |

## Journal (`web/js/views/logs.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « Tout » (`<button>` l.22, @click) | change l'onglet, le filtre ou la sélection affichée | `level=''` | — | affichage local, sans requête d'écriture | aucun effet visible | — | aucun |
| 2 | « ⟨ v[0] ⟩ » (`<button>` l.22, @click) | change l'onglet, le filtre ou la sélection affichée | `level=k` | — | affichage local, sans requête d'écriture | libellé dynamique : instances cliquées listées en fin de document | — | aucun |

## Contexte (composant) (`web/js/views/context.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « ↻ » (`<button>` l.23, @click) | rafraîchit le contexte de marché | `refresh` | POST /api/context/refresh | indicateurs de contexte | POST /api/context/refresh 200 | — | aucun |

## Banc (composant) (`web/js/views/learning.js`)

| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |
|---|---|---|---|---|---|---|---|---|
| 1 | « 🧪 Tester les capacités d'apprentissage » (`<button>` l.51, @click) | lance le banc des capacités d'apprentissage | `start` | POST /api/ai/bench | rapport du banc | POST /api/ai/bench 200 ; message « Test lancé : environ deux minutes » | — | e2e `ui_check.js`; pytest `test_learning.py` |

## Instances constatées sans ligne statique distincte

Actions à libellé dynamique (symboles, périodes, onglets, liens de navigation), cliquées par l'audit dynamique :

| Page | Action cliquée | Résultat constaté |
|---|---|---|
| dashboard | 1 j | 1 lecture(s) GET |
| dashboard | 7 j | 2 lecture(s) GET ; affichage modifié |
| dashboard | 30 j | 1 lecture(s) GET |
| dashboard | 90 j | 1 lecture(s) GET |
| performance | Aujourd’hui | 1 lecture(s) GET ; affichage modifié |
| performance | 7 jours | 1 lecture(s) GET ; affichage modifié |
| performance | 30 jours | 1 lecture(s) GET ; affichage modifié |
| performance | 90 jours | 2 lecture(s) GET ; affichage modifié |
| performance | Depuis le lancement | 1 lecture(s) GET ; affichage modifié |
| trade | BTC | aucun effet visible |
| trade | ETH | 2 lecture(s) GET ; affichage modifié |
| trade | ETH | affichage modifié |
| trade | SOL | 4 lecture(s) GET ; affichage modifié |
| trade | Acheter SOL | désactivé dans l'état de démonstration |
| trade | SOL | aucun effet visible |
| trade | BNB | 2 lecture(s) GET ; affichage modifié |
| trade | Acheter BNB | désactivé dans l'état de démonstration |
| trade | BNB | 2 lecture(s) GET |
| trade | XRP | 2 lecture(s) GET ; affichage modifié |
| trade | Acheter XRP | désactivé dans l'état de démonstration |
| trade | XRP | aucun effet visible |
| trade | DOGE | 3 lecture(s) GET ; affichage modifié |
| trade | Acheter DOGE | désactivé dans l'état de démonstration |
| trade | DOGE | 1 lecture(s) GET |
| trade | ADA | 3 lecture(s) GET ; affichage modifié |
| trade | Acheter ADA | désactivé dans l'état de démonstration |
| trade | ADA | aucun effet visible |
| trade | LINK | 3 lecture(s) GET ; affichage modifié |
| trade | Acheter LINK | désactivé dans l'état de démonstration |
| trade | LINK | 1 lecture(s) GET |
| trade | select Portefeuille principal | aucun effet visible |
| trade | 1m | 1 lecture(s) GET ; affichage modifié |
| trade | 5m | 3 lecture(s) GET ; affichage modifié |
| trade | 15m | 1 lecture(s) GET ; affichage modifié |
| trade | 1h | 1 lecture(s) GET ; affichage modifié |
| trade | 4h | 3 lecture(s) GET ; affichage modifié |
| trade | 1d | 1 lecture(s) GET ; affichage modifié |
| trade | Acheter | 1 lecture(s) GET |
| trade | Vendre LINK | désactivé dans l'état de démonstration |
| trade | EUR | 1 lecture(s) GET ; affichage modifié |
| trade | 10 % | affichage modifié |
| trade | 25 % | 1 lecture(s) GET |
| trade | 50 % | 1 lecture(s) GET |
| trade | 100 % | aucun effet visible |
| ai | select 1 an 2 ans 3 ans 4 ans 6 ans 8 ans (historique étendu | aucun effet visible |
| ai | select Avec le modèle actif Tous les signaux (sans filtre IA | affichage modifié |
| ai | select 30 jours 90 jours 6 mois 1 an 2 ans | aucun effet visible |
| history | select Portefeuille principal | aucun effet visible |
| history | select Toutes les années | aucun effet visible |
| memecoin | Live | 3 lecture(s) GET |
| memecoin | Positions | affichage modifié |
| memecoin | Radar | affichage modifié |
| memecoin | Stratégies | affichage modifié |
| dmh | Marchés | 2 lecture(s) GET ; affichage modifié |
| dmh | Stratégie | affichage modifié |
| dmh | Prévisualiser (aucune exécution) | message « Indiquez l'avantage brut attendu (hypothèse) : sans lui, l'opération n » |
| dmh | input input | aucun effet visible |
| dmh | input input | aucun effet visible |
| dmh | input input | aucun effet visible |
| dmh | input input | aucun effet visible |
| dmh | select BTC ETH SOL DOGE BONK WIF PEPE | aucun effet visible |
| dmh | Prévisualisation | affichage modifié |
| dmh | Simulateur | 2 lecture(s) GET ; affichage modifié |
| dmh | Observatoire | 1 lecture(s) GET ; affichage modifié |
| dmh | Limites | affichage modifié |
| signals | input input | PUT /api/settings 200 |
| settings | Général | aucun effet visible |
| settings | Risque | 1 lecture(s) GET ; affichage modifié |
| settings | IA | affichage modifié |
| settings | input input | 3 lecture(s) GET |
| settings | select Mixte : arbres simples + modèle linéaire (recommandé) | aucun effet visible |
| settings | input input | aucun effet visible |
| settings | input input | aucun effet visible |
| settings | input input | 1 lecture(s) GET |
| settings | input input | aucun effet visible |
| settings | input input | aucun effet visible |
| settings | select 15 minutes 1 heure 4 heures (recommandé) 1 jour (peu  | aucun effet visible |
| settings | Données | 1 lecture(s) GET ; affichage modifié |
| settings | Calendrier macro | affichage modifié |
| settings | Bouclier | affichage modifié |
| settings | Portefeuille | affichage modifié |
| settings | Simulation | 1 lecture(s) GET ; affichage modifié |
| settings | Verrou et fiabilité | affichage modifié |
| settings | Telegram | affichage modifié |
| settings | select Tout (y compris l’entraînement) Important seulement C | aucun effet visible |
| settings | Actualités | affichage modifié |
| settings | Surveillance | affichage modifié |
| settings | Comptes | 1 lecture(s) GET ; affichage modifié |
| settings | Sécurité | affichage modifié |
| settings | À propos | 1 lecture(s) GET ; affichage modifié |
| logs | Info | 2 lecture(s) GET ; affichage modifié |
| logs | Attention | 1 lecture(s) GET ; affichage modifié |
| logs | Erreur | 1 lecture(s) GET ; affichage modifié |
| logs | Critique | 1 lecture(s) GET |
| nav | Lien #/dashboard | aucun effet visible ; page atteinte |
| nav | Lien #/performance | 1 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/trade | 4 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/ai | 7 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/history | 5 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/knowledge | 2 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/apprentissage | 2 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/macro | 1 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/preparation | 2 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/memecoin | 4 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/dmh | 5 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/news | 2 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/signals | 2 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/settings | 4 lecture(s) GET ; affichage modifié ; page atteinte |
| nav | Lien #/logs | 1 lecture(s) GET ; affichage modifié ; page atteinte |
| mobile | Barre basse #/dashboard | affichage modifié ; page atteinte |
| mobile | Barre basse #/performance | affichage modifié ; page atteinte |
| mobile | Barre basse #/trade | affichage modifié ; page atteinte |
| mobile | Barre basse #/ai | affichage modifié ; page atteinte |
| mobile | Largeur de page à 390 px | aucun effet visible |
