# Passation de Claude Code à GPT Work — 25 novembre 2026, 21 h 30 UTC

Rédigée en application du cadrage fusionné par #181 (« avant chaque interruption, l'auteur consigne HEAD, diff, essais réellement exécutés, CI restante et prochaine opération »). Depuis #181, GPT Work dirige le programme moteur OMEGA. Claude Code n'a plus poussé sur les branches reprises par GPT Work et n'ouvre aucune nouvelle PR sans lot convenu.

## 1. Branches de Claude reprises par GPT Work

| Branche | PR | Dernier commit de Claude | Tête actuelle | Contenu de Claude |
|---|---|---|---|---|
| `claude/omega-hive` | #172 | `d89491a` | `1793e48` | HIVE, GENOME, BORDER : individuel, collectif, hybride |
| `claude/omega-cortex` | #173 | `99a1b44` | `609fa9e` | CORTEX, ECHO |
| `claude/omega-pulse` | #174 | `852f820` | `b21fd1d` | PULSE : détecteurs a priori |
| `claude/omega-atlas` | #175 | `2aefdf2` | `d5a2f31` | Inventaire RADAR/ATLAS, dépendance en crise (`dfb0349`) |
| `claude/omega-replay` | #176 | `4efe67b` | `12aa79e` | Reproduction des trois expériences du registre V3 |
| `claude/omega-genome` | #177 | `a95a5ef` | `4b4ec90` | Profils par actif ; **échec Windows** signalé par la CI sur `a95a5ef`, non diagnostiqué par Claude |
| `claude/c082-recalage` | #178 | `0f510b8` | `53e52cc` | Recalage de Platt : rejet |
| `claude/c042-carnets` | #179 | `4570954` | `9aeac5c` | Carnets publics et contrôles a posteriori |
| `claude/pulse-etalonnage` | #198 | `a354f40` | `a227b5a` | Étalonnage de PULSE |
| `claude/flotte-quatre-roles` | #180 | `e998880` | `8c6c2a5` | Quatre rôles en lecture seule ; protocole C100 figé, non démarré |

- **Essais réellement exécutés par Claude** : chaque PR cite ses tests (Python 3.12.3 et 3.13) et ses mesures. Aucune validation sous Windows n'a été exécutée par Claude.
- **CI restante** : celle des têtes actuelles, poussées par GPT Work.
- **Ordre de fusion conseillé** : celui du journal de temps en ajout seul, soit #172 → #173 → #174 → #175 → #176 → #177 → #178 → #179 → étalonnage → #180.

## 2. Incident à éviter (constaté sur #171)

La CI se déclenche sur l'événement `edited`, et sa concurrence annule l'exécution en cours. Modifier la description d'une PR prête **après** un envoi produit des contrôles annulés sur la même tête. Le garde de fusion automatique les refuse alors, à juste titre. Il faut donc toujours modifier la description **avant** l'envoi. Une révision neuve à contenu réel (`6bdd839`) a débloqué #171.

## 3. Étude sur cette branche, terminée, non proposée en PR

**Archives d'actualités** (`sentinelle/scalp/annonces.py`), préenregistrée au commit `8eeaae0`.

**Inventaire des sources** :

| Source | Accès | Profondeur et horodatage |
|---|---|---|
| Annonces Binance (CMS public) | accessible | 447 retraits depuis le 2022-02-17 ; 2 303 cotations ; heure de publication et de correction |
| GDELT, fichiers bruts | accessible | tous les quarts d'heure depuis le 2015-02-18 |
| GDELT DOC | limité (429) ou refusé | — |
| Hacker News (Algolia) | accessible | — |
| Wayback Machine | refusé depuis l'environnement | — |
| Flux RSS des médias crypto | accessible | derniers articles seulement, donc aucune profondeur historique sans collecte prospective |

**Retraits de cote au comptant** : 37 annonces, 112 jetons mesurés. Exclusions : 29 jetons sans paire USDT, 3 jetons en période réservée. 5 annonces ont été corrigées plus d'une heure après leur publication.

