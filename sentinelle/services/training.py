"""Entraînement de l'IA et simulations accélérées (tâches de fond)."""

from __future__ import annotations

import copy
import time
from typing import Any

from ..ai.trainer import build_dataset, train_walk_forward
from ..backtest.engine import run_backtest
from ..market.history import last_continuous
from ..storage.db import dumps
from .common import UserError
from .learning import KNOWLEDGE_DAYS


class TrainingMixin:
    """Méthodes de l'application (entraînement de l'IA et simulations accélérées (tâches de fond))."""

    # ======================================================================
    # Entraînement et backtests
    # ======================================================================
    def start_training(self, days: int | None = None, auto: bool = False) -> dict:
        tf = self.settings["ai"]["timeframe"]
        days = int(days or self.settings["market"]["history_days"])
        days = max(180, min(days, 3000))
        settings = copy.deepcopy(self.settings)

        def job(progress):
            return self._train(progress, settings, tf, days, auto)

        try:
            return self.jobs.submit("train", f"Entraînement de l'IA ({tf}, {days} jours)", job)
        except RuntimeError as exc:
            raise UserError(str(exc)) from None

    def _histories(self, settings: dict, tf: str, days: int, progress, extended_ok: bool = True) -> tuple[dict, Any]:
        quote = settings["general"]["quote"]
        wl = [s for s in settings["general"]["watchlist"] if not self.hub.markets or s in self.hub.markets]
        if not wl:
            raise UserError("Aucune des cryptos suivies n'est disponible sur la source de données")
        btc_sym = f"BTC/{quote}"
        need = wl + ([btc_sym] if btc_sym not in wl else [])
        hist = {}
        for k, sym in enumerate(need):
            progress(0.25 * k / len(need), f"Téléchargement de l'historique {sym}")
            try:
                hist[sym] = self.history.update(sym, tf, days)
            except Exception as exc:
                if sym in wl:
                    raise UserError(f"Historique indisponible pour {sym} : {exc}") from None
                continue
            # Never append a differently denominated USDT history as EUR/USD:
            # a historical FX series and its timestamp alignment are required.
            hist[sym] = last_continuous(hist[sym])
        btc = hist.get(btc_sym) if btc_sym not in wl else None
        return {s: hist[s] for s in wl if s in hist}, btc

    def _train(self, progress, settings: dict, tf: str, days: int, auto: bool) -> dict:
        hist, btc = self._histories(settings, tf, days, progress)
        enabled = [n for n, on in settings["ai"]["strategies"].items() if on]
        fee, slip = settings["paper"]["fee_rate"], settings["paper"]["slippage_bps"] / 10_000
        progress(0.28, "Calcul des caractéristiques et des situations d'apprentissage")
        cand, _ = build_dataset(hist, tf, enabled, fee, slip, btc=btc)

        def sub(x, msg):
            progress(0.3 + 0.45 * x, msg)

        res = train_walk_forward(cand, tf, min_edge=settings["ai"]["min_edge_r"], progress=sub,
                                 rules_fallback=settings["ai"].get("rules_fallback", True),
                                 recipe=settings["ai"]["recipe"], half_life_days=settings["ai"]["half_life_days"])
        try:
            if days >= KNOWLEDGE_DAYS:  # les fiches par crypto profitent de l'historique complet déjà chargé
                self.update_knowledge(hist, btc, tf, settings)
            else:  # entraînement sur une période courte : les fiches gardent leur historique complet
                self.refresh_knowledge_if_stale()
        except Exception as exc:
            self.db.log("warning", "ia", f"Fiches par crypto non mises à jour : {str(exc)[:150]}")
        report = dict(res.report)
        report.update(verdict=res.verdict, reasons=res.reasons, days=days, auto=auto, trained_at=time.time())
        if res.eval_model is not None and res.holdout_start:
            progress(0.8, "Simulation du portefeuille sur la période de contrôle (avec l'IA)")
            markets = {s: self.hub.markets[s] for s in hist if s in self.hub.markets}
            bt = run_backtest(hist, tf, settings, res.eval_model, start_ts=res.holdout_start, btc=btc, markets=markets)
            progress(0.9, "Simulation de référence (tous les signaux, sans l'IA)")
            base = run_backtest(hist, tf, settings, None, exploration=True, start_ts=res.holdout_start, btc=btc, markets=markets)
            report["backtest_model"] = {k: bt[k] for k in ("metrics", "equity_metrics", "equity", "buy_hold_pct", "final_equity", "initial", "fees_paid", "start", "end", "halted")}
            report["backtest_baseline"] = {k: base[k] for k in ("metrics", "equity_metrics", "equity", "buy_hold_pct", "final_equity", "initial", "fees_paid", "halted")}
        version = None
        if res.final_model is not None:
            row = self.models.register(res, report)
            version = row["version"]
        self.db.set_meta("last_train_ts", str(time.time()))
        self.db.set_meta("last_train_report", dumps({k: report.get(k) for k in ("verdict", "reasons", "summary", "n_resolved", "trained_at")}))
        level = "info" if res.ok else "warning"
        self.db.log(level, "ia", f"Entraînement terminé : modèle {res.verdict}" + (f" ({version})" if version else ""), {"reasons": res.reasons})
        self.bus.publish("training_done", {"verdict": res.verdict, "version": version, "reasons": res.reasons})
        return {"verdict": res.verdict, "ok": res.ok, "reasons": res.reasons, "version": version, "summary": report.get("summary"),
                "report": report}

    def _maybe_retrain(self) -> None:
        ai = self.settings["ai"]
        if not ai["learning"] or "ia_entrainement" not in self.agents or self.jobs.running("train"):
            return
        last = float(self.db.get_meta("last_train_ts", "0") or 0)
        if time.time() - last < ai["retrain_hours"] * 3600:
            return
        new_samples = self.db.scalar("SELECT COUNT(*) FROM candidates WHERE source = 'live' AND resolved_at > ?", (last,), 0)
        if last and new_samples < ai["min_new_samples"]:
            return
        self.db.log("info", "ia", "Ré-entraînement automatique avec les données les plus récentes")
        try:
            self.start_training(auto=True)
        except UserError:
            pass

    def start_backtest(self, days: int, use_model: bool) -> dict:
        tf = self.settings["ai"]["timeframe"]
        settings = copy.deepcopy(self.settings)
        days = max(30, min(int(days), 3000))

        def job(progress):
            warm = 210 if tf != "4h" else 230
            hist, btc = self._histories(settings, tf, days + warm, progress)
            model = self.models.champion(tf) if use_model else None
            if use_model and model is None:
                raise UserError("Aucun modèle validé : lancez la simulation « tous les signaux »")
            start = int((time.time() - days * 86400) * 1000)
            markets = {s: self.hub.markets[s] for s in hist if s in self.hub.markets}
            res = run_backtest(hist, tf, settings, model, start_ts=start, btc=btc, markets=markets, exploration=not use_model,
                               progress=lambda x, m: progress(0.3 + 0.7 * x, m))
            res["use_model"] = use_model
            res["model"] = model.version if model else None
            res["days"] = days
            return res

        try:
            return self.jobs.submit("backtest", f"Simulation sur {days} jours", job)
        except RuntimeError as exc:
            raise UserError(str(exc)) from None
