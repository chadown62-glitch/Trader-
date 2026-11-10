"""Bot Telegram : notifications (B) et commandes (C).

Sécurité :
- le bot ne répond qu'à VOTRE identifiant Telegram (liste blanche) ;
- tant que l'identifiant n'est pas configuré, il se contente d'indiquer
  l'identifiant de la personne qui lui écrit (pour la configuration) ;
- les commandes sensibles demandent une confirmation par bouton ;
- l'IA en argent réel ne peut pas être démarrée depuis Telegram (panel seulement).
"""

from __future__ import annotations

import html
import logging
import threading
import time

import httpx

log = logging.getLogger("sentinelle.telegram")

API = "https://api.telegram.org"

HELP = (
    "<b>Commandes Sentinelle</b>\n"
    "/etat : état du bot et des IA\n"
    "/positions : positions ouvertes\n"
    "/gains : résultats des comptes\n"
    "/actus : dernières actualités importantes\n"
    "/fiche BTC : ce que Sentinelle sait d'une crypto\n"
    "/pause : arrêter les IA (les positions gardent leurs stops)\n"
    "/reprendre : relancer l'IA d'entraînement\n"
    "/urgence : tout revendre et tout arrêter (avec confirmation)\n"
    "/aide : cette aide"
)

LEVELS = {"all": 0, "important": 1, "critical": 2}


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def money(x) -> str:
    if x is None:
        return "—"
    return f"{x:,.2f}".replace(",", " ")


