# Verrou « simulation uniquement »

Mis à jour le 11 novembre 2026 (mission n° 3 sur GitHub).

Pendant tout le développement, Sentinelle **ne peut ni envoyer, ni modifier, ni annuler un ordre réel**. Les cours du marché sont réels ; l'argent est toujours fictif.

Ce verrou **n'est pas un réglage**. Il n'existe :
- aucun paramètre dans les réglages ;
- aucune variable d'environnement ;
- aucune commande (panel, API, Telegram) ;
- aucune clé API, aucun compte « validé », aucun bulletin favorable ;

qui permette de l'ouvrir. Le code qui parlait à la partie privée d'une plateforme d'échange (ordres, annulations, soldes) a été **retiré du programme**. Il reste consultable dans l'historique Git (commit `5fa6b36`), mais il n'est plus exécutable.

---

## 1. Les couches de protection

Chaque couche refuse seule. Une erreur future dans l'une est arrêtée par les autres.

| Couche | Fichier | Ce qui est garanti |
|--------|---------|--------------------|
| **Courtier réel** | `sentinelle/execution/live.py` | La classe `LiveBroker` ne contient plus aucun code d'exécution. Elle ne peut pas être construite. Si un objet de cette classe existait malgré tout (créé sans constructeur, ou resté en mémoire), chacune de ses méthodes refuse, sans toucher à l'objet « exchange » qu'il porte. |
| **Client de marché** | `sentinelle/market/sources.py` | `make_ccxt_exchange` n'accepte **aucun identifiant** : impossible de lui passer une clé. Le client est enveloppé dans une façade (`PublicExchange`) qui ne laisse passer que neuf lectures publiques (cours, carnet, transactions…). Tout le reste est refusé. Si un identifiant apparaissait sur le client, même les lectures seraient refusées. |
| **Coffre des secrets** | `sentinelle/services/secrets.py` | `get()` ne renvoie **jamais** une clé d'exchange : la valeur ne sort pas du fichier. Les anciennes clés restent dans `data/secrets.json` tant que vous ne les supprimez pas. |
| **Comptes** | `sentinelle/services/core.py`, `live_ops.py` | `runtime()` n'accepte qu'un compte « papier » dont le courtier est exactement le courtier simulé. Tout autre compte actif est écarté de la mémoire et journalisé, à chaque utilisation et toutes les 10 secondes. Aucune connexion à un exchange n'est tentée au démarrage, à la reconnexion ou lors d'un changement de réglages. |
| **Positions** | `sentinelle/execution/positions.py` | Lié à un autre courtier que le courtier simulé, le gestionnaire de positions est inerte : il ne s'y abonne pas, ignore ses exécutions et refuse chaque ouverture, fermeture, modification, pose ou ajustement d'un stop ou d'un objectif. Le courtier est revérifié à chaque action. |
| **Agents IA** | `sentinelle/agent/agent.py`, `services/agents.py` | Le mode « live » n'existe plus. L'agent « IA — Portefeuille réel » a été retiré : le démarrer est refusé, même avec la phrase de confirmation, un bulletin favorable ou un redémarrage automatique. |
| **API et Telegram** | `sentinelle/api/server.py`, `notify/telegram.py` | Toute demande visant un ancien compte réel reçoit une erreur **403** avec le code `paper_only` (à ne pas confondre avec « introuvable »). Enregistrer une clé d'exchange est refusé ; effacer les anciennes est permis. L'urgence Telegram n'agit que sur les comptes fictifs. |

Le module central est `sentinelle/safety.py`. Il ne lit ni réglage, ni variable d'environnement : ses refus sont inconditionnels.

## 2. En cas de panne d'un contrôle

Le principe est le refus par défaut :
- si un contrôle lève une erreur inattendue, l'opération échoue ;
- si la vérification interne d'un compte tombe en panne, le compte est considéré comme non simulé, donc refusé ;
- si le journal est indisponible, le refus a lieu quand même.

