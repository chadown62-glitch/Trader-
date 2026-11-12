"""Entraînement de l'IA et simulations accélérées (tâches de fond)."""

from __future__ import annotations

import copy
import time
from typing import Any

from ..ai.compare import compare_models
from ..ai.trainer import build_dataset, train_walk_forward
from ..backtest.engine import run_backtest
from ..execution.base import OrderError
from ..market.history import continuous_history, last_continuous
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
        extended = bool(settings["ai"].get("extended_history", True)) and extended_ok
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
            alt = f"{sym.split('/')[0]}/USDT"
            older = None
            if extended and quote != "USDT" and (not self.hub.markets or alt in self.hub.markets):
                try:  # paire USDT plus ancienne : davantage d'années pour apprendre
                    older = self.history.update(alt, tf, days)
                except Exception:
                    older = None
            # jamais de trou de cotation dans l'historique d'apprentissage (paire retirée puis recotée)
            hist[sym] = continuous_history(hist[sym], older) if older is not None else last_continuous(hist[sym])
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

        # Plus on a essayé de configurations différentes, plus la preuve exigée est forte (journal des expériences).
        config = self.training_config(settings, tf)
        alpha, n_configs = self.training_alpha(settings, tf)
        res = train_walk_forward(cand, tf, min_edge=settings["ai"]["min_edge_r"], progress=sub,
                                 rules_fallback=settings["ai"].get("rules_fallback", True),
                                 recipe=settings["ai"]["recipe"], half_life_days=settings["ai"]["half_life_days"], alpha=alpha)
        res.report["experiments"] = {"distinct_configs": n_configs, "alpha": alpha, "confidence_pct": (1 - alpha) * 100}
        if n_configs > 1:
            res.reasons.append(f"{n_configs} configurations différentes ont été essayées récemment : la confiance exigée a été portée de 95 % à "
                               + f"{(1 - alpha) * 100:.2f}".replace(".", ",") + " %.")
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
            # même taille de risque que la simulation avec l'IA : sinon les deux résultats ne sont pas comparables
            base = run_backtest(hist, tf, settings, None, exploration=True, start_ts=res.holdout_start, btc=btc, markets=markets,
                                exploration_risk_factor=1.0)
            report["backtest_model"] = {k: bt[k] for k in ("metrics", "equity_metrics", "equity", "buy_hold_pct", "final_equity", "initial", "fees_paid", "start", "end", "halted")}
            report["backtest_baseline"] = {k: base[k] for k in ("metrics", "equity_metrics", "equity", "buy_hold_pct", "final_equity", "initial", "fees_paid", "halted")}
        version, comparison, status = None, None, None
        if res.final_model is not None:
            champion = self.models.champion(tf)
            if champion is not None and res.ok and res.eval_model is not None and res.holdout_start:
                # Champion contre nouveau modèle, sur la période de contrôle du nouveau. Seules comptent les
                # situations que le champion n'a pas vues pendant son propre apprentissage.
                progress(0.95, "Comparaison avec le champion sur les mêmes situations")
                hold = cand[cand["decision_ts"] >= res.holdout_start]
                comparison = compare_models(hold, champion, res.eval_model, challenger_seen_until=res.holdout_start)
            row = self.models.register(res, report, comparison)
            version, status = row["version"], row["status"]
            if comparison is not None:
                res.reasons.append("Face au champion : " + comparison["explanation"])
        self.log_experiment("train", config, {
            "verdict": res.verdict, "version": version, "status": status, "days": days, "auto": auto, "alpha": alpha,
            "distinct_configs": n_configs, "summary": report.get("summary"), "n_resolved": report.get("n_resolved"),
            "comparison": {k: comparison.get(k) for k in ("verdict", "explanation", "champion", "champion_knew_share")} if comparison else None})
        try:
            self.review_models()
        except Exception as exc:
            self.db.log("warning", "ia", f"Revue des modèles impossible : {str(exc)[:150]}")
        self.db.set_meta("last_train_ts", str(time.time()))
        self.db.set_meta("last_train_report", dumps({k: report.get(k) for k in ("verdict", "reasons", "summary", "n_resolved", "trained_at")}))
        level = "info" if res.ok else "warning"
        self.db.log(level, "ia", f"Entraînement terminé : modèle {res.verdict}" + (f" ({version})" if version else ""), {"reasons": res.reasons})
        self.bus.publish("training_done", {"verdict": res.verdict, "version": version, "reasons": res.reasons})
        return {"verdict": res.verdict, "ok": res.ok, "reasons": res.reasons, "version": version, "status": status,
                "summary": report.get("summary"), "comparison": comparison, "report": report}

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

    def backtest_conditions(self, capital: float | None = None) -> dict:
        """Conditions de la simulation accélérée : celles du portefeuille principal (capital, coût de conversion).

        La simulation est tenue dans la monnaie de cotation, sur un compte à part. Le capital en euros est converti
        une fois, au taux du moment ; le coût de conversion s'ajoute aux frais de chaque ordre."""
        quote = self.settings["general"]["quote"]
        if self.portfolio_mode() != "main":
            return {"initial": float(capital or self.settings["paper"]["initial_balance"]), "extra_cost": 0.0, "currency": quote,
                    "capital": float(capital or self.settings["paper"]["initial_balance"]), "fx": None}
        cfg = self.settings["portfolio"]
        main = self.main_account()
        amount = float(capital or (main["initial_balance"] if main else cfg["initial"]))
        if not (10 <= amount <= 10_000_000):
            raise UserError("Capital simulé entre 10 et 10 000 000")
        fxq = self.fx.quote(quote, for_trading=False)
        return {"initial": amount / fxq.rate, "extra_cost": cfg["fx_cost_bps"] / 10_000, "currency": cfg["currency"],
                "capital": amount, "fx": fxq.to_dict(self.market_clock())}

    def start_backtest(self, days: int, use_model: bool, capital: float | None = None) -> dict:
        tf = self.settings["ai"]["timeframe"]
        settings = copy.deepcopy(self.settings)
        days = max(30, min(int(days), 3000))
        try:
            cond = self.backtest_conditions(capital)
        except OrderError as exc:
            raise UserError(exc.message) from None

        def job(progress):
            warm = 210 if tf != "4h" else 230
            hist, btc = self._histories(settings, tf, days + warm, progress)
            model = self.models.champion(tf) if use_model else None
            if use_model and model is None:
                raise UserError("Aucun modèle validé : lancez la simulation « tous les signaux »")
            start = int((time.time() - days * 86400) * 1000)
            markets = {s: self.hub.markets[s] for s in hist if s in self.hub.markets}
            res = run_backtest(hist, tf, settings, model, start_ts=start, btc=btc, markets=markets, exploration=not use_model,
                               progress=lambda x, m: progress(0.3 + 0.7 * x, m),
                               initial=cond["initial"], extra_cost=cond["extra_cost"])
            res["conditions"] = {**cond, "quote": settings["general"]["quote"]}
            self.log_experiment("backtest", {"days": days, "use_model": use_model, "model": model.version if model else None, "timeframe": tf,
                                             "capital": cond["capital"], "currency": cond["currency"]},
                                {"return_pct": res["equity_metrics"]["return_pct"], "max_dd_pct": res["equity_metrics"]["max_dd_pct"],
                                 "trades": res["metrics"]["n"], "mean_r": res["metrics"]["mean_r"]})
            res["use_model"] = use_model
            res["model"] = model.version if model else None
            res["days"] = days
            return res

        try:
            return self.jobs.submit("backtest", f"Simulation sur {days} jours", job)
        except RuntimeError as exc:
            raise UserError(str(exc)) from None
