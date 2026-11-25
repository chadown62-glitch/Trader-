# Dossier d'évaluation indépendante : moteur court terme (Sentinelle AI)

Ce dossier, préparé par Claude Code le 24 novembre 2026, est destiné à GPT Work. Il permet de rejouer, vérifier et contester chaque résultat du chantier « Moteur court terme » (`SPEC.md`, Sprint 5).

- **Statut** : simulation exclusive. Aucun ordre réel, aucune clé, aucun levier, aucune vente à découvert.
- **Verdict actuel** : **négatif**. Aucune stratégie n'atteint +1,30 % par semaine en moyenne géométrique avec une pire baisse d'au plus 25 %, même sur un historique déjà consulté. Aucun résultat n'a encore été confirmé sur une période aveugle.

---

## 1. Références à vérifier

### Commits des règles fixées avant calcul

Chaque préenregistrement a été poussé sur GitHub avant le calcul correspondant.

| Objet | Commit | Poussé (UTC) |
|---|---|---|
| Système figé (`frozen-system-v1.json`, SHA-256 `2a508b2b…01bb`) | `6ba92be67b18796c00b382f7125eddad3a50692d` | 23/11, 20:47 |
| Moteur unique v1 | `019c8a65e27ad6f6c101d8038497ad75c90ff4c1` | 24/11, 08:20 |
| Recherche v1 (grille, sélection hors échantillon) | `caf34214a866edf004283728762f22d497b20b8a` | 24/11, 08:41 |
| Contrôle du biais de survie (recherche v1b) | `1b24030bea62e3d24766a2d156de31af9344ac03` | 24/11, 08:48 |
| Étude des familles de signaux et définition de la stabilité | `7cf26540aee1d36446b1186b70bd5919189bda34` | 24/11, 12:05 |
| Correctif de recotation (LUNA) | `45235463222e954b91330eaad827cca780208e04` | 24/11, 12:13 |
| Relance des signaux après correctif, diagnostic F7 a posteriori | `874e42c38d94bc327d093e5a27130684f209f466` | 24/11, 12:14 |

### Empreintes SHA-256 des modules mesurés

Version du 24 novembre 2026, après le correctif de recotation.

| Fichier | SHA-256 |
|---|---|
| `sentinelle/scalp/signaux.py` | `caf7696771a622241f355fd5a624c085464511ed6a75d71f9e37353ac1792412` |
| `sentinelle/scalp/recherche.py` | `712607eb85657d470fc0d3f293b34e6ddb7d1b3d28c0c46e22447e93fe756ab4` |
| `sentinelle/scalp/moteur.py` | `2c6c060cbc978700aff616c6c7b5381630a25ede8fbd125c587d96fa4751c191` |
| `sentinelle/scalp/core.py` | `b2485ba64e2fcb4024b8cd3e8ab99bedc9e085e36bf0b8d003e6f38ac5c4f4de` |
| `sentinelle/scalp/actifs.py` | `c31ada257b484e5420e051d373bb443380f661db60d6b3213681c8827958ec99` |
| `sentinelle/scalp/data.py` | `220f6b58771259c5d9cf6c6fcd97266d7d412aae940421de61c6619c3a2afd83` |
| `research/scalp-v1/live-costs-2026-11-23.json` | `f390cc7f2938ea86a96e7423e7697ec2ed2109e7ab74cd61bd62bbeefea3d205` |

---

## 2. Données et empreintes

### Cryptos

- **Source** : bougies journalières de Binance, publiées sur `data.binance.vision`, paires USDT.
- **Archives** : mensuelles, plus des archives journalières pour novembre 2026, jusqu'au 22 novembre 2026.
- **Contrôle d'intégrité** : chaque archive est vérifiée par la somme de contrôle SHA-256 publiée par Binance. Le cache est revérifié à chaque lecture.
- **Manifeste** : `research/scalp-v1/signaux-v1.json`, clé `donnees.crypto`. Il couvre 4 281 archives et 42 paires, avec pour chacune son empreinte publiée.
- **Trous jamais comblés** :
  - **LUNA** est coupée au 31 mai 2022 : après 17 jours sans cotation, un autre actif a été recoté sous le même nom.
  - **FTT** a 9 mois d'archives absentes (décembre 2022 → août 2023), soit 310 jours sans cotation. Elle reprend ensuite à un cours comparable et est gardée.
  - Cryptos retirées de la cote, prises jusqu'à leur dernier mois publié : EOS (2025-05), XMR (2024-02), MATIC (2024-09), WAVES et OMG (2024-06), TON (2026-06).

