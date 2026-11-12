# Guide d'utilisation de Sentinelle

> **À lire d'abord.** Sentinelle ne garantit aucun gain. Aucun logiciel ne le peut.
> Le trading comporte un risque de perte en capital, et les résultats passés (réels ou simulés) ne garantissent pas les résultats futurs.
> Sentinelle est conçu pour **protéger** votre argent : argent fictif uniquement, limites de perte automatiques, et aucun ordre réel possible dans cette version (voir [SECURITE.md](SECURITE.md)).

---

## 1. Installation sur Windows

### Étape 1 : récupérer Sentinelle
- Sur la page GitHub du projet : **Code → Download ZIP**, puis extrayez le dossier, par exemple dans `C:\Sentinelle`.
- Ou, si vous utilisez Git : `git clone` du dépôt.

### Étape 2 : installer Python (une seule fois)
1. Allez sur <https://www.python.org/downloads/> et téléchargez **Python 3.12**.
2. Pendant l'installation, **cochez « Add python.exe to PATH »**.

Autre possibilité : ouvrez un terminal et tapez `winget install Python.Python.3.12`.

### Étape 3 : lancer
- Double-cliquez sur **`lancer.bat`**.
- Au premier lancement, Sentinelle installe ses composants (2 à 5 minutes, connexion internet requise).
- Le panel s'ouvre ensuite dans votre navigateur, à l'adresse **http://127.0.0.1:8765**.
- **Choisissez votre mot de passe** (10 caractères minimum). Il protège le panel.

### Raccourcis utiles

| Fichier | Rôle |
|---------|------|
| `lancer.bat` | Démarre Sentinelle. Pour l'arrêter, fermez la fenêtre noire. |
| `demo.bat` | Démo **hors-ligne** sur un marché simulé, pour découvrir sans connexion. |
| `diagnostic.bat` | Vérifie l'installation : Python, internet, données de marché, Ollama, carte graphique. |

---

## 2. Tour du panel

| Page | Ce que vous y faites |
|------|---------------------|
| **Tableau de bord** | Capital de chaque compte, état des IA, bulletin de fiabilité, courbe de capital, décisions de l'IA en direct. |
| **Performances** | Ce que le portefeuille a réellement rapporté, net de tous les frais : capital, résultat réalisé et latent, rendement par trade et par jour, baisse maximale, résultats par stratégie, signaux refusés et leurs raisons. |
| **Trader** | Graphique en temps réel, passage d'ordres, calculatrice de risque, avis de l'IA, positions et ordres. |
| **IA** | Entraînement en direct, bulletin de fiabilité, apprentissage sur l'historique, simulation accélérée, journal des décisions. |
| **Historique** | Tous vos trades, leurs statistiques et l'export CSV (utile pour les impôts). |
| **Actualités** | Fil d'actualités crypto analysé par l'IA locale. |
| **Réglages** | Risque, IA, verrou et fiabilité, Telegram, actualités, comptes, mot de passe. |
| **Journal** | Tout ce qui se passe dans le système. |

En haut à droite, le bouton rouge **🛑 Urgence** propose deux niveaux :
- **Arrêter les IA** : les positions ouvertes restent protégées par leurs stops.
- **Tout revendre** : vente immédiate au prix du marché, après avoir tapé « URGENCE ».

---

## 3. Trader vous-même

