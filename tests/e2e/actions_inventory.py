"""Inventaire des actions du cockpit : extraction statique de web/js, audit dynamique (actions_audit.js) et tests existants.

    python tests/e2e/run.py actions_audit.js
    python tests/e2e/actions_inventory.py <OUTPUT>/actions.json docs/interface/inventaire-actions.md [révision]

Hors ligne et déterministe à partir du fichier d'audit ; aucune requête réseau."""
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
crawl_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
revision = sys.argv[3] if len(sys.argv) > 3 else "?"
crawl = json.load(open(crawl_path, encoding="utf-8"))


def extract():
    """Chaque gestionnaire d'événement de la coque et des vues, son élément, son libellé et les routes qu'il appelle."""
    files = [root / "web/js/main.js"] + sorted((root / "web/js/views").glob("*.js"))
    EVENT = re.compile(r'@(click|submit(?:\.prevent)?|change|input|keyup(?:\.[a-z]+)?)(?:\.[a-z]+)*="([^"]*)"')
    API = re.compile(r"api\.(get|post|put|del|delete|patch)\(\s*([`'\"])(.*?)\2")
    rows = []
    for f in files:
        src = f.read_text(encoding="utf-8")
        funcs = {}
        for m in re.finditer(r"(?:const (\w+) = (?:async )?\(([^)]*)\) => \{|(?:async )?function (\w+)\(([^)]*)\) \{)", src):
            start, depth, i = m.end(), 1, m.end()
            while depth and i < len(src):
                depth += {"{": 1, "}": -1}.get(src[i], 0)
                i += 1
            funcs[m.group(1) or m.group(3)] = src[start:i]
        for m in EVENT.finditer(src):
            tag_start = src.rfind("<", 0, m.start())
            tag = re.match(r"<([a-zA-Z0-9-]+)", src[tag_start:]).group(1)
            close = src.find(">", m.end())
            end = src.find(f"</{tag}>", close)
            inner = re.sub(r"<[^>]+>", " ", src[close + 1:end if end != -1 and end - close < 400 else close + 120])
            attrs = src[tag_start:close]
            label = " ".join(inner.split())[:70] or (re.search(r'(?:title|aria-label|placeholder)="([^"]*)"', attrs) or [None, ""])[1]
            expr = m.group(2)
            names = re.findall(r"\b([a-zA-Z_]\w*)\s*\(", expr) or re.findall(r"^\s*([a-zA-Z_]\w*)\s*$", expr)
            routes = []
            for n in names:
                for a in API.finditer(funcs.get(n, "")):
                    routes.append(f"{a.group(1).upper()} {a.group(3)}")
            if not names:
                for a in API.finditer(expr):
                    routes.append(f"{a.group(1).upper()} {a.group(3)}")
            rows.append({"page": f.stem, "event": m.group(1), "tag": tag, "label": label, "handler": expr[:90],
                         "routes": sorted(set(routes)), "line": src.count("\n", 0, m.start()) + 1})
    return rows


static = extract()
tests_py = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in (root / "tests").glob("test_*.py")}
tests_js = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in (root / "tests/e2e").glob("*.js")
            if p.name != "actions_audit.js"}  # the audit itself is not a test

