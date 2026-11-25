# SENTINELLE AI — Catalogue des capacités C001–C100 (dossier du propriétaire, reçu en quatre parties le 24 novembre 2026, transcrit sans modification de fond)

> Second envoi du catalogue par le propriétaire (24 novembre 2026, « Catalogue C001–C100 et reprise du Sprint 5 ») : mêmes 100 identifiants ; huit libellés précisés et repris ici (C013, C016, C027, C049, C062, C064, C096, C100) ; l'inventaire ajoute la méthode de validation, la valeur ajoutée attendue et le lot de chaque capacité.

## PARTIE 1/4

Contexte : dépôt chadown62-glitch/Trader- ; Sprint 5 ; PR #147 fusionnée : e7be7af8f05c558dd2a46170dbb6975a25ccfc81.
Le propriétaire autorise l'étude et la réalisation progressive des 100 capacités candidates, sous réserve du cadrage préalable dans SPEC.md.

Gouvernance :
- PAPER TRADING exclusivement. Aucun ordre réel, aucune clé privée, aucun levier, aucun short, aucune promotion automatique.
- Archives intouchables : PR #54 5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18 ; PR #55 18b31bb901f656d6126b370f47c510775f135c57.
- Guardian, Risk Commander, causalité, plafonds de risque et protections réseau jamais assouplis.
- Maximum quatre agents actifs. Les 100 capacités sont des composants légers et non des agents autonomes.
- Relire CLAUDE.md, AGENTS.md et SPEC.md sur main. Aucune nouvelle mission autonome hors Sprint 5.
- Maximum six fichiers par PR. Aucun service permanent ni observation prospective sans autorisation spécifique.
- Sources publiques autorisées uniquement, avec respect des quotas et conditions d'accès.
- La période réservée déjà consommée n'est plus un holdout aveugle.
- Objectif expérimental : +1,30 % net hebdomadaire géométrique sur 52 semaines, drawdown maximal cible de 10 %. Ne jamais forcer ces résultats.
- Windows 11, Python 3.12, i5-14400F, RTX 4060 Ti 8 Go, 16 Go RAM. Modèles légers, chargement à la demande, jamais plusieurs LLM lourds simultanément.

Définition de fait — chaque capacité reçoit dans le registre versionné :
1. Statut vérifié : EXISTANTE_ET_TESTÉE, PARTIELLE, ABSENTE, BLOQUÉE_DONNÉES, EXPÉRIMENT_REJETÉ ou EXPÉRIMENT_VALIDÉ_PROVISOIRE.
2. Fichiers et lignes exactes.
3. Hypothèse falsifiable, entrées, sorties, comparaison et métriques.
4. Contrat de données : provenance, licence, horodatages, disponibilité effective, couverture, trous, identifiants et latence.
5. Tests déterministes : cas normal, absent, ancien, dupliqué, contradictoire, incident réseau, absence de fuite temporelle et invariants de sécurité.
6. Tests comparatifs et ablation : mêmes périodes, capital, coûts et univers, intervalles de confiance, correction des essais multiples.
7. Verdict : utilité technique, avantage historique, avantage prospectif, rejet ou non évaluable.
Toutes les capacités sont initialement À AUDITER. Ne pas les supposer absentes. Aucune intégration à la décision fictive sans approbation humaine.

Catalogue (priorité normale sauf HAUTE) :

### Actualités et événements — première preuve : horodatage, source et effet mesuré après disponibilité effective
- C001 — Mesure des délais publication→réception→marché. [HAUTE]
- C002 — Classification automatique des événements.
- C003 — Association sûre des nouvelles aux actifs. [HAUTE]
- C004 — Notation de provenance et fiabilité des sources.
- C005 — Déduplication inter-sources. [HAUTE]
- C006 — Surprise chiffrée contre consensus antérieur. [HAUTE]
- C007 — Études de réactions aux événements. [HAUTE]
- C008 — Décroissance temporelle des alertes et vetos.
- C009 — Détection d'événements systémiques.
- C010 — Attente d'une réaction initiale et contrôle de liquidité.

### Macroéconomie et inter-marchés — première preuve : publication antérieure et référence comparée sans fuite
- C011 — Surprises d'inflation.
- C012 — Surprises emploi et révisions.
- C013 — Décisions et communications des banques centrales.
- C014 — Mouvements des rendements obligataires.
- C015 — Contexte de force du dollar.
- C016 — Indicateurs de stress actions, dont VIX.
- C017 — Propagation sectorielle actions→crypto.
- C018 — Flux publics de stablecoins.
- C019 — Flux ETF horodatés.
- C020 — Calendrier des événements à risque.

### Régimes de marché — première preuve : classement de régime et abstention hors échantillon
- C021 — Classification tendance/range. [HAUTE]
- C022 — Breadth et participation des altcoins.
- C023 — Force de tendance ADX.
- C024 — Régimes de volatilité. [HAUTE]
- C025 — Détection des ruptures de régime.

FIN PARTIE 1/4 : 25 capacités transmises.

