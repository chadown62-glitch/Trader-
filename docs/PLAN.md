# Plan : ce qui reste à faire

Mis à jour le 9 novembre 2026. ✅ = fait · 🔲 = à faire · 👤 = c'est à vous de le faire (je ne peux pas le faire à votre place).

## 1. Avant tout argent réel (indispensable)

| # | Tâche | État |
|---|-------|------|
| 1 | Passer le dépôt GitHub en **privé** (Settings → General → Danger Zone) | 👤 🔲 |
| 2 | Sur votre PC : `diagnostic.bat`, puis `demo.bat`, puis `lancer.bat`, et me signaler tout problème | 👤 🔲 |
| 3 | Clés **testnet** Binance : ordre manuel, stop posé, fermeture, arrêt d'urgence, redémarrage du PC | 👤 🔲 |
| 4 | Bot Telegram (@BotFather) : alertes et commandes | 👤 🔲 |
| 5 | Ollama + `ollama pull qwen2.5:7b`, puis activer les actualités | 👤 🔲 |
| 6 | Laisser tourner l'IA d'entraînement **30 jours minimum** | 👤 🔲 |
| 7 | Plateforme agréée MiCA + conseil fiscal | 👤 🔲 |
| 7 bis | Compléter le calendrier macro (Réglages → Calendrier macro) avec les dates 2027 de la Fed et de l'inflation US | 👤 🔲 |

## 2. Fonctions ajoutées

| # | Fonction | État |
|---|----------|------|
| 8 | Lecture des canaux Telegram de signaux, **en observation**, fiabilité mesurée par canal sur les vrais prix (page « Signaux ») | ✅ (testée avec des messages types ; la connexion réelle à votre compte reste à vérifier, point 👤) |
| 9 | Calendrier macro : pas de nouvelle position autour des annonces | ✅ |
| 10 | Contexte : Fear & Greed, S&P 500, Nasdaq, VIX, dollar, or, taux de financement | ✅ (affichage seulement : aucun avantage prouvé) |
| 11 | Alerte « bot muet » via un service externe (healthchecks.io) | ✅ (à configurer : Réglages → Surveillance) |
| 12 | Sauvegarde quotidienne de la base (`data/backups`) | ✅ |
| 13 | Vérification croisée des prix (Kraken) avant chaque entrée de l'IA | ✅ |
| 14 | Double authentification du panel (QR code) | ✅ |

## 3. Recherche sur les résultats (détail dans RESULTATS.md)

| # | Piste | Résultat |
|---|-------|----------|
| 15 | Historique étendu (paires USDT depuis 2017) | ✅ Adopté. Test indépendant 2018-2022 : le suivi de tendance est **nettement positif** (+0,33 R en 4 h, +0,77 R en 1 jour) |
| 16 | Unité de temps journalière | ✅ Disponible. Meilleur résultat par trade, mais trop peu de trades pour valider en 30 jours : 4 h reste le réglage par défaut |
| 17 | Allocation par tendance (investi ou en USDC) | ✅ Étudiée, **non retenue** : +46 %/an mais baisse maximale de 54 %, incompatible avec la prudence |
| 18 | Validation « règles seules » quand le filtre IA n'apporte rien | ✅ Ajoutée |
| 19 | Nouvelles idées jugées uniquement sur des données futures | En continu |

État honnête : sur les 18 derniers mois, le système reste **à l'équilibre** (≈ 0 %) pendant que le marché perd environ 30 %. L'IA est toujours « non validée » et le mode réel reste verrouillé.

## 4. Confort

| # | Fonction | État |
|---|----------|------|
| 20 | Mise à jour : `mettre_a_jour.bat` (avec Git), ou nouveau ZIP en gardant `data/` | ✅ |
| 21 | Notifications du navigateur (Réglages → Sécurité) | ✅ |
| 22 | Thème clair (bouton ☀ en haut) | ✅ |
| 23 | Export fiscal : toutes les opérations en CSV + résultat par année (page Historique) | ✅ (le calcul officiel français reste à faire par un expert-comptable) |

## 5. Pistes pour la suite

- Après 30 jours d'entraînement : bilan chiffré. Si le bulletin est au vert : testnet, puis réel avec un petit budget.
- Activer un canal de signaux en trading **seulement** s'il est positif sur au moins 30 signaux mesurés (pas encore codé : à faire après observation).
- Coupler 4 h et 1 jour (deux agents) si le journalier se confirme en direct.
- API Claude en option pour une analyse plus fine des actualités (payant, désactivé par défaut).