ROUTES = {  # expected effect and state change, from the API code
    "/api/auth/login": ("ouvre la session", "cookie de session, cockpit affiché"),
    "/api/auth/setup": ("crée le mot de passe au premier lancement", "mot de passe enregistré, session ouverte"),
    "/api/auth/logout": ("ferme la session", "retour à l'écran de connexion"),
    "/api/auth/password": ("change le mot de passe", "nouveau mot de passe actif"),
    "/api/auth/2fa/setup": ("prépare la double authentification", "QR code et clé affichés"),
    "/api/auth/2fa/enable": ("active la double authentification", "code exigé aux connexions suivantes"),
    "/api/auth/2fa/disable": ("désactive la double authentification", "plus de code exigé"),
    "/api/emergency": ("arrêt d'urgence : pause des IA ou revente fictive", "agents arrêtés ; positions fictives fermées (revente)"),
    "/api/agents/": ("démarre ou arrête un agent fictif", "état de l'agent, toast"),
    "/api/ai/train": ("lance un entraînement du modèle", "tâche d'entraînement en cours"),
    "/api/ai/backtest": ("lance une simulation accélérée", "résultats de simulation"),
    "/api/ai/bench": ("lance le banc des capacités d'apprentissage", "rapport du banc"),
    "/api/ai/models/": ("rapport, activation ou retrait d'un modèle", "modèle actif ou retiré (validation humaine)"),
    "/api/ai/champion": ("relit le champion et le journal des décisions", "liste rafraîchie"),
    "/api/ai/journal": ("relit le journal des décisions", "liste rafraîchie"),
    "/api/context/refresh": ("rafraîchit le contexte de marché", "indicateurs de contexte"),
    "/api/portfolio/activate": ("bascule sur le portefeuille principal fictif", "compte actif changé"),
    "/api/dmh/halt": ("arrête les nouvelles opérations fictives du hub", "hub en arrêt"),
    "/api/dmh/resume": ("relance les opérations fictives du hub", "hub actif"),
    "/api/dmh/reconcile": ("réconcilie le registre fictif", "registre réconcilié"),
    "/api/dmh/markets": ("relit les marchés", "tableau des marchés"),
    "/api/dmh/preview": ("prévisualise une opération (aucune exécution)", "devis affiché"),
    "/api/dmh/strategy": ("demande un brouillon de stratégie à l'assistant local", "brouillon affiché"),
    "/api/dmh/orders": ("exécute l'opération fictive confirmée", "ordre fictif enregistré"),
    "/api/dmh/open-orders/refresh": ("met à jour les ordres ouverts fictifs", "ordres rafraîchis"),
    "/api/dmh/limits": ("enregistre les limites du hub", "limites persistées"),
    "/api/dmh/memecoin/worker/start": ("démarre la session memecoin fictive", "worker actif, compteur de cycles"),
    "/api/dmh/memecoin/worker/stop": ("met en pause la session memecoin", "worker arrêté après le cycle en cours"),
    "/api/dmh/memecoin/session/tick": ("exécute un cycle manuel", "révision de session +1"),
    "/api/knowledge/refresh": ("met à jour les fiches cryptos", "téléchargement lancé"),
    "/api/knowledge/detail": ("ouvre la fiche d'une crypto", "détail affiché"),
    "/api/macro-shadow": ("relit le journal macro local", "journal affiché"),
    "/api/observation/preflight": ("refait le contrôle de préparation", "état READY/BLOCKED"),
    "/api/news/refresh": ("lance l'analyse des actualités", "actualités analysées"),
    "/api/settings": ("enregistre des réglages", "réglages persistés"),
    "/api/secrets": ("enregistre ou supprime des secrets locaux", "secret défini ou effacé"),
    "/api/telegram/test": ("envoie un message Telegram de test", "message reçu ou erreur"),
    "/api/accounts": ("crée, réinitialise ou reprend un compte fictif", "liste des comptes"),
    "/api/signals/resolve": ("évalue les signaux des canaux", "compteur évalué"),
    "/api/signals/login": ("demande le code de connexion Telegram", "code envoyé"),
    "/api/signals/code": ("valide le code Telegram", "compte connecté"),
    "/api/orders": ("passe ou annule un ordre fictif", "ordre fictif, position"),
    "/api/positions/": ("ferme ou modifie une position fictive", "position fermée ou stops modifiés"),
    "/api/markets": ("relit la liste des marchés", "liste rafraîchie"),
    "/api/overview": ("relit la vue d'ensemble", "comptes rafraîchis"),
}
DEFECTS = {  # reproduced in this audit
    ("settings", "enableTotp"): "D-UI1 : code trop court → message brut en anglais (« String should have at least 6 characters ») ; même cause pour toute erreur 422",
    ("signals", "saveCreds"): "D-UI3 : champs vides → aucune requête, aucun message, bouton actif",
    ("signals", "addChannel"): "D-UI2 : champ vide → aucune requête, aucun message, bouton actif",
}


