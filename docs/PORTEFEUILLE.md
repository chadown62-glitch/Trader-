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

Dans l'ordre :
1. la paire EUR/USDC de la source de marché (Binance, cotée 24 h/24) ;
2. à défaut, EUR/USDT, en supposant 1 USDC ≈ 1 USDT (le taux est alors marqué « approximation ») ;
3. à défaut, le taux de référence quotidien de la Banque centrale européenne (marqué « approximation ») ;
4. en démonstration : un taux simulé.

Vérifié sur le réseau réel le 11 novembre 2026 : Binance EUR/USDC donnait 1 € = 1,1139 USDC ; la BCE (taux de la veille) 1 € = 1,1214 dollar.

**Sans taux assez récent, aucun ordre n'est passé** (30 minutes par défaut pour le taux lu sur le marché, 4 jours pour celui de la BCE, qui n'est pas publié le week-end). Sentinelle n'invente jamais un taux. Pour le simple affichage, le dernier taux connu est utilisé et signalé comme ancien.

### Les coûts appliqués

| Coût | Valeur par défaut | Réglage |
|------|-------------------|---------|
| Frais de la plateforme | 0,10 % par ordre | Réglages → Simulation |
| Glissement de prix | 0,05 % par ordre au marché | Réglages → Simulation |
| Écart achat/vente | Demi-écart lu sur le carnet d'ordres au moment de l'ordre (en direct seulement) | — |
| Conversion euro ↔ USDC | 0,05 % par opération (hypothèse prudente) | Réglages → Portefeuille |

Un aller-retour coûte donc environ 0,4 % du montant, avant tout gain ou perte.

---

## 2. Ce que 30 € permettent, et ne permettent pas

La plateforme refuse tout ordre inférieur à **5 USDC** (environ 4,50 €). Ce minimum s'applique aussi au **stop** : au prix du stop, la position doit encore valoir 5 USDC, sinon le stop serait refusé et la position resterait sans protection.

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

1. Au premier démarrage de cette version, vos **anciens comptes papier (10 000 USDC)** sont conservés tels quels et continuent de fonctionner.
2. Le tableau de bord propose le **passage au portefeuille principal**. Rien ne change tant que vous n'avez pas confirmé.
3. Quand vous confirmez :
   - l'IA est arrêtée (vous la relancez ensuite) ;
   - les positions encore ouvertes des anciens comptes sont vendues au prix simulé du moment ;
   - le portefeuille principal de 30 € est créé ;
   - les anciens comptes restent **consultables** (historique, courbes, grand livre), mais on n'y trade plus.

Leurs soldes, leur historique et leur capital de départ ne sont ni effacés, ni remis à zéro. Le passage n'est pas réversible depuis le panel.

Sur une **nouvelle installation**, seul le portefeuille principal existe.

---

## 5. Limites connues

- Le coût de conversion de 0,05 % est une hypothèse, pas une mesure. Il se règle.
- Le demi-écart achat/vente n'est appliqué qu'en direct (le carnet d'ordres n'existe pas dans l'historique). En simulation accélérée, seul le glissement s'applique.
- Les exécutions partielles ne sont pas simulées : un ordre est exécuté en entier ou pas du tout. Pour des ordres de quelques euros sur des paires très liquides, c'est réaliste.
- Le taux de change est supposé le même à l'achat et à la vente (taux moyen), le coût de conversion tenant lieu d'écart.
- Seul l'euro est proposé comme monnaie du portefeuille.
