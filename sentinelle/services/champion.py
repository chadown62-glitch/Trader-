"""Cycle d'apprentissage champion / challenger, surveillance après promotion, journal des expériences.

1. À chaque situation examinée par l'IA, les prédictions du champion, du challenger et de l'ancien champion
   encore suivi sont enregistrées AVANT que le résultat soit connu (`shadow_predictions`).
2. Quand le résultat est connu, les modèles sont comparés sur exactement les mêmes situations.
3. Le challenger ne devient champion que s'il fait mieux, avec une différence très probablement pas due au hasard.
4. Après une promotion, l'ancien champion reste suivi : si le nouveau fait nettement moins bien, retour automatique.
5. Chaque entraînement est inscrit au journal des expériences ; plus on essaie de configurations différentes,
   plus la preuve exigée pour valider un modèle est forte.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

import numpy as np
import pandas as pd

from ..ai.compare import WEEK_DAYS
from ..ai.features import FEATURE_COLUMNS
from ..ai.metrics import MIN_BLOCKS, bonferroni_alpha, comparison_verdict, paired_comparison, trade_metrics
from ..storage.db import dumps, loads
from .common import UserError

DAY = 86_400
MIN_DECISIVE = 30  # situations où les deux modèles décident différemment, pour pouvoir conclure
EXPERIMENT_WINDOW_DAYS = 180  # période sur laquelle on compte les configurations différentes déjà essayées
DRIFT_MIN_ROWS = 30
PSI_ALERT = 0.25  # au-delà, le marché ne ressemble plus aux données d'apprentissage


def psi(expected_edges: list[float], values: np.ndarray) -> float | None:
    """Indice de stabilité : compare la répartition actuelle d'une caractéristique à celle de l'apprentissage.

    Les bornes sont les déciles de l'apprentissage : chaque tranche y pesait la même part. 0 = identique ;
    au-delà de 0,25, la répartition a nettement changé."""
    values = values[np.isfinite(values)]
    if len(values) < DRIFT_MIN_ROWS or len(expected_edges) < 2:
        return None
    counts = np.bincount(np.searchsorted(np.asarray(expected_edges), values, side="right"), minlength=len(expected_edges) + 1)
    actual = np.clip(counts / counts.sum(), 1e-4, None)
    expected = np.full(len(counts), 1.0 / len(counts))
    return float(np.sum((actual - expected) * np.log(actual / expected)))


class ChampionMixin:
    """Méthodes de l'application : prédictions à blanc, comparaison champion / challenger, promotions, journal des expériences."""

    # ======================================================================
    # Prédictions enregistrées avant le résultat
    # ======================================================================
    def record_shadow_predictions(self, symbol: str, timeframe: str, bar_ts: int, rows: list[dict]) -> int:
        """Enregistre ce que chaque modèle suivi déciderait sur ces situations. Appelé par l'agent, avant toute issue."""
        models = self.models.shadows(timeframe)
        if not models or not rows:
            return 0
        frame = pd.DataFrame(rows)
        now = self.market_clock()
        n = 0
        for model in models:
            try:
                p, er = model.predict(frame)
            except Exception:
                continue
            for row, pk, ek in zip(rows, p, er):
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO shadow_predictions(symbol, timeframe, ts, strategy, model_version, p_win, expected_r, take, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (symbol, timeframe, int(bar_ts), row["strategy"], model.version, float(pk), float(ek), int(ek >= model.threshold), now))
                n += cur.rowcount or 0
        return n

    def live_comparison(self, timeframe: str, version_a: str, version_b: str, since: float | None = None) -> dict:
        """Compare deux modèles sur les situations vécues en direct, dénouées, où les deux avaient enregistré leur décision."""
        rows = self.db.query(
            "SELECT c.ts, c.outcome_r, a.take AS take_a, b.take AS take_b FROM candidates c "
            "JOIN shadow_predictions a ON a.symbol = c.symbol AND a.timeframe = c.timeframe AND a.ts = c.ts AND a.strategy = c.strategy AND a.model_version = ? "
            "JOIN shadow_predictions b ON b.symbol = c.symbol AND b.timeframe = c.timeframe AND b.ts = c.ts AND b.strategy = c.strategy AND b.model_version = ? "
            "WHERE c.source = 'live' AND c.timeframe = ? AND c.outcome_r IS NOT NULL AND c.ts >= ? ORDER BY c.ts",
            (version_a, version_b, timeframe, int((since or 0) * 1000)))
        r = np.array([x["outcome_r"] for x in rows], dtype=float)
        cmp = paired_comparison(r * np.array([x["take_a"] for x in rows], dtype=float), r * np.array([x["take_b"] for x in rows], dtype=float),
                                np.array([x["ts"] for x in rows], dtype=float), block_days=WEEK_DAYS)
        verdict, explanation = comparison_verdict(cmp, MIN_DECISIVE, MIN_BLOCKS)
        pending = self.db.scalar(
            "SELECT COUNT(*) FROM candidates c JOIN shadow_predictions b ON b.symbol = c.symbol AND b.timeframe = c.timeframe AND b.ts = c.ts "
            "AND b.strategy = c.strategy AND b.model_version = ? WHERE c.source = 'live' AND c.timeframe = ? AND c.outcome_r IS NULL AND c.ts >= ?",
            (version_b, timeframe, int((since or 0) * 1000)), 0)
        return {**cmp, "a": version_a, "b": version_b, "verdict": verdict, "explanation": explanation, "pending": int(pending),
                "since": since, "required": {"decisive": MIN_DECISIVE, "blocks": MIN_BLOCKS, "block_days": WEEK_DAYS}}

    # ======================================================================
    # Décisions : promotion, rejet, retour en arrière
    # ======================================================================
    def review_models(self) -> dict:
        """Applique les règles fixées d'avance. Appelé périodiquement et après chaque entraînement."""
        tf = self.settings["ai"]["timeframe"]
        out: dict = {"timeframe": tf, "action": None}
        champ_row = self.models._row("champion", tf)
        chal_row = self.models._row("challenger", tf)
        if champ_row and chal_row:
            cmp = self.live_comparison(tf, champ_row["version"], chal_row["version"], chal_row["created_at"])
            out["challenger"] = cmp
            if cmp["verdict"] == "meilleur":
                self.models.promote_challenger(tf, cmp)
                out["action"] = "promotion"
                self.bus.publish("alert", {"level": "info", "message": f"Nouveau champion : {chal_row['version']} a fait mieux que l'ancien sur les mêmes situations."})
            elif cmp["verdict"] == "moins bon":
                self.models.reject_challenger(tf, cmp)
                out["action"] = "rejet"
        mon = self.models.monitoring(tf)
        if mon and out["action"] is None:
            cmp = self.live_comparison(tf, mon["previous"], mon["champion"], mon["since"])
            out["monitoring"] = cmp
            if cmp["verdict"] == "moins bon":
                self.models.rollback(tf, "Retour automatique : depuis sa promotion, le nouveau champion fait moins bien que l'ancien sur les mêmes situations. "
                                     + cmp["explanation"], {"comparison": cmp, "automatic": True})
                out["action"] = "retour en arrière"
                self.bus.publish("alert", {"level": "warning", "message": f"Retour en arrière : {mon['previous']} redevient le champion."})
        return out

    def rollback_model(self, confirm: bool = False) -> dict:
        if confirm is not True:
            raise UserError("Confirmation nécessaire pour revenir au champion précédent")
        try:
            row = self.models.rollback(self.settings["ai"]["timeframe"], "Retour en arrière demandé par l'utilisateur.")
        except ValueError as exc:
            raise UserError(str(exc)) from None
        return {"champion": row["version"]}

    # ======================================================================
    # Surveillance du champion : résultats en direct, dérive du marché
    # ======================================================================
    def champion_health(self, timeframe: str) -> dict | None:
        model = self.models.champion(timeframe)
        if model is None:
            return None
        row = self.models.get(model.version)
        since = (row or {}).get("promoted_at") or (row or {}).get("created_at") or 0
        rows = self.db.query(
            "SELECT c.ts, c.outcome_r FROM candidates c JOIN shadow_predictions s ON s.symbol = c.symbol AND s.timeframe = c.timeframe "
            "AND s.ts = c.ts AND s.strategy = c.strategy AND s.model_version = ? AND s.take = 1 "
            "WHERE c.source = 'live' AND c.timeframe = ? AND c.outcome_r IS NOT NULL AND c.ts >= ? ORDER BY c.ts",
            (model.version, timeframe, int(since * 1000)))
        m = trade_metrics([r["outcome_r"] for r in rows], ts=[r["ts"] for r in rows])
        expected = (model.metrics or {}).get("holdout_mean_r")
        se = m.get("se_cluster") or m.get("se")
        degraded = bool(m["n"] >= 30 and se and expected is not None and m["mean_r"] + 1.645 * se < expected)
        losing = bool(m["n"] >= 30 and se and m["mean_r"] + 1.645 * se < 0)
        drift = self.feature_drift(model, timeframe)
        notes = []
        if losing:
            notes.append("En direct, les situations retenues par le champion perdent de l'argent de façon nette.")
        elif degraded:
            notes.append("En direct, le champion fait nettement moins bien que lors de sa validation.")
        if drift and drift["alert"]:
            notes.append("Le marché ne ressemble plus aux données sur lesquelles le champion a appris.")
        if m["n"] < 30:
            notes.append(f"Seulement {m['n']} situation(s) retenue(s) et dénouée(s) en direct : trop peu pour juger (30 au minimum).")
        return {"version": model.version, "since": since, "live": {k: m.get(k) for k in ("n", "mean_r", "win_rate", "ci_low", "ci_high", "blocks")},
                "expected_mean_r": expected, "degraded": degraded, "losing": losing, "drift": drift, "notes": notes}

    def feature_drift(self, model, timeframe: str, days: int = 60) -> dict | None:
        bins = getattr(model, "feature_bins", None) or {}
        if not bins:
            return None
        since = int((self.market_clock() - days * DAY) * 1000)
        rows = self.db.query("SELECT features FROM candidates WHERE source = 'live' AND timeframe = ? AND ts >= ?", (timeframe, since))
        feats = [loads(r["features"], {}) or {} for r in rows]
        scores = []
        for col in FEATURE_COLUMNS:
            if col not in bins:
                continue
            values = np.array([f.get(col) if isinstance(f.get(col), (int, float)) else np.nan for f in feats], dtype=float)
            score = psi(bins[col], values)
            if score is not None:
                scores.append({"feature": col, "psi": score})
        if not scores:
            return {"n": len(feats), "mean_psi": None, "alert": False, "top": [], "days": days}
        scores.sort(key=lambda s: -s["psi"])
        mean = float(np.mean([s["psi"] for s in scores]))
        return {"n": len(feats), "mean_psi": mean, "alert": mean > PSI_ALERT, "top": scores[:5], "days": days}

    # ======================================================================
    # Journal des expériences
    # ======================================================================
    @staticmethod
    def training_config(settings: dict, timeframe: str) -> dict:
        """Ce qui définit une « configuration » d'entraînement : la changer, c'est faire un nouvel essai."""
        ai = settings["ai"]
        return {"timeframe": timeframe, "strategies": sorted(n for n, on in ai["strategies"].items() if on), "recipe": ai["recipe"],
                "half_life_days": ai["half_life_days"], "min_edge_r": ai["min_edge_r"], "rules_fallback": ai.get("rules_fallback", True),
                "extended_history": ai.get("extended_history", True), "watchlist": sorted(settings["general"]["watchlist"]),
                "fee_rate": settings["paper"]["fee_rate"], "slippage_bps": settings["paper"]["slippage_bps"]}

    @staticmethod
    def config_hash(config: dict) -> str:
        # clés triées : deux configurations identiques ont la même empreinte, quel que soit l'ordre d'écriture
        return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]

    def log_experiment(self, kind: str, params: dict, result: dict | None = None) -> int:
        return self.db.insert("experiments", {"ts": time.time(), "kind": kind, "config_hash": self.config_hash(params),
                                              "params": dumps(params), "result": dumps(result) if result is not None else None})

    def distinct_training_configs(self, extra_hash: str | None = None) -> int:
        """Nombre de configurations d'entraînement DIFFÉRENTES essayées récemment (les ré-entraînements identiques ne comptent pas)."""
        since = time.time() - EXPERIMENT_WINDOW_DAYS * DAY
        hashes = {r["config_hash"] for r in self.db.query("SELECT DISTINCT config_hash FROM experiments WHERE kind = 'train' AND ts >= ?", (since,))}
        if extra_hash:
            hashes.add(extra_hash)
        return max(1, len(hashes))

    def training_alpha(self, settings: dict, timeframe: str) -> tuple[float, int]:
        """Risque d'erreur accepté pour valider un modèle : 5 % divisé par le nombre de configurations essayées."""
        n = self.distinct_training_configs(self.config_hash(self.training_config(settings, timeframe)))
        return bonferroni_alpha(n), n

    def experiments(self, limit: int = 100) -> dict:
        rows = self.db.query("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),))
        for r in rows:
            r["params"], r["result"] = loads(r["params"], {}), loads(r["result"], None)
        totals = {r["kind"]: r["n"] for r in self.db.query("SELECT kind, COUNT(*) AS n FROM experiments GROUP BY kind")}
        n = self.distinct_training_configs()
        validated = sum(1 for r in self.db.query("SELECT result FROM experiments WHERE kind = 'train'")
                        if (loads(r["result"], {}) or {}).get("verdict") == "validé")
        return {"rows": rows, "totals": totals, "distinct_training_configs": n, "alpha": bonferroni_alpha(n),
                "confidence_pct": (1 - bonferroni_alpha(n)) * 100, "trainings": totals.get("train", 0), "validated": validated,
                "window_days": EXPERIMENT_WINDOW_DAYS,
                "note": ("Chaque configuration différente essayée sur les mêmes données augmente le risque de « valider » un modèle par hasard. "
                         f"{n} configuration(s) différente(s) sur {EXPERIMENT_WINDOW_DAYS} jours : la confiance exigée est de "
                         + f"{(1 - bonferroni_alpha(n)) * 100:.2f}".replace(".", ",") + " %.")}

    # ======================================================================
    # Preuve d'apprentissage sur l'historique : modèle figé contre modèle ré-entraîné
    # ======================================================================
    def start_learning_proof(self, days: int | None = None, n_steps: int = 6) -> dict:
        """Mesure si ré-apprendre avec les données nouvelles fait mieux que garder un modèle figé (historique réel)."""
        if self.jobs.running("proof"):
            raise UserError("Une mesure est déjà en cours")
        import copy

        from ..ai.compare import frozen_vs_retrained
        from ..ai.trainer import build_dataset

        settings = copy.deepcopy(self.settings)
        tf = settings["ai"]["timeframe"]
        days = max(720, min(int(days or settings["market"]["history_days"]), 3000))

        def job(progress):
            hist, btc = self._histories(settings, tf, days, progress)
            enabled = [n for n, on in settings["ai"]["strategies"].items() if on]
            progress(0.28, "Calcul des situations d'apprentissage")
            cand, _ = build_dataset(hist, tf, enabled, settings["paper"]["fee_rate"], settings["paper"]["slippage_bps"] / 10_000, btc=btc)
            rep = frozen_vs_retrained(cand, tf, recipe=settings["ai"]["recipe"], half_life_days=settings["ai"]["half_life_days"], n_steps=n_steps,
                                      progress=lambda x, m: progress(0.3 + 0.7 * x, m))
            rep.update(days=days, timeframe=tf, symbols=sorted(hist), n_candidates=int(cand["outcome"].notna().sum()), ts=time.time())
            self.db.set_meta("learning_proof", dumps(rep))
            self.log_experiment("proof", {"days": days, "timeframe": tf, "n_steps": n_steps, "recipe": settings["ai"]["recipe"],
                                          "symbols": sorted(hist)},
                                {"verdict": rep["verdict"], "diff": (rep.get("comparison") or {}).get("diff"),
                                 "ci_low": (rep.get("comparison") or {}).get("ci_low"), "ci_high": (rep.get("comparison") or {}).get("ci_high")})
            self.db.log("info", "ia", f"Modèle figé contre ré-entraîné : {rep['verdict']}. {rep['explanation']}"[:400])
            return rep

        try:
            return self.jobs.submit("proof", "Mesure : modèle figé contre modèle ré-entraîné", job)
        except RuntimeError as exc:
            raise UserError(str(exc)) from None

    def learning_proof(self) -> dict | None:
        return loads(self.db.get_meta("learning_proof"), None)

    # ======================================================================
    # Vue d'ensemble pour le panel
    # ======================================================================
    def learning_status(self) -> dict:
        tf = self.settings["ai"]["timeframe"]

        def card(status: str) -> dict | None:
            row = self.models._row(status, tf)
            if row is None:
                return None
            metrics, params = loads(row["metrics"], {}) or {}, loads(row.get("params"), {}) or {}
            return {"version": row["version"], "status": status, "created_at": row["created_at"], "promoted_at": row.get("promoted_at"),
                    "sha256": (row.get("sha256") or "")[:12] or None, "metrics": metrics, "params": params, "notes": row["notes"]}

        champion, challenger, trial = card("champion"), card("challenger"), card("trial")
        comparison = None
        if champion and challenger:
            comparison = self.live_comparison(tf, champion["version"], challenger["version"], challenger["created_at"])
            comparison["historical"] = ((self.models.report(challenger["version"]) or {}).get("comparison"))
        mon = self.models.monitoring(tf)
        monitoring = None
        if mon:
            monitoring = {**mon, "comparison": self.live_comparison(tf, mon["previous"], mon["champion"], mon["since"])}
        acting = champion or trial
        if champion:
            state = "approuvé pour le trading en argent fictif"
        elif trial:
            state = "à l'essai (prometteur, pas prouvé) : taille réduite, argent fictif"
        else:
            state = "aucun modèle validé : l'IA explore ou observe, en argent fictif"
        return {
            "timeframe": tf, "state": state, "acting": acting["version"] if acting else None,
            "champion": champion, "challenger": challenger, "trial": trial,
            "comparison": comparison, "monitoring": monitoring,
            "health": self.champion_health(tf),
            "history": self.models.history(tf, 60),
            "experiments": {k: v for k, v in self.experiments(1).items() if k != "rows"},
            "proof": self.learning_proof(),
            "bench": {k: (self.bench_report() or {}).get(k) for k in ("score", "adaptation", "false_discoveries", "noise_runs", "ts", "recipe")}
            if self.bench_report() else None,
            "rules": [
                "Un modèle n'est validé que s'il gagne sur une période qu'il n'a jamais vue, avec une confiance d'au moins 95 % "
                "(davantage si plusieurs configurations ont été essayées), les trades proches dans le temps n'étant pas supposés indépendants.",
                "Sans champion, le premier modèle validé le devient.",
                f"Avec un champion, un modèle validé ne le remplace que s'il fait mieux sur les mêmes situations, jamais vues par aucun des deux : "
                f"au moins {MIN_DECISIVE} situations où ils décident différemment, sur au moins {MIN_BLOCKS} semaines, et une différence "
                "dont l'intervalle de confiance est entièrement positif.",
                "S'il fait nettement moins bien, il est rejeté. Si l'on ne peut pas conclure, il attend comme challenger, sans trader.",
                "Après une promotion, l'ancien champion reste suivi 90 jours : si le nouveau fait nettement moins bien, retour automatique à l'ancien.",
                "Aucune version n'est effacée ni réécrite : le fichier de chaque modèle porte une empreinte vérifiée à chaque chargement.",
            ],
        }


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)
