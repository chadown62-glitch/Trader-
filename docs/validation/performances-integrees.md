# Page Performances intégrée à la chaîne #21

Le diff de Claude #20 est repris par cherry-pick, avec conservation des routes
`/api/portfolio/evaluation` et `/api/performance`. Le portefeuille, les migrations,
le verrou et le registre Champion/Challenger de #21 restent présents.

Corrections d'intégration : valorisation périmée affichée comme indisponible avec
la date du dernier relevé, sans contourner le refus des cours périmés ; frais des
positions encore ouvertes inclus ; absence de parité FX implicite dans le calcul
du rendement d'un trade ; fenêtres de 7/30/90 jours alignées sur les jours UTC.
Le verdict de validation et le nombre de versions du registre sont exposés dans
la page. Les statistiques entre versions restent descriptives.

La série quotidienne conserve explicitement `observed=false` pour les jours
sans relevé. Le rendement de ces jours est imputé à zéro et le changement est
reporté au relevé suivant : c'est une convention affichée, pas une observation.
Les intervalles en R de cette page sont descriptifs (rééchantillonnage individuel),
pas un critère de promotion scientifique.

Suite complète hors réseau : 358 tests réussis (347 de #21 + 11 performances),
aucun skip/xfail. Un avertissement de dépréciation Starlette/TestClient.
Le scénario de cinq jours calcule les chiffres attendus à la main. Un nouveau
cas vérifie qu'un cours périmé ne crée pas un faux jour observé.

Le test navigateur `tests/e2e/performance_check.js` couvre 1440 et 390 pixels.
Son exécution est bloquée dans cette session : Chromium local ne peut pas créer
sa socket Unix (`Operation not permitted`), et le navigateur distant ne peut
joindre le serveur local (`ERR_CONNECTION_REFUSED`). Le rendu desktop/mobile
reste donc NON VÉRIFIÉ dans cette session. Aucun succès visuel n'est revendiqué.
