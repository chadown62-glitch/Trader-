"""Bulletin de préparation : l'IA est-elle prête pour l'argent réel ?

Ne comptent que les trades réalisés en entraînement (argent fictif, prix réels
en direct) avec un modèle validé. Les trades « exploration » n'entrent pas en
compte. Les seuils viennent des réglages (section « live »).
"""

from __future__ import annotations

import time

import numpy as np

from ..ai.metrics import trade_metrics
from ..storage.db import Database


def compute_readiness(db: Database, settings: dict, account_id: int | None, champion) -> dict:
    live = settings["live"]
    now = time.time()
    trades = []
    if account_id is not None:
        trades = db.query(
            "SELECT * FROM positions WHERE account_id = ? AND origin = 'ai' AND status = 'closed' "
            "AND data LIKE '%\"mode\":\"model\"%' ORDER BY exit_ts",
            (account_id,),
        )
    first = trades[0]["entry_ts"] if trades else None
    days = (now - first) / 86400 if first else 0.0
    rs = [t["pnl_r"] for t in trades if t["pnl_r"] is not None]
    pnl = float(sum(t["realized_pnl"] for t in trades))
    m = trade_metrics(rs)
    max_dd = 0.0
    if account_id is not None and first:
        eq = [r["equity"] for r in db.query("SELECT equity FROM equity WHERE account_id = ? AND ts >= ? ORDER BY ts", (account_id, first))]
        if len(eq) > 1:
            a = np.asarray(eq)
            peak = np.maximum.accumulate(a)
            max_dd = float(((peak - a) / peak).max())
    critical = db.scalar("SELECT COUNT(*) FROM logs WHERE level = 'critical' AND ts >= ?", (now - 7 * 86400,), 0)
    champ_ok = champion is not None and (champion.metrics or {}).get("verdict") == "validé"

    def crit(key, label, ok, value, target, progress):
        return {"key": key, "label": label, "ok": bool(ok), "value": value, "target": target,
                "progress": float(max(0.0, min(1.0, progress)))}

    criteria = [
        crit("model", "Modèle validé sur des données jamais vues", champ_ok,
             champion.version if champion is not None else "aucun", "validé", 1.0 if champ_ok else 0.0),
        crit("days", "Durée d'entraînement en direct", days >= live["unlock_min_days"],
             round(days, 1), live["unlock_min_days"], days / live["unlock_min_days"]),
        crit("trades", "Nombre de trades avec le modèle", len(rs) >= live["unlock_min_trades"],
             len(rs), live["unlock_min_trades"], len(rs) / live["unlock_min_trades"]),
        crit("pnl", "Résultat net positif (frais compris)", pnl > 0 and len(rs) > 0,
             round(pnl, 2), "> 0", 1.0 if pnl > 0 and rs else 0.0),
        crit("drawdown", "Baisse maximale contenue", max_dd < live["unlock_max_drawdown"] and len(rs) > 0,
             round(max_dd * 100, 2), f"< {live['unlock_max_drawdown']*100:.0f} %",
             1.0 if (max_dd < live["unlock_max_drawdown"] and rs) else 0.0),
        crit("errors", "Aucune erreur critique sur 7 jours", critical == 0, int(critical), 0, 1.0 if critical == 0 else 0.0),
    ]
    score = float(np.mean([c["progress"] for c in criteria]))
    return {
        "unlocked": all(c["ok"] for c in criteria),
        "score": round(score * 100),
        "criteria": criteria,
        "metrics": m,
        "pnl": pnl,
        "days": days,
        "max_dd_pct": max_dd * 100,
    }
