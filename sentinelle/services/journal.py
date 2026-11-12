"""Journal des décisions : relire chaque décision de l'IA après coup, et en tirer une leçon honnête.

Pour chaque situation examinée (prise ou refusée) :
- le plan écrit AVANT l'issue (raisons, probabilité, scénarios, stop, objectif, frais, taille) ;
- ce que l'IA savait à ce moment-là (caractéristiques et contexte enregistrés avec la décision) ;
- l'issue réelle, quand elle est connue ;
- pourquoi le trade a gagné ou perdu : le signal, les frais, le mouvement général du marché, ou le hasard probable ;
- ce que le modèle actuel déciderait aujourd'hui sur exactement la même situation.

Une perte isolée ne prouve rien : quand l'IA annonçait 45 % de chances de gain, perdre est l'issue la plus probable.
Le journal le dit, pour éviter de « corriger » ce qui n'est que du hasard.
"""

from __future__ import annotations

import bisect

import pandas as pd

from ..ai.features import FEATURE_COLUMNS
from ..ai.metrics import trade_metrics
from ..ai.strategies import STRATEGIES
from ..config import timeframe_ms
from ..storage.db import loads

MARKET_MOVE = 0.03  # mouvement du Bitcoin pendant le trade, au-delà duquel le marché entier explique l'issue
CONFIDENT = 0.6  # probabilité annoncée à partir de laquelle une perte mérite d'être regardée de près
SHRINK = 30  # une moyenne observée sur peu de cas est ramenée vers zéro (mémoire de marché prudente)
REGIMES = {"bull": "haussier", "bear": "baissier", "range": "neutre", "neutral": "neutre", "unknown": "inconnu"}


def explain_outcome(net_r: float | None, cost_r: float | None, p_win: float | None, market_move: float | None) -> dict | None:
    """Cause la plus plausible d'un gain ou d'une perte. Renvoie None tant que l'issue est inconnue.

    - frais : le prix est allé dans le bon sens, mais pas assez pour couvrir les coûts ;
    - marché : le marché entier a bougé nettement dans le sens du résultat ;
    - hasard probable : l'issue est conforme aux probabilités annoncées ;
    - signal : l'IA était confiante et s'est trompée, sans que le marché l'explique."""
    if net_r is None:
        return None
    gross = net_r + (cost_r or 0.0)
    won = net_r > 0
    pct = None if market_move is None else f"{market_move * 100:+.1f} %".replace(".", ",")
    if not won and gross > 0:
        return {"cause": "frais", "label": "Les frais", "detail": "Le prix est allé dans le bon sens, mais trop peu : les frais ont mangé le gain "
                f"({gross:+.2f} R avant frais, {net_r:+.2f} R après).".replace(".", ",")}
    if market_move is not None and abs(market_move) >= MARKET_MOVE and (market_move > 0) == won:
        direction = "monté" if market_move > 0 else "baissé"
        return {"cause": "marché", "label": "Le mouvement du marché", "detail": f"Le marché entier a {direction} pendant le trade (Bitcoin {pct}) : "
                + ("ce gain doit plus au marché qu'à ce signal en particulier." if won else "cette perte vient du climat général, pas de ce signal en particulier.")}
    if p_win is None:
        return {"cause": "inconnu", "label": "Sans probabilité annoncée", "detail": "Aucun modèle n'annonçait de probabilité pour ce trade : "
                "on ne peut pas dire si son issue était attendue."}
    odds = f"{p_win * 100:.0f} %"
    if not won and p_win >= CONFIDENT:
        return {"cause": "signal", "label": "Le signal s'est trompé", "detail": f"L'IA annonçait {odds} de chances de gain et le marché n'explique pas la perte : "
                "si ce cas se répète, ses probabilités sont trop optimistes (voir la calibration)."}
    if won:
        return {"cause": "hasard probable", "label": "Issue conforme aux probabilités", "detail": f"Gain attendu environ {odds} du temps : "
                "un gain isolé ne prouve pas que le signal était bon."}
    return {"cause": "hasard probable", "label": "Issue conforme aux probabilités", "detail": f"L'IA n'annonçait que {odds} de chances de gain : "
            "une perte était l'issue la plus probable ou presque. Une perte isolée ne prouve rien."}