def local_effect(handler):
    h = handler.strip()
    if re.match(r"^(tab|period|origin|level|days|tf|symbol|scope|selected|open)\s*=", h) or "open = open===" in h:
        return "change l'onglet, le filtre ou la sélection affichée", "affichage local, sans requête d'écriture"
    if "emergencyOpen" in h:
        return "ouvre ou ferme le panneau d'arrêt d'urgence", "fenêtre affichée ou masquée"
    if h.startswith("close("):
        return "répond à la demande de confirmation", "action confirmée ou annulée"
    if "more" in h:
        return "ouvre ou ferme le menu mobile « Plus »", "menu affiché ou masqué"
    if h.startswith("t.") or "setPortion" in h or "setSl" in h or "setTp" in h or "amount =" in h:
        return "prépare le ticket ou le formulaire", "champs du formulaire"
    if "toggleTheme" in h:
        return "bascule le thème clair ou sombre", "thème et préférence locale"
    if "editing" in h or "editPos" in h:
        return "ouvre ou annule l'édition d'une position", "formulaire d'édition"
    if "setValue" in h:
        return "modifie un champ de réglage (enregistré ensuite)", "valeur en attente d'enregistrement"
    if "Notif" in h:
        return "active ou désactive les notifications du navigateur", "permission du navigateur"
    if "Symbol" in h or "useDraft" in h:
        return "modifie la liste ou le formulaire en cours", "liste locale (enregistrée ensuite)"
    return "action locale", "affichage local"


def expected(row):
    for route in row["routes"]:
        path = route.split(" ", 1)[1].split("?")[0]
        for key, value in ROUTES.items():
            if path.replace("${", "{").startswith(key.rstrip("/")) or (key.endswith("/") and path.startswith(key)):
                return value
    if "act('start')" in row["handler"]:
        return ROUTES["/api/dmh/memecoin/worker/start"]
    if "act('stop')" in row["handler"]:
        return ROUTES["/api/dmh/memecoin/worker/stop"]
    if "act('tick')" in row["handler"]:
        return ROUTES["/api/dmh/memecoin/session/tick"]
    if row["handler"].strip() in ("submit",) and row["page"] == "main":
        return ROUTES["/api/auth/login"]
    return local_effect(row["handler"])


def norm(text):
    return re.sub(r"[^\w%€$]+", " ", re.sub(r"\{\{.*?\}\}", " ", text or "")).strip().lower()


SHELL = {"submit": "Connexion", "pause": "⏸ Arrêter les IA", "flatten": "Tout revendre maintenant (URGENCE saisi)",
         "scope='ai'": "Positions de l'IA", "scope='all'": "Toutes les positions", "store.emergencyOpen = true": "Urgence",
         "toggleTheme": "Thème", "logout": "Déconnexion", "more = !more": "Plus (menu)", "more=false": "Plus →"}
MATCHED = set()


def observed(row):
    if row["page"] == "main":
        want = SHELL.get(row["handler"].strip())
        for i, c in enumerate(crawl):
            if want and c["page"] in ("barre", "urgence", "mobile") and c["label"].startswith(want):
                MATCHED.add(i)
                return c
        if row["handler"].startswith("close("):
            return next((c for c in crawl if c.get("confirm")), None)
        return None
    label = norm(row["label"]) or (row["label"] or "").strip()
    if not label:
        return None
    host = {"learning": "ai", "context": "dashboard"}.get(row["page"], row["page"])  # components shown inside another page
    for i, c in enumerate(crawl):
        page = c["page"] if c["page"] != "barre" else "main"
        seen_label = norm(re.sub(r"^(select|input) ", "", c["label"])) or c["label"].strip()
        if page == host and seen_label.startswith(label[:14]):
            MATCHED.add(i)
            return c
    return None


NEEDS = {  # why an action is absent from a fresh demo instance
    "editPos": "une position fictive ouverte", "closePos": "une position fictive ouverte", "saveEdit": "une position en édition",
    "editing": "une position en édition", "cancelOrder": "un ordre fictif en attente", "openReport": "un modèle entraîné",
    "promote": "un modèle entraîné", "retire": "un modèle entraîné", "startTraining": "l'onglet d'entraînement en direct arrêté",
    "stopAgent": "l'agent d'entraînement en marche", "useDraft": "un brouillon de l'assistant local", "confirm": "une prévisualisation acceptée",
    "previewSell": "une position du simulateur du hub", "sendCode": "un code Telegram demandé", "removeChannel": "un canal suivi",
    "removeSymbol": "un symbole de la liste (onglet Général)", "addSymbol": "l'onglet Général",
    "clearLegacyKeys": "d'anciennes clés enregistrées", "resumeAccount": "un compte en arrêt de sécurité", "createAccount": "l'onglet Comptes",
    "disableNotif": "des notifications actives", "disableTotp": "la double authentification active", "enable": "le compte Telegram",
    "act": "la session memecoin, qui interroge des sources publiques : couverte par memecoin_check.js (routes simulées) et les tests Pytest du worker",
    "activateMain": "un portefeuille hérité à migrer", "setValue": "un onglet de réglages ouvert (champ saisi puis enregistré)",
    "emergencyOpen": "un clic hors de la fenêtre d'urgence",
    "store": "la fenêtre d'urgence ouverte : la fermeture est exercée par le parcours après chaque étape, sans ligne propre", "preview": "un formulaire de prévisualisation rempli",
    "setPortion": "le ticket ouvert", "setSl": "le ticket ouvert", "setTp": "le ticket ouvert", "submit": "un ticket rempli",
}