class TelegramService:
    name = "telegram"

    def __init__(self, app, transport=None):
        self.app = app
        self.transport = transport
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.offset = 0
        self.state = {"connected": False, "bot": None, "error": None, "last_update": None}
        self._last_summary_day = None
        self._unknown_seen: set[int] = set()
        app.bus.add_listener(self._on_event)

    @property
    def cfg(self) -> dict:
        return self.app.settings["telegram"]

    def token(self) -> str | None:
        return self.app.secrets.get("telegram_token")

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=40.0, transport=self.transport)

    def _call(self, method: str, **params):
        tok = self.token()
        if not tok:
            raise RuntimeError("Jeton Telegram non configuré")
        with self._client() as c:
            r = c.post(f"{API}/bot{tok}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "erreur Telegram"))
        return data["result"]

    # -- envoi -------------------------------------------------------------------
    def send(self, text: str, chat_id: str | int | None = None, buttons: list | None = None) -> bool:
        chat = chat_id or self.cfg.get("chat_id")
        if not chat or not self.token():
            return False
        params = {"chat_id": chat, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        try:
            self._call("sendMessage", **params)
            return True
        except Exception as exc:
            self.state["error"] = str(exc)[:200]
            return False

    def notify(self, text: str, level: str = "important") -> None:
        if not self.cfg["enabled"]:
            return
        if LEVELS.get(level, 1) < LEVELS.get(self.cfg["notify_level"], 1):
            return
        threading.Thread(target=self.send, args=(text,), daemon=True).start()

    def _on_event(self, topic: str, event: dict) -> None:
        d = event.get("data", {})
        try:
            if topic == "position_opened" and d.get("origin") == "ai":
                acc = self.app.db.one("SELECT name, kind FROM accounts WHERE id = ?", (d.get("account_id"),)) or {}
                tag = "💶 RÉEL" if acc.get("kind") == "live" else "🧪 Entraînement"
                self.notify(f"{tag} — <b>Achat {esc(d.get('symbol'))}</b>\n{esc(d.get('reason_open') or '')}", "all" if acc.get("kind") != "live" else "important")
            elif topic == "trade_closed":
                acc = self.app.db.one("SELECT name, kind FROM accounts WHERE id = ?", (d.get("account_id"),)) or {}
                tag = "💶 RÉEL" if acc.get("kind") == "live" else ("🧪 Entraînement" if d.get("origin") == "ai" else "📝 Papier")
                pnl = d.get("realized_pnl") or 0.0
                r = d.get("pnl_r")
                emoji = "✅" if pnl > 0 else "❌"
                txt = (f"{tag} — {emoji} <b>Vente {esc(d.get('symbol'))}</b> ({esc(d.get('reason_close') or '')})\n"
                       f"Résultat : {money(pnl)}" + (f" ({r:+.2f} R)" if r is not None else ""))
                self.notify(txt, "important" if acc.get("kind") == "live" else "all")
            elif topic == "alert":
                self.notify(f"⚠️ {esc(d.get('message'))}", "critical" if d.get("level") == "critical" else "important")
            elif topic == "training_done":
                self.notify(f"🧠 Entraînement terminé : modèle <b>{esc(d.get('verdict'))}</b>\n" + esc(" ".join(d.get("reasons") or [])[:800]), "important")
            elif topic == "news":
                s = d.get("sentiment", 0)
                self.notify(f"📰 {'🔴' if s < -0.3 else '🟢' if s > 0.3 else '⚪'} <b>{esc(d.get('title'))}</b>\n{esc(d.get('summary') or '')}", "important" if abs(s) >= 0.6 else "all")
        except Exception:
            log.exception("Notification Telegram impossible")

    # -- réception ------------------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="sentinelle-telegram", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not (self.cfg["enabled"] and self.token()):
                self.state["connected"] = False
                self._stop.wait(10)
                continue
            try:
                if not self.state["bot"]:
                    me = self._call("getMe")
                    self.state["bot"] = me.get("username")
                updates = self._call("getUpdates", offset=self.offset, timeout=25, allowed_updates=["message", "callback_query"])
                self.state.update(connected=True, error=None, last_update=time.time())
                for u in updates:
                    self.offset = max(self.offset, u["update_id"] + 1)
                    self.handle_update(u)
                self._daily_summary()
            except Exception as exc:
                self.state.update(connected=False, error=str(exc)[:200])
                self._stop.wait(15)

    def authorized(self, chat_id) -> bool:
        cid = str(self.cfg.get("chat_id") or "").strip()
        return bool(cid) and str(chat_id) == cid

    def handle_update(self, u: dict) -> None:
        if "callback_query" in u:
            cq = u["callback_query"]
            chat = cq.get("message", {}).get("chat", {}).get("id")
            if not self.authorized(chat):
                return
            try:
                self._call("answerCallbackQuery", callback_query_id=cq["id"])
            except Exception:
                pass
            if cq.get("data") == "urgence_ok":
                res = self.app.call(self.app.emergency, "flatten", "URGENCE", "ai").result(timeout=60)
                self.send(f"🛑 Arrêt d'urgence exécuté : {res['positions_closed']} position(s) IA fermée(s), IA arrêtées.", chat)
            elif cq.get("data") == "urgence_annule":
                self.send("Arrêt d'urgence annulé.", chat)
            return
        msg = u.get("message") or {}
        chat = msg.get("chat", {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return
        if not self.authorized(chat):
            if not self.cfg.get("chat_id") and text.startswith("/start"):
                self.send(f"Bonjour ! Votre identifiant Telegram est <code>{chat}</code>.\n"
                          "Copiez-le dans le panel Sentinelle (Réglages → Telegram) pour recevoir les alertes.", chat)
            elif chat not in self._unknown_seen:
                self._unknown_seen.add(chat)
                self.app.db.log("warning", "telegram", f"Message ignoré d'un compte non autorisé ({chat})")
            return
        words = text.split()
        cmd = words[0].split("@")[0].lower()
        reply = self.command(f"{cmd} {words[1]}" if cmd == "/fiche" and len(words) > 1 else cmd)
        if cmd == "/urgence":
            self.send(reply, chat, buttons=[[{"text": "🛑 Confirmer : tout revendre", "callback_data": "urgence_ok"},
                                             {"text": "Annuler", "callback_data": "urgence_annule"}]])
        else:
            self.send(reply, chat)

    def command(self, cmd: str) -> str:
        app = self.app
        if cmd in ("/start", "/aide", "/help"):
            return HELP
        if cmd == "/etat":
            agents = app.call(app.agents_status).result(timeout=30)
            lines = ["<b>État de Sentinelle</b>", f"Données de marché : {'✅' if app.hub.status.get('ok') else '⚠️'}"]
            for a in agents:
                lines.append(f"{esc(a['label'])} : {esc(a.get('status'))}")
            live = app.live_status
            lines.append(f"Portefeuille réel : {'connecté' if live.get('connected') else 'non connecté'}")
            sh = app.shield.status()
            pauses = [v["reason"] for v in sh["volatility"].values()] + ([sh["cascade"]["reason"]] if sh["cascade"] else [])
            pauses += [sh["news"]] if sh["news"] else []
            if not sh["enabled"]:
                lines.append("Bouclier temps réel : désactivé")
            else:
                lines.append("Bouclier temps réel : " + ("⏸ " + esc(" ; ".join(pauses)) if pauses else "✅ aucune pause"))
            for ext in app.extensions:
                if getattr(ext, "name", "") == "context" and ext.summary():
                    lines.append(f"Contexte : {esc(ext.summary())}")
            return "\n".join(lines)
        if cmd == "/positions":
            ov = app.call(app.overview).result(timeout=30)
            lines = ["<b>Positions ouvertes</b>"]
            for acc in ov["accounts"]:
                for p in acc.get("positions") or []:
                    lines.append(f"{esc(acc['name'])} · {esc(p['symbol'])} : {p['qty']:g} @ {money(p['entry_price'])}, "
                                 f"latent {money(p.get('pnl'))}, stop {money(p.get('stop_price'))}")
            return "\n".join(lines) if len(lines) > 1 else "Aucune position ouverte."
        if cmd in ("/gains", "/rapport"):
            ov = app.call(app.overview).result(timeout=30)
            lines = ["<b>Résultats</b>"]
            for acc in ov["accounts"]:
                if acc.get("equity") is None:
                    continue
                lines.append(f"{esc(acc['name'])} : {money(acc['equity'])} {esc(acc.get('quote', ''))}"
                             f" (jour {money(acc.get('day_pnl'))}, total {money(acc.get('total_pnl'))})")
            return "\n".join(lines)
        if cmd == "/actus":
            rows = app.db.query("SELECT title, sentiment, importance FROM news WHERE sentiment IS NOT NULL ORDER BY published_ts DESC LIMIT 30")
            rows = sorted(rows, key=lambda r: -r["importance"])[:6]
            if not rows:
                return "Aucune actualité analysée (activez le module Actualités et Ollama)."
            return "<b>Actualités importantes</b>\n" + "\n".join(
                f"{'🔴' if r['sentiment'] < -0.3 else '🟢' if r['sentiment'] > 0.3 else '⚪'} {esc(r['title'])}" for r in rows)
        if cmd.startswith("/fiche"):
            base = cmd.split()[1].upper() if len(cmd.split()) > 1 else ""
            sym = next((s for s in app.settings["general"]["watchlist"] if s.split("/")[0] == base), None)
            if sym is None:
                return "Indiquez une crypto suivie, par exemple : /fiche BTC"
            prof = app.knowledge_get(sym) or {}
            info = prof.get("info") or {}
            lines = [f"<b>{esc(info.get('name', base))} ({esc(base)})</b>", esc(info.get("what", ""))]
            if prof.get("reading"):
                lines += ["• " + esc(x) for x in prof["reading"][:7]]
            else:
                lines.append("Statistiques pas encore calculées : ouvrez « Fiches cryptos » dans le panel et cliquez sur « Mettre à jour ».")
            return "\n".join(lines)
        if cmd == "/pause":
            res = app.call(app.emergency, "pause").result(timeout=60)
            return f"⏸ IA arrêtées : {', '.join(res['agents_stopped']) or 'aucune en marche'}. Les positions gardent leurs stops."
        if cmd == "/reprendre":
            try:
                app.call(app.start_agent, "ia_entrainement").result(timeout=60)
                return "▶️ IA d'entraînement relancée (l'IA réelle ne se démarre que depuis le panel)."
            except Exception as exc:
                return f"Impossible : {esc(exc)}"
        if cmd == "/urgence":
            return "⚠️ <b>Arrêt d'urgence</b> : toutes les positions de l'IA seront revendues au prix du marché et les IA arrêtées. Confirmez :"
        return "Commande inconnue. Tapez /aide."

    def _daily_summary(self) -> None:
        if not self.cfg["enabled"]:
            return
        lt = time.localtime()
        if lt.tm_hour != self.cfg["daily_summary_hour"] or self._last_summary_day == lt.tm_yday:
            return
        self._last_summary_day = lt.tm_yday
        self.send("🗓 <b>Résumé du jour</b>\n" + self.command("/gains").split("\n", 1)[-1])

    # -- panel ------------------------------------------------------------------------
    def status(self) -> dict:
        return {"enabled": self.cfg["enabled"], "chat_configured": bool(self.cfg.get("chat_id")),
                "token_set": bool(self.token()), **self.state}

    def register_routes(self, api) -> None:
        from fastapi import HTTPException

        @api.post("/api/telegram/test")
        async def telegram_test():
            import asyncio

            ok = await asyncio.to_thread(self.send, "✅ Sentinelle est bien connectée à votre Telegram.")
            if not ok:
                raise HTTPException(400, self.state.get("error") or "Envoi impossible : vérifiez le jeton et votre identifiant")
            return {"ok": True}
