# OMEGA — correspondance des 25 concepts avec les capacités C001–C100

Décision du propriétaire du 25 novembre 2026, extension du Sprint 5 (`SPEC.md`).

**Ce que sont ces concepts.** Ce sont des propositions de composants ou de méthodes expérimentales. Ce ne sont pas des agents : au plus quatre agents restent actifs. Avant toute implémentation, chaque concept est rapproché des capacités existantes.

**Source des descriptions.** Le registre SENTINELLE OMEGA V3 du 25 novembre 2026 est versé en copie conforme : `docs/scalp/omega-registre-v3.md`, SHA-256 `2310472d4d986c52be6da5ec0a89d69cbe7f9c7f15ae33b278d71627538ac0e3`.
- La fonction de chaque concept est celle de ce registre :
  - pour les concepts 1 à 20, son « Catalogue conservé » ;
  - pour les concepts 21 à 25, ses « Cinq extensions conceptuelles ».
- Ce registre reste un registre de **propositions de recherche**, pas de missions.
- Une première version de cette correspondance, établie sans le registre, portait 11 interprétations provisoires. Elles sont remplacées ici. Trois étaient fausses :
  - TITAN désigne l'efficience informatique, non le passage à l'échelle des capitaux ;
  - IMMUNITY désigne la qualité des données, non les vetos ;
  - REPLAY désigne une bibliothèque de ruptures synthétiques, non le rejeu des décisions.

**Lecture du tableau.**
- Les capacités citées sont celles de `docs/scalp/catalogue-C001-C100.md`.
- Pour LINEAGE, BLINDSPOT, MIRROR, COMPASS et PROVENANCE, ce sont celles proposées par le registre V3.
- L'état est celui du registre `research/scalp-v1/registre-capacites.json` à la date du cadrage :
  - 43 capacités existantes et testées ;
  - 28 partielles ;
  - 4 absentes ;
  - 2 bloquées ;
  - 23 rejetées ;
  - 0 validée.

