"""L'agent de trading IA.

À chaque clôture de bougie, pour chaque crypto suivie :
1. gère ses positions ouvertes (stop au point mort, sortie après la durée max) ;
2. note le résultat des situations passées (candidats « fantômes ») pour
   mesurer en continu la fiabilité de l'IA, y compris sur les trades refusés ;
3. examine les signaux des stratégies, demande l'avis du modèle, applique les
   filtres (climat du marché, actualités, fraîcheur des données) puis soumet
   au gestionnaire de risque, qui a le dernier mot ;
4. explique chaque décision en français (journal « pourquoi »).

Le même code tourne en entraînement (argent fictif, prix en direct) et en backtest.
Il n'existe pas de mode réel : l'agent refuse tout courtier qui n'est pas simulé.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import numpy as np
import pandas as pd

from ..ai.exits import ExitState, end_of_bar
from ..ai.features import FEATURE_COLUMNS, REGIME_LABELS
from ..ai.labeling import candidate_row, simulate_candidate
from ..ai.model import MetaModel
from ..ai.strategies import STRATEGIES, levels
from ..config import timeframe_ms
from ..execution.base import OrderError
from ..execution.positions import PositionManager
from ..risk.manager import RiskManager
from ..safety import forbid, require_paper_broker
from ..storage.db import Database, dumps, loads

log = logging.getLogger("sentinelle.agent")

MODES = {"training": "Entraînement (argent fictif)", "backtest": "Backtest"}


def fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if x >= 1:
        return f"{x:,.2f}".replace(",", " ")
    return f"{x:.5f}"


class TradingAgent:
    def __init__(
        self,
        name: str,
        mode: str,
        db: Database,
        positions: PositionManager,
        risk: RiskManager,
        settings_fn: Callable[[], dict],
        model_fn: Callable[[], MetaModel | None],
        *,
        timeframe: str,
        news_fn: Callable[[str], dict | None] | None = None,
        fresh_fn: Callable[[str], bool] | None = None,
        budget_fn: Callable[[], float | None] | None = None,
        price_check_fn: Callable[[str, float], str | None] | None = None,
        shield_fn: Callable[[str, float], str | None] | None = None,
        context_fn: Callable[[str], dict] | None = None,
        bus=None,
        clock: Callable[[], float] = time.time,
        record: bool = True,
    ):
        if mode not in MODES:  # dont l'ancien mode « live » : refus explicite, jamais d'argent réel
            forbid(f"création d'un agent en mode « {mode} »", db=db, agent=name)
        require_paper_broker(positions.broker, "création d'un agent sur un courtier non simulé", db)
        self.name = name
        self.mode = mode
        self.db = db
        self.positions = positions
        self.broker = positions.broker
        self.risk = risk
        self.settings_fn = settings_fn
        self.model_fn = model_fn
        self.timeframe = timeframe
        self.tf_ms = timeframe_ms(timeframe)
        self.news_fn = news_fn
        self.fresh_fn = fresh_fn
        self.budget_fn = budget_fn
        self.price_check_fn = price_check_fn
        self.shield_fn = shield_fn
        self.context_fn = context_fn
        self.bus = bus
        self.clock = clock
        self.record = record
        self.running = False
        self.last_bar: dict[str, int] = {}
        self.last_scan: dict[str, dict] = {}
        self.stats = {"decisions": 0, "entries": 0, "refused": 0}
        self.refusal_reasons: dict[str, int] = {}

    @property
    def account_id(self) -> int:
        return self.broker.account_id

    @property
    def fee_rate(self) -> float:
        return float(getattr(self.broker, "fee_rate", 0.001))

    def cost_rate(self, symbol: str) -> float:
        """Coût par opération : frais de la plateforme + conversion en euros si le compte est en euros."""
        return self.fee_rate + float(self.broker.conversion_cost(symbol))

    @property
    def slip(self) -> float:
        return float(getattr(self.broker, "slip", self.settings_fn()["paper"]["slippage_bps"] / 10_000))

    # -- point d'entrée -------------------------------------------------------------
    def on_bar(self, symbol: str, feat: pd.DataFrame, idx: int | None = None, sig_at: dict[str, bool] | None = None) -> None:
        if not self.running or len(feat) == 0:
            return
        i = len(feat) - 1 if idx is None else idx
        row = feat.iloc[i]
        bar_ts = int(row["ts"])
        if self.last_bar.get(symbol, -1) >= bar_ts:
            return  # bougie déjà traitée
        self.last_bar[symbol] = bar_ts
        self._manage_positions(symbol, feat, i, bar_ts + self.tf_ms)
        if self.mode != "backtest":
            self._resolve_shadows(symbol, feat)
        enabled = [n for n, on in self.settings_fn()["ai"]["strategies"].items() if on and n in STRATEGIES]
        if sig_at is None:
            sig_at = {n: bool(STRATEGIES[n].signal(feat).iloc[i]) for n in enabled}
        names = [n for n in enabled if sig_at.get(n)]
        if self.mode != "backtest":
            self.record_scan(symbol, feat, i, names)
        if names and np.isfinite(row["atr"]) and row["atr"] > 0:
            self._consider(symbol, row, names, bar_ts)

    def record_scan(self, symbol: str, feat: pd.DataFrame, i: int | None = None, names: list[str] | None = None) -> None:
        """Mémorise ce que l'IA « voit » sur une crypto (affiché dans le panel, même sans trade)."""
        if len(feat) == 0:
            return
        i = len(feat) - 1 if i is None else i
        row = feat.iloc[i]
        if names is None:
            enabled = [n for n, on in self.settings_fn()["ai"]["strategies"].items() if on and n in STRATEGIES]
            names = [n for n in enabled if bool(STRATEGIES[n].signal(feat).iloc[i])]

        def num(key):
            v = row.get(key)
            return float(v) if v is not None and np.isfinite(v) else None

        self.last_scan[symbol] = {
            "ts": int(row["ts"]) + self.tf_ms,
            "close": num("close"),
            "regime": REGIME_LABELS.get(row["regime"], row["regime"]),
            "btc_regime": REGIME_LABELS.get(row.get("btc_regime", "unknown"), "inconnu"),
            "rsi": num("rsi14"),
            "trend_200d": num("trend_200d"),
            "signals": [STRATEGIES[n].label for n in names],
        }

    # -- gestion des positions ouvertes ------------------------------------------------
    def _manage_positions(self, symbol: str, feat: pd.DataFrame, i: int, bar_close_ms: int) -> None:
        row = feat.iloc[i]
        for pos in self.positions.open_positions(symbol, agent=self.name):
            if pos["status"] != "open" or not pos["entry_ts"]:
                continue
            if bar_close_ms <= pos["entry_ts"] * 1000 + 1:
                continue  # bougie antérieure à l'entrée
            st = STRATEGIES.get(pos["strategy"] or "")
            # nombre de bougies écoulées depuis l'entrée (robuste si le PC a été en veille)
            elapsed = int((bar_close_ms - pos["entry_ts"] * 1000) // self.tf_ms)
            extra = loads(pos.get("data"), {}) or {}
            peak = pos["entry_price"]
            if st is not None and st.trail_atr:
                closes = feat["close"].to_numpy()[: i + 1]
                closes_ts = feat["ts"].to_numpy()[: i + 1] + self.tf_ms
                since = closes[closes_ts > pos["entry_ts"] * 1000]
                if len(since):
                    peak = max(peak, float(since.max()))
            state = ExitState(
                entry=pos["entry_price"],
                stop=pos["stop_price"] or 0.0,
                target=pos["target_price"],
                initial_stop=pos["initial_stop"] or pos["stop_price"] or 0.0,
                horizon_bars=pos["horizon_bars"] or (st.horizon(self.timeframe) if st else 48),
                breakeven_r=st.breakeven_r if st else None,
                fee_rate=self.cost_rate(symbol),
                slip=self.slip,
                bars=max(pos["bars_held"], elapsed - 1),
                breakeven_done=bool(pos["breakeven_done"]),
                trail_atr=st.trail_atr if st else None,
                atr_entry=float(extra.get("atr_entry") or 0.0),
                peak=peak,
            )
            moved, timed_out = end_of_bar(state, float(row["high"]), float(row["close"]))
            self.db.update("positions", pos["id"], {"bars_held": state.bars, "breakeven_done": int(state.breakeven_done)})
            try:
                if timed_out:
                    self.positions.close(pos["id"], "Durée maximale atteinte")
                    self._journal(symbol, "sortie", f"Sortie de {symbol} : durée maximale atteinte ({state.bars} bougies).")
                elif moved:
                    self.positions.modify(pos["id"], stop=state.stop)
                    if state.trail_atr:
                        msg = f"{symbol} : stop suiveur remonté à {fmt_price(state.stop)} pour protéger les gains."
                    else:
                        msg = f"{symbol} : stop remonté au point mort ({fmt_price(state.stop)}), le trade ne peut plus perdre (hors trou de cotation)."
                    self._journal(symbol, "stop_relevé", msg)
            except OrderError as exc:
                if exc.code == "would_trigger":
                    self.positions.close(pos["id"], "Stop dépassé : sortie immédiate")
                else:
                    log.warning("Gestion de position %s : %s", pos["id"], exc.message)
                    self._journal(symbol, "erreur", f"{symbol} : gestion de position impossible ({exc.message}).")

    # -- candidats fantômes -------------------------------------------------------------
    def _resolve_shadows(self, symbol: str, feat: pd.DataFrame) -> None:
        rows = self.db.query(
            "SELECT id, ts, strategy, stop, target, entry, features FROM candidates WHERE source = 'live' AND outcome IS NULL "
            "AND symbol = ? AND timeframe = ? AND account_id = ?",
            (symbol, self.timeframe, self.account_id),
        )
        if not rows:
            return
        ts_arr = feat["ts"].to_numpy()
        o, h, l, c = (feat[k].to_numpy() for k in ("open", "high", "low", "close"))
        for r in rows:
            i = int(np.searchsorted(ts_arr, r["ts"]))
            if i >= len(ts_arr) or ts_arr[i] != r["ts"]:
                if len(ts_arr) and r["ts"] < ts_arr[0]:
                    self.db.update("candidates", r["id"], {"outcome": "expired", "resolved_at": self.clock()})
                continue
            if r["strategy"] not in STRATEGIES:
                continue
            atr_entry = 0.0
            if STRATEGIES[r["strategy"]].trail_atr:
                feats = loads(r.get("features"), {}) or {}
                atr_entry = float(feats.get("atr_pct") or 0.0) * float(r.get("entry") or 0.0)
            sim = simulate_candidate(o, h, l, c, i, r["stop"], r["target"], r["strategy"], self.cost_rate(symbol), self.slip,
                                     self.timeframe, atr_entry)
            if sim is None:
                self.db.update("candidates", r["id"], {"outcome": "impossible", "resolved_at": self.clock()})
            elif sim[0] is not None:
                self.db.update(
                    "candidates",
                    r["id"],
                    {"outcome": sim[0], "outcome_r": float(sim[1]), "exit_ts": int(ts_arr[sim[2]] + self.tf_ms), "resolved_at": self.clock()},
                )

    # -- décision d'entrée ----------------------------------------------------------------
    def _consider(self, symbol: str, row, names: list[str], bar_ts: int) -> None:
        cfg = self.settings_fn()
        ai = cfg["ai"]
        fee, slip = self.cost_rate(symbol), self.slip  # frais + conversion : le coût réel de ce compte
        model = self.model_fn()
        thr = ai.get("min_edge_r")
        if thr is None and model is not None:
            thr = model.threshold
        close, a = float(row["close"]), float(row["atr"])
        cands = []
        for name in names:
            st = STRATEGIES[name]
            stop, target = levels(st, close, a)
            cands.append({"name": name, "st": st, "stop": stop, "target": target, "row": candidate_row(row, name, stop, fee, slip)})
        if model is not None:
            p, er = model.predict(pd.DataFrame([c["row"] for c in cands]))
            for c, pk, ek in zip(cands, p, er):
                c["p"], c["er"] = float(pk), float(ek)
            cands.sort(key=lambda c: -c["er"])
        else:
            for c in cands:
                c["p"] = c["er"] = None

        block = None
        regime = row["regime"]
        btc_regime = row.get("btc_regime", regime)
        if regime == "unknown":
            block = "Historique insuffisant pour juger le climat du marché"
        elif ai.get("regime_filter", True) and btc_regime == "bear":
            block = "Climat baissier sur le marché (BTC sous ses moyennes de fond) : l'IA reste à l'abri"
        if block is None and self.fresh_fn is not None and not self.fresh_fn(symbol):
            block = "Données de marché périmées : aucune nouvelle position par prudence"
        if block is None and self.price_check_fn is not None and any(
            (c["er"] is None or thr is None or c["er"] >= thr) for c in cands
        ):
            problem = self.price_check_fn(symbol, close)
            if problem:
                block = f"Sécurité : {problem}"
        news = None
        if block is None and ai.get("use_news", True) and self.news_fn is not None:
            news = self.news_fn(symbol)
            if news and news.get("score") is not None and news["score"] <= cfg["news"]["veto_threshold"]:
                block = f"Actualités très négatives ({news.get('headline', '')[:120]})"

        # Journal de contexte (carnet d'ordres, flux acheteur/vendeur, sentiment) : enregistré pour
        # vérifier plus tard s'il aurait aidé. Il n'influence PAS la décision tant que ce n'est pas prouvé.
        context = {}
        if self.context_fn is not None and self.mode != "backtest":
            try:
                context = self.context_fn(symbol) or {}
            except Exception:
                log.info("Contexte de décision indisponible pour %s", symbol)

        opened = False
        # Un modèle « à l'essai » (prometteur, pas prouvé) n'est utilisé qu'en argent fictif, avec une taille réduite.
        validated = model is not None and (self.mode == "backtest" or (model.metrics or {}).get("verdict") == "validé")
        on_trial = model is not None and not validated and self.mode == "training" and ai.get("exploration", True)
        for c in cands:
            st = c["st"]
            mode_used, risk_factor, decision, reason = None, 1.0, "refusé", ""
            if block:
                reason = block
            elif validated or on_trial:
                if c["er"] >= thr:
                    mode_used = "model" if validated else "essai"
                    if on_trial:
                        risk_factor = float(ai.get("exploration_risk_factor", 0.5))
                else:
                    reason = f"Espérance insuffisante selon l'IA ({c['er']:+.2f} R, seuil {thr:+.2f} R)"
            elif model is None and self.mode in ("training", "backtest") and ai.get("exploration", True):
                mode_used, risk_factor = "exploration", float(ai.get("exploration_risk_factor", 0.5))
            else:
                reason = "Aucun modèle validé : l'IA observe sans trader"
            if mode_used and opened:
                mode_used, reason = None, "Une position vient d'être ouverte sur cette crypto"
            if mode_used:
                require_paper_broker(self.broker, "décision d'achat de l'IA sur un courtier non simulé", self.db)
                equity, _cash = self.broker.equity()
                fx = self.broker.fx_rate(symbol)  # taux vers la monnaie du compte (1 si c'est la même)
                try:
                    market = self.broker.market_fn(symbol)
                    rd = None if fx is None else self.risk.evaluate_entry(
                        symbol, close, c["stop"], c["target"], equity=equity,
                        cash=self.broker.available(self.broker.quote), market=market,
                        fee_rate=self.fee_rate, slip=slip, agent=self.name,
                        budget=self.budget_fn() if self.budget_fn else None, risk_factor=risk_factor,
                        fx=fx, fx_cost=self.broker.conversion_cost(symbol),
                    )
                except KeyError:
                    rd, fx = None, 1.0
                if fx is None:
                    reason = "Taux de change indisponible : aucun ordre par prudence"
                elif rd is None:
                    reason = "Paire indisponible"
                elif not rd.approved:
                    reason = "Gestionnaire de risque : " + rd.reasons[0]
                elif self.shield_fn is not None and (danger := self.shield_fn(symbol, rd.qty * close)):
                    reason = "Bouclier temps réel : " + danger
                else:
                    explanation = self._explain(symbol, c, row, thr, mode_used, rd)
                    try:
                        self.positions.open(
                            symbol, rd.qty, origin="ai", agent=self.name, stop=c["stop"], target=c["target"],
                            strategy=c["name"], p_win=c["p"], expected_r=c["er"],
                            model_version=model.version if model is not None and mode_used in ("model", "essai") else None,
                            horizon_bars=st.horizon(self.timeframe), timeframe=self.timeframe, reason=explanation,
                            data={"mode": mode_used, "risk_checks": rd.checks, "regime": regime, "atr_entry": a,
                                  "min_order_bump": rd.bumped},
                        )
                        decision, reason, opened = "pris", explanation, True
                        self.stats["entries"] += 1
                    except OrderError as exc:
                        reason = f"Ordre refusé : {exc.message}"
            self.stats["decisions"] += 1
            if decision != "pris":
                self.stats["refused"] += 1
                self.refusal_reasons[reason] = self.refusal_reasons.get(reason, 0) + 1
            if self.mode == "backtest":
                continue
            msg = reason if decision == "pris" else f"Signal « {st.label} » sur {symbol} non suivi : {reason}."
            if self.record:
                self.db.execute(
                    "INSERT OR IGNORE INTO candidates(symbol, timeframe, ts, strategy, source, entry, stop, target, horizon_bars, "
                    "features, p_win, expected_r, model_version, decision, decision_reason, account_id) "
                    "VALUES (?, ?, ?, ?, 'live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (symbol, self.timeframe, bar_ts, c["name"], close, c["stop"], c["target"], st.horizon(self.timeframe),
                     dumps({**{k: c["row"][k] for k in FEATURE_COLUMNS + ["cost_r"]}, "contexte": context}), c["p"], c["er"],
                     model.version if model is not None else None, decision, reason, self.account_id),
                )
            self._journal(symbol, decision, msg, {"strategy": c["name"], "p_win": c["p"], "expected_r": c["er"]})

    def _explain(self, symbol, c, row, thr, mode_used, rd) -> str:
        st = c["st"]
        base = symbol.split("/")[0]
        parts = [f"Achat {base} : stratégie « {st.label} »", f"climat {REGIME_LABELS.get(row['regime'], row['regime'])}"]
        if mode_used == "model":
            parts.append(f"IA : {c['p']*100:.0f} % de chances de gain, espérance {c['er']:+.2f} R (seuil {thr:+.2f} R)")
        elif mode_used == "essai":
            parts.append(f"IA à l'essai : {c['p']*100:.0f} % de chances de gain, espérance {c['er']:+.2f} R (seuil {thr:+.2f} R) ; "
                         "modèle prometteur mais pas encore prouvé, taille réduite")
        else:
            parts.append("mode exploration (aucun modèle validé encore, taille réduite)")
        risk_pct = rd.risk_amount / max(1e-9, self.broker.equity()[0]) * 100
        objective = f"objectif {fmt_price(c['target'])}" if c["target"] else "sans objectif fixe (stop suiveur)"
        parts.append(f"risque {risk_pct:.2f} % du capital, stop {fmt_price(c['stop'])}, {objective}")
        return " ; ".join(parts) + "."

    def _journal(self, symbol: str, kind: str, message: str, data: dict | None = None) -> None:
        if self.mode == "backtest":
            return
        self.db.decision(self.account_id, self.name, symbol, kind, message, data)
        if self.bus:
            self.bus.publish("decision", {"agent": self.name, "account_id": self.account_id, "symbol": symbol, "kind": kind, "message": message})

    def status(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "mode_label": MODES[self.mode],
            "running": self.running,
            "account_id": self.account_id,
            "timeframe": self.timeframe,
            "stats": dict(self.stats),
            "last_bar": dict(self.last_bar),
            "scan": dict(self.last_scan),
        }
