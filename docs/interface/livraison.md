# Interface web et application Windows V1 : livraison

Chantier défini dans `SPEC.md` (« Extension du Sprint 5 », cadrage #116). Simulation exclusive : aucun ordre réel, aucune
clé privée, aucun levier, aucun short. Le moteur, le Guardian, le Risk Commander, la causalité des transactions fictives et
les archives gelées #54/#55 sont inchangés. La campagne prospective de 72 heures n'a pas été lancée.

## 1. État en une phrase

L'interface web est livrée et vérifiée automatiquement. L'application Windows est **lançable** (`bureau.bat`), mais
**non validée sur Windows 11** : aucun test n'a pu afficher la vraie fenêtre sous Windows. Elle ne doit pas être
considérée comme opérationnelle avant la vérification de la section 4 sur votre PC.

## 2. Ce qui a été livré

| PR | Contenu |
|---|---|
| #116 | Cadrage du chantier dans `SPEC.md` : périmètre, responsabilités, critères de validation, séquence |
| #117 | Inventaire des 118 actions du cockpit et audit dynamique reproductible (`docs/interface/inventaire-actions.md`) |
| #118 | Application de bureau Windows légère : `bureau.bat`, raccourci du Bureau, fenêtre dédiée, serveur local arrêté avec elle |
| #119 | Erreurs de validation en français ; boutons Signaux inactifs quand il n'y a rien à envoyer (D-UI1 à D-UI3) |
| #120 | Tests Pytest des 10 routes appelées par des boutons et jusqu'ici sans test |
| #121 | Thème qui suit le système puis le choix enregistré ; menu latéral en 5 rubriques (D-UI4) |
| #122 | Session expirée expliquée, serveur injoignable signalé, commande non dupliquée au double clic (D-UI5 à D-UI7) |
| #123 | Audit final, inventaire régénéré, présent document, section « Application de bureau » de `docs/WINDOWS.md` |
| #124 | Fiabilisation : fenêtres maintenues au double clic, refus de réglage lisible, arrêt d'urgence d'une IA en préparation (D-UI8 à D-UI10) ; parcours des actions critiques |
| #125 | Fiabilisation : parcours des actions secondaires, inventaire régénéré, mise à jour du présent document |

L'interface reste en Vue 3, FastAPI et Lightweight Charts, sans étape de construction. Aucune règle métier n'a été
ajoutée dans le navigateur : chaque page affiche ce que l'API locale calcule.

## 3. Interface web

**Navigation.** Le menu latéral regroupe les 15 pages en 5 rubriques : Pilotage (Tableau de bord, Performances,
Historique, Journal), Trading fictif (Trader, Memecoin, Hub de marché), Modèles IA (IA, Apprentissage, Préparation),
Informations (Fiches cryptos, Contexte macro, Actualités, Signaux) et Configuration (Réglages). Sur téléphone, la barre du
bas garde Tableau de bord, Performances, Trader et IA ; « Plus » donne les 11 autres pages.

**Thème.** Sans choix enregistré, le cockpit suit le thème clair ou sombre de l'ordinateur ou du téléphone. Le bouton ☀/☾
enregistre le choix, conservé après rechargement. Listes, barres de défilement et barre du navigateur mobile suivent le
thème actif.

**États affichés.**
- Chargement : « Chargement… » dans les pages qui attendent des données.
- Vide : message propre à la page (« Aucune donnée. », « Aucune position ouverte. », « Aucune décision enregistrée. »…).
- Erreur : message en français ; une erreur de saisie nomme le champ (« « code » : au moins 6 caractères »).
- Déconnecté : indicateur « Hors ligne » ; si le serveur ne répond plus, un bandeau « Serveur injoignable » remplace
  l'impression d'un état vide. Au retour, le bandeau disparaît, le temps réel se reconnecte et la page se recharge.
- Session expirée : l'écran de connexion l'explique ; la reconnexion ramène sur la même page.
- Simulation : le badge « Simulation » est présent sur les 15 pages ; les montants sont fictifs.

**Commandes.** Une même commande renvoyée pendant que la première est en cours (double clic) n'est envoyée qu'une fois ; un
message identique déjà affiché est prolongé, pas répété.

## 4. Application Windows

Mode d'emploi, fonctionnement et diagnostic : `docs/WINDOWS.md`, section 7. En bref :
1. Double-cliquez sur `bureau.bat` (dossier de TRADER AI). Au premier lancement, il prépare Python, installe la fenêtre
   légère pywebview 5.4 (facultative) et crée le raccourci « Sentinelle » sur le Bureau.
2. Le serveur n'écoute que sur 127.0.0.1 ; `--remote` est refusé. Fermer la fenêtre arrête Sentinelle.
3. Journal : `data\logs\sentinelle.log`.

**À vérifier sur votre PC avant de la déclarer opérationnelle :**
1. La fenêtre « Sentinelle » s'ouvre sur l'écran de connexion ; le raccourci apparaît sur le Bureau.
2. Fermer la fenêtre ferme la console, et `http://127.0.0.1:8765` ne répond plus.
3. Sans pywebview, la fenêtre Edge en mode application s'ouvre ; à sa fermeture, le serveur s'arrête et aucun processus
   Edge du profil `fenetre` ne reste dans le Gestionnaire des tâches (commandes : `docs/WINDOWS.md`, section 7).
4. La fenêtre suit le thème clair ou sombre de Windows.

## 5. Fonctionnalités réellement opérationnelles

« Vérifié » signifie qu'un test automatisé contrôle l'effet sur le système (état, base, requête reçue), pas seulement une
réponse HTTP 200.

| Domaine | Fonction | Preuve |
|---|---|---|
| Accès | Premier mot de passe, connexion, déconnexion, protection CSRF | `test_api.py`, `actions_audit.js` |
| Accès | Session expirée expliquée, retour sur la même page | `robustness_check.js` |
| Accès | Changement de mot de passe : autres sessions fermées, ancien mot de passe refusé | `test_cockpit_routes.py` |
| Accès | Double authentification : code refusé expliqué en français | `feedback_check.js`, `test_api.py` |
| Navigation | 15 pages en 5 rubriques, barre du bas et « Plus » sur téléphone, sans débordement à 390 px | `design_check.js` |
| Affichage | Thème du système, puis choix conservé | `design_check.js` |
| Sécurité | Arrêt d'urgence : pause des IA ; revente fictive refusée sans la saisie de URGENCE | `actions_audit.js`, `test_api.py`, `test_lockdown.py` |
| Trader | Graphique par crypto et unité de temps ; bougies validées et triées | `actions_audit.js`, `test_cockpit_routes.py` |
| Trader | Ordre fictif (aperçu puis exécution), modification du stop, fermeture ; ordre impossible refusé avec un message lisible | `test_api.py` |
| Performances | Capital fictif de 30 € et résultat nul tant qu'aucune opération n'a eu lieu ; aucun résultat inventé | `champion_check.js`, `performance_check.js` |
| IA | Démarrage et arrêt de l'IA d'entraînement, sans doublon au double clic ; IA en argent réel refusée | `robustness_check.js`, `test_api.py` |
| IA | Entraînement : un seul à la fois, verdict affiché, aucun modèle inventé | `train_check.js`, `test_cockpit_routes.py` |
| Apprentissage | Aucune comparaison ni aucun résultat inventés ; journal des décisions filtrable | `champion_check.js` |
| Memecoin | Démarrage, mise à jour en direct (courbe, opérations, décisions de la flotte), pause et cycle manuel, sur des sources simulées | `memecoin_check.js`, Pytest du worker |
| Hub de marché | Bandeau « SIMULATION — ARGENT FICTIF » ; prévisualisation refusée sans avantage annoncé ; plafonds, capital, frais et reliquats affichés | `dmh_check.js`, `test_dmh_api.py` |
| Informations | Fiches cryptos mises à jour | `test_cockpit_routes.py` |
| Informations | Contexte de marché : source indisponible signalée, aucune valeur inventée | `macro_check.js`, `test_cockpit_routes.py` |
| Informations | « Analyser maintenant » relance la collecte des actualités | `test_cockpit_routes.py` |
| Informations | Signaux : canal enregistré, évaluation des signaux échus | `feedback_check.js`, `test_cockpit_routes.py` |
| Réglages | Liste suivie conservée après rechargement et après redémarrage du serveur | `robustness_check.js` |
| Réglages | Message de test Telegram refusé avec une raison si Telegram n'est pas configuré | `test_cockpit_routes.py` |
| Robustesse | Serveur arrêté puis relancé : bandeau, reconnexion, session conservée | `robustness_check.js` |
| Bureau | Serveur sur 127.0.0.1 seulement, arrêté à la fermeture de la fenêtre ; démarrage complet en contrôle | `test_desktop.py` (Linux et Windows en CI) |
| Trader | Ticket : 25 %, stop −2 %, objectif +4 % ; un seul ordre au double clic ; stop et objectif enregistrés | `critical_check.js` |
| Trader | « Modifier » puis « Enregistrer » change le stop ; « Annuler » ne change rien | `critical_check.js` |
| Trader | « Fermer » demande confirmation ; « Annuler » ne ferme rien ; une seule fermeture au double clic | `critical_check.js` |
| Trader | Ordre à cours limité puis « Annuler » : ordre et position en attente supprimés | `critical_check.js` |
| Trader | Ordre refusé par le serveur : message affiché, aucune position, bouton de nouveau disponible | `critical_check.js` |
| Trader | Crypto et unité de temps (bougies relues), montant en devise ou en quantité, côtés Acheter et Vendre | `secondary_check.js` |
| Sécurité | Arrêt d'urgence : fenêtre maintenue au double clic ; « Fermer » n'envoie rien ; portée « Positions de l'IA » transmise ; « Arrêter les IA » arrête aussi une IA en préparation | `critical_check.js`, `secondary_check.js`, `test_cockpit_routes.py` |
| Accès | Double authentification de bout en bout : activation avec un vrai code, connexion qui l'exige, mauvais code refusé, désactivation | `secondary_check.js` |
| Réglages | Valeur hors bornes ou champ vide refusés avec le libellé et l'unité du champ, rien d'enregistré ; valeur valide conservée après rechargement | `critical_check.js` |
| Robustesse | WebSocket fermé par le serveur : « Hors ligne », puis reconnexion automatique | `critical_check.js` |
| Memecoin | Démarrage refusé par le serveur (réponse simulée) : message affiché, paramètres envoyés exacts ; montants prédéfinis | `critical_check.js`, `secondary_check.js` |
| IA | « Rapport », « Activer » (refusé pour un modèle non validé, rien ne change) et « Retirer » | `test_cockpit_routes.py` |
| Affichage | 48 onglets, périodes et filtres (IA, Performances, Tableau de bord, Historique, Journal, Réglages, Memecoin, Hub) ; détail d'une vente dans l'Historique | `secondary_check.js` |
| Réglages | Canal de signaux retiré des réglages enregistrés ; notifications du navigateur activées puis désactivées | `secondary_check.js` |

## 6. Inventaire des boutons

`docs/interface/inventaire-actions.md` donne, pour chacune des 118 actions, les huit rubriques demandées : libellé et
emplacement, effet attendu, gestionnaire, route FastAPI, changement attendu, résultat constaté, erreurs et limites, test.

| Bilan | Premier audit (#117) | Audit final (#123) | Fiabilisation (#124 et cette PR) |
|---|---|---|---|
| Actions inventoriées | 118 | 118 | 118 |
| Effet constaté par l'audit dynamique | 57 | 55 | 54 |
| Non cliquées par l'audit dynamique (raison indiquée) | 38 | 38 | 39, dont 37 vérifiées par un parcours ciblé |
| Défauts ouverts | 3 | 0 (7 reproduits, tous corrigés) | 0 (10 reproduits, tous corrigés) |
| Sans test automatisé associé | 66 | 54 | 4 |

Depuis la fiabilisation, les relectures automatiques de la vue d'ensemble (toutes les 5 s) ne sont plus prises pour l'effet d'un
clic : « Effet constaté » passe de 55 à 54 pour cette raison. Les deux fonds de fenêtre partagent désormais le même
gestionnaire ; l'audit dynamique ne les clique pas (39 au lieu de 38), `critical_check.js` les vérifie. Les actions à
libellé dynamique vérifiées par un parcours sont déclarées une à une dans `tests/e2e/actions_inventory.py` (`VERIFIED`).

**Les 4 actions sans test, et pourquoi :**
- choix d'un compte au Tableau de bord : l'instance de démonstration n'en a qu'un ;
- détail d'une ligne de la page Apprentissage : il faut des comparaisons enregistrées ;
- « Reprendre dans le formulaire » (Hub) : il faut un brouillon de l'assistant local ;
- case « Lire les canaux » (Signaux) : il faut un vrai compte Telegram.

« Effet constaté » baisse de 2 : « Au marché » et « Tous » sont déjà sélectionnés par défaut, et leur clic ne change rien à
l'écran. Lors du premier audit, un rafraîchissement automatique avait coïncidé avec le clic. « Enregistrer » (Signaux)
est désormais inactif à vide, ce qui est l'effet de la correction D-UI3. À l'inverse, un rafraîchissement a coïncidé avec
« Tout ». La correspondance avec les tests se fait par route et par libellé. Elle donne un ordre de grandeur, pas une
preuve action par action.

L'inventaire se régénère :

```powershell
.venv\Scripts\python.exe tests\e2e\run.py actions_audit.js
.venv\Scripts\python.exe tests\e2e\actions_inventory.py <dossier des captures>\actions.json docs\interface\inventaire-actions.md <révision>
```

Aucun bouton n'a été trouvé simulant un succès. Les fonctions qui exigent un compte extérieur (Telegram, canaux de
signaux) ou une IA locale (Ollama) refusent avec une raison quand elles ne sont pas configurées.

## 7. Défauts corrigés

Chaque parcours cité échouait avant la correction et réussit après.

| Défaut | Constat avant correction | Correction | Parcours |
|---|---|---|---|
| D-UI1 | Code de double authentification trop court : message brut en anglais ; même cause pour toute erreur 422 | #119 | `feedback_check.js` |
| D-UI2 | « Ajouter » (Signaux) actif sur champ vide, sans requête ni message | #119 | `feedback_check.js` |
| D-UI3 | « Enregistrer » (Signaux) actif sur champs vides, sans requête ni message | #119 | `feedback_check.js` |
| D-UI4 | Système en clair : cockpit en sombre ; contrôles natifs et barre du navigateur restés sombres en thème clair | #121 | `design_check.js` |
| D-UI5 | Session expirée : 4 messages « Connexion requise » empilés, écran de connexion sans explication | #122 | `robustness_check.js` |
| D-UI6 | Serveur injoignable présenté comme un état vide (« Aucune donnée. ») | #122 | `robustness_check.js` |
| D-UI7 | Double clic : commande envoyée deux fois, message répété (le serveur ignorait déjà le doublon) | #122 | `robustness_check.js` |
| D-UI8 | Double clic sur « Urgence », « Fermer » (position) ou « Réinitialiser » : la fenêtre qui venait de s'ouvrir se refermait aussitôt, sans rien faire ni rien dire | #124 | `critical_check.js` |
| D-UI9 | Réglage refusé : clé interne et fractions brutes (« risk.risk_per_trade doit être compris entre 0.001 et 0.02 » pour un champ en %) | #124 | `critical_check.js` |
| D-UI10 | « Arrêter les IA » pendant la préparation de l'IA d'entraînement : « 0 IA arrêtée », puis l'IA démarrait (correction limitée à l'arrêt d'urgence, dans `sentinelle/services/agents.py`) | #124 | `critical_check.js`, `test_cockpit_routes.py` |

## 8. Tests automatisés

En CI, sur chaque PR vers main :
- `pytest (ubuntu-latest)` et `pytest (windows-latest)` : suite complète, dont `test_desktop.py`,
  `test_cockpit_routes.py` et `test_active_time_journal.py` (cohérence du présent journal de temps, hors budget moteur) ;
- `Vue cockpit (Chromium)` : `memecoin_check.js`, `feedback_check.js`, `design_check.js`, `critical_check.js`,
  `secondary_check.js`, puis `robustness_check.js`.

Le parcours de robustesse démarre sa propre instance jetable, l'arrête et la relance. Les autres parcours utilisent
`tests/e2e/run.py`, qui démarre une instance de démonstration jetable par parcours.

## 9. Limites restantes et tests à faire

- **Windows 11 :** la vraie fenêtre (pywebview ou Edge) n'a jamais été affichée par un test ; vérification de la
  section 4 à faire sur votre PC.
- **Pas d'installateur `.exe` :** le lancement passe par `bureau.bat` et le raccourci du Bureau.
- **Navigateurs :** seul Chromium est testé (bureau en 1280 à 1440 px, téléphone émulé en 390 px). Ni Firefox, ni
  Safari, ni un vrai téléphone.
- **Actions non cliquées par l'audit dynamique :** 39 sur 118, raison donnée pour chacune ; 37 sont vérifiées par un
  parcours ciblé. Restent sans test : les 4 actions de la section 6.
- **Services extérieurs :** Telegram, Ollama et les sources publiques (Memecoin, contexte de marché, actualités) ne sont
  testés qu'avec des réponses simulées ; leur fonctionnement avec les vrais services n'est pas validé.
- **Actualités :** l'analyse exige Ollama sur le PC ; elle n'est pas testée en CI, seule la collecte l'est.
- **Retour de connexion :** la page affichée est rechargée ; une saisie non enregistrée sur cette page est perdue.
- **Commande identique pendant l'envoi :** elle n'est envoyée qu'une fois, par construction. Une commande renvoyée après
  la réponse part normalement.
- **Arrêt d'urgence :** deux messages pour une revente (celui de l'action et l'alerte critique du serveur) : voulu.
- **Accessibilité :** contrastes et lecteur d'écran non audités formellement.
- **Inventaire :** les lectures en arrière-plan (rafraîchissement toutes les 5 s) peuvent coïncider avec un clic et
  apparaître dans « Résultat constaté » ; la correspondance avec les tests se fait par route et par libellé.

## 10. Vérifier sous Windows 11

```powershell
.venv\Scripts\python.exe -m pytest tests\test_desktop.py tests\test_cockpit_routes.py -v
.venv\Scripts\python.exe tests\e2e\run.py feedback_check.js design_check.js
$env:PYTHON = ".venv\Scripts\python.exe"; node tests\e2e\robustness_check.js
.venv\Scripts\python.exe tests\e2e\run.py critical_check.js secondary_check.js
.\bureau.bat --demo --data $env:TEMP\sentinelle-demo
```

Les commandes navigateur exigent Node.js et Playwright (`npx playwright install chromium`).

## 11. Temps du chantier

Consigné dans `docs/interface/active-time.jsonl`, hors budget des 24 heures actives du moteur : 8623 s (2 h 24 min) sur 8 entrées, attentes de CI et d'exécutions locales exclues.
