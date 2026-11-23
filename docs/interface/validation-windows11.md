# Validation de l'application de bureau sous Windows 11

Consignation demandée par `SPEC.md` (chantier Interface web et application Windows V1) : l'application de bureau n'est
déclarée opérationnelle qu'après un test effectif sous Windows 11, consigné.

## Contexte

- **Date :** 23 novembre 2026.
- **Testeur :** le propriétaire, sur son PC Windows 11.
- **Version :** main `ba7eba8` (#125), installée par `mettre_a_jour.bat` ou par le ZIP de main. Le commit exactement installé
  n'a pas été relevé.
- **Mode :** démonstration, avec `.\bureau.bat --demo --data data-demo`. Le marché est simulé et le dossier de données est
  distinct du dossier `data` : simulation exclusive, aucun ordre réel.
- **Résultat rapporté :** « tous les 4 points sont bons ».

## Vérifications

| # | Vérification | Résultat |
|---|---|---|
| 1 | La fenêtre « Sentinelle » s'ouvre sur l'écran de connexion ; le raccourci « Sentinelle » apparaît sur le Bureau. | Réussi |
| 2 | Fermer la fenêtre ferme aussi la console ; `http://127.0.0.1:8765` ne répond plus. | Réussi |
| 3 | La fenêtre suit le thème clair ou sombre de Windows. | Réussi |
| 4 | Sans pywebview, la fenêtre Edge en mode application s'ouvre ; après sa fermeture, aucun processus Edge ne reste. | Réussi |

## Conclusion

L'application de bureau est **validée sous Windows 11** pour cet usage : lancement par `bureau.bat`, fenêtre pywebview ou
Edge de secours, arrêt du serveur à la fermeture, thème du système. Elle peut être déclarée opérationnelle.

## Hors de cette validation

- **Lancement sur les données réelles.** Le raccourci du Bureau, qui utilise le dossier `data` et les prix publics, suit
  le même code, mais son lancement n'a pas été rapporté.
- **`diagnostic.bat`.** Son verdict d'installation et les contrôles visuels de la démonstration (double clic sur
  « Urgence », message des Réglages, ordre fictif) n'ont pas été rapportés.
- **Informations non relevées :** numéro de build de Windows, version de Python.
- **Installateur `.exe` :** il n'en existe pas ; le lancement passe par `bureau.bat`.
- **Observation prospective :** non concernée. Elle garde son propre démarrage autorisé et la campagne de 72 heures n'est
  pas lancée.