## 3. Journal des tentatives

Chaque tentative refusée est écrite dans le journal du panel (catégorie « sécurité ») avec l'opération, le numéro de compte et le type de courtier. **Aucun secret n'y figure** : toute information dont le nom évoque une clé, un secret, un jeton ou un mot de passe est écartée avant l'écriture.

## 4. Anciennes installations

Rien n'est effacé automatiquement.

| Élément | Ce qu'il devient |
|---------|------------------|
| Ancien compte « Portefeuille réel » | Conservé dans la base, affiché comme « ancien compte — inerte », jamais connecté. |
| Anciennes positions et anciens ordres réels | Conservés tels quels dans la base. Sentinelle ne les suit plus. |
| Anciennes clés API | Conservées dans `data/secrets.json`, jamais lues. Bouton « Supprimer les anciennes clés » dans **Réglages → Verrou et fiabilité**. |
| Anciens réglages (`testnet`, budget, plafond) | Conservés, sans aucun effet. |
| « IA — Portefeuille réel » activée | Désactivée au démarrage, avec une ligne dans le journal. |

> **Attention.** Si vous aviez de vrais ordres ou de vraies positions sur une plateforme d'échange, ils y sont toujours. Sentinelle ne les voit plus et ne les gère plus : vérifiez-les directement sur le site de la plateforme, et révoquez-y vos anciennes clés API.

## 5. Preuves

| Vérification | Résultat |
|--------------|----------|
| Tests de Codex (PR n° 2, `tests/test_paper_only_security.py`, version du commit `9d71bba`), exécutés sans modification sur une branche de validation locale | 24 réussis sur 24 (19 des 20 tests de la première version échouaient avant le verrou) |
| Tests du verrou (`tests/test_lockdown.py`) | 27 réussis sur 27 |
| Suite complète du dépôt, tests de Codex compris | 176 réussis sur 176 (152 tests du dépôt + 24 tests de Codex), le 11 novembre 2026 |

Ce que vérifient les tests du verrou, en plus de ceux de Codex :
- **analyse du code source** : aucun appel privé d'exchange (`create_order`, `fetch_balance`, `withdraw`…) dans tout le programme ; CCXT n'est importé que par le module des données publiques ; aucun fichier ne manipule de champ d'identification ;
- le client de marché refuse toutes les fonctions privées essayées (ordres, annulations, soldes, retraits…) et toute écriture d'identifiant ;
- toutes les méthodes d'un `LiveBroker` créé sans constructeur refusent, sans un seul appel à l'exchange ;
- un compte actif « hérité » est écarté et jamais utilisé, dans quatre combinaisons (compte réel ou papier, courtier réel ou imitation), à travers onze chemins (ordres, relevés, réglages, urgence, vue d'ensemble…) ;
- l'annulation d'un ordre réel **existant** est refusée par un 403 explicite, et l'ordre reste inchangé ;
- quatre pannes simulées des contrôles conduisent toutes à un refus ;
- les fausses clés n'apparaissent ni dans le journal, ni dans les réponses de l'API ;
- le trading en argent fictif continue de fonctionner.

Tous ces tests utilisent de fausses clés, un faux exchange et un marché simulé. Toute tentative de connexion réseau les fait échouer.

## 6. Limites connues

- Le verrou protège contre les erreurs et les chemins oubliés. Il ne protège pas contre une personne qui **réécrirait volontairement le code** : aucun logiciel ne le peut.
- CCXT reste installé, car il sert aux données publiques. Sans clé, ses fonctions privées échouent de toute façon chez la plateforme : c'est une barrière supplémentaire, indépendante de Sentinelle.
- L'isolation réseau au niveau du système (pare-feu) n'est pas gérée par Sentinelle. Les tests bloquent les connexions à l'intérieur de Python seulement.
- Une revue indépendante (Codex) est attendue avant de considérer l'exigence comme satisfaite.
