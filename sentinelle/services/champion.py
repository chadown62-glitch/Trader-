"""Preregistered paired forecasts; no order, promotion or automatic rollback authority.

Selective integration of Claude #26. A threshold crossing is a signal-filter decision,
not a fill. Resolved signal labels support predictive diagnostics, never wallet PnL.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from ..ai.evaluation import block_interval
from ..ai.features import FEATURE_COLUMNS
from ..ai.strategies import STRATEGIES
from ..config import timeframe_ms
from ..storage.db import loads


def canonical(value):
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, float) and not math.isfinite(x):
            return None  # features may explicitly be unavailable
        return x
    return json.dumps(clean(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def register_pair(db, models, *, champion, challenger, settings, start, end, now=None):
    """Explicit future registration. Does not choose/train models or start observation."""
    now = time.time() if now is None else now
    if not 0 < now < start < end < float("inf") or champion == challenger:
        raise ValueError("Deux versions distinctes et une période future sont requises")
    if settings["general"]["quote"] != "EUR" or settings["portfolio"]["initial"] != 30:
        raise ValueError("Comparaison EUR, capital fictif 30 requis")
    cards = {}
    for role, version in (("champion", champion), ("challenger", challenger)):
        row, model = models.get(version), models.by_version(version)
        if (not row or not row.get("sha256") or model is None or model.trained_until is None
                or not 0 < model.trained_until / 1000 < start):
            raise ValueError("Modèle immuable, lisible et période vue documentée requis")
        if model.timeframe != settings["ai"]["timeframe"]:
            raise ValueError("Horizons incompatibles")
        cards[role] = {"version": version, "sha256": row["sha256"], "params": row["params"]}
    document = {"schema": 1, "name": "paired-forecasts-v4", "registered_at": now, "start": start, "end": end,
                "models": cards, "settings": settings, "settings_sha": sha(settings), "capital_eur": 30,
                "origin": "synthetic_fixture" if settings["market"]["source"] == "synthetic" else "prospective_public",
                "can_trade": False, "promotion_allowed": False, "automatic_rollback": False,
                "universe": "All common strategy signals including refused signals; no take-based selection",
                "criteria": {"min_observations": 30, "min_calendar_days": 28, "block_days": 7, "reps": 2000,
                             "family_alpha": .05, "multiplicity": "All registrations, including rejected/negative runs; no expiry"},
                "financial_evidence": "Requires separate causal EUR30 portfolio replay with common costs/risk/liquidity; absent here",
                "historical_promotion": False}
    document["implementation_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    raw, digest = canonical(document), sha(document)
    with db.transaction():
        # Any previously registered window is consumed even when no positive result was found.
        for row in db.query("SELECT document FROM learning_protocols"):
            old = json.loads(row["document"])
            if start < old["end"] and old["start"] < end:
                raise ValueError("Période déjà enregistrée : aucun réemploi de fenêtre finale")
        db.execute("INSERT INTO learning_protocols VALUES(?,?,?)", (digest, now, raw))
        models.lock_policy(digest, start, end, "paired-forecasts-v4: observation sans promotion")
    return {"sha256": digest, "protocol": document}


class RegisteredComparison:
    def __init__(self, db, models, protocol_sha, *, clock=time.time):
        self.db, self.models, self.clock = db, models, clock
        row = db.one("SELECT * FROM learning_protocols WHERE sha256=?", (protocol_sha,))
        if not row or hashlib.sha256(row["document"].encode()).hexdigest() != protocol_sha:
            raise ValueError("Protocole enregistré absent ou corrompu")
        self.protocol_sha, self.protocol = protocol_sha, json.loads(row["document"])
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != self.protocol["implementation_sha256"]:
            raise ValueError("Implémentation différente du protocole parallèle figé")

    def record(self, candidate, settings, state):
        """Called within the agent's intent transaction. Exceptions prevent execution."""
        p, now = self.protocol, self.clock()
        if sha(settings) != p["settings_sha"]:
            raise ValueError("Réglages différents du protocole parallèle")
        if (candidate["source"] != "live" or candidate["timeframe"] != p["settings"]["ai"]["timeframe"]
                or candidate["symbol"] not in p["settings"]["general"]["watchlist"]):
            raise ValueError("Situation hors univers préenregistré")
        account = self.db.one("SELECT * FROM accounts WHERE id=?", (candidate["account_id"],))
        if not account or (account["kind"], account["quote"], account["initial_balance"]) != ("paper", "EUR", 30):
            raise ValueError("Portefeuille fictif EUR 30 requis, aucun autre compte créé")
        features = loads(candidate["features"], {}) or {}
        available = (candidate["ts"] + timeframe_ms(candidate["timeframe"])) / 1000
        decision = features.get("prediction_recorded_at")
        if (not isinstance(decision, (int, float)) or not math.isfinite(decision)
                or not p["start"] <= available <= decision <= now < p["end"] or now - decision > 180):
            raise ValueError("Prédiction tardive, anticipée ou hors période")
        identity = [self.protocol_sha, candidate["account_id"], candidate["symbol"], candidate["timeframe"],
                    candidate["ts"], candidate["strategy"]]
        key = sha(identity)
        input_sha = sha({"features": features, "price": candidate["entry"], "stop": candidate["stop"], "target": candidate["target"]})
        previous = self.db.one("SELECT * FROM paired_predictions WHERE key=?", (key,))
        # Integrity is checked on every restart, including already recorded forecasts.
        loaded = {}
        for role, card in p["models"].items():
            row = self.models.get(card["version"])
            model = self.models.by_version(card["version"])
            if (not row or row["sha256"] != card["sha256"] or row["params"] != card["params"] or model is None):
                raise ValueError("Version de modèle modifiée ou illisible")
            loaded[role] = model
        if previous:
            if loads(previous["payload"], {})["input_sha"] != input_sha:
                raise ValueError("Conflit de prédiction idempotente")
            return key
        if candidate["decision"] != "préparé" or candidate["outcome"] is not None:
            raise ValueError("Enregistrement exigé avant l'exécution et l'issue")
        if (not isinstance(state.get("available_at"), (int, float)) or not math.isfinite(state["available_at"])
                or not available <= state["available_at"] <= now
                or any(not isinstance(state.get(k), (int, float)) or not math.isfinite(state[k]) or state[k] < 0
                       for k in ("equity_eur", "exposure_eur"))):
            raise ValueError("État comptable daté requis avant exécution")
        model_row = {k: features.get(k) for k in FEATURE_COLUMNS + ["cost_r"]}
        model_row.update(strategy=candidate["strategy"])
        model_row.update({f"s_{name}": float(name == candidate["strategy"]) for name in STRATEGIES})
        forecasts = {}
        for role, model in loaded.items():
            prob, edge = model.predict(pd.DataFrame([model_row]))
            prob, edge = float(prob[0]), float(edge[0])
            if not math.isfinite(edge) or not 0 <= prob <= 1:
                raise ValueError("Prédiction non finie ou probabilité invalide")
            forecasts[role] = {"version": model.version, "p_win": prob, "expected_r": edge,
                               "threshold": model.threshold, "signal_pass": edge >= model.threshold,
                               "executed": False, "wallet_pnl": None}
        payload = {"input_sha": input_sha, "symbol": candidate["symbol"], "strategy": candidate["strategy"],
                   "price_available": candidate["entry"], "price_available_at": available,
                   "cost_r": features.get("cost_r"), "fee_rate": features.get("fee_rate"), "slippage": features.get("slippage"),
                   "context": features.get("contexte"), "features": model_row, "state_before_execution": state,
                   "forecasts": forecasts, "origin": p["origin"], "interpretation": "signal_filter_only"}
        self.db.execute("INSERT INTO paired_predictions VALUES(?,?,?,?,?,?,?,?)",
                        (key, self.protocol_sha, candidate["id"], candidate["account_id"], decision, available, now, canonical(payload)))
        return key

    def resolve(self):
        """Append matured theoretical labels. Never joins positions/fills or imputes wallet profits."""
        now, count = self.clock(), 0
        rows = self.db.query("SELECT p.*,c.outcome_r,c.exit_ts,c.resolved_at,c.outcome FROM paired_predictions p "
                             "JOIN candidates c ON c.id=p.candidate_id LEFT JOIN paired_outcomes o ON o.prediction_key=p.key "
                             "WHERE p.protocol_sha=? AND o.prediction_key IS NULL AND c.outcome_r IS NOT NULL", (self.protocol_sha,))
        with self.db.transaction():
            for row in rows:
                exit_s = row["exit_ts"] / 1000 if row["exit_ts"] is not None else None
                resolved = row["resolved_at"]
                if (exit_s is None or resolved is None or not math.isfinite(row["outcome_r"])
                        or not row["recorded_at"] < exit_s <= resolved <= now):
                    continue
                outcome = {"y": int(row["outcome_r"] > 0), "theoretical_r": row["outcome_r"],
                           "exit_ts": exit_s, "outcome": row["outcome"], "wallet_pnl": None,
                           "origin": "signal_label_only"}
                count += self.db.execute("INSERT OR IGNORE INTO paired_outcomes VALUES(?,?,?,?)",
                                         (row["key"], resolved, now, canonical(outcome))).rowcount
        return count

    def report(self, as_of=None):
        now = self.clock() if as_of is None else as_of
        if not math.isfinite(now) or now > self.clock():
            raise ValueError("Date de lecture future ou non finie")
        rows = self.db.query("SELECT p.*,o.payload AS outcome,o.available_at AS outcome_at,o.recorded_at AS outcome_recorded "
                             "FROM paired_predictions p LEFT JOIN paired_outcomes o ON o.prediction_key=p.key "
                             "AND o.available_at<=? AND o.recorded_at<=? WHERE p.protocol_sha=? AND p.recorded_at<=? ORDER BY p.decision_ts,p.key",
                             (now, now, self.protocol_sha, now))
        resolved = [r for r in rows if r["outcome"] is not None]
        diagnostics = {}
        daily = {}
        for role in ("champion", "challenger"):
            prob = np.array([loads(r["payload"])["forecasts"][role]["p_win"] for r in resolved])
            y = np.array([loads(r["outcome"])["y"] for r in resolved])
            diagnostics[role] = {"n": len(resolved), "brier": float(np.mean((prob-y)**2)) if len(y) else None,
                                 "auc": float(roc_auc_score(y, prob)) if len(np.unique(y)) == 2 else None,
                                 "calibration": [], "wallet_pnl": None}
            for k in range(5):
                mask = (prob >= k/5) & (prob <= 1 if k == 4 else prob < (k+1)/5)
                if mask.any():
                    diagnostics[role]["calibration"].append({"n": int(mask.sum()), "p_mean": float(prob[mask].mean()),
                                                            "win_rate": float(y[mask].mean())})
        for row in resolved:
            f, y = loads(row["payload"])["forecasts"], loads(row["outcome"])["y"]
            improvement = (f["champion"]["p_win"]-y)**2 - (f["challenger"]["p_win"]-y)**2
            daily.setdefault(int(row["decision_ts"] // 86400), []).append(improvement)
        # Unobserved days are not zero returns. CI requires a continuous common observation span.
        days = sorted(daily)
        contiguous = bool(days) and days == list(range(days[0], days[-1]+1))
        trials = max(1, self.db.scalar("SELECT COUNT(*) FROM learning_protocols WHERE registered_at<=?", (now,), default=1))
        enough = len(resolved) >= 30 and len(days) >= 28 and contiguous
        interval = block_interval([np.mean(daily[d]) for d in days], confidence=1-.05/trials) if enough else None
        return {"protocol_sha": self.protocol_sha, "origin": self.protocol["origin"], "as_of": now,
                "n": len(rows), "resolved": len(resolved), "pending": len(rows)-len(resolved), "diagnostics": diagnostics,
                "brier_improvement_daily_interval": interval, "observed_days": len(days), "continuous_days": contiguous,
                "trials": trials, "financial_comparison": None, "promotion_allowed": False,
                "verdict": "insuffisant", "explanation": "Diagnostics prédictifs communs seulement ; portefeuilles contrefactuels non rejoués."}


class ChampionMixin:
    def review_models(self):
        return {"action": None, "automatic_promotion": False, "automatic_rollback": False,
                "reason": "Protocoles gelés ; revue informative uniquement"}

    def log_experiment(self, kind, params, result=None):
        return self.db.insert("experiments", {"ts": time.time(), "kind": kind, "config_hash": sha(params),
                                              "params": canonical(params), "result": canonical(result) if result is not None else None})

    def experiments(self, limit=100):
        rows = self.db.query("SELECT * FROM experiments ORDER BY id DESC LIMIT ?", (max(1, min(500, limit)),))
        for row in rows:
            row["params"], row["result"] = loads(row["params"]), loads(row["result"])
        return {"rows": rows, "total": self.db.scalar("SELECT COUNT(*) FROM experiments", default=0),
                "distinct_configs": self.db.scalar("SELECT COUNT(DISTINCT config_hash) FROM experiments", default=0),
                "note": "Journal descriptif ; aucun effacement après 180 jours ni autorisation de promotion."}

    def learning_status(self):
        tf = self.settings["ai"]["timeframe"]
        cards = {}
        for status in ("champion", "challenger", "trial"):
            row = self.models._row(status, tf)
            card = self.models.get(row["version"]) if row else None
            if card:
                card.pop("path", None)
                card["integrity"] = "verified" if card.get("sha256") and self.models.by_version(card["version"]) else "unverified_or_unreadable"
            cards[status] = card
        runs = self.db.query("SELECT sha256 FROM learning_protocols ORDER BY registered_at DESC LIMIT 10")
        return {**cards, "timeframe": tf, "history": self.models.history(tf, 60), "experiments": self.experiments(20),
                "comparisons": [RegisteredComparison(self.db, self.models, r["sha256"]).report() for r in runs],
                "policy_locks": self.models.policy_locks(), "review": self.review_models(),
                "status": "Données insuffisantes" if not runs else "Observation parallèle préenregistrée",
                "rules": ["Aucune promotion ou rétrogradation automatique.",
                          "Fenêtres fit/calibration/sélection/holdout purgées conservées ; évaluations finales consommées une seule fois.",
                          "AUC et calibration portent sur les signaux communs, y compris refusés ; elles ne démontrent aucun avantage net.",
                          "Sans replay indépendant des portefeuilles, aucun PnL attribué au challenger.",
                          "Prospectif v2 et macro shadow v3 inchangés ; Forex Factory reste soumis aux droits existants."]}