1. Page **Trader** : le **portefeuille principal** (30 € fictifs, partagé avec l'IA) utilise de l'argent fictif sur les prix réels. Indiquez le montant **en euros** : l'aperçu affiche le montant en USDC, les frais, le coût de conversion, le total débité et le taux de change appliqué. La plateforme refuse les ordres de moins de 5 USDC (voir [PORTEFEUILLE.md](PORTEFEUILLE.md)).
2. Choisissez la crypto et l'unité de temps du graphique.
3. Remplissez le ticket :
   - Cochez **« Calculer la quantité selon le risque »** et indiquez par exemple 0,5 %.
   - Placez un **stop-loss** (boutons −1 %, −2 %… ou un prix précis).
   - Ajoutez éventuellement un **objectif de gain**.
   - L'aperçu affiche la quantité, les frais estimés et la **perte maximale** si le stop est touché.
4. Le stop et l'objectif apparaissent sur le graphique. Vous pouvez les modifier ou fermer la position à tout moment.

> Tout se passe en argent fictif : vous pouvez vous entraîner sans aucun risque.

---

## 4. L'IA : le parcours recommandé

### a) Apprentissage sur l'historique
Page **IA → Apprentissage sur l'historique → Lancer l'entraînement**. Comptez une à quelques minutes.
1. Sentinelle télécharge l'historique des prix.
2. Il repère toutes les situations où les stratégies auraient acheté et calcule ce que chacune aurait rapporté, frais compris.
3. L'IA apprend lesquelles valent la peine. Elle est ensuite testée sur une **période qu'elle n'a jamais vue** : c'est le seul test honnête.
4. **Verdict :**
   - **validé** : gain moyen positif sur la période jamais vue, avec **95 % de confiance** que ce n'est pas dû au hasard. Le modèle devient actif.
   - **non validé** : l'IA n'a pas prouvé d'avantage. Elle observe, ou utilise un modèle « à l'essai » avec une taille réduite. **C'est une protection, pas un bug.**
     - Si le modèle est prometteur sans être prouvé, il est mis **« à l'essai »** : l'IA d'entraînement s'en sert en argent fictif, avec une taille réduite de moitié, pour le juger sur des données vraiment nouvelles.
     - Sinon, l'IA reste en « exploration » : elle prend les signaux de base, avec une taille réduite.

### a bis) Vérifier que l'IA sait apprendre
Page **IA → Capacités d'apprentissage → Tester les capacités d'apprentissage** (environ deux minutes, sans argent ni données de marché).
Sentinelle fabrique des marchés artificiels où **une règle est cachée exprès**, puis mesure quelle part de cette règle l'IA retrouve sur des données jamais vues :
- **taux d'apprentissage** : 100 % signifie que toute la règle a été retrouvée ;
- **bruit pur** : aucune règle n'existe, l'IA ne doit rien « découvrir » ;
- **changement de règle** : la règle s'inverse en cours de route. L'IA ne s'adapte pas instantanément, mais elle refuse de se valider tant qu'elle n'a pas réappris.

Le même onglet montre **ce que l'IA apprend en direct** : ce qu'elle annonçait (chances de gain) comparé à ce qui s'est réellement passé, et la date du prochain ré-entraînement.

### b) Entraînement en direct (argent fictif, prix réels)
Page **IA → Entraînement en direct → Démarrer**. L'IA surveille le marché 24 h/24 :
- à chaque clôture de bougie, elle examine les signaux, demande l'avis du modèle et soumet chaque trade au gestionnaire de risque ;
- chaque décision (prise **ou refusée**) est expliquée dans le journal ;
- chaque signal est suivi jusqu'à son issue, ce qui permet de vérifier si l'IA a eu raison de refuser ;
- elle se ré-entraîne régulièrement avec les données récentes. Un nouveau modèle n'est activé que s'il est validé.

### c) Bulletin de fiabilité
Il indique si l'IA a fait ses preuves **en argent fictif**. Il est au vert quand **toutes** ces conditions sont remplies (seuils réglables dans **Réglages → Verrou et fiabilité**) :
- un modèle validé ;
- au moins 30 jours d'entraînement en direct ;
- au moins 25 trades avec le modèle ;
- un résultat net positif ;
- une baisse maximale inférieure à 10 % ;
- aucune erreur critique sur 7 jours.

### d) Et l'argent réel ?
Cette version ne le permet pas. Un bulletin au vert ne déverrouille rien : il n'existe ni bouton, ni réglage, ni phrase de confirmation pour passer en réel. Le détail du verrou est dans [SECURITE.md](SECURITE.md).

---

## 5. Comprendre les chiffres

| Indicateur | Signification |
|------------|---------------|
| **R** | Multiple du risque pris. Si vous risquez 50 € (distance jusqu'au stop), +1 R = +50 € et −1 R = −50 €. |
| **Résultat moyen (R)** | Le chiffre le plus important. Il doit être **positif sur beaucoup de trades**, frais compris. |
| **Taux de réussite** | Ne veut rien dire seul. On peut gagner 95 % des trades et perdre de l'argent (gros stops, petits gains). |
| **Intervalle 95 %** | Marge d'incertitude. S'il contient des valeurs négatives, le résultat n'est pas prouvé. |
| **Profit factor** | Gains totaux ÷ pertes totales. Au-dessus de 1, on gagne. |
| **Acheter et garder** | Référence : ce qu'aurait rapporté le simple fait d'acheter et de garder. Battre cette référence est difficile. |

---

## 5 bis. Lire la page « Performances »

Tout y est **net** : frais de la plateforme, glissement de prix et conversion en euros sont déjà déduits.

| Élément | Ce qu'il dit |
|---------|--------------|
| **Capital de départ / actuel / disponible / investi** | D'où l'on part, où l'on en est, ce qui est libre et ce qui est placé en cryptos. |
| **Résultat net** | Capital actuel moins capital de départ. Il se décompose en **réalisé** (trades clôturés) et **latent** (positions encore ouvertes, au dernier prix). Le badge « comptes vérifiés » confirme que la somme tombe juste. |
| **Par période** | Aujourd'hui, 7, 30, 90 jours et depuis le lancement. Le lancement est la dernière réinitialisation du portefeuille. |
| **Rendement net par trade** | Résultat du trade divisé par le montant engagé. La **contribution** le rapporte au portefeuille entier. |
| **Rendement de chaque jour** | Une valeur par jour, **y compris les jours sans aucun trade** (0 %). Les jours où Sentinelle était éteint sont signalés. |
| **Rendement quotidien géométrique** | Le rendement constant par jour qui aurait donné le même capital. Il n'est jamais « annualisé ». |
| **Baisse maximale** | Plus forte baisse depuis un plus haut. |
| **Exposition maximale** | Plus grande part du capital placée en même temps. |
| **Versions successives du modèle** | Les trades de l'IA regroupés par version du modèle : utile seulement avec assez de trades par version. |
| **Signaux refusés** | Combien de signaux l'IA a refusés, pourquoi, et **ce qu'ils auraient donné**. C'est ainsi qu'on vérifie si elle a eu raison de refuser. |

Deux avertissements s'affichent tout seuls :
- **moins de 30 trades** : les moyennes ne prouvent rien, un bon chiffre peut venir du hasard ;
- **marché simulé** (mode démonstration) : les chiffres ne sont pas une performance.

---

## 6. Simulation uniquement

- Sentinelle ne peut **ni envoyer, ni modifier, ni annuler un ordre réel**. Les cours sont réels, l'argent est fictif.
- **Aucune clé API de plateforme d'échange** n'est demandée, lue ou utilisée. N'en créez pas pour Sentinelle.
- Si vous utilisiez une ancienne version avec de vraies clés :
  - elles restent dans `data/secrets.json` mais ne sont plus jamais lues ;
  - supprimez-les dans **Réglages → Verrou et fiabilité**, et **révoquez-les sur le site de la plateforme** ;
  - vos éventuels ordres et positions réels existent toujours sur la plateforme : Sentinelle ne les gère plus, vérifiez-les là-bas.
- Méfiez-vous des arnaques : personne de sérieux ne vous demandera une clé avec droit de retrait ni de l'argent « pour débloquer des gains ».
- Les gains obtenus en simulation ne garantissent jamais des gains réels.

---

## 7. Telegram (alertes et commandes)

1. Dans Telegram, ouvrez **@BotFather**, envoyez `/newbot` et suivez les étapes. Copiez le **jeton**.
2. Panel → **Réglages → Telegram** : collez le jeton, enregistrez, cochez « Activer » et enregistrez.
3. Écrivez `/start` à votre bot : il vous répond avec **votre identifiant**.
4. Collez cet identifiant dans les réglages et enregistrez. Le bot ne répondra **qu'à vous**.

Commandes disponibles :

| Commande | Effet |
|----------|-------|
| `/etat` | État de Sentinelle et des IA |
| `/positions` | Positions ouvertes |
| `/gains` | Résultats des comptes |
| `/actus` | Dernières actualités importantes |
| `/pause` | Arrête les IA (les positions gardent leurs stops) |
| `/reprendre` | Relance l'IA d'entraînement |
| `/urgence` | Tout revendre, après confirmation par bouton |
| `/fiche BTC` | Ce que Sentinelle sait d'une crypto |
| `/aide` | Liste des commandes |

Aucune commande Telegram ne peut toucher de l'argent réel : cette version fonctionne en simulation uniquement.

---

## 8. Actualités avec l'IA locale (gratuite)

Votre carte graphique (RTX 4060 Ti, 8 Go) fait tourner un modèle d'environ 7 à 8 milliards de paramètres.
1. Installez **Ollama** : <https://ollama.com/download>.
2. Dans un terminal : `ollama pull qwen2.5:7b`. Environ 5 Go à télécharger.
3. Panel → **Actualités → Activer le module**.

Le module joue le rôle de **garde du corps** : une actualité très négative bloque les achats, mais seulement si elle est **confirmée par deux sources**, ou à la fois critique et très sûre. L'IA des actualités ne passe jamais d'ordre elle-même.

Sources lues :
- les médias crypto et les **communiqués officiels** de la Fed, de la BCE et de la SEC (flux RSS, toutes les 10 minutes) ;
- en option, des **canaux Telegram de dépêches** : leurs flashs arrivent en quelques secondes et sont analysés dès réception. Inscrivez-les dans **Réglages → Actualités** (`@nom_du_canal`, un par ligne). Ils utilisent le compte de lecture Telegram de la page **Signaux** (voir 8 bis). Choisissez des canaux d'**information**, pas des canaux de « signaux ».

Une même nouvelle reprise par plusieurs médias n'est analysée qu'**une fois**. Elle ne compte comme « confirmée » que si **deux sources différentes** la rapportent.

La liste des sources étudiées, celles retenues et celles écartées (avec les raisons) est dans [SOURCES.md](SOURCES.md).

---

## 8 bis A. Fiches cryptos

Page **Fiches cryptos**. Pour chaque crypto suivie, Sentinelle rassemble tout ce qu'il sait, calculé sur l'historique complet :
- ce qu'est cette crypto, ce qui fait bouger son prix et ses risques propres ;
- l'ampleur d'une journée normale, les pires chutes, le lien avec le Bitcoin (« quand le Bitcoin perd 5 %, elle perd en moyenne… ») ;
- la liquidité, l'écart achat/vente, le montant minimal d'un ordre ;
- les résultats passés de chaque stratégie sur cette crypto, frais compris, avec une fourchette de confiance.

Les fiches se mettent à jour à chaque entraînement de l'IA, ou avec le bouton « Mettre à jour ». Sur Telegram : `/fiche BTC`.
Elles servent à **comprendre et doser le risque**, pas à choisir les trades : il a été mesuré que le passé d'une crypto ne prédit presque rien de ses résultats futurs.
Ces connaissances aident aussi l'analyse des actualités à reconnaître de quelle crypto parle un article (« Ripple » désigne XRP, « Ether » désigne ETH).

## 8 bis. Canaux de signaux Telegram (observation)

Page **Signaux**.
1. Sur <https://my.telegram.org>, ouvrez « API development tools » et créez une application. Notez **api_id** et **api_hash**.
2. Saisissez-les avec votre numéro, puis cliquez sur « Recevoir le code » et validez le code reçu dans Telegram.
3. Ajoutez les canaux (`@nom` ou lien `t.me/...`) et cochez « Lire les canaux ».

Les 300 derniers messages de chaque canal sont analysés tout de suite. Chaque signal reconnu est évalué sur les vrais prix, frais compris, et vous voyez le **vrai** taux de réussite et le résultat moyen de chaque canal.
Rien n'est jamais tradé automatiquement. La connexion de votre compte reste sur votre PC (`data/telegram_utilisateur.session`) : ne partagez jamais ce fichier.

## 8 ter. Protections supplémentaires

- **Calendrier économique automatique** (Réglages → Calendrier macro) : les annonces à fort impact de la semaine (inflation, emploi, banques centrales…) sont importées toutes les 3 heures. Aucune nouvelle position une heure avant et après. Les annonces à venir, avec les valeurs attendues, s'affichent sur le tableau de bord. Vous pouvez ajouter des dates connues à l'avance (réunions de la Fed), en heure UTC.
- **Bouclier temps réel** (Réglages → Bouclier, carte sur le tableau de bord). Il interdit d'ouvrir une position :
  - pendant un **mouvement brutal** (bougie d'une minute anormale, pause de 30 min) ;
  - quand le **carnet d'ordres** est trop mince ou l'écart achat/vente trop large ;
  - pendant une **cascade de liquidations** sur les marchés à levier (pause d'une heure) ;
  - juste après une **actualité majeure**.

  Il ne vend jamais rien : les positions ouvertes gardent leurs stops. Pour un achat manuel, il affiche seulement un avertissement. Sur 30 jours réels, il n'a bloqué les entrées que 0,1 % à 2 % du temps selon la crypto.
- **Vérification des prix** (Réglages → Données) : le prix est comparé à celui de Kraken avant chaque entrée de l'IA.
- **Surveillance externe** (Réglages → Surveillance) :
  1. créez un contrôle gratuit sur <https://healthchecks.io> (période 5 minutes) et collez son adresse ;
  2. si Sentinelle ou le PC s'arrête, vous êtes prévenu (e-mail, Telegram…).
- **Sauvegardes** : une copie de la base est faite chaque jour dans `data/backups` (14 jours conservés).
- **Double authentification** (Réglages → Sécurité) : scannez le QR code avec Google Authenticator ou Authy.
- **Notifications du navigateur** (Réglages → Sécurité) et **thème clair** (bouton ☀ en haut).

## 9. Laisser tourner 24 h/24

- **Windows → Paramètres → Système → Alimentation** : mise en veille sur « Jamais ».
- **Windows Update** : définissez des « heures d'activité » pour éviter les redémarrages surprises.
- **BIOS de la carte mère** : option « Restore on AC Power Loss » sur « Power On », pour que le PC redémarre seul après une coupure de courant.
- **Démarrage automatique** : dans un terminal ouvert dans le dossier de Sentinelle, tapez `py -3 scripts\demarrage_auto.py --activer`. Pour annuler, remplacez `--activer` par `--desactiver`.
- Après un redémarrage, Sentinelle relance l'IA d'entraînement si elle était en marche. Il compare aussi ses positions à l'exchange (réconciliation) et repose un stop manquant.

---

## 10. Accès depuis le téléphone

1. Installez **Tailscale** (gratuit) sur le PC et sur le téléphone, avec le même compte. Cela crée un réseau privé.
2. Lancez Sentinelle avec `lancer.bat --remote`. Si Windows le demande, autorisez l'accès sur les **réseaux privés** seulement.
3. Sur le téléphone, ouvrez `http://<adresse Tailscale du PC>:8765`.

Ne rendez **jamais** le panel accessible directement depuis internet.

> Le premier mot de passe ne peut être créé que depuis le PC lui-même (sécurité).

---

## 11. Sauvegardes et mises à jour

- Toutes vos données sont dans le dossier **`data/`** : base, historique des prix, modèles, secrets. Sauvegardez-le de temps en temps, avec Sentinelle arrêté.
- Pour mettre à jour : double-cliquez sur **`mettre_a_jour.bat`** si vous avez installé avec Git. Sinon, remplacez les fichiers du programme en **gardant `data/`**. Relancez ensuite `lancer.bat` : les composants se mettent à jour tout seuls.

---

## 12. Dépannage

| Problème | Solution |
|----------|----------|
| « Python n'est pas installé » | Réinstallez Python en cochant « Add python.exe to PATH ». |
| Le panel ne s'ouvre pas | Allez sur http://127.0.0.1:8765. Lancez `diagnostic.bat`. |
| « Données de marché » en orange | Vérifiez votre connexion internet. Sentinelle réessaie tout seul. |
| L'IA ne fait aucun trade | Normal si le marché est baissier (l'IA reste à l'abri) ou si aucun signal n'est assez bon. Lisez le journal des décisions. |
| Modèle « non validé » | L'IA n'a pas trouvé d'avantage fiable sur la période de contrôle. Réessayez avec plus d'historique, une autre unité de temps (4 h) ou d'autres cryptos. |
| « Mode simulation uniquement » (erreur 403) | C'est le verrou de sécurité : l'opération demandée aurait touché de l'argent réel. Voir [SECURITE.md](SECURITE.md). |
| Ollama injoignable | Lancez Ollama, puis vérifiez le modèle avec `ollama list`. |
| Arrêt de sécurité (baisse max) | Réglages → Comptes → « Reprendre après arrêt de sécurité », après avoir compris ce qui s'est passé. |

---

## 13. Fiscalité (France)

Les plus-values crypto sont imposables. Les comptes ouverts sur des plateformes étrangères se déclarent chaque année (formulaire 3916-bis).
Exportez toutes vos opérations (page **Historique → Déclaration fiscale**) et faites valider votre situation par un expert-comptable. *Ceci n'est pas un conseil fiscal.*