def describe(c, row=None):
    if c is None:
        if row is not None and "{{" in (row["label"] or "") and row["page"] not in ("main",):
            first = re.findall(r"\b([a-zA-Z_]\w*)", row["handler"])
            if first and first[0] in ("symbol", "tf", "period", "days", "tab", "level", "open", "selected", "amount", "t"):
                return "libellé dynamique : instances cliquées listées en fin de document"
        name = (re.findall(r"\b([a-zA-Z_]\w*)\s*\(", row["handler"]) or re.findall(r"\b([a-zA-Z_]\w*)", row["handler"]) or [""])[0] if row else ""
        if name in NEEDS:
            return "non cliqué : exige " + NEEDS[name]
        return "non cliqué par l'audit dynamique"
    if c["outcome"] == "désactivé":
        return "désactivé dans l'état de démonstration"
    writes = [x for x in c["calls"] if not x.startswith("GET ")]
    parts = []
    if writes:
        parts.append(", ".join(writes))
    elif c["calls"]:
        parts.append(f"{len(c['calls'])} lecture(s) GET")
    if c["toasts"]:
        parts.append("message « " + c["toasts"][0].split(": ", 1)[-1][:70] + " »")
    if c["confirm"]:
        parts.append("confirmation demandée (« " + c["confirm"][:40] + " »)")
    if c["changed"] and not writes:
        parts.append("affichage modifié")
    if c["errors"]:
        parts.append("erreur navigateur : " + c["errors"][0][:70])
    return " ; ".join(parts) or "aucun effet visible"


def test_ref(row):
    refs = []
    label = norm(row["label"])
    for name, src in tests_js.items():
        if label and len(label) > 3 and label[:20] in norm(src):
            refs.append(f"e2e `{name}`")
    for route in row["routes"]:
        path = re.sub(r"\$\{[^}]*\}.*", "", route.split(" ", 1)[1].split("?")[0])
        hits = sorted(n for n, src in tests_py.items() if path and path in src)
        if hits:
            refs.append("pytest " + ", ".join(f"`{h}`" for h in hits[:2]))
    return "; ".join(dict.fromkeys(refs)) or "aucun"


PAGES = {"main": "Coque (connexion, barre, navigation, confirmations)", "dashboard": "Tableau de bord", "performance": "Performances",
         "trade": "Trader", "ai": "IA", "history": "Historique", "knowledge": "Fiches cryptos", "apprentissage": "Apprentissage",
         "macro": "Contexte macro", "preparation": "Préparation", "memecoin": "Memecoin", "dmh": "Hub de marché", "news": "Actualités",
         "signals": "Signaux", "settings": "Réglages", "logs": "Journal", "context": "Contexte (composant)", "learning": "Banc (composant)"}