class JournalMixin:
    """Méthodes de l'application : journal des décisions, leçons tirées des issues, mémoire de marché."""

    def _btc_closes(self, timeframe: str) -> tuple[list[int], list[float]]:
        """Clôtures du Bitcoin (heure de clôture en ms, prix) pour mesurer le mouvement général du marché."""
        sym = f"BTC/{self.settings['general']['quote']}"
        try:
            series = self.hub.series(sym, timeframe)
            if len(series) < 2:
                series = self.history.load(sym, timeframe)
        except Exception:
            return [], []
        if series is None or len(series) < 2:
            return [], []
        tf = timeframe_ms(timeframe)
        return [int(t) + tf for t in series["ts"].tolist()], [float(c) for c in series["close"].tolist()]

    @staticmethod
    def _move(closes: tuple[list[int], list[float]], start_ms: float, end_ms: float) -> float | None:
        ts, px = closes
        if not ts:
            return None
        a, b = bisect.bisect_right(ts, start_ms) - 1, bisect.bisect_right(ts, end_ms) - 1
        if a < 0 or b <= a or px[a] <= 0:
            return None
        return px[b] / px[a] - 1.0

    def _today(self, cand: dict, feats: dict, timeframe: str) -> dict | None:
        """Ce que le modèle actuel déciderait sur cette même situation (mêmes caractéristiques qu'à l'époque)."""
        model = self.models.challenger(timeframe) or self.models.champion(timeframe) or self.models.trial(timeframe)
        if model is None or not all(k in feats for k in FEATURE_COLUMNS):
            return None
        row = {k: feats.get(k) for k in FEATURE_COLUMNS + ["cost_r"]}
        row.update({f"s_{name}": 1.0 if name == cand["strategy"] else 0.0 for name in STRATEGIES})
        row["strategy"] = cand["strategy"]
        try:
            p, er = model.predict(pd.DataFrame([row]))
        except Exception:
            return None
        take = bool(er[0] >= model.threshold)
        was_taken = cand["decision"] == "pris"
        same = cand["model_version"] == model.version
        if same:
            text = "C'est ce même modèle qui a décidé."
        else:
            verb = "la prendrait" if take else "la refuserait"
            changed = "ce serait une décision différente" if take != was_taken else "la décision serait la même"
            text = f"Le modèle {model.version} annoncerait {p[0] * 100:.0f} % de chances de gain et {er[0]:+.2f} R : il {verb} ; {changed}."
        return {"model": model.version, "p_win": float(p[0]), "expected_r": float(er[0]), "take": take, "same_model": same,
                "changed": (take != was_taken) and not same, "text": text}

    def decision_journal(self, limit: int = 50, decision: str | None = None, symbol: str | None = None) -> dict:
        tf = self.settings["ai"]["timeframe"]
        acc = self.ai_account()
        if acc is None:
            return {"rows": [], "timeframe": tf, "lessons": None}
        sql = "SELECT * FROM candidates WHERE source = 'live' AND account_id = ?"
        params: list = [acc["id"]]
        if decision in ("pris", "refusé"):
            sql += " AND decision = ?"
            params.append(decision)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        cands = self.db.query(sql + " ORDER BY ts DESC LIMIT ?", [*params, max(1, min(int(limit), 500))])
        positions = {}
        for p in self.db.query("SELECT * FROM positions WHERE account_id = ? AND origin = 'ai' ORDER BY id DESC LIMIT 2000", (acc["id"],)):
            bar_ts = (loads(p.get("data"), {}) or {}).get("bar_ts")
            if bar_ts is not None:
                positions[(p["symbol"], p["strategy"], int(bar_ts))] = p
        closes = self._btc_closes(tf)
        tfms = timeframe_ms(tf)
        rows = []
        for c in cands:
            feats = loads(c.get("features"), {}) or {}
            pos = positions.get((c["symbol"], c["strategy"], int(c["ts"])))
            st = STRATEGIES.get(c["strategy"])
            start_ms = c["ts"] + tfms
            if pos is not None and pos["status"] == "closed":
                net_r, end_ms, status = pos["pnl_r"], (pos["exit_ts"] or 0) * 1000, "dénoué"
            elif pos is not None and pos["status"] in ("open", "pending"):
                net_r, end_ms, status = None, None, "en cours"
            elif c["outcome_r"] is not None:
                net_r, end_ms, status = c["outcome_r"], c["exit_ts"], "dénoué"
            else:
                net_r, end_ms, status = None, None, "en cours" if c["outcome"] is None else c["outcome"]
            move = self._move(closes, start_ms, end_ms) if end_ms else None
            rows.append({
                "id": c["id"], "ts": start_ms / 1000, "symbol": c["symbol"], "strategy": c["strategy"], "label": st.label if st else c["strategy"],
                "decision": c["decision"], "reason": c["decision_reason"], "p_win": c["p_win"], "expected_r": c["expected_r"],
                "model_version": c["model_version"], "plan": feats.get("plan"), "context": feats.get("contexte"),
                "known": {k: feats.get(k) for k in ("rsi14", "atr_pct", "trend_200d", "vol_z", "d_ema50", "cost_r") if feats.get(k) is not None},
                "outcome": {"status": status, "kind": c["outcome"], "r": net_r, "theoretical": pos is None,
                            "pnl": pos["realized_pnl"] if pos is not None and pos["status"] == "closed" else None,
                            "currency": acc["quote"], "reason_close": pos["reason_close"] if pos is not None else None,
                            "market_move": move},
                "review": explain_outcome(net_r, feats.get("cost_r"), c["p_win"], move) if status == "dénoué" else None,
                "today": self._today(c, feats, tf),
            })
        return {"rows": rows, "timeframe": tf, "lessons": self.lessons(acc["id"], tf)}

    def lessons(self, account_id: int, timeframe: str) -> dict:
        """Ce que l'on peut dire, prudemment, des issues déjà connues : causes des pertes, mémoire par crypto et par climat."""
        cands = self.db.query(
            "SELECT symbol, strategy, decision, p_win, outcome_r, ts, exit_ts, features FROM candidates "
            "WHERE source = 'live' AND account_id = ? AND timeframe = ? AND outcome_r IS NOT NULL ORDER BY ts", (account_id, timeframe))
        closes = self._btc_closes(timeframe)
        tfms = timeframe_ms(timeframe)
        causes: dict[str, dict] = {}
        memory: dict[tuple[str, str], list] = {}
        for c in cands:
            feats = loads(c.get("features"), {}) or {}
            regime = (feats.get("contexte") or {}).get("regime")
            memory.setdefault(("crypto", c["symbol"]), []).append(c)
            memory.setdefault(("climat", REGIMES.get(regime, regime) if regime else "non précisé"), []).append(c)
            if c["decision"] != "pris":
                continue
            review = explain_outcome(c["outcome_r"], feats.get("cost_r"), c["p_win"], self._move(closes, c["ts"] + tfms, c["exit_ts"] or 0))
            kind = "gains" if c["outcome_r"] > 0 else "pertes"
            slot = causes.setdefault(review["cause"], {"cause": review["cause"], "label": review["label"], "gains": 0, "pertes": 0})
            slot[kind] += 1
        mem = []
        for (kind, key), sub in memory.items():
            m = trade_metrics([c["outcome_r"] for c in sub], ts=[c["ts"] for c in sub])
            n = m["n"]
            mem.append({"kind": kind, "key": key, "n": n, "win_rate": m["win_rate"], "mean_r": m["mean_r"],
                        "ci_low": m["ci_low"], "ci_high": m["ci_high"],
                        # moyenne prudente : ramenée vers zéro tant que les cas sont peu nombreux
                        "prudent_mean_r": (m["mean_r"] * n / (n + SHRINK)) if n else None, "enough": n >= SHRINK})
        mem.sort(key=lambda g: (g["kind"], -g["n"]))
        return {"resolved": len(cands), "taken_resolved": sum(1 for c in cands if c["decision"] == "pris"),
                "causes": sorted(causes.values(), key=lambda g: -(g["gains"] + g["pertes"])), "memory": mem, "shrink": SHRINK,
                "note": ("Ces moyennes décrivent le passé récent, sur peu de cas : elles servent à rester prudent, pas à prédire. "
                         f"La « moyenne prudente » est ramenée vers zéro tant qu'il y a moins de {SHRINK} cas.")}
