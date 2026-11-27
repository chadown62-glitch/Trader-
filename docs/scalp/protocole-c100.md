# Protocole C100 — observation prospective fictive sur 52 semaines

**Statut : préparé et figé, NON DÉMARRÉ.** Décision du propriétaire du 25 novembre 2026 : « Prépare le protocole C100 sans démarrer l'observation prospective. » L'observation ne commence qu'avec une autorisation distincte du propriétaire. Elle reste ensuite exclusivement fictive : aucun ordre réel, aucune clé, aucun levier, aucune vente à découvert, aucune promotion automatique.

## Pourquoi

Tout l'historique 2018 → 2026 est déjà consulté (dossier, section 3). La seule preuve encore possible est une observation **prospective** d'une configuration figée avant son début.

## Configurations observées (figées au démarrage)

| Nom | Définition | Rôle |
|---|---|---|
| **B0 + A1** (candidate) | Stratégie B0 du moteur unique. La poche crypto passe en liquidités le lendemain quand le BTC est « en range » : rendement d'efficacité de Kaufman sur 30 jours ≤ 0,3 (`regimes.rules`, A1) | Candidate expérimentale (#156) |
| B0 | Même stratégie, sans abstention | Référence appariée |
| B1 | Stratégie diversifiée du moteur unique | Référence |
| Témoin A1 | Masque A1 décalé d'un an | Contrôle du calendrier |
| BTC détenu | Achat et conservation | Référence passive |
| Liquidités | Rendement nul | Référence |

**Rappel historique (déjà consulté, non probant)** : avec A1, la pire baisse de B0 passe de 60 % à 23 %. L'écart de rendement n'est pas significatif (Holm 1).

## Données, décisions et coûts

- **Univers** : les 42 cryptos candidates définies à la date, top 10 du volume connu avant le mois. Les actifs retirés de la cote sont traités à leur retrait.
- **Données** : archives journalières publiques de data.binance.vision, vérifiées par leur somme de contrôle. Le jour d est lu une fois publié.
- **Causalité** : décision à la clôture du jour d, exécution fictive à l'ouverture du jour d + 1.
- **Coûts** :
  - barème daté officiel (C061) ;
  - écart à l'ouverture mesuré (C099) ;
  - stress à × 2 publié à côté (C097).
- **Capitaux fictifs** : 30, 100, 1 000 et 10 000 €. La faisabilité tient compte des minima d'ordre (C062) : une entrée infaisable n'est pas prise.

## Durée et rythme

- 52 semaines, à partir du lundi 00:00 UTC qui suit l'autorisation.
- Relevé hebdomadaire chaque lundi, consigné en ajout seul avec chaînage des empreintes.
- Rapport mensuel **descriptif seulement**. Aucune décision n'est prise sur un résultat intermédiaire.

## Critères préenregistrés (tous exigés, sur les 52 semaines, pour au moins un capital)

1. Rendement net géométrique d'au moins +1,30 % par semaine.
2. Pire baisse d'au plus 10 %.
3. Au moins 60 % de semaines positives.
4. B0 + A1 bat B0 en écart hebdomadaire apparié : intervalle par blocs de 4 semaines, p unilatérale < 0,05.

Tous les résultats sont publiés, y compris négatifs. Atteindre les critères ne déclenche **aucune** promotion : la décision revient au propriétaire, après audit indépendant (GPT Work).

## Arrêts

- **Pas d'arrêt anticipé pour succès.**
- **Arrêt pour intégrité** dans les cas suivants :
  - donnée invérifiable ;
  - empreinte du code modifiée ;
  - garde-fou contourné ;
  - décision du propriétaire.

  Tout arrêt est consigné. Une reprise demande un nouveau protocole.

## Intégrité

- **Empreintes SHA-256 relevées au démarrage** : modules du moteur (`moteur.py`, `signaux.py`, `regimes.py`, `ablation.py`, `data.py`, `recherche.py`), réglages et présent protocole. Toute modification ultérieure de ces fichiers invalide l'observation en cours.
- **Aucun réglage pendant l'observation.**
- PR #54 et #55 restent gelées. Guardian et Risk Commander ne sont pas assouplis.

## Ce qui reste à décider par le propriétaire

- L'autorisation de démarrer.
- La confirmation de la candidate : B0 + A1, ou une autre configuration figée avant le démarrage.