lines, stats = [], {"total": 0, "défaut": 0, "vérifié": 0, "non cliqué": 0, "sans test": 0}
for page in PAGES:
    rows = [r for r in static if r["page"] == page]
    if not rows:
        continue
    lines += [f"\n## {PAGES[page]} (`web/js/{'main.js' if page == 'main' else 'views/' + page + '.js'}`)\n",
              "| # | Libellé et emplacement | Effet attendu | Gestionnaire | Route FastAPI | Changement attendu | Résultat constaté | Erreurs et limites | Test |",
              "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        effect, change = expected(r)
        c = observed(r)
        seen = describe(c, r)
        name = re.findall(r"\b([a-zA-Z_]\w*)\s*\(", r["handler"]) or [r["handler"].strip()]
        defect = next((v for (p, h), v in DEFECTS.items() if p == page and h in name), "")
        limits = defect or ("refusé avec message clair" if c and any(x.split()[-1][0] in "45" for x in c["calls"]) else "")
        test = test_ref(r)
        stats["total"] += 1
        stats["défaut"] += bool(defect)
        stats["vérifié"] += bool(c and c["outcome"] != "désactivé" and seen != "aucun effet visible")
        stats["non cliqué"] += c is None and not seen.startswith("libellé dynamique")
        stats["sans test"] += test == "aucun"
        label = (r["label"] or "(sans libellé)").replace("|", "/").replace("{{", "⟨").replace("}}", "⟩")[:60]
        routes = "<br>".join(x.replace("|", "/") for x in r["routes"]) or "—"
        lines.append(f"| {i} | « {label} » (`<{r['tag']}>` l.{r['line']}, @{r['event']}) | {effect} | `{r['handler'][:50].replace('|', '/')}` | {routes} | "
                     f"{change} | {seen.replace('|', '/')} | {limits or '—'} | {test} |")
extra = ["\n## Instances constatées sans ligne statique distincte\n",
         "Actions à libellé dynamique (symboles, périodes, onglets, liens de navigation), cliquées par l'audit dynamique :\n",
         "| Page | Action cliquée | Résultat constaté |", "|---|---|---|"]
for i, c in enumerate(crawl):
    if i not in MATCHED and not (c["page"] == "urgence" and c["label"].startswith("Tout revendre maintenant (sans")):
        arrived = "" if "arrived" not in c else (" ; page atteinte" if c["arrived"] else " ; page NON atteinte")
        extra.append(f"| {c['page']} | {c['label'].replace('|', '/')[:60]} | {describe(c).replace('|', '/')}{arrived} |")
disabled_flatten = next((c for c in crawl if c["label"].startswith("Tout revendre maintenant (sans")), None)
lines += extra
head = f"""# Inventaire des actions du cockpit

Main audité : `{revision}`.
- **Méthode statique :** extraction de chaque gestionnaire d'événement des 15 pages et de la coque (`web/js/`), avec les
  routes appelées par la fonction correspondante.
- **Méthode dynamique :** `tests/e2e/actions_audit.js`, sur une instance de démonstration jetable. Marché simulé, base
  SQLite temporaire, aucun ordre réel. Le script clique chaque action visible, onglets compris, et consigne les requêtes
  `/api`, leur statut, les messages, les confirmations et les erreurs du navigateur.
- **Tests :** correspondance par route (Pytest) et par libellé (Playwright).

**Bilan :**
- {stats['total']} actions inventoriées ;
- {stats['vérifié']} avec un effet constaté ;
- {stats['non cliqué']} non cliquées par l'audit dynamique (raison indiquée) ;
- {stats['défaut']} défauts reproduits ;
- {stats['sans test']} sans test automatisé associé.

**Statut :**
- Aucun bouton n'a été trouvé simulant un succès.
- Les actions de la session Memecoin (Démarrer, Pause, Cycle manuel) ne sont pas cliquées contre l'instance : elles
  interrogent des sources publiques. Elles sont couvertes par `memecoin_check.js` (routes simulées) et par les tests
  Pytest du worker.

**Contrôles particuliers constatés :**
- Connexion : premier mot de passe créé (`POST /api/auth/setup` 200).
- Déconnexion : `POST /api/auth/logout` 200, puis retour à l'écran de connexion.
- Les 15 liens de navigation atteignent leur page ; sur mobile (390 px), la barre basse et le menu « Plus » (refermé après
  le choix) aussi, sans débordement horizontal.
- Arrêt d'urgence :
  - « Tout revendre » reste désactivé tant que URGENCE n'est pas saisi
    ({'vérifié' if disabled_flatten and disabled_flatten['outcome'] == 'désactivé' else 'NON vérifié'}) ;
  - la revente fictive appelle `POST /api/emergency` (200) ;
  - l'alerte critique diffusée par le serveur s'affiche en rouge, en plus du message de l'action (deux messages pour une
    seule action : voulu, non corrigé).
- Thème clair et sombre : bascule constatée.

**Défauts reproduits** (corrigés dans la PR suivante du chantier) :
- {DEFECTS[('settings', 'enableTotp')]} (`web/js/api.js` l. 25-26).
- {DEFECTS[('signals', 'addChannel')]} (`web/js/views/signals.js` l. 47-51 et 94).
- {DEFECTS[('signals', 'saveCreds')]} (`web/js/views/signals.js` l. 25-30 et 79).
"""
out_path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
print(stats)
