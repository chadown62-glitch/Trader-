"""Agents IA : démarrage, arrêt, bulletin de préparation, arrêt d'urgence."""

from __future__ import annotations

import time

from ..agent.agent import TradingAgent
from ..execution.base import OrderError
from ..safety import forbid, is_paper_runtime
from .common import AGENTS, EMERGENCY_CONFIRM, RETIRED_AGENTS, UserError
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
            acc = self.ai_account()
            st["account_id"] = acc["id"] if acc else None
            st["account_name"] = acc["name"] if acc else None
            if agent:
                st.update(agent.status())
            row = self.db.one("SELECT started_at FROM agents WHERE name = ?", (name,))
            st["started_at"] = row["started_at"] if row else None
            tf = self.settings["ai"]["timeframe"]
            model = self.models.champion(tf)
            st["model_trial"] = False
            if model is None:  # à défaut de modèle validé : le modèle « à l'essai » (argent fictif)
                model = self.models.trial(tf)
                st["model_trial"] = model is not None
            st["model"] = model.version if model else None
            out.append(st)
        return out

    def readiness(self) -> dict:
        """Bulletin de fiabilité de l'IA en argent fictif. Il n'ouvre aucun mode réel."""
        acc = self.ai_account()
        return compute_readiness(self.db, self.settings, acc["id"] if acc else None, self.models.champion(self.settings["ai"]["timeframe"]))

    def start_agent(self, name: str, confirm: str | None = None, auto: bool = False) -> dict:
        # Simulation uniquement : l'IA en argent réel n'existe plus. Ni phrase de confirmation,
        # ni bulletin favorable, ni redémarrage automatique ne peuvent la lancer.
        if name in RETIRED_AGENTS or name not in AGENTS or AGENTS[name]["mode"] != "training":
            if name in RETIRED_AGENTS:
                forbid("démarrage de l'IA en argent réel", db=self.db, agent=name)
            raise UserError("Agent inconnu")
        if name in self.agents or self.agent_state[name]["status"] == "préparation":
            return self.agent_state[name]
        d = AGENTS[name]
        tf = self.settings["ai"]["timeframe"]
        acc = self.ai_account()  # portefeuille principal (partagé avec le trading manuel), ou ancien compte avant la migration
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
            news_fn = None
            for ext in self.extensions:
                if hasattr(ext, "score_for"):
                    news_fn = ext.score_for
            # modèle validé ; à défaut, le modèle « à l'essai » (argent fictif, taille réduite)
            def model_fn():
                return self.models.champion(tf) or self.models.trial(tf)

            agent = TradingAgent(
                name, d["mode"], self.db, rt.positions, rt.risk, lambda: self.settings,
                model_fn, timeframe=tf, news_fn=news_fn,
                fresh_fn=lambda s: self.hub.is_fresh(s, 180), bus=self.bus,
                price_check_fn=self._price_check,
                shield_fn=self.shield.check_entry,
                context_fn=self.decision_context,
                shadow_fn=self.record_shadow_predictions,
                clock=self.market_clock,
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

    def decision_context(self, symbol: str) -> dict:
        """Microstructure et sentiment au moment d'une décision (journal de recherche)."""
        out: dict = {}
        ms = self.shield.microstructure(symbol, 1000.0)
        if ms:
            out.update(spread_bps=round(ms["spread_bps"], 3), liquidite_0_5pct=round(ms["depth_quote"], 2))
        flow_fn = getattr(self.source, "fetch_trade_flow", None)
        if flow_fn is not None and self.settings["market"]["source"] != "synthetic":
            try:
                f = flow_fn(symbol, 15)
                total = f["buy"] + f["sell"]
                if total > 0:
                    out["flux_acheteur_15m"] = round((f["buy"] - f["sell"]) / total, 4)
                    out["flux_minutes"] = f.get("minutes")
            except Exception:
                pass
        for ext in self.extensions:
            if getattr(ext, "name", "") == "context":
                fg = ext.data.get("fear_greed") or {}
                if fg:
                    out["fear_greed"] = fg.get("value")
                fund = (ext.data.get("funding") or {}).get(symbol.split("/")[0])
                if fund:
                    out["funding"] = fund.get("rate")
        liq = self.shield.metrics.get("liquidations_15m_musd")
        if liq is not None:
            out["liquidations_15m_musd"] = round(liq, 2)
        return out

    def _price_check(self, symbol: str, price: float) -> str | None:
        m = self.settings["market"]
        if not m.get("cross_check") or m["source"] == "synthetic" or self.crosscheck is None:
            return None
        return self.crosscheck.check(symbol, price, m["cross_check_max_pct"])

    def stop_agent(self, name: str, close_positions: bool = False) -> dict:
        if name not in AGENTS:
            raise UserError("Agent inconnu")
        agent = self.agents.pop(name, None)
        if agent:
            agent.running = False
            if close_positions:
                rt = self.runtimes.get(agent.account_id)
                if rt is not None and not is_paper_runtime(rt):
                    self._evict_runtime(agent.account_id, "compte actif non simulé écarté")
                    rt = None
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
        self.paper_runtimes()  # sécurité : l'urgence n'agit que sur des comptes simulés
        stopped = [n for n in list(self.agents)]
        for n in stopped:
            self.stop_agent(n)
        closed, errors = 0, []
        if action == "flatten":
            if (confirm or "").strip().upper() != EMERGENCY_CONFIRM:
                raise UserError(f"Pour tout revendre, tapez exactement : {EMERGENCY_CONFIRM}")
            for _aid, rt in self.paper_runtimes():  # uniquement les comptes simulés
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
