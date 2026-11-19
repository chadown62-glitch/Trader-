# Portefeuille principal de 30 € fictifs

Mis à jour le 11 novembre 2026.

Sentinelle n'a plus qu'**un seul portefeuille d'argent fictif**, de **30 €** par défaut. Il est **partagé** entre votre trading manuel et l'IA : ce que l'un dépense n'est plus disponible pour l'autre.

---

## 1. Comment il est tenu

| Point | Règle |
|-------|-------|
| **Monnaie des comptes** | L'euro. Les liquidités, les frais, le résultat de chaque trade et le capital sont en euros. |
| **Monnaie des prix** | Celle de la paire (USDC par défaut). Le prix d'entrée, le stop et l'objectif restent ceux du graphique. |
| **Conversion** | Chaque achat et chaque vente est converti au taux du moment. Le **taux, sa source et son heure** sont enregistrés avec l'opération. |
| **Résultat d'un trade** | Vente convertie au taux du jour de la vente, moins achat converti au taux du jour de l'achat, moins tous les frais. C'est exactement ce qui entre ou sort de la caisse. |
| **Liquidités** | *Disponible* = liquidités − *réservé* (ordres d'achat en attente). *Investi* = valeur des cryptos détenues. *Capital* = liquidités + investi. |
| **Soldes** | Aucun solde ne peut devenir négatif : l'opération est refusée. Pas de levier, pas de vente à découvert. |
| **Grand livre** | Chaque variation d'un solde est inscrite avec le solde obtenu (page **Historique**). Un contrôle vérifie en permanence que la somme des écritures égale le solde. |

### D'où vient le taux de change

Le courtier utilise les deux côtés d’un carnet public daté de la paire exacte. Pour un achat il calcule le coût d’acquisition de la devise, pour une vente le produit de sa cession. Il vérifie la profondeur disponible lors de l’écriture du fill. Les conversions explicites peuvent utiliser deux paires avec un intermédiaire nommé et des horodatages compatibles.

Un carnet futur, absent, trop ancien ou incohérent entraîne un refus. Le cache garde sa date de marché. Un replay EUR consulte les carnets archivés antérieurs ou égaux à l’événement. Le mode démonstration génère ses propres cotations datées et les identifie comme synthétiques. Les anciennes estimations BCE et les parités supposées sont exclues de l’exécution.

### Les coûts appliqués

| Coût | Valeur par défaut | Réglage |
|------|-------------------|---------|
| Frais de la plateforme | 0,10 % par ordre | Réglages → Simulation |
| Glissement de prix | 0,05 % par ordre au marché | Réglages → Simulation |
| Écart achat/vente | Prix des niveaux réellement traversés dans le carnet observé | — |
| Conversion euro ↔ USDC | 0,05 % par opération (hypothèse prudente) | Réglages → Portefeuille |

Sur carnet, le coût de l’aller-retour dépend aussi des niveaux traversés dans chaque sens.

---

## 2. Ce que 30 € permettent, et ne permettent pas

Pour l’exemple ci-dessous, le marché impose un minimum de **5 USDC** (4,50 € au taux de l’exemple). Ce minimum s'applique aussi au **stop** : au prix du stop, la position doit encore valoir 5 USDC, sinon le stop serait refusé et la position resterait sans protection.

Avec la règle de prudence (0,5 % du capital risqué par trade, soit 0,15 €), pour un bitcoin à 50 000 USDC et 1 USDC = 0,90 € :

| Distance du stop | Taille prudente | Décision |
|------------------|-----------------|----------|
| 3 % | 5,5 USDC | Acceptée telle quelle |
| 4 % | 4,0 USDC, sous le minimum | Portée au minimum (5,5 USDC) : le risque passe à 0,66 % du capital, sous le plafond de 1 % |
| 10 % | 1,5 USDC | **Refusée** : au minimum, le risque serait de 1,8 % du capital |

- Quand la taille prudente est sous le minimum, l'IA monte au minimum **seulement si le risque reste sous 1 % du capital** (réglage « Petit capital », plafond absolu 2 %). Sinon elle refuse le trade.
- Chaque refus est écrit dans le journal avec sa raison. L'IA ne simule jamais un ordre que la plateforme refuserait.
- Conséquence assumée : avec 30 €, **beaucoup de signaux seront refusés**, surtout ceux dont le stop est éloigné (suivi de tendance sur les cryptos volatiles). Les signaux refusés restent suivis jusqu'à leur issue : l'IA apprend aussi de ce qu'elle n'a pas pu prendre.

En trading manuel, les mêmes minimums s'appliquent : l'aperçu de l'ordre indique le montant en USDC et en euros, les frais, le coût de conversion, le total débité et le taux appliqué.

---

## 3. Simulations et entraînements : des comptes à part

Les simulations accélérées et les entraînements sur l'historique utilisent leur propre base de données, en mémoire. Ils **ne peuvent pas modifier** le portefeuille principal. Un test le vérifie : toutes les tables du portefeuille sont identiques avant et après une simulation qui a tradé.

---

## 4. Si vous aviez une ancienne installation

Rien n'est remplacé en silence.

Le démarrage conserve les anciens comptes, soldes, ordres, positions et courbes en lecture seule. Il ouvre le portefeuille principal de 30 € ou reprend son identifiant et ses soldes s’il existait déjà sous le rôle `main` ou `principal`. Aucune liquidation de migration n’est simulée. La reprise des soldes antérieurs au grand livre porte un libellé distinct.


## 5. Limites connues

- Le coût de conversion de 0,05 % est une hypothèse, pas une mesure. Il se règle.
- Le demi-écart achat/vente n'est appliqué qu'en direct (le carnet d'ordres n'existe pas dans l'historique). En simulation accélérée, seul le glissement s'applique.
- Le courtier sur carnet enregistre les fills partiels, les frais payés et la quantité restante. Les historiques OHLC utilisent le modèle par bougie.
- Le change utilise les côtés acheteur et vendeur avec leur profondeur. Le coût paramétré s’ajoute à cet écart.
- Seul l'euro est proposé comme monnaie du portefeuille.
