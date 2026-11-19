# ALPHA FACTORY v2 — préparation de l'évaluation unique (mission 52)

Branche `claude/alpha-factory-v2-readiness`, empilée sur COST OPTIMIZER (#50) : le laboratoire (#49) et le contrat de
coûts (#50) sont dans sa lignée, le hub (#51) n'y est pas (le laboratoire n'en dépend pas). **Aucune évaluation n'a été
lancée, aucun plan n'est enregistré, aucune stratégie n'a été exécutée sur les données réelles** : seuls des panneaux
synthétiques ont servi aux tests ; sur l'archive réelle, seuls le catalogue de la plateforme et la liste des actifs ont
été lus (couverture des règles), jamais un rendement.

## 1. Compatibilité du contrat de coûts #50 avec le laboratoire #49

Commande : `python -m tools.alpha_lab costs-check` (vérifie, n'adopte rien).

| Scénario (par ordre, pb) | BTC, ETH | Autres | Aller-retour BTC, ETH / autres | Commission | Conversion |
|---|---|---|---|---|---|
| Actuel « prudent » (provisoire) | 22,00 | 28,00 | 44,0 / 56,0 | 10 | 5 |
| Actuel « tendu » (provisoire) | 36,00 | 54,00 | 72,0 / 108,0 | 10 | 5 |
| Proposé « mesure » (#50) | 21,88 | 26,36 | 43,8 / 52,7 | 10 (hypothèse) | 11,67 |
| Proposé « tension » (#50) | 30,66 | 37,95 | 61,3 / 75,9 | 10 (hypothèse) | 14,51 |

Résultat : **compatible**. Le fichier proposé se charge sans erreur, ses scénarios sont ordonnés (brut ≤ mesure ≤
tension), ses valeurs sont exactement celles publiées par le contrat `cost_contract_v1` (test
`test_the_cost_contract_of_pr50_is_readable_by_the_lab_and_matches_its_published_values`), le minimum d'ordre (5) et
les actifs majeurs (BTC, ETH) correspondent. **Il n'est pas adopté** : le laboratoire garde son fichier provisoire tant
que vous ne l'avez pas décidé.

## 2. Simulation des reliquats (correction)

Avant : le simulateur prélevait la commission dans les liquidités et n'arrondissait aucune quantité ; il ignorait donc
les reliquats que COST OPTIMIZER a mesurés (médiane 2,6 % d'une position de 10 € sur BTC/ETH).

Maintenant (seulement si les règles de marché sont fournies ; sans elles, comportement strictement identique) :
- quantité achetée arrondie au **pas de la plateforme** ; commission prélevée **dans l'actif reçu** ; conversion payée
  en liquidités ;
- à la vente, seule la part multiple du pas est vendable ; le reste est un **reliquat compté comme perdu** à sa
  création (règle prudente, la même que celle du hub), retiré du portefeuille et rapporté à part (`dust_lost`) ;
- minimum d'ordre = le plus grand du contrat (5) et de celui de la plateforme ; un ordre qui tombe sous le minimum
  après arrondi est refusé ;
- règles lues dans le **catalogue archivé** de la plateforme, vérifié par son empreinte : 775 actifs sur 775 couverts
  (les paires retirées de la cote y restent, statut « BREAK ») ;
- le plan déclarera le modèle d'exécution et l'empreinte du catalogue ; l'exécution refuse un catalogue différent.

Tests (données synthétiques) : sans règles, résultats identiques ; avec un pas grossier, chaque reliquat vaut au plus
un pas et la conservation de l'argent est exacte (capital = valeur finale + frais + écart + reliquats) ; avec un pas
très fin, le modèle rejoint l'ancien ; jamais de liquidités créées ni de position négative sur un panneau aléatoire ;
ordre refusé sous le minimum après arrondi.

Ordre de grandeur **structurel** (synthétique, pas un résultat de stratégie) : un aller-retour de 30 € en BTC au pas
de 0,00001 laisse ≈ 0,58 € de reliquat ; une règle qui échange souvent un petit capital y perd beaucoup.

## 3. Verdict : ALPHA FACTORY v2 est-il prêt pour son évaluation unique ?

**Techniquement prêt, mais PAS ENCORE prêt à être lancé.** L'évaluation ne doit pas commencer tant que les points
suivants ne sont pas tranchés :

| Point | État |
|---|---|
| Code du laboratoire, modèle de reliquats, compatibilité du contrat | prêt, testé (données synthétiques) |
| Plan préenregistré | non enregistré (étape suivante, après votre accord) |
| **Décision 1 : adopter le contrat #50** (scénarios « mesure » et « tension ») | **en attente de vous** |
| **Décision 2 : commission** (0,10 %, non vérifiée ; la confirmer, ou l'accepter comme hypothèse déclarée) | **en attente de vous** |
| **Décision 3 : conversion à chaque ordre** (recommandé) plutôt qu'« USDT conservé » | **en attente de vous** |
| Décision 4 : ordre au marché à l'ouverture (déjà le cas du laboratoire) | conforme |
| Décision 5 : modéliser les reliquats | fait ; il sera appliqué dès l'enregistrement du plan (à confirmer) |
| Archive des données (775 actifs, manifeste `f301ffe9…`, catalogue `3ec824f8…`) | présente dans l'espace de travail temporaire, **pas dans le dépôt** : elle doit être conservée entre l'enregistrement et l'exécution (ou re-téléchargée AVANT l'enregistrement) |

Après votre accord : (1) remplacer le fichier de coûts par la proposition #50 (nouvelle version, nouvelle empreinte) ;
(2) `python -m tools.alpha_lab register --archive …` (fige le code, les candidats, les coûts, le modèle de reliquats,
l'archive) ; (3) `python -m tools.alpha_lab run …` une seule fois ; (4) publier les résultats tels quels, y compris
négatifs.

## 4. Limites

- Pas de Windows : l'évaluation tourne dans cet environnement Linux ; Windows 11 n'est pas testé.
- Les filtres de marché sont ceux du jour de l'archive, appliqués à tout l'historique (les pas ont pu changer).
- La règle « reliquat perdu » est prudente : en réalité il peut être regroupé ou converti (avec des frais).

## 5. Intégration Sprint 1 sur main — 19 novembre 2026

Cette section décrit les contrôles réexécutés pour l'intégration ; les sections précédentes sont le rapport
historique de #53, et ne constituent pas une nouvelle évaluation financière.

- Base : `main` après #57, `0cfff6469540fcc88ab67ae94db10edf9ac430db`.
- Source #53 : `ff4650933eceb915e1c45d011da36b4dec280e54`, intégrée par fusion sans conflit.
  L'encodage UTF-8 déjà présent sur main dans `data.py` a été conservé.
- `sentinelle/alpha/lab/simulate.py` est strictement identique à #53 (blob Git
  `124eb58a26139eb02bc674ec35850fb75b5743a1`) : `lose_dust()` et le modèle conservateur restent intacts.
- Audit AST des six fichiers Python importés/modifiés : sept accès texte, tous explicitement UTF-8.
  Un seul ajout nécessaire : `encoding="utf-8"` sur le manifeste JSON du test de catalogue.
  Les lectures binaires de JSON restent indépendantes de l'encodage système Windows.
- `SPEC.md` propose Sprint 1 `[TERMINÉ]` / Sprint 2 `[EN COURS]` ; cette transition reste soumise à la
  validation et à la fusion humaines. Aucune fonctionnalité Sprint 2 n'est développée ici.
- Huit fichiers au total, avec la dérogation ponctuelle autorisée par le propriétaire pour intégrer #53.
  `CLAUDE.md` et `AGENTS.md` sont conservés tels qu'ils sont sur main.
- Le chemin `docs/coordination/development-campaign.md` cité dans SPEC est absent sur main et dans cette
  intégration ; aucun contenu de coordination n'a été inventé pour le remplacer.

### Validation réellement exécutée

Environnement Linux x86_64, Python 3.12.14, pytest 8.3.5, NumPy 2.3.5, pandas 2.2.3,
scikit-learn 1.8.0, FastAPI 0.141.1, CCXT 4.4.99. Code testé au commit local
`5bb624e3f1e1390ac25391443a0ff2a47368b3f5` ; seul ce rapport est ajouté ensuite.

Commande de suite complète réellement exécutée depuis la racine de la branche :

```bash
env -i PATH=/usr/bin:/bin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /workspace/scratch/6168c8d38e5c/sprint1-offline /workspace/scratch/6168c8d38e5c/trader-ai/.venv/bin/python -m pytest -o addopts='' -q --junitxml=/workspace/scratch/6168c8d38e5c/integrate53-full.xml
```

Le lanceur `sprint1-offline` est compilé depuis `tools/security/offline_exec.c` ; il interdit les sockets
réseau au processus testé et à ses enfants. Les fichiers JUnit locaux ont été lus : 0 erreur, 0 échec.

| Vérification | Résultat observé |
|---|---|
| Suite complète | **1041 passed, 1 skipped, 8 warnings in 493.40s (0:08:13)** |
| `tests/test_alpha_lab_readiness.py tests/test_alpha_lab.py tests/test_windows_utf8.py` | **78 passed in 5.38s** |
| `tests/test_diagnostic.py::test_real_panel_starts_answers_stops_and_restarts_in_a_temporary_folder`, séparément sans filtre de sockets | **1 passed in 6.43s** |
| `python -m tools.alpha_lab costs-check`, sous filtre réseau | code retour 0 ; `compatible: true`, `problems: []`, `adopted: false` |
| `git diff --check` | aucune erreur |

Le seul test ignoré par la suite complète exige des sockets locales ; il est précisément celui passé séparément.
Ce contrôle utilise une API de démonstration dans un dossier temporaire, puis vérifie son arrêt et son redémarrage.
Les huit avertissements sont une dépréciation Starlette/httpx et sept avertissements scikit-learn pour des
estimateurs archivés en 1.9.1, relus avec 1.8.0. Ils existaient déjà dans la validation #57 ; aucun modèle n'est
réentraîné ou réécrit pour les masquer. La compatibilité des artefacts sérialisés reste une limite à traiter séparément.

### Gel scientifique et limites

HEAD GitHub contrôlés avant l'intégration et après les changements :

| PR | HEAD attendu et observé, inchangé |
|---|---|
| #54 | `5284a71358bb7a5f7ef3e5e46f2905db4ffc9e18` |
| #55 | `18b31bb901f656d6126b370f47c510775f135c57` |

Aucune écriture, aucun rebase, aucun push sur ces branches ; aucun changement à leurs artefacts.
Aucune adoption du nouveau fichier de coûts, aucun préenregistrement, aucune nouvelle évaluation historique ou
prospective, aucune promotion. Aucun trading réel ni service permanent activé. Capital de simulation inchangé : 30 EUR.
Windows 11 et Edge ne sont pas disponibles ici : les tests Linux et l'audit UTF-8 ne valent pas une attestation Windows.

### Vérification locale Windows 11 / PowerShell

Sur une copie de travail propre, après publication de la branche :

```powershell
git fetch origin
git switch --track origin/feat/sprint1-integrate-53
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q
.\.venv\Scripts\python.exe -m tools.alpha_lab costs-check

# API en demonstration synthetique, donnees temporaires separees
$demoData = Join-Path $env:TEMP ('sentinelle-sprint1-' + [guid]::NewGuid().ToString())
.\.venv\Scripts\python.exe -m sentinelle --demo --no-browser --data $demoData --port 8765
# Deuxieme fenetre PowerShell :
Invoke-RestMethod http://127.0.0.1:8765/api/health
Start-Process msedge.exe http://127.0.0.1:8765/
# Arreter le serveur avec Ctrl+C.
```

Le lancement manuel en mode démo est borné par Ctrl+C et utilise un dossier temporaire distinct des données utilisateur.
Arrêt de cette mission après l'ouverture de la PR brouillon ; validation humaine avant toute fusion.
