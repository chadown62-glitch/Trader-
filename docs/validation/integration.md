# Intégration Claude et Codex

La base est la PR #17, commit `788c141`. Les deux historiques divergent depuis `f61430f`. Le numéro #6 désigne une issue.

| PR | Décision d'intégration |
|---|---|
| #2, #5, #7 | Conserver le verrou d'exécution de Claude. Intégrer la façade publique renforcée, les refus avec objets existants et les tests de redémarrage. |
| #8, #16 | Un seul rôle actif `main`. Le rôle `principal` existant garde son identifiant et son solde. Reprendre chaque solde préexistant au grand livre. Figer les autres comptes, positions et ordres en place. |
| #9, #11, #17 | Conserver le contrat de prix commun et l'ouverture suivante. Restreindre les données fournies à l'agent et les bougies synthétiques partielles. |
| #10, #12, #15 | Utiliser les carnets FX horodatés dans les deux sens. Les conversions explicites et celles des fills partagent la consommation de profondeur. Les anciennes références monodevises restent consultables. |
| #13 | Le calculateur de profondeur sert au courtier papier. SQLite enregistre le carnet consommé, le fill, les soldes, les protections et le reliquat dans la même transaction. |
| #4, #14 | Le laboratoire et la purge temporelle sont traités sur la branche de validation scientifique. |

## Conventions du simulateur

Les carnets publics nécessitent un horodatage fourni par la source. Un carnet manquant ou incohérent suspend les exécutions concernées. Les ordres au marché et les limites négociables traversent les niveaux affichés, dans la limite du solde et du prix limite. Les frais portent sur chaque quantité exécutée. Les stops déjà déclenchés restent actifs en cas d'exécution partielle. La priorité simulée donne la priorité aux stops puis à l'ordre d'arrivée. Dans le même portefeuille, les ordres protecteurs d'une position partagent une seule réservation.

Une nouvelle photographie du carnet remplace la profondeur observable. L'identité de la photographie repose sur la source, le symbole et son horodatage. Cette convention ne permet pas d'inférer le rang dans la file, les annulations externes ni le réapprovisionnement entre deux observations. Les démonstrations synthétiques et les historiques OHLC suivent le contrat par bougie avec frais et glissement paramétrés. Ils n'ont pas de profondeur mesurée.

Le portefeuille EUR utilise la fourchette FX au moment du fill. Un replay sur d'autres devises consulte les carnets archivés disponibles à sa date. La source synthétique peut générer un carnet FX de démonstration à la date simulée. Les retraits, dépôts externes et comptes privés n'interviennent dans aucun de ces parcours.