## PARTIE 2/4

### Régimes de marché — suite — première preuve : classement de régime et abstention hors échantillon
- C026 — Corrélations dynamiques BTC/altcoins. [HAUTE]
- C027 — Funding rate en observation uniquement.
- C028 — Open interest et liquidations en observation.
- C029 — Effets horaires et calendaires.
- C030 — Régime d'abstention. [HAUTE]

### Analyse technique — première preuve : indicateur causal et comparaison avec momentum simple
- C031 — Pivots confirmés causalement.
- C032 — Trendlines avec qualité d'ajustement.
- C033 — Zones support/résistance.
- C034 — Retest de cassure.
- C035 — Identification des fausses cassures.
- C036 — Amplitude des ranges.
- C037 — Compression de volatilité.
- C038 — Divergences RSI/prix.
- C039 — Épuisement de mouvement.
- C040 — Confluence multi-timeframe authentique.

### Microstructure et volumes — première preuve : niveau de carnet, tailles et coûts réellement accessibles
- C041 — Spread achat/vente mesuré.
- C042 — Déséquilibre de carnet multi-niveaux.
- C043 — Pression des transactions agressives. [HAUTE]
- C044 — Volume delta cumulatif.
- C045 — Instabilité des ordres affichés.
- C046 — Impact du notional sur l'exécution. [HAUTE]
- C047 — Glissement conditionnel à la volatilité.
- C048 — Profondeur de revente exploitable. [HAUTE]
- C049 — Anomalies compatibles avec le wash trading.
- C050 — Contrôle de cohérence inter-plateformes.

FIN PARTIE 2/4 : total cumulé 50 capacités.

## PARTIE 3/4

### On-chain et Memecoins — première preuve : token et pool identifiés, veto inchangé et revente vérifiée
- C051 — Historique âge et activité du pool.
- C052 — Historique de concentration des détenteurs.
- C053 — Surveillance des verrous de liquidité.
- C054 — Routes et volumes de revente.
- C055 — Restrictions et frais de transfert.
- C056 — Flux des grands détenteurs.
- C057 — Retraits de liquidité rapides.
- C058 — Portefeuilles liés au créateur, preuves vérifiées.
- C059 — Performance point-in-time des smart wallets.
- C060 — Cohérence de prix et liquidité multi-pools.

### Exécution et sorties fictives — première preuve : règles d'exécution identiques au simulateur causal
- C061 — Frais point-in-time par instrument. [HAUTE]
- C062 — Minimum de notional, lot et tick. [HAUTE]
- C063 — Exécutions partielles simulées.
- C064 — Modèles de latence et d’exécution.
- C065 — Causalité stricte décision→cotation ultérieure.
- C066 — Refus des cotations périmées.
- C067 — Stops avec gaps et liquidité insuffisante. [HAUTE]
- C068 — Prises de bénéfices partielles.
- C069 — Limite de durée des positions.
- C070 — Filtre d'avantage espéré net de coûts.

### Gestion des risques — première preuve : invariants des plafonds de risque et scénarios extrêmes
- C071 — Frein perte quotidienne.
- C072 — Frein perte hebdomadaire.
- C073 — Exposition plafonnée par stratégie.
- C074 — Exposition plafonnée par clusters corrélés. [HAUTE]
- C075 — Dimensionnement sous incertitude plafonné.

FIN PARTIE 3/4 : total cumulé 75 capacités.

## PARTIE 4/4

### Gestion des risques — suite — première preuve : invariants des plafonds de risque et scénarios extrêmes
- C076 — Ciblage de volatilité sans levier.
- C077 — Réduction d'exposition après drawdown.
- C078 — Stress de liquidité.
- C079 — Scénarios de pertes extrêmes. [HAUTE]
- C080 — Réserve minimale de liquidités.

### Apprentissage et orchestration — première preuve : calibration temporelle, robustesse et absence de promotion automatique
- C081 — Étiquettes et features causales.
- C082 — Calibration probabiliste. [HAUTE]
- C083 — Abstention par incertitude. [HAUTE]
- C084 — Détection de dérive des données. [HAUTE]
- C085 — Spécialistes légers par régime.
- C086 — Déduplication des caractéristiques corrélées.
- C087 — Ensembles légers et benchmark.
- C088 — Analyse textuelle du contexte à la demande.
- C089 — Anomalies de marché et incertitude.
- C090 — Champion/challenger paper-only. [HAUTE]

### Preuves et exploitation — première preuve : reproductibilité SHA et mesure statistique indépendante
- C091 — Univers point-in-time sans biais de survie. [HAUTE]
- C092 — Registre immuable d'essais et empreintes.
- C093 — Validation temporelle purgée avec embargo.
- C094 — Correction des essais multiples. [HAUTE]
- C095 — Tests d'ablation par capacité. [HAUTE]
- C096 — Intervalles de confiance robustes avec dépendance temporelle.
- C097 — Stress frais/spread/glissement. [HAUTE]
- C098 — Faisabilité avec capital fictif de 30 € et produits UE.
- C099 — Écart entre simulation et cotation accessible. [HAUTE]
- C100 — Observation prospective figée sur 52 semaines, dont le démarrage exige une autorisation distincte. [HAUTE]

