# OMEGA — correspondance des 25 concepts avec les capacités C001–C100

Décision du propriétaire du 25 novembre 2026, extension du Sprint 5 (`SPEC.md`).

**Ce que sont ces concepts.** Ce sont des propositions de composants ou de méthodes expérimentales. Ce ne sont pas des agents : au plus quatre agents restent actifs. Avant toute implémentation, chaque concept est rapproché des capacités existantes.

**Origine des descriptions.**
- Description « propriétaire » : reprise de la décision du 25 novembre.
- Description « provisoire » : interprétation tirée du seul nom du concept. Le registre SENTINELLE OMEGA V3 n'est accessible ni dans le dépôt, ni dans les sources ouvertes à la session. Ces lignes sont donc à corriger dès que ce registre sera versé au dépôt.

**Lecture du tableau.**
- Les capacités citées sont celles de `docs/scalp/catalogue-C001-C100.md`.
- L'état est celui du registre `research/scalp-v1/registre-capacites.json` à la date du cadrage :
  - 43 capacités existantes et testées ;
  - 28 partielles ;
  - 4 absentes ;
  - 2 bloquées ;
  - 23 rejetées ;
  - 0 validée.

| Concept | Description | Origine | Capacités rattachées | Acquis mesurés | Priorité OMEGA |
|---|---|---|---|---|---|
| HIVE | Apprentissage mutualisé entre actifs (modèle collectif) | propriétaire | C085, C087, C093 | C085 rejetée (#164) : des spécialistes par régime ne battent pas le modèle unique de l'IA principale | **A** |
| GENOME | Spécialisation statistique de chaque actif (modèle individuel) | propriétaire | C026 (profil par actif), C085, C093 | Fiches par crypto (`ai/knowledge.py`), sans comparaison individuelle contre collective | **A** |
| BORDER | Généralisation hors du domaine d'apprentissage : actifs non vus, périodes futures, transfert négatif | propriétaire | C093, C091, C094 | Walk-forward mensuel de l'IA principale (#157) ; univers défini à la date (C091) | **A** |
| CORTEX | Complémentarité effective des modèles | propriétaire | C087, C083 | C083 : désaccord arbres/linéaire sans gain (#157) ; C087 : ensemble léger, avantage non démontré | **B** |
| ECHO | Corrélation des erreurs, biais communs, dépendances informationnelles | propriétaire | C083, C086, C087 | C086 rejetée (#164) : redondance des entrées, pas des erreurs | **B** |
| RADAR | Élargissement de l'univers observé (cryptos, memecoins, actions, fonds) | propriétaire | C091, C092, C049, C060 | 42 cryptos candidates, fonds Yahoo, archives memecoin ; actifs retirés inclus | **C** |
| ATLAS | Inventaire : couverture, historiques, qualité, dépendances, coûts, liquidité | propriétaire | C061, C062, C091, C098, C099 | Frais datés (#155), minima d'ordre (#150), écart de cotation (#154) | **C** |
| PULSE | Surveillance de la dégradation des modèles, entrées normales comprises | propriétaire | C084, C082 | C084 : dérive des entrées (PSI) sans lien avec la dégradation (#157) ; C082 : défaut de calibration démontré | **D** |
| QUARANTINE | Mise à l'écart d'un modèle dégradé, sans assouplir les vetos | propriétaire | C083, C030, C084 | Abstention par incertitude sans gain (#157) | **D** |
| TRUTH | Résultats négatifs conservés, aucune optimisation des évaluations | propriétaire (principe E) | C094, C095 | Holm, BH et DSR (#147), outil d'ablation (#149) | E |
| TIMELOCK | Préenregistrement horodaté poussé avant calcul, causalité vérifiée | propriétaire (principe E) | C092, C094 | Toutes les études #149 à #169 préenregistrées | E |
| MIRROR | Témoins et références simples (masque décalé, B0 et B1) | propriétaire (principe E) | C095 | Témoins décalés d'un an systématiques | E |
| MEMORY | Mémoire des essais en ajout seul | propriétaire (principe E) | C092 | `trials.jsonl`, `active-time.jsonl` | E |
| PROVENANCE | Empreintes des données et des archives | propriétaire (principe E) | C092, C050 | Sommes de contrôle publiées revérifiées à chaque lecture (#168) | E |
| PHANTOM | Observation fantôme, sans décision | provisoire | C006, C100 | Séries macro en observation fantôme (C006) ; actualités positives jamais signal d'achat (lot 2) | — |
| ORACLE | Prévision probabiliste calibrée | provisoire | C082, C081 | Défaut de calibration de l'IA principale (#157, #164) | lié à B et D |
| NEMESIS | Scénarios adverses et stress | provisoire | C076, C077, C079, C097 | Stress de coûts (#149), pires semaines (#159) | lié à D |
| CHRONOS | Structure temporelle : calendrier, événements, régimes | provisoire | C007, C021, C029 | FOMC et CPI (#158), calendrier rejeté (#162), régimes (#156) | — |
| IMMUNITY | Vetos déterministes et défenses (anti-rug, bouclier) | provisoire | C009, C050 à C055 | Guardian, Risk Commander et vetos jamais assouplis | conservé |
| EVOLUTION | Sélection de stratégies, champion contre challenger | provisoire | C087, C100 | Aucune promotion automatique | — |
| TITAN | Passage à l'échelle des capitaux fictifs (30, 100, 1 000 et 10 000 €) | provisoire | C062, C098 | Faisable à partir d'environ 1 000 € (#150) | lié à C |
| REPLAY | Rejeu exact des décisions et des exécutions | provisoire | C099, C092 | Écart à l'ouverture simulée (#154) | E |
| LINEAGE | Filiation des modèles, des données et des résultats | provisoire | C092 | Registre des essais, empreintes du baseline (#151) | E |
| BLINDSPOT | Angles morts : données manquantes, capacités non mesurées | provisoire | C019, C045, C094 | Mentions NON MESURÉ, capacités bloquées | E |
| COMPASS | Cibles et critères d'arrêt (+1,30 %/semaine, 10 %, 60 %) | provisoire | C095, C100 | Verdict global négatif | E |

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