| Rendement anormal contre le BTC (entrée à la minute qui suit la publication) | Moyenne | Intervalle à 95 % | Holm |
|---|---|---|---|
| H1, à 24 h | **−23,3 %** | [−31,0 ; −16,3] | < 0,001 |
| H2, à 1 h | **−16,6 %** | [−22,4 ; −11,7] | < 0,001 |

- 73 % des jetons perdent plus de 10 % en 24 h.
- Dérive des 24 h **précédant** l'annonce : −7,8 %. Fuite d'information, ou faiblesse propre aux jetons promis au retrait.
- L'effet se retrouve chaque année de 2022 à 2026 et diminue : −53 % en 2023, −13 % en 2026.
- **Lecture** : le veto existant (`flash.py`, 24 h sans entrée après un retrait) est justifié. Une sortie à la première minute après l'annonce aurait évité l'essentiel de la baisse. Rien n'est modifié dans le moteur.
- **Limite** : le motif retenu (« Binance Will Delist X on date ») ne couvre pas tous les intitulés anciens.

## 4. Brouillons locaux, jamais poussés

**Mise à jour du registre C001–C100**, à refaire sur la base réelle après les fusions, avec une couverture rejouée sous traceur :

| Capacité | Statut | Preuves et notes à ajouter |
|---|---|---|
| C026 | inchangé | `atlas.dependence` ; note sur la dépendance en crise |
| C042 | ABSENTE → EXPÉRIMENT_REJETÉ | `carnet.intraday_test`, `carnet.robustness`, `carnet.fetch` ; information réelle, inférieure aux coûts |
| C082 | inchangé | note sur le rejet du recalage de Platt (#178) |
| C084 | inchangé | `pulse.psi_alarm`, `pulse.cusum`, `pulse.page_hinkley`, `etalonnage.calibrate` ; seuils étalonnés |
| C085 | inchangé | `hive.judge_year`, `hive.hybrid` ; spécialistes par actif moins bons que le collectif |
| C087 | EXISTANTE_ET_TESTÉE → EXPÉRIMENT_REJETÉ | `cortex.ensemble`, `cortex.judge_year`, `cortex.marginal_contribution` |
| C091 | inchangé | `atlas.crypto_row` |
| C093 | inchangé | `hive.split` |
| C003, C008 | inchangé | `annonces.event_study`, à rattacher |

Un essai local sur une intégration de toutes les branches a donné 95 capacités à couverture constatée, aucune à confirmer.

**Section 12 du dossier d'évaluation indépendante** : brouillon complet en annexe ci-dessous. Il porte sur les PR #172 à #180, l'étalonnage et cette étude. Les recalculs éventuels de GPT Work devront y être reportés.

## 5. Propositions de lots pour Claude (25 %), à convenir

1. Revue indépendante des recalculs de GPT Work sur HIVE, PULSE et les carnets : Claude relit, GPT Work modifie.
2. Archives d'actualités : étude GDELT hors ligne sur les fichiers bruts, si GPT Work et le propriétaire la jugent utile.
3. Tenue du registre et du dossier après les fusions.
4. Bibliothèque REPLAY : scénarios du registre V3 et scénarios négatifs.

## 6. Décisions qui restent au propriétaire

- Démarrage de C100.
- Interprétation de la règle « quatre agents actifs ».
- Suite des étapes de la consolidation en quatre rôles.

---

## Annexe — brouillon de la section 12 du dossier (Claude, avant les recalculs de GPT Work)

## 12. Phase de recherche OMEGA : premières mesures du 25 novembre 2026

Cadre : extension du Sprint 5 décidée par le propriétaire (`SPEC.md`, #171) et correspondance des 25 concepts avec C001–C100 (`docs/scalp/omega-cadrage.md`).

Le registre SENTINELLE OMEGA V3 n'a été transmis qu'après #172 à #174. Il est désormais versé en copie conforme (`docs/scalp/omega-registre-v3.md`, #171). Conséquences :
- les contrôles synthétiques de ces trois PR sont des **reconstructions**, faites d'après la description du propriétaire ;
- les trois expériences du registre sont reproduites dans #176 (bibliothèque REPLAY) ;
- les 11 interprétations provisoires des concepts sont remplacées par les fonctions du registre. Trois étaient fausses : TITAN, IMMUNITY et REPLAY.

**Méthode commune.**
- Protocole poussé avant calcul dans `research/scalp-v1/trials.jsonl`.
- Historique 2018-2026 **déjà consulté** ; aucune période aveugle.
- Échantillon hebdomadaire partagé par A, B et D :
  - chaque dimanche, les membres de l'univers défini à la date ;
  - cible : rendement des 7 jours suivants positif ;
  - 11 caractéristiques causales.
- Années 2020 à 2026 jugées une à une, apprentissage sur les années antérieures seulement, embargo de 8 jours.
- 3 521 prévisions, 35 actifs, 359 semaines. Taux de semaines positives : 48,7 %.
- Critère : perte logarithmique. Intervalle par grappes de semaines, correction de Holm dans chaque étude.
- Calculs légers (régression logistique, arbres à gradient peu profonds) : de 22 s (B) à 4 min 29 s (D) par étude sur 4 cœurs.

| PR | Préenregistrement | Concepts | Capacités | Résultat principal |
|---|---|---|---|---|
| #172 | `54c4503` | HIVE, GENOME, BORDER | C085, C093, C026 | Le modèle collectif bat les modèles par actif (perte −0,025, Holm 0,004). **Aucune version ne bat le taux appris** (collectif +0,004, Holm 0,33). |
| #177 | `79591ed` | GENOME | C026, C085 | Volatilité et bêta stables par actif (Holm 0,003 et 0,004). Momentum et dérive non stables. Familles a priori sans apport. |
| #173 | `aad46a8` | CORTEX, ECHO | C087, C083 | Modèles divers mais sans information utile : l'ensemble ne bat ni la régression logistique (+0,002, Holm 0,46) ni le taux appris. |
| #175 | descriptif ; crise : `dfb0349` | RADAR, ATLAS | C091, C026, C074 | 42 cryptos inventoriées. Un facteur commun explique 59 à 84 % de la variance chaque année. En crise, la corrélation monte à 0,67 contre 0,42 (p 0,008). |
| #174 | `ef4e208` | PULSE, QUARANTINE | C084, C082 | Seules les erreurs révèlent une rupture de relation. Le PSI des entrées n'en voit aucune et alerte chaque année sur le marché. |
| #178 | `39c5a3c` | ORACLE, COMPASS | C082 | Le recalage de Platt bat l'isotonique sans significativité (Holm 0,08). Il reste moins bon que le taux appris et prendrait 962 trades au R moyen de −0,29 : **rejet**. |
| #179 | `b94ef2e` (a posteriori : `d193d9e`, `4db0665`) | RADAR, IMMUNITY, TIMELOCK | C042 | Carnets publics vérifiés. Information propre du carnet à 5 et 60 min, au-delà du rendement passé, mais au plus 4 pb contre environ 20 pb de coûts. Rien de cohérent à 1 jour. |
| PR d'étalonnage | `514d6af` | PULSE, REPLAY | C084, C079 | Seuils à 7 σ : 11 à 14,5 % de fausses alertes sur scénarios indépendants, 97 % des ruptures nettes détectées. Les deux hypothèses sont confirmées. |
| #180 | — | EVOLUTION, TRUTH | C090, C100 | Douze agents rangés en quatre rôles (lecture seule). Protocole C100 figé, non démarré. |
| #176 | `044eb65` | REPLAY (registre V3) | C079, C084, C095 | Expériences synthétiques du V3 reproduites : CORTEX et PULSE à l'identique, HIVE à moins de 3 erreurs types (identique sur le scénario stable). |

**A — HIVE, GENOME, BORDER (#172).**
- **Contrôle synthétique retrouvé** (perte logarithmique) :

  | Famille | Individuel | Collectif |
  |---|---|---|
  | Cohérente | 0,624 | 0,609 |
  | Divergente | 0,448 | 0,667 |

  La mesure sait donc voir un gain du collectif comme un transfert négatif.
- **Marché, perte logarithmique moyenne** :

  | Modèle | Perte |
  |---|---|
  | Taux appris | 0,6954 |
  | Collectif | 0,6999 |
  | Actif exclu de l'apprentissage | 0,7015 |
  | Hybride | 0,7041 |
  | Individuel | 0,7248 |

- **Comparaisons** :

  | Comparaison | Écart | Holm |
  |---|---|---|
  | Collectif − individuel | −0,025 | 0,004 |
  | Hybride − individuel | −0,021 | 0,004 |
  | Hybride − collectif | +0,004 | 0,16 |

  Le retrait de l'hybride (poids n / (n + 104)) n'apporte rien.
- **BORDER** : un actif jamais vu est prévu presque aussi bien qu'un actif appris (+0,0016, Holm 0,12).
- **Transfert négatif** : 4 actifs sur 24 (BCH, EOS, ETC, FIL). Aucun lien avec la divergence de l'actif (rang −0,30, n.s.).
- **Par année** : le collectif bat le taux appris en 2023, 2025 et 2026 seulement.
- **Portefeuille descriptif** (non préenregistré comme critère) : +0,42 %/semaine géométrique, pire baisse 77 %, 41 % de semaines positives. Loin des cibles.
- **Verdict.** Mutualiser vaut mieux que spécialiser par actif. Aucun avantage prédictif absolu n'est démontré.

**A — GENOME (#177).**
- **Données.** 42 actifs, 378 semaines cotées par actif en médiane, 89 au minimum.
- **Familles fixées a priori** : paiement (9 actifs), plateformes (20), infrastructure et services (10), memecoins (3).
- **Prédictions préenregistrées** : volatilité stable, bêta stable, momentum non stable, dérive non stable. **Les quatre se vérifient.**

  | Profil | Stabilité entre moitiés (Spearman) | Holm | Fiabilité | Meilleure prévision de la seconde moitié |
  |---|---|---|---|---|
  | Volatilité | 0,49 | 0,003 | 0,80 | moyenne générale |
  | Bêta au BTC | 0,48 | 0,004 | 0,77 | profil propre |
  | Momentum | 0,17 | 0,29 | −0,76 | moyenne générale |
  | Dérive | −0,02 | 0,54 | 0,17 | moyenne générale |

- **Verdict.**
  - Le risque est un profil propre à chaque actif. Ce qui servirait à prévoir ne l'est pas : c'est ce qui explique HIVE.
  - Les familles n'apportent rien au-delà de la moyenne générale.
- **Coût.** 52 s de calcul et 190 Mo de mémoire.

**B — CORTEX, ECHO (#173).**
- **Perte logarithmique** :

  | Modèle | Perte |
  |---|---|
  | Règle de momentum (signe du rendement sur 28 jours) | 0,6948 |
  | Taux appris | 0,6954 |
  | Régression logistique | 0,6999 |
  | Ensemble à poids égaux fixés a priori | 0,7021 |
  | Arbres à gradient | 0,7303 |

- **Défaut de mesure relevé par un test.** La corrélation des erreurs brutes (0,98 à 0,99) est dominée par l'issue commune ; elle le serait même entre modèles indépendants.
- **Mesure retenue : les écarts à la référence.** Ils sont peu corrélés (régression–arbres 0,38, régression–momentum 0,05). Mais l'apport marginal de chaque modèle à l'erreur de la référence est nul (|r| < 0,05).
- **Biais annuels communs à tous les modèles** (corrélation 0,98 à 0,999). Les années 2020, 2022 et 2026 sont mal prévues par tous.
- **Verdict.** La diversité est réelle mais sans information utile. **N'ajouter aucun modèle** ; l'hypothèse de C087 est rejetée.

**C — RADAR, ATLAS (#175).**
- **Cryptos.** 42 candidates, toutes cotées. 7 retirées, 1 coupée à une recotation. 3 memecoins : DOGE, PEPE, SHIB. 10 membres par jour en médiane.
- **Facteur commun.** Corrélation hebdomadaire moyenne entre membres de 0,51 (2021) à 0,82 (2022) ; première composante de 59 à 84 %. Ce facteur dominant borne ce que la diversification entre cryptos et l'apprentissage par actif peuvent apporter. C'est cohérent avec A.
- **Fonds et séries macroéconomiques.** Historiques depuis 2000 à 2006 ; COIN depuis 2021.
- **Faisabilité à 30 €.** 4 % des entrées de B1 et 73 % de celles de B0 (C062, rappel). La cible « 30 € » reste structurellement contrainte par les minima d'ordre.
- **Dépendance en crise** (préenregistrée, `dfb0349`, d'après le registre V3) :

  | Semaines | Corrélation moyenne | Membres en baisse |
  |---|---|---|
  | 47 semaines où le BTC perd au moins 9,8 % | **0,668** | **94 %** |
  | 369 semaines intermédiaires | 0,479 | 52 % |
  | 47 semaines où le BTC gagne au moins 11,6 % | 0,420 | 11 % |

  - Écart crise − hausse : +0,248, intervalle à 95 % [+0,047 ; +0,435], p unilatérale 0,0075. **H1 est confirmée**, sur un historique déjà consulté.
  - Limite : la volatilité plus forte des semaines de baisse peut expliquer une partie de l'écart.
- **Stabilité de la structure.** Rang de Spearman des corrélations par paire d'une année à l'autre : −0,03 à 0,76, médiane 0,35. La hiérarchie des paires change, ce qui rend fragile un plafond par groupe corrélé fixé une fois pour toutes (C074 rejetée, #159).
- **Lecture.** La diversification entre cryptos disparaît en crise. La cible de 10 % de pire baisse est hors d'atteinte pour un portefeuille composé seulement de cryptos.
- **Garanties.** Aucune nouvelle source, aucune exposition ajoutée.

**D — PULSE, QUARANTINE (#174).**
- **Détecteurs**, seuils fixés a priori :
  - PSI des entrées, supérieur à 0,25 sur 13 semaines ;
  - CUSUM bilatéral du biais, k = 0,5 σ et h = 5 σ ;
  - Page-Hinkley de la perte, δ = 0,5 σ et λ = 5 σ.
- **Six scénarios synthétiques reconstruits**, 200 répétitions chacun :

  | Scénario | Page-Hinkley (erreurs) | CUSUM (biais) | PSI (entrées) |
  |---|---|---|---|
  | Aucune rupture | 31 % de fausses alertes | 41 % de fausses alertes | 0 % de fausses alertes |
  | Rupture nette | 82 % détectées, délai 1 semaine | 48 % détectées, délai 29 semaines | 0 % détectées |
  | Rupture faible | 84 %, délai 13 semaines | 21 %, délai 36 semaines | 0 % |
  | Rupture progressive | 83 %, délai 16 semaines | 39 %, délai 47 semaines | 0 % |
  | Rupture dépendante | 81 %, délai 1 semaine | 76 %, délai 6 semaines | 0 % |
  | Entrées seules, relation intacte | 31 % d'alertes | 38 % d'alertes | 100 % d'alertes, toutes inutiles |

- **Sur le marché** (modèle collectif de A, remis à zéro chaque année) :
  - le PSI alerte **chaque année**, dès la 13e semaine ;
  - Page-Hinkley alerte le 2020-03-29 et le 2021-05-16 ;
  - CUSUM alerte le 2022-05-15 ;
  - rien de 2023 à 2026.
- **Écart au cadrage.** Le cadrage prévoyait d'appliquer les détecteurs à l'IA principale. Le préenregistrement les a appliqués au modèle collectif de A, qui partage l'échantillon de B.
- **Verdict.** Surveiller les erreurs et le biais, pas les entrées. Les seuils a priori sont trop sensibles (31 à 41 % de fausses alertes). Un étalonnage devra être préenregistré séparément, avec une cible de durée moyenne sans fausse alerte. Aucune quarantaine n'est appliquée et les vetos déterministes restent inchangés.

**REPLAY — reproduction du registre V3 (#176).**
- **Préenregistrement** (`044eb65`) :
  - CORTEX et PULSE avec le code et les graines du registre, critère « identique » ;
  - HIVE avec le modèle du registre, dont l'ordre des tirages n'est pas publié et a été fixé a priori, critère « au plus 3 erreurs types ».
- **Résultats.**
  - CORTEX / ECHO : 16 valeurs sur 16 identiques à 10⁻⁶ près.
  - PULSE / QUARANTINE : 25 valeurs sur 25 identiques.
  - HIVE / BORDER : scénario stable identique, écart maximal de 2,22 erreurs types ailleurs. Les valeurs théoriques exactes sont publiées.
  - Coût : 0,9 s de calcul et 110 Mo de mémoire.
- **Cohérence avec les reconstructions.**
  - L'hybride dégrade l'individuel sur des familles peu cohérentes. C'est ce qu'a trouvé le V3 et ce que donne #172 sur la famille divergente.
  - Une alerte sur les entrées n'est pas une dégradation : le V3 et #174 le montrent tous deux.
- **Différence avec les reconstructions.** Les fenêtres de 32 observations du V3 ne donnent que 1,9 à 2,7 % d'alertes avant la rupture. Les seuils CUSUM et Page-Hinkley de #174, à 5 σ, en donnent 31 à 41 %. Cela confirme qu'il faut un étalonnage préenregistré.

**Recherches autorisées le 25 novembre au soir.**

- **C082, calibration de l'IA principale (#178).**
  - Préenregistrement (`39c5a3c`) : recalage logistique de Platt, appris sur la même tranche de calibration que l'isotonique.

  | Probabilités | Score de Brier |
  |---|---|
  | Isotoniques | 0,1953 |
  | Brutes | 0,1910 |
  | Platt | 0,1914 |
  | Mélange isotonique–taux appris | 0,1903 |
  | Taux appris | **0,1890** |

  - T1 (Platt bat l'isotonique) : Holm 0,08. T2 (Platt bat le taux appris) : compétence −1,3 %, Holm 0,93.
  - Platt prendrait 962 trades au R moyen de −0,29, contre 365 trades à −0,14 pour l'isotonique.
  - La pente du recalage descend jusqu'à −0,17 certains mois : le classement du modèle s'inverse.
  - **Rejet.** Le défaut ne se corrige pas par un recalage, car les scores n'ont pas d'information propre. Le moteur n'est pas modifié.
- **C042, carnets d'ordres publics (#179).** Archives `bookDepth` des contrats perpétuels, 2023-01-01 → 2026-08-31, période réservée exclue.
  - **Intégrité** : 2 673 archives vérifiées par leur somme de contrôle ; 3 jours absents pour BTC, 2 pour ETH.
  - **Couverture** : 169 jours incomplets. Le niveau ±0,2 % n'existe que depuis le 2026-01-15.
  - **Disponibilité** : publication médiane 7,3 h après la fin du jour, mais 185 jours publiés plus de 7 jours après.
  - **Alignement horaire** : correct (3 pb à 0 h, contre 20 à 27 pb à ±1 h).
  - **Tests préenregistrés** : les 8 tests intrajournaliers sont significatifs (rang moyen de 0,06 à 0,16). À 1 jour, seul BTC ±5 % l'est (Holm 0,006), sans forme cohérente par terciles.
  - **Contrôles a posteriori**, déclarés avant leur calcul et non confirmatoires : décalage de 5 min, placebo sur le passé, sans recouvrement, retour pur du prix, apport propre à rendement passé donné (0,05 à 0,15).
  - **Verdict.** L'information est réelle mais très inférieure aux coûts : au plus 4 pb entre déciles extrêmes, contre environ 20 pb pour un aller-retour au comptant. **Expérience rejetée pour la décision.**
- **Étalonnage de PULSE** (préenregistré, `514d6af`).
  - Étalonnage sur le scénario sans rupture (graine A) ; seuils retenus h = λ = 7 σ.
  - Jugement sur deux jeux indépendants : même générateur avec la graine B, et générateur différent avec la graine C.
  - **H1 confirmée** : fausses alertes de 11 à 14,5 %, contre 35 à 46 % avec les seuils a priori.
  - **H2 confirmée** : Page-Hinkley détecte 96,5 à 97 % des ruptures nettes, et 83 à 96 % des ruptures faibles, progressives ou dépendantes.
  - Le CUSUM du biais ne voit bien que les ruptures dépendantes.
  - Sur le marché, il ne reste que deux alertes : 2020-04-26 et 2022-06-05.
- **Quatre rôles (#180)** :

  | Rôle | Agents |
  |---|---|
  | Veille | System Doctor, Market Sentinel, Regime Detector, News & Event Analyst |
  | Signal | Alpha Hunter, Challenger Lab |
  | Garde | Rug Pull Guardian, Risk Commander, Exit Guardian |
  | Exécution | Liquidity Scout, Cost Router, Portfolio Optimizer |

  Le rangement est en lecture seule : protections et ordre de décision sont inchangés. Les étapes suivantes sont soumises au propriétaire.
- **Protocole C100 (#180)**, figé et **non démarré**. Candidate B0 + A1 et cinq références. Critères préenregistrés (+1,30 %/semaine, 10 %, 60 %, B0 + A1 meilleure que B0), 52 semaines, aucun arrêt anticipé pour succès, aucune promotion.

**Hypothèses rejetées, gardées en mémoire :**
- spécialistes par actif contre modèle collectif (C085, élargie), et profils prédictifs par actif (GENOME : momentum et dérive non stables) ;
- ensemble léger contre meilleur modèle seul et règles simples (C087, désormais EXPÉRIMENT_REJETÉ) ;
- dérive des entrées comme signal de dégradation (C084 : utilité technique, mais pas comme alerte de rupture) ;
- correction de la calibration par recalage de Platt (C082, #178) ;
- usage décisionnel du déséquilibre du carnet (C042, #179) : information réelle, inférieure aux coûts.

**Registre.** Entrées mises à jour : C026, C084, C085, C087, C091 et C093 (preuves de #172 à #176). Couverture rejouée sous traceur.

**Points pour l'audit indépendant (GPT Work) :**
1. Contrôles synthétiques de #172 à #174 reconstruits avant la réception du registre V3. Ils sont confrontés au registre dans #176, mais restent des reconstructions. Pour HIVE, l'ordre des tirages du V3 n'est pas publié.
2. Échantillon commun à A, B et D : leurs conclusions ne sont pas indépendantes.
3. Cible binaire à 7 jours et perte logarithmique : un autre critère (rendement net de coûts) pourrait changer le classement A.
4. Faible taux de semaines positives (48,7 %) et biais annuels communs : un effet de marché domine toute prévision.
5. Étalonnage des seuils de PULSE : protocole séparé à préenregistrer avant toute quarantaine.

**Ce que le registre V3 demande et qui n'est pas encore mesuré :**
- **ATLAS** : les regroupements par méthode de classification ne sont pas mesurés. La dépendance en crise et la stabilité des corrélations le sont (#175).
- **QUARANTINE** : signalement explicite d'un actif ou d'un régime hors distribution, avec des états distincts (diagnostic inconnu, données périmées, erreur confirmée). Aucune étude à ce jour.
- **TITAN** : aucune capacité du catalogue ne lui correspond. Le temps et la mémoire ne sont mesurés que pour REPLAY.

**Suites possibles (dans le cadre actuel, sans nouvelle source ni observation prospective) :**
- intégrité (priorité E) : contrôle automatique de l'ordre du registre des essais ;
- premier bilan le 1er décembre.

**Verdict global inchangé : négatif.** Aucune configuration n'atteint +1,30 %/semaine net géométrique avec une pire baisse de 10 % et 60 % de semaines positives. Aucun avantage prédictif reproductible n'est démontré.

**Reproduction (Windows 11, Python 3.12)**

```powershell
python -m pytest tests/test_scalp_hive.py tests/test_scalp_genome.py tests/test_scalp_cortex.py tests/test_scalp_pulse.py tests/test_scalp_atlas.py tests/test_scalp_replay.py tests/test_scalp_registre.py -q
python -m sentinelle.scalp.hive --trials ""
python -m sentinelle.scalp.genome --trials ""
python -m sentinelle.scalp.cortex --trials ""
python -m sentinelle.scalp.pulse --trials ""
python -m sentinelle.scalp.atlas --trials ""
python -m sentinelle.scalp.replay --trials ""
python -m sentinelle.scalp.registre
```