### État de départ à vérifier (audit initial sur main 1bb22879950b3d9183f84b714d40dbdf66cd7c86 ; lignes à actualiser sur le HEAD courant)
- sentinelle/scalp/flash.py:1-16,34-66,69-84 : huit flux, horodatage publication/réception, classification déterministe, vetos. Archive trop jeune pour prouver son apport financier.
- sentinelle/scalp/veille.py:1-8,39-65 : agrégation quotidienne et vetos.
- sentinelle/scalp/watchers.py:1-10,32-37,45-71 : pullback, breakout, capitulation, squeeze.
- sentinelle/scalp/features.py:12-17,20-35,42-84 : ATR, tendance, volume, flux taker, contexte BTC.
- sentinelle/scalp/brain.py:33-87 : IA légère, calibration et abstention.
- sentinelle/scalp/signaux.py et docs/scalp/dossier-evaluation-independante.md:136-194 : onze familles déjà étudiées.
- docs/scalp/dossier-evaluation-independante.md:120-132,222-234 : limites des coûts historiques, biais de survie, essais multiples, petit capital, instruments UCITS et absence de période historique aveugle.
- Prendre en compte #147 (sentinelle/scalp/essais.py et tests) pour C094. Après correction, aucune des onze familles n'est significative à 5 %.

### Ordre de construction dans le Sprint 5
Lot 0 — Autorisation et référence :
- Relire CLAUDE.md, AGENTS.md et SPEC.md sur le dernier main. Identifier les capacités déjà livrées.
- Rédiger et fusionner un addendum ciblé du Sprint 5 cadrant explicitement C001–C100 avant toute implémentation qui en dépend.
- Créer un registre de couverture versionné : statut, fichiers, lignes, tests, dépendances et priorité.
- Figer le baseline : SHA moteur, rendement géométrique net, drawdown, coûts, nombre de transactions, semaines positives, tests et provenance.
- Conserver les résultats négatifs de #140 et #146 ainsi que les conclusions de #147. Ne pas inventer une date d'achèvement des 100 capacités.
Lot 1 — Fiabilité des mesures et exécution : priorités C091, C094, C095, C061, C062, C097, C099. Univers point-in-time, essais multiples, ablations, coûts historiques, minima d'ordre, écarts simulation/observations. Vérifier LUNA/MATIC, conversions et ETF européens dans le périmètre autorisé. Tests de propriétés et rapport d'invariants.
Lot 2 — Actualités, régimes et abstention : priorités C001, C003, C005, C006, C007, C021, C024, C026, C030, C082, C083, C084. Renforcer flash.py sans réécrire les huit sources. Actualités positives en shadow uniquement, jamais signal automatique d'achat. Comparaison sans actualités / actualités et vetos sur une archive adéquate (l'actuelle ne suffit pas). Tester régimes, calibration, abstention, dérive.
Lot 3 — Microstructure et maîtrise du risque : priorités C043, C046, C048, C067, C074, C079. Pression acheteuse/vendeuse, impact, revendabilité, gaps, corrélations, krachs. Tests A/B hors ligne avec cotations datées et données manquantes explicites. Ne pas inventer de données historiques.
Lots suivants : selon dépendances et valeur marginale démontrée. Ne pas multiplier artificiellement les PR. Ne pas intégrer cent filtres simultanément. Une capacité rejetée peut être abandonnée comme filtre.

Répartition : Claude Code (audit, inventaire, cadrage, registre, tests techniques, corrections mesurables autorisées) ; GPT Work (audit indépendant, mercredi 25 novembre 2026). Fichiers réservés à Work : tools/evaluation/, tests/test_memecoin_evaluation*.py, docs/evaluation/. Le propriétaire décide de toute observation prospective, promotion de capacité et extension des sources.

Rapport d'acceptation de chaque PR : numéro, SHA, ≤ 6 fichiers ; trois CI vertes sur le dernier commit (Ubuntu, Windows, Chromium) ; IDs Cxxx ; fichiers et lignes ; état avant/après et tests ; commande Windows 11 ; Windows validé seulement après exécution Windows effective ; résultats négatifs conservés et protocoles préenregistrés ; gel #54/#55 et invariants paper-only, Guardian, Risk Commander, causalité ; rendement, IC, drawdown, coûts, actifs, périodes, seuils d'abstention ou NON MESURÉ ; aucun ordre réel, clé, service permanent ou promotion automatique.

Règle d'arrêt : un indicateur au gain net non significatif, instable ou issu d'une période déjà optimisée n'est pas activé dans la décision. Une capacité de sécurité peut être conservée malgré un rendement inférieur. La cible +1,30 % n'est pas une fonction de perte à optimiser sur les jeux d'évaluation.

FIN DU DOSSIER — 100 CAPACITÉS.
