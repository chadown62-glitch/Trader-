"""Capacités d'apprentissage de l'IA : banc d'essai, suivi en direct, fiches de connaissance par crypto."""

from __future__ import annotations

import copy
import math
import time

import numpy as np

from ..ai.bench import run_bench
from ..ai.coins import coin_info
from ..ai.knowledge import brief, compute_profile
from ..ai.model import RECIPE_LABELS
from ..storage.db import dumps, loads
from .common import UserError

BENCH_SIZES = (1500, 4000)
BENCH_SEEDS = 3
KNOWLEDGE_DAYS = 2900  # les fiches sont calculées sur l'historique le plus long possible (environ 8 ans)


class LearningMixin:
    """Méthodes de l'application (banc d'essai de l'apprentissage, suivi en direct, fiches par crypto)."""

    # ======================================================================
    # Banc d'essai : que sait apprendre l'IA ?
    # ======================================================================
    def start_bench(self) -> dict:
        if self.jobs.running("bench"):
            raise UserError("Un test des capacités d'apprentissage est déjà en cours")
        ai = copy.deepcopy(self.settings["ai"])

        def job(progress):
            rep = run_bench(BENCH_SIZES, BENCH_SEEDS, ai["timeframe"], progress, recipe=ai["recipe"], half_life_days=ai["half_life_days"])
            rep["recipe"] = ai["recipe"]
            rep["half_life_days"] = ai["half_life_days"]
            self.db.set_meta("bench_report", dumps(rep))
            self.log_experiment("bench", {"sizes": list(BENCH_SIZES), "seeds": BENCH_SEEDS, "timeframe": ai["timeframe"], "recipe": ai["recipe"],
                                          "half_life_days": ai["half_life_days"]},
                                {"score": rep["score"], "adaptation": rep["adaptation"], "false_discoveries": rep["false_discoveries"],
                                 "noise_runs": rep["noise_runs"]})
            score = "—" if rep["score"] is None else f"{rep['score'] * 100:.0f} %"
            self.db.log("info", "ia", f"Test des capacités d'apprentissage terminé : taux d'apprentissage {score}")
            return rep

        return self.jobs.submit("bench", "Test des capacités d'apprentissage", job)

    def bench_report(self) -> dict | None:
        return loads(self.db.get_meta("bench_report"), None)

    # ======================================================================
    # Suivi de l'apprentissage en direct : les prévisions se vérifient-elles ?
    # ======================================================================
    def learning_monitor(self) -> dict:
        """Compare, sur les situations vues en direct, ce que l'IA annonçait et ce qui s'est réellement passé."""
        ai = self.settings["ai"]
        rows = self.db.query(
            "SELECT p_win, expected_r, decision, outcome_r, resolved_at FROM candidates WHERE source = 'live' AND timeframe = ?",
            (ai["timeframe"],),
        )
        resolved = [r for r in rows if r["outcome_r"] is not None]
        scored = [r for r in resolved if r["p_win"] is not None]
        out: dict = {"seen": len(rows), "resolved": len(resolved), "scored": len(scored), "pending": len(rows) - len(resolved)}
        if scored:
            p = np.array([r["p_win"] for r in scored], dtype=float)
            y = np.array([1.0 if r["outcome_r"] > 0 else 0.0 for r in scored])
            base = float(y.mean())
            brier = float(np.mean((p - y) ** 2))
            ref = float(np.mean((base - y) ** 2))
            se = math.sqrt(max(base * (1 - base), 1e-9) / len(y))
            gap = float(p.mean() - base)
            out["forecast"] = {
                "announced": float(p.mean()), "observed": base, "gap": gap, "n": len(y),
                "skill": (1 - brier / ref) if ref > 0 else None,
                # écart significatif entre ce que l'IA annonce et ce qui arrive (au-delà de 2 erreurs types)
                "drift": bool(len(y) >= 30 and abs(gap) > 2 * se), "optimistic": bool(gap > 0),
            }
            if len(y) >= 30 and 0 < y.sum() < len(y):
                from sklearn.metrics import roc_auc_score

                out["forecast"]["auc"] = float(roc_auc_score(y, p))
        last = float(self.db.get_meta("last_train_ts", "0") or 0)
        new = self.db.scalar("SELECT COUNT(*) FROM candidates WHERE source = 'live' AND resolved_at > ?", (last,), 0)
        champion = self.models.champion(ai["timeframe"])
        trial = champion is None
        if trial:
            champion = self.models.trial(ai["timeframe"])
        out["retraining"] = {
            "enabled": bool(ai["learning"]), "last": last or None, "every_hours": ai["retrain_hours"],
            "new_samples": int(new), "min_new_samples": ai["min_new_samples"],
            "next": (last + ai["retrain_hours"] * 3600) if last else None,
        }
        out["model"] = None
        if champion is not None:
            chosen = getattr(champion, "chosen", None) or "riche"
            out["model"] = {"version": champion.version, "level": RECIPE_LABELS.get(chosen, chosen), "trial": trial,
                            "half_life_days": getattr(champion, "half_life_days", None),
                            "mode": (champion.metrics or {}).get("mode")}
        return out

    # ======================================================================
    # Fiches de connaissance par crypto
    # ======================================================================
    def start_knowledge(self) -> dict:
        if self.jobs.running("knowledge"):
            raise UserError("La mise à jour des fiches est déjà en cours")
        settings = copy.deepcopy(self.settings)
        tf = settings["ai"]["timeframe"]

        def job(progress):
            hist, btc = self._histories(settings, tf, KNOWLEDGE_DAYS, lambda x, m: progress(0.8 * x / 0.25 if x <= 0.25 else 0.8, m))
            n = self.update_knowledge(hist, btc, tf, settings, lambda x, m: progress(0.8 + 0.2 * x, m))
            return {"updated": n}

        return self.jobs.submit("knowledge", "Mise à jour des fiches par crypto", job)

    def refresh_knowledge_if_stale(self, max_age_hours: float = 24.0) -> bool:
        """Relance le calcul des fiches (historique complet) si l'une manque ou date de plus d'un jour."""
        if self.jobs.running("knowledge"):
            return False
        rows = {r["symbol"]: r["updated_at"] for r in self.db.query("SELECT symbol, updated_at FROM knowledge")}
        wl = self.settings["general"]["watchlist"]
        if all(s in rows and time.time() - rows[s] < max_age_hours * 3600 for s in wl):
            return False
        self.start_knowledge()
        return True

    def update_knowledge(self, hist: dict, btc, tf: str, settings: dict, progress=None) -> int:
        """Recalcule la fiche de chaque crypto à partir de son historique complet (appelé aussi à chaque entraînement)."""
        quote = settings["general"]["quote"]
        btc_sym = f"BTC/{quote}"
        btc_df = hist.get(btc_sym) if btc_sym in hist else btc
        fee, slip = settings["paper"]["fee_rate"], settings["paper"]["slippage_bps"] / 10_000
        profiles = {}
        for k, (sym, df) in enumerate(hist.items()):
            if progress:
                progress(k / max(1, len(hist)), f"Fiche {sym}")
            try:
                prof = compute_profile(sym, df, tf, btc=None if sym == btc_sym else btc_df, fee_rate=fee, slip=slip)
            except Exception as exc:  # une crypto à l'historique atypique ne doit pas bloquer les autres
                self.db.log("warning", "ia", f"Fiche {sym} impossible à calculer : {str(exc)[:150]}")
                continue
            m = self.hub.markets.get(sym) if self.hub.markets else None
            if m is not None:
                prof["rules"] = {"price_step": m.price_step, "amount_step": m.amount_step, "min_amount": m.min_amount,
                                 "min_notional": m.min_notional, "taker_fee": m.taker_fee}
            try:
                ms = self.shield.microstructure(sym, 1000.0)
                if ms:
                    prof["book"] = {"spread_bps": round(ms["spread_bps"], 3), "depth_quote": round(ms["depth_quote"], 0), "ts": time.time()}
            except Exception:
                pass
            prof["enabled_strategies"] = [n for n, on in settings["ai"]["strategies"].items() if on]
            prof["updated"] = time.time()
            profiles[sym] = prof
        # rangs parmi les cryptos suivies (1 = la plus volatile / la plus liquide)
        full = [p for p in profiles.values() if not p.get("insufficient")]
        for key, path in (("volatility_rank", ("volatility", "annual")), ("liquidity_rank", ("liquidity", "daily_volume_30d"))):
            order = sorted(full, key=lambda p: -(p[path[0]][path[1]] or 0))
            for i, p in enumerate(order):
                p[key] = {"rank": i + 1, "of": len(order)}
        for sym, prof in profiles.items():
            self.db.execute(
                "INSERT INTO knowledge(symbol, timeframe, updated_at, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET timeframe = excluded.timeframe, updated_at = excluded.updated_at, data = excluded.data",
                (sym, tf, prof["updated"], dumps(prof)),
            )
        if progress:
            progress(1.0, "Fiches mises à jour")
        return len(profiles)

    def knowledge_list(self) -> dict:
        wl = self.settings["general"]["watchlist"]
        rows = {r["symbol"]: r for r in self.db.query("SELECT symbol, updated_at, data FROM knowledge")}
        items = []
        for sym in wl:
            r = rows.get(sym)
            if r is None:
                info = coin_info(sym.split("/")[0])
                items.append({"symbol": sym, "name": info["name"], "kind": info["kind"], "missing": True})
                continue
            prof = loads(r["data"], {})
            items.append({**brief(prof), "volatility_rank": prof.get("volatility_rank"), "liquidity_rank": prof.get("liquidity_rank")})
        return {"items": items, "updated": max((r["updated_at"] for r in rows.values()), default=None),
                "running": bool(self.jobs.running("knowledge") or self.jobs.running("train"))}

    def knowledge_get(self, symbol: str) -> dict | None:
        r = self.db.one("SELECT data FROM knowledge WHERE symbol = ?", (symbol,))
        if r is not None:
            return loads(r["data"], None)
        if symbol in self.settings["general"]["watchlist"]:
            return {"symbol": symbol, "info": coin_info(symbol.split("/")[0]), "missing": True}
        return None
