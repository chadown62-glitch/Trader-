"""Tableau de rentabilité : ce que le portefeuille a réellement rapporté, mesuré sans arrangement.

Principes :
- tout est **net** : frais de la plateforme, glissement et conversion sont déjà dans les résultats ;
- le **réalisé** (trades clôturés) est séparé du **latent** (positions encore ouvertes, valorisées au
  dernier prix), et les deux sont rapprochés de la variation du capital ;
- les rendements quotidiens comptent **tous les jours**, y compris ceux sans aucun trade (rendement nul)
  et ceux où Sentinelle était éteint (capital inchangé, jour signalé) ;
- aucun chiffre n'est extrapolé : pas de rendement « annualisé », pas de projection ;
- avec peu de trades, les chiffres ne prouvent rien : c'est dit explicitement.

Les jours sont découpés en temps universel (UTC), comme les limites de perte du gestionnaire de risque.
"""

from __future__ import annotations

import bisect
import math
import time

from ..ai.metrics import trade_metrics
from ..ai.strategies import STRATEGIES
from ..execution.base import OrderError
from ..execution.positions import settlement_rate
from ..storage.db import loads
from .common import UserError

DAY = 86_400
PERIODS = {
    "today": ("Aujourd'hui", 0),
    "7d": ("7 jours", 7),
    "30d": ("30 jours", 30),
    "90d": ("90 jours", 90),
    "all": ("Depuis le lancement", None),
}
REGIMES = {"bull": "haussier", "bear": "baissier", "range": "neutre", "neutral": "neutre", "unknown": "inconnu"}
HORIZONS = (("moins d'un jour", 1.0), ("1 à 3 jours", 3.0), ("3 à 7 jours", 7.0), ("plus de 7 jours", float("inf")))
MIN_TRADES = 30  # en dessous, aucune conclusion statistique

# Motifs de refus regroupés : (libellé, mots repérés dans la raison enregistrée avec la décision)
REFUSAL_GROUPS = (
    ("Ordre trop petit ou impossible avec ce capital", ("trop petit", "impossible avec ce capital", "minimal", "minimum")),
    ("Avantage jugé insuffisant par l'IA", ("espérance insuffisante",)),
    ("Climat de marché baissier", ("climat baissier",)),
    ("Frais trop lourds par rapport au gain visé", ("face aux frais",)),
    ("Limite de perte ou arrêt de sécurité", ("limite de perte", "arrêt de sécurité", "pertes d'affilée")),
    ("Déjà en position ou nombre maximal atteint", ("déjà ouverte", "vient d'être ouverte", "nombre maximal")),
    ("Délai de réflexion après une perte", ("délai de réflexion",)),
    ("Annonce économique proche", ("annonce macro",)),
    ("Bouclier temps réel", ("bouclier",)),
    ("Actualités très négatives", ("actualités",)),
    ("Données ou taux de change indisponibles", ("périmées", "taux de change", "historique insuffisant", "paire indisponible", "prix incohérent")),
    ("Aucun modèle validé : l'IA observe", ("aucun modèle validé",)),
    ("Liquidités ou exposition insuffisantes", ("fonds insuffisants", "liquidités", "exposition")),
)


def refusal_group(reason: str | None) -> str:
    low = (reason or "").lower()
    for label, words in REFUSAL_GROUPS:
        if any(w in low for w in words):
            return label
    return "Autre motif"


def day_of(ts: float) -> int:
    return int(ts // DAY)


def day_label(day: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day * DAY))


def geometric_mean_return(returns: list[float]) -> float | None:
    """Rendement moyen géométrique : le rendement constant qui aurait donné le même résultat sur ces jours."""
    if not returns:
        return None
    growth = 1.0
    for r in returns:
        if r <= -1.0:
            return -1.0
        growth *= 1.0 + r
    return growth ** (1.0 / len(returns)) - 1.0


def max_drawdown(values: list[float]) -> float:
    """Plus forte baisse depuis un plus haut, en fraction (0,12 = −12 %)."""
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1.0 - v / peak)
    return worst