| Concept | Fonction (registre V3) | Test proposé (registre V3) | Capacités rattachées | Acquis mesurés avant OMEGA | Priorité |
|---|---|---|---|---|---|
| HIVE | Apprentissage mutualisé | Individuel, mutualisé et hybride sur partitions temporelles et actifs jamais vus | C085, C087, C093 | C085 rejetée (#164) : des spécialistes par régime ne battent pas le modèle unique | **A** |
| GENOME | Profils statistiques par actif | Stabilité, effectif disponible et erreur des profils par famille | C026, C085 | Fiches par crypto (`ai/knowledge.py`), jamais évaluées | **A** |
| BORDER | Détection de transfert négatif | Transfert contre estimation individuelle, familles cohérentes et incohérentes, sur données non utilisées pour sélectionner | C093, C091, C094 | Walk-forward mensuel de l'IA principale (#157) ; univers défini à la date (C091) | **A** |
| CORTEX | Combinaison de modèles | Comparaison aux modèles seuls, aux références simples et aux erreurs corrélées | C087, C083 | C083 : désaccord arbres/linéaire sans gain (#157) ; C087 : avantage non démontré | **B** |
| ECHO | Redondance des connaissances | Corrélation des erreurs entre modèles, référence simple, élimination des doublons conceptuels | C086, C087 | C086 rejetée (#164) : redondance des entrées, pas des erreurs | **B** |
| RADAR | Univers d'observation étendu | Couverture, qualité des données et apport marginal ; univers défini à la date | C091, C092, C049, C060 | 42 cryptos candidates, fonds Yahoo, archives memecoin ; actifs retirés inclus | **C** |
| ATLAS | Dépendances entre actifs | Stabilité des corrélations, regroupements et dépendance en crise | C026, C074 | Corrélation au BTC sans effet d'abstention (#156) ; plafond par groupe corrélé rejeté (#159) | **C** |
| PULSE | Détection de dérive | Délai, fausses alertes et stabilité sur ruptures synthétiques | C084, C082 | Dérive des entrées (PSI) sans lien avec la dégradation (#157) | **D** |
| QUARANTINE | Gestion explicite de l'inconnu | Signaler un actif ou un régime hors distribution, sans assouplir aucun veto | C083, C089, C084 | Abstention par incertitude sans gain (#157) ; anomalies de marché bloquantes (C089) | **D** |
| REPLAY | Bibliothèque de ruptures synthétiques | Scénarios reproductibles de dérive et d'incidents, indépendants des résultats financiers historiques | C079, C084, C095 | Aucune bibliothèque commune | lié à D |
| TRUTH | Intégrité scientifique | Essais multiples, biais, témoins et réutilisation de l'historique | C094, C095, C096 | Holm, BH et DSR (#147) ; outil d'ablation (#149) | E |
| TIMELOCK | Traçabilité temporelle | Dates de publication, de réception et de disponibilité, et corrections des sources | C001, C006, C092 | Délai de réception des actualités (C001) ; heures officielles FOMC et CPI (#158) | E |
| MEMORY | Mémoire des erreurs | Index des essais, hypothèses rejetées, versions et causes d'échec | C092 | `trials.jsonl` en ajout seul, rejets conservés dans le registre | E |
| MIRROR | Contrôles négatifs | Signaux artificiels sans information, auxquels le laboratoire ne doit attribuer aucune capacité prédictive | C093, C094, C095 | Témoins décalés d'un an systématiques | E |
| PROVENANCE | Traçabilité des prédictions | Identifiants des jeux de données, fenêtres et empreintes de chaque prédiction expérimentale | C081, C092, C093 | Sommes de contrôle publiées revérifiées à chaque lecture (#168) | E |
| PHANTOM | Portefeuilles fictifs parallèles | Rejeu des mêmes données et coûts, sans ordre réel ni modification du portefeuille principal | C090, C095, C098 | Outil d'ablation à capital, univers et coûts identiques (#149) | — |
| ORACLE | Prévisions probabilistes | Brier, calibration, incertitude et couverture | C082, C081, C083 | Défaut de calibration de l'IA principale (#157, #164) | lié à B et D |
| NEMESIS | Contre-expertise adversariale | Injection de ruptures, de données manquantes, de coûts et de défauts simulés | C079, C097, C078 | Stress de coûts (#149) ; pires semaines (#159) | lié à D |
| CHRONOS | Échelles temporelles | Alignement temporel et contribution de chaque horizon | C081, C093 | Caractéristiques causales sur 1 à 90 jours, contribution par horizon non mesurée | — |
| IMMUNITY | Qualité et anomalies des données | Données incohérentes, périmées, manipulées, absentes, et provenance | C089, C092, C049, C050 | Vérification croisée des prix (C050) ; volumes fictifs non détectés (C049 absente) | — |
| EVOLUTION | Champion/challenger | Configurations figées comparées, aucune promotion automatique | C090, C100 | Comparaisons préenregistrées en simulation (C090) | — |
| TITAN | Efficience informatique | Temps, mémoire, entrées-sorties et reproductibilité sur le matériel cible | aucune capacité dédiée (contrainte matérielle de `CLAUDE.md`) | Durée de calcul publiée dans chaque résultat ; mémoire non mesurée | — |
| LINEAGE | Dépendances de provenance | Sources et transformations communes des modèles | C086, C087, C092 | Redondance des entrées (#164) | lié à B |
| BLINDSPOT | Erreurs communes rares | Erreurs partagées et individuelles sur des scénarios artificiels rares | C079, C084, C095 | Pires semaines (#159) | lié à D |
| COMPASS | Incertitude contre erreur | Calibration sur données synthétiques dégradées, sans résultat futur | C082, C083, C089 | Recalage isotonique trop confiant (#157) | lié à D |

## Premiers travaux (priorités A à D)

1. **A — HIVE, GENOME, BORDER.** Les mêmes caractéristiques causales et les mêmes modèles légers sont comparés en trois versions : individuelle, collective et hybride.
   - **Contrôle synthétique.** Il est reconstruit à partir de la description du propriétaire : familles cohérentes ou divergentes. Il vérifie que la mesure détecte le gain et le transfert négatif.
   - **Marché.** Données : les 42 cryptos journalières. Quatre mesures :
     - walk-forward annuel ;
     - actifs exclus de l'apprentissage ;
     - transfert négatif actif par actif ;
     - correction de Holm.
2. **B — CORTEX, ECHO.** Corrélation des erreurs entre modèles et références simples, part d'information commune, gain d'un ensemble à poids fixés a priori.
3. **C — RADAR, ATLAS.** Inventaire chiffré de l'univers observable, sans nouvelle source sans autorisation.
4. **D — PULSE, QUARANTINE.** Détecteurs de dérive des entrées, des biais et des erreurs, étudiés sur des ruptures synthétiques nettes, faibles, progressives et dépendantes, puis sur l'IA principale. Mesures : délais, fausses alertes et non-détections.
5. **Registre V3.** Ses trois expériences synthétiques sont reproduites à l'identique dans le dépôt (graines 20261125, 20261126 et 20261127), comme première pièce de la bibliothèque REPLAY. Elles sont ensuite comparées aux contrôles reconstruits avant sa réception.

## Recherches autorisées le 25 novembre au soir : rattachement aux concepts

| Recherche | Concepts | Capacités | Ce qui est mesuré |
|---|---|---|---|
| Archives publiques de carnets d'ordres | RADAR, IMMUNITY, TIMELOCK | C042, C092 | Intégrité par somme de contrôle, couverture, délai de publication, alignement horaire, déséquilibre du carnet contre rendement futur |
| Archives d'actualités | TIMELOCK, RADAR, PROVENANCE | C001, C002, C005, C006 | Inventaire des sources publiques (couverture, horodatage, licence), puis étude historique hors ligne |
| Calibration de l'IA principale | ORACLE, COMPASS | C082, C083 | Correction préenregistrée jugée hors échantillon contre l'isotonique et le taux appris |
| Étalonnage de PULSE | PULSE, REPLAY, NEMESIS | C084, C079 | Seuils fixés sur des scénarios d'étalonnage, jugés sur des scénarios indépendants |
| Consolidation des 12 agents en quatre rôles | EVOLUTION, TRUTH | C090 | Préparation architecturale seulement, sans supprimer aucune protection |
| Protocole C100 | EVOLUTION, PHANTOM, COMPASS | C100, C090, C095 | Protocole figé ; l'observation ne démarre pas sans autorisation distincte |