### Fonds indiciels

- **Source** : cours ajustés publiés par Yahoo Finance (SPY, EFA, EEM, IEF, TLT, GLD, DBC, VNQ).
- **Empreintes** : SHA-256 et date de téléchargement de chaque fichier, dans la clé `donnees.fonds_yahoo`.
- **Précaution** : Yahoo ne publie pas d'empreinte. Une copie téléchargée plus tard peut différer (cours ajustés révisés).

### Coûts

- **Mesure** : instantané unique du carnet d'ordres public de Binance (USDC), le 23 novembre 2026 à 20:47 UTC, enregistré dans `live-costs-2026-11-23.json`.

### Actualités

- **Archive** : SQLite en ajout seul, ouverte le 23 novembre 2026, avec une veille de 20 minutes publiée dans `actualites-2026-11-23.json`.
- **Limite** : il n'existe **aucun historique d'actualités**. Leur contribution n'est pas mesurable sur le passé.

---

## 3. Périodes : ce qui a déjà été consulté

| Période | Usage |
|---|---|
| A : septembre 2024 → août 2026 | Mise au point de v1 à v13 et du moteur rapide (essais du 23 novembre). Consultée. |
| C : 1er septembre → 22 novembre 2026 | Évaluation unique du système figé (#140, verdict « non confirmé »). Consultée ensuite par #143, #144 et l'étude des signaux. |
| B : 2018 → août 2024 | Inédite jusqu'au 24 novembre. Consultée depuis par le moteur unique (#143), la recherche v1 (#144) et l'étude des signaux. |

**Il ne reste aucune période historique aveugle.** Tous les résultats sur 2018 → 2026 sont un historique consulté. Ils peuvent orienter, mais ne prouvent rien.

**Seule période aveugle possible** : l'observation prospective, à partir du 24 novembre 2026, d'une configuration figée avant son début. Elle n'est pas démarrée : la décision revient au propriétaire.

---

## 4. Registre des essais

`research/scalp-v1/trials.jsonl` : ajout seul, essais négatifs, invalidés et corrections compris.

- 7 préenregistrements et 2 corrections consignées.
- La première passe de l'étude des signaux, faite avant le correctif LUNA, est gardée (12:11:13Z).
- Les résultats remplacés restent dans le registre avec leur date.

---

## 5. Audit des résultats antérieurs

Les rendements hebdomadaires sont recalculés en **moyenne géométrique**, à partir du rendement total. Les comptes rendus antérieurs donnaient des moyennes arithmétiques, plus flatteuses.

| Stratégie | Période | Semaine arithm. | **Semaine géom.** | Pire baisse | Remarque |
|---|---|---|---|---|---|
| v10 | A | +1,28 % | **+1,00 %** | 35 % | +199 % la 1re année, −3 % la 2e |
| v10 | B | +1,25 % | +0,91 % | 44 % | univers de 8 cryptos choisi en 2026 |
| v10, univers à la date | 2020 → 2026 | +0,51 % | **+0,28 %** | 50 % | biais de survie retiré |
| v13 | A / B | +0,60 % / +0,57 % | +0,53 % / +0,52 % | 20 % | |
| Système figé (fond 70 % + rapide 30 %) | C | | −0,60 % | 9 % | −6,95 %, « non confirmé » |
| Sélection R1, toutes années, univers 2026 | 2020 → 2026 | +0,88 % | +0,77 % | 30 % | biais de survie |
| **R1, toutes années, univers à la date** | 2020 → 2026 | +0,49 % | **+0,42 %** | 26 % | |
| **R2, toutes années, univers à la date** | 2020 → 2026 | +0,48 % | **+0,42 %** | 21 % | |
| R1, deux dernières années, univers à la date | 2020 → 2026 | +0,75 % | +0,62 % | 38 % | |
| Moteur rapide r1 (repli et cassure) | A | −0,36 % | −0,40 % | 43 % | 1 005 trades, 26 % gagnants |
| Moteur rapide r2 (cassures, stop suiveur) | A | +0,14 % | +0,13 % | 9 % | règle fixée après un échec ; univers 2026 |
| Rapide r2, memecoins seuls | A | +0,16 % | +0,16 % | 4 % | biais de survie extrême (memecoins survivants) |
| Moteur unique U1 (#143) | 2018 → 2026 | +0,22 % | +0,20 % | 21 % | 355 transferts, ≈ 534 € de frais pour 1 000 € |
| Moteur unique U0, parts fixes | 2018 → 2026 | +0,22 % | +0,20 % | 18 % | |
| Crypto 35 % + fonds G2 65 % (#141) | sept. 2024 → nov. 2026 | +0,29 % | +0,27 % | 11 % | poche crypto figée, période réservée incluse |

---

## 6. Défauts de méthode résiduels

| Défaut | État |
|---|---|
| **Biais de survie** | Mesuré (#144). Sur 2020 → 2026, il gonflait v10 de +0,28 % à +0,94 % par semaine (géométrique). Il reste dans #137, #138, #141 et #143 (univers de 8, 14 ou 20 cryptos choisi en 2026). L'étude des signaux et #144 utilisent l'univers à la date. Résidu : la liste des 42 candidates a elle-même été dressée en 2026, mais elle inclut les échecs connus (LUNA, FTT, EOS, WAVES…). |
| **Identité historique des actifs** | Corrigé (#145) : LUNA recotée. Non traités : un changement de nom ou de pas de cotation sans saut de prix ; le passage de MATIC à POL (la série MATIC s'arrête en septembre 2024, POL n'est pas suivi). |
| **Univers à chaque date** | Top 8 ou top 10 du volume Binance des 30 jours précédant le mois. Limite : le volume de 2018-2019 est en partie gonflé par des échanges fictifs ; Binance seule. |
| **Coût historique** | Il est mesuré une seule fois (novembre 2026) puis appliqué à tout l'historique. En 2018, les écarts entre prix d'achat et de vente étaient plus larges sur les cryptos secondaires. Un test sous stress (0,25 % par côté) est publié. |
| **Regard vers l'avenir** | Testé : les signaux ne changent pas si l'avenir change (test automatique). Exécution à l'ouverture suivante. Les décisions des fonds sont appliquées le jour de bourse suivant. Limite : un stop suiveur intrajournalier est exécuté exactement au prix du stop ; en krach réel, le glissement serait pire. |
| **Surapprentissage** | v1 à v13 ont été mis au point sur A. La grille de 3 888 réglages et les 4 variantes de sélection constituent des essais multiples. L'étude des signaux utilise des réglages standard fixés d'avance, mais le choix des combinaisons dépend des résultats de l'étape 1 sur les mêmes données. Aucune correction formelle pour essais multiples n'est faite (ce qui reste à faire). |
| **Dépendance entre marchés** | Les cryptos suivent fortement le BTC : en 2018 et en 2022, l'univers a perdu 76 % et 82 %. En 2022, les fonds ont baissé en même temps. Les intervalles de confiance utilisent des blocs de 20 jours, mais ne corrigent pas la dépendance entre cryptos. |
| **Petit capital et instruments européens** | Les fonds américains (SPY…) ne sont pas achetables par un particulier européen ; les équivalents UCITS ne sont pas testés. Le risque de change euro/dollar et les impôts ne sont pas comptés. Les frais du petit capital (1 000 €) sont estimés, non mesurés. |
| **Moyenne arithmétique ou géométrique** | Corrigé dans ce dossier : la géométrie est désormais la mesure de référence. |

---

## 7. Étude des familles de signaux (`signaux-v1`)

### Protocole (préenregistré, `7cf2654`)

- **Univers** : chaque mois, les 10 paires USDT les plus échangées sur les 30 jours précédents, parmi les 42 candidates.
- **Exécution** : signal calculé sur les données jusqu'à la clôture de la veille ; position prise à l'ouverture.
- **Taille et coûts** : 1/10 du capital par actif ; coûts de 0,125 % par côté (stress : 0,25 %).
- **Période** : 2018 → 22 novembre 2026, **historique consulté**.

### Étape 1 : chaque famille seule, mesurée sur l'écart avec l'univers

| Famille | Écart quotidien (IC 95 %) | Seule : semaine géom. / an / pire baisse | Réussite |
|---|---|---|---|
| F1 élan sur 28 jours | **+0,095 % [+0,019 ; +0,181]** | +0,53 % / +32,0 % / 60 % | ✅ |
| F2 tendance (EMA 50) | +0,060 % [−0,012 ; +0,137] | +0,34 % / +19,6 % / 74 % | ✗ |
| F3 ligne de tendance (pente et R²) | +0,071 % [−0,058 ; +0,206] | +0,28 % / +15,6 % / 38 % | ✗ |
| F4 cassure confirmée | +0,014 % [−0,075 ; +0,104] | +0,46 % / +27,2 % / 49 % | ✗ |
| F5 support | −0,025 % [−0,112 ; +0,066] | +0,01 % / +0,8 % / 61 % | ✗ |
| F6 volume et pression acheteuse | **+0,169 % [+0,034 ; +0,310]** | +0,12 % / +6,7 % / 42 % | ✅ |
| F7 régime BTC (moyenne de 200 jours) | 0 par construction | +0,24 % / +13,2 % / 70 % | ✗ |
| F8 élan ajusté du risque | **+0,156 % [+0,029 ; +0,294]** | +0,43 % / +25,1 % / 40 % | ✅ |
| F9 volatilité calme | +0,012 % [−0,028 ; +0,050] | +0,04 % / +1,9 % / 74 % | ✗ |
| F10 multi-horizon (hebdomadaire et journalier) | +0,023 % [−0,057 ; +0,106] | +0,28 % / +15,5 % / 73 % | ✗ |
| F11 cassure filtrée par le volume | +0,038 % [−0,060 ; +0,140] | +0,39 % / +22,7 % / 46 % | ✗ |

**Lecture de l'étape 1**

- **Seuls l'élan, l'élan ajusté du risque et la pression acheteuse** montrent un avantage statistiquement positif avant frais.
- **Familles sans avantage démontré** : lignes de tendance, cassures, supports, multi-horizon et filtres de faux signaux.
- **F6 et les frais** : son avantage est largement consommé par les frais (+6,7 % par an net).
- **Limite pour F7** : l'écart avec l'univers ne peut pas mesurer un filtre de marché, qui allume tout l'univers à la fois.
- **Diagnostic temporel de F7** (ajouté a posteriori, consigné avant la relance) : l'univers gagne +0,116 % par jour de plus quand le filtre est vrai, avec un IC à 95 % de [−0,177 ; +0,417]. **Ce n'est pas significatif.**

### Étape 2 : combinaisons, limitées aux familles réussies (F1, F6, F8)

| Combinaison | Semaine géom. | Par an | Pire baisse | Semaines + (non nulles) | Médiane sur 52 sem. | Fenêtres 52 sem. > 0 | 2018 / 2022 |
|---|---|---|---|---|---|---|---|
| C_VOTE (≥ 2 sur 3), 1/10 par place | +0,46 % | +27,1 % | 48 % | 46 % | +0,46 % | 75 % | −22 % / −31 % |
| C_VOTE, volatilité visée 30 % | +0,24 % | +13,5 % | 19 % | 48 % | +0,28 % | 78 % | −8 % / −13 % |
| **C_VOTE, volatilité visée, avec fonds (35/65)** | **+0,20 %** | **+11,2 %** | **13 %** | **59 %** | +0,25 % | **83 %** | −5 % / −10 % |
| Même chose, petit capital (1 000 €) | +0,18 % | +9,8 % | 14 % | 59 % | +0,22 % | 81 % | −6 % / −11 % |
| C_ET (3 sur 3), 1/10, avec fonds | +0,18 % | +9,6 % | 14 % | 58 % | +0,22 % | 73 % | −5 % / −10 % |

### Références (même période, mêmes mesures)

| Référence | Semaine géom. | Par an | Pire baisse |
|---|---|---|---|
| CASH | 0 % | 0 % | 0 % |
| BTC | +0,32 % | +18,3 % | 81 % |
| Univers à poids égaux | −0,08 % | −4,1 % | 90 % |
| SPY | +0,23 % | +12,9 % | 34 % |
| 60/40 | +0,15 % | +8,4 % | 21 % |
| Fonds G2 | +0,18 % | +9,8 % | 15 % |

### Cible de +1,30 % par semaine

- **Atteinte par aucune stratégie**, références comprises.
- **Meilleure part de fenêtres de 52 semaines à au moins +1,30 %** : 21 %, pour F1 seul, avec une pire baisse de 60 %.
- **Verdict préenregistré : négatif.**

### Transactions simulées et motifs d'abstention

Clé `etape2.<combinaison>` du fichier `signaux-v1.json` :
- `transactions` : chaque entrée et chaque sortie, avec sa date, l'actif et sa part du capital (2 232 lignes pour C_VOTE).
- `abstention` : par année, le nombre de jours en position, « aucun signal », « régime défavorable » et « univers vide ».

---

## 8. Changements justifiés et non justifiés

**Justifiés par les mesures**
- Un univers défini à chaque date.
- L'élan et l'élan ajusté du risque comme signaux.
- Une taille de position à volatilité visée, qui divise la pire baisse par plus de deux.
- Le mélange avec des fonds diversifiés : plus de semaines positives, moins de baisse.
- La moyenne géométrique comme mesure.
- Le correctif de recotation.

**Non justifiés**
- Lignes de tendance, cassures, supports, filtres multi-horizon et filtres de faux signaux : aucun avantage démontré.
- Transferts entre poches du moteur unique : leurs frais annulent leur gain.
- Memecoins : coûts élevés et biais de survie extrême.
- Toute augmentation de risque pour viser 1,30 %.

---

## 9. Ce qui reste à démontrer

1. **Une période aveugle.**
   - Observation prospective fictive d'une configuration figée avant son début (SHA publié), d'au moins 8 semaines et 150 trades.
   - Critères de `SPEC.md` : espérance positive à 95 %, pire baisse ≤ 10 %, au moins 60 % de semaines positives, meilleure que CASH.
   - Évaluation par GPT Work.
2. **L'outil d'observation de C_VOTE.** La vue du jour existe pour le moteur unique (#143), pas encore pour C_VOTE.
3. **La correction pour essais multiples** de la grille et des variantes de sélection.
4. **Les coûts réels d'un petit capital et les équivalents européens (UCITS)** des fonds.
5. **La contribution des actualités**, qui ne se mesurera que sur l'archive prospective.
6. **Le glissement des sorties en krach.**

**Aucune reproductibilité sur le marché réel n'est garantie. L'objectif n'est pas atteint.**

---

## 10. Reproduction (Windows 11, Python 3.12)

```powershell
git checkout main
git pull
python -m pytest tests/test_scalp_signaux.py tests/test_scalp_recherche.py tests/test_scalp_moteur.py
python -m sentinelle.scalp.signaux --trials ""
python -m sentinelle.scalp.recherche --trials ""
python -m sentinelle.scalp.recherche --survie --trials ""
python -m sentinelle.scalp.moteur --trials ""
```

- **Données** : le premier lancement télécharge environ 4 300 archives publiques vérifiées dans `data/scalp-cache` (quelques minutes). Les lancements suivants relisent le cache, revérifié à chaque lecture.
- **Registre** : `--trials ""` évite d'écrire dans le registre.

---

## 11. Catalogue C001–C100 : mesures des 24 et 25 novembre 2026

Chaque étude a été **préenregistrée et poussée avant calcul** (registre `research/scalp-v1/trials.jsonl`). Elle a ensuite été fusionnée après réussite des trois contrôles obligatoires : pytest Ubuntu, pytest Windows et Chromium.

Toutes les mesures portent sur l'historique 2018-01-01 → 2026-11-22, **déjà consulté**. Les comparaisons utilisent l'outil d'ablation C095 :
- mêmes jours, mêmes coûts et même univers ;
- intervalle à 95 % par blocs de 4 semaines ;
- correction de Holm sur chaque lot.

| PR (fusion) | Préenregistrement | Capacités | Résultat principal |
|---|---|---|---|
| #149 (`1212727`) | `1cb0a1f` | C095, C097, C076, C077 | Outil d'ablation et stress de coûts. Aucune capacité n'améliore le rendement après Holm. Volatilité visée : pire baisse de 31 % à 13 %. |
| #150 (`a14e3f1`) | `f19b1bc` | C062, C098 | À 30 €, 4 % des entrées de B1 et 73 % de celles de B0 sont faisables. Faisable à partir d'environ 1 000 €. Fonds UCITS NON MESURÉS. |
| #151 (`c5af5ca`), #152 (`af64f41`) | — | registre, C092 | Exécution effective des tests cités distinguée du simple référencement. Empreintes vérifiées au commit du gel. |
| #153 (`fd943be`) | `8f582a3` | C091 | 5 renommages officiels vérifiés à la minute près, 4 reliés, VEN → VET refusé. Effet non significatif. |
| #154 (`bc69191`) | `7e71882` | C099 | Écart à l'ouverture simulée : 1 à 1,7 pb en moyenne la première minute, p95 de 20 à 24 pb. Écart achat-vente NON MESURÉ. |
| #155 (`cb0cc47`) | `ab5df53` | C061 | Barème daté officiel : écart inférieur à 0,001 %/semaine. Sans BNB : −0,013 %/semaine sur B0. |
| #156 (`14b328d`) | `f4e7d8f` | C021, C024, C026, C030 | A1 « BTC en range » : pire baisse de B0 de 60 % à 23 %, rendement −0,05 %/semaine géométrique (non significatif), mieux que son témoin. Candidat, non indépendant. |
| #157 (`d0d744c`) | `00908bb` | C082, C083, C084 | Probabilités de l'IA principale moins bonnes que le taux appris : −3,3 % [−6,1 ; −0,5]. Recalage isotonique trop confiant. |
| #158 (`c930c19`) | `e51a306` | C007, C001 | FOMC et CPI à l'heure officielle : variation du BTC ×2 à ×3,5 (Holm p < 0,001). Sens non prévisible sans la surprise (C006 bloquée). |
| #159 (`a0eb040`) | `d5ed57a` | C079, C074, C067 | Pires semaines du BTC : B1 ≥ −4,9 %. Plafond par groupe corrélé rejeté. Stops avec écart : +0,055 R en moyenne. |
| #160 (`4831790`) | `2198f9d` | C022, C023, C025, C031, C034, C038 | Aucune famille technique n'améliore B0 après Holm (verdict négatif). |
| #161 (`695774e`) | `4e10df7` | C014, C015, C016, C017 | Abstentions selon les taux, le dollar, le VIX ou le Nasdaq-100 : aucun gain après Holm. Seul lien significatif : légère inversion du BTC le lendemain d'une séance du Nasdaq-100 (rang −0,056, Holm 0,032), non exploitée. |
| #162 (`a3a5247`) | `3512fab` | C018, C029 | Offre de stablecoins (série non point-in-time, borne optimiste) : aucun effet. Liquidités le week-end : −0,67 %/semaine sur B0. Aucun jour distinct après Holm. |
| #163 (`7295149`) | `1fd27f7` | C036, C039 | Amplitude du range : moins bonne que la volatilité réalisée pour prévoir l'ampleur. Capitulations : aucun gain, pire baisse de B0 portée à 66 %. |
| #164 (`c797ec4`) | `d392974` | C085, C086 | IA principale : spécialistes par régime et retrait des redondances sans gain significatif ; les probabilités restent moins bonnes que le taux appris. |

**Inventaire après ces mesures** (registre `research/scalp-v1/registre-capacites.json`) :

| Statut | Nombre |
|---|---|
| Existantes et testées | 43 |
| Partielles | 29 |
| Absentes | 4 |
| Bloquées (données) | 2 |
| Expériences rejetées | 22 |
| Validées | **0** |

**Couverture des tests.** 95 capacités ont une couverture « constatée » : toutes leurs fonctions citées ont réellement tourné pendant un test réussi, rejoué seul sous traceur avec un fil de calcul mathématique par processus (contre 55 au premier inventaire). Aucune n'est partielle ni non constatée ; 5 sont sans preuve (C019 et C045 bloquées ; C049, C058 et C100 absentes). Plus aucune capacité n'est « à confirmer » (détail : clé `mesure_execution`, Python 3.12.3, commit `97126c0`). Les derniers chemins non exécutés ont reçu un test : `flux.supply_lead`, `epuisement.amplitude_study` (#166), `costs.measure.stress_states`, le chemin EVM `_goplus_evm` (#167) et `signaux.load_bars` sur réseau simulé, avec la correction de `data.read_month` sous pandas 3.

**Défauts de la section 6, mis à jour :**
- **Identité des actifs** : traitée (#153), sauf deux cas. VEN → VET (1 pour 100) reprend 22,8 % au-dessus de l'équivalent et n'est pas relié. BCC → BCHABC est une scission, également non reliée. Le raccord est désactivé par défaut, ce qui laisse les résultats publiés reproductibles.
- **Coût historique** : frais datés mesurés (#155). Glissement à l'ouverture mesuré (#154). Stress jusqu'à ×3 plus 0,10 % (#149).
- **Glissement des sorties** : mesuré sur 7 624 stops minute (#159). La liquidité insuffisante à la sortie reste NON MESURÉE.
- **Surapprentissage et essais multiples** : correction de Holm, Benjamini-Hochberg et DSR (#147), puis Holm dans chaque lot.
- **Petit capital** : mesuré (#150). Les fonds UCITS et la fiscalité restent NON MESURÉS.

**Points soumis au propriétaire et à GPT Work :**
- **A1** remplit le critère préenregistré, mais elle reprend le filtre BTC des versions v10 sur un historique consulté. Elle n'est donc pas une preuve indépendante. Toute intégration attend une validation humaine et une observation prospective autorisée.
- **Recalage des probabilités de l'IA principale** : le défaut est démontré, rien n'est modifié. Une correction devra être préenregistrée et validée.
- **Inversion après le Nasdaq-100** (#161) : lien faible (0,3 % de la variance) de sens contraire à l'hypothèse du catalogue. Il n'est pas testé comme règle : il faudrait un protocole distinct, préenregistré, sur une période non consultée.
- **IA principale** (#157, #164) : son défaut de calibration ne vient ni des caractéristiques redondantes ni de l'absence de spécialisation par régime.
- **Flotte de 12 agents** : l'objectif est de 4 agents principaux actifs. Rien n'a été supprimé ni désactivé ; ce point est réservé à l'audit architectural.

**Verdict global inchangé : négatif.** Aucune configuration n'atteint +1,30 %/semaine net géométrique sur 52 semaines avec une pire baisse de 10 %. Il ne reste aucune période aveugle.

**Reproduction des études (Windows 11, Python 3.12)**

```powershell
python -m pytest tests/test_scalp_ablation.py tests/test_scalp_faisabilite.py tests/test_scalp_recherche.py tests/test_scalp_ouverture.py tests/test_scalp_frais.py tests/test_scalp_regimes.py tests/test_scalp_calibration.py tests/test_scalp_evenements.py tests/test_scalp_risques.py tests/test_scalp_techniques.py tests/test_scalp_macro.py tests/test_scalp_flux.py tests/test_scalp_epuisement.py tests/test_scalp_apprentissage.py tests/test_scalp_registre.py -q
python -m sentinelle.scalp.ablation --trials ""
python -m sentinelle.scalp.regimes --trials ""
python -m sentinelle.scalp.evenements --trials ""
python -m sentinelle.scalp.macro --trials ""
python -m sentinelle.scalp.flux --trials ""
python -m sentinelle.scalp.epuisement --trials ""
python -m sentinelle.scalp.registre
```