def trade_stats(trades: list[dict]) -> dict:
    """Statistiques d'un ensemble de trades clôturés (montants dans la monnaie du compte)."""
    n = len(trades)
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [t["pnl_r"] for t in trades if t["pnl_r"] is not None]
    m = trade_metrics(rs)
    rets = sorted(t["ret_position"] for t in trades if t["ret_position"] is not None)
    contribs = [t["contribution"] for t in trades if t["contribution"] is not None]
    gross_loss = -sum(losses)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n if n else None,
        "pnl": float(sum(pnls)),
        "avg_win": float(sum(wins) / len(wins)) if wins else None,
        "avg_loss": float(sum(losses) / len(losses)) if losses else None,
        "expectancy": float(sum(pnls) / n) if n else None,
        "profit_factor": (float(sum(wins) / gross_loss) if gross_loss > 0 else None),
        "mean_r": m.get("mean_r") if rs else None,
        "ci_low": m.get("ci_low") if rs else None,
        "ci_high": m.get("ci_high") if rs else None,
        "fees": float(sum(t["fees"] for t in trades)),
        "avg_duration_h": float(sum(t["duration_s"] for t in trades) / n / 3600) if n else None,
        "mean_ret_position": float(sum(rets) / len(rets)) if rets else None,
        "median_ret_position": (float(rets[len(rets) // 2]) if len(rets) % 2 else float((rets[len(rets) // 2 - 1] + rets[len(rets) // 2]) / 2)) if rets else None,
        "mean_contribution": float(sum(contribs) / len(contribs)) if contribs else None,
        "enough": n >= MIN_TRADES,
    }


def grouped(trades: list[dict], key: str, labels: dict | None = None) -> list[dict]:
    groups: dict = {}
    for t in trades:
        groups.setdefault(t.get(key), []).append(t)
    out = []
    for k, rows in groups.items():
        s = trade_stats(rows)
        label = labels[k] if labels and k in labels else (k if k is not None else "non précisé")
        out.append({"key": k, "label": label,
                    **{f: s[f] for f in ("n", "win_rate", "pnl", "expectancy", "mean_r", "ci_low", "ci_high", "profit_factor",
                                         "mean_ret_position", "fees", "avg_duration_h", "enough")}})
    return sorted(out, key=lambda g: (-g["n"], str(g["label"])))


class PerformanceMixin:
    """Méthodes de l'application : tableau de rentabilité (capital, résultats nets, rendements par trade et par jour)."""

    # -- données de base ------------------------------------------------------------------
    def _launch_ts(self, acc: dict) -> float:
        """Début de la période « depuis le lancement » : dernier dépôt ou dernière réinitialisation du compte."""
        row = self.db.one(
            "SELECT ts FROM ledger WHERE account_id = ? AND asset = ? AND kind IN ('depot', 'reinitialisation') ORDER BY id DESC LIMIT 1",
            (acc["id"], acc["quote"]))
        if row:
            return float(row["ts"])
        first = self.db.one("SELECT MIN(ts) AS ts FROM equity WHERE account_id = ?", (acc["id"],))
        return float((first and first["ts"]) or acc["created_at"] or 0.0)

    def _closed_trades(self, acc: dict, launch: float, snaps_ts: list[float], snaps_eq: list[float]) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM positions WHERE account_id = ? AND status = 'closed' AND exit_ts IS NOT NULL AND entry_ts >= ? "
            "AND initial_qty > 0 AND entry_price IS NOT NULL ORDER BY exit_ts, id", (acc["id"], launch - 1e-6))
        initial = float(acc["initial_balance"] or 0.0)
        out = []
        for p in rows:
            data = loads(p.get("data"), {}) or {}
            try:
                cost = p["entry_price"] * p["initial_qty"] * settlement_rate(p["symbol"], acc["quote"], p.get("entry_fx")) + p["entry_fees"]
            except OrderError:
                cost = 0.0  # missing historical FX basis: return unavailable, never assume parity
            k = bisect.bisect_right(snaps_ts, p["entry_ts"]) - 1
            equity_at_entry = snaps_eq[k] if k >= 0 else initial
            duration = max(0.0, p["exit_ts"] - p["entry_ts"])
            days = duration / DAY
            out.append({
                "id": p["id"], "symbol": p["symbol"], "origin": p["origin"], "strategy": p["strategy"],
                "entry_ts": p["entry_ts"], "exit_ts": p["exit_ts"], "entry_price": p["entry_price"], "exit_price": p["exit_price"],
                "pnl": float(p["realized_pnl"] or 0.0), "pnl_r": p["pnl_r"], "fees": float(p["fees"] or 0.0),
                "cost": cost, "ret_position": (p["realized_pnl"] / cost) if cost > 0 else None,
                "contribution": (p["realized_pnl"] / equity_at_entry) if equity_at_entry > 0 else None,
                "duration_s": duration, "reason_close": p["reason_close"],
                "regime": data.get("regime"), "mode": data.get("mode") or ("manuel" if p["origin"] == "manual" else None),
                "model_version": p["model_version"], "timeframe": p["timeframe"],
                "horizon": next(label for label, limit in HORIZONS if days < limit),
            })
        return out

    def _daily_series(self, launch: float, now: float, initial: float, snaps: list[dict], current: float | None,
                      trades: list[dict]) -> list[dict]:
        """Une ligne par jour depuis le lancement, sans trou : les jours sans trade ou sans relevé y figurent."""
        first, last = day_of(launch), day_of(now)
        close: dict[int, float] = {}
        for s in snaps:
            close[day_of(s["ts"])] = s["equity"]  # relevés triés : le dernier du jour l'emporte
        if current is not None:
            close[last] = current
        closed_by_day: dict[int, list[dict]] = {}
        for t in trades:
            closed_by_day.setdefault(day_of(t["exit_ts"]), []).append(t)
        out, prev = [], initial
        for d in range(first, last + 1):
            observed = d in close
            eq = close.get(d, prev)
            day_trades = closed_by_day.get(d, [])
            out.append({"day": day_label(d), "ts": d * DAY, "equity": eq, "ret": (eq / prev - 1.0) if prev > 0 else 0.0,
                        "pnl": eq - prev, "trades": len(day_trades), "realized": float(sum(t["pnl"] for t in day_trades)),
                        "observed": observed})
            prev = eq
        return out

    def _refusals(self, acc: dict, start: float, now: float) -> dict:
        rows = self.db.query(
            "SELECT decision, decision_reason, outcome, outcome_r FROM candidates WHERE source = 'live' AND account_id = ? "
            "AND ts >= ? AND ts <= ?", (acc["id"], int(start * 1000), int(now * 1000)))
        taken = [r for r in rows if r["decision"] == "pris"]
        refused = [r for r in rows if r["decision"] != "pris"]

        def outcome(sub: list[dict]) -> dict:
            rs = [r["outcome_r"] for r in sub if r["outcome_r"] is not None]
            m = trade_metrics(rs)
            return {"n_resolved": len(rs), "mean_r": m.get("mean_r") if rs else None, "win_rate": m.get("win_rate") if rs else None,
                    "ci_low": m.get("ci_low") if rs else None, "ci_high": m.get("ci_high") if rs else None}

        groups: dict[str, list[dict]] = {}
        for r in refused:
            groups.setdefault(refusal_group(r["decision_reason"]), []).append(r)
        reasons = [{"label": label, "n": len(sub), "share": len(sub) / len(refused), **outcome(sub)} for label, sub in groups.items()]
        return {"seen": len(rows), "taken": len(taken), "refused": len(refused),
                "reasons": sorted(reasons, key=lambda g: -g["n"]),
                "taken_outcome": outcome(taken), "refused_outcome": outcome(refused)}

    # -- rapport -------------------------------------------------------------------------------
    def performance(self, account_id: int | None = None, period: str = "all") -> dict:
        if period not in PERIODS:
            raise UserError("Période inconnue")
        acc = self.db.one("SELECT * FROM accounts WHERE id = ?", (account_id,)) if account_id is not None else self.ai_account()
        if acc is None:
            raise UserError("Compte introuvable")
        now = self.market_clock()
        launch = min(self._launch_ts(acc), now)
        initial = float(acc["initial_balance"] or 0.0)
        snaps = self.db.query("SELECT ts, equity, cash FROM equity WHERE account_id = ? AND ts >= ? AND ts <= ? ORDER BY ts",
                              (acc["id"], launch - 1e-6, now))
        snaps_ts = [s["ts"] for s in snaps]
        snaps_eq = [s["equity"] for s in snaps]

        rt = self.runtimes.get(acc["id"])
        parts, open_positions, latent, exit_costs = None, [], 0.0, 0.0
        valuation_error = None
        if rt is not None and acc["kind"] == "paper":
            try:
                parts = rt.broker.breakdown()
            except OrderError as exc:
                valuation_error = exc.message
            for p in rt.positions.open_positions():
                u = rt.positions.unrealized(p)
                if p["status"] == "open":
                    if u["pnl"] is None:
                        valuation_error = u.get("error") or "Prix ou change indisponible"
                        continue
                    latent += u["pnl"]  # déjà net des frais de sortie estimés (prudent)
                    exit_costs += (u.get("value") or 0.0) * (rt.broker.fee_rate + rt.broker.conversion_cost(p["symbol"]))
                    open_positions.append({"id": p["id"], "symbol": p["symbol"], "origin": p["origin"], "strategy": p["strategy"],
                                           "entry_ts": p["entry_ts"], "value": u.get("value"), "pnl": u["pnl"], "r": u["r"]})
        current = parts["equity"] if parts else (snaps_eq[-1] if snaps_eq else initial)

        trades = self._closed_trades(acc, launch, snaps_ts, snaps_eq)
        daily = self._daily_series(launch, now, initial, snaps, current if parts else None, trades)

        def window(key: str) -> dict:
            days = PERIODS[key][1]
            start = launch if days is None else max(launch, (now - now % DAY) if days == 0 else (day_of(now) - days + 1) * DAY)
            # capital au début de la période : dernier relevé strictement AVANT son début (un relevé pris à minuit
            # pile appartient au jour qui commence, comme dans la série quotidienne)
            k = bisect.bisect_left(snaps_ts, start) - 1
            capital_start = snaps_eq[k] if (k >= 0 and start > launch) else initial
            sub = [t for t in trades if t["exit_ts"] >= start]
            ds = [d for d in daily if d["ts"] + DAY > start]
            eqs = [capital_start] + [e for ts, e in zip(snaps_ts, snaps_eq) if ts >= start] + [current]
            exposures = [1.0 - s["cash"] / s["equity"] for s in snaps if s["ts"] >= start and s["equity"] > 0]
            if parts and parts["equity"] > 0:
                exposures.append(parts["invested"] / parts["equity"])
            rets = [d["ret"] for d in ds]
            st = trade_stats(sub)
            return {
                "key": key, "label": PERIODS[key][0], "start": start, "capital_start": capital_start, "capital_end": current,
                "net": current - capital_start, "net_pct": (current / capital_start - 1.0) if capital_start > 0 else None,
                "realized": st["pnl"], "trades": st,
                "days": len(ds), "days_with_trades": sum(1 for d in ds if d["trades"]), "days_unobserved": sum(1 for d in ds if not d["observed"]),
                "positive_days": sum(1 for r in rets if r > 0), "negative_days": sum(1 for r in rets if r < 0),
                "mean_daily_return": (sum(rets) / len(rets)) if rets else None,
                "geo_daily_return": geometric_mean_return(rets),
                "best_day": max(rets) if rets else None, "worst_day": min(rets) if rets else None,
                "max_drawdown": max_drawdown(eqs), "max_exposure": max(exposures) if exposures else 0.0,
            }

        periods = {key: window(key) for key in PERIODS}
        sel = periods[period]
        start = sel["start"]
        sub = [t for t in trades if t["exit_ts"] >= start]
        step = max(1, len([s for s in snaps if s["ts"] >= start]) // 500)
        curve = [[s["ts"] * 1000, s["equity"]] for s in snaps if s["ts"] >= start][::step]
        if not curve or curve[0][0] > start * 1000:
            curve.insert(0, [start * 1000, sel["capital_start"]])
        curve.append([now * 1000, current])
        peak, dd = -math.inf, []
        for ts_ms, eq in curve:
            peak = max(peak, eq)
            dd.append([ts_ms, (eq / peak - 1.0) * 100 if peak > 0 else 0.0])

        realized_all = float(sum(t["pnl"] for t in trades))
        strategy_labels = {name: st.label for name, st in STRATEGIES.items()}
        strategy_labels[None] = "trade manuel"
        models = grouped([t for t in sub if t["origin"] == "ai"], "model_version")
        for g in models:
            rows = [t for t in sub if t["origin"] == "ai" and t["model_version"] == g["key"]]
            g["label"] = g["key"] or "sans modèle (exploration)"
            g["first_ts"], g["last_ts"] = min(t["entry_ts"] for t in rows), max(t["exit_ts"] for t in rows)
            g["modes"] = sorted({t["mode"] for t in rows if t["mode"]})
        models.sort(key=lambda g: g["first_ts"])

        caution = []
        if valuation_error:
            caution.append("Valorisation actuelle indisponible : dernier relevé conservé. " + valuation_error)
        if self.settings["market"]["source"] == "synthetic":
            caution.append("Marché simulé (démonstration) : ces chiffres ne sont pas une performance, ils montrent seulement comment le tableau fonctionne.")
        if sel["trades"]["n"] < MIN_TRADES:
            caution.append(f"Seulement {sel['trades']['n']} trade(s) clôturé(s) sur la période : c'est trop peu pour conclure "
                           f"({MIN_TRADES} au minimum). Un bon ou un mauvais chiffre peut être dû au hasard.")
        if sel["days_unobserved"]:
            caution.append(f"{sel['days_unobserved']} jour(s) sans relevé (Sentinelle éteint) : le capital y est supposé inchangé.")
        caution.append("Argent fictif : les résultats simulés ne garantissent jamais des résultats réels.")

        return {
            "account": {"id": acc["id"], "name": acc["name"], "role": acc["role"], "currency": acc["quote"],
                        "tradable": self.is_tradable(acc)},
            "simulated_market": self.settings["market"]["source"] == "synthetic",
            "now": now, "launch_ts": launch, "min_trades": MIN_TRADES,
            "capital": {"initial": initial, "current": current, "cash": parts["cash"] if parts else None,
                        "available": parts["available"] if parts else None, "reserved": parts["reserved"] if parts else None,
                        "invested": parts["invested"] if parts else None},
            "result": {"net": current - initial, "net_pct": (current / initial - 1.0) if initial > 0 else None,
                       "realized": realized_all, "latent": latent if not valuation_error else None, "fees": float(sum(t["fees"] for t in trades)) + float(sum(p["fees"] or 0 for p in rt.positions.open_positions()) if rt else 0),
                       "exit_costs_estimate": exit_costs,
                       # contrôle comptable : capital − départ = réalisé + latent + frais de sortie estimés (pas encore payés)
                       "check_gap": (current - initial) - (realized_all + latent + exit_costs) if not valuation_error else None},
            "valuation": {"current": not bool(valuation_error), "error": valuation_error,
                          "as_of": now if parts else (snaps_ts[-1] if snaps_ts else launch)},
            "validation": self.evaluation_dashboard(),
            "model_history": self.models.list(),
            "open_positions": open_positions,
            "periods": periods,
            "period": period,
            "detail": {
                "summary": sel,
                "equity": curve,
                "drawdown": dd,
                "daily": [d for d in daily if d["ts"] + DAY > start],
                "trades": sub[-300:],
                "by_strategy": grouped(sub, "strategy", strategy_labels),
                "by_symbol": grouped(sub, "symbol"),
                "by_origin": grouped(sub, "origin", {"ai": "IA", "manual": "Vous (manuel)"}),
                "by_regime": grouped(sub, "regime", REGIMES),
                "by_horizon": grouped(sub, "horizon"),
                "by_timeframe": grouped(sub, "timeframe"),
                "by_model": models,
                "refusals": self._refusals(acc, start, now),
            },
            "caution": caution,
        }
