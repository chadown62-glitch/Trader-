"""Agents IA : démarrage, arrêt, bulletin de préparation, arrêt d'urgence."""

from __future__ import annotations

import time

from ..agent.agent import TradingAgent
from ..execution.base import OrderError
from .common import AGENTS, EMERGENCY_CONFIRM, LIVE_CONFIRM, UserError
from .readiness import compute_readiness


class AgentsMixin:
    """Méthodes de l'application (agents IA : démarrage, arrêt, bulletin de préparation, arrêt d'urgence)."""

    # ======================================================================
    # Agents IA
    # ======================================================================
    def agents_status(self) -> list[dict]:
        out = []
        for name, d in AGENTS.items():
            agent = self.agents.get(name)
            st = {"name": name, "label": d["label"], "mode": d["mode"], **self.agent_state.get(name, {})}
            acc = self.db.one("SELECT id, name FROM accounts WHERE role = ? AND archived = 0", (d["role"],))
            st["account_id"] = acc["id"] if acc else None
            st["account_name"] = acc["name"] if acc else None
            if agent:
                st.update(agent.status())
            row = self.db.one("SELECT started_at FROM agents WHERE name = ?", (name,))
            st["started_at"] = row["started_at"] if row else None
            model = self.models.champion(self.settings["ai"]["timeframe"])
            st["model"] = model.version if model else None
            out.append(st)
        return out

    def readiness(self) -> dict:
        acc = self.db.one("SELECT id FROM accounts WHERE role = 'ai_training' AND archived = 0")
        return compute_readiness(self.db, self.settings, acc["id"] if acc else None, self.models.champion(self.settings["ai"]["timeframe"]))

    def start_agent(self, name: str, confirm: str | None = None, auto: bool = False) -> dict:
        if name not in AGENTS:
            raise UserError("Agent inconnu")
        if name in self.agents or self.agent_state[name]["status"] == "préparation":
            return self.agent_state[name]
        d = AGENTS[name]
        tf = self.settings["ai"]["timeframe"]
        if d["mode"] == "live":
            if not self.live_status.get("connected"):
                raise UserError("Portefeuille réel non connecté (clés API manquantes ou refusées)")
            if self.models.champion(tf) is None:
                raise UserError("Aucun modèle d'IA validé : l'IA ne peut pas trader en réel")
            rd = self.readiness()
            if not rd["unlocked"]:
                missing = [c["label"] for c in rd["criteria"] if not c["ok"]]
                raise UserError("Mode réel verrouillé. Conditions manquantes : " + " ; ".join(missing))
            if not auto and (confirm or "").strip().upper() != LIVE_CONFIRM:
                raise UserError(f"Pour confirmer, tapez exactement : {LIVE_CONFIRM}")
            acc = self.live_account()
        else:
            acc = self.db.one("SELECT * FROM accounts WHERE role = 'ai_training' AND archived = 0")
        if acc is None:
            raise UserError("Compte associé introuvable")
        self.agent_state[name] = {"status": "préparation", "message": "Téléchargement de l'historique des prix…"}
        self.bus.publish("agent", {"name": name, **self.agent_state[name]})
        symbols = self._agent_symbols()
        days = max(self.settings["market"]["history_days"], 260 if tf != "15m" else 220)

        def prepare(progress):
            for k, sym in enumerate(symbols):
                progress(k / max(1, len(symbols)), f"Historique {sym}")
                self.history.update(sym, tf, days)
            self.call(self._activate_agent, name, acc["id"], tf, symbols, days).result(timeout=600)
            return {"symbols": symbols}

        try:
            self.jobs.submit("prepare", f"Préparation : {d['label']}", prepare)
        except RuntimeError as exc:
            self.agent_state[name] = {"status": "arrêté", "message": str(exc)}
            raise UserError(str(exc)) from None
        return self.agent_state[name]

    def _agent_symbols(self) -> list[str]:
        quote = self.settings["general"]["quote"]
        syms = [s for s in self.settings["general"]["watchlist"] if not self.hub.markets or s in self.hub.markets]
        btc = f"BTC/{quote}"
        if btc not in syms and (not self.hub.markets or btc in self.hub.markets):
            syms.append(btc)  # contexte de marché
        return syms

    def _activate_agent(self, name: str, account_id: int, tf: str, symbols: list[str], days: int) -> None:
        try:
            for sym in symbols:
                self.hub.track(sym, tf, days)
            rt = self.runtime(account_id)
            d = AGENTS[name]
            budget_fn = (lambda: self.settings["live"]["budget"]) if d["mode"] == "live" else None
            news_fn = None
            for ext in self.extensions:
                if hasattr(ext, "score_for"):
                    news_fn = ext.score_for
            agent = TradingAgent(
                name, d["mode"], self.db, rt.positions, rt.risk, lambda: self.settings,
                lambda: self.models.champion(tf), timeframe=tf, news_fn=news_fn,
                fresh_fn=lambda s: self.hub.is_fresh(s, 180), budget_fn=budget_fn, bus=self.bus,
                price_check_fn=self._price_check,
                clock=self.market_clock if d["mode"] != "live" else time.time,
            )
            agent.running = True
            for sym in symbols:  # ne pas rejouer la dernière bougie déjà clôturée
                s = self.hub.series(sym, tf)
                if len(s):
                    agent.last_bar[sym] = int(s["ts"].iloc[-1])
                if sym in self.settings["general"]["watchlist"]:
                    feat = self._features_for(sym, tf)
                    if feat is not None:
                        agent.record_scan(sym, feat)
            self.agents[name] = agent
            now = time.time()
            self.db.execute(
                "INSERT INTO agents(name, account_id, enabled, started_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(name) DO UPDATE SET enabled = 1, account_id = excluded.account_id, started_at = excluded.started_at",
                (name, account_id, now),
            )
            self.agent_state[name] = {"status": "en marche", "message": None}
            self.db.log("info", "agents", f"{AGENTS[name]['label']} démarrée ({tf}, {', '.join(symbols)})")
        except Exception as exc:
            self.agent_state[name] = {"status": "arrêté", "message": f"Démarrage impossible : {exc}"}
            raise
        finally:
            self.bus.publish("agent", {"name": name, **self.agent_state[name]})

    def _price_check(self, symbol: str, price: float) -> str | None:
        m = self.settings["market"]
        if not m.get("cross_check") or m["source"] == "synthetic" or self.crosscheck is None:
            return None
        return self.crosscheck.check(symbol, price, m["cross_check_max_pct"])

    def stop_agent(self, name: str, close_positions: bool = False) -> dict:
        agent = self.agents.pop(name, None)
        if agent:
            agent.running = False
            if close_positions:
                rt = self.runtimes.get(agent.account_id)
                if rt:
                    for p in rt.positions.open_positions(agent=name):
                        try:
                            rt.positions.close(p["id"], "Arrêt de l'IA")
                        except OrderError as exc:
                            self.db.log("error", "agents", f"Fermeture impossible ({p['symbol']}) : {exc.message}")
        self.db.execute("UPDATE agents SET enabled = 0 WHERE name = ?", (name,))
        self.agent_state[name] = {"status": "arrêté", "message": None}
        self.db.log("info", "agents", f"{AGENTS.get(name, {}).get('label', name)} arrêtée")
        self.bus.publish("agent", {"name": name, **self.agent_state[name]})
        return self.agent_state[name]

    def _restart_agent_later(self, name: str) -> None:
        self.stop_agent(name)
        try:
            self.start_agent(name, auto=True)
        except UserError as exc:
            self.db.log("warning", "agents", f"Redémarrage impossible : {exc}")

    # ======================================================================
    # Arrêt d'urgence
    # ======================================================================
    def emergency(self, action: str, confirm: str | None = None, scope: str = "ai") -> dict:
        stopped = [n for n in list(self.agents)]
        for n in stopped:
            self.stop_agent(n)
        closed, errors = 0, []
        if action == "flatten":
            if (confirm or "").strip().upper() != EMERGENCY_CONFIRM:
                raise UserError(f"Pour tout revendre, tapez exactement : {EMERGENCY_CONFIRM}")
            for rt in list(self.runtimes.values()):
                for p in rt.positions.open_positions():
                    if scope == "ai" and p["origin"] != "ai":
                        continue
                    try:
                        rt.positions.close(p["id"], "Arrêt d'urgence")
                        closed += 1
                    except OrderError as exc:
                        errors.append(f"{p['symbol']} : {exc.message}")
        self.db.log("critical" if action == "flatten" else "warning", "urgence",
                    f"Arrêt d'urgence ({action}) : {len(stopped)} IA arrêtée(s), {closed} position(s) fermée(s)", {"errors": errors})
        self.bus.publish("alert", {"level": "critical", "message": f"Arrêt d'urgence exécuté ({closed} position(s) fermée(s))"})
        return {"agents_stopped": stopped, "positions_closed": closed, "errors": errors}

    def resume_account(self, account_id: int) -> None:
        self.runtime(account_id).risk.resume()
        self.db.log("info", "risque", f"Reprise manuelle du compte {account_id} après arrêt de sécurité")
