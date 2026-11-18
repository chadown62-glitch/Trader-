# Mission #25 — contexte macro/actualités causal en shadow

## Périmètre et séparation de v2

Base : PR #24, `118b19b6729dfb9b5995b56841d425a6a44d52e3`.
Les modules existants de calendrier, blackout, bouclier et RSS/Ollama ont été
lus avant développement. Le nouveau code réutilise `CALENDAR_URL`, le nettoyage,
les fonctions de similarité/détection des actifs, `OllamaClient` et la validation
des analyses du service existant. Il ne remplace aucun service de trading.

Le nouveau journal est `macro-shadow.sqlite`, séparé de `sentinelle.db`.
Aucune modification du modèle, des réglages ou fichiers de
`research/prospective-v2`. Aucune nouvelle feature ni nouveau veto dans l'agent.
`/api/macro-shadow` lit uniquement le journal local ; il ne télécharge rien.
Le panel ajoute « Contexte macro ». Aucun thread, abonnement ni service
permanent supplémentaire n'est démarré.

## Sources et droits d'utilisation

- Forex Factory : export hebdomadaire JSON déjà référencé dans
  `market/context.py`, sans accès à une API privée ni scraping de pages.
  Le nouveau connecteur est désactivé sans `--forex-rights-confirmed`.
  Cet indicateur suppose des droits effectivement obtenus pour l'usage et le
  stockage local ; il n'accorde aucun droit. L'export public ne constitue pas une
  licence de redistribution. Les [Notices de Forex Factory](https://www.forexfactory.com/notices?fr=1)
  interdisent notamment la copie/redistribution de leur calendrier sans consentement.
  Aucun export FF réel n'a été téléchargé ni publié pour cette mission. Les
  fixtures du dépôt sont artificielles. Seules des observations normalisées et
  l'empreinte du corps sont stockées localement ; aucun miroir brut du flux.
- [Fed RSS](https://www.federalreserve.gov/feeds/feeds.htm),
  [BLS RSS](https://www.bls.gov/feed/),
  [BEA](https://apps.bea.gov/rss/rss.xml),
  [BCE RSS](https://www.ecb.europa.eu/home/html/rss.en.html),
  [Eurostat RSS](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/catalogue-api/rss).
- BLS API publique `CUUR0000SA0` : indice CPI urbain non désaisonnalisé. Ce n'est
  **pas** un chiffre CPI y/y ni l'actual d'un événement FF. Les valeurs manquantes
  officielles (« - ») sont conservées à null. Aucune clé requise.
- Le flux RSS CoinDesk déjà présent dans la configuration historique possède
  un adaptateur causal. Son accès réseau réel et un serveur Ollama réel n'ont
  pas été validés ici ; les tests de ces chemins utilisent des doubles.

Les textes RSS sont limités à des titres et extraits courts, avec lien et
provenance. Le dépôt ne republie aucun article ni calendrier réel. Avant tout
élargissement de collecte, vérifier les conditions propres aux sources retenues.

## Modèle de temps et provenance

Chaque observation conserve source, clé d'item, version, type, contenu normalisé,
SHA-256 du contenu et du corps reçu, réception, stockage, publication connue.
Le calendrier sépare l'heure programmée (UTC) de la publication (inconnue pour
l'export FF). Le header HTTP Last-Modified est conservé dans les métadonnées de
collecte ; il ne prouve pas l'heure d'une annonce.

Disponibilité = maximum de réception, stockage, publication et publication de
révision connues. `MarketContext(as_of)` exclut toutes les versions et analyses
non disponibles à cet instant. Une révision ajoute une ligne ; triggers SQLite
interdisant UPDATE/DELETE sur observations, analyses, collectes et annotations.
Les heures sans offset, « Tentative » et « All Day » restent inconnues.
Les changements d'heure avec offsets explicites sont testés.

Un identifiant fournisseur permet de relier un report d'un jour à l'autre. Sans
ID, la clé faible titre/devise/jour est signalée comme telle : **aucun rapprochement
trans-journalier certain n'est inventé**. Des doublons contradictoires avec la
même clé sont refusés. Des valeurs identiques ne créent pas de nouvelle version,
mais un retour à une valeur ancienne après une révision en crée une.

## Collecte bornée et dégradation

Une invocation ne réalise qu'une collecte par source. Le cache/lease SQLite est
partagé entre les processus shadow utilisant le même fichier : réservation avant
HTTP, intervalle FF 3 h, RSS 1 h, BLS index 6 h. Le calendrier historique v2 reste
inchangé et n'utilise pas ce nouveau cache : le partage concerne les collecteurs
shadow, pas une refonte du mécanisme historique.

Pas de redirection suivie automatiquement, réponses limitées à 2 Mo, timeout
20 s, Retry-After respecté et backoff exponentiel sur erreurs/429. Aucun
contournement d'un blocage. Une réponse HTML/JSON/XML invalide devient une erreur
explicite ; les anciennes observations ne sont pas écrasées. Un arrêt après
commit d'observations mais avant confirmation de collecte garde les observations,
conserve la lease et affiche une mise à jour non confirmée.

Le contexte affiche missing/unavailable_or_stale/fresh et les raisons. Un
calendrier absent, périmé, vide ou d'importance inconnue ne prouve pas une période
calme. Le shadow recommande l'abstention dans ces situations, autour d'une annonce
High à ±30 minutes, ou face à une rumeur, origine non vérifiée, date inconnue,
instruction suspecte ou analyses contradictoires. **Ces avis ne modifient pas les
filtres existants et n'ont aucune capacité d'acheter.** Les dates de publication
anciennes ne deviennent pas des actualités fraîches du seul fait d'une réception
récente. Le régime de volatilité reste explicitement inconnu faute d'une mesure
causale fournie ; aucune volatilité n'est inventée.

## Interprétation, actuals et limites

- `%`, K/M/B, signes et unités sont normalisés ; virgules ambiguës refusées.
- `surprise_at` exige une source officielle datée, disponible, un indicateur et
  une période explicitement identiques, des unités compatibles et un forecast
  observé avant la publication. Une révision tardive du forecast n'est pas
  réinjectée dans le passé. Le signe est propre à l'indicateur (par exemple
  inflation au-dessus du consensus ou marché du travail plus faible), jamais
  une règle « hausse/baisse crypto ».
- Les `actual` du calendrier FF demeurent nulls, même si un champ supplémentaire
  non documenté est présent. Aucun taux n'est extrait automatiquement d'une prose
  RSS. **Le rapprochement opérationnel des annonces FF avec les chiffres publiés
  Fed/BLS/BEA/BCE/Eurostat n'est pas achevé.** Le helper de surprise est testé sur
  des fixtures explicitement appariées ; les données BLS réelles restent une
  série séparée, sans surprise calendaire déduite.
- Les groupes de provenance ne prouvent pas l'indépendance réelle des médias.
  Les reprises sont regroupées par similarité ; `independence_verified=false`.
  La contradiction détectée porte sur des analyses d'un même cluster, pas sur
  une vérification générale des faits.
- Ollama est optionnel, local, sans outils ni fonctions de trading. L'analyse
  passe par le client existant et une validation numérique stricte. Les champs
  `orders`/`settings` sont rejetés du résultat conservé. Le texte généré devient
  une hypothèse, jamais un fait officiel. Une injection peut encore altérer une
  interprétation ; elle ne donne aucune autorité d'exécution à ce module.

## Protocole distinct et ablation préparée

`research/macro-shadow-v3/protocol.json` est enregistré avant ses observations,
du 14/11/2026 au 14/02/2027 UTC. Les empreintes de v2, du catalogue de sources et
des modules shadow sont épinglées. Un code/catalogue modifié refuse la reprise
sous ce protocole. L'observation v2 du 13/11 au 13/02 est intacte.

Trois bras : cours seuls / cours+calendrier / cours+calendrier+actualités.
`annotate` ouvre la base de trading **en lecture seule**, retient uniquement les
décisions papier engagées dans la période et écrit leur contexte `as_of` et les
avis contrefactuels dans le sidecar. Une décision annotée ne peut être réaffectée
à un autre protocole ou à une base modifiée. Une reprise ne double pas l'annotation.

Même modèle, période, capital, frais et risque prévus pour la comparaison ; blocs
quotidiens 7 jours, 2 000 tirages, correction pour deux contrastes et deux
intervalles, au moins 30 trades, 28 jours et 95 % de couverture de sources. Les
fenêtres historiques explorées sont exclues de toute promotion.

**Aucune ablation financière n'a été réalisée.** Les bras sont des annotations,
pas des portefeuilles exécutés. Un replay causal d'exécution contrefactuelle
reste nécessaire avant de mesurer leur PnL : supprimer simplement les trades
refusés du journal n'est pas une simulation valide. Aucune amélioration financière
ni promotion n'est revendiquée.

## Commandes explicites (aucun démon)

```sh
# Vue locale, sans téléchargement ni analyse :
python -m tools.macro_shadow view --data data

# À partir du 14/11 seulement, après validation des droits et supervision :
python -m tools.macro_shadow fetch --data data --sources fed bls bea ecb eurostat
python -m tools.macro_shadow fetch --data data --sources forex_factory --forex-rights-confirmed
python -m tools.macro_shadow fetch --data data --sources coindesk bls_cpi_index

# Optionnel : un appel local d'analyse d'une seule observation :
python -m tools.macro_shadow analyze --data data --observation-id ID

# Annotation finie, base papier existante ouverte en lecture seule :
python -m tools.macro_shadow annotate --data data --trading-db data/sentinelle.db
```

Les actions d'ingestion/analyse/annotation refusent une période ou une empreinte
incompatible. Aucune commande n'a été utilisée pour une observation prospective
avant la date prévue. Pour un environnement avec proxy SOCKS, l'extra
`httpx[socks]` peut être requis (socksio 1.0.0 installé pour les sondes de cette
session, sans modification des réglages v2).

## Vérifications réseau séparées et UI

Le fichier `mesures/macro-shadow/connectivity.jsonl` conserve seulement les
statuts, tailles, empreintes et nombres d'items des sondes publiques : aucun
texte RSS réel n'est redistribué. Fed/BLS/BEA/BCE/Eurostat : HTTP 200 et parsing
réussi. Le premier essai BLS API a révélé une valeur manquante ; le parseur
corrigé a été revérifié sur le réseau (34 observations, dont un actual null).
Ces sondes ne sont ni des observations prospectives ni des entrées du modèle.

Le test Playwright `tests/e2e/macro_check.js` couvre 1440 et 390 px, navigation
mobile, état vide, absence de débordement et erreurs JavaScript. Son lancement
avec Chromium système a été tenté : refus de socket Unix (`Operation not
permitted`). Le rendu desktop/mobile reste **non vérifié**, sans capture de
succès revendiquée. Windows 11 reste non exécuté ; la procédure antérieure est
conservée. Aucun service permanent n'a été lancé ; `main` n'est pas modifiée.

## Bilan final exécuté

- 32 tests spécifiques passent sur fixtures artificielles datées.
- Suite complète : **409 tests réussis**, 0 échec, 0 skip, 0 xfail, 104,81 secondes.
- CPython 3.12.14, Linux x86-64, CCXT 4.4.99 complet (sync, async et Pro).
- Tests sous seccomp, environnement vidé, aucune connexion réseau de test ;
  tentative de création de socket refusée séparément.
- Un avertissement Starlette/TestClient, aucun conflit dans `pip check`.
- Compilation des nouveaux modules Python, syntaxe JavaScript et
  `git diff --check` réussis. Vue CLI locale testée sans téléchargement :
  « Données insuffisantes », shadow_only, can_trade=false.
- Les preuves JSON jointes épinglent les empreintes v2 et les résultats.
- Aucun ordre réel, aucun compte privé, aucune fusion, aucun service permanent.
