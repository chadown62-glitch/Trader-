"""Decision journal, ported from Claude #26 without attributing theoretical PnL to a wallet."""
from __future__ import annotations

from ..ai.metrics import trade_metrics
from ..config import timeframe_ms
from ..storage.db import loads


def explain_outcome(net_r, cost_r, p_win):
    if net_r is None:
        return None
    # Descriptive checks, not an inference of the causal origin of a single result.
    if net_r <= 0 < net_r + (cost_r or 0):
        text = "Le résultat brut estimé est positif ; les coûts estimés dépassent ce gain."
    elif p_win is None:
        text = "Aucune probabilité annoncée ; une issue isolée ne démontre aucune relation."
    else:
        text = "Une issue isolée ne valide ni n'invalide la probabilité annoncée ; contrôler la calibration agrégée."
    return {"detail": text, "causal_claim": False}


class JournalMixin:
    def decision_journal(self, limit=50, decision=None, symbol=None):
        account = self.ai_account()
        if not account:
            return {"rows": [], "currency": "EUR", "lessons": None}
        where, params = "source='live' AND account_id=?", [account["id"]]
        if decision in ("préparé", "pris", "refusé"):
            where += " AND decision=?"
            params.append(decision)
        if symbol:
            where += " AND symbol=?"
            params.append(symbol)
        candidates = self.db.query("SELECT * FROM candidates WHERE " + where + " ORDER BY ts DESC,id DESC LIMIT ?",
                                   [*params, max(1, min(500, int(limit)))])
        rows = []
        for c in candidates:
            features = loads(c["features"], {}) or {}
            pos = self.db.one("SELECT * FROM positions WHERE account_id=? AND candidate_id=? ORDER BY id DESC LIMIT 1",
                              (account["id"], c["id"]))
            if pos is None:
                # #24 linkage, scoped by account/symbol/strategy/timeframe; never borrow another trade's fills.
                for p in self.db.query("SELECT * FROM positions WHERE account_id=? AND symbol=? AND strategy=? AND timeframe=?",
                                       (account["id"], c["symbol"], c["strategy"], c["timeframe"])):
                    data = loads(p["data"], {}) or {}
                    if data.get("prediction_key") == f"{c['timeframe']}:{c['ts']}:{c['strategy']}" or data.get("bar_ts") == c["ts"]:
                        pos = p
                        break
            # A taken signal with a lost linkage stays unavailable, even if its theoretical label is resolved.
            actual = None if pos is None else {
                "position_id": pos["id"], "status": pos["status"], "realized_pnl": pos["realized_pnl"],
                "pnl_r": pos["pnl_r"] if pos["status"] == "closed" else None,
                "fees": pos["fees"], "currency": pos.get("currency") or account["quote"],
                "entry_ts": pos["entry_ts"], "exit_ts": pos["exit_ts"],
                "partial_realization": pos["status"] != "closed" and pos["qty"] < pos["initial_qty"],
                "reason_close": pos["reason_close"], "origin": "paper_execution"}
            r = actual["pnl_r"] if actual else None
            rows.append({"id": c["id"], "symbol": c["symbol"], "timeframe": c["timeframe"], "strategy": c["strategy"],
                         "ts": (c["ts"] + timeframe_ms(c["timeframe"])) / 1000,
                         "recorded_at": features.get("prediction_recorded_at"), "model_version": c["model_version"],
                         "decision": c["decision"], "reason": c["decision_reason"], "p_win": c["p_win"],
                         "expected_r": c["expected_r"], "plan": features.get("plan"), "context": features.get("contexte"),
                         "actual": actual, "theoretical": {"outcome": c["outcome"], "r": c["outcome_r"],
                         "exit_ms": c["exit_ts"], "origin": "signal_label_only", "wallet_pnl": None},
                         "link_missing": c["decision"] == "pris" and pos is None,
                         "review": explain_outcome(r, features.get("cost_r"), c["p_win"])})
        closed = [r["actual"]["pnl_r"] for r in rows if r["actual"] and r["actual"]["status"] == "closed"]
        return {"rows": rows, "currency": account["quote"],
                "lessons": {"scope": "Displayed closed paper positions only; descriptive, no promotion",
                            "metrics": trade_metrics(closed), "causal_claim": False}}
